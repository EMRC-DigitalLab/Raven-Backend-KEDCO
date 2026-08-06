"""
reports/tmo_management_report.py

TMO Management Report — ARIA-narrative, management-style A4 PDF.
Mirrors ManagementPDFGenerator layout: same CSS, same page wrapper,
same cover/back page. Clean minimal charts. Short ARIA commentary.
Toggleable sections. Month-based period logic.

Period logic
────────────
Current month → from_date = 1st, to_date = yesterday
Past month    → from_date = 1st, to_date = last day of month
"Yesterday"   = actual yesterday (current) OR last day of month (past)

Entry points
────────────
POST /api/reports/generate/management/tmo/
POST /api/reports/generate/management/tmo/preview/
"""

from __future__ import annotations

import base64
import calendar
import io
import json
import logging
import math
from datetime import date, timedelta

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None
    _PLAYWRIGHT_AVAILABLE = False

try:
    from weasyprint import HTML as _WeasyHTML
    from weasyprint.text.fonts import FontConfiguration as _FontConfig
    _WEASYPRINT_AVAILABLE = True
except (OSError, ImportError):
    _WeasyHTML = _FontConfig = None
    _WEASYPRINT_AVAILABLE = False

from reports.management_report import MANAGEMENT_STYLES

# =============================================================================
# TMO-SPECIFIC STYLE ADDITIONS (appended on top of MANAGEMENT_STYLES)
# =============================================================================

TMO_EXTRA = """
/* ── tighten page spacing ── */
.page-content { padding: 18px 28px 14px; }
.section-title { margin-top: 0; margin-bottom: 8px; font-size: 17px; }
.subsection-title { margin-top: 10px; margin-bottom: 4px; font-size: 10px; }
.narrative { margin: 6px 0 8px; font-size: 9.5px; line-height: 1.5; }
.callout { padding: 8px 12px; margin: 6px 0; }
.callout-title { font-size: 7.5px; margin-bottom: 2px; }
.callout-text  { font-size: 12px; }
.mgmt-table { margin-bottom: 8px; }
.mgmt-table th, .mgmt-table td { padding: 4px 8px; font-size: 9px; }
.two-col { gap: 12px; margin: 6px 0; }

.kpi-strip {
    display: flex; gap: 0; margin-bottom: 10px;
    border: 1px solid rgba(0,32,80,0.12);
    border-radius: 10px; overflow: hidden;
}
.kpi-strip-item {
    flex: 1; padding: 8px 10px; text-align: center;
    border-right: 1px solid rgba(0,32,80,0.1);
}
.kpi-strip-item:last-child { border-right: none; }
.kpi-strip-label { font-size: 7.5px; font-weight: 700; text-transform: uppercase; opacity: 0.55; margin-bottom: 3px; }
.kpi-strip-value { font-size: 17px; font-weight: 800; }
.kpi-strip-unit  { font-size: 8px; font-weight: 400; opacity: 0.6; }

.bucket-badge { display:inline-block; font-size:8px; font-weight:700; padding:2px 7px; border-radius:4px; }
.bk-exceeding   { background:#C8E6C9; color:#1B5E20; }
.bk-on_target   { background:#DCEDC8; color:#33691E; }
.bk-below_target{ background:#FFF9C4; color:#F57F17; }
.bk-poor        { background:#FFCDD2; color:#B71C1C; }
.bk-critical    { background:#FFCDD2; color:#B71C1C; }

/* TMO report: pages expand to fit content — no clipping */
.page         { height: auto !important; min-height: 265mm; overflow: visible !important; }
.page-content { overflow: visible !important; height: auto !important; flex: none !important; }
.two-col-half { overflow: visible !important; }

.chart-wrap { margin: 4px 0 6px; }

/* TOC page */
.toc-page { padding: 40px 48px; }
.toc-row { display:flex; align-items:baseline; gap:6px; padding:5px 0;
           border-bottom: 1px dashed rgba(0,32,80,0.1); }
.toc-num  { font-size:9px; font-weight:800; color:#5D87FF; width:22px; flex-shrink:0; }
.toc-title{ font-size:10px; font-weight:600; color:#002050; flex:1; }
.toc-dots { flex:1; border-bottom:1px dotted rgba(0,32,80,0.2); margin:0 6px 3px; }
.toc-pg   { font-size:9px; color:#7C8FAC; }
"""

# =============================================================================
# COLOUR PALETTE
# =============================================================================

_C = {
    'primary':      '#5D87FF',
    'warning':      '#FBC02D',
    'success':      '#2E7D32',
    'error':        '#E53935',
    'on_target':    '#8BC34A',
    'below_target': '#FBC02D',
    'poor':         '#EF9A9A',
    'critical':     '#E53935',
    'gap_green':    '#4CAF50',
    'text':         '#002050',
    'grey':         '#7C8FAC',
    'divider':      '#e5eaef',
}

_BUCKET_COLORS = {
    'exceeding':    '#2E7D32',
    'on_target':    '#8BC34A',
    'below_target': '#FBC02D',
    'poor':         '#EF9A9A',
    'critical':     '#E53935',
}

# =============================================================================
# FORMATTING HELPERS
# =============================================================================

def _v(v, d=1, sfx=''):
    try:    return f'{float(v):,.{d}f}{sfx}'
    except: return '—'

def _gwh(v):
    try:
        v = float(v)
        return f'{v/1000:.2f} TWh' if v >= 1000 else f'{v:.2f} GWh'
    except: return '—'

def _mw(v):
    try:
        v = float(v)
        return f'{v/1000:.2f} GW' if abs(v) >= 1000 else f'{v:,.1f} MW'
    except: return '—'

def _naira(v):
    try:
        v = float(v)
        neg, av = v < 0, abs(v)
        p = '₦-' if neg else '₦'
        if av >= 1e9: return f'{p}{av/1e9:.2f}B'
        if av >= 1e6: return f'{p}{av/1e6:.1f}M'
        if av >= 1e3: return f'{p}{av/1e3:.1f}K'
        return f'{p}{av:,.0f}'
    except: return '—'

def _pct(v, d=1):
    try:    return f'{float(v):.{d}f}%'
    except: return '—'

def _rag(v, g, a, higher=True):
    try:
        v = float(v)
        if higher: return 'green' if v >= g else ('amber' if v >= a else 'red')
        else:      return 'green' if v <= g else ('amber' if v <= a else 'red')
    except: return 'grey'

def _rag_badge(status):
    lbl = {'green':'On Track','amber':'Watch','red':'At Risk','grey':'N/A'}.get(status, status)
    return f'<span class="rag rag-{status}">{lbl}</span>'

def _bucket_badge(bucket):
    lbl = {'exceeding':'Exceeding','on_target':'On Target','below_target':'Below Target',
           'poor':'Poor','critical':'Critical'}.get(bucket, bucket.replace('_',' ').title())
    return f'<span class="bucket-badge bk-{bucket}">{lbl}</span>'

# =============================================================================
# PERIOD HELPERS
# =============================================================================

def _compute_period(year: int, month: int):
    """Return (from_date, to_date, is_current, effective_yesterday)."""
    today  = date.today()
    from_d = date(year, month, 1)
    if year == today.year and month == today.month:
        to_d      = today - timedelta(days=1)
        yesterday = to_d
        is_cur    = True
    else:
        ld        = calendar.monthrange(year, month)[1]
        to_d      = date(year, month, ld)
        yesterday = to_d
        is_cur    = False
    return from_d, to_d, is_cur, yesterday

def _period_label(from_d, to_d, is_cur):
    if is_cur:
        return f"MTD: {from_d.strftime('%d %b')} – {to_d.strftime('%d %b %Y')}"
    return f"{from_d.strftime('%B %Y')} · Full Month"

# =============================================================================
# CLEAN SVG CHART HELPERS
# =============================================================================

_W, _H   = 560, 160
_ML, _MR = 34,  10
_MT, _MB = 12,  20
_CW      = _W - _ML - _MR   # 516
_CH      = _H - _MT - _MB   # 128

_GC = '#e5eaef'   # grid colour
_LC = '#7C8FAC'   # label colour


def _svg_open(w=_W, h=_H):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {w} {h}" width="100%" '
            f'style="display:block;font-family:sans-serif;aspect-ratio:{w}/{h};">')


def _svg_grid(y_max, y_min=0, n=4, w=_W, h=_H):
    ml, mr, mt, mb = _ML, _MR, _MT, _MB
    cw = w - ml - mr
    ch = h - mt - mb
    span = (y_max - y_min) or 1
    out = ''
    for i in range(n + 1):
        v  = y_min + (y_max - y_min) * i / n
        py = mt + ch - (v - y_min) / span * ch
        if abs(v) >= 10000: lbl = f'{v/1000:.0f}K'
        elif abs(v) >= 10:  lbl = f'{v:.0f}'
        else:               lbl = f'{v:.1f}'
        out += (f'<line x1="{ml}" y1="{py:.1f}" x2="{w-mr}" y2="{py:.1f}" '
                f'stroke="{_GC}" stroke-width="0.5"/>'
                f'<text x="{ml-4}" y="{py+3:.1f}" text-anchor="end" '
                f'font-size="7" fill="{_LC}">{lbl}</text>')
    return out


def _bar_positions(n):
    slot = max(6, _CW // max(n, 1))
    bw   = max(3, min(40, slot - 3))
    return slot, bw


def _day_label_set(days):
    n = len(days)
    key_days = {1, 5, 10, 15, 20, 25, 30, 31}
    result = set()
    for i, d in enumerate(days):
        dn = d.get('day', i + 1)
        if dn in key_days or i == n - 1:
            result.add(i)
    return result


def svg_vbar_dual(days, key1, color1, key2=None, color2=None,
                  y_title='GWh', label_key='day'):
    """
    Clean vertical bar chart. key1=primary series, key2=optional second series
    (side-by-side). Labels only on key days to avoid clutter.
    """
    if not days:
        return ''
    vals = [float(d.get(key1, 0) or 0) for d in days]
    if key2:
        vals += [float(d.get(key2, 0) or 0) for d in days]
    y_max = (max(vals) * 1.15) if vals else 1.0
    span  = y_max or 1
    n     = len(days)
    slot, bw = _bar_positions(n)
    paired   = key2 is not None
    if paired:
        bw = max(3, (slot - 3) // 2)

    svg  = _svg_open()
    svg += (f'<text x="{_ML - 26}" y="{_MT + _CH // 2}" text-anchor="middle" '
            f'font-size="7" fill="{_LC}" '
            f'transform="rotate(-90,{_ML-26},{_MT+_CH//2})">{y_title}</text>')
    svg += _svg_grid(y_max)

    show_lbl = _day_label_set(days)

    for i, d in enumerate(days):
        sx = _ML + i * slot
        v1 = float(d.get(key1, 0) or 0)

        if paired:
            bx = sx + max(0, (slot - (bw * 2 + 2)) // 2)
        else:
            bx = sx + max(0, (slot - bw) // 2)

        if v1 > 0:
            bh = max(v1 / span * _CH, 2)
            by = _MT + _CH - bh
            svg += f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw}" height="{bh:.1f}" fill="{color1}" rx="1"/>'

        if paired and key2:
            v2 = float(d.get(key2, 0) or 0)
            ax = bx + bw + 2
            if v2 > 0:
                bh2 = max(v2 / span * _CH, 2)
                by2 = _MT + _CH - bh2
                svg += f'<rect x="{ax:.1f}" y="{by2:.1f}" width="{bw}" height="{bh2:.1f}" fill="{color2}" rx="1"/>'

        if i in show_lbl:
            lbl = str(d.get(label_key, d.get('day', i + 1)))
            cx  = sx + slot / 2
            svg += (f'<text x="{cx:.1f}" y="{_H - 4}" text-anchor="middle" '
                    f'font-size="6.5" fill="{_LC}">{lbl}</text>')

    svg += '</svg>'
    return svg


def svg_allocation(days):
    """Grouped diverging: blue=expected, yellow=actual, red/green below zero."""
    if not days:
        return ''
    pos = [max(float(d.get('expected_mw', 0) or 0), float(d.get('actual_mw', 0) or 0)) for d in days]
    neg = [abs(float(d.get('unpicked_mw', 0) or 0)) for d in days]
    y_max = (max(pos) * 1.15) if pos else 1
    y_neg = (max(neg) * 1.15) if any(neg) else 0
    y_min = -y_neg if y_neg > 0 else 0
    span  = (y_max - y_min) or 1
    zy    = _MT + (y_max / span) * _CH

    n = len(days)
    slot, bw = _bar_positions(n)
    bw = max(3, (slot - 2) // 2)

    svg  = _svg_open()
    svg += _svg_grid(y_max, y_min)
    if y_min < 0:
        svg += (f'<line x1="{_ML}" y1="{zy:.1f}" x2="{_W-_MR}" y2="{zy:.1f}" '
                f'stroke="#2A3547" stroke-width="1" stroke-dasharray="3,2"/>')

    show = _day_label_set(days)
    for i, d in enumerate(days):
        sx  = _ML + i * slot
        gw  = bw * 2 + 1
        gx  = sx + max(0, (slot - gw) // 2)
        exp = float(d.get('expected_mw', 0) or 0)
        act = float(d.get('actual_mw', 0) or 0)
        upk = abs(float(d.get('unpicked_mw', 0) or 0))

        if exp > 0:
            bh = max(exp / span * _CH, 2)
            svg += f'<rect x="{gx:.1f}" y="{zy-bh:.1f}" width="{bw}" height="{bh:.1f}" fill="{_C["primary"]}" rx="1"/>'
        if act > 0:
            bh = max(act / span * _CH, 2)
            svg += f'<rect x="{gx+bw+1:.1f}" y="{zy-bh:.1f}" width="{bw}" height="{bh:.1f}" fill="{_C["warning"]}" rx="1"/>'
        if upk > 0:
            rh = max(upk / span * _CH, 2)
            rc = _C['error'] if act > exp else _C['gap_green']
            svg += f'<rect x="{gx:.1f}" y="{zy:.1f}" width="{gw}" height="{rh:.1f}" fill="{rc}" rx="1"/>'

        if i in show:
            cx = sx + slot / 2
            svg += (f'<text x="{cx:.1f}" y="{_H-4}" text-anchor="middle" '
                    f'font-size="6.5" fill="{_LC}">{d.get("day", i+1)}</text>')

    svg += '</svg>'
    return svg


def svg_hbar_segments(segments):
    """Horizontal stacked compliance bars per segment."""
    if not segments:
        return ''
    BUCKETS = [('exceeding','#2E7D32'),('on_target','#8BC34A'),
               ('below_target','#FBC02D'),('poor','#EF9A9A'),('critical','#E53935')]
    ROW_H = 18
    PAD   = 5
    BAR_X = 120
    BAR_W = 360
    H_TOT = len(segments) * (ROW_H + PAD) + 14

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 500 {H_TOT}" width="100%" '
           f'style="display:block;font-family:sans-serif;aspect-ratio:500/{H_TOT};">')

    for si, seg in enumerate(segments):
        y   = 8 + si * (ROW_H + PAD)
        lbl = seg.get('label', seg.get('segment', ''))
        svg += (f'<text x="115" y="{y + ROW_H*0.68:.1f}" text-anchor="end" '
                f'font-size="8.5" fill="{_C["text"]}" font-weight="600">{lbl}</text>')
        svg += (f'<rect x="{BAR_X}" y="{y}" width="{BAR_W}" height="{ROW_H}" '
                f'fill="{_GC}" rx="2"/>')
        x = BAR_X
        for key, color in BUCKETS:
            pct = float(seg.get(key, 0) or 0)
            w   = pct / 100 * BAR_W
            if w >= 1:
                svg += f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{ROW_H}" fill="{color}" rx="1"/>'
                if w > 22:
                    tx = x + w / 2
                    ty = y + ROW_H * 0.68
                    svg += (f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" '
                            f'font-size="7" fill="#fff" font-weight="700">{pct:.0f}%</text>')
                x += w

    svg += '</svg>'
    return svg


def svg_donut(slices, size=130):
    """Clean donut chart. slices = [(label, value, color), ...]"""
    total = sum(v for _, v, _ in slices if v > 0)
    if not total:
        return ''
    cx = cy = size / 2
    R, Ri = size * 0.42, size * 0.24

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
           f'style="display:inline-block;font-family:sans-serif;">')

    angle = -math.pi / 2
    for lbl, val, color in slices:
        if val <= 0:
            continue
        sw  = val / total * 2 * math.pi
        x1  = cx + R  * math.cos(angle)
        y1  = cy + R  * math.sin(angle)
        x2  = cx + R  * math.cos(angle + sw)
        y2  = cy + R  * math.sin(angle + sw)
        xi1 = cx + Ri * math.cos(angle + sw)
        yi1 = cy + Ri * math.sin(angle + sw)
        xi2 = cx + Ri * math.cos(angle)
        yi2 = cy + Ri * math.sin(angle)
        lg  = 1 if sw > math.pi else 0
        svg += (f'<path d="M{x1:.2f} {y1:.2f} A{R} {R} 0 {lg} 1 {x2:.2f} {y2:.2f} '
                f'L{xi1:.2f} {yi1:.2f} A{Ri} {Ri} 0 {lg} 0 {xi2:.2f} {yi2:.2f} Z" '
                f'fill="{color}"/>')
        angle += sw

    biggest = max(slices, key=lambda s: s[1])
    pct = biggest[1] / total * 100
    svg += (f'<text x="{cx}" y="{cy-3}" text-anchor="middle" font-size="8" '
            f'font-weight="700" fill="{biggest[2]}">{biggest[0]}</text>'
            f'<text x="{cx}" y="{cy+9}" text-anchor="middle" font-size="12" '
            f'font-weight="800" fill="{_C["text"]}">{pct:.1f}%</text>')
    svg += '</svg>'
    return svg


def svg_line_chart(days, series, y_title='GWh', h=170, every_day=False):
    """
    Clean multi-series line/area chart with visible data points.
    series = [(key, color, fill_opacity, dashed), ...]
    every_day=True → circle at every day, label at every day (staggered above/below).
    """
    if not days:
        return ''
    n = len(days)
    W, H = 560, h
    ML, MR, MT, MB = 38, 12, 14, 22
    CW = W - ML - MR
    CH = H - MT - MB

    all_vals = []
    for key, *_ in series:
        all_vals += [float(d.get(key, 0) or 0) for d in days]
    y_max = max(all_vals) * 1.12 if all_vals and max(all_vals) > 0 else 1.0
    span = y_max

    def gx(i):
        return ML + (i * CW / (n - 1)) if n > 1 else ML + CW // 2

    def gy(v):
        return MT + CH - (v / span) * CH

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'width="100%" style="display:block;font-family:sans-serif;aspect-ratio:{W}/{H};">')

    cx, cy = ML - 28, MT + CH // 2
    svg += (f'<text x="{cx}" y="{cy}" text-anchor="middle" font-size="7" fill="{_LC}" '
            f'transform="rotate(-90,{cx},{cy})">{y_title}</text>')

    for i in range(5):
        v = y_max * i / 4
        y = gy(v)
        lbl = f'{v:.0f}' if v >= 10 else f'{v:.1f}'
        svg += (f'<line x1="{ML}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}" '
                f'stroke="{_GC}" stroke-width="0.5"/>'
                f'<text x="{ML-4}" y="{y+3:.1f}" text-anchor="end" '
                f'font-size="7" fill="{_LC}">{lbl}</text>')

    # Value labels only on key days regardless of every_day — prevents clutter
    lbl_idx = _day_label_set(days)

    base_y = gy(0)

    # Area fills first
    for key, color, fill_op, _dash in series:
        if not fill_op:
            continue
        vals = [float(d.get(key, 0) or 0) for d in days]
        pts  = [(gx(i), gy(v)) for i, v in enumerate(vals)]
        pd   = f'M{pts[0][0]:.1f},{base_y:.1f} L{pts[0][0]:.1f},{pts[0][1]:.1f}'
        for px_, py_ in pts[1:]:
            pd += f' L{px_:.1f},{py_:.1f}'
        pd += f' L{pts[-1][0]:.1f},{base_y:.1f} Z'
        svg += f'<path d="{pd}" fill="{color}" opacity="{fill_op}"/>'

    # Lines
    for key, color, _fop, dashed in series:
        vals  = [float(d.get(key, 0) or 0) for d in days]
        pts   = [(gx(i), gy(v)) for i, v in enumerate(vals)]
        dash  = 'stroke-dasharray="5,3"' if dashed else ''
        ld    = 'M' + ' L'.join(f'{px_:.1f},{py_:.1f}' for px_, py_ in pts)
        svg += (f'<path d="{ld}" fill="none" stroke="{color}" '
                f'stroke-width="2" {dash} stroke-linejoin="round" stroke-linecap="round"/>')

    # Circles at ALL days; value labels at lbl_idx (staggered above/below when every_day)
    for key, color, _fop, _dash in series:
        vals = [float(d.get(key, 0) or 0) for d in days]
        for i in range(n):
            v = vals[i]
            px_, py_ = gx(i), gy(v)
            r = 2 if every_day else 3
            svg += (f'<circle cx="{px_:.1f}" cy="{py_:.1f}" r="{r}" '
                    f'fill="{color}" stroke="#fff" stroke-width="0.8"/>')
            if i in lbl_idx and v > 0:
                lbl = f'{v:.1f}'
                y_text = py_ - 7 if py_ > MT + 20 else py_ + 14
                fs = '6.5'
                svg += (f'<text x="{px_:.1f}" y="{y_text:.1f}" text-anchor="middle" '
                        f'font-size="{fs}" fill="{color}" font-weight="700">{lbl}</text>')

    # X-axis day labels — every day when every_day=True
    x_lbl_idx = range(n) if every_day else lbl_idx
    for i in x_lbl_idx:
        d = days[i]
        svg += (f'<text x="{gx(i):.1f}" y="{H-4}" text-anchor="middle" '
                f'font-size="6" fill="{_LC}">{d.get("day", i+1)}</text>')

    svg += '</svg>'
    return svg


def svg_line_allocation(days):
    """2-line area chart: expected allocation (blue dashed) vs actual consumption (yellow).
    Overrun segment (actual > expected) highlighted red."""
    if not days:
        return ''
    n = len(days)
    W, H = 560, 170
    ML, MR, MT, MB = 38, 12, 14, 22
    CW = W - ML - MR
    CH = H - MT - MB

    exps = [float(d.get('expected_mw', 0) or 0) for d in days]
    acts = [float(d.get('actual_mw',   0) or 0) for d in days]
    y_max = max(max(exps, default=0), max(acts, default=0)) * 1.12 or 1.0
    span  = y_max

    def gx(i):
        return ML + (i * CW / (n - 1)) if n > 1 else ML + CW // 2

    def gy(v):
        return MT + CH - (v / span) * CH

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'width="100%" style="display:block;font-family:sans-serif;aspect-ratio:{W}/{H};">')

    cx, cy = ML - 28, MT + CH // 2
    svg += (f'<text x="{cx}" y="{cy}" text-anchor="middle" font-size="7" fill="{_LC}" '
            f'transform="rotate(-90,{cx},{cy})">MW</text>')

    for i in range(5):
        v = y_max * i / 4
        y = gy(v)
        lbl = f'{v:.0f}' if v >= 10 else f'{v:.1f}'
        svg += (f'<line x1="{ML}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}" '
                f'stroke="{_GC}" stroke-width="0.5"/>'
                f'<text x="{ML-4}" y="{y+3:.1f}" text-anchor="end" '
                f'font-size="7" fill="{_LC}">{lbl}</text>')

    base_y = gy(0)
    pts_e  = [(gx(i), gy(v)) for i, v in enumerate(exps)]
    pts_a  = [(gx(i), gy(v)) for i, v in enumerate(acts)]

    def area(pts, color, op):
        p = f'M{pts[0][0]:.1f},{base_y:.1f} L{pts[0][0]:.1f},{pts[0][1]:.1f}'
        for px_, py_ in pts[1:]:
            p += f' L{px_:.1f},{py_:.1f}'
        p += f' L{pts[-1][0]:.1f},{base_y:.1f} Z'
        return f'<path d="{p}" fill="{color}" opacity="{op}"/>'

    svg += area(pts_e, _C['primary'], 0.1)
    svg += area(pts_a, _C['warning'], 0.15)

    # Overrun highlight (actual > expected)
    for i in range(len(days) - 1):
        if acts[i] > exps[i] or acts[i+1] > exps[i+1]:
            x1, x2   = gx(i), gx(i+1)
            ey1, ey2  = gy(exps[i]), gy(exps[i+1])
            ay1, ay2  = gy(acts[i]), gy(acts[i+1])
            svg += (f'<polygon points="{x1:.1f},{ey1:.1f} {x2:.1f},{ey2:.1f} '
                    f'{x2:.1f},{ay2:.1f} {x1:.1f},{ay1:.1f}" '
                    f'fill="{_C["error"]}" opacity="0.2"/>')

    def lpath(pts, color, dashed=False):
        dash = 'stroke-dasharray="5,3"' if dashed else ''
        ld   = 'M' + ' L'.join(f'{px_:.1f},{py_:.1f}' for px_, py_ in pts)
        return (f'<path d="{ld}" fill="none" stroke="{color}" '
                f'stroke-width="2" {dash} stroke-linejoin="round" stroke-linecap="round"/>')

    svg += lpath(pts_e, _C['primary'], dashed=True)
    svg += lpath(pts_a, _C['warning'])

    # Circles at ALL days; value labels only on key days
    show = _day_label_set(days)
    for i in range(len(days)):
        e, a = exps[i], acts[i]
        px_e, py_e = gx(i), gy(e)
        px_a, py_a = gx(i), gy(a)
        svg += (f'<circle cx="{px_e:.1f}" cy="{py_e:.1f}" r="2" '
                f'fill="{_C["primary"]}" stroke="#fff" stroke-width="0.8"/>')
        svg += (f'<circle cx="{px_a:.1f}" cy="{py_a:.1f}" r="2" '
                f'fill="{_C["warning"]}" stroke="#fff" stroke-width="0.8"/>')
        if i in show:
            a_y = py_a - 7 if py_a > MT + 20 else py_a + 12
            e_y = py_e - 7 if py_e > MT + 20 else py_e + 12
            svg += (f'<text x="{px_a:.1f}" y="{a_y:.1f}" text-anchor="middle" '
                    f'font-size="6.5" fill="{_C["warning"]}" font-weight="700">{a:.0f}</text>')
            svg += (f'<text x="{px_e:.1f}" y="{e_y:.1f}" text-anchor="middle" '
                    f'font-size="6.5" fill="{_C["primary"]}" font-weight="700">{e:.0f}</text>')

    for i, d in enumerate(days):
        svg += (f'<text x="{gx(i):.1f}" y="{H-4}" text-anchor="middle" '
                f'font-size="6" fill="{_LC}">{d.get("day", i+1)}</text>')

    svg += '</svg>'
    return svg


def _legend(items):
    """Render a simple SVG legend row. items = [(label, color), ...]"""
    parts = []
    for lbl, color in items:
        parts.append(
            f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;">'
            f'<span style="width:10px;height:10px;border-radius:50%;background:{color};'
            f'display:inline-block;flex-shrink:0;"></span>'
            f'<span style="font-size:8.5px;">{lbl}</span></span>'
        )
    return f'<div style="margin-bottom:8px;">{"".join(parts)}</div>'

# =============================================================================
# PAGE WRAPPER (mirrors management_report._page)
# =============================================================================

def _page(content: str, context: dict, page_number: int) -> str:
    company = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    title   = context.get('report_title', 'TMO Management Report')
    period  = context.get('period_label', '')
    return f"""
    <div class="page">
        <div class="page-content">
            <div class="page-header">
                <span class="header-company">{company}</span>
                <span class="header-subtitle">{title} &nbsp;|&nbsp; {period}</span>
            </div>
            {content}
        </div>
        <div class="page-footer">
            <span class="footer-label">RAVEN · TMO Management Report · Confidential</span>
            <span class="footer-page">{page_number}</span>
        </div>
    </div>"""

# =============================================================================
# ARIA — short narrative prompt
# =============================================================================

_ARIA_SYSTEM = (
    "You are ARIA, an AI analyst for KEDCO (Kano Electricity Distribution Company). "
    "Write comprehensive, data-driven management commentary for a formal report. Rules:\n"
    "1. Every sentence must reference an actual number, percentage, or figure from the data.\n"
    "2. Write like a senior utility analyst briefing a CEO and Board: plain English, confident, no jargon.\n"
    "3. For each narrative field write a proper analytical paragraph (3-5 sentences): "
    "what the data shows, whether it is good or bad vs target, the trend or pattern, and the implication for operations.\n"
    "4. Never say 'data is shown below', 'presented in the chart', 'as illustrated', or any filler phrase.\n"
    "5. State both strengths and concerns — do not soften bad news.\n"
    "6. Priority issues and action plan must be specific, operational, and tied to real figures.\n"
    "7. Return valid JSON only. No markdown fences. No em-dashes. No ellipsis.\n"
    "8. Never include literal newline characters inside JSON string values — keep each value on one line."
)


def _clean_aria_json(raw: str) -> str:
    """
    Clean common JSON issues from ARIA responses:
    - Strip markdown fences
    - Collapse literal newlines inside string values into spaces
    - Remove trailing commas before } or ]
    """
    import re
    raw = re.sub(r'^```[a-z]*\n?', '', raw.strip(), flags=re.M)
    raw = re.sub(r'\n?```$',       '', raw,          flags=re.M)

    # Collapse newlines that appear inside JSON string values
    # Strategy: scan char by char, track whether we're inside a string
    result = []
    in_str = False
    escaped = False
    for ch in raw:
        if escaped:
            result.append(ch)
            escaped = False
        elif ch == '\\' and in_str:
            result.append(ch)
            escaped = True
        elif ch == '"':
            in_str = not in_str
            result.append(ch)
        elif ch in ('\n', '\r') and in_str:
            result.append(' ')   # replace newline with space inside strings
        else:
            result.append(ch)
    cleaned = ''.join(result)

    # Remove trailing commas before ] or }
    cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)
    return cleaned


def _call_aria(prompt: str, max_tokens: int = 3500) -> dict:
    import re, anthropic
    from django.conf import settings
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        return {}
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model='claude-sonnet-4-6', max_tokens=max_tokens,
        system=[{"type": "text", "text": _ARIA_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()

    # Attempt 1: direct parse after cleaning
    try:
        return json.loads(_clean_aria_json(raw))
    except Exception:
        pass

    # Attempt 2: strip trailing text after final }
    try:
        cleaned = _clean_aria_json(raw)
        brace_end = cleaned.rfind('}')
        if brace_end > 0:
            return json.loads(cleaned[:brace_end + 1])
    except Exception:
        pass

    # Attempt 3: extract outermost {...} block
    try:
        m = re.search(r'\{.*\}', _clean_aria_json(raw), re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass

    logger.warning("ARIA: all JSON parse attempts failed; using fallback")
    return {}


def _build_aria_prompt(all_data: dict, period: str, company: str) -> str:
    overview = all_data.get('overview', {})
    energy   = all_data.get('daily_energy', {})
    alloc_s  = all_data.get('allocation', {}).get('summary', {})
    comp_tbl = all_data.get('compliance_table', {})
    comp_seg = all_data.get('compliance_segment', {})
    pear     = all_data.get('pear', {})
    gcr      = all_data.get('gcr', {})
    minigrids= all_data.get('minigrids', {})
    incidents= all_data.get('incidents', {})

    feeders        = comp_tbl.get('feeders', [])
    critical_count = sum(1 for f in feeders if f.get('bucket') in ('critical', 'poor'))
    total_feeders  = len(feeders)
    energy_days    = energy.get('days', [])
    lowest_day  = min(energy_days, key=lambda d: float(d.get('actual_gwh', 0) or 0), default={})
    highest_day = max(energy_days, key=lambda d: float(d.get('actual_gwh', 0) or 0), default={})
    exp_mw = float(alloc_s.get('total_expected_mw', 0) or 1)
    upk_mw = float(alloc_s.get('total_unpicked_mw', 0) or 0)
    unpicked_pct = round(upk_mw / exp_mw * 100, 1) if exp_mw else 0

    data_block = {
        'period': period,
        'overview': overview,
        'energy': {
            'monthly_target_gwh':  energy.get('monthly_target_gwh'),
            'total_actual_gwh':    energy.get('total_actual_gwh'),
            'mtd_achievement_pct': energy.get('mtd_achievement_pct'),
            'lowest_day':  {'day': lowest_day.get('day'),  'gwh': lowest_day.get('actual_gwh')},
            'highest_day': {'day': highest_day.get('day'), 'gwh': highest_day.get('actual_gwh')},
        },
        'allocation': {**alloc_s, 'unpicked_pct': unpicked_pct},
        'compliance': {
            'summary': comp_tbl.get('summary', {}),
            'critical_or_poor_feeders': critical_count,
            'total_feeders': total_feeders,
            'segments': comp_seg.get('segments', []),
        },
        'pear': pear,
        'gcr': (gcr.get('segments') or [])[:4],
        'minigrid_total_mwh': minigrids.get('summary', {}).get('total_mwh'),
        'incidents': incidents,
    }

    tpl = (
        "Prepare a comprehensive TMO management report narrative for {company}, period: {period}.\n\n"
        "Critical rules:\n"
        "- Every sentence must cite a real number from the data below.\n"
        "- Write at the depth of a formal management report — analytical, confident, professional.\n"
        "- State clearly whether performance is good or bad vs target, and by how much.\n"
        "- Explain the operational implication, not just the number.\n"
        "- Never use: 'shown below', 'presented in the chart', 'as illustrated', or any placeholder phrase.\n\n"
        "DATA:\n{data}\n\n"
        "Return ONLY valid JSON (no markdown, no extra text):\n"
        "{{\n"
        "  \"executive_summary\": \"3-4 sentences. Cover: (1) overall MTD achievement vs target with the actual % figure, "
        "(2) the strongest and weakest performance area with numbers, "
        "(3) the primary operational risk to closing the period, "
        "(4) a forward-looking statement on what must happen to recover.\",\n"
        "  \"executive_bullets\": [\n"
        "    \"What is performing above target — cite the specific figure and why it matters\",\n"
        "    \"The most critical underperformance — cite the specific gap and its consequence\",\n"
        "    \"The single highest-priority action needed this period with a target number\"\n"
        "  ],\n"
        "  \"kpi_narrative\": \"3-4 sentences. Walk through the key KPIs: energy achievement, supply compliance, "
        "and any metric that is materially off-track. State each figure, the target, and the gap.\",\n"
        "  \"daily_energy_narrative\": \"3-4 sentences. Describe the MTD energy position vs monthly target "
        "(cite both GWh figures and the achievement %). Identify the best and worst performing days with their GWh figures. "
        "Comment on whether the daily trend is improving, declining, or flat, and what that means for end-of-month delivery.\",\n"
        "  \"allocation_narrative\": \"3-4 sentences. State the total MW allocated vs total consumed and the resulting unpicked %. "
        "Explain whether the shortfall is a capacity issue or a demand issue. "
        "State the financial and operational implication of the unpicked energy. "
        "Recommend whether the allocation level should be reviewed.\",\n"
        "  \"compliance_narrative\": \"3-4 sentences. Give the overall feeder compliance rate and the number of compliant vs total feeders. "
        "Identify which DM segment is performing worst and its compliance level. "
        "State how many feeders are in the critical/poor buckets and what that means for customer supply hours. "
        "Note whether compliance is improving or deteriorating.\",\n"
        "  \"minigrid_voltage_narrative\": \"3-4 sentences. State the total minigrid energy contribution in GWh or MWh for the period. "
        "Break down the 33KV vs 11KV energy split by segment and note which voltage level is carrying more load. "
        "Comment on whether the voltage mix is aligned with the DM segment demand profile.\",\n"
        "  \"pnl_narrative\": \"3-4 sentences. State the current PEAR ratio (yesterday and MTD) and whether it is above or below target. "
        "Quantify the GCR billing gap — both in energy terms (GWh) and revenue terms (Naira) if available. "
        "Explain which segment is driving the largest billing gap. "
        "State the revenue-at-risk if the gap is not closed by month end.\",\n"
        "  \"priority_issues\": [\n"
        "    {{\"issue\": \"Concise issue title\", \"evidence\": \"Specific figure or fact from the data — e.g. X feeders at critical, Y% below target\", \"risk\": \"High/Medium/Low\", \"response\": \"Concrete operational action with a target or deadline\"}},\n"
        "    {{\"issue\": \"Concise issue title\", \"evidence\": \"Specific figure or fact from the data\", \"risk\": \"High/Medium/Low\", \"response\": \"Concrete operational action\"}},\n"
        "    {{\"issue\": \"Concise issue title\", \"evidence\": \"Specific figure or fact from the data\", \"risk\": \"High/Medium/Low\", \"response\": \"Concrete operational action\"}},\n"
        "    {{\"issue\": \"Concise issue title\", \"evidence\": \"Specific figure or fact from the data\", \"risk\": \"High/Medium/Low\", \"response\": \"Concrete operational action\"}}\n"
        "  ],\n"
        "  \"action_plan\": [\n"
        "    {{\"area\": \"Area name\", \"action\": \"Specific action with a measurable target — e.g. raise feeder X compliance to 90% by day 25\", \"team\": \"Responsible team\", \"timeline\": \"Specific deadline\"}},\n"
        "    {{\"area\": \"Area name\", \"action\": \"Specific action with a measurable target\", \"team\": \"Responsible team\", \"timeline\": \"Specific deadline\"}},\n"
        "    {{\"area\": \"Area name\", \"action\": \"Specific action with a measurable target\", \"team\": \"Responsible team\", \"timeline\": \"Specific deadline\"}},\n"
        "    {{\"area\": \"Area name\", \"action\": \"Specific action with a measurable target\", \"team\": \"Responsible team\", \"timeline\": \"Specific deadline\"}}\n"
        "  ]\n"
        "}}"
    )

    return tpl.format(
        company=company,
        period=period,
        data=json.dumps(data_block, indent=2, default=str)[:4000],
    )


def _fallback_aria():
    return {
        "executive_summary": (
            "This TMO Management Report covers KEDCO's operational performance for the reporting period, "
            "consolidating energy dispatch, feeder compliance, real-time allocation, P&L billing, and incident data "
            "into a single management view. The report highlights areas of strength and identifies where immediate "
            "operational intervention is required to protect revenue and service delivery targets."
        ),
        "executive_bullets": [
            "Review MTD energy achievement against contracted monthly target — identify days with largest gap.",
            "Escalate critical and poor feeder compliance cases, particularly in MDI/MDNI segments where revenue impact is highest.",
            "Close the GCR billing gap before month-end by validating metering accuracy and dispatch records.",
        ],
        "kpi_narrative": (
            "The KPI dashboard tracks KEDCO's core operational metrics against contracted targets. "
            "Energy achievement measures the MTD actual delivery as a percentage of the monthly contracted target, "
            "with 95% as the optimal threshold and 80% as the minimum acceptable performance level. "
            "Supply compliance measures the share of feeders delivering at or above their required daily supply hours. "
            "Any metric below the amber threshold requires immediate management attention and corrective action."
        ),
        "daily_energy_narrative": (
            "The daily energy chart plots KEDCO's actual energy consumption against the daily target allocation for each day of the period. "
            "The dashed line represents the contracted target; the solid line is the actual delivered figure. "
            "Days where actual falls below the target line indicate shortfalls that compound into the MTD gap. "
            "Consistent underperformance on weekdays typically reflects dispatch constraints from TCN, while weekend dips often reflect lower industrial demand."
        ),
        "allocation_narrative": (
            "The real-time allocation chart compares TCN's daily allocated MW (the dashed blue line) against KEDCO's actual consumption (the amber line). "
            "When KEDCO consumes less than its allocation, energy is returned to the TCN pool unpicked — representing a missed revenue and distribution opportunity. "
            "When KEDCO exceeds its allocation, it indicates ungated or unmetered load that must be investigated. "
            "The overrun shaded area (red) highlights days where KEDCO consumption exceeded its TCN dispatch limit."
        ),
        "compliance_narrative": (
            "Feeder compliance measures the percentage of feeders achieving their daily contracted supply hours. "
            "MDI and MDNI feeders are shown here as they represent KEDCO's premium revenue segments — sustained non-compliance "
            "on these feeders directly erodes billing revenue and risks customer attrition to alternative energy sources. "
            "Feeders classified as Critical (below 25% compliance) require same-day field dispatch; "
            "those in the Poor band (25–50%) must be escalated to district managers within 24 hours."
        ),
        "minigrid_voltage_narrative": (
            "This section breaks down energy delivery by voltage level (33KV vs 11KV) and tracks the performance of KEDCO's minigrid feeders. "
            "The 33KV network primarily serves MDI and industrial customers; the 11KV network serves MDNI and residential customers. "
            "Minigrid feeders provide last-mile energy to off-grid communities and are tracked separately to ensure rural electrification targets are met. "
            "Month-on-month comparison identifies voltage-level growth or decline trends critical for infrastructure planning."
        ),
        "pnl_narrative": (
            "The P&L review examines KEDCO's energy revenue positioning for the period. "
            "PEAR (Priority Energy Allocation Ratio) measures the share of energy delivered to MD (Maximum Demand) customers versus NMD — "
            "the target is to maximise the MD share as these customers generate higher tariff revenue per unit. "
            "The GCR (Generation Cost Recovery) analysis shows the gap between what KEDCO should have billed versus what was actually billed, "
            "broken down by segment to pinpoint where revenue leakage is concentrated."
        ),
        "priority_issues": [{"issue":"Data Review","evidence":"Manual check required.","risk":"Low","response":"Validate with field teams."}],
        "action_plan": [{"area":"Operations","action":"Review daily dispatch.","team":"TMO","timeline":"Weekly"}],
    }

# =============================================================================
# SECTION RENDERERS
# =============================================================================

# ── 1. Cover ─────────────────────────────────────────────────────────────────

def render_cover(context: dict) -> str:
    company     = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    title       = context.get('report_title', 'TMO Management Report')
    period      = context.get('period_label', '')
    footer_logo = context.get('footer_logo_url', '')

    words       = title.split()
    main        = ' '.join(words[:-1]) if len(words) > 1 else title
    accent      = words[-1] if len(words) > 1 else ''

    return f"""
    <div class="cover-page">
        <div class="cover-accent"></div>
        <div class="cover-body">
            <div class="cover-eyebrow">{company} &nbsp;&bull;&nbsp; TMO Management Report</div>
            <div style="flex:1;">
                <div style="font-size:10px;font-weight:700;text-transform:uppercase;
                            letter-spacing:2.5px;opacity:0.5;margin-bottom:18px;">
                    Transmission &amp; Market Operations
                </div>
                <h1 class="cover-main-title">
                    {main}<br/>
                    <span class="cover-main-title-accent">{accent}</span>
                </h1>
                <div class="cover-rule"></div>
                <div class="cover-subtitle">{company}</div>
            </div>
            <div class="cover-footer-strip">
                <img src="{footer_logo}" alt="RAVEN"/>
                <span class="cover-period">{period}</span>
            </div>
        </div>
    </div>"""


# ── Table of Contents ────────────────────────────────────────────────────────

_TOC_SECTIONS = [
    (1,  'Executive Summary',           'Key performance indicators and management narrative'),
    (2,  'KPI Dashboard',               'Energy achievement, compliance status and gap analysis'),
    (3,  'Daily Energy Review',         'Day-by-day target vs actual energy delivery trend'),
    (4,  'Daily Real-Time Allocation',  'TCN allocation vs KEDCO consumption and overrun analysis'),
    (5,  'Feeder Compliance Performance','Segment compliance breakdown and feeder criticality log'),
    (6,  'Minigrid & Voltage Analysis', '33KV/11KV energy split and minigrid feeder performance'),
    (7,  'P&L & Billing Review',        'PEAR mix, GCR billing gap and revenue achievement'),
    (8,  'Priority Issues & Incidents', 'Active fault log and ARIA strategic issue analysis'),
    (9,  'ARIA Action Plan',            'AI-recommended actions, owners and timelines'),
]

def render_toc(context: dict, sections: list) -> str:
    company = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    period  = context.get('period_label', '')
    title   = context.get('report_title', 'TMO Management Report')

    # Map section numbers to page numbers (cover=1, TOC=2, sections start at 3)
    sec_keys = [
        'tmo_executive_summary', 'tmo_kpi_dashboard', 'tmo_daily_energy',
        'tmo_daily_allocation', 'tmo_feeder_compliance', 'tmo_minigrid_voltage',
        'tmo_pnl_review', 'tmo_priority_issues', 'tmo_action_plan',
    ]
    active = set(sections)
    pg = 3  # cover=1, toc=2, first section=3

    rows = ''
    for (num, sec_title, desc), key in zip(_TOC_SECTIONS, sec_keys):
        if key not in active:
            continue
        rows += f"""
        <div class="toc-row">
            <span class="toc-num">{num:02d}</span>
            <div style="flex:1;">
                <div class="toc-title">{sec_title}</div>
                <div style="font-size:8px;color:#7C8FAC;margin-top:1px;">{desc}</div>
            </div>
            <span class="toc-dots"></span>
            <span class="toc-pg">{pg}</span>
        </div>"""
        pg += 1

    content = f"""
        <div class="page-header">
            <span class="header-company">{company}</span>
            <span class="header-subtitle">{title} &nbsp;|&nbsp; {period}</span>
        </div>
        <div style="margin-bottom:18px;">
            <div style="font-size:7px;font-weight:700;text-transform:uppercase;letter-spacing:2px;
                        color:#5D87FF;margin-bottom:6px;">Contents</div>
            <h2 style="font-size:20px;font-weight:800;color:#002050;margin:0 0 4px;">
                Table of Contents</h2>
            <div style="width:32px;height:3px;background:#5D87FF;border-radius:2px;"></div>
        </div>
        {rows}"""

    return f"""
    <div class="page">
        <div class="page-content" style="padding:22px 28px 14px;">
            {content}
        </div>
        <div class="page-footer">
            <span class="footer-label">RAVEN · TMO Management Report · Confidential</span>
            <span class="footer-page">2</span>
        </div>
    </div>"""


# ── 2. Executive Summary ──────────────────────────────────────────────────────

def render_executive_summary(data: dict, ai: dict, context: dict, pg: int) -> str:
    ov      = data.get('overview', {})
    supply  = ov.get('supply', {})
    summ    = ai.get('executive_summary', '')
    bullets = ai.get('executive_bullets', [])

    ach_pct  = float(ov.get('mtd_achievement_pct', 0) or 0)
    act_gwh  = float(ov.get('total_actual_gwh',    0) or 0)
    tgt_gwh  = float(ov.get('monthly_target_gwh',  0) or 0)
    comp_pct = float(supply.get('compliance_pct',  0) or 0)
    comp_n   = int(supply.get('compliant_feeders', 0) or 0)
    total_f  = int(supply.get('total_feeders',     0) or 0)

    ach_color = (_C['success'] if ach_pct >= 95
                 else (_C['warning'] if ach_pct >= 80 else _C['error']))

    kpi_strip = f"""
    <div class="kpi-strip">
        <div class="kpi-strip-item">
            <div class="kpi-strip-label">Energy Achievement</div>
            <div class="kpi-strip-value" style="color:{ach_color};">{_pct(ach_pct)}</div>
        </div>
        <div class="kpi-strip-item">
            <div class="kpi-strip-label">MTD Actual</div>
            <div class="kpi-strip-value">{_gwh(act_gwh)}</div>
        </div>
        <div class="kpi-strip-item">
            <div class="kpi-strip-label">Monthly Target</div>
            <div class="kpi-strip-value">{_gwh(tgt_gwh)}</div>
        </div>
        <div class="kpi-strip-item">
            <div class="kpi-strip-label">Feeder Compliance</div>
            <div class="kpi-strip-value" style="color:{_C['error'] if comp_pct < 50 else _C['warning']};">
                {_pct(comp_pct)}</div>
            <div style="font-size:8px;opacity:0.55;">{comp_n} / {total_f} feeders</div>
        </div>
    </div>"""

    bullet_html = ''
    if bullets:
        items = ''.join(f'<li style="margin-bottom:4px;">{b}</li>' for b in bullets[:3])
        bullet_html = f'<ul style="padding-left:18px;margin:10px 0;">{items}</ul>'

    narrative = f'<p class="narrative">{summ}</p>' if summ else ''

    content = f"""
        <h1 class="section-title">1. Executive Summary</h1>
        {kpi_strip}
        {narrative}
        {bullet_html}"""
    return _page(content, context, pg)


# ── 3. KPI Dashboard ─────────────────────────────────────────────────────────

def render_kpi_dashboard(data: dict, ai: dict, context: dict, pg: int) -> str:
    ov      = data.get('overview', {})
    supply  = ov.get('supply', {})
    narr    = ai.get('kpi_narrative', '')

    ach     = float(ov.get('mtd_achievement_pct', 0) or 0)
    comp    = float(supply.get('compliance_pct',  0) or 0)
    comp_n  = int(supply.get('compliant_feeders', 0) or 0)
    total_f = int(supply.get('total_feeders',     0) or 0)
    act_gwh = float(ov.get('total_actual_gwh',    0) or 0)
    tgt_gwh = float(ov.get('monthly_target_gwh',  0) or 0)
    gap_gwh = tgt_gwh - act_gwh
    status  = ov.get('mtd_status', '')

    rows = f"""
    <tr>
        <td>Energy Achievement</td>
        <td><strong>{_pct(ach)}</strong></td>
        <td>{_rag_badge(_rag(ach, 95, 80))}</td>
        <td>Target: ≥ 95% optimal, ≥ 80% acceptable</td>
    </tr>
    <tr>
        <td>Supply Compliance</td>
        <td><strong>{_pct(comp)}</strong></td>
        <td>{_rag_badge(_rag(comp, 70, 50))}</td>
        <td>{comp_n} of {total_f} feeders meeting supply hours — target: ≥ 70%</td>
    </tr>
    <tr>
        <td>MTD Energy Actual</td>
        <td><strong>{_gwh(act_gwh)}</strong></td>
        <td>{_rag_badge('grey')}</td>
        <td>Month-to-date energy consumed</td>
    </tr>
    <tr>
        <td>MTD Energy Target</td>
        <td><strong>{_gwh(tgt_gwh)}</strong></td>
        <td>{_rag_badge('grey')}</td>
        <td>Contracted monthly target</td>
    </tr>
    <tr>
        <td>Energy Gap to Target</td>
        <td><strong>{_gwh(gap_gwh)}</strong></td>
        <td>{_rag_badge(_rag(gap_gwh, 0, 5, higher=False))}</td>
        <td>Remaining gap to achieve target</td>
    </tr>"""

    narrative = f'<p class="narrative">{narr}</p>' if narr else ''
    if status:
        # Colour based on actual achievement %, not just the status string
        if ach >= 95:
            rag_cls = 'green'
        elif ach >= 80:
            rag_cls = 'amber'
        else:
            rag_cls = 'red'
        status_lbl  = str(status).replace('_', ' ').title()
        status_chip = f'<span class="rag rag-{rag_cls}">{status_lbl}</span>'
    else:
        status_chip = ''

    content = f"""
        <h1 class="section-title">2. KPI Dashboard</h1>
        {narrative}
        <table class="mgmt-table kpi-table">
            <thead><tr>
                <th style="width:25%">Metric</th>
                <th style="width:15%">Value</th>
                <th style="width:12%">Status</th>
                <th>Management Note</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>
        {(f'<div class="callout"><div class="callout-title">Overall Status</div>'
          f'<div class="callout-text">{status_chip}</div></div>') if status_chip else ''}"""
    return _page(content, context, pg)


# ── 4. Daily Energy Review ────────────────────────────────────────────────────

def render_daily_energy(data: dict, ai: dict, context: dict, pg: int) -> str:
    energy = data.get('daily_energy', {})
    days   = energy.get('days', [])
    narr   = ai.get('daily_energy_narrative', '')

    # Dual-series line chart: target (dashed dark) vs actual (solid blue) — every day visible
    chart1 = svg_line_chart(
        days,
        [
            ('target_gwh', '#2A3547', 0.05, True),   # dark navy — clearly distinct from actual
            ('actual_gwh', _C['primary'], 0.18, False),  # bright primary blue
        ],
        y_title='GWh',
        every_day=True,
    )
    # Single-series: actual daily consumed
    chart2 = svg_line_chart(
        days,
        [('actual_gwh', _C['primary'], 0.2, False)],
        y_title='GWh',
        h=130,
        every_day=True,
    )

    leg1 = _legend([('Daily Target (dashed)', '#2A3547'), ('Actual Consumed', _C['primary'])])
    narrative = f'<p class="narrative">{narr}</p>' if narr else ''

    tot_target = energy.get('monthly_target_gwh', 0)
    tot_actual = energy.get('total_actual_gwh', sum(float(d.get('actual_gwh', 0) or 0) for d in days))
    mtd_ach    = energy.get('mtd_achievement_pct', 0)
    ach_col    = _C['success'] if float(mtd_ach or 0) >= 95 else (_C['warning'] if float(mtd_ach or 0) >= 80 else _C['error'])

    # Daily breakdown table rows
    energy_rows = ''
    for d in days:
        tgt  = float(d.get('target_gwh', 0) or 0)
        act  = float(d.get('actual_gwh',  0) or 0)
        var  = act - tgt
        ach  = float(d.get('achievement_pct', 0) or 0)
        var_col = _C['success'] if var >= 0 else _C['error']
        ach_col_row = _C['success'] if ach >= 95 else (_C['warning'] if ach >= 80 else _C['error'])
        var_sym = '+' if var >= 0 else ''
        energy_rows += (
            f'<tr>'
            f'<td style="text-align:center;font-weight:600;">{d.get("day","")}</td>'
            f'<td style="text-align:right;">{tgt:.2f}</td>'
            f'<td style="text-align:right;font-weight:700;">{act:.2f}</td>'
            f'<td style="text-align:right;color:{var_col};font-weight:600;">{var_sym}{var:.2f}</td>'
            f'<td style="text-align:right;color:{ach_col_row};font-weight:700;">{ach:.1f}%</td>'
            f'</tr>'
        )

    content = f"""
        <h1 class="section-title">3. Daily Energy Review</h1>
        {narrative}
        <div class="subsection-title">Daily Target vs Actual (GWh)</div>
        {leg1}
        <div class="chart-wrap">{chart1}</div>
        <div style="display:flex;gap:24px;margin:8px 0 12px;flex-wrap:wrap;">
            <div><span style="font-size:8px;opacity:0.6;">Monthly Target</span>
                 <div style="font-size:16px;font-weight:800;">{_gwh(tot_target)}</div></div>
            <div><span style="font-size:8px;opacity:0.6;">MTD Actual</span>
                 <div style="font-size:16px;font-weight:800;">{_gwh(tot_actual)}</div></div>
            <div><span style="font-size:8px;opacity:0.6;">Achievement</span>
                 <div style="font-size:16px;font-weight:800;color:{ach_col};">{_pct(mtd_ach)}</div></div>
        </div>
        <div class="subsection-title">Daily Energy Consumed (GWh)</div>
        <div class="chart-wrap">{chart2}</div>
        <div class="subsection-title" style="margin-top:10px;">Daily Breakdown</div>
        <table class="mgmt-table" style="width:100%;table-layout:fixed;">
            <thead><tr>
                <th style="text-align:center;width:32px;">Day</th>
                <th style="text-align:right;">Target (GWh)</th>
                <th style="text-align:right;">Actual (GWh)</th>
                <th style="text-align:right;">Variance (GWh)</th>
                <th style="text-align:right;">Achievement</th>
            </tr></thead>
            <tbody>{energy_rows or "<tr><td colspan='5'>No data</td></tr>"}</tbody>
        </table>"""
    return _page(content, context, pg)


# ── 5. Daily Allocation ───────────────────────────────────────────────────────

def render_daily_allocation(data: dict, ai: dict, context: dict, pg: int) -> str:
    alloc   = data.get('allocation', {})
    days    = alloc.get('days', [])
    summary = alloc.get('summary', {})
    narr    = ai.get('allocation_narrative', '')

    tot_exp = float(summary.get('total_expected_mw', 0) or 0)
    tot_act = float(summary.get('total_actual_mw',   0) or 0)
    tot_upk = float(summary.get('total_unpicked_mw', 0) or 0)
    # Positive unpicked = KEDCO under-consumed; negative = KEDCO over-ran allocation
    is_overrun  = tot_upk < 0
    upk_label   = 'Total Overrun' if is_overrun else 'Total Unpicked'
    upk_color   = _C['error'] if is_overrun else _C['success']

    chart = svg_line_allocation(days)
    leg   = _legend([
        ('Expected Allocation (dashed)', _C['primary']),
        ('Actual KEDCO Consumption',     _C['warning']),
        ('Overrun area',                 _C['error']),
    ])
    narrative = f'<p class="narrative">{narr}</p>' if narr else ''

    # Daily allocation table rows — Day | TCN Allocated | KEDCO Actual | Variance
    alloc_rows = ''
    for d in days:
        exp = float(d.get('expected_mw', 0) or 0)
        act = float(d.get('actual_mw',   0) or 0)
        var = float(d.get('unpicked_mw', exp - act) or (exp - act))
        # positive var = underpicked (good/neutral); negative = overrun (bad)
        var_col = _C['error'] if var < 0 else (_C['success'] if var > 0 else _C['grey'])
        var_sym = '+' if var > 0 else ''
        row_bg  = 'background:rgba(220,53,69,0.06);' if var < 0 else ''
        alloc_rows += (
            f'<tr style="{row_bg}">'
            f'<td style="text-align:center;font-weight:600;">{d.get("day","")}</td>'
            f'<td style="text-align:right;">{exp:.1f}</td>'
            f'<td style="text-align:right;font-weight:700;">{act:.1f}</td>'
            f'<td style="text-align:right;color:{var_col};font-weight:600;">{var_sym}{var:.1f}</td>'
            f'</tr>'
        )

    content = f"""
        <h1 class="section-title">4. Daily Real-Time Allocation</h1>
        {narrative}
        {leg}
        <div class="chart-wrap">{chart}</div>
        <div style="display:flex;gap:20px;margin-top:10px;">
            <div><span style="font-size:8px;opacity:0.6;">Total Expected (MW)</span>
                 <div style="font-size:15px;font-weight:800;">{_v(tot_exp, 1)} MW</div></div>
            <div><span style="font-size:8px;opacity:0.6;">Total Consumed (MW)</span>
                 <div style="font-size:15px;font-weight:800;">{_v(tot_act, 1)} MW</div></div>
            <div><span style="font-size:8px;opacity:0.6;">{upk_label}</span>
                 <div style="font-size:15px;font-weight:800;color:{upk_color};">
                    {_v(abs(tot_upk), 1)} MW</div></div>
        </div>
        <p style="font-size:8.5px;color:{_C['grey']};margin-top:6px;">
            {'&#9650; KEDCO consumed more than its TCN allocation — indicates ungated or unmetered load above dispatch level.'
             if is_overrun else
             '&#9660; KEDCO consumed less than allocated — energy left unpicked on the TCN side.'}
        </p>
        <div class="subsection-title" style="margin-top:10px;">Daily Allocation Breakdown</div>
        <table class="mgmt-table" style="width:100%;table-layout:fixed;">
            <thead><tr>
                <th style="text-align:center;width:32px;">Day</th>
                <th style="text-align:right;">TCN Allocated (MW)</th>
                <th style="text-align:right;">KEDCO Actual (MW)</th>
                <th style="text-align:right;">Variance (MW)</th>
            </tr></thead>
            <tbody>{alloc_rows or "<tr><td colspan='4'>No data</td></tr>"}</tbody>
        </table>"""
    return _page(content, context, pg)


# ── 6. Feeder Compliance ──────────────────────────────────────────────────────

def render_feeder_compliance(data: dict, ai: dict, context: dict, pg: int) -> str:
    feeders  = (data.get('compliance_table') or {}).get('feeders', [])
    _raw_segs = (data.get('compliance_segment') or {}).get('segments', [])
    # Flatten nested buckets: {'exceeding': {'count':x,'pct':y}, ...} → {'exceeding': pct, ...}
    segments = []
    for s in _raw_segs:
        flat = {'label': s.get('segment', ''), 'segment': s.get('segment', '')}
        for bk, info in (s.get('buckets') or {}).items():
            flat[bk] = info.get('pct', 0) if isinstance(info, dict) else info
        segments.append(flat)
    narr     = ai.get('compliance_narrative', '')

    narrative = f'<p class="narrative">{narr}</p>' if narr else ''

    # MDI/MDNI feeders only — highest revenue segments for KEDCO
    mdi_feeders = [
        f for f in feeders
        if str(f.get('segment', f.get('district', ''))).upper() in ('MDI', 'MDNI')
    ]
    total_feeder_count = len(feeders)
    mdi_total = len(mdi_feeders)
    mdi_critical = sum(1 for f in mdi_feeders if f.get('bucket', f.get('status','')) in ('critical','poor'))

    # Sort: green (exceeding→on_target) first, then yellow, then red — top 10 only
    _BUCKET_RANK = {'exceeding': 0, 'on_target': 1, 'below_target': 2, 'poor': 3, 'critical': 4}
    mdi_feeders_sorted = sorted(
        mdi_feeders,
        key=lambda f: (
            _BUCKET_RANK.get(f.get('bucket', f.get('status', '')), 5),
            -float(f.get('compliance_pct', f.get('supply_compliance_pct', 0)) or 0),
        )
    )
    display_feeders = mdi_feeders_sorted[:10]

    rows = ''
    for f in display_feeders:
        bucket = f.get('bucket', f.get('status', ''))
        rows += f"""
        <tr>
            <td>{f.get('feeder_name','—')}</td>
            <td>{f.get('segment', f.get('district','—'))}</td>
            <td><strong>{_pct(f.get('compliance_pct', f.get('supply_compliance_pct', 0)))}</strong></td>
            <td>{_v(f.get('avg_daily_hours', f.get('hours_of_supply', 0)), 1)} hrs</td>
            <td>{_bucket_badge(bucket)}</td>
        </tr>"""

    seg_chart = svg_hbar_segments(segments)
    leg = _legend([
        ('Exceeding', '#2E7D32'), ('On Target', '#8BC34A'),
        ('Below Target', '#FBC02D'), ('Poor', '#EF9A9A'), ('Critical', '#E53935'),
    ])

    remaining = mdi_total - 10
    mdi_context_note = (
        f'<p style="font-size:8.5px;color:{_C["grey"]};margin:4px 0 6px;line-height:1.5;">'
        f'MDI and MDNI customers represent KEDCO\'s highest-revenue segments — '
        f'showing top 10 of {mdi_total} MDI/MDNI feeders (of {total_feeder_count} total), best performers first. '
        f'{mdi_critical} are in critical/poor status and require immediate field intervention.</p>'
    ) if mdi_feeders else ''

    content = f"""
        <h1 class="section-title">5. Feeder Compliance Performance</h1>
        {narrative}
        <div class="subsection-title">Compliance by Segment</div>
        {leg}
        <div class="chart-wrap">{seg_chart}</div>
        <div class="subsection-title">MDI / MDNI Feeder Criticality
            <span style="font-size:8px;font-weight:400;color:{_C['grey']};margin-left:8px;">
                Top 10 of {mdi_total} feeders — best performers first</span>
        </div>
        {mdi_context_note}
        <table class="mgmt-table">
            <thead><tr>
                <th>Feeder</th><th>District</th>
                <th>Compliance</th><th>Hours Supply</th><th>Status</th>
            </tr></thead>
            <tbody>{rows or "<tr><td colspan='5'>No MDI/MDNI feeder data available.</td></tr>"}</tbody>
        </table>"""
    return _page(content, context, pg)


# ── 7. Minigrid & Voltage (no AI) ────────────────────────────────────────────

def render_minigrid_voltage(data: dict, ai: dict, context: dict, pg: int) -> str:
    narr         = ai.get('minigrid_voltage_narrative', '')
    narrative    = f'<p class="narrative">{narr}</p>' if narr else ''
    minigrids    = data.get('minigrids', {})
    voltage      = data.get('voltage', {})
    yday_lbl     = context.get('yesterday_label', '')
    date_tag     = (f'<span style="font-size:8px;color:{_C["grey"]};margin-left:8px;">'
                    f'As of {yday_lbl}</span>') if yday_lbl else ''

    # Minigrid feeder table + per-feeder trend chart
    feeder_rows  = ''
    mg_total_mwh = 0.0
    feeder_charts = ''
    mg_feeders = minigrids.get('feeders') or []

    for mg in mg_feeders:
        nm      = mg.get('feeder_name', mg.get('feeder', '—'))
        mwh_val = float(mg.get('total_mwh', 0) or 0)
        mg_total_mwh += mwh_val
        feeder_rows += (f'<tr><td>{nm}</td>'
                        f'<td style="text-align:right;font-weight:700;">{_gwh(mwh_val/1000)}</td></tr>')
        # Per-feeder daily trend if daily data available
        mg_days = mg.get('days') or []
        if mg_days:
            fc = svg_line_chart(
                mg_days,
                [('mwh', _C['primary'], 0.15, False)],
                y_title='MWh', h=110,
                every_day=True,
            )
            feeder_charts += f"""
            <div style="margin-bottom:12px;">
                <div style="font-size:9px;font-weight:700;color:{_C['text']};margin-bottom:4px;">{nm}</div>
                {fc}
            </div>"""

    # Voltage MTD comparison — current month per segment
    month_cmp = voltage.get('month_comparison', {})
    volt_rows = ''
    tot_33, tot_11, tot_all = 0.0, 0.0, 0.0
    for seg_name in ('MDI', 'MDNI', 'Regional'):
        curr = (month_cmp.get(seg_name) or {}).get('current_month', {})
        prev = (month_cmp.get(seg_name) or {}).get('previous_month', {})
        gwh_33  = float(curr.get('energy_33kv_gwh', 0) or 0)
        gwh_11  = float(curr.get('energy_11kv_gwh', 0) or 0)
        gwh_tot = float(curr.get('total_gwh', 0) or 0)
        prev_tot= float(prev.get('total_gwh', 0) or 0)
        tot_33 += gwh_33; tot_11 += gwh_11; tot_all += gwh_tot
        chg     = gwh_tot - prev_tot
        chg_col = _C['success'] if chg >= 0 else _C['error']
        chg_sym = '▲' if chg >= 0 else '▼'
        pct_33  = round(gwh_33 / gwh_tot * 100, 1) if gwh_tot else 0
        pct_11  = round(gwh_11 / gwh_tot * 100, 1) if gwh_tot else 0
        volt_rows += f"""
        <tr>
            <td><strong>{seg_name}</strong></td>
            <td style="text-align:right">{_gwh(gwh_33)}<br/><span style="font-size:8px;opacity:0.5;">{pct_33}%</span></td>
            <td style="text-align:right">{_gwh(gwh_11)}<br/><span style="font-size:8px;opacity:0.5;">{pct_11}%</span></td>
            <td style="text-align:right;font-weight:700;">{_gwh(gwh_tot)}</td>
            <td style="text-align:right;color:{chg_col};">{chg_sym} {_gwh(abs(chg))}</td>
        </tr>"""

    volt_rows += f"""
        <tr style="background:rgba(0,32,80,0.06);">
            <td><strong>Network Total</strong></td>
            <td style="text-align:right;font-weight:700;">{_gwh(tot_33)}</td>
            <td style="text-align:right;font-weight:700;">{_gwh(tot_11)}</td>
            <td style="text-align:right;font-weight:700;">{_gwh(tot_all)}</td>
            <td></td>
        </tr>"""

    content = f"""
        <h1 class="section-title">6. Minigrid &amp; Voltage Analysis</h1>
        {narrative}
        <div class="subsection-title">Energy by Voltage Level — MTD (vs Previous Month) {date_tag}</div>
        <table class="mgmt-table">
            <thead><tr>
                <th>Segment</th>
                <th style="text-align:right">33KV (GWh)</th>
                <th style="text-align:right">11KV (GWh)</th>
                <th style="text-align:right">Total (GWh)</th>
                <th style="text-align:right">vs Prev Month</th>
            </tr></thead>
            <tbody>{volt_rows or "<tr><td colspan='5'>No data</td></tr>"}</tbody>
        </table>
        <div class="subsection-title" style="margin-top:12px;">Minigrid Feeder Energy — MTD {date_tag}
            <span style="font-size:9px;font-weight:400;opacity:0.6;margin-left:8px;">
                Total: {_gwh(mg_total_mwh/1000)}</span>
        </div>
        <table class="mgmt-table" style="margin-bottom:12px;">
            <thead><tr><th>Feeder</th><th style="text-align:right">MTD Energy</th></tr></thead>
            <tbody>{feeder_rows or "<tr><td colspan='2'>No data</td></tr>"}</tbody>
        </table>
        {(f'<div class="subsection-title">Daily Energy Trend per Minigrid Feeder (MWh)</div>{feeder_charts}')
         if feeder_charts else ''}"""
    return _page(content, context, pg)


# ── 8. P&L & Billing Review ──────────────────────────────────────────────────

def render_pnl_review(data: dict, ai: dict, context: dict, pg: int) -> str:
    pear    = data.get('pear', {})
    donut_d = data.get('pnl_donut', {})
    gcr     = data.get('gcr', {})
    vol     = data.get('volatility', {})
    narr    = ai.get('pnl_narrative', '')
    narrative = f'<p class="narrative">{narr}</p>' if narr else ''
    yday_lbl  = context.get('yesterday_label', '')
    date_tag  = (f'<span style="font-size:8px;color:{_C["grey"]};margin-left:8px;">'
                 f'As of {yday_lbl}</span>') if yday_lbl else ''

    # ── PEAR (MD share vs NMD) ────────────────────────────────────────────────
    pear_yday   = pear.get('yesterday', {})
    pear_mtd    = pear.get('mtd', {})
    tgt_mix     = pear.get('target_mix', {})
    tgt_md_pct  = float(tgt_mix.get('md_pct', 65) or 65)
    yday_md_pct = float(pear_yday.get('md_share_pct', 0) or 0)
    mtd_md_pct  = float(pear_mtd.get('md_share_pct',  0) or 0)
    yday_md_col = _C['success'] if yday_md_pct >= tgt_md_pct else _C['error']
    mtd_md_col  = _C['success'] if mtd_md_pct  >= tgt_md_pct else _C['error']

    pear_html = f"""
    <div style="display:flex;gap:20px;margin-bottom:10px;flex-wrap:wrap;">
        <div><span style="font-size:8px;opacity:0.6;">MD Share — Yesterday
                 <span style="color:{_C['grey']};"> ({yday_lbl})</span></span>
             <div style="font-size:18px;font-weight:800;color:{yday_md_col};">{_pct(yday_md_pct)}</div>
             <div style="font-size:8px;opacity:0.5;">NMD: {_pct(pear_yday.get('nmd_share_pct',0))}</div></div>
        <div><span style="font-size:8px;opacity:0.6;">MD Share — MTD</span>
             <div style="font-size:18px;font-weight:800;color:{mtd_md_col};">{_pct(mtd_md_pct)}</div>
             <div style="font-size:8px;opacity:0.5;">NMD: {_pct(pear_mtd.get('nmd_share_pct',0))}</div></div>
        <div><span style="font-size:8px;opacity:0.6;">Target MD Share</span>
             <div style="font-size:18px;font-weight:800;">{_pct(tgt_md_pct)}</div></div>
        <div><span style="font-size:8px;opacity:0.6;">MD Gap vs Target</span>
             <div style="font-size:18px;font-weight:800;color:{_C['error'] if mtd_md_pct < tgt_md_pct else _C['success']};">
                 {_pct(abs(mtd_md_pct - tgt_md_pct))} {'below' if mtd_md_pct < tgt_md_pct else 'above'}</div></div>
    </div>"""

    # ── Segment mix — yesterday (table) ──────────────────────────────────────
    yday_donut = donut_d.get('yesterday', {})
    SEG_COLORS = {'MDI': _C['primary'], 'MDNI': _C['warning'], 'Regions': _C['gap_green']}
    mix_rows = ''
    mix_total = 0.0
    mix_segs  = yday_donut.get('segments') or []
    for seg in mix_segs:
        val = float(seg.get('energy_gwh', seg.get('gwh', 0)) or 0)
        mix_total += val
    for seg in mix_segs:
        sn  = seg.get('segment', '—')
        val = float(seg.get('energy_gwh', seg.get('gwh', 0)) or 0)
        pct = (val / mix_total * 100) if mix_total else 0
        col = SEG_COLORS.get(sn, _C['grey'])
        mix_rows += f"""
        <tr>
            <td><span style="display:inline-block;width:10px;height:10px;border-radius:2px;
                             background:{col};margin-right:6px;vertical-align:middle;"></span>
                <strong>{sn}</strong></td>
            <td style="text-align:right;">{_gwh(val)}</td>
            <td style="text-align:right;font-weight:700;color:{col};">{_pct(pct)}</td>
        </tr>"""

    # ── GCR full table (target, consumed, gap, billing) ──────────────────────
    gcr_rows = ''
    for seg in (gcr.get('segments') or gcr.get('rows') or []):
        sn = seg.get('segment', '—')
        fw = 'font-weight:700;' if sn == 'Total' else ''
        bg = 'background:rgba(0,32,80,0.06);' if sn == 'Total' else ''
        gcr_rows += f"""
        <tr style="{bg}">
            <td style="{fw}">{sn}</td>
            <td style="text-align:right;{fw}">{_gwh(seg.get('target_gwh', 0))}</td>
            <td style="text-align:right;{fw}">{_gwh(seg.get('consumed_gwh', 0))}</td>
            <td style="text-align:right;color:{_C['error']};{fw}">{_gwh(seg.get('gap_gwh', 0))}</td>
            <td style="text-align:right;{fw}">{_naira(seg.get('expected_bill_value', 0))}</td>
            <td style="text-align:right;{fw}">{_naira(seg.get('mtd_bill_value', 0))}</td>
            <td style="text-align:right;color:{_C['error']};{fw}">{_naira(seg.get('gap_bill_value', 0))}</td>
            <td style="text-align:right;{fw}">{_pct(seg.get('mtd_achievement_pct', 0))}</td>
        </tr>"""

    # ── Volatility — P&L Mix (yesterday vs MTD share) ────────────────────────
    vol_rows = ''
    for seg in (vol.get('segments') or []):
        diff = float(seg.get('difference_pct', 0) or 0)
        diff_col = _C['success'] if abs(diff) <= 1 else (_C['error'] if diff < -2 else _C['warning'])
        remark = seg.get('remark', '')
        vol_rows += f"""
        <tr>
            <td>{seg.get('segment','—')}</td>
            <td style="text-align:right">{_pct(seg.get('yesterday_share_pct', 0))}</td>
            <td style="text-align:right">{_pct(seg.get('mtd_share_pct', 0))}</td>
            <td style="text-align:right;color:{diff_col};font-weight:700;">
                {'▲' if diff > 0 else '▼'} {_pct(abs(diff))}</td>
            <td>{remark}</td>
        </tr>"""

    content = f"""
        <h1 class="section-title">7. P&amp;L &amp; Billing Review</h1>
        {narrative}

        <div class="subsection-title">PEAR — MD vs NMD Energy Mix {date_tag}</div>
        {pear_html}

        <div class="two-col">
            <div class="two-col-half">
                <div class="subsection-title">Segment Mix — Yesterday {date_tag}</div>
                {(f'''<table class="mgmt-table">
                    <thead><tr>
                        <th>Segment</th>
                        <th style="text-align:right">Energy (GWh)</th>
                        <th style="text-align:right">Share %</th>
                    </tr></thead>
                    <tbody>{mix_rows}</tbody>
                    <tfoot><tr style="background:rgba(0,32,80,0.06);">
                        <td><strong>Total</strong></td>
                        <td style="text-align:right;font-weight:700;">{_gwh(mix_total)}</td>
                        <td style="text-align:right;font-weight:700;">100%</td>
                    </tr></tfoot>
                </table>''' if mix_rows else '<p style="font-size:9px;color:#999;">No data.</p>')}
            </div>
            <div class="two-col-half">
                <div class="subsection-title">P&amp;L Mix Volatility (Yesterday vs MTD) {date_tag}</div>
                <table class="mgmt-table">
                    <thead><tr>
                        <th>Segment</th>
                        <th style="text-align:right">Yesterday %</th>
                        <th style="text-align:right">MTD Avg %</th>
                        <th style="text-align:right">Diff</th>
                        <th>Signal</th>
                    </tr></thead>
                    <tbody>{vol_rows or "<tr><td colspan='5'>No data</td></tr>"}</tbody>
                </table>
            </div>
        </div>

        <div class="subsection-title" style="margin-top:12px;">GCR — Energy &amp; Revenue Gap by Segment (MTD) {date_tag}</div>
        <table class="mgmt-table">
            <thead><tr>
                <th>Segment</th>
                <th style="text-align:right">Target (GWh)</th>
                <th style="text-align:right">Consumed (GWh)</th>
                <th style="text-align:right">Gap (GWh)</th>
                <th style="text-align:right">Expected Billing</th>
                <th style="text-align:right">MTD Billing</th>
                <th style="text-align:right">Billing Gap</th>
                <th style="text-align:right">Achievement</th>
            </tr></thead>
            <tbody>{gcr_rows or "<tr><td colspan='8'>No data</td></tr>"}</tbody>
        </table>"""
    return _page(content, context, pg)


# ── 9. Priority Issues ────────────────────────────────────────────────────────

def _is_load_shedding(inc: dict) -> bool:
    fault = str(inc.get('nature_of_fault', '') or '').lower()
    return 'load shed' in fault or 'l/s' in fault or fault.startswith('ls ')


def _is_permit(inc: dict) -> bool:
    fault = str(inc.get('nature_of_fault', '') or '').lower()
    return 'permit' in fault


def render_priority_issues(data: dict, ai: dict, context: dict, pg: int) -> str:
    # ── Actual incidents — exclude Load Shedding (systemic grid event, not KEDCO fault) ──
    inc_data     = data.get('incidents', {})
    all_incidents = inc_data.get('incidents', [])
    yday_lbl     = context.get('yesterday_label', '')
    date_tag     = (f'<span style="font-size:8px;color:{_C["grey"]};margin-left:6px;">'
                    f'MTD as of {yday_lbl}</span>') if yday_lbl else ''

    # Split into L/S, Permit, and real faults
    ls_incidents     = [i for i in all_incidents if _is_load_shedding(i)]
    permit_incidents = [i for i in all_incidents if not _is_load_shedding(i) and _is_permit(i)]
    real_incidents   = [i for i in all_incidents if not _is_load_shedding(i) and not _is_permit(i)]

    # Recalculate summary from real faults only
    total_inc  = len(real_incidents)
    rectified  = sum(1 for i in real_incidents if 'rectif' in str(i.get('status','')).lower())
    lingering  = total_inc - rectified
    fin_loss   = sum(float(i.get('financial_loss_ngn', 0) or 0) for i in real_incidents)
    ls_count   = len(ls_incidents)
    ls_loss    = sum(float(i.get('financial_loss_ngn', 0) or 0) for i in ls_incidents)
    permit_count = len(permit_incidents)

    # Summary strip
    inc_strip = f"""
        <div style="display:flex;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
            <div class="callout" style="flex:1;min-width:100px;text-align:center;">
                <div class="callout-title">Equipment Faults {date_tag}</div>
                <div class="callout-text" style="font-size:24px;font-weight:800;
                     color:{_C['error']};">{total_inc}</div>
            </div>
            <div class="callout" style="flex:1;min-width:100px;text-align:center;">
                <div class="callout-title">Rectified</div>
                <div class="callout-text" style="font-size:24px;font-weight:800;
                     color:{_C['success']};">{rectified}</div>
            </div>
            <div class="callout" style="flex:1;min-width:100px;text-align:center;">
                <div class="callout-title">Lingering</div>
                <div class="callout-text" style="font-size:24px;font-weight:800;
                     color:{_C['warning']};">{lingering}</div>
            </div>
            <div class="callout" style="flex:2;min-width:140px;text-align:center;">
                <div class="callout-title">Est. Financial Loss (Faults)</div>
                <div class="callout-text" style="font-size:16px;font-weight:700;
                     color:{_C['error']};">{_naira(fin_loss)}</div>
            </div>
        </div>
        <p style="font-size:8px;color:{_C['grey']};margin:0 0 8px;padding:4px 8px;
                  background:rgba(0,32,80,0.04);border-radius:4px;">
            &#9432; Excluded from fault log: {ls_count:,} Load Shedding (L/S) events
            (est. {_naira(ls_loss)} loss — grid-side, not KEDCO-attributable)
            {f'and {permit_count:,} Permit/planned-maintenance events' if permit_count else ''}.
            Figures above reflect unplanned equipment faults only.
        </p>"""

    # Incidents table — real faults only, top 10 by financial loss
    sorted_incidents = sorted(real_incidents, key=lambda i: float(i.get('financial_loss_ngn', 0) or 0), reverse=True)
    top10 = sorted_incidents[:10]
    remaining = len(real_incidents) - len(top10)

    inc_rows = ''
    for inc in top10:
        feeder   = inc.get('feeder_name', '—')
        fault    = inc.get('nature_of_fault', '—')
        dur      = inc.get('duration_hours', '')
        loss     = float(inc.get('financial_loss_ngn', 0) or 0)
        status   = inc.get('status', '—')
        s_color  = _C['success'] if 'rectif' in status.lower() else _C['warning']
        inc_rows += f"""
        <tr>
            <td><strong>{feeder}</strong></td>
            <td style="line-height:1.4;">{fault}</td>
            <td style="text-align:center;">{dur}</td>
            <td style="text-align:right;">₦{loss:,.0f}</td>
            <td style="text-align:center;">
                <span style="background:{s_color};color:#fff;padding:2px 8px;
                             border-radius:10px;font-size:9px;font-weight:700;
                             text-transform:uppercase;white-space:nowrap;">
                    {status}
                </span>
            </td>
        </tr>"""

    more_note = (f'<p style="font-size:8px;color:{_C["grey"]};margin:-4px 0 8px;">'
                 f'Showing top 10 by financial loss — {remaining:,} additional incidents not shown.</p>'
                 if remaining > 0 else '')

    inc_table = f"""
        <h2 class="subsection-title" style="margin-top:0;">Active Fault Log — Top 10 by Financial Impact</h2>
        <table class="mgmt-table" style="margin-bottom:6px;">
            <thead><tr>
                <th style="width:22%">Feeder</th>
                <th style="width:32%">Nature of Fault</th>
                <th style="width:10%;text-align:center;">Duration (hrs)</th>
                <th style="width:18%;text-align:right;">Financial Loss</th>
                <th style="width:18%;text-align:center;">Status</th>
            </tr></thead>
            <tbody>{inc_rows or "<tr><td colspan='5' style='text-align:center;'>No incidents recorded for this period.</td></tr>"}</tbody>
        </table>
        {more_note}"""

    # ── ARIA priority issues ──────────────────────────────────────────────────
    issues = ai.get('priority_issues', [])
    aria_rows = ''
    for iss in issues:
        aria_rows += f"""
        <tr>
            <td><strong>{iss.get('issue','—')}</strong></td>
            <td style="line-height:1.4;">{iss.get('evidence','')}</td>
            <td>{iss.get('risk','')}</td>
            <td style="line-height:1.4;">{iss.get('response','')}</td>
        </tr>"""

    aria_table = f"""
        <h2 class="subsection-title">ARIA Priority Analysis</h2>
        <table class="mgmt-table">
            <thead><tr>
                <th style="width:20%">Issue</th>
                <th style="width:35%">Evidence</th>
                <th style="width:12%">Risk</th>
                <th style="width:33%">Required Response</th>
            </tr></thead>
            <tbody>{aria_rows or "<tr><td colspan='4'>No priority issues identified.</td></tr>"}</tbody>
        </table>"""

    content = f"""
        <h1 class="section-title">8. Priority Issues &amp; Incidents</h1>
        {inc_strip}
        {inc_table}
        {aria_table}"""
    return _page(content, context, pg)


# ── 10. Action Plan ───────────────────────────────────────────────────────────

def render_action_plan(ai: dict, context: dict, pg: int) -> str:
    actions = ai.get('action_plan', [])
    rows = ''
    for a in actions:
        rows += f"""
        <tr>
            <td><strong>{a.get('area','—')}</strong></td>
            <td style="line-height:1.4;">{a.get('action','')}</td>
            <td>{a.get('team','')}</td>
            <td>{a.get('timeline','')}</td>
        </tr>"""

    content = f"""
        <h1 class="section-title">9. ARIA Action Plan</h1>
        <table class="mgmt-table">
            <thead><tr>
                <th style="width:18%">Area</th>
                <th style="width:45%">Recommended Action</th>
                <th style="width:18%">Team</th>
                <th style="width:19%">Timeline</th>
            </tr></thead>
            <tbody>{rows or "<tr><td colspan='4'>No actions generated.</td></tr>"}</tbody>
        </table>"""
    return _page(content, context, pg)


# ── 11. Back Page ─────────────────────────────────────────────────────────────

def render_back_page(context: dict) -> str:
    company     = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    period      = context.get('period_label', '')
    footer_logo = context.get('footer_logo_url', '')
    return f"""
    <div class="cover-page" style="justify-content:center;align-items:center;text-align:center;">
        <div class="cover-accent"></div>
        <div class="cover-body" style="justify-content:center;align-items:center;">
            <div style="margin-bottom:32px;">
                <div class="cover-eyebrow" style="text-align:center;margin-bottom:24px;">End of Report</div>
                <h2 style="font-size:28px;font-weight:800;color:#fff;text-transform:uppercase;
                            letter-spacing:-0.5px;line-height:1.1;">{company}</h2>
                <div class="cover-rule" style="margin:24px auto;"></div>
                <div style="font-size:11px;color:#fff;opacity:0.5;letter-spacing:1.5px;
                             text-transform:uppercase;font-weight:600;">{period}</div>
            </div>
            <div style="font-size:10px;color:#fff;opacity:0.3;text-transform:uppercase;
                         letter-spacing:2px;margin-top:60px;">
                Powered by RAVEN &mdash; Performance Monitoring Tool
            </div>
        </div>
    </div>"""

# =============================================================================
# SECTION REGISTRY — maps toggle keys → renderer functions
# =============================================================================

ALL_SECTIONS = [
    'tmo_executive_summary',
    'tmo_kpi_dashboard',
    'tmo_daily_energy',
    'tmo_daily_allocation',
    'tmo_feeder_compliance',
    'tmo_minigrid_voltage',
    'tmo_pnl_review',
    'tmo_priority_issues',
    'tmo_action_plan',
]

# =============================================================================
# MAIN GENERATOR CLASS
# =============================================================================

class TMOManagementPDFGenerator:
    """
    Usage:
        generator = TMOManagementPDFGenerator(report_config, tmo_service)
        html = generator.generate_html()
        pdf  = generator.generate_pdf()

    report_config keys:
        year          int  — report month year  (default: current year)
        month         int  — report month 1-12  (default: current month)
        company_name  str
        report_title  str
        include_ai    bool (default True)
        sections      list[str] — subset of ALL_SECTIONS; omit for all
    """

    def __init__(self, report_config: dict, data_service, user=None):
        import base64 as _b64
        from django.conf import settings

        self.cfg        = report_config
        self.svc        = data_service
        self.include_ai = report_config.get('include_ai', True)

        # Which sections to render (default = all)
        requested = report_config.get('sections') or ALL_SECTIONS
        self.sections = [s for s in ALL_SECTIONS if s in requested]

        # Period
        today = date.today()
        yr  = int(report_config.get('year',  today.year))
        mo  = int(report_config.get('month', today.month))
        self.from_date, self.to_date, self.is_current, self.yesterday = _compute_period(yr, mo)
        self.period_label = _period_label(self.from_date, self.to_date, self.is_current)

        # Build context
        company = report_config.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
        title   = report_config.get('report_title',
                                    f"{self.from_date.strftime('%B %Y')} TMO Management Report")
        self.context = {
            'company_name':    company,
            'report_title':    title,
            'period_label':    self.period_label,
            'footer_logo_url': self._static_b64('reports/images/footer_logo.png'),
            'yesterday':       self.yesterday,
            'yesterday_label': self.yesterday.strftime('%d %b %Y'),
            'is_current':      self.is_current,
        }

    def _static_b64(self, rel_path: str) -> str:
        import os
        from django.conf import settings
        for d in getattr(settings, 'STATICFILES_DIRS', []):
            p = os.path.join(d, rel_path)
            if os.path.isfile(p):
                ext  = os.path.splitext(p)[1].lower().lstrip('.')
                mime = {'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg',
                        'svg':'image/svg+xml'}.get(ext,'image/png')
                with open(p, 'rb') as f:
                    return f'data:{mime};base64,{base64.b64encode(f.read()).decode()}'
        base = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        url  = getattr(settings, 'STATIC_URL', '/static/')
        return f"{base.rstrip('/')}{url}{rel_path}"

    def _gather(self) -> dict:
        """Fetch all data from TMO service, returning a unified dict."""
        svc = self.svc

        # Sync the service's date range to the generator's computed period
        try:
            svc.from_date = self.from_date
            svc.to_date   = self.to_date
        except Exception:
            pass

        def _safe(fn, *args, **kwargs):
            try:    return fn(*args, **kwargs)
            except Exception as e:
                logger.warning("TMO data fetch failed (%s): %s", fn.__name__ if hasattr(fn,'__name__') else fn, e)
                return {}

        return {
            'overview':           _safe(svc.section_tmo_overview),
            'daily_energy':       _safe(svc.section_tmo_daily_network_energy),
            'allocation':         _safe(svc.section_tmo_daily_allocation),
            'compliance_table':   _safe(svc.section_tmo_feeder_compliance_table),
            'compliance_segment': _safe(svc.section_tmo_compliance_by_segment),
            'minigrids':          _safe(svc.section_tmo_minigrids_daily, {}),
            'voltage':            _safe(svc.section_tmo_energy_by_voltage),
            'pear':               _safe(svc.section_tmo_pear),
            'pnl_donut':          _safe(svc.section_tmo_energy_pnl_donut),
            'pnl_deficit':        _safe(svc.section_tmo_pnl_deficit),
            'gcr':                _safe(svc.section_tmo_gcr),
            'volatility':         _safe(svc.section_tmo_volatility),
            'incidents':          _safe(svc.section_tmo_incidents),
        }

    def generate_html(self) -> str:
        all_data = self._gather()

        # ARIA narrative
        if self.include_ai:
            try:
                prompt = _build_aria_prompt(all_data, self.period_label,
                                            self.context['company_name'])
                ai = _call_aria(prompt)
            except Exception as e:
                logger.warning("ARIA call failed: %s", e)
                ai = _fallback_aria()
        else:
            ai = _fallback_aria()

        ctx = self.context
        sec = set(self.sections)
        pg  = 2  # page 1 = cover

        pages = render_cover(ctx)
        pages += render_toc(ctx, self.sections)

        if 'tmo_executive_summary' in sec:
            pages += render_executive_summary(all_data, ai, ctx, pg); pg += 1
        if 'tmo_kpi_dashboard' in sec:
            pages += render_kpi_dashboard(all_data, ai, ctx, pg); pg += 1
        if 'tmo_daily_energy' in sec:
            pages += render_daily_energy(all_data, ai, ctx, pg); pg += 1
        if 'tmo_daily_allocation' in sec:
            pages += render_daily_allocation(all_data, ai, ctx, pg); pg += 1
        if 'tmo_feeder_compliance' in sec:
            pages += render_feeder_compliance(all_data, ai, ctx, pg); pg += 1
        if 'tmo_minigrid_voltage' in sec:
            pages += render_minigrid_voltage(all_data, ai, ctx, pg); pg += 1
        if 'tmo_pnl_review' in sec:
            pages += render_pnl_review(all_data, ai, ctx, pg); pg += 1
        if 'tmo_priority_issues' in sec:
            pages += render_priority_issues(all_data, ai, ctx, pg); pg += 1
        if 'tmo_action_plan' in sec:
            pages += render_action_plan(ai, ctx, pg); pg += 1

        pages += render_back_page(ctx)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{ctx['report_title']}</title>
    <style>
        {MANAGEMENT_STYLES}
        {TMO_EXTRA}
    </style>
</head>
<body>
    {pages}
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
                        format='A4', landscape=False,
                        print_background=True,
                        margin={'top':'0','bottom':'0','left':'0','right':'0'},
                    )
                    browser.close()
                buf = io.BytesIO(pdf_bytes)
                buf.seek(0)
                return buf
            except Exception as e:
                logger.warning("Playwright PDF failed: %s", e)

        if _WEASYPRINT_AVAILABLE:
            buf = io.BytesIO()
            _WeasyHTML(string=html).write_pdf(buf, font_config=_FontConfig())
            buf.seek(0)
            return buf

        raise RuntimeError("No PDF engine available (install playwright or weasyprint).")

    def generate_pdf_base64(self) -> str:
        return base64.b64encode(self.generate_pdf().read()).decode()
