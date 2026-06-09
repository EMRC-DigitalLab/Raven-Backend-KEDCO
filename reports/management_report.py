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
    "theme": { "primary_color": "#002050", ... },            // optional
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
@page { size: A4 portrait; margin: 0; }
@page :first { margin: 0; }

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    background: #ffffff;
    color: #002050;
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
    border-left: 4px solid #002050;
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
    border-left: 3px solid #002050;
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
    background: #002050;
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
    background: #002050;
    color: #ffffff;
}
.cover-accent { width: 10px; background: rgba(255,255,255,0.2); flex-shrink: 0; }
.cover-body {
    flex: 1;
    padding: 48px 55px;
    display: flex;
    flex-direction: column;
}
.cover-eyebrow {
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 3px;
    opacity: 0.5; margin-bottom: 60px;
}
.cover-main-title {
    font-size: 52px; font-weight: 800;
    line-height: 1.05; text-transform: uppercase;
    letter-spacing: -0.5px; margin: 0 0 8px 0;
}
.cover-main-title-accent { opacity: 0.7; }
.cover-rule {
    width: 60px; height: 4px;
    background: rgba(255,255,255,0.4);
    border-radius: 3px; margin: 22px 0;
}
.cover-subtitle { font-size: 13px; font-weight: 400; opacity: 0.6; line-height: 1.6; }
.cover-footer-strip {
    margin-top: auto;
    padding-top: 18px;
    border-top: 1px solid rgba(255,255,255,0.15);
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.cover-footer-strip img { max-height: 40px; width: auto; }
.cover-period { font-size: 11px; font-weight: 600; opacity: 0.55; letter-spacing: 1px; }
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

    return f"""
You are preparing a formal management report for {company_name} covering {period_label}.

Below is the performance data. Write a management narrative and return a single JSON object
with the exact structure specified. Keep all text concise, professional and grounded in
the actual numbers. Use specific figures from the data wherever possible.

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
    {{"issue":"<name>","implication":"<what this means operationally>","response":"<required management action>"}}
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

    primary = context.get('primary_color', '#002050')
    accent  = context.get('accent_color',  'rgba(0,32,80,0.2)')
    primary_light = context.get('primary_light', 'rgba(0,32,80,0.05)')

    return f"""
    <div class="page">
        <div class="page-content">
            {header_html}
            {content}
        </div>
        <div class="page-footer">
            <span class="footer-label">Prepared for management review</span>
            <span class="footer-page">{page_number}</span>
        </div>
    </div>"""


# =============================================================================
# SECTION RENDERERS
# =============================================================================

def render_mgmt_cover(context: dict) -> str:
    """Render the management report cover page."""
    company  = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    title    = context.get('report_title',  'Management Report')
    date     = context.get('report_date',   '')
    subtitle = context.get('report_subtitle', '')
    footer_logo = context.get('footer_logo_url', '')

    words = title.split()
    title_main   = ' '.join(words[:-1]) if len(words) > 1 else title
    title_accent = words[-1] if len(words) > 1 else ''

    subtitle_html = (
        f'<div class="cover-subtitle">{subtitle}</div>' if subtitle
        else f'<div class="cover-subtitle">{company}</div>'
    )

    return f"""
    <div class="cover-page">
        <div class="cover-accent"></div>
        <div class="cover-body">
            <div class="cover-eyebrow">{company} &nbsp;&bull;&nbsp; Management Report</div>

            <div style="flex:1;">
                <div style="font-size:10px;font-weight:700;text-transform:uppercase;
                            letter-spacing:2.5px;opacity:0.5;margin-bottom:18px;">
                    Performance Monitoring Tool
                </div>
                <h1 class="cover-main-title">
                    {title_main}<br/>
                    <span class="cover-main-title-accent">{title_accent}</span>
                </h1>
                <div class="cover-rule"></div>
                {subtitle_html}
            </div>

            <div class="cover-footer-strip">
                <img src="{footer_logo}" alt="Powered by EMRC" />
                <span class="cover-period">{date}</span>
            </div>
        </div>
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


def render_mgmt_reliability_review(narrative: dict, data: dict,
                                    context: dict, page_number: int):
    """Returns (html, pages_used)."""
    interr_data  = data.get("interruption_breakdown", [])
    intro        = narrative.get("reliability_intro", "")
    implications = narrative.get("interruption_implications", [])

    # ── Page 1: Interruption breakdown table ──
    rows_html = ""
    for item in (interr_data or []):
        rows_html += f"""
        <tr>
            <td><strong>{item.get('type', '—')}</strong></td>
            <td style="text-align:right">{item.get('count', 0):,}</td>
            <td style="text-align:right">{item.get('total_hours', 0)} hrs</td>
            <td style="text-align:right">{item.get('avg_duration', 0)} hrs</td>
            <td style="line-height:1.4;">{item.get('management_note', '')}</td>
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


def render_mgmt_back_page(context: dict) -> str:
    company = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    date    = context.get('report_date', '')
    footer_logo = context.get('footer_logo_url', '')

    return f"""
    <div class="cover-page" style="justify-content:center;align-items:center;text-align:center;">
        <div class="cover-accent"></div>
        <div class="cover-body" style="justify-content:center;align-items:center;">
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

        # Theme
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

        # Previous-period comparison data (if available via filters)
        previous = {}
        try:
            prev_filters = dict(ds.filters)
            compare_cfg  = {'comparison_type': 'previous_period'}
            period_data  = ds.get_period_comparison_data(compare_cfg, user=None)
            if period_data and isinstance(period_data, dict):
                prev_m = period_data.get('previous_period', {}).get('metrics', {})
                previous = prev_m
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
