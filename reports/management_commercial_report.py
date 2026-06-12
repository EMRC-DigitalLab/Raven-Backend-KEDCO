"""
reports/management_commercial_report.py

Commercial Management Report Generator.

Produces a portrait PDF focused on MDI + MDNI revenue performance,
billing coverage, customer-level movements, and ARIA-generated
commercial narrative — all in the management-report style.

Entry point
───────────
POST /api/reports/generate/management/commercial/

Body:
{
    "report_title":  "May 2026 Commercial Management Report",
    "company_name":  "KANO ELECTRICITY DISTRIBUTION COMPANY",
    "theme":         { "primary_color": "#002050", ... },
    "include_ai":    true,
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
# AI SYSTEM PROMPT — commercial narrative
# =============================================================================

_COMMERCIAL_SYSTEM_PROMPT = (
    "You are a senior commercial consultant preparing formal management reports "
    "for KEDCO (Kano Electricity Distribution Company), a Nigerian electricity "
    "distribution company. You write clear, professional commercial commentary "
    "grounded in the actual billing and revenue data. "
    "Every response must be valid JSON only — no markdown fences, no prose outside "
    "the JSON object. Do not use em dashes (—) anywhere; use commas, colons, or "
    "full stops instead. Do not include trailing commas in JSON arrays or objects."
)

_COMMERCIAL_USER_PROMPT_TEMPLATE = """
You are writing the ARIA commercial management report for {company} covering {period}.
Write like a senior commercial director presenting to the board. Every narrative field
must be substantial — 2 to 4 full sentences minimum. Be specific about numbers.
Return ONLY a valid JSON object with this exact structure (no trailing commas):

{
  "headline": "<one bold sentence capturing the single most important commercial finding>",

  "executive_summary": {
    "paragraph_1": "<Overall revenue performance: what changed, by how much, and what it means>",
    "paragraph_2": "<Coverage and revenue at risk: how many customers were unread, what revenue is exposed, urgency>",
    "paragraph_3": "<MDI vs MDNI balance: which segment drives revenue, which needs attention>",
    "management_priority": "<The one thing management must act on this cycle>"
  },

  "revenue_narrative": "<2-3 sentences interpreting total revenue, trend direction, and what is driving it>",
  "coverage_narrative": "<2-3 sentences on coverage rate — what the unread gap means in naira terms and operational risk>",
  "mdi_narrative": "<Full paragraph on MDI segment: revenue contribution, top accounts, trend, any concerns>",
  "mdni_narrative": "<Full paragraph on MDNI segment: revenue contribution, trend, any concerns or opportunities>",
  "district_narrative": "<2-3 sentences on which districts are leading, which are lagging, and why it matters>",
  "movements_narrative": "<2-3 sentences explaining the biggest revenue gainers and decliners and what management should note>",
  "customer_narrative": "<2-3 sentences on the top customer concentration risk — what share of revenue comes from top N accounts>",

  "priority_issues": [
    {"issue": "<commercial issue name>", "evidence": "<specific data point — naira amount, %, customer count>", "risk": "<management risk if unaddressed>", "response": "<required management action>"},
    {"issue": "<commercial issue name>", "evidence": "<specific data point>", "risk": "<management risk>", "response": "<required action>"},
    {"issue": "<commercial issue name>", "evidence": "<specific data point>", "risk": "<management risk>", "response": "<required action>"},
    {"issue": "<commercial issue name>", "evidence": "<specific data point>", "risk": "<management risk>", "response": "<required action>"},
    {"issue": "<commercial issue name>", "evidence": "<specific data point>", "risk": "<management risk>", "response": "<required action>"}
  ],
  "action_plan": [
    {"area": "<action area>", "action": "<specific recommended commercial action>", "team": "<responsible team or role>", "timeline": "<e.g. Before next report / 2 weeks / Monthly>", "output": "<expected deliverable>"},
    {"area": "<action area>", "action": "<specific action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"},
    {"area": "<action area>", "action": "<specific action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"},
    {"area": "<action area>", "action": "<specific action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"},
    {"area": "<action area>", "action": "<specific action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"},
    {"area": "<action area>", "action": "<specific action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"},
    {"area": "<action area>", "action": "<specific action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"},
    {"area": "<action area>", "action": "<specific action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"}
  ]
}

DATA:

PERIOD: {period}

REVENUE:
  Total Billed Revenue (current): {curr_revenue}
  Total Billed Revenue (previous): {prev_revenue}
  Change: {revenue_change}
  MDI Revenue: {mdi_revenue}
  MDNI Revenue: {mdni_revenue}
  Total kWh Billed: {total_kwh}
  ARPU: {arpu}
  ATC Loss: {atc_loss}

COVERAGE:
  Total Customers: {total_customers}
  Customers Billed: {customers_read}
  Coverage Rate: {coverage_rate}
  Unread Customers: {customers_unread}
  Revenue at Risk: {revenue_at_risk}

MDI:
  Count: {mdi_count}
  Revenue: {mdi_revenue}
  Coverage: {mdi_coverage}
  Top 5: {top_mdi}

MDNI:
  Count: {mdni_count}
  Revenue: {mdni_revenue}
  Coverage: {mdni_coverage}
  Top 5: {top_mdni}

TOP DISTRICTS:
{district_table}

MOVEMENTS:
  Gainers: {gainers}
  Decliners: {decliners}
"""


# =============================================================================
# HELPERS
# =============================================================================

def _fmt_naira(value: float) -> str:
    if value >= 1_000_000_000:
        return f"₦{value/1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"₦{value/1_000_000:.1f}M"
    if value >= 1_000:
        return f"₦{value/1_000:.1f}K"
    return f"₦{value:,.0f}"


def _fmt_kwh(value: float) -> str:
    if value >= 1_000_000:
        return f"{value/1_000_000:.2f}M kWh"
    if value >= 1_000:
        return f"{value/1_000:.1f}K kWh"
    return f"{value:,.0f} kWh"


def _pct_change(current: float, previous: float) -> str:
    if not previous:
        return "N/A"
    pct = (current - previous) / abs(previous) * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _movement_html(current, previous) -> str:
    try:
        c, p = float(current), float(previous)
        if p == 0:
            return '<span class="kpi-movement-neu">—</span>'
        pct = ((c - p) / abs(p)) * 100
        sign = "+" if pct >= 0 else ""
        cls  = "kpi-movement-pos" if pct >= 0 else "kpi-movement-neg"
        return f'<span class="{cls}">{sign}{pct:.1f}%</span>'
    except (TypeError, ValueError):
        return '<span class="kpi-movement-neu">—</span>'


def _trend_badge(trend: str) -> str:
    colors = {
        'Major Positive Trend': ('#e8f5e9', '#2e7d32'),
        'Positive Trend':       ('#f1f8e9', '#558b2f'),
        'Moderated Trend':      ('#fff8e1', '#f57f17'),
        'Declined Trend':       ('#fff3e0', '#e65100'),
        'Major Declined Trend': ('#fde8e8', '#c62828'),
        'No Previous Data':     ('#f0f0f0', '#666'),
        'No Current Data':      ('#f0f0f0', '#666'),
    }
    bg, fg = colors.get(trend, ('#f0f0f0', '#666'))
    return (f'<span style="background:{bg};color:{fg};font-size:8px;font-weight:700;'
            f'padding:2px 7px;border-radius:4px;white-space:nowrap;">{trend}</span>')


def _page_header(context: dict) -> str:
    return f"""
    <div class="page-header">
        <span class="header-company">{context.get('company_name', '')} &nbsp;|&nbsp; Commercial Management Report</span>
        <span class="header-subtitle">{context.get('report_date', '')}</span>
    </div>"""


def _page_footer(_context: dict, page_num: int) -> str:
    return f"""
    <div class="page-footer">
        <span class="footer-label">Written by ARIA &nbsp;·&nbsp; Automated Raven Intelligence Assistance</span>
        <span class="footer-page">{page_num}</span>
    </div>"""


def _aria_badge() -> str:
    return """
    <div style="display:inline-flex;align-items:center;gap:8px;
                background:#002050;color:#fff;border-radius:8px;
                padding:5px 12px;margin-bottom:12px;">
        <span style="font-size:12px;font-weight:800;letter-spacing:1px;">ARIA</span>
        <span style="font-size:8.5px;font-weight:400;opacity:0.75;letter-spacing:0.5px;">
            Automated Raven Intelligence Assistance
        </span>
    </div>"""


def _mgmt_page(content: str, context: dict, page_number: int) -> str:
    """Standard portrait page shell — header, content, footer."""
    return f"""
    <div class="page">
        <div class="page-content">
            <div class="page-header">
                <span class="header-company">{context.get('company_name', '')}</span>
                <span class="header-subtitle">Commercial Management Report &nbsp;|&nbsp; {context.get('report_date', '')}</span>
            </div>
            {content}
        </div>
        <div class="page-footer">
            <span class="footer-label">Written by ARIA &nbsp;·&nbsp; Automated Raven Intelligence Assistance</span>
            <span class="footer-page">{page_number}</span>
        </div>
    </div>"""


# =============================================================================
# AI NARRATIVE
# =============================================================================

def _generate_commercial_narrative(data: dict, period_label: str, company: str) -> dict:
    """Call Claude API to generate commercial management narrative."""
    import re
    import anthropic
    from django.conf import settings

    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        logger.error("ANTHROPIC_API_KEY is not configured — commercial narrative unavailable.")
        return _fallback_narrative()

    try:
        client = anthropic.Anthropic(api_key=api_key)

        overview    = data.get('overview', {})
        prev_ov     = data.get('prev_overview', {})
        districts   = data.get('districts', [])[:6]
        comparison  = data.get('comparison', {})
        customers   = comparison.get('customers', [])

        mdi_custs  = [c for c in customers if c.get('customer_type') == 'MDI']
        mdni_custs = [c for c in customers if c.get('customer_type') == 'MDNI']

        top_mdi  = [f"{c['customer_name']} ({_fmt_naira(c.get('current_billed_amount', 0))})"
                    for c in sorted(mdi_custs,  key=lambda x: -(x.get('current_billed_amount') or 0))[:5]]
        top_mdni = [f"{c['customer_name']} ({_fmt_naira(c.get('current_billed_amount', 0))})"
                    for c in sorted(mdni_custs, key=lambda x: -(x.get('current_billed_amount') or 0))[:5]]

        gainers   = sorted([c for c in customers if (c.get('variance_pct') or 0) > 0],
                           key=lambda x: -(x.get('variance_pct') or 0))[:5]
        decliners = sorted([c for c in customers if (c.get('variance_pct') or 0) < 0],
                           key=lambda x:  (x.get('variance_pct') or 0))[:5]

        district_table = "\n".join(
            f"  {d.get('district','')}: {_fmt_kwh(d.get('total_billed_kwh', 0))} / {_fmt_naira(d.get('total_billed_amount', 0))}"
            for d in districts
        ) or "  No district data"

        curr_rev = overview.get('total_billed_amount', 0)
        prev_rev = prev_ov.get('total_billed_amount', 0)

        mdi_cov_n  = len([c for c in mdi_custs  if (c.get('current_billed_amount') or 0) > 0])
        mdni_cov_n = len([c for c in mdni_custs if (c.get('current_billed_amount') or 0) > 0])
        mdi_total  = overview.get('mdi_count',  1) or 1
        mdni_total = overview.get('mdni_count', 1) or 1

        # Build prompt using manual substitution to avoid .format() crashing on
        # any { or } characters that may appear in district/customer names.
        replacements = {
            '{company}':          company,
            '{period}':           period_label,
            '{curr_revenue}':     _fmt_naira(curr_rev),
            '{prev_revenue}':     _fmt_naira(prev_rev) if prev_rev else "N/A",
            '{revenue_change}':   _pct_change(curr_rev, prev_rev) if prev_rev else "N/A",
            '{mdi_revenue}':      _fmt_naira(overview.get('mdi_revenue', 0)),
            '{mdni_revenue}':     _fmt_naira(overview.get('mdni_revenue', 0)),
            '{total_kwh}':        _fmt_kwh(overview.get('total_billed_kwh', 0)),
            '{arpu}':             _fmt_naira(overview.get('arpu', 0)),
            '{atc_loss}':         f"{overview.get('atc_loss', 0):.1f}%" if overview.get('atc_loss') else "N/A",
            '{total_customers}':  str(overview.get('total_customers', 0)),
            '{customers_read}':   str(overview.get('customers_read', 0)),
            '{coverage_rate}':    f"{overview.get('coverage_rate', 0):.1f}%",
            '{customers_unread}': str(overview.get('customers_unread', 0)),
            '{revenue_at_risk}':  _fmt_naira(overview.get('estimated_revenue', 0)),
            '{mdi_count}':        str(overview.get('mdi_count', 0)),
            '{mdi_coverage}':     f"{mdi_cov_n}/{mdi_total} ({mdi_cov_n/mdi_total*100:.0f}%)",
            '{mdni_count}':       str(overview.get('mdni_count', 0)),
            '{mdni_coverage}':    f"{mdni_cov_n}/{mdni_total} ({mdni_cov_n/mdni_total*100:.0f}%)",
            '{top_mdi}':          "; ".join(top_mdi)  or "No MDI data",
            '{top_mdni}':         "; ".join(top_mdni) or "No MDNI data",
            '{district_table}':   district_table,
            '{gainers}':          "; ".join(f"{c['customer_name']} (+{c['variance_pct']:.1f}%)" for c in gainers)   or "None",
            '{decliners}':        "; ".join(f"{c['customer_name']} ({c['variance_pct']:.1f}%)"  for c in decliners) or "None",
        }
        prompt = _COMMERCIAL_USER_PROMPT_TEMPLATE
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, str(value))

        response = client.messages.create(
            model      = "claude-sonnet-4-6",
            max_tokens = 8000,
            system     = _COMMERCIAL_SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise

    except json.JSONDecodeError as exc:
        logger.error("Commercial AI JSON parse failed: %s", exc)
        return _fallback_narrative()
    except Exception as exc:
        logger.error("Commercial AI narrative failed: %s", exc, exc_info=True)
        return _fallback_narrative()


def _fallback_narrative() -> dict:
    msg = "ARIA narrative unavailable for this period. Key metrics are displayed in the sections below."
    return {
        "headline": "Commercial performance data compiled for the selected period.",
        "executive_summary": {
            "paragraph_1": msg,
            "paragraph_2": "",
            "paragraph_3": "",
            "management_priority": "",
        },
        "revenue_narrative":   "",
        "coverage_narrative":  "",
        "mdi_narrative":       "",
        "mdni_narrative":      "",
        "district_narrative":  "",
        "movements_narrative": "",
        "customer_narrative":  "",
        "priority_issues": [],
        "action_plan":     [],
    }


# =============================================================================
# SECTION RENDERERS
# =============================================================================

def render_commercial_cover(context: dict) -> str:
    period = context.get('period_label', '')
    title  = context.get('report_title', 'Commercial Management Report')
    words  = title.rsplit(' ', 1)
    main   = words[0] if len(words) > 1 else title
    last   = words[1] if len(words) > 1 else ''

    return f"""
    <div class="cover-page">
        <div class="cover-accent"></div>
        <div class="cover-body">
            <div class="cover-eyebrow">{context.get('company_name', 'KEDCO')}</div>
            <div style="flex:1;display:flex;flex-direction:column;justify-content:center;">
                <div style="font-size:11px;font-weight:700;text-transform:uppercase;
                            letter-spacing:3px;opacity:0.5;margin-bottom:16px;">
                    Commercial
                </div>
                <div class="cover-main-title">
                    {main}<br/>
                    <span class="cover-main-title-accent">{last}</span>
                </div>
                <div class="cover-rule"></div>
                <div class="cover-subtitle">
                    MDI &amp; MDNI Revenue Performance Review<br/>
                    Billing Coverage · Customer Analysis · ARIA Insights
                </div>
            </div>
            <div class="cover-footer-strip">
                <img src="{context.get('logo_gray_url', '')}" alt="KEDCO" />
                <span class="cover-period">{period}</span>
            </div>
        </div>
    </div>"""


def render_commercial_executive_summary(narrative: dict, data: dict, context: dict, page_num: int) -> str:
    """Full narrative executive summary page — the first thing management reads."""
    es       = narrative.get('executive_summary', {})
    headline = narrative.get('headline', '')
    p1       = es.get('paragraph_1', '')
    p2       = es.get('paragraph_2', '')
    p3       = es.get('paragraph_3', '')
    priority = es.get('management_priority', '')

    ov       = data.get('overview', {})
    prev_ov  = data.get('prev_overview', {})

    curr_rev = ov.get('total_billed_amount', 0)
    prev_rev = prev_ov.get('total_billed_amount', 0)
    mdi_rev  = ov.get('mdi_revenue', 0)
    mdni_rev = ov.get('mdni_revenue', 0)
    cov      = ov.get('coverage_rate', 0)
    at_risk  = ov.get('estimated_revenue', 0)

    kpi_strip = f"""
    <div style="display:flex;gap:0;margin-bottom:18px;border:1px solid rgba(0,32,80,0.12);
                border-radius:10px;overflow:hidden;">
        <div style="flex:1;padding:11px 14px;text-align:center;border-right:1px solid rgba(0,32,80,0.1);">
            <div style="font-size:7.5px;font-weight:700;text-transform:uppercase;opacity:0.55;margin-bottom:4px;">Total Revenue</div>
            <div style="font-size:18px;font-weight:800;color:#002050;">{_fmt_naira(curr_rev)}</div>
            <div style="font-size:8px;color:#64748b;margin-top:2px;">{_movement_html(curr_rev, prev_rev) if prev_rev else ''}</div>
        </div>
        <div style="flex:1;padding:11px 14px;text-align:center;border-right:1px solid rgba(0,32,80,0.1);">
            <div style="font-size:7.5px;font-weight:700;text-transform:uppercase;opacity:0.55;margin-bottom:4px;">MDI Revenue</div>
            <div style="font-size:18px;font-weight:800;color:#002050;">{_fmt_naira(mdi_rev)}</div>
        </div>
        <div style="flex:1;padding:11px 14px;text-align:center;border-right:1px solid rgba(0,32,80,0.1);">
            <div style="font-size:7.5px;font-weight:700;text-transform:uppercase;opacity:0.55;margin-bottom:4px;">MDNI Revenue</div>
            <div style="font-size:18px;font-weight:800;color:#002050;">{_fmt_naira(mdni_rev)}</div>
        </div>
        <div style="flex:1;padding:11px 14px;text-align:center;">
            <div style="font-size:7.5px;font-weight:700;text-transform:uppercase;opacity:0.55;margin-bottom:4px;">Coverage Rate</div>
            <div style="font-size:18px;font-weight:800;color:{'#22c55e' if cov >= 80 else '#f59e0b' if cov >= 60 else '#ef4444'};">{cov:.1f}%</div>
            <div style="font-size:8px;color:#ef4444;margin-top:2px;">₦{at_risk/1e6:.1f}M at risk</div>
        </div>
    </div>"""

    headline_html = f"""
    <div style="background:#002050;border-radius:10px;padding:14px 18px;margin-bottom:16px;">
        <div style="font-size:7.5px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;
                    color:rgba(255,255,255,0.55);margin-bottom:5px;">Key Finding</div>
        <div style="font-size:13px;font-weight:700;color:#fff;line-height:1.5;">{headline}</div>
    </div>""" if headline else ""

    paras = "".join(
        f'<p class="narrative">{p}</p>'
        for p in [p1, p2, p3] if p
    )

    priority_html = f"""
    <div class="callout">
        <div class="callout-title">Management Priority for Next Reporting Cycle</div>
        <div class="callout-text">{priority}</div>
    </div>""" if priority else ""

    content = f"""
        <h1 class="section-title">1. Executive Commercial Summary</h1>
        {_aria_badge()}
        {kpi_strip}
        {headline_html}
        {paras}
        {priority_html}"""

    return _mgmt_page(content, context, page_num)


def render_commercial_kpi_dashboard(narrative: dict, data: dict, context: dict, page_num: int) -> str:
    ov     = data.get('overview', {})
    prev_ov = data.get('prev_overview', {})

    curr_rev  = ov.get('total_billed_amount', 0)
    prev_rev  = prev_ov.get('total_billed_amount', 0)
    curr_kwh  = ov.get('total_billed_kwh', 0)
    prev_kwh  = prev_ov.get('total_billed_kwh', 0)
    mdi_rev   = ov.get('mdi_revenue', 0)
    mdni_rev  = ov.get('mdni_revenue', 0)
    prev_mdi  = prev_ov.get('mdi_revenue', 0)
    prev_mdni = prev_ov.get('mdni_revenue', 0)
    cov_rate  = ov.get('coverage_rate', 0)
    arpu      = ov.get('arpu', 0)
    at_risk   = ov.get('estimated_revenue', 0)
    atc_loss  = ov.get('atc_loss')

    def _card(label, current_str, prev_str, movement_html, highlight=False):
        bg = '#002050' if highlight else '#f8fafc'
        tc = '#fff'    if highlight else '#002050'
        bc = 'transparent' if highlight else 'rgba(0,32,80,0.08)'
        mc = 'rgba(255,255,255,0.65)' if highlight else '#64748b'
        return f"""
        <div style="background:{bg};border:1px solid {bc};border-radius:8px;
                    padding:11px 13px;display:flex;flex-direction:column;gap:4px;">
            <div style="font-size:7.5px;font-weight:700;text-transform:uppercase;
                        letter-spacing:1px;color:{tc};opacity:0.55;">{label}</div>
            <div style="font-size:16px;font-weight:800;color:{tc};line-height:1.1;">
                {current_str}
            </div>
            <div style="border-top:1px solid rgba(128,128,128,0.15);padding-top:5px;margin-top:2px;">
                <div style="font-size:8px;color:{mc};margin-bottom:1px;">Previous</div>
                <div style="font-size:11px;font-weight:700;color:{tc};opacity:0.7;">{prev_str}</div>
                <div style="font-size:10px;font-weight:700;margin-top:2px;">{movement_html}</div>
            </div>
        </div>"""

    rev_mv   = _movement_html(curr_rev,   prev_rev)
    kwh_mv   = _movement_html(curr_kwh,   prev_kwh)
    mdi_mv   = _movement_html(mdi_rev,    prev_mdi)
    mdni_mv  = _movement_html(mdni_rev,   prev_mdni)

    atc_str  = f"{atc_loss:.1f}%" if atc_loss is not None else "N/A"
    cov_color = '#22c55e' if cov_rate >= 80 else ('#f59e0b' if cov_rate >= 60 else '#ef4444')

    rev_narrative = narrative.get('revenue_narrative', '')
    cov_narrative = narrative.get('coverage_narrative', '')

    narrative_blocks = ""
    if rev_narrative or cov_narrative:
        narrative_blocks = f"""
        {_aria_badge()}
        {"<p class='narrative'>" + rev_narrative + "</p>" if rev_narrative else ""}
        {"<div class='callout'><div class='callout-title'>Coverage Risk</div><div class='callout-text'>" + cov_narrative + "</div></div>" if cov_narrative else ""}"""

    content = f"""
        <h1 class="section-title">2. Revenue KPI Dashboard</h1>

        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-bottom:8px;">
            {_card('Total Billed Revenue', _fmt_naira(curr_rev), _fmt_naira(prev_rev) if prev_rev else 'N/A', rev_mv, highlight=True)}
            {_card('MDI Revenue', _fmt_naira(mdi_rev), _fmt_naira(prev_mdi) if prev_mdi else 'N/A', mdi_mv)}
            {_card('MDNI Revenue', _fmt_naira(mdni_rev), _fmt_naira(prev_mdni) if prev_mdni else 'N/A', mdni_mv)}
            {_card('Coverage Rate', f'{cov_rate:.1f}%', f"{prev_ov.get('coverage_rate',0):.1f}%" if prev_ov else 'N/A', _movement_html(cov_rate, prev_ov.get('coverage_rate',0)))}
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-bottom:14px;">
            {_card('Total kWh Billed', _fmt_kwh(curr_kwh), _fmt_kwh(prev_kwh) if prev_kwh else 'N/A', kwh_mv)}
            {_card('ARPU', _fmt_naira(arpu), '—', '<span class="kpi-movement-neu">—</span>')}
            {_card('Revenue at Risk', _fmt_naira(at_risk), f"{ov.get('customers_unread',0)} unread", '<span class="kpi-movement-neu">—</span>')}
            {_card('ATC Loss', atc_str, '—', '<span class="kpi-movement-neu">—</span>')}
        </div>

        <div style="margin-bottom:12px;">
            <div style="font-size:9px;font-weight:700;text-transform:uppercase;
                        letter-spacing:0.8px;opacity:0.6;margin-bottom:5px;">
                Billing Coverage — {ov.get('customers_read',0):,} of {ov.get('total_customers',0):,} customers billed
            </div>
            <div style="background:#e2e8f0;border-radius:4px;height:10px;overflow:hidden;">
                <div style="background:{cov_color};height:100%;width:{min(cov_rate,100):.1f}%;border-radius:4px;"></div>
            </div>
            <div style="display:flex;justify-content:space-between;margin-top:4px;">
                <span style="font-size:8px;color:{cov_color};font-weight:700;">{cov_rate:.1f}% billed</span>
                <span style="font-size:8px;color:#ef4444;font-weight:700;">
                    {ov.get('customers_unread',0):,} unread · Revenue at Risk {_fmt_naira(at_risk)}
                </span>
            </div>
        </div>

        {narrative_blocks}"""

    return _mgmt_page(content, context, page_num)


def render_commercial_mdi_mdni_split(narrative: dict, data: dict, context: dict, page_num: int) -> str:
    ov   = data.get('overview', {})
    ctype = data.get('customer_type_summary', {})

    mdi_data  = ctype.get('mdi',  {})
    mdni_data = ctype.get('mdni', {})

    mdi_rev   = ov.get('mdi_revenue', 0)
    mdni_rev  = ov.get('mdni_revenue', 0)
    total_rev = (mdi_rev + mdni_rev) or 1

    mdi_share  = mdi_rev  / total_rev * 100
    mdni_share = mdni_rev / total_rev * 100

    mdi_count  = ov.get('mdi_count', 0)
    mdni_count = ov.get('mdni_count', 0)

    mdi_kwh  = mdi_data.get('total_billed_kwh', 0)
    mdni_kwh = mdni_data.get('total_billed_kwh', 0)

    mdi_arpu  = (mdi_rev  / mdi_count)  if mdi_count  else 0
    mdni_arpu = (mdni_rev / mdni_count) if mdni_count else 0

    mdi_note  = narrative.get('mdi_narrative', '')
    mdni_note = narrative.get('mdni_narrative', '')
    cov_note  = narrative.get('coverage_narrative', '')

    def _seg_card(label, count, kwh, revenue, arpu, share, note, color):
        return f"""
        <div style="flex:1;background:#f8fafc;border-radius:10px;padding:16px 18px;
                    border-top:4px solid {color};">
            <div style="font-size:9px;font-weight:700;text-transform:uppercase;
                        letter-spacing:1.5px;color:{color};margin-bottom:12px;">{label}</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
                <div>
                    <div style="font-size:7.5px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;margin-bottom:2px;">Customers</div>
                    <div style="font-size:20px;font-weight:800;color:#002050;">{count:,}</div>
                </div>
                <div>
                    <div style="font-size:7.5px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;margin-bottom:2px;">Revenue Share</div>
                    <div style="font-size:20px;font-weight:800;color:{color};">{share:.1f}%</div>
                </div>
                <div>
                    <div style="font-size:7.5px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;margin-bottom:2px;">Total Billed</div>
                    <div style="font-size:14px;font-weight:700;color:#002050;">{_fmt_naira(revenue)}</div>
                </div>
                <div>
                    <div style="font-size:7.5px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;margin-bottom:2px;">kWh Billed</div>
                    <div style="font-size:14px;font-weight:700;color:#002050;">{_fmt_kwh(kwh)}</div>
                </div>
                <div>
                    <div style="font-size:7.5px;color:#64748b;text-transform:uppercase;
                                letter-spacing:0.8px;margin-bottom:2px;">Avg Rev / Customer</div>
                    <div style="font-size:13px;font-weight:700;color:#002050;">{_fmt_naira(arpu)}</div>
                </div>
            </div>
            {"<div style='font-size:9.5px;color:#475569;line-height:1.55;border-top:1px solid rgba(0,32,80,0.08);padding-top:10px;'>" + note + "</div>" if note else ""}
        </div>"""

    # Revenue bar
    bar_html = f"""
    <div style="margin:16px 0 12px;">
        <div style="font-size:8px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.8px;opacity:0.6;margin-bottom:5px;">Revenue Split</div>
        <div style="display:flex;height:14px;border-radius:6px;overflow:hidden;">
            <div style="background:#002050;width:{mdi_share:.1f}%;display:flex;align-items:center;
                        justify-content:center;">
                <span style="font-size:7.5px;font-weight:700;color:#fff;">MDI {mdi_share:.1f}%</span>
            </div>
            <div style="background:#0ea5e9;flex:1;display:flex;align-items:center;
                        justify-content:center;">
                <span style="font-size:7.5px;font-weight:700;color:#fff;">MDNI {mdni_share:.1f}%</span>
            </div>
        </div>
    </div>"""

    content = f"""
        <h1 class="section-title">3. MDI vs MDNI Revenue Analysis</h1>
        {_aria_badge()}
        {bar_html}
        <div style="display:flex;gap:12px;">
            {_seg_card('MDI — Maximum Demand Industrial', mdi_count, mdi_kwh, mdi_rev, mdi_arpu, mdi_share, mdi_note, '#002050')}
            {_seg_card('MDNI — Maximum Demand Non-Industrial', mdni_count, mdni_kwh, mdni_rev, mdni_arpu, mdni_share, mdni_note, '#0ea5e9')}
        </div>
        {"<div class='callout' style='margin-top:12px;'><div class='callout-title'>Coverage Assessment</div><div class='callout-text'>" + cov_note + "</div></div>" if cov_note else ""}"""
    return _mgmt_page(content, context, page_num)


def render_commercial_revenue_by_district(narrative: dict, data: dict, context: dict, page_num: int) -> str:
    districts = data.get('districts', [])
    feeders   = data.get('feeders', [])[:12]
    ov        = data.get('overview', {})
    total_rev = ov.get('total_billed_amount', 1) or 1
    dist_narr = narrative.get('district_narrative', '')

    dist_rows = ""
    for i, d in enumerate(districts[:10], 1):
        share = d['total_billed_amount'] / total_rev * 100
        bar_w = min(share * 4, 100)
        dist_rows += f"""
        <tr>
            <td style="font-weight:700;">{i}</td>
            <td style="font-weight:600;">{d['district']}</td>
            <td style="text-align:right;">{_fmt_kwh(d['total_billed_kwh'])}</td>
            <td style="text-align:right;font-weight:700;">{_fmt_naira(d['total_billed_amount'])}</td>
            <td style="text-align:right;">{share:.1f}%</td>
            <td style="width:80px;padding:6px 9px;">
                <div style="background:#e2e8f0;border-radius:3px;height:6px;">
                    <div style="background:#002050;height:100%;width:{bar_w:.0f}%;border-radius:3px;"></div>
                </div>
            </td>
        </tr>"""

    feeder_rows = ""
    for i, f in enumerate(feeders, 1):
        fname = f.get('feeder_name') or f.get('feeder', f'Feeder {i}')
        feeder_rows += f"""
        <tr>
            <td style="font-weight:700;">{i}</td>
            <td style="font-weight:600;">{fname}</td>
            <td style="text-align:right;">{_fmt_kwh(f.get('total_billed_kwh', 0))}</td>
            <td style="text-align:right;font-weight:700;">{_fmt_naira(f.get('total_billed_amount', 0))}</td>
        </tr>"""

    content = f"""
        <h1 class="section-title">4. Revenue by District &amp; Feeder</h1>
        {_aria_badge()}
        {"<p class='narrative'>" + dist_narr + "</p>" if dist_narr else ""}

        <div class="subsection-title">Revenue by Business District</div>
        <table class="mgmt-table" style="margin-bottom:16px;">
            <thead>
                <tr>
                    <th style="width:30px;">#</th>
                    <th>District</th>
                    <th style="text-align:right;">kWh Billed</th>
                    <th style="text-align:right;">Revenue</th>
                    <th style="text-align:right;">% Share</th>
                    <th style="width:100px;">Contribution</th>
                </tr>
            </thead>
            <tbody>{dist_rows if dist_rows else "<tr><td colspan='6' style='text-align:center;color:#94a3b8;'>No district data</td></tr>"}</tbody>
        </table>

        <div class="subsection-title">Top Feeders by Revenue</div>
        <table class="mgmt-table">
            <thead>
                <tr>
                    <th style="width:30px;">#</th>
                    <th>Feeder</th>
                    <th style="text-align:right;">kWh Billed</th>
                    <th style="text-align:right;">Revenue</th>
                </tr>
            </thead>
            <tbody>{feeder_rows if feeder_rows else "<tr><td colspan='4' style='text-align:center;color:#94a3b8;'>No feeder data</td></tr>"}</tbody>
        </table>"""
    return _mgmt_page(content, context, page_num)


def render_commercial_top_customers(narrative: dict, data: dict, context: dict, page_num: int) -> tuple[str, int]:
    comparison    = data.get('comparison', {})
    all_custs     = comparison.get('customers', [])
    cust_narr     = narrative.get('customer_narrative', '')

    mdi_custs  = [c for c in all_custs if c.get('customer_type') == 'MDI']
    mdni_custs = [c for c in all_custs if c.get('customer_type') == 'MDNI']

    mdi_sorted  = sorted(mdi_custs,  key=lambda x: -(x.get('current_billed_amount') or 0))[:15]
    mdni_sorted = sorted(mdni_custs, key=lambda x: -(x.get('current_billed_amount') or 0))[:15]

    def _customer_table(custs, title, section_num, page, show_narr=''):
        def _rows():
            r = ""
            for i, c in enumerate(custs, 1):
                curr_amt = c.get('current_billed_amount', 0) or 0
                curr_kwh = c.get('current_consumption_kwh', 0) or 0
                vp       = c.get('variance_pct')
                trend    = c.get('trend', 'No Previous Data')
                vp_str   = f"{vp:+.1f}%" if vp is not None else "N/A"
                vp_color = '#22c55e' if (vp or 0) >= 0 else '#ef4444'
                r += f"""
                <tr>
                    <td style="font-weight:700;color:#002050;">{i}</td>
                    <td><strong style="font-size:9.5px;">{c.get('customer_name','—')}</strong><br/>
                        <span style="font-size:8px;color:#64748b;">{c.get('account_no','—')}</span></td>
                    <td style="font-size:8.5px;">{c.get('feeder','—')}</td>
                    <td style="text-align:right;font-size:9px;">{curr_kwh:,.1f}</td>
                    <td style="text-align:right;font-weight:700;">{_fmt_naira(curr_amt)}</td>
                    <td style="text-align:right;color:{vp_color};font-weight:700;">{vp_str}</td>
                    <td>{_trend_badge(trend)}</td>
                </tr>"""
            return r

        body = "<p style='color:#94a3b8;font-size:11px;margin-top:20px;'>No data available.</p>" if not custs else f"""
            {_aria_badge()}
            {"<p class='narrative'>" + show_narr + "</p>" if show_narr else ""}
            <table class="mgmt-table">
                <thead>
                    <tr>
                        <th style="width:30px;">#</th><th>Customer</th><th>Feeder</th>
                        <th style="text-align:right;">kWh</th><th style="text-align:right;">Revenue</th>
                        <th style="text-align:right;">vs Prev</th><th>Trend</th>
                    </tr>
                </thead>
                <tbody>{_rows()}</tbody>
            </table>"""

        return _mgmt_page(f'<h1 class="section-title">{section_num}. {title}</h1>{body}', context, page), 1

    mdi_html,  mdi_pages  = _customer_table(mdi_sorted,  "Top MDI Customers by Revenue",  5, page_num,            cust_narr)
    mdni_html, mdni_pages = _customer_table(mdni_sorted, "Top MDNI Customers by Revenue", 6, page_num + mdi_pages, cust_narr)

    return mdi_html + mdni_html, mdi_pages + mdni_pages


def render_commercial_notable_movements(narrative: dict, data: dict, context: dict, page_num: int) -> str:
    mov_narr   = narrative.get('movements_narrative', '')
    comparison = data.get('comparison', {})
    customers  = comparison.get('customers', [])

    with_variance = [c for c in customers if c.get('variance_pct') is not None]
    gainers   = sorted([c for c in with_variance if c['variance_pct'] > 0],
                       key=lambda x: -x['variance_pct'])[:8]
    decliners = sorted([c for c in with_variance if c['variance_pct'] < 0],
                       key=lambda x:  x['variance_pct'])[:8]

    def _mov_rows(custs, pos):
        if not custs:
            return "<tr><td colspan='5' style='text-align:center;color:#94a3b8;'>No data</td></tr>"
        color = '#22c55e' if pos else '#ef4444'
        rows  = ""
        for c in custs:
            curr = c.get('current_billed_amount', 0) or 0
            prev = c.get('previous_billed_amount', 0) or 0
            vp   = c.get('variance_pct', 0)
            rows += f"""
            <tr>
                <td><strong style="font-size:9px;">{c.get('customer_name','—')}</strong><br/>
                    <span style="font-size:7.5px;color:#64748b;">{c.get('account_no','—')} · {c.get('feeder','—')}</span></td>
                <td style="text-align:right;">{_fmt_naira(prev)}</td>
                <td style="text-align:right;font-weight:700;">{_fmt_naira(curr)}</td>
                <td style="text-align:right;color:{color};font-weight:800;">
                    {vp:+.1f}%
                </td>
                <td>{_trend_badge(c.get('trend',''))}</td>
            </tr>"""
        return rows

    content = f"""
        <h1 class="section-title">7. Notable Revenue Movements</h1>
        {_aria_badge()}
        {"<p class='narrative'>" + mov_narr + "</p>" if mov_narr else ""}

        <div class="two-col">
            <div class="two-col-half">
                <div class="subsection-title" style="color:#2e7d32;">Top Gainers vs Previous Period</div>
                <table class="mgmt-table">
                    <thead>
                        <tr>
                            <th>Customer</th>
                            <th style="text-align:right;">Prev</th>
                            <th style="text-align:right;">Current</th>
                            <th style="text-align:right;">Change</th>
                            <th>Trend</th>
                        </tr>
                    </thead>
                    <tbody>{_mov_rows(gainers, True)}</tbody>
                </table>
            </div>
            <div class="two-col-half">
                <div class="subsection-title" style="color:#c62828;">Top Decliners vs Previous Period</div>
                <table class="mgmt-table">
                    <thead>
                        <tr>
                            <th>Customer</th>
                            <th style="text-align:right;">Prev</th>
                            <th style="text-align:right;">Current</th>
                            <th style="text-align:right;">Change</th>
                            <th>Trend</th>
                        </tr>
                    </thead>
                    <tbody>{_mov_rows(decliners, False)}</tbody>
                </table>
            </div>
        </div>"""
    return _mgmt_page(content, context, page_num)


def render_commercial_priority_issues(narrative: dict, context: dict, page_num: int) -> tuple[str, int]:
    """Mirrors render_mgmt_priority_issues — 7 issues per page."""
    issues = narrative.get('priority_issues', [])
    chunks = [issues[i:i + 7] for i in range(0, max(len(issues), 1), 7)]
    html   = ""

    for idx, chunk in enumerate(chunks):
        pnum   = page_num + idx
        suffix = " (continued)" if idx > 0 else ""
        rows   = ""
        for iss in chunk:
            rows += f"""
            <tr>
                <td style="line-height:1.4;"><strong>{iss.get('issue', '—')}</strong></td>
                <td style="line-height:1.4;">{iss.get('evidence', '')}</td>
                <td style="line-height:1.4;">{iss.get('risk', '')}</td>
                <td style="line-height:1.4;">{iss.get('response', '')}</td>
            </tr>"""

        content = f"""
            <h1 class="section-title">8. Priority Commercial Issues{suffix}</h1>
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

        html += _mgmt_page(content, context, pnum)

    return html, len(chunks)


def render_commercial_action_plan(narrative: dict, context: dict, page_num: int) -> tuple[str, int]:
    """Mirrors render_mgmt_action_plan — 8 actions per page."""
    actions = narrative.get('action_plan', [])
    chunks  = [actions[i:i + 8] for i in range(0, max(len(actions), 1), 8)]
    html    = ""

    for idx, chunk in enumerate(chunks):
        pnum   = page_num + idx
        suffix = " (continued)" if idx > 0 else ""
        rows   = ""
        for act in chunk:
            rows += f"""
            <tr>
                <td style="line-height:1.4;"><strong>{act.get('area', '—')}</strong></td>
                <td style="line-height:1.4;">{act.get('action', '')}</td>
                <td style="line-height:1.4;">{act.get('team', '')}</td>
                <td style="text-align:center;">{act.get('timeline', '')}</td>
                <td style="line-height:1.4;">{act.get('output', '')}</td>
            </tr>"""

        content = f"""
            <h1 class="section-title">9. Recommended Commercial Action Plan{suffix}</h1>
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

        html += _mgmt_page(content, context, pnum)

    return html, len(chunks)


def render_commercial_back_page(context: dict) -> str:
    return f"""
    <div class="cover-page" style="justify-content:center;align-items:center;text-align:center;">
        <div style="padding:60px;">
            <div style="font-size:52px;font-weight:800;text-transform:uppercase;
                        letter-spacing:-1px;opacity:0.15;margin-bottom:20px;">KEDCO</div>
            <div style="font-size:13px;font-weight:600;opacity:0.55;margin-bottom:8px;">
                {context.get('company_name','KANO ELECTRICITY DISTRIBUTION COMPANY')}
            </div>
            <div style="font-size:10px;opacity:0.4;">{context.get('period_label','')}</div>
            <div style="margin-top:30px;font-size:9px;opacity:0.3;font-style:italic;">
                Generated by ARIA — Automated Raven Intelligence Assistance
            </div>
        </div>
    </div>"""


# =============================================================================
# MAIN GENERATOR CLASS
# =============================================================================

class CommercialManagementPDFGenerator:
    """
    Generates the commercial management report PDF.

    Usage:
        generator = CommercialManagementPDFGenerator(report_config, data_service, user=request.user)
        pdf_bytes = generator.generate_pdf()
    """

    def __init__(self, report_config: dict, data_service, user=None):
        from django.conf import settings
        from reports.pdf_generator import _build_theme_css, _hex_to_rgb

        self.report_config = report_config
        self.data_service  = data_service
        self.include_ai    = report_config.get('include_ai', True)
        self.user          = user

        raw_theme = report_config.get('theme') or {}
        self.theme = {
            'primary_color': raw_theme.get('primary_color') or '#002050',
            'accent_color':  raw_theme.get('accent_color')  or 'rgba(0, 32, 80, 0.2)',
            'text_color':    raw_theme.get('text_color')    or '#002050',
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

        self.context = self._build_context()

    def _build_context(self) -> dict:
        from reports.services import ReportDataService
        from_date = self.data_service.from_date
        to_date   = self.data_service.to_date

        period_label = ReportDataService._period_label(from_date, to_date)

        return {
            'company_name':    self.report_config.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY'),
            'report_title':    self.report_config.get('report_title', 'Commercial Management Report'),
            'report_subtitle': self.report_config.get('report_subtitle', ''),
            'report_date':     period_label,
            'period_label':    period_label,
            'logo_gray_url':   self._get_static_url('reports/images/kedco_gray_logo.png'),
            'footer_logo_url': self._get_static_url('reports/images/footer_logo.png'),
            **self.theme,
        }

    def _get_static_url(self, path: str) -> str:
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
        """Pull all commercial data for the report period + previous period."""
        import datetime
        ds = self.data_service

        # ── Current period ─────────────────────────────────────────────────────
        try:
            overview = ds.section_commercial_overview()
        except Exception:
            overview = {}
        try:
            districts = ds.section_revenue_by_district()
        except Exception:
            districts = []
        try:
            feeders = ds.section_revenue_by_feeder()
        except Exception:
            feeders = []
        try:
            customer_type_summary = ds.section_customer_type_summary()
        except Exception:
            customer_type_summary = {}
        try:
            coverage = ds.section_commercial_coverage()
        except Exception:
            coverage = {}

        # ── Previous period (same duration, immediately before) ────────────────
        prev_overview = {}
        try:
            from reports.commercial_service import CommercialReportDataService
            period_days  = (ds.to_date - ds.from_date).days + 1
            prev_to      = ds.from_date - datetime.timedelta(days=1)
            prev_from    = prev_to - datetime.timedelta(days=period_days - 1)
            prev_filters = dict(ds.filters)
            prev_filters['from_date'] = str(prev_from)
            prev_filters['to_date']   = str(prev_to)
            prev_ds      = CommercialReportDataService(prev_filters)
            prev_overview = prev_ds.section_commercial_overview()
        except Exception:
            pass

        # ── Per-customer comparison (current vs previous) ──────────────────────
        comparison = {}
        if self.user is not None:
            try:
                import datetime as dt
                from analytics.services.compare_service import compare_customers
                period_days  = (ds.to_date - ds.from_date).days + 1
                prev_to      = ds.from_date - dt.timedelta(days=1)
                prev_from    = prev_to - dt.timedelta(days=period_days - 1)
                comparison = compare_customers(
                    user          = self.user,
                    current_from  = ds.from_date,
                    current_to    = ds.to_date,
                    previous_from = prev_from,
                    previous_to   = prev_to,
                    customer_type = 'all',
                    select_all    = True,
                    sort_by       = 'current_consumption',
                )
            except Exception as exc:
                logger.warning("compare_customers failed in commercial report: %s", exc)

        return {
            'overview':             overview,
            'prev_overview':        prev_overview,
            'districts':            districts,
            'feeders':              feeders,
            'customer_type_summary': customer_type_summary,
            'coverage':             coverage,
            'comparison':           comparison,
        }

    def generate_html(self) -> str:
        all_data     = self._gather_data()
        period_label = self.context['period_label']
        company      = self.context['company_name']

        if self.include_ai:
            narrative = _generate_commercial_narrative(all_data, period_label, company)
        else:
            narrative = _fallback_narrative()

        from reports.management_report import MANAGEMENT_STYLES

        html_parts = [render_commercial_cover(self.context)]
        page = 2

        html_parts.append(render_commercial_executive_summary(narrative, all_data, self.context, page))
        page += 1

        html_parts.append(render_commercial_kpi_dashboard(narrative, all_data, self.context, page))
        page += 1

        html_parts.append(render_commercial_mdi_mdni_split(narrative, all_data, self.context, page))
        page += 1

        html_parts.append(render_commercial_revenue_by_district(narrative, all_data, self.context, page))
        page += 1

        top_html, top_pages = render_commercial_top_customers(narrative, all_data, self.context, page)
        html_parts.append(top_html)
        page += top_pages

        html_parts.append(render_commercial_notable_movements(narrative, all_data, self.context, page))
        page += 1

        issues_html, issues_pages = render_commercial_priority_issues(narrative, self.context, page)
        html_parts.append(issues_html)
        page += issues_pages

        action_html, action_pages = render_commercial_action_plan(narrative, self.context, page)
        html_parts.append(action_html)
        page += action_pages

        html_parts.append(render_commercial_back_page(self.context))

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
    {''.join(html_parts)}
</body>
</html>"""

    def generate_pdf(self) -> io.BytesIO:
        html = self.generate_html()

        if _PLAYWRIGHT_AVAILABLE:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page    = browser.new_page()
                page.set_content(html, wait_until='networkidle')
                pdf_bytes = page.pdf(
                    format='A4',
                    landscape=False,
                    print_background=True,
                    margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'},
                )
                browser.close()
            return io.BytesIO(pdf_bytes)

        if _WEASYPRINT_AVAILABLE:
            font_config = _FontConfig()
            buf = io.BytesIO()
            _WeasyHTML(string=html).write_pdf(buf, font_config=font_config)
            buf.seek(0)
            return buf

        raise RuntimeError(
            "No PDF engine available. "
            "Install Playwright: pip install playwright && playwright install chromium"
        )

    def generate_pdf_base64(self) -> str:
        return base64.b64encode(self.generate_pdf().read()).decode()
