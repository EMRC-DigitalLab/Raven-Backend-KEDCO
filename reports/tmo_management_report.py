"""
reports/tmo_management_report.py

TMO Dashboard Management Report Generator.

Produces a narrative, data-rich portrait PDF reproducing every chart and table
from the TMO live dashboard — with an ARIA (Claude Sonnet) commentary layer for
management-level interpretation.

Visual spec: colors and section order follow the exact TMO dashboard spec
(TMO_STATUS, TMO_BUCKETS, TMO_SEGMENT_COLORS from the frontend).

Entry point
───────────
POST /api/reports/generate/management/tmo/
POST /api/reports/generate/management/tmo/preview/   → returns HTML

Body:
{
    "report_title":  "July 2026 TMO Dashboard Report",
    "company_name":  "KANO ELECTRICITY DISTRIBUTION COMPANY",
    "theme":         {},
    "include_ai":    true,
    "return_base64": false,
    "sections": [
        "tmo_overview",
        "tmo_daily_network_energy",
        "tmo_daily_allocation",
        "tmo_feeder_compliance_table",
        "tmo_compliance_by_segment",
        "tmo_pear",
        "tmo_energy_pnl_donut",
        "tmo_energy_by_voltage",
        "tmo_incidents",
        "tmo_pnl_deficit",
        "tmo_gcr",
        "tmo_volatility"
    ],
    "filters": {
        "from_date": "2026-07-01",
        "to_date":   "2026-07-27"
    }
}

Default sections (when "sections" is omitted): all 14 dashboard sections.
"""

from __future__ import annotations

import base64
import calendar as _calendar
import io
import json
import logging
import math
from datetime import datetime as _dt

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
# SPEC COLORS  (from frontend DefaultColors.js + tmo.js)
# =============================================================================

_C = {
    'primary':   '#5D87FF',
    'secondary': '#49BEFF',
    'success':   '#13DEB9',
    'warning':   '#FFAE1F',
    'error':     '#FA896B',
    'info':      '#539BFF',
    'grey':      '#7C8FAC',
    'text':      '#2A3547',
    'divider':   '#e5eaef',
    'bg':        '#ffffff',
    # TMO_BUCKETS
    'exceeding':    '#2E7D32',
    'on_target':    '#8BC34A',
    'below_target': '#FBC02D',
    'poor':         '#EF9A9A',
    'critical':     '#E53935',
    # TMO_SEGMENT_COLORS
    'mdi':          '#5D87FF',
    'mdni':         '#FDD835',
    'regions':      '#66BB6A',
    'minigrid':     '#49BEFF',
    # P&L deficit chart
    'gap_green':    '#2E7D32',
    'target_yellow':'#FFAE1F',
}


# =============================================================================
# AI SYSTEM PROMPT
# =============================================================================

_TMO_SYSTEM_PROMPT = (
    "You are a senior technical and commercial operations director preparing formal "
    "management reports for KEDCO (Kano Electricity Distribution Company). "
    "You write clear, professional, and candid commentary grounded in the actual "
    "energy dispatch and supply compliance data provided. "
    "Every response must be valid JSON only — no markdown fences, no prose outside "
    "the JSON object. Do not use em dashes (—) anywhere; use commas, colons, or "
    "full stops instead. Do not include trailing commas in JSON arrays or objects."
)

_TMO_USER_PROMPT_TEMPLATE = """
You are writing the ARIA TMO dashboard management report for {company} covering {period}.
Write like a senior operations director presenting to the executive team. Every narrative
field must be substantive — 2 to 4 full sentences minimum. Be specific about numbers.
Return ONLY a valid JSON object with this exact structure (no trailing commas):

{{
  "headline": "<one sentence capturing the single most critical TMO finding for {period}>",

  "executive_summary": {{
    "paragraph_1": "<Energy dispatch performance: total GWh achieved vs target, achievement %, what is driving the gap or surplus>",
    "paragraph_2": "<Supply compliance: overall hours-of-supply situation, which segment is leading or lagging, key concerns>",
    "paragraph_3": "<P&L energy mix: MDI vs MDNI vs Regions energy share, PEAR ratio assessment, whether the premium-energy target is being met>",
    "management_priority": "<The one action management must take this reporting cycle>"
  }},

  "energy_narrative": "<2-3 sentences on the daily energy forecast vs actual trend — where were the biggest gaps, any improvement or deterioration pattern>",
  "allocation_narrative": "<2-3 sentences on the daily TCN allocation vs consumption — average unpicked energy, what it means operationally>",
  "compliance_narrative": "<Full paragraph on feeder supply compliance — MDI segment performance, Non-MDI Band A, Non-MDI Non-Band A, most critical feeders>",
  "pear_narrative": "<2-3 sentences on the PEAR ratio — actual MD vs NMD mix vs the target, whether premium energy is being prioritised, trend vs yesterday>",
  "pnl_narrative": "<2-3 sentences on P&L target realization — which segment has the largest deficit, total energy gap, financial exposure>",
  "incidents_narrative": "<2-3 sentences on incidents: total logged, financial impact, rectification rate, lingering faults that need immediate attention>",
  "volatility_narrative": "<2-3 sentences on P&L mix volatility — any segment with a swing >1%, what it signals about dispatch stability>",

  "priority_issues": [
    {{"issue": "<TMO issue>", "evidence": "<specific data point — GWh, %, segment>", "risk": "<management risk>", "response": "<required action>"}},
    {{"issue": "<TMO issue>", "evidence": "<specific data point>", "risk": "<risk>", "response": "<action>"}},
    {{"issue": "<TMO issue>", "evidence": "<specific data point>", "risk": "<risk>", "response": "<action>"}},
    {{"issue": "<TMO issue>", "evidence": "<specific data point>", "risk": "<risk>", "response": "<action>"}},
    {{"issue": "<TMO issue>", "evidence": "<specific data point>", "risk": "<risk>", "response": "<action>"}}
  ],

  "action_plan": [
    {{"area": "<area>", "action": "<specific TMO action>", "team": "<responsible team>", "timeline": "<e.g. 48 hrs / Weekly / Before next report>", "output": "<expected deliverable>"}},
    {{"area": "<area>", "action": "<action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"}},
    {{"area": "<area>", "action": "<action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"}},
    {{"area": "<area>", "action": "<action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"}},
    {{"area": "<area>", "action": "<action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"}},
    {{"area": "<area>", "action": "<action>", "team": "<team>", "timeline": "<timeline>", "output": "<output>"}}
  ]
}}

DATA:

PERIOD: {period}
COMPANY: {company}

OVERVIEW:
  Total Energy Target (GWh):   {monthly_target_gwh}
  Total Actual (GWh):          {total_actual_gwh}
  MTD Achievement:             {mtd_achievement_pct}%
  Overall Status:              {mtd_status}

SUPPLY COMPLIANCE:
{compliance_summary}

PEAR:
  Target MD%: {pear_target_md_pct}%  Target NMD%: {pear_target_nmd_pct}%
  Yesterday MD%: {pear_yesterday_md_pct}%  Yesterday NMD%: {pear_yesterday_nmd_pct}%
  MTD MD%: {pear_mtd_md_pct}%  MTD NMD%: {pear_mtd_nmd_pct}%

ENERGY BY SEGMENT:
  MDI: {mdi_gwh} GWh ({mdi_pct}%)
  MDNI: {mdni_gwh} GWh ({mdni_pct}%)
  Regions: {regions_gwh} GWh ({regions_pct}%)

P&L DEFICIT (top segments):
{pnl_deficit_table}

INCIDENTS:
  Total Logged:    {incidents_total}
  Financial Impact: {incidents_financial_impact}
  Rectified:       {incidents_rectified}
  Lingering:       {incidents_lingering}
  Rectification Rate: {incidents_rectification_rate}%

VOLATILITY (segments with swing > 1%):
{volatility_flags}
"""

ALL_SECTIONS = [
    'tmo_overview',
    'tmo_daily_network_energy',
    'tmo_daily_energy_consumed',
    'tmo_daily_allocation',
    'tmo_feeder_compliance_table',
    'tmo_compliance_by_segment',
    'tmo_minigrids_daily',
    'tmo_pear',
    'tmo_energy_pnl_donut',
    'tmo_energy_by_voltage',
    'tmo_incidents',
    'tmo_pnl_deficit',
    'tmo_gcr',
    'tmo_volatility',
]


# =============================================================================
# HELPERS
# =============================================================================

def _fmt_gwh(v):
    try:
        return f"{float(v):.2f} GWh"
    except (TypeError, ValueError):
        return "—"

def _fmt_mwh(v):
    try:
        return f"{float(v):.1f} MWh"
    except (TypeError, ValueError):
        return "—"

def _fmt_mw(v):
    try:
        return f"{float(v):.1f} MW"
    except (TypeError, ValueError):
        return "—"

def _fmt_pct(v):
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "—"

def _fmt_hrs(v):
    try:
        return f"{float(v):.1f} hrs"
    except (TypeError, ValueError):
        return "—"

def _fmt_naira(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v >= 1_000_000_000:
        return f"₦{v/1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"₦{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"₦{v/1_000:.1f}K"
    return f"₦{v:,.0f}"

def _bucket_color(bucket):
    return _C.get(bucket, _C['grey'])

def _bucket_text_color(bucket):
    dark = {'exceeding', 'on_target', 'below_target', 'critical'}
    return '#ffffff' if bucket in dark else _C['text']

def _status_color(status):
    map_ = {
        'on_target':    _C['success'],
        'below_target': _C['info'],
        'poor':         _C['warning'],
        'critical':     _C['error'],
    }
    return map_.get(status, _C['grey'])

def _seg_color(seg):
    s = str(seg).upper()
    if 'MDI' in s and 'MDNI' not in s: return _C['mdi']
    if 'MDNI' in s or 'NMD' in s:     return _C['mdni']
    if 'REGION' in s:                  return _C['regions']
    if 'MINI' in s:                    return _C['minigrid']
    return _C['grey']

def _pct_bar(value, max_val=100, color='#5D87FF', height=8):
    try:
        w = min(100, max(0, float(value) / float(max_val) * 100))
    except (TypeError, ZeroDivisionError):
        w = 0
    return (
        f'<div style="background:{_C["divider"]};border-radius:4px;height:{height}px;width:100%;overflow:hidden;">'
        f'<div style="width:{w:.1f}%;height:100%;background:{color};border-radius:4px;"></div>'
        f'</div>'
    )

def _stacked_pct_bar(segments, height=12):
    """segments = list of (label, pct, color)"""
    parts = ''.join(
        f'<div title="{label}: {pct:.1f}%" style="width:{pct:.1f}%;height:{height}px;background:{color};"></div>'
        for label, pct, color in segments if pct > 0
    )
    return (
        f'<div style="display:flex;border-radius:4px;overflow:hidden;height:{height}px;">'
        f'{parts}</div>'
    )


# =============================================================================
# AI NARRATIVE
# =============================================================================

def _call_aria(prompt_text: str) -> dict:
    try:
        from django.conf import settings
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=4096,
            messages=[
                {'role': 'user', 'content': prompt_text},
            ],
            system=_TMO_SYSTEM_PROMPT,
        )
        raw = msg.content[0].text.strip()
        raw = raw.lstrip('```json').lstrip('```').rstrip('```').strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning('ARIA call failed in TMO report: %s', exc)
        return {}


def _build_aria_prompt(company, period, section_data):
    ov   = section_data.get('tmo_daily_network_energy') or {}
    comp = section_data.get('tmo_compliance_by_segment') or {}
    pear = section_data.get('tmo_pear') or {}
    eseg = section_data.get('tmo_energy_pnl_donut') or {}
    pnl  = section_data.get('tmo_pnl_deficit') or {}
    inc  = section_data.get('tmo_incidents') or {}
    vol  = section_data.get('tmo_volatility') or {}

    # Compliance summary text
    comp_lines = []
    for seg in (comp.get('segments') or []):
        seg_name = seg.get('segment', '')
        total    = seg.get('total', 0)
        on_t     = seg.get('on_target', {}).get('count', 0)
        crit     = seg.get('critical', {}).get('count', 0)
        comp_lines.append(f"  {seg_name}: {total} feeders, {on_t} on-target, {crit} critical")
    compliance_summary = '\n'.join(comp_lines) or '  No compliance data'

    # PEAR
    mtd   = pear.get('mtd')   or {}
    yest  = pear.get('yesterday') or {}
    tgt   = pear.get('target')    or {}

    # Energy by segment (MTD)
    mtd_seg = eseg.get('mtd') or {}
    segs = {s.get('segment', ''): s for s in (mtd_seg.get('segments') or [])}
    mdi_entry   = segs.get('MDI', {})
    mdni_entry  = segs.get('MDNI', {})
    reg_entry   = segs.get('Regions', {})

    # P&L deficit top segments
    pnl_rows = (pnl.get('segments') or [])[:4]
    pnl_lines = [
        f"  {r.get('segment','')}: consumed={_fmt_gwh(r.get('consumed_gwh',0))}, "
        f"target={_fmt_gwh(r.get('target_gwh',0))}, "
        f"gap={_fmt_gwh(r.get('gap_gwh',0))} ({_fmt_pct(r.get('achievement_pct',0))} achieved)"
        for r in pnl_rows
    ]

    # Incidents
    inc_sum = inc.get('summary') or {}

    # Volatility flags (only segments with |diff| > 1%)
    vol_rows = [
        r for r in (vol.get('feeders') or vol.get('segments') or [])
        if abs(float(r.get('difference', 0) or 0)) > 1.0
    ]
    vol_lines = [
        f"  {r.get('segment','')}: yesterday={_fmt_pct(r.get('yesterday_share',0))}, "
        f"MTD={_fmt_pct(r.get('mtd_share',0))}, diff={_fmt_pct(r.get('difference',0))}"
        for r in vol_rows
    ] or ['  No segments with >1% swing']

    return _TMO_USER_PROMPT_TEMPLATE.format(
        company=company,
        period=period,
        monthly_target_gwh=_fmt_gwh(ov.get('monthly_target_gwh', 0)),
        total_actual_gwh=_fmt_gwh(ov.get('total_actual_gwh', 0)),
        mtd_achievement_pct=_fmt_pct(ov.get('mtd_achievement_pct', 0)),
        mtd_status=(ov.get('mtd_status') or 'N/A').replace('_', ' ').title(),
        compliance_summary=compliance_summary,
        pear_target_md_pct=_fmt_pct((tgt.get('md_pct') or 60)),
        pear_target_nmd_pct=_fmt_pct((tgt.get('nmd_pct') or 40)),
        pear_yesterday_md_pct=_fmt_pct((yest.get('md_pct') or 0)),
        pear_yesterday_nmd_pct=_fmt_pct((yest.get('nmd_pct') or 0)),
        pear_mtd_md_pct=_fmt_pct((mtd.get('md_pct') or 0)),
        pear_mtd_nmd_pct=_fmt_pct((mtd.get('nmd_pct') or 0)),
        mdi_gwh=_fmt_gwh(mdi_entry.get('energy_gwh', mdi_entry.get('total_mwh', 0))),
        mdi_pct=_fmt_pct(mdi_entry.get('pct', 0)),
        mdni_gwh=_fmt_gwh(mdni_entry.get('energy_gwh', mdni_entry.get('total_mwh', 0))),
        mdni_pct=_fmt_pct(mdni_entry.get('pct', 0)),
        regions_gwh=_fmt_gwh(reg_entry.get('energy_gwh', reg_entry.get('total_mwh', 0))),
        regions_pct=_fmt_pct(reg_entry.get('pct', 0)),
        pnl_deficit_table='\n'.join(pnl_lines) or '  No P&L data',
        incidents_total=inc_sum.get('total', 0),
        incidents_financial_impact=_fmt_naira(inc_sum.get('total_loss', 0)),
        incidents_rectified=inc_sum.get('rectified', 0),
        incidents_lingering=inc_sum.get('lingering', 0),
        incidents_rectification_rate=_fmt_pct(inc_sum.get('rectification_rate_pct', 0)),
        volatility_flags='\n'.join(vol_lines),
    )


# =============================================================================
# CSS
# =============================================================================

TMO_REPORT_STYLES = f"""
@page {{ size: A4 portrait; margin: 0; }}
@page :first {{ margin: 0; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: -apple-system, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    background: #ffffff;
    color: {_C['text']};
    font-size: 10.5px;
    line-height: 1.5;
}}

/* ── Page shell ─────────────────────────────────────── */
.page {{
    width: 210mm;
    min-height: 297mm;
    padding: 0;
    page-break-after: always;
    position: relative;
    overflow: hidden;
}}
.page:last-child {{ page-break-after: auto; }}

/* ── Cover / back page (matches management_report.py style) ─── */
.cover-page {{
    padding: 0;
    display: flex;
    flex-direction: row;
    min-height: 297mm;
    page-break-after: always;
    background: #002050;
    color: #ffffff;
}}
.cover-accent {{ width: 10px; background: rgba(255,255,255,0.2); flex-shrink: 0; }}
.cover-body {{
    flex: 1;
    padding: 48px 55px;
    display: flex;
    flex-direction: column;
}}
.cover-eyebrow {{
    font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 3px;
    opacity: 0.5; margin-bottom: 60px;
}}
.cover-main-title {{
    font-size: 52px; font-weight: 800;
    line-height: 1.05; text-transform: uppercase;
    letter-spacing: -0.5px; margin: 0 0 8px 0;
}}
.cover-main-title-accent {{ opacity: 0.7; }}
.cover-rule {{
    width: 60px; height: 4px;
    background: rgba(255,255,255,0.4);
    border-radius: 3px; margin: 22px 0;
}}
.cover-subtitle {{ font-size: 13px; font-weight: 400; opacity: 0.6; line-height: 1.6; }}
.cover-footer-strip {{
    margin-top: auto;
    padding-top: 18px;
    border-top: 1px solid rgba(255,255,255,0.15);
    display: flex;
    justify-content: space-between;
    align-items: center;
}}
.cover-period {{ font-size: 11px; font-weight: 600; opacity: 0.55; letter-spacing: 1px; }}

/* ── Section page ───────────────────────────────────── */
.section-page {{
    padding: 24px 28px 24px 28px;
}}
.page-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 2px solid {_C['primary']};
    padding-bottom: 8px;
    margin-bottom: 18px;
}}
.page-header-title {{
    font-size: 13px;
    font-weight: 700;
    color: {_C['primary']};
    letter-spacing: 0.01em;
}}
.page-header-period {{
    font-size: 9px;
    color: {_C['grey']};
    background: #ECF2FF;
    border-radius: 10px;
    padding: 3px 10px;
}}

/* ── KPI tiles ──────────────────────────────────────── */
.kpi-row {{
    display: flex;
    gap: 10px;
    margin-bottom: 16px;
    flex-wrap: wrap;
}}
.kpi-tile {{
    flex: 1;
    min-width: 80px;
    background: #f8f9fa;
    border-radius: 8px;
    padding: 12px 14px;
    border-left: 3px solid {_C['primary']};
}}
.kpi-tile.success {{ border-left-color: {_C['success']}; }}
.kpi-tile.warning {{ border-left-color: {_C['warning']}; }}
.kpi-tile.error   {{ border-left-color: {_C['error']};   }}
.kpi-tile.info    {{ border-left-color: {_C['info']};    }}
.kpi-label {{
    font-size: 8.5px;
    color: {_C['grey']};
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 4px;
}}
.kpi-value {{
    font-size: 18px;
    font-weight: 700;
    color: {_C['text']};
    line-height: 1;
}}
.kpi-sub {{
    font-size: 9px;
    color: {_C['grey']};
    margin-top: 3px;
}}

/* ── AI narrative blocks ────────────────────────────── */
.ai-block {{
    background: #f0f4ff;
    border-left: 3px solid {_C['primary']};
    border-radius: 0 6px 6px 0;
    padding: 10px 14px;
    margin-bottom: 14px;
    font-size: 9.5px;
    line-height: 1.6;
    color: {_C['text']};
}}
.ai-headline {{
    background: linear-gradient(135deg, {_C['primary']} 0%, #4570EA 100%);
    color: #fff;
    border-radius: 8px;
    padding: 12px 18px;
    margin-bottom: 18px;
    font-size: 11px;
    font-weight: 600;
    line-height: 1.5;
}}

/* ── Tables ─────────────────────────────────────────── */
.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 8.5px;
    margin-bottom: 14px;
}}
.data-table th {{
    background: {_C['primary']};
    color: #fff;
    font-weight: 600;
    padding: 6px 8px;
    text-align: left;
    font-size: 8px;
    letter-spacing: 0.03em;
    text-transform: uppercase;
}}
.data-table td {{
    padding: 5px 8px;
    border-bottom: 1px solid {_C['divider']};
    vertical-align: middle;
}}
.data-table tr:nth-child(even) td {{
    background: #f8f9fa;
}}
.data-table tr:last-child td {{
    border-bottom: none;
}}

/* ── Status chips ───────────────────────────────────── */
.chip {{
    display: inline-block;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 8px;
    font-weight: 600;
}}

/* ── Chart bars ─────────────────────────────────────── */
.chart-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
}}
.chart-label {{
    width: 110px;
    font-size: 8.5px;
    color: {_C['text']};
    flex-shrink: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.chart-bar-track {{
    flex: 1;
    background: {_C['divider']};
    border-radius: 4px;
    height: 10px;
    overflow: hidden;
    position: relative;
}}
.chart-bar-fill {{
    height: 100%;
    border-radius: 4px;
}}
.chart-value {{
    width: 55px;
    font-size: 8.5px;
    color: {_C['grey']};
    text-align: right;
    flex-shrink: 0;
}}

/* ── Stacked 100% bar chart ─────────────────────────── */
.stacked-bar {{
    display: flex;
    height: 18px;
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 5px;
}}
.stacked-segment {{
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 7.5px;
    font-weight: 600;
    color: #fff;
    overflow: hidden;
    white-space: nowrap;
}}

/* ── Donut substitute ───────────────────────────────── */
.donut-row {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
}}
.donut-card {{
    flex: 1;
    min-width: 120px;
    background: #f8f9fa;
    border-radius: 8px;
    padding: 12px;
}}
.donut-title {{
    font-size: 9px;
    font-weight: 700;
    color: {_C['text']};
    margin-bottom: 10px;
}}
.donut-legend-row {{
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
    font-size: 8.5px;
}}
.donut-dot {{
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
}}

/* ── Priority issues table ──────────────────────────── */
.issue-table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 14px;
    font-size: 8.5px;
}}
.issue-table th {{
    background: {_C['error']};
    color: #fff;
    padding: 5px 8px;
    text-align: left;
    font-size: 8px;
    letter-spacing: 0.03em;
    text-transform: uppercase;
}}
.issue-table td {{
    padding: 6px 8px;
    border-bottom: 1px solid {_C['divider']};
    vertical-align: top;
    line-height: 1.5;
}}
.issue-table tr:nth-child(even) td {{
    background: #fff5f5;
}}

/* ── Action plan ────────────────────────────────────── */
.action-table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 14px;
    font-size: 8.5px;
}}
.action-table th {{
    background: {_C['success']};
    color: #fff;
    padding: 5px 8px;
    text-align: left;
    font-size: 8px;
    letter-spacing: 0.03em;
    text-transform: uppercase;
}}
.action-table td {{
    padding: 6px 8px;
    border-bottom: 1px solid {_C['divider']};
    vertical-align: top;
    line-height: 1.5;
}}
.action-table tr:nth-child(even) td {{
    background: #f0fdf4;
}}

/* ── Section divider ────────────────────────────────── */
.section-divider {{
    height: 1px;
    background: {_C['divider']};
    margin: 14px 0;
}}
.section-heading {{
    font-size: 10px;
    font-weight: 700;
    color: {_C['primary']};
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 10px;
    padding-bottom: 4px;
    border-bottom: 1px solid {_C['divider']};
}}
.sub-heading {{
    font-size: 9px;
    font-weight: 600;
    color: {_C['grey']};
    margin-bottom: 6px;
}}

/* ── Footer ─────────────────────────────────────────── */
.page-footer {{
    position: absolute;
    bottom: 14px;
    left: 28px;
    right: 28px;
    display: flex;
    justify-content: space-between;
    font-size: 7.5px;
    color: {_C['grey']};
    border-top: 1px solid {_C['divider']};
    padding-top: 6px;
}}

/* ── Two-column layout ──────────────────────────────── */
.two-col {{
    display: flex;
    gap: 16px;
    margin-bottom: 14px;
}}
.two-col > div {{
    flex: 1;
    min-width: 0;
}}
"""


# =============================================================================
# SVG CHART HELPERS
# =============================================================================

_SVG_W  = 680   # chart canvas width  (fits A4 portrait content area)
_SVG_H  = 170   # chart canvas height
_ML     = 42    # left margin (Y-axis)
_MR     = 10    # right margin
_MT     = 14    # top margin
_MB     = 28    # bottom margin (X-axis labels)
_CW     = _SVG_W - _ML - _MR   # plot width
_CH     = _SVG_H - _MT - _MB   # plot height


def _y_scale(values, allow_negative=False):
    """Return (y_max, y_min, scale_fn) where scale_fn(v) → pixel y from top."""
    pos_vals = [v for v in values if v is not None and v > 0]
    neg_vals = [v for v in values if v is not None and v < 0]
    y_max = max(pos_vals) * 1.15 if pos_vals else 1.0
    y_min = min(neg_vals) * 1.15 if (neg_vals and allow_negative) else 0.0
    span  = y_max - y_min if (y_max - y_min) > 0 else 1.0

    def _fn(v):
        return _MT + (y_max - float(v)) / span * _CH

    return y_max, y_min, _fn


def _zero_y(y_max, y_min):
    span = y_max - y_min if (y_max - y_min) > 0 else 1.0
    return _MT + y_max / span * _CH


def _x_positions(n):
    """Return list of (bar_x, bar_w) for n bars across the plot width."""
    if n == 0:
        return []
    bar_w = max(4, min(24, int(_CW / n) - 2))
    gap   = max(1, (_CW - n * bar_w) // max(n - 1, 1))
    positions = []
    x = _ML
    for _ in range(n):
        positions.append((x, bar_w))
        x += bar_w + gap
    return positions


def _svg_open(h=None):
    h = h or _SVG_H
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_SVG_W}" height="{h}" '
        f'style="display:block;max-width:100%;font-family:sans-serif;">'
    )


def _svg_axes(y_max, y_min, n_ticks=4, y_title='GWh', zero_line=False):
    span = y_max - y_min if (y_max - y_min) > 0 else 1.0
    lines = f'<line x1="{_ML}" y1="{_MT}" x2="{_ML}" y2="{_MT+_CH}" stroke="#e5eaef" stroke-width="1"/>'
    for i in range(n_ticks + 1):
        v     = y_min + (y_max - y_min) * i / n_ticks
        py    = _MT + _CH - (v - y_min) / span * _CH
        label = f'{v:.1f}'
        lines += (
            f'<line x1="{_ML-4}" y1="{py:.1f}" x2="{_SVG_W-_MR}" y2="{py:.1f}" '
            f'stroke="#e5eaef" stroke-width="0.5"/>'
            f'<text x="{_ML-6}" y="{py+3:.1f}" text-anchor="end" '
            f'font-size="7" fill="#7C8FAC">{label}</text>'
        )
    if zero_line and y_min < 0 < y_max:
        zy = _MT + _CH - (0 - y_min) / span * _CH
        lines += (
            f'<line x1="{_ML}" y1="{zy:.1f}" x2="{_SVG_W-_MR}" y2="{zy:.1f}" '
            f'stroke="#2A3547" stroke-width="1" stroke-dasharray="3,2"/>'
        )
    # Y-axis title
    lines += (
        f'<text x="10" y="{_MT + _CH//2}" text-anchor="middle" '
        f'font-size="7" fill="#7C8FAC" '
        f'transform="rotate(-90,10,{_MT + _CH//2})">{y_title}</text>'
    )
    return lines


def _svg_x_labels(positions, labels):
    out = ''
    step = max(1, len(labels) // 15)   # show at most ~15 labels so they don't overlap
    for i, ((x, w), lbl) in enumerate(zip(positions, labels)):
        if i % step == 0:
            cx = x + w / 2
            out += (
                f'<text x="{cx:.1f}" y="{_SVG_H-6}" text-anchor="middle" '
                f'font-size="7" fill="#7C8FAC">{lbl}</text>'
            )
    return out


# ── Stacked bar chart (bottom→top) ───────────────────────────────────────────

def _svg_stacked_bar(days, series, y_title='GWh', label_fmt='{:.2f}', show_labels=True):
    """
    days   : list of dicts; each dict has a 'label' key and one key per series
    series : list of (key, color)  — bottom series first
    Returns SVG string.
    """
    if not days:
        return ''

    # compute max stacked total for y-scale
    totals = [sum(float(d.get(k, 0) or 0) for k, _ in series) for d in days]
    y_max  = max(totals) * 1.15 if totals else 1.0
    y_min  = 0.0
    span   = y_max if y_max > 0 else 1.0

    positions = _x_positions(len(days))
    svg  = _svg_open()
    svg += _svg_axes(y_max, y_min, y_title=y_title)

    for i, (d, (x, w)) in enumerate(zip(days, positions)):
        stack_bottom = _MT + _CH  # starts at x-axis
        for key, color in series:
            val = float(d.get(key, 0) or 0)
            if val <= 0:
                continue
            bar_h = val / span * _CH
            y     = stack_bottom - bar_h
            svg  += f'<rect x="{x}" y="{y:.1f}" width="{w}" height="{bar_h:.1f}" fill="{color}" rx="1"/>'
            svg  += f'<line x1="{x}" y1="{y:.1f}" x2="{x+w}" y2="{y:.1f}" stroke="#fff" stroke-width="1"/>'
            if show_labels and bar_h > 9 and w >= 12:
                lbl = label_fmt.format(val)
                svg += (
                    f'<text x="{x + w/2:.1f}" y="{y + bar_h/2 + 2.5:.1f}" '
                    f'text-anchor="middle" font-size="5" fill="#fff" font-weight="600">'
                    f'{lbl}</text>'
                )
            stack_bottom = y

    svg += _svg_x_labels(positions, [str(d.get('label', d.get('day', ''))) for d in days])
    svg += '</svg>'
    return svg


# ── Single-series bar chart ───────────────────────────────────────────────────

def _svg_single_bar(days, key, color, y_title='GWh', show_labels=True):
    if not days:
        return ''
    vals  = [float(d.get(key, 0) or 0) for d in days]
    y_max = max(vals) * 1.15 if vals else 1.0
    span  = y_max if y_max > 0 else 1.0

    positions = _x_positions(len(days))
    svg  = _svg_open()
    svg += _svg_axes(y_max, 0, y_title=y_title)

    for d, (x, w), v in zip(days, positions, vals):
        bar_h = v / span * _CH
        y     = _MT + _CH - bar_h
        svg  += f'<rect x="{x}" y="{y:.1f}" width="{w}" height="{bar_h:.1f}" fill="{color}" rx="1"/>'
        if show_labels and bar_h > 9 and w >= 12:
            svg += (
                f'<text x="{x + w/2:.1f}" y="{y + bar_h/2 + 2.5:.1f}" '
                f'text-anchor="middle" font-size="5" fill="#fff" font-weight="600">'
                f'{v:.2f}</text>'
            )

    svg += _svg_x_labels(positions, [str(d.get('label', d.get('day', ''))) for d in days])
    svg += '</svg>'
    return svg


# ── Diverging bar chart (positive above zero, negative below) ─────────────────

def _svg_diverging_bar(days, pos_series, neg_key, neg_color_fn, y_title='MW'):
    """
    pos_series : list of (key, color) — stacked above zero line
    neg_key    : single key whose magnitude goes below the zero line
    neg_color_fn : fn(day_dict) → color string (conditional red/green)
    """
    if not days:
        return ''

    pos_totals = [sum(float(d.get(k, 0) or 0) for k, _ in pos_series) for d in days]
    neg_vals   = [abs(float(d.get(neg_key, 0) or 0)) for d in days]
    y_max      = max(pos_totals) * 1.15 if pos_totals else 1.0
    y_min      = -(max(neg_vals) * 1.15) if neg_vals else 0.0
    span       = y_max - y_min if (y_max - y_min) > 0 else 1.0

    zy        = _zero_y(y_max, y_min)  # pixel y of the zero line
    positions = _x_positions(len(days))

    svg  = _svg_open()
    svg += _svg_axes(y_max, y_min, y_title=y_title, zero_line=True)

    lbl_fs = max(5.5, min(7.0, w * 0.55))  # font size scales with bar width
    cx_fn  = lambda x, w: x + w / 2       # bar centre x

    for d, (x, w) in zip(days, positions):
        cx = cx_fn(x, w)

        # Positive stacked segments above zero
        stack_bottom = zy
        seg_tops = []   # (top_y, bar_h, key, val) for label placement
        for key, color in pos_series:
            val   = float(d.get(key, 0) or 0)
            if val <= 0:
                continue
            bar_h = val / span * _CH
            y     = stack_bottom - bar_h
            svg  += f'<rect x="{x}" y="{y:.1f}" width="{w}" height="{bar_h:.1f}" fill="{color}" rx="1"/>'
            svg  += f'<line x1="{x}" y1="{y:.1f}" x2="{x+w}" y2="{y:.1f}" stroke="#fff" stroke-width="1"/>'
            seg_tops.append((y, bar_h, key, val))
            stack_bottom = y

        # Labels on positive bars: value centred inside each segment if tall enough
        for seg_y, seg_h, key, val in seg_tops:
            lbl = str(round(val))
            if seg_h >= 10:
                mid_y = seg_y + seg_h / 2 + lbl_fs * 0.35
                svg += (f'<text x="{cx:.1f}" y="{mid_y:.1f}" text-anchor="middle" '
                        f'font-size="{lbl_fs:.1f}" fill="#fff" font-weight="600">{lbl}</text>')

        # Negative segment below zero — min floor 0.5 MW for visibility
        neg_raw  = float(d.get(neg_key, 0) or 0)
        neg_v    = max(abs(neg_raw), 0.5)
        neg_h    = neg_v / span * _CH
        neg_h    = max(neg_h, 2)
        svg     += f'<rect x="{x}" y="{zy:.1f}" width="{w}" height="{neg_h:.1f}" fill="{neg_color_fn(d)}" rx="1"/>'

        # Label below zero: show actual signed value (e.g. -44 or 5)
        neg_lbl = str(round(neg_raw)) if abs(neg_raw) >= 0.5 else '0'
        if neg_h >= 8:
            lbl_y = zy + neg_h / 2 + lbl_fs * 0.35
            svg += (f'<text x="{cx:.1f}" y="{lbl_y:.1f}" text-anchor="middle" '
                    f'font-size="{lbl_fs:.1f}" fill="#fff" font-weight="600">{neg_lbl}</text>')

    svg += _svg_x_labels(positions, [str(d.get('label', d.get('day', ''))) for d in days])
    svg += '</svg>'
    return svg


# ── Period-total mini-chart (side-by-side comparison columns) ────────────────

def _svg_period_total(bars, title='Period\nTotal', h=None):
    """
    bars : list of (label, value, color) — independent side-by-side bars.
    Returns a small 120×h SVG for placement beside a main chart.
    """
    h = h or _SVG_H
    if not bars:
        return ''
    W  = 120
    ML = 6
    MR = 6
    MT = 26
    MB = 20
    n  = len(bars)
    BW = max(10, (W - ML - MR - (n - 1) * 6) // max(n, 1))
    CH = h - MT - MB

    vals  = [abs(float(v)) for _, v, _ in bars]
    y_max = max(vals) * 1.2 if any(v > 0 for v in vals) else 1.0
    span  = y_max if y_max > 0 else 1.0
    base  = MT + CH

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" '
        f'style="display:block;font-family:sans-serif;flex-shrink:0;">'
    )
    for li, line in enumerate(title.split('\n')):
        svg += (
            f'<text x="{W//2}" y="{10 + li*10}" text-anchor="middle" '
            f'font-size="6" fill="#7C8FAC" font-weight="600">{line}</text>'
        )

    for i, (label, val, color) in enumerate(bars):
        v   = abs(float(val))
        bx  = ML + i * (BW + 6)
        bh  = max(v / span * CH, 2)
        by  = base - bh
        svg += f'<rect x="{bx}" y="{by:.1f}" width="{BW}" height="{bh:.1f}" fill="{color}" rx="2"/>'
        if bh > 14:
            svg += (
                f'<text x="{bx + BW/2:.1f}" y="{by + bh/2 + 3:.1f}" '
                f'text-anchor="middle" font-size="5.5" fill="#fff" font-weight="600">'
                f'{v:.2f}</text>'
            )
        svg += (
            f'<text x="{bx + BW/2:.1f}" y="{base + 12}" '
            f'text-anchor="middle" font-size="5.5" fill="#7C8FAC">{label}</text>'
        )

    svg += '</svg>'
    return svg


# ── Period comparison stacked mini-chart (prev vs current month) ──────────────

def _svg_period_stacked_total(months, series, title='Comparison\nPrev vs Current', h=None,
                              label_colors=None):
    """
    months       : list of dicts with 'label' and value keys from series.
    series       : list of (key, color) — bottom series first.
    label_colors : optional list of text colors per series (matches series order).
                   Defaults to '#fff' for every series.
    Returns a 130×h SVG.
    """
    h = h or _SVG_H
    if not months:
        return ''
    W  = 130
    ML = 6
    MR = 6
    MT = 28
    MB = 20
    n  = len(months)
    BW = max(10, (W - ML - MR - (n - 1) * 10) // max(n, 1))
    CH = h - MT - MB

    if label_colors is None:
        label_colors = ['#fff'] * len(series)

    # 20%-of-max floor so thin segments stay visible (§7.10 spec)
    totals = [sum(abs(float(m.get(k, 0) or 0)) for k, _ in series) for m in months]
    y_max  = max(totals) * 1.2 if any(t > 0 for t in totals) else 1.0
    comp_max = max(y_max, 0.001)
    floor_px_ratio = 0.20  # 20% of chart height as minimum bar segment

    span   = y_max if y_max > 0 else 1.0
    base   = MT + CH

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" '
        f'style="display:block;font-family:sans-serif;flex-shrink:0;">'
    )
    for li, line in enumerate(title.split('\n')):
        svg += (
            f'<text x="{W//2}" y="{10 + li*9}" text-anchor="middle" '
            f'font-size="5.5" fill="#7C8FAC" font-weight="600">{line}</text>'
        )

    for mi, month in enumerate(months):
        bx    = ML + mi * (BW + 10)
        stack = base
        for si, (key, color) in enumerate(series):
            true_v = abs(float(month.get(key, 0) or 0))
            if true_v <= 0:
                continue
            nat_bh = true_v / span * CH
            bh = max(nat_bh, CH * floor_px_ratio) if true_v > 0 else 0
            by = stack - bh
            lbl_color = label_colors[si] if si < len(label_colors) else '#fff'
            svg += f'<rect x="{bx}" y="{by:.1f}" width="{BW}" height="{bh:.1f}" fill="{color}" rx="1"/>'
            svg += f'<line x1="{bx}" y1="{by:.1f}" x2="{bx+BW}" y2="{by:.1f}" stroke="#fff" stroke-width="0.5"/>'
            if bh > 10:
                svg += (
                    f'<text x="{bx + BW/2:.1f}" y="{by + bh/2 + 2.5:.1f}" '
                    f'text-anchor="middle" font-size="5" fill="{lbl_color}" font-weight="600">'
                    f'{true_v:.2f}</text>'
                )
            stack = by
        svg += (
            f'<text x="{bx + BW/2:.1f}" y="{base + 12}" '
            f'text-anchor="middle" font-size="5.5" fill="#7C8FAC">'
            f'{month.get("label", "")}</text>'
        )

    svg += '</svg>'
    return svg


# ── 100% stacked vertical columns (compliance by segment) ─────────────────────

def _svg_100pct_stacked_vertical(segments, series_buckets, h=200, label_min_pct=5):
    """
    segments      : list of dicts with 'label' key, bucket display pct values,
                    and optional '{key}_true' keys for the printed label text.
    series_buckets: list of (key, color) bottom bucket first (exceeding → critical).
    Bar height uses 'key' (already floored by caller); label uses 'key_true' when
    present (shows the true unfloored percentage), else falls back to 'key'.
    Returns a full-width SVG string.
    """
    if not segments:
        return ''
    n      = len(segments)
    slot_w = _CW // max(n, 1)
    bar_w  = max(40, slot_w * 2 // 3)
    MT2    = _MT
    CH2    = h - MT2 - _MB

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_SVG_W}" height="{h}" '
        f'style="display:block;max-width:100%;font-family:sans-serif;">'
    )
    svg += f'<line x1="{_ML}" y1="{MT2}" x2="{_ML}" y2="{MT2+CH2}" stroke="#e5eaef" stroke-width="1"/>'
    for v in [0, 20, 40, 60, 80, 100]:
        py = MT2 + CH2 - (v / 100 * CH2)
        svg += (
            f'<line x1="{_ML-2}" y1="{py:.1f}" x2="{_SVG_W-_MR}" y2="{py:.1f}" '
            f'stroke="#e5eaef" stroke-width="0.5"/>'
            f'<text x="{_ML-4}" y="{py+3:.1f}" text-anchor="end" font-size="7" fill="#7C8FAC">{v}%</text>'
        )
    base_y = MT2 + CH2

    for si, seg in enumerate(segments):
        bx    = _ML + si * slot_w + (slot_w - bar_w) // 2
        stack = base_y
        for key, color in series_buckets:
            display_pct = float(seg.get(key, 0) or 0)
            if display_pct <= 0:
                continue
            true_pct = float(seg.get(f'{key}_true', display_pct) or 0)
            bh = display_pct / 100 * CH2
            by = stack - bh
            svg += f'<rect x="{bx}" y="{by:.1f}" width="{bar_w}" height="{bh:.1f}" fill="{color}" rx="1"/>'
            svg += f'<line x1="{bx}" y1="{by:.1f}" x2="{bx+bar_w}" y2="{by:.1f}" stroke="#fff" stroke-width="1"/>'
            # Label shows true value (empty string when true value is 0)
            if true_pct > 0 and bh > 10:
                svg += (
                    f'<text x="{bx + bar_w/2:.1f}" y="{by + bh/2 + 2.5:.1f}" '
                    f'text-anchor="middle" font-size="7" fill="#fff" font-weight="700">'
                    f'{true_pct:.0f}%</text>'
                )
            stack = by

        lbl = seg.get('label', seg.get('segment', ''))
        svg += (
            f'<text x="{bx + bar_w/2:.1f}" y="{base_y + 14}" '
            f'text-anchor="middle" font-size="7.5" fill="{_C["text"]}">{lbl}</text>'
        )

    svg += '</svg>'
    return svg


# ── 100%-stacked horizontal bar (for compliance by segment, PEAR) ─────────────

def _css_100pct_bar(segments_pct, height=18, label_min_pct=10):
    """
    segments_pct : list of (label, pct, color)
    Any nonzero bucket floored to label_min_pct for visibility;
    shortfall subtracted proportionally from non-floored buckets.
    True pct shown in label text regardless.
    """
    # Apply minimum-visibility floor per spec (§5.5 — 10%, §5.7 — just render)
    visible = [(lbl, max(pct, label_min_pct) if pct > 0 else 0, c, pct)
               for lbl, pct, c in segments_pct]
    total_display = sum(dp for _, dp, _, _ in visible)
    # Renormalise to 100
    if total_display > 0:
        visible = [(l, dp / total_display * 100, c, tp) for l, dp, c, tp in visible]

    parts = ''
    for lbl, dp, color, true_pct in visible:
        if dp <= 0:
            continue
        label_html = (
            f'<span style="font-size:7.5px;font-weight:600;color:#fff;">'
            f'{true_pct:.0f}%</span>'
            if true_pct >= 8 else ''
        )
        parts += (
            f'<div title="{lbl}: {true_pct:.1f}%" '
            f'style="width:{dp:.2f}%;height:{height}px;background:{color};'
            f'display:inline-flex;align-items:center;justify-content:center;'
            f'overflow:hidden;white-space:nowrap;">'
            f'{label_html}</div>'
        )
    return (
        f'<div style="display:flex;width:100%;border-radius:4px;overflow:hidden;'
        f'height:{height}px;">{parts}</div>'
    )


# =============================================================================
# SECTION HTML RENDERERS
# =============================================================================

def _render_overview(data, ai, period):
    status = (data.get('mtd_status') or 'critical').lower()
    status_col = _status_color(status)
    status_label = status.replace('_', ' ').title()

    energy = data.get('energy', {}) or {}
    supply = data.get('supply', {}) or {}

    kpis = [
        ('Monthly Target', _fmt_gwh(data.get('monthly_target_gwh', 0)), 'info'),
        ('Actual (MTD)',   _fmt_gwh(data.get('total_actual_gwh', 0)), 'primary'),
        ('MTD Achievement', f"{data.get('mtd_achievement_pct', 0):.1f}%",
         'success' if status == 'on_target' else 'error'),
        ('Supply Compliance', f"{supply.get('compliance_pct', data.get('supply_compliance_pct', 0)):.1f}%", 'warning'),
    ]

    kpi_html = ''.join(
        f'<div class="kpi-tile {cls}"><div class="kpi-label">{lbl}</div>'
        f'<div class="kpi-value">{val}</div>'
        f'<div class="kpi-sub">Period: {period}</div></div>'
        for lbl, val, cls in kpis
    )

    chip = (
        f'<span class="chip" style="background:{status_col};color:#fff;">'
        f'{status_label}</span>'
    )

    ai_html = ''
    if ai.get('executive_summary'):
        es = ai['executive_summary']
        ai_html = (
            f'<div class="ai-block">'
            f'<p style="margin-bottom:6px;">{es.get("paragraph_1","")}</p>'
            f'<p style="margin-bottom:6px;">{es.get("paragraph_2","")}</p>'
            f'<p style="margin-bottom:6px;">{es.get("paragraph_3","")}</p>'
            f'<p style="margin-top:8px;font-weight:600;color:{_C["primary"]};">'
            f'Priority: {es.get("management_priority","")}</p>'
            f'</div>'
        )

    return (
        f'<div class="section-heading">TMO Overview — {period}</div>'
        f'<div class="kpi-row">{kpi_html}</div>'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
        f'<span style="font-size:9px;color:{_C["grey"]};">Overall Status:</span>{chip}'
        f'</div>'
        f'{ai_html}'
    )


def _seg_display_name(seg):
    """Normalise raw segment codes → display labels for energy P&L donut."""
    s = str(seg).upper().strip()
    if s in ('MDI', 'MD_INDUSTRIAL', 'MD INDUSTRIAL'):
        return 'MD Industrial'
    if s in ('MDNI', 'NMD', 'MD_NON_INDUSTRIAL', 'MD NON-INDUSTRIAL'):
        return 'MD Non-Industrial'
    if 'REGION' in s:
        return 'Regions'
    if 'MINI' in s:
        return 'Minigrids'
    return seg


def _render_daily_network_energy(data, ai):
    """5.1 — Stacked vertical bar per day: Forecast (#5D87FF) base → Actual (#49BEFF) on top.
    Period Total panel on right: two solid bars (Forecast, Actual)."""
    days         = data.get('days') or []
    total_target = data.get('monthly_target_gwh', 0)
    total_actual = data.get('total_actual_gwh', 0)
    ach_pct      = data.get('mtd_achievement_pct', 0)

    ai_html = ''
    if ai.get('energy_narrative'):
        ai_html = f'<div class="ai-block">{ai["energy_narrative"]}</div>'

    # get_daily_energy() returns 'target_gwh'; remap to 'forecast_gwh' for the SVG helper.
    _days = [{**d, 'forecast_gwh': d.get('target_gwh', d.get('forecast_gwh', 0))} for d in days]
    svg = _svg_stacked_bar(
        _days,
        [('forecast_gwh', _C['primary']), ('actual_gwh', _C['secondary'])],
        y_title='GWh',
    ) if _days else '<p style="font-size:9px;color:#999;">No daily energy data.</p>'

    period_total = _svg_period_total(
        [('Forecast', total_target, _C['primary']), ('Actual', total_actual, _C['secondary'])],
        title='Period\nTotal',
    )

    legend = (
        f'<div style="display:flex;gap:14px;font-size:8.5px;margin-bottom:8px;">'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["primary"]};display:inline-block;"></span>Daily Energy Forecast</span>'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["secondary"]};display:inline-block;"></span>Actual Energy Consumed</span>'
        f'</div>'
    )

    return (
        f'<div class="section-heading">Daily Energy — Forecast vs Actual</div>'
        f'{ai_html}'
        f'<div class="kpi-row">'
        f'<div class="kpi-tile info"><div class="kpi-label">Monthly Target</div>'
        f'<div class="kpi-value">{_fmt_gwh(total_target)}</div></div>'
        f'<div class="kpi-tile"><div class="kpi-label">MTD Actual</div>'
        f'<div class="kpi-value">{_fmt_gwh(total_actual)}</div></div>'
        f'<div class="kpi-tile {"success" if ach_pct >= 95 else "error"}">'
        f'<div class="kpi-label">MTD Achievement</div>'
        f'<div class="kpi-value">{_fmt_pct(ach_pct)}</div></div>'
        f'</div>'
        f'{legend}'
        f'<div style="display:flex;align-items:flex-start;gap:8px;">'
        f'<div style="flex:1;overflow-x:auto;">{svg}</div>'
        f'{period_total}'
        f'</div>'
    )


def _render_daily_energy_consumed(data, ai):
    """5.2 — Single-series vertical bar per day: Actual GWh only (#49BEFF).
    Period Total panel on right: single bar showing period sum."""
    days         = data.get('days') or []
    total_actual = data.get('total_actual_gwh', 0)
    ach_pct      = data.get('mtd_achievement_pct', 0)

    svg = _svg_single_bar(days, 'actual_gwh', _C['secondary'], y_title='GWh') \
        if days else '<p style="font-size:9px;color:#999;">No daily energy data.</p>'

    period_total = _svg_period_total(
        [('Actual', total_actual, _C['secondary'])],
        title='Period\nTotal',
    )

    return (
        f'<div class="section-heading">Daily Energy Consumed</div>'
        f'<div class="kpi-row">'
        f'<div class="kpi-tile"><div class="kpi-label">MTD Energy Consumed</div>'
        f'<div class="kpi-value">{_fmt_gwh(total_actual)}</div></div>'
        f'<div class="kpi-tile {"success" if ach_pct >= 95 else "error"}">'
        f'<div class="kpi-label">MTD Achievement</div>'
        f'<div class="kpi-value">{_fmt_pct(ach_pct)}</div></div>'
        f'</div>'
        f'<div style="display:flex;align-items:flex-start;gap:8px;">'
        f'<div style="flex:1;overflow-x:auto;">{svg}</div>'
        f'{period_total}'
        f'</div>'
    )


def _render_daily_allocation(data, ai):
    """5.3 — Diverging bar per spec §7.3.
    Red when actual > expected (overrun), green when actual ≤ expected.
    Period Total: fixed thirds (66.67% pos / 33.33% neg), always REAL_RED negative."""
    days    = data.get('days') or []
    summary = data.get('summary') or {}

    ai_html = ''
    if ai.get('allocation_narrative'):
        ai_html = f'<div class="ai-block">{ai["allocation_narrative"]}</div>'

    def _unpicked_color(d):
        # Red = consumed MORE than available (overrun); green = consumed less (§7.3)
        return _C['critical'] if float(d.get('actual_mw', 0) or 0) > float(d.get('expected_mw', 0) or 0) else _C['gap_green']

    processed_days = []
    for d in days:
        exp = float(d.get('expected_mw', 0) or 0)
        act = float(d.get('actual_mw', 0) or 0)
        processed_days.append({**d, 'excess_mw': max(0.0, act - exp)})

    svg = _svg_diverging_bar(
        processed_days,
        pos_series=[('expected_mw', _C['primary']), ('excess_mw', _C['warning'])],
        neg_key='unpicked_mw',
        neg_color_fn=_unpicked_color,
        y_title='MW',
    ) if processed_days else '<p style="font-size:9px;color:#999;">No allocation data.</p>'

    # Period Total panel — fixed thirds, not proportional (§7.3)
    tot_exp = float(summary.get('total_expected_mw', 0) or 0)
    tot_act = float(summary.get('total_actual_mw',   0) or 0)
    variance_signed   = tot_act - tot_exp
    variance_magnitude = abs(variance_signed)
    REAL_RED = '#E53935'
    period_total = (
        f'<div style="width:90px;flex-shrink:0;display:flex;flex-direction:column;'
        f'font-size:6.5px;font-weight:600;color:#fff;">'
        f'<div style="font-size:6px;color:#7C8FAC;text-align:center;margin-bottom:3px;font-weight:400;">Period Total</div>'
        f'<div style="flex:1;display:flex;flex-direction:column;">'
        # Positive portion: fixed flex:2 (66.67%) split into two equal halves
        f'<div style="flex:2;display:flex;flex-direction:column;">'
        f'<div style="flex:1;background:{_C["primary"]};display:flex;align-items:center;'
        f'justify-content:center;border-radius:3px 3px 0 0;">{round(tot_exp):,}</div>'
        f'<div style="flex:1;background:{_C["warning"]};display:flex;align-items:center;'
        f'justify-content:center;">{round(tot_act):,}</div>'
        f'</div>'
        # Zero divider
        f'<div style="height:2px;background:#2A3547;flex-shrink:0;"></div>'
        # Negative portion: fixed flex:1 (33.33%), always REAL_RED
        + (f'<div style="flex:1;background:{REAL_RED};display:flex;align-items:center;'
           f'justify-content:center;border-radius:0 0 3px 3px;">-{round(variance_magnitude):,}</div>'
           if variance_magnitude > 0 else
           f'<div style="flex:1;"></div>')
        + f'</div></div>'
    )

    # Legend: 3 swatches only — green swatch intentionally omitted (§7.3)
    legend = (
        f'<div style="display:flex;gap:14px;font-size:8.5px;margin-bottom:8px;flex-wrap:wrap;">'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["primary"]};display:inline-block;"></span>Hourly Expected allocation</span>'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["warning"]};display:inline-block;"></span>Hourly average consumption</span>'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{REAL_RED};display:inline-block;"></span>'
        f'Unpicked energy due to low energy and TCN constraints </span>'
        f'</div>'
    )

    return (
        f'<div class="section-heading">KEDCO Daily Real-Time Allocation</div>'
        f'{ai_html}'
        f'<div class="kpi-row">'
        f'<div class="kpi-tile info"><div class="kpi-label">Total Expected</div>'
        f'<div class="kpi-value">{_fmt_mw(summary.get("total_expected_mw", 0))}</div></div>'
        f'<div class="kpi-tile"><div class="kpi-label">Total Consumed</div>'
        f'<div class="kpi-value">{_fmt_mw(summary.get("total_actual_mw", 0))}</div></div>'
        f'<div class="kpi-tile error"><div class="kpi-label">Total Unpicked</div>'
        f'<div class="kpi-value">{_fmt_mw(summary.get("total_unpicked_mw", 0))}</div></div>'
        f'</div>'
        f'{legend}'
        f'<div style="display:flex;align-items:stretch;gap:8px;">'
        f'<div style="flex:1;overflow-x:auto;">{svg}</div>'
        f'{period_total}'
        f'</div>'
    )


def _render_feeder_compliance_table(data, ai):
    feeders = data.get('feeders') or []
    if not feeders:
        return '<p style="font-size:9px;color:#999;">No feeder compliance data.</p>'

    ai_html = ''
    if ai.get('compliance_narrative'):
        ai_html = f'<div class="ai-block">{ai["compliance_narrative"]}</div>'

    def _fmt_hrs(v, decimals=2):
        try:
            f = float(v)
            return f'{f:.{decimals}f}' if f != int(f) else str(int(f))
        except (TypeError, ValueError):
            return '—'

    rows_html = ''
    for fd in feeders[:40]:
        bucket  = fd.get('bucket') or fd.get('status', 'critical')
        b_col   = _bucket_color(bucket)
        b_txt   = _bucket_text_color(bucket)
        ach     = float(fd.get('compliance_pct') or fd.get('achievement_pct') or fd.get('pct_achieved', 0))
        target  = float(fd.get('band_minimum_hours') or fd.get('target_hours', 0))
        actual  = float(fd.get('avg_daily_hours') or fd.get('actual_hours', 0))
        gap     = float(fd.get('gap_hours') or fd.get('gap', 0))
        gap_col = _C['success'] if gap >= 0 else _C['error']

        # Feeder subtitle: segment · band · voltage
        seg_part     = fd.get('segment', fd.get('segment_name', ''))
        band_part    = fd.get('band', fd.get('tariff_band', ''))
        voltage_part = fd.get('voltage', fd.get('voltage_level', ''))
        subtitle_parts = [p for p in [seg_part, band_part, voltage_part] if p]
        subtitle = ' · '.join(subtitle_parts)

        name_cell = (
            f'<div style="font-weight:600;">{fd.get("feeder_name","")}</div>'
            + (f'<div style="font-size:7px;color:{_C["grey"]};margin-top:1px;">{subtitle}</div>' if subtitle else '')
        )

        rows_html += (
            f'<tr style="background:{b_col}10;">'
            f'<td>{name_cell}</td>'
            f'<td style="text-align:right;">{_fmt_hrs(target)}</td>'
            f'<td style="text-align:right;">{_fmt_hrs(actual)}</td>'
            f'<td style="text-align:right;color:{gap_col};">{_fmt_hrs(gap)}</td>'
            f'<td style="text-align:right;">'
            f'<span class="chip" style="background:{b_col};color:{b_txt};">{ach:.0f}%</span>'
            f'</td></tr>'
        )

    return (
        f'<div class="section-heading">Feeder Compliance Criticality</div>'
        f'{ai_html}'
        f'<table class="data-table">'
        f'<thead><tr><th>Feeder</th><th>Target</th><th>Actual</th><th>Gap</th><th>% Achieved</th></tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
    )


def _render_compliance_by_segment(data, ai):
    segments = data.get('segments') or []
    if not segments:
        return '<p style="font-size:9px;color:#999;">No compliance summary data.</p>'

    BUCKETS = ['exceeding', 'on_target', 'below_target', 'poor', 'critical']
    BUCKET_LABELS = {
        'exceeding':    'Exceeding(>105%)',
        'on_target':    'On Target(95%-105%)',
        'below_target': 'Below target(85%-94%)',
        'poor':         'Poor(75%-84%)',
        'critical':     'Critical(<75%)',
    }
    MIN_VISIBLE_PCT = 10.0  # §7.5: any nonzero bucket < 10% raised to 10%

    def _apply_floor(raw: dict) -> dict:
        """Floor nonzero buckets to MIN_VISIBLE_PCT; subtract proportionally from donors."""
        result = dict(raw)
        deficit = 0.0
        for bk in BUCKETS:
            v = result.get(bk, 0)
            if 0 < v < MIN_VISIBLE_PCT:
                deficit += MIN_VISIBLE_PCT - v
                result[bk] = MIN_VISIBLE_PCT
        if deficit <= 0:
            return result
        donors = {bk: result[bk] for bk in BUCKETS
                  if result.get(bk, 0) >= MIN_VISIBLE_PCT and raw.get(bk, 0) >= MIN_VISIBLE_PCT}
        donor_total = sum(donors.values())
        if donor_total > 0:
            for bk in donors:
                result[bk] -= deficit * (donors[bk] / donor_total)
        return result

    # Pre-compute true percentages then apply floor; true values kept for labels
    processed = []
    for seg in segments:
        total = max(seg.get('total_feeders', seg.get('total', 1)), 1)
        entry = {'label': seg.get('segment', ''), 'segment': seg.get('segment', '')}
        buckets_data = seg.get('buckets', {})
        true_pcts = {}
        for bk in BUCKETS:
            bucket = buckets_data.get(bk) or seg.get(bk) or {}
            if isinstance(bucket, dict):
                cnt = bucket.get('count', 0)
            else:
                cnt = float(bucket or 0)
            true_pcts[bk] = round(cnt / total * 100, 1) if total else 0.0
        display_pcts = _apply_floor(true_pcts)
        for bk in BUCKETS:
            # Store display height + true value for labels
            entry[bk]              = display_pcts[bk]   # used for bar height
            entry[f'{bk}_true']    = true_pcts[bk]       # used for label text
        processed.append(entry)

    # series_buckets: exceeding at bottom, critical at top (matches dashboard stacking)
    series_buckets = [(bk, _C[bk]) for bk in BUCKETS]
    svg = _svg_100pct_stacked_vertical(processed, series_buckets, h=220)

    legend_global = ' '.join(
        f'<span style="display:inline-flex;align-items:center;gap:4px;font-size:8px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C[bk]};display:inline-block;"></span>'
        f'{BUCKET_LABELS[bk]}</span>'
        for bk in BUCKETS
    )

    return (
        f'<div class="section-heading">Compliance by Segment</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;">{legend_global}</div>'
        f'<div style="overflow-x:auto;">{svg}</div>'
    )


_MINIGRID_CYAN = '#29B6D6'   # matches dashboard minigrid bar colour

def _render_minigrids_daily(data, ai):
    """5.6 — One card per feeder: single-series vertical bar per day, cyan bar."""
    feeders = data.get('feeders') or []
    ai_html = f'<div class="ai-block">{ai["minigrids_narrative"]}</div>' if ai.get('minigrids_narrative') else ''

    def _feeder_card(fname, ftype, days):
        total = sum(float(d.get('mwh', 0) or 0) for d in days)
        svg   = _svg_single_bar(days, 'mwh', _MINIGRID_CYAN, y_title='MWh') if days else ''
        subtitle = f'{fname} — {ftype}' if ftype else fname
        return (
            f'<div style="margin-bottom:18px;">'
            f'<div style="font-size:9.5px;font-weight:700;color:{_C["primary"]};margin-bottom:1px;">'
            f'{subtitle}</div>'
            f'<div style="font-size:8px;color:{_C["grey"]};margin-bottom:6px;">Daily energy received (MWh)</div>'
            f'<div style="overflow-x:auto;">{svg}</div>'
            f'</div>'
        )

    if not feeders:
        days  = data.get('days') or data.get('daily') or []
        fname = data.get('feeder_name') or 'Feeder'
        ftype = data.get('feeder_type') or data.get('minigrid_type') or ''
        return (
            f'<div class="section-heading">Minigrids Daily Energy</div>'
            f'{ai_html}'
            f'{_feeder_card(fname, ftype, days)}'
        )

    cards = ''.join(
        _feeder_card(
            fd.get('feeder_name') or fd.get('name') or 'Feeder',
            fd.get('feeder_type') or fd.get('minigrid_type') or '',
            fd.get('daily') or fd.get('days') or [],
        )
        for fd in feeders[:10]
    )

    return (
        f'<div class="section-heading">Minigrids Daily Energy</div>'
        f'{ai_html}'
        f'{cards}'
    )


def _render_pear(data, ai):
    mtd  = data.get('mtd')        or {}
    yest = data.get('yesterday')  or {}
    tgt  = data.get('target_mix') or {}

    tgt_md   = float(tgt.get('md_pct',  65) or 65)
    tgt_nmd  = float(tgt.get('nmd_pct', 35) or 35)
    yest_md  = float(yest.get('md_share_pct',  0) or 0)
    yest_nmd = float(yest.get('nmd_share_pct', 0) or 0)
    mtd_md   = float(mtd.get('md_share_pct',   0) or 0)
    mtd_nmd  = float(mtd.get('nmd_share_pct',  0) or 0)

    ai_html = f'<div class="ai-block">{ai["pear_narrative"]}</div>' if ai.get('pear_narrative') else ''

    BAR_H = 200   # px height for each vertical bar
    BAR_W = 80

    def _vert_bar(label, md, nmd):
        nmd_h = round(nmd / 100 * BAR_H)
        md_h  = BAR_H - nmd_h
        return (
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px;">'
            f'<div style="width:{BAR_W}px;height:{BAR_H}px;display:flex;flex-direction:column;border-radius:4px;overflow:hidden;">'
            # MD on top (blue)
            f'<div style="flex:{md:.1f};background:{_C["primary"]};display:flex;align-items:center;justify-content:center;color:#fff;font-size:9px;font-weight:700;">'
            f'{"MD " + str(round(md)) + "%" if md >= 12 else ""}</div>'
            # NMD on bottom (orange)
            f'<div style="flex:{nmd:.1f};background:{_C["warning"]};display:flex;align-items:center;justify-content:center;color:#fff;font-size:9px;font-weight:700;">'
            f'{"NMD " + str(round(nmd)) + "%" if nmd >= 12 else ""}</div>'
            f'</div>'
            f'<div style="font-size:8.5px;color:{_C["grey"]};text-align:center;">{label}</div>'
            f'</div>'
        )

    legend = (
        f'<div style="display:flex;gap:12px;font-size:8.5px;margin-bottom:10px;">'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["warning"]};display:inline-block;"></span>NMD</span>'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["primary"]};display:inline-block;"></span>MD</span>'
        f'</div>'
    )

    return (
        f'<div class="section-heading">PEAR — Premium Energy Allocation Ratio</div>'
        f'{ai_html}'
        f'{legend}'
        f'<div style="display:flex;gap:40px;align-items:flex-end;">'
        f'<div>'
        f'<div style="font-size:8.5px;font-weight:600;margin-bottom:8px;">Target Mix of Energy</div>'
        f'{_vert_bar("Target Mix", tgt_md, tgt_nmd)}'
        f'</div>'
        f'<div style="width:1px;background:#e0e0e0;align-self:stretch;"></div>'
        f'<div>'
        f'<div style="font-size:8.5px;font-weight:600;margin-bottom:8px;">Share of Energy: PEAR</div>'
        f'<div style="display:flex;gap:16px;">'
        f'{_vert_bar("Yesterday", yest_md, yest_nmd)}'
        f'{_vert_bar("MTD Average", mtd_md, mtd_nmd)}'
        f'</div>'
        f'</div>'
        f'</div>'
    )


def _svg_donut_chart(segs_in, total_gwh):
    """SVG donut chart matching dashboard: ring chart with arc labels + center text.
    segs_in = [(disp_name, pct, color, energy_gwh), ...]
    """
    CX, CY = 100, 100
    R    = 62   # ring centre radius
    SW   = 38   # stroke-width (ring thickness)
    CIRC = 2 * math.pi * R

    svg = (
        f'<svg viewBox="0 0 200 200" width="190" height="190" '
        f'style="flex-shrink:0;overflow:visible;">'
    )
    # Grey background track
    svg += (
        f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="none" '
        f'stroke="#f0f2f5" stroke-width="{SW}"/>'
    )

    # Accumulate arcs
    cum_deg = 0.0
    arc_data = []
    for disp_name, pct, color, energy in segs_in:
        pct = max(0.0, float(pct or 0))
        dash = pct / 100 * CIRC
        arc_data.append((disp_name, pct, color, energy, dash, cum_deg))
        cum_deg += pct / 100 * 360

    # Draw arcs (stroke-dasharray circles, rotated from 12 o'clock)
    for disp_name, pct, color, energy, dash, start_deg in arc_data:
        svg += (
            f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="none" stroke="{color}" '
            f'stroke-width="{SW}" '
            f'stroke-dasharray="{dash:.3f} {CIRC:.3f}" '
            f'stroke-dashoffset="{-(start_deg / 360 * CIRC):.3f}" '
            f'transform="rotate(-90 {CX} {CY})"/>'
        )

    # Arc labels (GWh + %)
    for disp_name, pct, color, energy, dash, start_deg in arc_data:
        if pct < 5:
            continue
        mid_deg = start_deg + pct / 2
        mid_rad = math.radians(mid_deg - 90)
        lx = CX + R * math.cos(mid_rad)
        ly = CY + R * math.sin(mid_rad)
        svg += (
            f'<text x="{lx:.1f}" y="{ly - 4:.1f}" text-anchor="middle" '
            f'font-size="8.5" font-weight="700" fill="#fff">{energy:.2f}</text>'
            f'<text x="{lx:.1f}" y="{ly + 7:.1f}" text-anchor="middle" '
            f'font-size="8" fill="#fff">{pct:.1f}%</text>'
        )

    # Center label: name of the largest segment + its pct
    if arc_data:
        biggest = max(arc_data, key=lambda x: x[1])
        svg += (
            f'<text x="{CX}" y="{CY - 6}" text-anchor="middle" '
            f'font-size="9.5" font-weight="600" fill="{biggest[2]}">{biggest[0]}</text>'
            f'<text x="{CX}" y="{CY + 8}" text-anchor="middle" '
            f'font-size="12" font-weight="700" fill="{_C["text"]}">{biggest[1]:.1f}</text>'
        )

    svg += '</svg>'
    return svg


def _render_energy_pnl_donut(data, ai):
    """5.9 — Donut chart per period: MDI/MDNI/Regions with arc labels, matching dashboard."""
    yesterday = data.get('yesterday') or {}
    mtd       = data.get('mtd')       or {}

    def _build_segs(d):
        segs      = d.get('segments') or []
        segs      = [s for s in segs if 'MINI' not in str(s.get('segment', '')).upper()]
        total_gwh = float(d.get('total_gwh', 0) or 0)
        out = []
        for s in segs:
            raw_name  = s.get('segment', '')
            disp_name = _seg_display_name(raw_name)
            color     = _seg_color(raw_name)
            pct       = float(s.get('pct', 0) or 0)
            energy    = float(s.get('energy_gwh', 0) or 0)
            out.append((disp_name, pct, color, energy))
        return out, total_gwh

    def _donut_card(title, d):
        segs, total_gwh = _build_segs(d)
        svg = _svg_donut_chart(segs, total_gwh)

        legend_html = ''
        for disp_name, pct, color, energy in segs:
            legend_html += (
                f'<div class="donut-legend-row">'
                f'<div class="donut-dot" style="background:{color};"></div>'
                f'<div style="flex:1;">{disp_name}</div>'
                f'<div style="font-weight:600;">{_fmt_gwh(energy)}</div>'
                f'<div style="color:{_C["grey"]};margin-left:4px;">({_fmt_pct(pct)})</div>'
                f'</div>'
            )

        return (
            f'<div class="donut-card">'
            f'<div class="donut-title">{title}</div>'
            f'<div style="display:flex;align-items:center;gap:16px;">'
            f'{svg}'
            f'<div style="min-width:130px;">{legend_html}'
            f'<div style="margin-top:8px;font-size:9px;font-weight:700;color:{_C["text"]};">'
            f'Total: {_fmt_gwh(total_gwh)} GWh</div>'
            f'</div></div>'
            f'</div>'
        )

    ai_html = f'<div class="ai-block">{ai["pnl_narrative"]}</div>' if ai.get('pnl_narrative') else ''

    return (
        f'<div class="section-heading">Energy by P&L Segment</div>'
        f'{ai_html}'
        f'<div class="donut-row">'
        f'{_donut_card("Yesterday: Daily Total Energy Consumed per P&L (GWh & %)", yesterday)}'
        f'{_donut_card("Month till Date: Total Energy Consumed per P&L (GWh & %)", mtd)}'
        f'</div>'
    )


def _render_energy_by_voltage(data, ai):
    """5.10 — Per-segment daily stacked bar (33KV base + 11KV top) + prev/curr month comparison."""
    raw_days       = data.get('days') or []
    month_comp     = data.get('month_comparison') or {}
    period         = data.get('period') or {}

    if not raw_days and not month_comp:
        return '<p style="font-size:9px;color:#999;">No voltage breakdown data.</p>'

    ai_html = f'<div class="ai-block">{ai["voltage_narrative"]}</div>' if ai.get('voltage_narrative') else ''

    # Derive month labels from period
    try:
        from_dt   = _dt.strptime(period.get('from', ''), '%Y-%m-%d')
        curr_lbl  = from_dt.strftime('%B %Y')
        prev_m    = from_dt.month - 1 or 12
        prev_y    = from_dt.year if from_dt.month > 1 else from_dt.year - 1
        prev_lbl  = _dt(prev_y, prev_m, 1).strftime('%B %Y')
    except Exception:
        curr_lbl, prev_lbl = 'This Month', 'Prev Month'

    # Display name mapping (service uses 'Regional', dashboard shows 'Regions')
    _seg_disp = {'MDI': 'MDI', 'MDNI': 'MDNI', 'Regional': 'Regions'}

    SERIES = [('gwh_33kv', _C['primary']), ('gwh_11kv', _C['warning'])]

    legend = (
        f'<div style="display:flex;gap:14px;font-size:8.5px;margin-bottom:6px;">'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:2px;background:{_C["primary"]};display:inline-block;"></span>'
        f'<strong>33KV</strong></span>'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:2px;background:{_C["warning"]};display:inline-block;"></span>'
        f'<strong>11KV</strong></span>'
        f'</div>'
    )

    cards_html = ''
    for seg_key in ('MDI', 'MDNI', 'Regional'):
        disp_name = _seg_disp.get(seg_key, seg_key)

        # Build per-day list for this segment
        normalised = []
        for d in raw_days:
            seg_data = (d.get('segments') or {}).get(seg_key) or {}
            normalised.append({
                'label':    d.get('day', ''),
                'gwh_33kv': float(seg_data.get('energy_33kv_gwh', 0) or 0),
                'gwh_11kv': float(seg_data.get('energy_11kv_gwh', 0) or 0),
            })

        if not any(d['gwh_33kv'] or d['gwh_11kv'] for d in normalised):
            continue  # skip empty segments

        main_svg = _svg_stacked_bar(
            normalised,
            SERIES,
            y_title='GWh',
        )

        # Comparison chart: previous month vs current month
        mc        = month_comp.get(seg_key) or {}
        curr_mc   = mc.get('current_month')  or {}
        prev_mc   = mc.get('previous_month') or {}
        comp_data = [
            {
                'label':    prev_lbl,
                'gwh_33kv': float(prev_mc.get('energy_33kv_gwh', 0) or 0),
                'gwh_11kv': float(prev_mc.get('energy_11kv_gwh', 0) or 0),
            },
            {
                'label':    curr_lbl,
                'gwh_33kv': float(curr_mc.get('energy_33kv_gwh', 0) or 0),
                'gwh_11kv': float(curr_mc.get('energy_11kv_gwh', 0) or 0),
            },
        ]
        comp_svg = _svg_period_stacked_total(
            comp_data,
            SERIES,
            title='Comparison Between Present\nand Previous\nMonth (Total)',
            label_colors=['#fff', _C['text']],
        )

        cards_html += (
            f'<div style="margin-bottom:20px;page-break-inside:avoid;">'
            f'<div style="font-size:9.5px;font-weight:700;color:{_C["text"]};margin-bottom:6px;">'
            f'{disp_name} 33KV &amp; 11KV Summary: Daily Energy Allocation (GWh)'
            f'<span style="margin-left:8px;padding:2px 6px;background:{_C["primary"]}22;'
            f'color:{_C["primary"]};border-radius:4px;font-size:8px;font-weight:600;">'
            f'{curr_lbl}</span>'
            f'</div>'
            f'{legend}'
            f'<div style="display:flex;align-items:flex-start;gap:8px;">'
            f'<div style="flex:1;overflow-x:auto;">{main_svg}</div>'
            f'{comp_svg}'
            f'</div></div>'
        )

    return (
        f'<div class="section-heading">Daily Energy by Segment &amp; Voltage</div>'
        f'{ai_html}'
        f'{cards_html}'
    )


def _render_incidents(data, ai):
    summary = data.get('summary') or {}
    rows    = data.get('incidents') or data.get('rows') or []
    period  = data.get('period') or {}

    ai_html = f'<div class="ai-block">{ai["incidents_narrative"]}</div>' if ai.get('incidents_narrative') else ''

    # KPI labels match dashboard exactly
    total   = summary.get('total_incidents', summary.get('total', 0))
    loss    = summary.get('total_financial_loss_ngn', summary.get('total_loss', 0))
    rect    = summary.get('rectified', 0)
    linger  = summary.get('lingering', 0)
    rate    = summary.get('rectification_rate_pct', 0)

    # Period label for subtitle
    try:
        to_lbl = _dt.strptime(period.get('to', ''), '%Y-%m-%d').strftime('%b %d, %Y')
    except Exception:
        to_lbl = ''

    tiles = [
        ('TOTAL INCIDENTS LOGGED',  str(total),           'primary'),
        ('FINANCIAL IMPACT (LOSS)', _fmt_naira(loss),      'error'),
        ('FAULT RECTIFIED',         str(rect),             'success'),
        ('LINGERING',               str(linger),           'warning'),
        ('RECTIFICATION RATE (%)',  _fmt_pct(rate),        'info'),
    ]
    tiles_html = ''.join(
        f'<div class="kpi-tile {cls}"><div class="kpi-label">{lbl}</div>'
        f'<div class="kpi-value" style="font-size:14px;">{val}</div></div>'
        for lbl, val, cls in tiles
    )

    inc_rows_html = ''
    if rows:
        for i, r in enumerate(rows[:20], 1):
            status = str(r.get('status', '')).lower()
            s_col  = _C['success'] if 'rectif' in status else _C['warning']
            loss_v = r.get('financial_loss_ngn', r.get('loss', 0))
            inc_rows_html += (
                f'<tr><td>{i}</td>'
                f'<td style="font-size:7.5px;">{r.get("coordinate", r.get("location", ""))}</td>'
                f'<td>{r.get("region", r.get("regions", ""))}</td>'
                f'<td style="font-weight:600;">{r.get("feeder_name", r.get("feeder", ""))}</td>'
                f'<td>{r.get("nature_of_fault", r.get("fault_type", ""))}</td>'
                f'<td><span class="chip" style="background:{s_col}22;color:{s_col};">'
                f'{r.get("status", "").capitalize()}</span></td>'
                f'<td style="text-align:right;color:{_C["error"]};">{_fmt_naira(loss_v)}</td>'
                f'</tr>'
            )
    else:
        inc_rows_html = (
            f'<tr><td colspan="7" style="text-align:center;color:{_C["grey"]};padding:16px;">'
            f'No incidents match these filters for this period.</td></tr>'
        )

    period_tag = (
        f'<span style="margin-left:8px;padding:2px 6px;background:{_C["primary"]}22;'
        f'color:{_C["primary"]};border-radius:4px;font-size:8px;font-weight:600;">'
        f'{to_lbl}</span>'
    ) if to_lbl else ''

    return (
        f'<div class="section-heading">'
        f'Weekly Techno-Commercial Incidence{period_tag}'
        f'</div>'
        f'<div style="font-size:8px;color:{_C["grey"]};margin-bottom:10px;">'
        f'Feeder faults, financial impact &amp; rectification status</div>'
        f'{ai_html}'
        f'<div class="kpi-row">{tiles_html}</div>'
        f'<table class="data-table" style="font-size:8px;">'
        f'<thead><tr>'
        f'<th>#</th><th>Coordinate</th><th>Regions</th><th>Feeder</th>'
        f'<th>Nature of Fault</th><th>Status</th><th>Loss</th>'
        f'</tr></thead>'
        f'<tbody>{inc_rows_html}</tbody></table>'
    )


def _render_pnl_deficit(data, ai):
    """5.12 — STACKED vertical bar per category (bottom→top): Consumed (#5D87FF) → |Gap| (#2E7D32) → Target (#FFAE1F).
    Data comes from get_gcr() which has MDI/MDNI/Regions/Total with correct GWh keys."""
    all_segs = data.get('segments') or data.get('rows') or []
    # include Total bar (4th column on dashboard)
    segments = all_segs
    if not segments:
        return '<p style="font-size:9px;color:#999;">No P&L deficit data.</p>'

    ai_html = f'<div class="ai-block">{ai["pnl_deficit_narrative"]}</div>' if ai.get('pnl_deficit_narrative') else ''

    period = data.get('period') or {}
    try:
        period_lbl = _dt.strptime(period.get('from', ''), '%Y-%m-%d').strftime('%B %Y')
    except Exception:
        period_lbl = ''
    period_tag = (
        f'<span style="margin-left:8px;padding:2px 6px;background:{_C["primary"]}22;'
        f'color:{_C["primary"]};border-radius:4px;font-size:8px;font-weight:600;">'
        f'{period_lbl}</span>'
    ) if period_lbl else ''

    cats = [_seg_display_name(s.get('segment', f'Cat {i}')) for i, s in enumerate(segments, 1)]

    def _to_gwh(s, *keys):
        for k in keys:
            v = s.get(k)
            if v is not None:
                return float(v)
        return 0.0

    consumed = []
    gaps     = []
    targets  = []
    for s in segments:
        # Support both GWh keys (from get_gcr) and MWh keys (from get_pnl_targets)
        if s.get('consumed_gwh') is not None:
            c = max(0.0, float(s['consumed_gwh']))
            t = max(0.0, float(s.get('target_gwh', 0) or 0))
            g = abs(float(s.get('gap_gwh', t - c) or 0))
        else:
            c_mwh = max(0.0, float(s.get('actual_energy_mwh', 0) or 0))
            t_mwh = max(0.0, float(s.get('target_energy_mwh', 0) or 0))
            c = round(c_mwh / 1000, 4)
            t = round(t_mwh / 1000, 4)
            g = abs(round((t_mwh - c_mwh) / 1000, 4))
        consumed.append(c)
        targets.append(t)
        gaps.append(g)

    # Total bar height = consumed + abs_gap + target (all three stacked)
    totals = [c + g + t for c, g, t in zip(consumed, gaps, targets)]
    y_max  = max(totals + [0.001]) * 1.15
    span   = y_max if y_max > 0 else 1.0

    SVG_H  = 210
    CH     = SVG_H - _MT - _MB - 10
    n      = len(cats)
    slot_w = _CW // max(n, 1)
    bar_w  = max(20, slot_w * 2 // 3)
    MIN_PX = max(2, CH * 0.18)   # 18% minimum visibility floor per spec §7.12

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_SVG_W}" height="{SVG_H}" '
        f'style="display:block;max-width:100%;font-family:sans-serif;">'
    )

    # Y-axis
    svg += f'<line x1="{_ML}" y1="{_MT}" x2="{_ML}" y2="{_MT+CH}" stroke="#e5eaef" stroke-width="1"/>'
    for tick in range(5):
        v  = y_max * tick / 4
        py = _MT + CH - (v / span * CH)
        svg += (
            f'<line x1="{_ML-2}" y1="{py:.1f}" x2="{_SVG_W-_MR}" y2="{py:.1f}" '
            f'stroke="#e5eaef" stroke-width="0.5"/>'
            f'<text x="{_ML-4}" y="{py+3:.1f}" text-anchor="end" font-size="7" fill="#7C8FAC">{v:.0f}</text>'
        )
    svg += (
        f'<text x="10" y="{_MT + CH//2}" text-anchor="middle" font-size="7" fill="#7C8FAC" '
        f'transform="rotate(-90,10,{_MT + CH//2})">GWh</text>'
    )

    SERIES = [
        (consumed, _C['primary'],        '#fff'),   # bottom — Energy Consumed
        (gaps,     _C['gap_green'],      '#fff'),   # middle — |Gap|
        (targets,  _C['target_yellow'],  '#fff'),   # top    — Energy Target
    ]

    base_y = _MT + CH
    for gi, cat in enumerate(cats):
        bx    = _ML + gi * slot_w + (slot_w - bar_w) // 2
        stack = base_y

        for vals, color, label_col in SERIES:
            v  = vals[gi]
            bh = max(v / span * CH, MIN_PX) if v > 0 else 0
            if bh <= 0:
                continue
            by  = stack - bh
            svg += f'<rect x="{bx}" y="{by:.1f}" width="{bar_w}" height="{bh:.1f}" fill="{color}" rx="1"/>'
            svg += f'<line x1="{bx}" y1="{by:.1f}" x2="{bx+bar_w}" y2="{by:.1f}" stroke="#fff" stroke-width="1"/>'
            if bh > 12 and v > 0:
                svg += (
                    f'<text x="{bx + bar_w/2:.1f}" y="{by + bh/2 + 3:.1f}" '
                    f'text-anchor="middle" font-size="7" fill="{label_col}" font-weight="600">'
                    f'{v:.1f}</text>'
                )
            stack = by

        cx = bx + bar_w // 2
        svg += (
            f'<text x="{cx:.1f}" y="{base_y + 14}" text-anchor="middle" '
            f'font-size="7.5" fill="{_C["text"]}">{cat}</text>'
        )

    svg += '</svg>'

    legend = (
        f'<div style="display:flex;gap:14px;font-size:8.5px;margin-bottom:8px;">'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["primary"]};display:inline-block;"></span>Energy Consumed</span>'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["gap_green"]};display:inline-block;"></span>Gap</span>'
        f'<span style="display:flex;align-items:center;gap:4px;">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{_C["target_yellow"]};display:inline-block;"></span>Energy target</span>'
        f'</div>'
    )

    return (
        f'<div class="section-heading">P&L Target Realization Deficit{period_tag}</div>'
        f'<div style="font-size:8px;color:{_C["grey"]};margin-bottom:10px;">'
        f'Energy consumed vs the gap to target, by segment (GWh)</div>'
        f'{ai_html}'
        f'{legend}'
        f'<div style="overflow-x:auto;">{svg}</div>'
    )


def _render_gcr(data, ai):
    """5.13 — 11-column GCR table; Gap Bill Value in error color; Total row bold + primary tint."""
    all_segs = data.get('segments') or data.get('rows') or []
    # get_gcr() includes the Total row inside segments; split it out
    segments = [s for s in all_segs if str(s.get('segment', '')).lower() != 'total']
    total_row = next((s for s in all_segs if str(s.get('segment', '')).lower() == 'total'), None)
    totals    = data.get('totals') or data.get('total') or total_row or {}
    if not segments and not totals:
        return '<p style="font-size:9px;color:#999;">No GCR data.</p>'

    ai_html = f'<div class="ai-block">{ai["gcr_narrative"]}</div>' if ai.get('gcr_narrative') else ''

    def _row(i, seg, is_total=False):
        ach      = float(seg.get('mtd_achievement_pct', seg.get('achievement_pct', 0)) or 0)
        gap_pct  = float(seg.get('gap_pct', seg.get('gap_in_pct', 0)) or 0)
        tariff   = float(seg.get('average_tariff_per_kwh', seg.get('avg_tariff', seg.get('average_tariff', 0))) or 0)
        mtd_bill = float(seg.get('mtd_bill_value', seg.get('mtd_expected_bill_value', 0)) or 0)
        ach_col  = (
            _C['success'] if ach >= 95 else
            _C['info']    if ach >= 85 else
            _C['warning'] if ach >= 75 else
            _C['error']
        )
        bg = f'background:{_C["primary"]}0f;font-weight:700;' if is_total else ''
        label = '<strong>Total</strong>' if is_total else str(i)
        return (
            f'<tr style="{bg}">'
            f'<td>{label}</td>'
            f'<td style="font-weight:{"700" if is_total else "600"};">{seg.get("segment", "")}</td>'
            f'<td style="text-align:right;">{_fmt_gwh(seg.get("target_gwh", 0))}</td>'
            f'<td style="text-align:right;">{_fmt_gwh(seg.get("consumed_gwh", 0))}</td>'
            f'<td style="text-align:right;color:{_C["error"]};">{_fmt_gwh(seg.get("gap_gwh", 0))}</td>'
            f'<td style="text-align:right;">{_fmt_naira(seg.get("expected_bill_value", 0))}</td>'
            f'<td style="text-align:right;">{_fmt_naira(mtd_bill)}</td>'
            f'<td style="text-align:right;color:{_C["error"]};">{_fmt_naira(seg.get("gap_bill_value", 0))}</td>'
            f'<td style="text-align:right;">'
            f'<span class="chip" style="background:{ach_col}22;color:{ach_col};">{_fmt_pct(ach)}</span>'
            f'</td>'
            f'<td style="text-align:right;color:{_C["error"]};">{_fmt_pct(gap_pct)}</td>'
            f'<td style="text-align:right;">₦{tariff:.2f}</td>'
            f'</tr>'
        )

    rows_html = ''.join(_row(i, seg) for i, seg in enumerate(segments, 1))
    if totals:
        rows_html += _row(None, totals, is_total=True)

    return (
        f'<div class="section-heading">GCR — P&L Target vs Billing Value</div>'
        f'{ai_html}'
        f'<div style="overflow-x:auto;">'
        f'<table class="data-table" style="font-size:7.5px;min-width:700px;">'
        f'<thead><tr>'
        f'<th>#</th><th>Segment</th>'
        f'<th style="text-align:right;">Target (GWh)</th>'
        f'<th style="text-align:right;">Consumed (GWh)</th>'
        f'<th style="text-align:right;">Gap (GWh)</th>'
        f'<th style="text-align:right;">Expected Bill Value</th>'
        f'<th style="text-align:right;">MTD Expected Bill</th>'
        f'<th style="text-align:right;color:{_C["error"]};">Gap Bill Value</th>'
        f'<th style="text-align:right;">MTD % Achieved</th>'
        f'<th style="text-align:right;">Gap %</th>'
        f'<th style="text-align:right;">Avg Tariff (₦/kWh)</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
        f'</div>'
    )


def _render_volatility(data, ai):
    rows = data.get('feeders') or data.get('segments') or []

    ai_html = ''
    if ai.get('volatility_narrative'):
        ai_html = f'<div class="ai-block">{ai["volatility_narrative"]}</div>'

    rows_html = ''
    for i, r in enumerate(rows, 1):
        diff = float(r.get('difference_pct', r.get('difference', 0)) or 0)
        if diff > 1:
            diff_col = _C['success']
            diff_str = f'+{diff:.1f}%'
        elif diff < -1:
            diff_col = _C['error']
            diff_str = f'{diff:.1f}%'
        else:
            diff_col = _C['grey']
            diff_str = f'{diff:.1f}%'

        remark  = str(r.get('remark', r.get('remarks', '')) or '').strip()
        rows_html += (
            f'<tr><td>{i}</td>'
            f'<td style="font-weight:600;">{r.get("segment","")}</td>'
            f'<td style="color:{diff_col if remark.lower() != "stable" else _C["grey"]};">{remark}</td>'
            f'<td style="text-align:right;">{_fmt_pct(r.get("yesterday_share_pct", r.get("yesterday_share", 0)))}</td>'
            f'<td style="text-align:right;">{_fmt_pct(r.get("mtd_share_pct", r.get("mtd_share", 0)))}</td>'
            f'<td style="text-align:right;font-weight:600;color:{diff_col};">{diff_str}</td>'
            f'</tr>'
        )

    return (
        f'<div class="section-heading">P&L Mix Volatility Index</div>'
        f'{ai_html}'
        f'<table class="data-table">'
        f'<thead><tr><th>#</th><th>Segment</th><th>Remarks</th>'
        f'<th>Yesterday Share</th><th>MTD Share</th><th>Difference</th></tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
    )


def _render_priority_issues(ai):
    issues = ai.get('priority_issues') or []
    if not issues:
        return ''
    rows = ''.join(
        f'<tr><td>{i.get("issue","")}</td><td>{i.get("evidence","")}</td>'
        f'<td style="color:{_C["error"]};">{i.get("risk","")}</td>'
        f'<td style="color:{_C["primary"]};">{i.get("response","")}</td></tr>'
        for i in issues
    )
    return (
        f'<div class="section-heading" style="color:{_C["error"]};">Priority Issues</div>'
        f'<table class="issue-table">'
        f'<thead><tr><th>Issue</th><th>Evidence</th><th>Risk</th><th>Response</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


def _render_action_plan(ai):
    actions = ai.get('action_plan') or []
    if not actions:
        return ''
    rows = ''.join(
        f'<tr><td style="font-weight:600;">{a.get("area","")}</td>'
        f'<td>{a.get("action","")}</td><td>{a.get("team","")}</td>'
        f'<td>{a.get("timeline","")}</td><td>{a.get("output","")}</td></tr>'
        for a in actions
    )
    return (
        f'<div class="section-heading" style="color:{_C["success"]};">Action Plan</div>'
        f'<table class="action-table">'
        f'<thead><tr><th>Area</th><th>Action</th><th>Team</th><th>Timeline</th><th>Output</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


SECTION_RENDERERS = {
    'tmo_overview':                 _render_overview,
    'tmo_daily_network_energy':     _render_daily_network_energy,
    'tmo_daily_energy_consumed':    _render_daily_energy_consumed,
    'tmo_daily_allocation':         _render_daily_allocation,
    'tmo_feeder_compliance_table':  _render_feeder_compliance_table,
    'tmo_compliance_by_segment':    _render_compliance_by_segment,
    'tmo_minigrids_daily':          _render_minigrids_daily,
    'tmo_pear':                     _render_pear,
    'tmo_energy_pnl_donut':         _render_energy_pnl_donut,
    'tmo_energy_by_voltage':        _render_energy_by_voltage,
    'tmo_incidents':                _render_incidents,
    'tmo_pnl_deficit':              _render_pnl_deficit,
    'tmo_gcr':                      _render_gcr,
    'tmo_volatility':               _render_volatility,
}


# =============================================================================
# HTML ASSEMBLER
# =============================================================================

def _build_html(report_config, section_data, ai_narrative, company, period):
    title    = report_config.get('report_title', 'TMO Dashboard Report')
    subtitle = report_config.get('report_subtitle', '')
    sections = report_config.get('sections') or ALL_SECTIONS

    import datetime as _dt
    generated_at = _dt.datetime.now().strftime('%d %B %Y, %H:%M')

    # ── Cover page — same dark-navy style as management_report.py ────────────
    words        = title.split()
    title_main   = ' '.join(words[:-1]) if len(words) > 1 else title
    title_accent = words[-1] if len(words) > 1 else ''

    headline_html = ''
    if ai_narrative.get('headline'):
        headline_html = (
            f'<div style="margin-top:28px;background:rgba(255,255,255,0.10);'
            f'border-left:3px solid rgba(255,255,255,0.4);border-radius:0 6px 6px 0;'
            f'padding:12px 16px;font-size:10.5px;line-height:1.6;opacity:0.85;">'
            f'{ai_narrative["headline"]}</div>'
        )

    subtitle_html = (
        f'<div class="cover-subtitle">{subtitle}</div>' if subtitle
        else f'<div class="cover-subtitle">{company}</div>'
    )

    cover = (
        f'<div class="cover-page">'
        f'<div class="cover-accent"></div>'
        f'<div class="cover-body">'
        f'<div class="cover-eyebrow">{company} &nbsp;&bull;&nbsp; Technical &amp; Management Operations Report</div>'
        f'<div style="flex:1;">'
        f'<div style="font-size:10px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:2.5px;opacity:0.5;margin-bottom:18px;">Performance Monitoring Tool</div>'
        f'<h1 class="cover-main-title">{title_main}<br/>'
        f'<span class="cover-main-title-accent">{title_accent}</span></h1>'
        f'<div class="cover-rule"></div>'
        f'{subtitle_html}'
        f'<div style="margin-top:12px;font-size:11px;opacity:0.5;letter-spacing:0.5px;">Period: {period}</div>'
        f'{headline_html}'
        f'</div>'
        f'<div class="cover-footer-strip">'
        f'<span style="font-size:10px;opacity:0.4;text-transform:uppercase;letter-spacing:2px;">'
        f'Powered by RAVEN &mdash; Performance Monitoring Tool</span>'
        f'<span class="cover-period">{generated_at}</span>'
        f'</div>'
        f'</div></div>'
    )

    # Content pages — batch sections into groups to avoid giant single pages
    content_pages = ''
    current_content = ''
    section_count = 0

    for sec in sections:
        if sec not in section_data:
            continue
        renderer = SECTION_RENDERERS.get(sec)
        if not renderer:
            continue

        d = section_data[sec]
        # Overview renderer needs extra args
        if sec == 'tmo_overview':
            html = renderer(d, ai_narrative, period)
        else:
            html = renderer(d, ai_narrative)

        current_content += html + '<div class="section-divider"></div>'
        section_count += 1

        # Break into new page every 3-4 sections (heuristic)
        if section_count % 3 == 0:
            content_pages += (
                f'<div class="page section-page">'
                f'<div class="page-header">'
                f'<div class="page-header-title">TMO Dashboard Report — {company}</div>'
                f'<div class="page-header-period">{period}</div>'
                f'</div>'
                f'{current_content}'
                f'<div class="page-footer">'
                f'<span>KEDCO — Confidential</span>'
                f'<span>{title}</span>'
                f'<span>{generated_at}</span>'
                f'</div>'
                f'</div>'
            )
            current_content = ''

    # Flush remaining content
    if current_content.strip():
        content_pages += (
            f'<div class="page section-page">'
            f'<div class="page-header">'
            f'<div class="page-header-title">TMO Dashboard Report — {company}</div>'
            f'<div class="page-header-period">{period}</div>'
            f'</div>'
            f'{current_content}'
            f'<div class="page-footer">'
            f'<span>KEDCO — Confidential</span>'
            f'<span>{title}</span>'
            f'<span>{generated_at}</span>'
            f'</div>'
            f'</div>'
        )

    # AI analysis pages (priority issues + action plan)
    ai_analysis = _render_priority_issues(ai_narrative) + _render_action_plan(ai_narrative)
    if ai_analysis.strip():
        content_pages += (
            f'<div class="page section-page">'
            f'<div class="page-header">'
            f'<div class="page-header-title">ARIA Analysis — {company}</div>'
            f'<div class="page-header-period">{period}</div>'
            f'</div>'
            f'{ai_analysis}'
            f'<div class="page-footer">'
            f'<span>KEDCO — Confidential</span>'
            f'<span>ARIA — AI-Generated Analysis</span>'
            f'<span>{generated_at}</span>'
            f'</div>'
            f'</div>'
        )

    # ── Back page — same dark-navy style as management_report.py ─────────────
    back_page = (
        f'<div class="cover-page" style="justify-content:center;align-items:center;text-align:center;">'
        f'<div class="cover-accent"></div>'
        f'<div class="cover-body" style="justify-content:center;align-items:center;">'
        f'<div style="margin-bottom:32px;">'
        f'<div class="cover-eyebrow" style="text-align:center;margin-bottom:24px;">End of Report</div>'
        f'<h2 style="font-size:30px;font-weight:800;color:#fff;text-transform:uppercase;'
        f'letter-spacing:-0.5px;line-height:1.1;">{company}</h2>'
        f'<div class="cover-rule" style="margin:24px auto;"></div>'
        f'<div style="font-size:12px;color:#fff;opacity:0.5;letter-spacing:1.5px;'
        f'text-transform:uppercase;font-weight:600;">{period}</div>'
        f'</div>'
        f'<div style="font-size:10px;color:#fff;opacity:0.3;text-transform:uppercase;'
        f'letter-spacing:2px;margin-top:60px;">'
        f'Powered by RAVEN &mdash; Performance Monitoring Tool'
        f'</div>'
        f'</div></div>'
    )

    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="UTF-8">'
        f'<title>{title}</title>'
        f'<style>{TMO_REPORT_STYLES}</style>'
        '</head><body>'
        f'{cover}{content_pages}{back_page}'
        '</body></html>'
    )


# =============================================================================
# MAIN GENERATOR CLASS
# =============================================================================

class TMOManagementPDFGenerator:
    """
    Fetches data from all requested TMO sections, optionally calls ARIA,
    assembles HTML, and renders to PDF.
    """

    def __init__(self, report_config: dict, tmo_service, user=None):
        self._config  = report_config
        self._svc     = tmo_service   # TMOReportService instance
        self._user    = user
        self._include_ai = bool(report_config.get('include_ai', True))
        self._sections = report_config.get('sections') or ALL_SECTIONS

    def _collect_section_data(self):
        data = {}
        for sec in self._sections:
            try:
                config = {}
                data[sec] = self._svc.get_all_section_data(sec, config)
            except Exception as exc:
                logger.warning('TMO report section %s failed: %s', sec, exc)
                data[sec] = {}
        return data

    def _period_label(self):
        try:
            from_date = self._svc.from_date
            to_date   = self._svc.to_date
            if from_date.month == to_date.month and from_date.year == to_date.year:
                return from_date.strftime('%B %Y')
            return f"{from_date.strftime('%d %b')} – {to_date.strftime('%d %b %Y')}"
        except Exception:
            return 'Current Period'

    def generate_html(self) -> str:
        company = self._config.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
        period  = self._period_label()

        section_data = self._collect_section_data()

        ai_narrative = {}
        if self._include_ai:
            try:
                prompt = _build_aria_prompt(company, period, section_data)
                ai_narrative = _call_aria(prompt)
            except Exception as exc:
                logger.warning('ARIA prompt build failed: %s', exc)

        return _build_html(self._config, section_data, ai_narrative, company, period)

    def generate_pdf(self) -> io.BytesIO:
        html = self.generate_html()
        buf  = io.BytesIO()

        if _PLAYWRIGHT_AVAILABLE:
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page    = browser.new_page()
                    page.set_content(html, wait_until='networkidle')
                    pdf_bytes = page.pdf(
                        format='A4',
                        print_background=True,
                        margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'},
                    )
                    browser.close()
                buf.write(pdf_bytes)
                buf.seek(0)
                return buf
            except Exception as exc:
                logger.warning('Playwright failed, falling back to WeasyPrint: %s', exc)

        if _WEASYPRINT_AVAILABLE:
            font_config = _FontConfig()
            _WeasyHTML(string=html).write_pdf(buf, font_config=font_config)
            buf.seek(0)
            return buf

        raise RuntimeError('No PDF engine available. Install playwright or weasyprint.')

    def generate_pdf_base64(self) -> str:
        buf = self.generate_pdf()
        return base64.b64encode(buf.read()).decode('utf-8')
