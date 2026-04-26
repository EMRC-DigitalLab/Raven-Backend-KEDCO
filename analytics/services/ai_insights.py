"""
analytics/services/ai_insights.py

Claude AI insights for the commercial customer comparison engine.

Flow:
  1. Hash the comparison parameters → cache key
  2. Check CommercialComparisonCache — return cached insights if still valid
  3. Build a focused prompt from the comparison data
  4. Call Anthropic API (claude-sonnet-4-6)
  5. Parse the structured JSON response
  6. Store in cache (24-hour TTL)
  7. Return insights dict

The insights are purposefully helpful and conversational — not rigid rankings.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─── Cache key ─────────────────────────────────────────────────────────────────

def _build_cache_key(
    customer_type: str,
    current_from: date,
    current_to: date,
    previous_from: date,
    previous_to: date,
    scope_type: str | None,
    scope_id,
    positive_threshold: float,
    declined_threshold: float,
) -> str:
    payload = json.dumps({
        'ct':  customer_type,
        'cf':  current_from.isoformat(),
        'cto': current_to.isoformat(),
        'pf':  previous_from.isoformat(),
        'pto': previous_to.isoformat(),
        'st':  scope_type,
        'sid': str(scope_id) if scope_id else None,
        'pos': positive_threshold,
        'dec': declined_threshold,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# ─── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(comparison: dict) -> str:
    totals   = comparison.get('totals', {})
    trend_d  = comparison.get('trend_distribution', {})
    ctype    = comparison.get('customer_type', 'all')
    scope    = comparison.get('scope', {})
    cur_per  = comparison.get('current_period', {})
    prev_per = comparison.get('previous_period', {})

    # Pick top 5 decliners and top 5 growers to include in the prompt
    customers = comparison.get('customers', [])
    with_pct  = [c for c in customers if c.get('variance_pct') is not None]
    top_grow  = sorted(with_pct, key=lambda c: -(c['variance_pct'] or 0))[:5]
    top_decl  = sorted(with_pct, key=lambda c: (c['variance_pct'] or 0))[:5]

    def fmt_customer(c):
        return (
            f"  - {c.get('customer_name', 'Unknown')} ({c.get('account_no', '')}) | "
            f"Feeder: {c.get('feeder', '—')} | "
            f"Current: {c.get('current_consumption_kwh', 0):,.1f} kWh | "
            f"Prev: {c.get('previous_consumption_kwh', 0):,.1f} kWh | "
            f"Change: {c.get('variance_pct', 0):+.1f}%"
        )

    growers_text  = "\n".join(fmt_customer(c) for c in top_grow)  or "  None"
    decliners_text = "\n".join(fmt_customer(c) for c in top_decl) or "  None"

    scope_label = scope.get('label') or 'All feeders / KEDCO-wide'
    ctype_label = {'MDI': 'Maximum Demand Industrial (MDI)',
                   'MDNI': 'Maximum Demand Non-Industrial (MDNI)',
                   'all': 'All MDI + MDNI'}.get(ctype, ctype)

    curr_kwh  = totals.get('current_consumption_kwh', 0) or 0
    prev_kwh  = totals.get('previous_consumption_kwh', 0) or 0
    var_pct   = totals.get('variance_pct')
    curr_amt  = totals.get('current_billed_amount', 0) or 0
    prev_amt  = totals.get('previous_billed_amount', 0) or 0
    bv        = totals.get('billing_variance', 0) or 0

    trend_summary = ", ".join(
        f"{v} {k}" for k, v in trend_d.items() if v > 0
    )

    var_str = f"{var_pct:+.1f}%" if var_pct is not None else "n/a (no previous baseline)"

    prompt = f"""You are an energy analyst for KEDCO (Kano Electricity Distribution Company) in Nigeria.
Analyse the following MDI/MDNI customer consumption comparison and return a JSON object with insights.

## Comparison Context
Customer type : {ctype_label}
Scope         : {scope_label}
Current period: {cur_per.get('from_date')} to {cur_per.get('to_date')} ({cur_per.get('days')} days)
Previous period: {prev_per.get('from_date')} to {prev_per.get('to_date')} ({prev_per.get('days')} days)

## Aggregate Totals
Current consumption : {curr_kwh:,.1f} kWh
Previous consumption: {prev_kwh:,.1f} kWh
Overall change      : {var_str}
Current billed      : ₦{curr_amt:,.0f}
Previous billed     : ₦{prev_amt:,.0f}
Billing variance    : ₦{bv:+,.0f}
Customers returned  : {comparison.get('customers_returned', 0)}
Bypass count        : {comparison.get('bypass_count', 0)}

## Trend Distribution
{trend_summary or "No trend data available"}

## Top 5 Consumption Growers
{growers_text}

## Top 5 Consumption Decliners
{decliners_text}

---

Return ONLY valid JSON in this exact shape — no markdown, no explanation outside the JSON:

{{
  "headline": "<one punchy sentence summarising the overall direction>",
  "summary": "<2-3 sentences of overall narrative — what the numbers mean in plain language>",
  "notable_trends": [
    "<observation 1 — specific, grounded in the numbers>",
    "<observation 2>",
    "<observation 3>"
  ],
  "watch_list": [
    {{
      "customer_name": "<name>",
      "account_no": "<account_no>",
      "reason": "<1-2 sentences on why this customer needs attention>"
    }}
  ],
  "recommendations": [
    "<actionable recommendation 1>",
    "<actionable recommendation 2>",
    "<actionable recommendation 3>"
  ]
}}

Rules:
- Be specific — reference actual figures from the data above.
- watch_list should include 2-4 customers: mix of biggest decliners AND biggest growers if noteworthy.
- recommendations should be practical for a Nigerian DISCO field/commercial team.
- notable_trends: exactly 3 observations.
- No generic filler — every sentence should add value.
"""
    return prompt


# ─── Anthropic call ────────────────────────────────────────────────────────────

def _call_claude(prompt: str) -> dict:
    import anthropic

    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not configured.")

    client  = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1024,
        messages=[{'role': 'user', 'content': prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if Claude wraps with them
    if raw.startswith('```'):
        lines = raw.splitlines()
        raw = '\n'.join(
            line for line in lines
            if not line.strip().startswith('```')
        )

    return json.loads(raw)


# ─── Public API ────────────────────────────────────────────────────────────────

def get_comparison_insights(
    comparison: dict,
    customer_type: str,
    current_from: date,
    current_to: date,
    previous_from: date,
    previous_to: date,
    scope_type: str | None = None,
    scope_id=None,
    positive_threshold: float = 10.0,
    declined_threshold: float = -30.0,
) -> dict:
    """
    Return AI insights for a customer comparison, served from cache when possible.

    On error (API unavailable, JSON parse failure, etc.) returns an error key
    so the main response can surface it gracefully rather than failing entirely.
    """
    from commercial.models import CommercialComparisonCache

    cache_key = _build_cache_key(
        customer_type, current_from, current_to,
        previous_from, previous_to,
        scope_type, scope_id,
        positive_threshold, declined_threshold,
    )

    now = timezone.now()

    # ── Cache hit ──────────────────────────────────────────────────────────────
    try:
        cached = CommercialComparisonCache.objects.get(
            comparison_hash=cache_key, expires_at__gt=now,
        )
        logger.info("AI insights cache hit: %s", cache_key[:12])
        return {**cached.insights, 'cached': True}
    except CommercialComparisonCache.DoesNotExist:
        pass

    # ── Call Claude ────────────────────────────────────────────────────────────
    try:
        prompt   = _build_prompt(comparison)
        insights = _call_claude(prompt)
    except Exception as exc:
        logger.exception("Claude API call failed: %s", exc)
        return {'error': f"AI insights unavailable: {exc}", 'cached': False}

    # ── Store in cache (24-hour TTL) ───────────────────────────────────────────
    from datetime import timedelta
    expires = now + timedelta(hours=24)

    try:
        CommercialComparisonCache.objects.update_or_create(
            comparison_hash=cache_key,
            defaults={'insights': insights, 'expires_at': expires},
        )
    except Exception as exc:
        logger.warning("Failed to write AI insights cache: %s", exc)

    return {**insights, 'cached': False}
