"""
analytics/services/report_insights.py

AI insights for the unified report engine (POST /api/reports/generate/data/).

Architecture
────────────
• Per-section insights   — Claude Haiku (fast, cheap)
• Overall report summary — Claude Sonnet (deeper reasoning)
• Prompt caching         — system prompt marked ephemeral so Anthropic caches it
• Data-hash cache        — same section data → same insights from DB, no API call
• Parallel execution     — all section calls fired concurrently via ThreadPoolExecutor
• Graceful degradation   — one section failure never blocks the rest

Public API
──────────
get_report_insights(sections_data, period, context, include_summary=True)
    → {
        "sections": {
            "technical_metrics": { ...insight... },
            "hr_overview":        { ...insight... },
            ...
        },
        "overall": { ...report-level summary... } | None
      }
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

# ─── Models used for caching ───────────────────────────────────────────────────
# Imported lazily inside functions to avoid circular imports at startup.

# ─── Claude model identifiers ─────────────────────────────────────────────────
_HAIKU_MODEL  = 'claude-haiku-4-5-20251001'   # per-section — fast + cheap
_SONNET_MODEL = 'claude-sonnet-4-6'            # overall summary — deeper

# ─── Shared system prompt (cached at Anthropic side via cache_control) ─────────
_SYSTEM_PROMPT = (
    "You are a senior energy analyst for KEDCO (Kano Electricity Distribution Company), "
    "a Nigerian electricity distribution company. You produce concise, data-driven insights "
    "for internal management reports. Every response must be valid JSON only — "
    "no markdown fences, no prose outside the JSON object. "
    "Do not use em dashes (—) anywhere in your responses; use commas, full stops, or colons instead."
)

# ─── Section-type → human-readable domain label ───────────────────────────────
_SECTION_LABELS: dict[str, str] = {
    'technical_metrics':        'Technical Performance Metrics',
    'system_reliability':       'System Reliability',
    'interruption_breakdown':   'Interruption Breakdown',
    'infrastructure_overview':  'Infrastructure Overview',
    'feeder_performance_table': 'Feeder Performance',
    'state_performance_table':  'State Performance',
    'district_performance_table': 'District Performance',
    'service_band_summary':     'Service Band Summary',
    'hours_of_supply_chart':    'Hours of Supply Trend',
    'load_trend_chart':         'Load Trend',
    'energy_delivered_chart':   'Energy Delivered Trend',
    'dso_compliance_overview':  'DSO Compliance Overview',
    'dso_compliance_table':     'DSO Compliance by Station',
    'hr_overview':              'HR Overview',
    'staff_metrics':            'Staff Metrics',
    'wage_bill_analysis':       'Wage Bill Analysis',
    'department_headcount':     'Department Headcount',
    'attrition_analysis':       'Attrition Analysis',
    'recruitment_summary':      'Recruitment Summary',
    'commercial_overview':      'Commercial Overview',
    'commercial_coverage':      'Reading Coverage',
    'commercial_energy':        'Energy & Billing Analysis',
    'revenue_by_district':      'Revenue by District',
    'revenue_by_feeder':        'Revenue by Feeder',
    'customer_type_summary':    'Customer Type Summary',
    'financial_overview':       'Financial Overview',
    'opex_by_category':         'OPEX by Category',
    'opex_by_district':         'OPEX by District',
    'entity_comparison':        'Entity Comparison',
    'period_comparison':        'Period Comparison',
    'customer_comparison':      'Customer Consumption Comparison',
    'tmo_overview':              'TMO Overview',
    'tmo_gcr':                   'GCR — Target vs Billing Value',
    'tmo_pnl_deficit':           'P&L Target Realization Deficit',
    'tmo_energy_pnl_donut':      'Energy by P&L Segment',
    'tmo_compliance_by_segment': 'Feeder Compliance by Segment',
    'tmo_pear':                  'PEAR — MD vs Non-MD Mix',
    'tmo_feeder_scoped_summary': 'Selected Feeders Summary',
    'tmo_daily_network_energy':  'Daily Energy Forecast vs Actual',
    'tmo_daily_allocation':      'Daily Real-Time Allocation',
    'tmo_feeder_compliance_table': 'Feeder Compliance Criticality',
    'tmo_energy_by_voltage':     'Energy by Segment & Voltage',
    'tmo_incidents':             'Techno-Commercial Incidents',
    'tmo_volatility':            'P&L Mix Volatility',
}


# =============================================================================
# CACHE HELPERS
# =============================================================================

def _make_cache_key(section_type: str, data: Any) -> str:
    """SHA-256 of section_type + serialised data — same data = same key."""
    payload = json.dumps({'st': section_type, 'd': data}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_cached(cache_key: str) -> dict | None:
    from reports.models import ReportInsightsCache
    now = timezone.now()
    try:
        cached = ReportInsightsCache.objects.get(cache_key=cache_key, expires_at__gt=now)
        return {**cached.insights, 'cached': True}
    except ReportInsightsCache.DoesNotExist:
        return None


def _set_cached(cache_key: str, insights: dict) -> None:
    from reports.models import ReportInsightsCache
    expires = timezone.now() + timedelta(hours=24)
    try:
        ReportInsightsCache.objects.update_or_create(
            cache_key=cache_key,
            defaults={'insights': insights, 'expires_at': expires},
        )
    except Exception as exc:
        logger.warning("Failed to write ReportInsightsCache: %s", exc)


# =============================================================================
# CLAUDE CALL
# =============================================================================

def _call_claude(prompt: str, model: str, max_tokens: int = 2048) -> dict:
    import re

    import anthropic
    from django.conf import settings

    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not configured.")

    client  = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{'role': 'user', 'content': prompt}],
    )

    raw = message.content[0].text.strip()
    logger.debug("Claude (%s) raw (first 300): %s", model, raw[:300])

    # Strip markdown fences
    if raw.startswith('```'):
        lines = raw.splitlines()
        raw = '\n'.join(l for l in lines if not l.strip().startswith('```')).strip()

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Fallback: extract first {...} block
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())

    raise ValueError(f"No valid JSON in Claude response. Raw: {raw[:300]}")


# =============================================================================
# PROMPT BUILDERS
# =============================================================================

# Keys that indicate a section's data actually contains billing/revenue
# figures (naira amounts, collection/payment metrics) — not just energy (MWh/
# GWh/MW). Checked recursively since section data is often nested per-segment.
_BILLING_KEY_MARKERS = (
    'bill', 'billing', 'revenue', 'naira', 'payment', 'collection',
    'invoice', 'tariff', 'arrears', 'receivable',
)


def _has_billing_data(data) -> bool:
    if isinstance(data, dict):
        for k, v in data.items():
            if any(marker in str(k).lower() for marker in _BILLING_KEY_MARKERS):
                return True
            if _has_billing_data(v):
                return True
    elif isinstance(data, list):
        return any(_has_billing_data(item) for item in data)
    return False


def _build_section_prompt(section_type: str, data: dict, period_label: str) -> str:
    label = _SECTION_LABELS.get(section_type, section_type.replace('_', ' ').title())
    data_json = json.dumps(data, indent=2, default=str)

    billing_rule = (
        "- This section's data DOES include billing/revenue figures — you may reference "
        "payment, collection, or revenue impact where the data supports it."
        if _has_billing_data(data) else
        "- This section's data is energy/dispatch only (MWh/GWh/MW, no billing or naira "
        "figures) — do NOT mention billing, revenue loss, payment delays, or collection "
        "in any field. An energy gap here means energy was not delivered/dispatched yet, "
        "not that it was consumed and went unbilled — those are different problems with "
        "different owners. Frame gaps strictly as supply/dispatch/compliance issues."
    )

    return f"""Analyse the following KEDCO report section and return a JSON insights object.

Section: {label}
Period : {period_label}

Data:
{data_json}

Return ONLY valid JSON — no markdown, no prose outside the object:

{{
  "headline": "<one punchy sentence about the most important finding>",
  "summary": "<2 sentences explaining what this data means operationally>",
  "key_observations": [
    "<specific observation grounded in the actual numbers — observation 1>",
    "<observation 2>",
    "<observation 3>"
  ],
  "recommendations": [
    "<actionable recommendation for KEDCO field/management team>",
    "<recommendation 2>"
  ],
  "next_steps": [
    "<immediate, concrete action to take this week — who/what, not strategy>",
    "<next step 2>"
  ]
}}

Rules:
- Reference actual figures. Do not write generic platitudes.
- key_observations: exactly 3, each grounded in the data above.
- recommendations: exactly 2, practical for a Nigerian DISCO — the strategic "what to fix."
{billing_rule}
- next_steps: exactly 2, immediate/tactical "do this now" actions — distinct from
  recommendations, not a rephrasing of them.
- Keep headline under 20 words.
"""


def _build_overall_prompt(sections: list[dict], period_label: str, company_name: str) -> str:
    # Summarise each section with its headline so Sonnet sees the full picture
    section_lines = []
    for s in sections:
        st      = s.get('section_type', '')
        label   = _SECTION_LABELS.get(st, st.replace('_', ' ').title())
        insight = s.get('ai_insights') or {}
        headline = insight.get('headline', '(no insight)')
        section_lines.append(f"• {label}: {headline}")

    section_summary = "\n".join(section_lines) or "No section insights available."

    return f"""You are writing the executive summary for a {company_name} management report.

Period: {period_label}

Section highlights:
{section_summary}

Return ONLY valid JSON — no markdown:

{{
  "executive_headline": "<one sentence capturing the overall performance story>",
  "executive_summary": "<3-4 sentences: overall narrative, key wins, key risks, recommended focus>",
  "top_priorities": [
    "<highest-priority action for leadership — specific>",
    "<second priority>",
    "<third priority>"
  ],
  "positive_highlights": [
    "<something that went well, grounded in section data>",
    "<another positive>"
  ],
  "areas_of_concern": [
    "<concern 1 — specific>",
    "<concern 2>"
  ]
}}

Rules:
- Be direct and concise. No filler.
- Reference specific sections / metrics where relevant.
- top_priorities: exactly 3.
- positive_highlights: exactly 2.
- areas_of_concern: exactly 2.
"""


# =============================================================================
# PER-SECTION INSIGHT
# =============================================================================

def _get_one_section_insight(section_type: str, data: Any, period_label: str) -> dict:
    """Fetch insight for a single section — checks cache first, calls Haiku if miss."""
    if not data or (isinstance(data, dict) and 'error' in data):
        return None

    cache_key = _make_cache_key(section_type, data)
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        prompt   = _build_section_prompt(section_type, data, period_label)
        # 1024 was enough before per-feeder fault_breakdown data existed —
        # confirmed 2026-08-18 the richer tmo_supply_hours payload (fault
        # causes for every underperforming feeder) gives the model enough to
        # say that it now runs past 1024 tokens and gets cut off mid-JSON,
        # failing the whole insight (no card at all) instead of just being
        # terser. 2048 matches the overall/executive prompt's budget.
        insights = _call_claude(prompt, model=_HAIKU_MODEL, max_tokens=2048)
        _set_cached(cache_key, insights)
        return {**insights, 'cached': False}
    except Exception as exc:
        logger.error("Section insight failed for %s: %s", section_type, exc)
        return None


# =============================================================================
# OVERALL SUMMARY
# =============================================================================

def _get_overall_insight(
    sections: list[dict], period_label: str, company_name: str
) -> dict | None:
    # Cache key based on all section types + their headlines
    summary_payload = [
        {
            'st': s.get('section_type'),
            'h':  (s.get('ai_insights') or {}).get('headline'),
        }
        for s in sections
    ]
    cache_key = _make_cache_key('__overall__', {'p': period_label, 's': summary_payload})
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        prompt   = _build_overall_prompt(sections, period_label, company_name)
        insights = _call_claude(prompt, model=_SONNET_MODEL, max_tokens=2048)
        _set_cached(cache_key, insights)
        return {**insights, 'cached': False}
    except Exception as exc:
        logger.error("Overall report insight failed: %s", exc)
        return None


# =============================================================================
# PUBLIC API
# =============================================================================

# Section types that never need insights (no meaningful data to analyse)
_SKIP_INSIGHT_TYPES = {'cover_page', 'table_of_contents', 'custom_text', 'gaps_improvements'}


def get_report_insights(
    sections_data: list[dict],
    period_label: str,
    company_name: str = 'KANO ELECTRICITY DISTRIBUTION COMPANY',
    include_summary: bool = True,
) -> dict:
    """
    Generate AI insights for all report sections in parallel, then optionally
    generate one overall executive summary.

    sections_data — list of section dicts from generate_report_data, each with:
        { "section_type": "...", "data": {...}, ... }

    Returns:
    {
        "sections": {
            "technical_metrics": { headline, summary, key_observations, recommendations, cached },
            "hr_overview":        { ... },
            ...
        },
        "overall": { executive_headline, executive_summary, top_priorities, ... } | None
    }
    """
    # Only sections with real data get insights
    eligible = [
        s for s in sections_data
        if s.get('section_type') not in _SKIP_INSIGHT_TYPES
        and s.get('data')
        and not (isinstance(s.get('data'), dict) and 'error' in s['data'])
    ]

    section_insights: dict[str, dict | None] = {}

    # ── Parallel per-section calls ────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=min(len(eligible), 8)) as pool:
        future_to_type = {
            pool.submit(
                _get_one_section_insight,
                s['section_type'],
                s['data'],
                period_label,
            ): s['section_type']
            for s in eligible
        }
        for future in as_completed(future_to_type):
            st = future_to_type[future]
            try:
                section_insights[st] = future.result()
            except Exception as exc:
                logger.error("Parallel section insight failed for %s: %s", st, exc)
                section_insights[st] = None

    # ── Attach insights back onto sections for the overall prompt ────────────
    enriched = []
    for s in sections_data:
        enriched.append({
            **s,
            'ai_insights': section_insights.get(s['section_type']),
        })

    # ── Overall executive summary ─────────────────────────────────────────────
    overall = None
    if include_summary and eligible:
        overall = _get_overall_insight(enriched, period_label, company_name)

    return {
        'sections': section_insights,
        'overall':  overall,
    }
