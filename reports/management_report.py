"""
reports/management_report.py

Management / Admin Report Generator.

Produces a narrative, interpretation-heavy portrait PDF modelled on the
formal management report format (executive summary, RAG KPI dashboard,
reliability review, feeder rankings, state analysis, service-band
recovery actions, priority issues, action plan).

Every section is anchored by AI-generated management commentary (Claude
Sonnet), with graceful fallback to data-only pages when the API is
unavailable.

Entry point
───────────
POST /api/reports/generate/management/

Body:
{
    "report_title":  "May 2026 11kV Management Report",      // optional
    "company_name":  "KANO ELECTRICITY DISTRIBUTION COMPANY",// optional
    "theme": { "primary_color": "#001634", ... },            // optional
    "include_ai": true,                                      // default true
    "filters": {
        "from_date": "2026-05-01",
        "to_date":   "2026-05-31",
        ...
    }
}
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Playwright (preferred PDF engine) ─────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None
    _PLAYWRIGHT_AVAILABLE = False

# ── WeasyPrint (fallback) ──────────────────────────────────────────────────────
try:
    from weasyprint import HTML as _WeasyHTML
    from weasyprint.text.fonts import FontConfiguration as _FontConfig
    _WEASYPRINT_AVAILABLE = True
except (OSError, ImportError):
    _WeasyHTML = None
    _FontConfig = None
    _WEASYPRINT_AVAILABLE = False


# =============================================================================
# CSS — portrait document style
# =============================================================================

MANAGEMENT_STYLES = """
/* No @page size rule here on purpose — it used to hardcode "A4 portrait",
   which silently forced every landscape report back to portrait once this
   stylesheet became unconditional (2026-08-18). Page size is decided by
   PORTRAIT_STYLES/LANDSCAPE_STYLES (pdf_generator.py) for the generic
   report builder, and directly via the format/landscape Playwright
   page.pdf() params for the three standalone report generators (which
   are portrait-only regardless of any CSS). Only margin needs setting
   here, not size. */
@page { margin: 0; }
@page :first { margin: 0; }

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    background: #ffffff;
    color: #001634;
    font-size: 10.5px;
    line-height: 1.5;
}

/* ── Page shell ─────────────────────────────────────────────────────────── */
.page {
    width: 210mm;
    height: 297mm;
    padding: 20px 36px;
    page-break-after: always;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    overflow: hidden;
}
.page:last-child { page-break-after: avoid; }

.page-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* ── Header / Footer ────────────────────────────────────────────────────── */
.page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 8px;
    border-bottom: 1px solid rgba(0,32,80,0.15);
    margin-bottom: 16px;
    flex-shrink: 0;
}
.header-company { font-size: 9px; font-weight: 700; letter-spacing: 0.5px; opacity: 0.6; }
.header-subtitle { font-size: 9px; font-weight: 500; opacity: 0.5; }

.page-footer {
    flex-shrink: 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-top: 10px;
    border-top: 1px solid rgba(0,32,80,0.15);
    margin-top: auto;
}
.footer-label { font-size: 8.5px; opacity: 0.45; font-style: italic; }
.footer-page  { font-size: 13px; font-weight: 700; }

/* ── Section / subsection titles ────────────────────────────────────────── */
.section-title {
    font-size: 17px;
    font-weight: 700;
    padding-left: 10px;
    border-left: 4px solid #001634;
    line-height: 1.2;
    margin-bottom: 14px;
    flex-shrink: 0;
}
.subsection-title {
    font-size: 11.5px;
    font-weight: 700;
    border-bottom: 1px solid rgba(0,32,80,0.15);
    padding-bottom: 3px;
    margin: 14px 0 8px;
}

/* ── Narrative text ─────────────────────────────────────────────────────── */
.narrative { font-size: 10.5px; line-height: 1.65; margin-bottom: 8px; }

/* ── Callout box ────────────────────────────────────────────────────────── */
.callout {
    background: rgba(0,32,80,0.05);
    border-left: 3px solid #001634;
    border-radius: 0 8px 8px 0;
    padding: 9px 13px;
    margin: 10px 0;
}
.callout-title {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
    opacity: 0.75;
}
.callout-text { font-size: 10px; line-height: 1.6; }

/* ── RAG badges ─────────────────────────────────────────────────────────── */
.rag { display:inline-block; font-size:8.5px; font-weight:700; padding:2px 8px; border-radius:4px; white-space:nowrap; }
.rag-red    { background:#fde8e8; color:#c62828; }
.rag-amber  { background:#fff3e0; color:#e65100; }
.rag-green  { background:#e8f5e9; color:#2e7d32; }
.rag-grey   { background:#f0f0f0; color:#666;    }

/* ── Management tables ──────────────────────────────────────────────────── */
.mgmt-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 10px;
}
.mgmt-table thead th {
    background: #001634;
    color: #ffffff;
    font-size: 9px;
    font-weight: 700;
    padding: 7px 9px;
    text-align: left;
    vertical-align: middle;
}
.mgmt-table tbody td {
    font-size: 9.5px;
    padding: 6px 9px;
    border-bottom: 1px solid rgba(0,32,80,0.07);
    vertical-align: top;
    line-height: 1.5;
}
.mgmt-table tbody tr:last-child td { border-bottom: none; }
.mgmt-table tbody tr:nth-child(even) td { background: rgba(0,32,80,0.025); }

/* ── KPI dashboard table (wider cells, movement column) ────────────────── */
.kpi-table thead th { font-size: 8.5px; padding: 6px 8px; }
.kpi-table tbody td { font-size: 9px; padding: 7px 8px; }
.kpi-movement-pos { color: #2e7d32; font-weight: 700; }
.kpi-movement-neg { color: #c62828; font-weight: 700; }
.kpi-movement-neu { color: #666; }

/* ── Two-column layout for feeder tables ────────────────────────────────── */
.two-col { display: flex; gap: 14px; }
.two-col-half { flex: 1; overflow: hidden; }

/* ── Cover page ─────────────────────────────────────────────────────────── */
.cover-page {
    padding: 0;
    display: flex;
    flex-direction: row;
    min-height: 297mm;
    page-break-after: always;
    background: #001634;
    color: #ffffff;
    position: relative;
    overflow: hidden;
    /* Top-to-bottom navy gradient (deepest at the bottom) plus a subtle
       dot-grid texture for depth — palette matches the reference utility
       cover design (#001634 / #001430 / #D9A400 accent / #0067A8 &
       #1489D1 utility blues). */
    background-image:
        linear-gradient(180deg, #001634 0%, #001430 100%),
        radial-gradient(circle, rgba(255, 255, 255, 0.06) 1px, transparent 1.4px);
    background-size: 100% 100%, 26px 26px;
    background-position: 0 0, 0 0;
    background-repeat: no-repeat, repeat;
}
/* align-self:stretch guards against the back page's inline
   align-items:center on .cover-page (needed to vertically centre its
   content) — without it, that override stops this empty div from
   stretching to full height, collapsing the bar to ~0px instead of
   running the full page height. */
.cover-accent { width: 10px; background: #D9A400; flex-shrink: 0; align-self: stretch; }
.cover-grid-art {
    position: absolute;
    top: 0; right: 0; bottom: 0; left: 0;
    z-index: 0;
    pointer-events: none;
}
.cover-grid-art svg { width: 100%; height: 100%; display: block; }
.cover-body {
    flex: 1;
    padding: 48px 55px;
    display: flex;
    flex-direction: column;
    position: relative;
    z-index: 1;
}
.cover-top-company {
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 2.5px;
    opacity: 0.55; margin-bottom: 40px;
}
.cover-eyebrow {
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 3px;
    opacity: 0.5; margin-bottom: 20px;
}
.cover-main-title {
    font-size: 52px; font-weight: 800;
    line-height: 1.05; text-transform: uppercase;
    letter-spacing: -0.5px; margin: 0 0 8px 0;
}
/* Same gold used for "Achievement" throughout the report body — the cover
   should read as the same brand as the pages that follow it. */
.cover-main-title-accent { color: #D9A400; }
.cover-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 16px;
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.20);
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.85);
    margin-top: 20px;
    width: fit-content;
}
.cover-badge svg { width: 14px; height: 14px; color: #D9A400; flex-shrink: 0; }
.cover-rule {
    width: 60px; height: 4px;
    background: #D9A400;
    border-radius: 3px; margin: 22px 0;
}
.cover-subtitle { font-size: 13px; font-weight: 400; opacity: 0.6; line-height: 1.6; }
.cover-logo-center {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 60px;
}
.cover-logo-center img { max-height: 175px; max-width: 440px; width: auto; }
.cover-footer-strip {
    padding-top: 18px;
    border-top: 1px solid rgba(255,255,255,0.15);
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.cover-footer-strip img { max-height: 74px; width: auto; }
.cover-period {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 11px; font-weight: 600; opacity: 0.55; letter-spacing: 1px;
}
.cover-period svg { width: 12px; height: 12px; flex-shrink: 0; }

/* ── Table of Contents — single shared design (render_toc_row) ─────────── */
.toc-container {
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid rgba(0, 32, 80, 0.1);
}
.toc-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 9px 14px;
    border-bottom: 1px solid rgba(0, 32, 80, 0.08);
}
.toc-row:nth-child(even) { background: rgba(0, 32, 80, 0.03); }
.toc-row:last-child { border-bottom: none; }
.toc-number {
    flex: 0 0 20px;
    height: 20px;
    border-radius: 50%;
    background: #001634;
    color: #fff;
    font-size: 9px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
}
.toc-title { font-size: 10.5px; font-weight: 600; color: #001634; }
.toc-description { font-size: 8px; color: #7C8FAC; margin-top: 1px; }
.toc-dots { display: none; }
.toc-page {
    font-size: 10px;
    font-weight: 700;
    color: #001634;
    background: rgba(0, 32, 80, 0.08);
    border-radius: 5px;
    flex: 0 0 26px;
    height: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
}
"""


# =============================================================================
# RAG HELPERS
# =============================================================================

def _rag_status(value: float, green: float, amber: float,
                higher_is_better: bool = True) -> str:
    """Return 'green', 'amber', or 'red' based on numeric thresholds."""
    if higher_is_better:
        if value >= green: return 'green'
        if value >= amber: return 'amber'
        return 'red'
    else:
        if value <= green: return 'green'
        if value <= amber: return 'amber'
        return 'red'


def _rag_html(status: str) -> str:
    label = {'green': 'Green', 'amber': 'Amber', 'red': 'Red'}.get(status, 'N/A')
    return f'<span class="rag rag-{status}">{label}</span>'


def _movement_html(current, previous) -> str:
    """Return formatted movement string with CSS class."""
    try:
        c, p = float(current), float(previous)
        if p == 0:
            return '<span class="kpi-movement-neu">—</span>'
        pct = ((c - p) / abs(p)) * 100
        sign = '+' if pct >= 0 else ''
        cls = 'kpi-movement-pos' if pct >= 0 else 'kpi-movement-neg'
        return f'<span class="{cls}">{sign}{pct:.1f}%</span>'
    except (TypeError, ValueError):
        return '<span class="kpi-movement-neu">—</span>'


# =============================================================================
# AI NARRATIVE SERVICE
# =============================================================================

_MGMT_SYSTEM_PROMPT = (
    "You are a senior management consultant preparing formal management reports "
    "for KEDCO (Kano Electricity Distribution Company), a Nigerian electricity "
    "distribution company. You write clear, professional management commentary "
    "grounded in the actual data. Every response must be valid JSON only — "
    "no markdown fences, no prose outside the JSON object. "
    "Do not use em dashes (—) anywhere in your responses; use commas, full stops, or colons instead."
)

_SONNET_MODEL = 'claude-sonnet-4-6'


def _call_management_claude(prompt: str, max_tokens: int = 4000) -> dict:
    """Call Claude Sonnet and parse the JSON response."""
    import re
    import anthropic
    from django.conf import settings

    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not configured.")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=_SONNET_MODEL,
        max_tokens=max_tokens,
        system=[{
            "type": "text",
            "text": _MGMT_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group())

    raise ValueError(f"No valid JSON in Claude response. Raw: {raw[:400]}")


def _build_narrative_prompt(all_data: dict, period_label: str,
                             company_name: str) -> str:
    """Build the comprehensive management narrative prompt."""

    metrics   = all_data.get("technical_metrics", {})
    prev      = all_data.get("previous_metrics", {})
    relia     = all_data.get("system_reliability", {})
    interr    = all_data.get("interruption_breakdown", [])
    feeders   = all_data.get("feeder_performance", [])
    states    = all_data.get("state_performance", [])
    bands     = all_data.get("service_band_summary", [])

    # Derive top-10 and bottom-10 feeders for the prompt (keep prompt size manageable)
    sorted_feeders = sorted(feeders, key=lambda f: float(f.get("hours_of_supply", 0)),
                            reverse=True) if feeders else []
    top_feeders  = sorted_feeders[:10]
    weak_feeders = [f for f in sorted_feeders[-15:] if float(f.get("hours_of_supply", 0)) < 8][:10]

    data_summary = {
        "period":            period_label,
        "company":           company_name,
        "technical_metrics": metrics,
        "previous_metrics":  prev,
        "system_reliability": relia,
        "interruption_breakdown": interr[:15],
        "top_10_feeders":    top_feeders,
        "weak_feeders":      weak_feeders,
        "state_performance": states,
        "service_band_summary": bands,
    }

    # Build interruption_implications template rows using exact category codes from the data
    # so the AI returns keys that the renderer can match.
    interr_cats = [item.get('type', '') for item in (interr[:15] or []) if item.get('type')]
    if interr_cats:
        impl_rows = ",\n    ".join(
            '{"issue":"' + cat + '",'
            '"implication":"<management note on ' + cat + ' interruptions — reference the count and hours>",'
            '"response":"<required management action>"}'
            for cat in interr_cats
        )
    else:
        impl_rows = '{"issue":"Unknown","implication":"No interruption data.","response":"Verify data capture."}'

    return f"""
You are preparing a formal management report for {company_name} covering {period_label}.

Below is the performance data. Write a management narrative and return a single JSON object
with the exact structure specified. Keep all text concise, professional and grounded in
the actual numbers. Use specific figures from the data wherever possible.

CRITICAL RULE: In the interruption_implications array, the "issue" field MUST be copied
character-for-character from the category names already pre-filled in the template below.
Do NOT rename, translate, or rewrite them. This is required for the report renderer to
match AI text to the correct table row.

DATA:
{json.dumps(data_summary, indent=2, default=str)}

Return ONLY this JSON structure — no markdown, no text outside the object:

{{
  "executive_summary": {{
    "paragraph_1": "<Overall performance summary — key headline numbers and what they mean>",
    "paragraph_2": "<Supply improvement context — what drove the change, supply vs load analysis>",
    "paragraph_3": "<Reliability position — interruptions, duration, restoration time>",
    "paragraph_4": "<Service band position — which bands are strong/weak>",
    "management_priority": "<One callout sentence: the single most important management action for next period>"
  }},
  "kpi_status": {{
    "average_supply":               {{"status":"amber","interpretation":"<10-15 word management note>"}},
    "average_load":                 {{"status":"amber","interpretation":"<note>"}},
    "energy_delivered":             {{"status":"amber","interpretation":"<note>"}},
    "total_interruptions":          {{"status":"red",  "interpretation":"<note>"}},
    "cumulative_interruption_hours":{{"status":"red",  "interpretation":"<note>"}},
    "avg_interruption_duration":    {{"status":"red",  "interpretation":"<note>"}},
    "local_fault_tat":              {{"status":"red",  "interpretation":"<note>"}}
  }},
  "reliability_intro": "<Opening paragraph for the reliability section>",
  "interruption_implications": [
    {impl_rows}
  ],
  "feeder_strong_commentary": "<Paragraph interpreting strong feeder performance and what management should learn>",
  "feeder_weak_commentary_1": "<Paragraph explaining the two categories of weak feeders>",
  "feeder_weak_commentary_2": "<Paragraph on management approach — technical vs data exception>",
  "state_review_intro": "<Paragraph summarising state-level performance>",
  "state_conclusion": "<One callout sentence with the state-level management conclusion>",
  "service_band_intro": "<Paragraph explaining the service band compliance picture>",
  "band_interpretations": {{
    "A": "<Management interpretation for Band A>",
    "B": "<Management interpretation for Band B>",
    "C": "<Management interpretation for Band C>",
    "D": "<Management interpretation for Band D>",
    "E": "<Management interpretation for Band E — note if only 1 feeder>"
  }},
  "recovery_actions": [
    {{"band":"A","issue":"<key issue>","action":"<recommended management action>"}},
    {{"band":"B","issue":"<key issue>","action":"<recommended management action>"}},
    {{"band":"C","issue":"<key issue>","action":"<recommended management action>"}},
    {{"band":"D","issue":"<key issue>","action":"<recommended management action>"}}
  ],
  "priority_issues": [
    {{"issue":"<name>","evidence":"<specific data point>","risk":"<management risk>","response":"<required action>"}}
  ],
  "action_plan": [
    {{"area":"<action area>","action":"<specific recommended action>","team":"<responsible team>","timeline":"<e.g. Before next report / 2 weeks / Monthly>","output":"<expected deliverable>"}}
  ]
}}

Generate at least 5 priority_issues and 8 action_plan items grounded in the data.
"""


def generate_management_narrative(all_data: dict, period_label: str,
                                   company_name: str) -> dict:
    """
    Call Claude Sonnet to generate the full management narrative.
    Returns the narrative dict, or a safe fallback dict on failure.
    """
    try:
        prompt = _build_narrative_prompt(all_data, period_label, company_name)
        return _call_management_claude(prompt, max_tokens=8000)
    except Exception as exc:
        logger.warning("Management narrative AI call failed: %s", exc)
        return _fallback_narrative()


def _fallback_narrative() -> dict:
    """Minimal narrative used when AI is unavailable or disabled."""
    return {
        "executive_summary": {
            "paragraph_1": "Performance data for the reporting period is presented in this management report.",
            "paragraph_2": "Supply hours and energy delivery are summarised in the KPI dashboard.",
            "paragraph_3": "Reliability metrics including interruption counts and durations are detailed in the reliability section.",
            "paragraph_4": "Service band performance and feeder-level analysis follow in subsequent sections.",
            "management_priority": "Review priority issues and confirm action owners for the next reporting cycle.",
        },
        "kpi_status": {
            "average_supply":                {"status": "amber", "interpretation": "Review against service band targets."},
            "average_load":                  {"status": "amber", "interpretation": "Monitor for trend changes."},
            "energy_delivered":              {"status": "amber", "interpretation": "Verify metering methodology."},
            "total_interruptions":           {"status": "red",   "interpretation": "Interruption reduction plan required."},
            "cumulative_interruption_hours": {"status": "red",   "interpretation": "High outage exposure."},
            "avg_interruption_duration":     {"status": "red",   "interpretation": "Restoration times require improvement."},
            "local_fault_tat":               {"status": "red",   "interpretation": "Fault response tracking required."},
        },
        "reliability_intro": "Reliability data for the period is presented below.",
        "interruption_implications": [],
        "feeder_strong_commentary": "The strongest-performing feeders are listed below.",
        "feeder_weak_commentary_1": "Weak feeders fall into two categories: operational underperformance and data exceptions.",
        "feeder_weak_commentary_2": "Management should not treat all weak feeders the same. Some require technical intervention, others data validation.",
        "state_review_intro": "State-level performance is summarised below.",
        "state_conclusion": "Review state-level interruption intensity alongside absolute counts.",
        "service_band_intro": "Service band performance is compared against expected supply levels.",
        "band_interpretations": {"A": "", "B": "", "C": "", "D": "", "E": ""},
        "recovery_actions": [],
        "priority_issues": [],
        "action_plan": [],
    }


# =============================================================================
# HELPER — reusable page wrapper
# =============================================================================

def _page(content: str, context: dict, page_number: int,
          show_header: bool = True) -> str:
    header_html = ""
    if show_header:
        header_html = f"""
        <div class="page-header">
            <span class="header-company">{context.get('company_name', '')}</span>
            <span class="header-subtitle">{context.get('report_title', '')} &nbsp;|&nbsp; {context.get('report_date', '')}</span>
        </div>"""

    primary = context.get('primary_color', '#001634')
    accent  = context.get('accent_color',  'rgba(0,22,52,0.2)')
    primary_light = context.get('primary_light', 'rgba(0,22,52,0.05)')

    return f"""
    <div class="page">
        <div class="page-content">
            {header_html}
            {content}
        </div>
        <div class="page-footer">
            <span class="footer-label">Written by ARIA &nbsp;·&nbsp; Automated Raven Intelligence Assistance</span>
            <span class="footer-page">{page_number}</span>
        </div>
    </div>"""


# =============================================================================
# SECTION RENDERERS
# =============================================================================

# Transmission-tower + constellation background art for the cover — two
# detailed lattice-tower silhouettes (legs, 6 braced levels, 3 cross-arms
# with insulator drops, apex) bottom-right, fading into a denser 14-node
# constellation upper-right. Pure inline SVG (no external asset), sized to
# the cover's own viewBox so it scales with the page. Coordinates were
# generated programmatically (not hand-typed) from the same tower-lattice
# logic as the reference design, then pasted here as a static constant —
# only the cover's text content changes per report type, this art doesn't.
_COVER_GRID_SVG_PORTRAIT = """
<svg viewBox="0 0 210 297" preserveAspectRatio="xMaxYMax slice" xmlns="http://www.w3.org/2000/svg">
    <!-- constellation of connected nodes, upper-right, clear of the title/logo -->
    <g stroke="#1489D1" stroke-width="0.4" fill="none" opacity="0.4">
        <line x1="126.0" y1="17.8" x2="142.8" y2="29.7"/>
        <line x1="126.0" y1="17.8" x2="161.7" y2="20.8"/>
        <line x1="142.8" y1="29.7" x2="161.7" y2="20.8"/>
        <line x1="142.8" y1="29.7" x2="180.6" y2="35.6"/>
        <line x1="161.7" y1="20.8" x2="180.6" y2="35.6"/>
        <line x1="161.7" y1="20.8" x2="165.9" y2="56.4"/>
        <line x1="180.6" y1="35.6" x2="197.4" y2="23.8"/>
        <line x1="180.6" y1="35.6" x2="165.9" y2="56.4"/>
        <line x1="197.4" y1="23.8" x2="207.9" y2="53.5"/>
        <line x1="197.4" y1="23.8" x2="184.8" y2="59.4"/>
        <line x1="207.9" y1="53.5" x2="184.8" y2="59.4"/>
        <line x1="184.8" y1="59.4" x2="165.9" y2="56.4"/>
        <line x1="184.8" y1="59.4" x2="147.0" y2="65.3"/>
        <line x1="165.9" y1="56.4" x2="147.0" y2="65.3"/>
        <line x1="165.9" y1="56.4" x2="132.3" y2="50.5"/>
        <line x1="147.0" y1="65.3" x2="132.3" y2="50.5"/>
        <line x1="147.0" y1="65.3" x2="201.6" y2="89.1"/>
        <line x1="147.0" y1="65.3" x2="178.5" y2="86.1"/>
        <line x1="132.3" y1="50.5" x2="153.3" y2="83.2"/>
        <line x1="132.3" y1="50.5" x2="138.6" y2="95.0"/>
        <line x1="153.3" y1="83.2" x2="178.5" y2="86.1"/>
        <line x1="153.3" y1="83.2" x2="138.6" y2="95.0"/>
        <line x1="178.5" y1="86.1" x2="201.6" y2="89.1"/>
    </g>
    <g fill="#D9A400" opacity="0.9">
        <circle cx="142.8" cy="29.7" r="1.3"/>
        <circle cx="197.4" cy="23.8" r="1.3"/>
        <circle cx="147.0" cy="65.3" r="1.2"/>
        <circle cx="153.3" cy="83.2" r="1.2"/>
    </g>
    <g fill="#1489D1" opacity="0.65">
        <circle cx="126.0" cy="17.8" r="1"/>
        <circle cx="161.7" cy="20.8" r="1"/>
        <circle cx="180.6" cy="35.6" r="1"/>
        <circle cx="207.9" cy="53.5" r="1"/>
        <circle cx="184.8" cy="59.4" r="1"/>
        <circle cx="165.9" cy="56.4" r="1"/>
        <circle cx="132.3" cy="50.5" r="1"/>
        <circle cx="178.5" cy="86.1" r="1"/>
        <circle cx="201.6" cy="89.1" r="1"/>
        <circle cx="138.6" cy="95.0" r="1"/>
    </g>
    <!-- main lattice tower, bottom-right, well clear of the centred logo -->
    <g stroke="#1489D1" stroke-width="0.55" fill="none" opacity="0.32" stroke-linejoin="round" stroke-linecap="round">
        <line x1="173.0" y1="296.0" x2="189.5" y2="178.0"/>
        <line x1="211.0" y1="296.0" x2="194.5" y2="178.0"/>
        <line x1="192.0" y1="296.0" x2="192.0" y2="178.0"/>
        <line x1="174.9" y1="281.8" x2="209.1" y2="281.8"/>
        <line x1="176.9" y1="266.5" x2="207.1" y2="266.5"/>
        <line x1="174.9" y1="281.8" x2="207.1" y2="266.5"/>
        <line x1="209.1" y1="281.8" x2="176.9" y2="266.5"/>
        <line x1="178.9" y1="251.2" x2="205.1" y2="251.2"/>
        <line x1="176.9" y1="266.5" x2="205.1" y2="251.2"/>
        <line x1="207.1" y1="266.5" x2="178.9" y2="251.2"/>
        <line x1="180.9" y1="235.8" x2="203.1" y2="235.8"/>
        <line x1="178.9" y1="251.2" x2="203.1" y2="235.8"/>
        <line x1="205.1" y1="251.2" x2="180.9" y2="235.8"/>
        <line x1="183.0" y1="220.5" x2="201.0" y2="220.5"/>
        <line x1="180.9" y1="235.8" x2="201.0" y2="220.5"/>
        <line x1="203.1" y1="235.8" x2="183.0" y2="220.5"/>
        <line x1="184.8" y1="206.3" x2="199.2" y2="206.3"/>
        <line x1="183.0" y1="220.5" x2="199.2" y2="206.3"/>
        <line x1="201.0" y1="220.5" x2="184.8" y2="206.3"/>
        <line x1="184.0" y1="227.6" x2="200.0" y2="227.6"/>
        <line x1="184.0" y1="227.6" x2="184.0" y2="232.9"/>
        <line x1="200.0" y1="227.6" x2="200.0" y2="232.9"/>
        <line x1="182.5" y1="213.4" x2="201.5" y2="213.4"/>
        <line x1="182.5" y1="213.4" x2="182.5" y2="218.7"/>
        <line x1="201.5" y1="213.4" x2="201.5" y2="218.7"/>
        <line x1="185.2" y1="200.4" x2="198.8" y2="200.4"/>
        <line x1="185.2" y1="200.4" x2="185.2" y2="205.7"/>
        <line x1="198.8" y1="200.4" x2="198.8" y2="205.7"/>
        <line x1="189.5" y1="178.0" x2="192.0" y2="169.7"/>
        <line x1="194.5" y1="178.0" x2="192.0" y2="169.7"/>
    </g>
    <!-- smaller second tower, further left, partially behind the main one -->
    <g stroke="#1489D1" stroke-width="0.5" fill="none" opacity="0.20" stroke-linejoin="round" stroke-linecap="round">
        <line x1="139.0" y1="296.0" x2="148.4" y2="228.0"/>
        <line x1="161.0" y1="296.0" x2="151.6" y2="228.0"/>
        <line x1="150.0" y1="296.0" x2="150.0" y2="228.0"/>
        <line x1="140.1" y1="287.8" x2="159.9" y2="287.8"/>
        <line x1="141.3" y1="279.0" x2="158.7" y2="279.0"/>
        <line x1="140.1" y1="287.8" x2="158.7" y2="279.0"/>
        <line x1="159.9" y1="287.8" x2="141.3" y2="279.0"/>
        <line x1="142.4" y1="270.2" x2="157.6" y2="270.2"/>
        <line x1="141.3" y1="279.0" x2="157.6" y2="270.2"/>
        <line x1="158.7" y1="279.0" x2="142.4" y2="270.2"/>
        <line x1="143.6" y1="261.3" x2="156.4" y2="261.3"/>
        <line x1="142.4" y1="270.2" x2="156.4" y2="261.3"/>
        <line x1="157.6" y1="270.2" x2="143.6" y2="261.3"/>
        <line x1="144.8" y1="252.5" x2="155.2" y2="252.5"/>
        <line x1="143.6" y1="261.3" x2="155.2" y2="252.5"/>
        <line x1="156.4" y1="261.3" x2="144.8" y2="252.5"/>
        <line x1="145.9" y1="244.3" x2="154.1" y2="244.3"/>
        <line x1="144.8" y1="252.5" x2="154.1" y2="244.3"/>
        <line x1="155.2" y1="252.5" x2="145.9" y2="244.3"/>
        <line x1="145.4" y1="256.6" x2="154.6" y2="256.6"/>
        <line x1="145.4" y1="256.6" x2="145.4" y2="259.6"/>
        <line x1="154.6" y1="256.6" x2="154.6" y2="259.6"/>
        <line x1="144.5" y1="248.4" x2="155.5" y2="248.4"/>
        <line x1="144.5" y1="248.4" x2="144.5" y2="251.5"/>
        <line x1="155.5" y1="248.4" x2="155.5" y2="251.5"/>
        <line x1="146.0" y1="240.9" x2="154.0" y2="240.9"/>
        <line x1="146.0" y1="240.9" x2="146.0" y2="244.0"/>
        <line x1="154.0" y1="240.9" x2="154.0" y2="244.0"/>
        <line x1="148.4" y1="228.0" x2="150.0" y2="223.2"/>
        <line x1="151.6" y1="228.0" x2="150.0" y2="223.2"/>
    </g>
</svg>
"""

# Same art, re-proportioned for a landscape (297x210) page instead of
# stretching the portrait viewBox to fit — a straight reuse of the
# portrait coordinates would have scaled hugely to cover the wider frame
# and cropped almost everything out. Towers are shorter (less vertical
# room available) and the mesh is compressed into the same upper-right band.
_COVER_GRID_SVG_LANDSCAPE = """
<svg viewBox="0 0 297 210" preserveAspectRatio="xMaxYMax slice" xmlns="http://www.w3.org/2000/svg">
    <g stroke="#1489D1" stroke-width="0.4" fill="none" opacity="0.4">
        <line x1="178.2" y1="16.8" x2="199.0" y2="33.6"/>
        <line x1="178.2" y1="16.8" x2="222.8" y2="18.9"/>
        <line x1="199.0" y1="33.6" x2="222.8" y2="18.9"/>
        <line x1="199.0" y1="33.6" x2="246.5" y2="42.0"/>
        <line x1="222.8" y1="18.9" x2="246.5" y2="42.0"/>
        <line x1="222.8" y1="18.9" x2="231.7" y2="56.7"/>
        <line x1="246.5" y1="42.0" x2="267.3" y2="21.0"/>
        <line x1="246.5" y1="42.0" x2="231.7" y2="56.7"/>
        <line x1="267.3" y1="21.0" x2="285.1" y2="54.6"/>
        <line x1="267.3" y1="21.0" x2="255.4" y2="58.8"/>
        <line x1="285.1" y1="54.6" x2="255.4" y2="58.8"/>
        <line x1="255.4" y1="58.8" x2="231.7" y2="56.7"/>
        <line x1="255.4" y1="58.8" x2="207.9" y2="63.0"/>
        <line x1="231.7" y1="56.7" x2="207.9" y2="63.0"/>
        <line x1="231.7" y1="56.7" x2="190.1" y2="48.3"/>
        <line x1="207.9" y1="63.0" x2="190.1" y2="48.3"/>
        <line x1="207.9" y1="63.0" x2="279.2" y2="88.2"/>
        <line x1="207.9" y1="63.0" x2="249.5" y2="84.0"/>
        <line x1="190.1" y1="48.3" x2="216.8" y2="79.8"/>
        <line x1="190.1" y1="48.3" x2="196.0" y2="92.4"/>
        <line x1="216.8" y1="79.8" x2="249.5" y2="84.0"/>
        <line x1="216.8" y1="79.8" x2="196.0" y2="92.4"/>
        <line x1="249.5" y1="84.0" x2="279.2" y2="88.2"/>
    </g>
    <g fill="#D9A400" opacity="0.9">
        <circle cx="207.9" cy="63.0" r="1.3"/>
        <circle cx="199.0" cy="33.6" r="1.3"/>
        <circle cx="216.8" cy="79.8" r="1.3"/>
        <circle cx="267.3" cy="21.0" r="1.3"/>
    </g>
    <g fill="#1489D1" opacity="0.65">
        <circle cx="178.2" cy="16.8" r="1"/>
        <circle cx="222.8" cy="18.9" r="1"/>
        <circle cx="246.5" cy="42.0" r="1"/>
        <circle cx="285.1" cy="54.6" r="1"/>
        <circle cx="255.4" cy="58.8" r="1"/>
        <circle cx="231.7" cy="56.7" r="1"/>
        <circle cx="190.1" cy="48.3" r="1"/>
        <circle cx="249.5" cy="84.0" r="1"/>
        <circle cx="279.2" cy="88.2" r="1"/>
        <circle cx="196.0" cy="92.4" r="1"/>
    </g>
    <g stroke="#1489D1" stroke-width="0.5" fill="none" opacity="0.32" stroke-linejoin="round" stroke-linecap="round">
        <line x1="245.0" y1="209.0" x2="256.2" y2="131.0"/>
        <line x1="271.0" y1="209.0" x2="259.8" y2="131.0"/>
        <line x1="258.0" y1="209.0" x2="258.0" y2="131.0"/>
        <line x1="246.3" y1="199.6" x2="269.7" y2="199.6"/>
        <line x1="247.7" y1="189.5" x2="268.3" y2="189.5"/>
        <line x1="246.3" y1="199.6" x2="268.3" y2="189.5"/>
        <line x1="269.7" y1="199.6" x2="247.7" y2="189.5"/>
        <line x1="249.1" y1="179.4" x2="266.9" y2="179.4"/>
        <line x1="247.7" y1="189.5" x2="266.9" y2="179.4"/>
        <line x1="268.3" y1="189.5" x2="249.1" y2="179.4"/>
        <line x1="250.4" y1="169.2" x2="265.6" y2="169.2"/>
        <line x1="249.1" y1="179.4" x2="265.6" y2="169.2"/>
        <line x1="266.9" y1="179.4" x2="250.4" y2="169.2"/>
        <line x1="251.8" y1="159.1" x2="264.2" y2="159.1"/>
        <line x1="250.4" y1="169.2" x2="264.2" y2="159.1"/>
        <line x1="265.6" y1="169.2" x2="251.8" y2="159.1"/>
        <line x1="253.1" y1="149.7" x2="262.9" y2="149.7"/>
        <line x1="251.8" y1="159.1" x2="262.9" y2="149.7"/>
        <line x1="264.2" y1="159.1" x2="253.1" y2="149.7"/>
        <line x1="252.5" y1="163.8" x2="263.5" y2="163.8"/>
        <line x1="252.5" y1="163.8" x2="252.5" y2="167.3"/>
        <line x1="263.5" y1="163.8" x2="263.5" y2="167.3"/>
        <line x1="251.5" y1="154.4" x2="264.5" y2="154.4"/>
        <line x1="251.5" y1="154.4" x2="251.5" y2="157.9"/>
        <line x1="264.5" y1="154.4" x2="264.5" y2="157.9"/>
        <line x1="253.3" y1="145.8" x2="262.7" y2="145.8"/>
        <line x1="253.3" y1="145.8" x2="253.3" y2="149.3"/>
        <line x1="262.7" y1="145.8" x2="262.7" y2="149.3"/>
        <line x1="256.2" y1="131.0" x2="258.0" y2="125.5"/>
        <line x1="259.8" y1="131.0" x2="258.0" y2="125.5"/>
    </g>
    <g stroke="#1489D1" stroke-width="0.45" fill="none" opacity="0.20" stroke-linejoin="round" stroke-linecap="round">
        <line x1="214.0" y1="209.0" x2="220.8" y2="163.0"/>
        <line x1="230.0" y1="209.0" x2="223.2" y2="163.0"/>
        <line x1="222.0" y1="209.0" x2="222.0" y2="163.0"/>
        <line x1="214.8" y1="203.5" x2="229.2" y2="203.5"/>
        <line x1="215.6" y1="197.5" x2="228.4" y2="197.5"/>
        <line x1="214.8" y1="203.5" x2="228.4" y2="197.5"/>
        <line x1="229.2" y1="203.5" x2="215.6" y2="197.5"/>
        <line x1="216.5" y1="191.5" x2="227.5" y2="191.5"/>
        <line x1="215.6" y1="197.5" x2="227.5" y2="191.5"/>
        <line x1="228.4" y1="197.5" x2="216.5" y2="191.5"/>
        <line x1="217.3" y1="185.5" x2="226.7" y2="185.5"/>
        <line x1="216.5" y1="191.5" x2="226.7" y2="185.5"/>
        <line x1="227.5" y1="191.5" x2="217.3" y2="185.5"/>
        <line x1="218.2" y1="179.6" x2="225.8" y2="179.6"/>
        <line x1="217.3" y1="185.5" x2="225.8" y2="179.6"/>
        <line x1="226.7" y1="185.5" x2="218.2" y2="179.6"/>
        <line x1="219.0" y1="174.0" x2="225.0" y2="174.0"/>
        <line x1="218.2" y1="179.6" x2="225.0" y2="174.0"/>
        <line x1="225.8" y1="179.6" x2="219.0" y2="174.0"/>
        <line x1="218.6" y1="182.3" x2="225.4" y2="182.3"/>
        <line x1="218.6" y1="182.3" x2="218.6" y2="184.4"/>
        <line x1="225.4" y1="182.3" x2="225.4" y2="184.4"/>
        <line x1="218.0" y1="176.8" x2="226.0" y2="176.8"/>
        <line x1="218.0" y1="176.8" x2="218.0" y2="178.9"/>
        <line x1="226.0" y1="176.8" x2="226.0" y2="178.9"/>
        <line x1="219.1" y1="171.7" x2="224.9" y2="171.7"/>
        <line x1="219.1" y1="171.7" x2="219.1" y2="173.8"/>
        <line x1="224.9" y1="171.7" x2="224.9" y2="173.8"/>
        <line x1="220.8" y1="163.0" x2="222.0" y2="159.8"/>
        <line x1="223.2" y1="163.0" x2="222.0" y2="159.8"/>
    </g>
</svg>
"""


def render_mgmt_cover(context: dict) -> str:
    """Render the management report cover page.

    Single canonical cover renderer for EVERY report in the app, portrait
    or landscape — the generic flexible report builder, TMO Management
    Report, and Commercial Management Report all call this one function
    rather than each keeping their own copy of the cover HTML. A fix or
    design change made here applies everywhere at once; before this
    consolidation (2026-08-18) there were 3 separate near-duplicate cover
    functions that had quietly drifted out of sync with each other, and
    landscape reports used a 4th, entirely different implementation.

    context['orientation'] picks between the portrait- and
    landscape-proportioned background art (_COVER_GRID_SVG_PORTRAIT /
    _COVER_GRID_SVG_LANDSCAPE) — reusing one viewBox for both would either
    stretch it (wrong aspect ratio) or leave the wrong amount of empty
    canvas depending on which way round the page is.

    context['cover_eyebrow'] overrides the small label under the company
    name (default 'Performance Monitoring Tool') — e.g. 'Transmission &
    Market Operations' for the TMO report, 'Commercial' for the commercial
    report — everything else about the cover stays identical across report
    types.
    """
    company  = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    title    = context.get('report_title',  'Management Report')
    eyebrow  = context.get('cover_eyebrow', 'Performance Monitoring Tool')
    grid_svg = (
        _COVER_GRID_SVG_LANDSCAPE if context.get('orientation') == 'landscape'
        else _COVER_GRID_SVG_PORTRAIT
    )
    # report_date (always today's real generation date) — NOT period_label.
    # Every other recurring header/footer in the report shows today's date;
    # the actual data period only ever shows up inside the content itself
    # (the DATE KPI card, table titles, etc.), driven by whatever period
    # the frontend requested. Cover/back page chrome follows the same rule
    # (confirmed 2026-08-19) — it isn't a special case.
    date     = context.get('report_date', '')
    subtitle = context.get('report_subtitle', '')
    footer_logo = context.get('footer_logo_url', '')
    logo     = context.get('logo_url', '')

    words = title.split()
    title_main   = ' '.join(words[:-1]) if len(words) > 1 else title
    title_accent = words[-1] if len(words) > 1 else ''

    subtitle_html = (
        f'<div class="cover-subtitle">{subtitle}</div>' if subtitle
        else f'<div class="cover-subtitle">{company}</div>'
    )

    clock_svg = (
        '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>'
        '<path d="M12 7v5l3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>'
    )
    calendar_svg = (
        '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="3" y="5" width="18" height="16" rx="2" stroke="currentColor" stroke-width="2"/>'
        '<path d="M3 9.5h18M8 3v4M16 3v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
        '</svg>'
    )

    return f"""
    <div class="cover-page">
        <div class="cover-accent"></div>
        <div class="cover-grid-art">{grid_svg}</div>
        <div class="cover-body">
            <div class="cover-top-company">{company}</div>

            <div>
                <div class="cover-eyebrow">{eyebrow}</div>
                <h1 class="cover-main-title">
                    {title_main}<br/>
                    <span class="cover-main-title-accent">{title_accent}</span>
                </h1>
                <div class="cover-badge">{clock_svg}<span>Performance Report</span></div>
                <div class="cover-rule"></div>
                {subtitle_html}
            </div>

            <div class="cover-logo-center">
                <img src="{logo}" alt="Company Logo" />
            </div>

            <div class="cover-footer-strip">
                <img src="{footer_logo}" alt="Powered by EMRC" />
                <span class="cover-period">{calendar_svg}<span>{date}</span></span>
            </div>
        </div>
    </div>"""


def render_mgmt_back_page(context: dict) -> str:
    """Single canonical closing/back page — same consolidation as
    render_mgmt_cover: the generic flexible report builder, TMO Management
    Report, and Commercial Management Report all call this one function
    instead of each keeping a near-duplicate copy. Brings the back page in
    line with the cover: real KEDCO logo (was missing entirely from two of
    the three old copies), yellow accent bar and rule (.cover-accent /
    .cover-rule already resolve to gold via MANAGEMENT_STYLES).
    """
    company = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    # report_date (today), not period_label — same rule as render_mgmt_cover.
    date    = context.get('report_date', '')
    logo    = context.get('logo_url', '')

    return f"""
    <div class="cover-page" style="justify-content:center;align-items:center;text-align:center;">
        <div class="cover-accent"></div>
        <div class="cover-body" style="justify-content:center;align-items:center;">
            <div class="cover-logo-center" style="flex:none;margin-bottom:32px;">
                <img src="{logo}" alt="Company Logo" style="max-height:150px;" />
            </div>
            <div style="margin-bottom:32px;">
                <div class="cover-eyebrow" style="text-align:center;margin-bottom:24px;">End of Report</div>
                <h2 style="font-size:30px;font-weight:800;color:#fff;text-transform:uppercase;
                            letter-spacing:-0.5px;line-height:1.1;">{company}</h2>
                <div class="cover-rule" style="margin:24px auto;"></div>
                <div style="font-size:12px;color:#fff;opacity:0.5;letter-spacing:1.5px;
                             text-transform:uppercase;font-weight:600;">{date}</div>
            </div>
            <div style="font-size:10px;color:#fff;opacity:0.3;text-transform:uppercase;
                         letter-spacing:2px;margin-top:60px;">
                Powered by RAVEN &mdash; Performance Monitoring Tool
            </div>
        </div>
    </div>"""


def render_toc_row(num: int, title: str, page, description: str = None) -> str:
    """Single canonical Table-of-Contents row design (numbered circular
    badge, title, optional description line, rounded page-number badge) —
    used by both the generic flexible report builder
    (render_table_of_contents in pdf_generator.py) and every management
    report family (render_toc in tmo_management_report.py) so a design
    change here applies to every report's TOC at once, instead of needing
    to be copied into each report type separately.
    """
    desc_html = f'<div class="toc-description">{description}</div>' if description else ''
    return f"""
        <div class="toc-row">
            <span class="toc-number">{num:02d}</span>
            <div style="flex:1;">
                <div class="toc-title">{title}</div>
                {desc_html}
            </div>
            <span class="toc-dots"></span>
            <span class="toc-page">{page}</span>
        </div>"""


def render_mgmt_executive_summary(narrative: dict, data: dict,
                                   context: dict, page_number: int) -> str:
    es = narrative.get("executive_summary", {})
    p1 = es.get("paragraph_1", "")
    p2 = es.get("paragraph_2", "")
    p3 = es.get("paragraph_3", "")
    p4 = es.get("paragraph_4", "")
    priority = es.get("management_priority", "")

    metrics = data.get("technical_metrics", {})
    relia   = data.get("system_reliability", {})

    kpi_strip = f"""
    <div style="display:flex;gap:0;margin-bottom:14px;border:1px solid rgba(0,32,80,0.12);
                border-radius:10px;overflow:hidden;">
        <div style="flex:1;padding:10px 12px;text-align:center;border-right:1px solid rgba(0,32,80,0.1);">
            <div style="font-size:8px;font-weight:700;text-transform:uppercase;opacity:0.55;margin-bottom:4px;">Avg Supply</div>
            <div style="font-size:19px;font-weight:800;">{metrics.get('hours_of_supply', '—')}<span style="font-size:9px;font-weight:400;opacity:0.6;"> hrs/day</span></div>
        </div>
        <div style="flex:1;padding:10px 12px;text-align:center;border-right:1px solid rgba(0,32,80,0.1);">
            <div style="font-size:8px;font-weight:700;text-transform:uppercase;opacity:0.55;margin-bottom:4px;">Avg Load</div>
            <div style="font-size:19px;font-weight:800;">{metrics.get('average_load', '—')}<span style="font-size:9px;font-weight:400;opacity:0.6;"> MW</span></div>
        </div>
        <div style="flex:1;padding:10px 12px;text-align:center;border-right:1px solid rgba(0,32,80,0.1);">
            <div style="font-size:8px;font-weight:700;text-transform:uppercase;opacity:0.55;margin-bottom:4px;">Energy Delivered</div>
            <div style="font-size:19px;font-weight:800;">{metrics.get('energy_delivered', '—'):,.0f}<span style="font-size:9px;font-weight:400;opacity:0.6;"> MWh</span></div>
        </div>
        <div style="flex:1;padding:10px 12px;text-align:center;">
            <div style="font-size:8px;font-weight:700;text-transform:uppercase;opacity:0.55;margin-bottom:4px;">Interruptions</div>
            <div style="font-size:19px;font-weight:800;">{metrics.get('total_interruptions', '—'):,}</div>
        </div>
    </div>"""

    paras = " ".join(
        f'<p class="narrative">{p}</p>'
        for p in [p1, p2, p3, p4] if p
    )

    callout = ""
    if priority:
        callout = f"""
        <div class="callout">
            <div class="callout-title">Management Priority for Next Reporting Cycle</div>
            <div class="callout-text">{priority}</div>
        </div>"""

    content = f"""
        <h1 class="section-title">1. Executive Management Summary</h1>
        {kpi_strip}
        {paras}
        {callout}"""

    return _page(content, context, page_number)


def render_mgmt_kpi_dashboard(narrative: dict, data: dict,
                               context: dict, page_number: int) -> str:
    kpi_status = narrative.get("kpi_status", {})
    metrics    = data.get("technical_metrics", {})
    prev       = data.get("previous_metrics", {})
    relia      = data.get("system_reliability", {})

    def _prev_val(key, fallback="—"):
        v = prev.get(key)
        return str(v) if v is not None else fallback

    def _row(kpi_key, label, current_str, previous_str, unit=""):
        info   = kpi_status.get(kpi_key, {})
        status = info.get("status", "grey")
        interp = info.get("interpretation", "")
        move   = _movement_html(current_str.replace(unit, "").strip(),
                                previous_str.replace(unit, "").strip())
        return f"""
        <tr>
            <td>{label}</td>
            <td><strong>{current_str}</strong></td>
            <td>{previous_str}</td>
            <td>{move}</td>
            <td style="max-width:130px;line-height:1.4;">{interp}</td>
            <td>{_rag_html(status)}</td>
        </tr>"""

    hs   = metrics.get("hours_of_supply", 0)
    al   = metrics.get("average_load", 0)
    ed   = metrics.get("energy_delivered", 0)
    ti   = metrics.get("total_interruptions", 0)
    cih  = relia.get("cumulative_interruption_hours", 0)
    aid  = relia.get("avg_duration_of_interruption", 0)
    tat  = relia.get("avg_turnaround_time", 0)

    rows = (
        _row("average_supply",                "Average Hours of Supply",     f"{hs:,.2f} hrs/day",
             f"{prev.get('hours_of_supply', '—')} hrs/day") +
        _row("average_load",                  "Average Load",                f"{al:,.2f} MW",
             f"{prev.get('average_load', '—')} MW") +
        _row("energy_delivered",              "Energy Delivered",            f"{ed:,.2f} MWh",
             f"{prev.get('energy_delivered', '—')} MWh") +
        _row("total_interruptions",           "Total Interruptions",         f"{ti:,}",
             f"{prev.get('total_interruptions', '—')}") +
        _row("cumulative_interruption_hours", "Cumulative Interruption Hrs", f"{cih} hrs",
             "—") +
        _row("avg_interruption_duration",     "Avg Interruption Duration",   f"{aid} hrs",
             "—") +
        _row("local_fault_tat",               "Local Fault Turnaround Time", f"{tat} hrs",
             "—")
    )

    key_msg = narrative.get("executive_summary", {}).get(
        "management_priority",
        "Supply availability improved, but reliability, service-band performance and data quality still require management attention."
    )

    content = f"""
        <h1 class="section-title">2. Headline KPI Dashboard</h1>
        <table class="mgmt-table kpi-table">
            <thead>
                <tr>
                    <th style="width:22%">KPI</th>
                    <th style="width:15%">Current Period</th>
                    <th style="width:14%">Previous</th>
                    <th style="width:10%">Movement</th>
                    <th style="width:27%">Management Interpretation</th>
                    <th style="width:12%">Status</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        <div class="callout">
            <div class="callout-title">Key Message</div>
            <div class="callout-text">{key_msg}</div>
        </div>"""

    return _page(content, context, page_number)


def _auto_interp(cat: str, count: int, total_hrs: float, avg_dur: float) -> str:
    """Data-driven fallback interpretation when AI text is unavailable for a category."""
    c = cat.strip().upper()
    if 'L/S' in c and 'GS' not in c and '330' not in c:
        label = "Load shedding"
    elif 'TCN' in c:
        label = "TCN-driven outage"
    elif 'E/F' in c:
        label = "Equipment fault"
    elif 'L/F' in c:
        label = "Line fault"
    elif 'O/S' in c:
        label = "Out-of-service event"
    elif 'PERMIT' in c:
        label = "Permitted/scheduled outage"
    elif 'P/M' in c:
        label = "Planned maintenance"
    elif 'MTNC' in c:
        label = "Maintenance outage"
    elif 'EM/D' in c or 'EMD' in c:
        label = "Emergency dispatch"
    else:
        label = cat
    sev = "High-frequency" if count > 100 else ("Significant" if count > 15 else "Low-frequency")
    dur = "long-duration" if avg_dur > 10 else ("moderate-duration" if avg_dur > 5 else "short-duration")
    return (f"{label}: {sev} {dur} — {count:,} events, {total_hrs:,.1f} total hours, "
            f"{avg_dur:.1f} hrs avg per event.")


def render_mgmt_reliability_review(narrative: dict, data: dict,
                                    context: dict, page_number: int):
    """Returns (html, pages_used)."""
    interr_data  = data.get("interruption_breakdown", [])
    intro        = narrative.get("reliability_intro", "")
    implications = narrative.get("interruption_implications", [])

    # Build a lookup from the AI implications list keyed by issue/category name
    impl_lookup = {}
    for imp in (implications or []):
        key = imp.get('issue', '').strip().upper()
        if key:
            impl_lookup[key] = imp.get('implication', '') or imp.get('response', '')

    # ── Page 1: Interruption breakdown table ──
    rows_html = ""
    for item in (interr_data or []):
        cat   = item.get('type', '—')
        count = item.get('count', 0)
        total = float(item.get('total_hours', 0))
        avg   = float(item.get('avg_duration', 0))
        # Use AI text if key matched, otherwise auto-generate from data
        note  = (impl_lookup.get(cat.strip().upper())
                 or item.get('management_note', '')
                 or _auto_interp(cat, count, total, avg))
        rows_html += f"""
        <tr>
            <td><strong>{cat}</strong></td>
            <td style="text-align:right">{item.get('count', 0):,}</td>
            <td style="text-align:right">{item.get('total_hours', 0)} hrs</td>
            <td style="text-align:right">{item.get('avg_duration', 0)} hrs</td>
            <td style="line-height:1.4;">{note}</td>
        </tr>"""

    intro_html = f'<p class="narrative">{intro}</p>' if intro else ""

    p1_content = f"""
        <h1 class="section-title">3. Reliability and Interruption Review</h1>
        {intro_html}
        <div class="subsection-title">4.1 Interruption Breakdown</div>
        <table class="mgmt-table">
            <thead>
                <tr>
                    <th style="width:13%">Category</th>
                    <th style="text-align:right;width:9%">Count</th>
                    <th style="text-align:right;width:13%">Total Hours</th>
                    <th style="text-align:right;width:13%">Avg Duration</th>
                    <th style="width:52%">Management Interpretation</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>"""

    page1 = _page(p1_content, context, page_number)
    pages_used = 1

    # ── Page 2: Reliability implications (only if AI provided them) ──
    if implications:
        impl_rows = ""
        for imp in implications:
            impl_rows += f"""
            <tr>
                <td><strong>{imp.get('issue', '—')}</strong></td>
                <td style="line-height:1.4;">{imp.get('implication', '')}</td>
                <td style="line-height:1.4;">{imp.get('response', '')}</td>
            </tr>"""

        p2_content = f"""
            <div class="subsection-title">4.2 Reliability Management Implications</div>
            <table class="mgmt-table">
                <thead>
                    <tr>
                        <th style="width:22%">Issue</th>
                        <th style="width:38%">Implication</th>
                        <th style="width:40%">Required Management Response</th>
                    </tr>
                </thead>
                <tbody>{impl_rows}</tbody>
            </table>"""

        page2 = _page(p2_content, context, page_number + 1)
        return page1 + page2, 2

    return page1, pages_used


def render_mgmt_feeder_review(narrative: dict, data: dict,
                               context: dict, page_number: int):
    """Returns (html, pages_used)."""
    feeders  = data.get("feeder_performance", []) or []
    sorted_f = sorted(feeders, key=lambda f: float(f.get("hours_of_supply", 0)), reverse=True)
    top10    = sorted_f[:10]
    weak10   = [f for f in sorted_f if float(f.get("hours_of_supply", 0)) < 8][:10]

    strong_comm = narrative.get("feeder_strong_commentary", "")
    weak_comm1  = narrative.get("feeder_weak_commentary_1", "")
    weak_comm2  = narrative.get("feeder_weak_commentary_2", "")

    def _feeder_row(f, show_concern=False):
        note = "Data exception requiring validation." if (
            float(f.get("hours_of_supply", 0)) == 0 and
            float(f.get("energy_delivered", 0)) > 0
        ) else ("No reported supply or energy." if
                 float(f.get("hours_of_supply", 0)) == 0 and
                 float(f.get("energy_delivered", 0)) == 0
                 else "Weak service delivery." if show_concern else "Strong supply availability.")
        return f"""
        <tr>
            <td>{f.get('name', '—')}</td>
            <td style="text-align:center">{f.get('band', '—')}</td>
            <td style="text-align:right">{float(f.get('hours_of_supply', 0)):,.2f} hrs</td>
            <td style="text-align:right">{float(f.get('availability_percentage', 0)):,.1f}%</td>
            <td style="text-align:right">{float(f.get('energy_delivered', 0)):,.2f} MWh</td>
            <td style="line-height:1.4;">{note}</td>
        </tr>"""

    header_row = """
        <thead>
            <tr>
                <th style="width:28%">Feeder</th>
                <th style="text-align:center;width:7%">Band</th>
                <th style="text-align:right;width:14%">Avg Supply</th>
                <th style="text-align:right;width:13%">Availability</th>
                <th style="text-align:right;width:16%">Energy (MWh)</th>
                <th style="width:22%">Management Interpretation</th>
            </tr>
        </thead>"""

    strong_rows = "".join(_feeder_row(f, show_concern=False) for f in top10)
    weak_rows   = "".join(_feeder_row(f, show_concern=True)  for f in weak10)

    comm_html_strong = (f'<p class="narrative">{strong_comm}</p>') if strong_comm else ""
    comm_html_weak1  = (f'<p class="narrative">{weak_comm1}</p>') if weak_comm1 else ""
    comm_html_weak2  = (f'<p class="narrative">{weak_comm2}</p>') if weak_comm2 else ""

    p1_content = f"""
        <h1 class="section-title">4. Feeder Performance Review</h1>
        <div class="subsection-title">Stronger-Performing Feeders</div>
        {comm_html_strong}
        <table class="mgmt-table">
            {header_row}
            <tbody>{strong_rows}</tbody>
        </table>"""

    p2_content = f"""
        <div class="subsection-title">Weak and Exception Feeders</div>
        {comm_html_weak1}
        <table class="mgmt-table">
            {header_row}
            <tbody>{weak_rows}</tbody>
        </table>
        {comm_html_weak2}"""

    return _page(p1_content, context, page_number) + _page(p2_content, context, page_number + 1), 2


def render_mgmt_state_review(narrative: dict, data: dict,
                              context: dict, page_number: int) -> str:
    states = data.get("state_performance", []) or []
    intro  = narrative.get("state_review_intro", "")
    conclu = narrative.get("state_conclusion", "")

    state_rows = ""
    for s in states:
        fc   = s.get("feeder_count", 0)
        intr = s.get("interruptions", 0)
        intensity = round(intr / fc, 1) if fc else 0
        state_rows += f"""
        <tr>
            <td><strong>{s.get('state_name', '—')}</strong></td>
            <td style="text-align:right">{fc:,}</td>
            <td style="text-align:right">{float(s.get('hours_of_supply', 0)):,.2f} hrs</td>
            <td style="text-align:right">{float(s.get('availability_percentage', 0)):,.1f}%</td>
            <td style="text-align:right">{intr:,}</td>
            <td style="text-align:right">{intensity}</td>
            <td style="text-align:right">{float(s.get('peak_load', 0)):,.2f} MW</td>
        </tr>"""

    intro_html = f'<p class="narrative">{intro}</p>' if intro else ""
    callout    = (f'<div class="callout"><div class="callout-title">State-Level Conclusion</div>'
                  f'<div class="callout-text">{conclu}</div></div>') if conclu else ""

    content = f"""
        <h1 class="section-title">5. State Performance Review</h1>
        {intro_html}
        <table class="mgmt-table">
            <thead>
                <tr>
                    <th style="width:16%">State</th>
                    <th style="text-align:right;width:9%">Feeders</th>
                    <th style="text-align:right;width:14%">Avg Supply</th>
                    <th style="text-align:right;width:12%">Availability</th>
                    <th style="text-align:right;width:12%">Interruptions</th>
                    <th style="text-align:right;width:15%">Intr/Feeder</th>
                    <th style="text-align:right;width:12%">Peak Load</th>
                </tr>
            </thead>
            <tbody>{state_rows}</tbody>
        </table>
        {callout}"""

    return _page(content, context, page_number)


def render_mgmt_service_band_review(narrative: dict, data: dict,
                                     context: dict, page_number: int) -> str:
    bands     = data.get("service_band_summary", []) or []
    intro     = narrative.get("service_band_intro", "")
    band_int  = narrative.get("band_interpretations", {})
    recovery  = narrative.get("recovery_actions", [])

    band_rows = ""
    for b in bands:
        band_letter = str(b.get("band", "?"))
        interp = band_int.get(band_letter, "")
        band_rows += f"""
        <tr>
            <td><strong>Band {band_letter}</strong></td>
            <td style="text-align:right">{b.get('feeder_count', 0):,}</td>
            <td style="text-align:right">{b.get('hours_of_supply', '—')} hrs</td>
            <td style="text-align:right">{b.get('interruptions', 0):,}</td>
            <td style="line-height:1.4;font-size:9px;">{interp}</td>
        </tr>"""

    recovery_rows = ""
    for r in recovery:
        recovery_rows += f"""
        <tr>
            <td><strong>Band {r.get('band', '?')}</strong></td>
            <td style="line-height:1.4;">{r.get('issue', '')}</td>
            <td style="line-height:1.4;">{r.get('action', '')}</td>
        </tr>"""

    intro_html = f'<p class="narrative">{intro}</p>' if intro else ""

    content = f"""
        <h1 class="section-title">6. Service Band Performance Review</h1>
        {intro_html}
        <table class="mgmt-table">
            <thead>
                <tr>
                    <th style="width:12%">Band</th>
                    <th style="text-align:right;width:10%">Feeders</th>
                    <th style="text-align:right;width:14%">Avg Supply</th>
                    <th style="text-align:right;width:14%">Interruptions</th>
                    <th style="width:50%">Management Interpretation</th>
                </tr>
            </thead>
            <tbody>{band_rows}</tbody>
        </table>
        <div class="subsection-title">Recommended Service-Band Recovery Actions</div>
        <table class="mgmt-table">
            <thead>
                <tr>
                    <th style="width:10%">Band</th>
                    <th style="width:38%">Key Issue</th>
                    <th style="width:52%">Recommended Management Action</th>
                </tr>
            </thead>
            <tbody>{recovery_rows}</tbody>
        </table>"""

    return _page(content, context, page_number)


def render_mgmt_priority_issues(narrative: dict, context: dict,
                                 page_number: int) -> tuple[str, int]:
    """Returns (html, pages_used)."""
    issues = narrative.get("priority_issues", [])

    chunks = [issues[i:i+7] for i in range(0, max(len(issues), 1), 7)]
    html   = ""

    for idx, chunk in enumerate(chunks):
        pnum    = page_number + idx
        suffix  = " (continued)" if idx > 0 else ""
        rows    = ""
        for iss in chunk:
            rows += f"""
            <tr>
                <td style="line-height:1.4;"><strong>{iss.get('issue','—')}</strong></td>
                <td style="line-height:1.4;">{iss.get('evidence','')}</td>
                <td style="line-height:1.4;">{iss.get('risk','')}</td>
                <td style="line-height:1.4;">{iss.get('response','')}</td>
            </tr>"""

        content = f"""
            <h1 class="section-title">7. Priority Management Issues{suffix}</h1>
            <table class="mgmt-table">
                <thead>
                    <tr>
                        <th style="width:20%">Priority Issue</th>
                        <th style="width:22%">Evidence from Report</th>
                        <th style="width:25%">Management Risk</th>
                        <th style="width:33%">Required Response</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>"""

        html += _page(content, context, pnum)

    return html, len(chunks)


def render_mgmt_action_plan(narrative: dict, context: dict,
                             page_number: int) -> tuple[str, int]:
    """Returns (html, pages_used)."""
    actions = narrative.get("action_plan", [])

    chunks = [actions[i:i+8] for i in range(0, max(len(actions), 1), 8)]
    html   = ""

    for idx, chunk in enumerate(chunks):
        pnum   = page_number + idx
        suffix = " (continued)" if idx > 0 else ""
        rows   = ""
        for act in chunk:
            rows += f"""
            <tr>
                <td style="line-height:1.4;"><strong>{act.get('area','—')}</strong></td>
                <td style="line-height:1.4;">{act.get('action','')}</td>
                <td style="line-height:1.4;">{act.get('team','')}</td>
                <td style="text-align:center;">{act.get('timeline','')}</td>
                <td style="line-height:1.4;">{act.get('output','')}</td>
            </tr>"""

        content = f"""
            <h1 class="section-title">8. Recommended Management Action Plan{suffix}</h1>
            <table class="mgmt-table">
                <thead>
                    <tr>
                        <th style="width:16%">Action Area</th>
                        <th style="width:28%">Recommended Action</th>
                        <th style="width:18%">Responsible Team</th>
                        <th style="text-align:center;width:13%">Timeline</th>
                        <th style="width:25%">Expected Output</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>"""

        html += _page(content, context, pnum)

    return html, len(chunks)


# =============================================================================
# MAIN GENERATOR CLASS
# =============================================================================

class ManagementPDFGenerator:
    """
    Generates the management / admin report PDF.

    Usage:
        generator = ManagementPDFGenerator(report_config, data_service)
        pdf_bytes = generator.generate_pdf()
    """

    def __init__(self, report_config: dict, data_service):
        from django.conf import settings
        from reports.pdf_generator import _build_theme_css, _hex_to_rgb

        self.report_config = report_config
        self.data_service  = data_service
        self.include_ai    = report_config.get('include_ai', True)

        # Theme — matches the cover page's navy (#001634); PDFGenerator in
        # pdf_generator.py got the same update, same reasoning.
        raw_theme = report_config.get('theme') or {}
        self.theme = {
            'primary_color': raw_theme.get('primary_color') or '#001634',
            'accent_color':  raw_theme.get('accent_color')  or 'rgba(0, 22, 52, 0.2)',
            'text_color':    raw_theme.get('text_color')    or '#001634',
        }

        try:
            r, g, b = _hex_to_rgb(self.theme['primary_color'])
            self.theme['primary_light'] = f"rgba({r},{g},{b},0.05)"
        except Exception:
            self.theme['primary_light'] = "rgba(0,32,80,0.05)"

        self._build_theme_css = lambda: _build_theme_css(
            self.theme['primary_color'],
            self.theme['accent_color'],
            self.theme['text_color'],
        )

        # Context dict passed to all renderers
        self.context = self._build_context()

    def _build_context(self) -> dict:
        from reports.services import ReportDataService
        filters   = self.data_service.filters
        from_date = self.data_service.from_date
        to_date   = self.data_service.to_date

        period_label = ReportDataService._period_label(from_date, to_date)

        return {
            'company_name':   self.report_config.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY'),
            'report_title':   self.report_config.get('report_title', 'Management Report'),
            'report_subtitle': self.report_config.get('report_subtitle', ''),
            'report_date':    period_label,
            'period_label':   period_label,
            'logo_url':       self._get_static_url('reports/images/kedco_logo.png'),
            'logo_gray_url':  self._get_static_url('reports/images/kedco_gray_logo.png'),
            'footer_logo_url': self._get_static_url('reports/images/footer_logo.png'),
            **self.theme,
        }

    def _get_static_url(self, path: str) -> str:
        """Return image as base64 data URI — same helper as PDFGenerator."""
        import os
        from django.conf import settings

        candidates = [
            os.path.join(d, path)
            for d in getattr(settings, 'STATICFILES_DIRS', [])
        ]
        static_root = getattr(settings, 'STATIC_ROOT', None)
        if static_root:
            candidates.append(os.path.join(static_root, path))

        for abs_path in candidates:
            if os.path.isfile(abs_path):
                ext  = os.path.splitext(abs_path)[1].lower().lstrip('.')
                mime = {'png': 'image/png', 'jpg': 'image/jpeg',
                        'jpeg': 'image/jpeg', 'svg': 'image/svg+xml'}.get(ext, 'image/png')
                with open(abs_path, 'rb') as f:
                    data = base64.b64encode(f.read()).decode()
                return f"data:{mime};base64,{data}"

        static_url = getattr(settings, 'STATIC_URL', '/static/')
        base_url   = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        return f"{base_url.rstrip('/')}{static_url}{path}"

    def _gather_data(self) -> dict:
        """Pull all data needed for the management report."""
        ds = self.data_service
        try:
            technical = ds.get_technical_metrics()
        except Exception:
            technical = {}
        try:
            reliability = ds.get_system_reliability()
        except Exception:
            reliability = {}
        try:
            interruptions = ds.get_interruption_breakdown()
        except Exception:
            interruptions = []
        try:
            feeders = ds.get_feeder_performance()
        except Exception:
            feeders = []
        try:
            states = ds.get_state_performance()
        except Exception:
            states = []
        try:
            bands = ds.get_service_band_summary()
        except Exception:
            bands = []

        # Previous-period comparison — same-length window immediately before current
        previous = {}
        try:
            import datetime
            from reports.services import ReportDataService as _RDS
            period_days = (ds.to_date - ds.from_date).days + 1
            prev_to     = ds.from_date - datetime.timedelta(days=1)
            prev_from   = prev_to - datetime.timedelta(days=period_days - 1)
            prev_filters = dict(ds.filters)
            prev_filters['from_date'] = str(prev_from)
            prev_filters['to_date']   = str(prev_to)
            prev_ds  = _RDS(prev_filters)
            prev_tech = prev_ds.get_technical_metrics()
            previous = {
                'hours_of_supply':    prev_tech.get('hours_of_supply'),
                'average_load':       prev_tech.get('average_load'),
                'energy_delivered':   prev_tech.get('energy_delivered'),
                'total_interruptions': prev_tech.get('total_interruptions'),
            }
        except Exception:
            pass

        return {
            'technical_metrics':   technical,
            'previous_metrics':    previous,
            'system_reliability':  reliability,
            'interruption_breakdown': interruptions,
            'feeder_performance':  feeders,
            'state_performance':   states,
            'service_band_summary': bands,
        }

    def generate_html(self) -> str:
        """Assemble the full management report HTML."""
        all_data     = self._gather_data()
        period_label = self.context['period_label']
        company      = self.context['company_name']

        # Generate AI narrative (or fallback)
        if self.include_ai:
            narrative = generate_management_narrative(all_data, period_label, company)
        else:
            narrative = _fallback_narrative()

        # ── Build sections ─────────────────────────────────────────────────────
        sections_html = render_mgmt_cover(self.context)

        page = 2   # page 1 is the cover

        sections_html += render_mgmt_executive_summary(narrative, all_data, self.context, page)
        page += 1

        sections_html += render_mgmt_kpi_dashboard(narrative, all_data, self.context, page)
        page += 1

        reliability_html, rp = render_mgmt_reliability_review(narrative, all_data, self.context, page)
        sections_html += reliability_html
        page += rp

        feeder_html, fp = render_mgmt_feeder_review(narrative, all_data, self.context, page)
        sections_html += feeder_html
        page += fp

        sections_html += render_mgmt_state_review(narrative, all_data, self.context, page)
        page += 1

        sections_html += render_mgmt_service_band_review(narrative, all_data, self.context, page)
        page += 1

        priority_html, pp = render_mgmt_priority_issues(narrative, self.context, page)
        sections_html += priority_html
        page += pp

        action_html, ap = render_mgmt_action_plan(narrative, self.context, page)
        sections_html += action_html
        page += ap

        sections_html += render_mgmt_back_page(self.context)

        # ── Apply theme CSS on top of management styles ────────────────────────
        theme_css = self._build_theme_css()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{self.context['report_title']}</title>
    <style>
        {MANAGEMENT_STYLES}
        {theme_css}
    </style>
</head>
<body>
    {sections_html}
</body>
</html>"""

    def generate_pdf(self) -> io.BytesIO:
        html = self.generate_html()

        if _PLAYWRIGHT_AVAILABLE:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page    = browser.new_page()
                    page.set_content(html, wait_until='domcontentloaded')
                    pdf_bytes = page.pdf(
                        format='A4',
                        landscape=False,
                        print_background=True,
                        margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'},
                    )
                    browser.close()
                return io.BytesIO(pdf_bytes)
            except Exception as pw_err:
                logger.warning("Playwright failed for management report (%s), falling back.", pw_err)

        if _WEASYPRINT_AVAILABLE:
            fc  = _FontConfig()
            buf = io.BytesIO()
            _WeasyHTML(string=html).write_pdf(buf, font_config=fc)
            buf.seek(0)
            return buf

        raise RuntimeError(
            "No PDF engine available. "
            "Install Playwright: pip install playwright && playwright install chromium"
        )

    def generate_pdf_base64(self) -> str:
        return base64.b64encode(self.generate_pdf().read()).decode()
