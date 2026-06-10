# reports/pdf_generator.py
"""
PDF generation service.
Primary engine: Playwright (headless Chromium) — produces output identical to
the browser live preview.
Fallback engine: WeasyPrint — used only if Playwright is unavailable.
"""
from django.conf import settings

# ── Playwright (preferred) ─────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False
    print("INFO: Playwright not installed. Install with: pip install playwright && playwright install chromium")

# ── WeasyPrint (fallback) ──────────────────────────────────────────────────
try:
    from weasyprint import HTML as WeasyHTML
    from weasyprint.text.fonts import FontConfiguration
    WEASYPRINT_AVAILABLE = True
except (OSError, ImportError) as e:
    WeasyHTML = None
    FontConfiguration = None
    WEASYPRINT_AVAILABLE = False
    print(f"WARNING: WeasyPrint could not be imported. Error: {e}")

import base64
import io
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# SECTION DISPLAY NAMES (used for Table of Contents)
# =============================================================================

SECTION_DISPLAY_NAMES = {
    'cover_page': 'Cover Page',
    'table_of_contents': 'Table of Contents',
    'infrastructure_overview': 'Infrastructure Overview',
    'technical_metrics': 'Technical Metrics',
    'system_reliability': 'System Reliability',
    'interruption_breakdown': 'Interruption Breakdown',
    'hours_of_supply_chart': 'Hours of Supply Trend',
    'load_trend_chart': 'Load Trend',
    'energy_delivered_chart': 'Energy Delivered Trend',
    'feeder_performance_table': 'Feeder Performance',
    'state_performance_table': 'State Performance',
    'district_performance_table': 'District Performance',
    'service_band_summary': 'Service Band Summary',
    'custom_text': 'Notes',
    'gaps_improvements': 'Gaps & Improvement Areas',
    # HR
    'hr_overview':          'HR Overview',
    'staff_metrics':        'Staff Metrics',
    'wage_bill_analysis':   'Wage Bill Analysis',
    'department_headcount': 'Department Headcount',
    'attrition_analysis':   'Attrition Analysis',
    'recruitment_summary':  'Recruitment Summary',
    # Commercial
    'commercial_overview':   'Commercial Overview',
    'commercial_coverage':   'Reading Coverage',
    'commercial_energy':     'Energy Analysis',
    'revenue_by_district':   'Revenue by District',
    'revenue_by_feeder':     'Revenue by Feeder',
    'customer_type_summary': 'Customer Type Summary',
    # Commercial Comparison
    'commercial_comparison_summary':  'Comparison Summary',
    'commercial_comparison_table':    'Customer Comparison Detail',
    'commercial_comparison_insights': 'ARIA Insights',
    # Financial
    'financial_overview': 'Financial Overview',
    'opex_by_category':   'OPEX by Category',
    'opex_by_district':   'OPEX by District',
    # DSO Compliance
    'dso_compliance_overview': 'DSO Compliance Overview',
    'dso_compliance_table':         'DSO Compliance by Station',
    'segment_compliance_summary':   'Compliance by Business Segment',
    'feeder_segment_compliance':    'Feeder Compliance by Segment',
    'energy_by_segment_pl':         'Energy by P&L Segment',
    'segment_voltage_energy':       'Energy by Segment & Voltage',
    'energy_md_nmd_mix':            'MD vs NMD Energy Mix',
    'segment_compliance_trend':     'Segment Compliance Trend',
}


# =============================================================================
# PDF STYLES
# =============================================================================

BASE_STYLES = """
@page {
    margin: 0;
}

@page :first {
    margin: 0;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    background-color: #ffffff;
    color: #002050;
    font-size: 12px;
    line-height: 1.4;
}

/* ── Page layout ─────────────────────────────────────────────────────────── */
/* IMPORTANT: height is fixed at exactly A4 (297 mm portrait / 210 mm landscape)
   so every .page div maps to precisely one printed page.  The flex column
   layout pushes .footer to the physical bottom of the page regardless of how
   much body content there is.  overflow:hidden ensures content that is taller
   than the page body area is clipped rather than expanding the page (the
   _paginate_table helper already splits long tables into multiple pages).    */

.page {
    width: 297mm;
    height: 210mm;
    padding: 18px 35px;
    page-break-after: always;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    overflow: hidden;
}

.page:last-child {
    page-break-after: avoid;
}

.page-landscape {
    width: 297mm;
    height: 210mm;
    padding: 18px 30px;
}

/* ── Header ──────────────────────────────────────────────────────────────── */
.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
}

.company-name {
    font-size: 14px;
    font-weight: 600;
}

.date {
    font-size: 14px;
    font-weight: 600;
}

/* ── Page Title ──────────────────────────────────────────────────────────── */
.page-title {
    font-size: 32px;
    font-weight: 400;
    margin-bottom: 20px;
}

/* ── Page content wrapper ────────────────────────────────────────────────── */
/* flex:1 grows to fill available space between page top and the footer.
   overflow:hidden prevents body content from pushing the footer off the page. */
.page-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* ── Footer ──────────────────────────────────────────────────────────────── */
/* flex-shrink:0 prevents the footer from being compressed when the page is
   content-heavy.  page-break-inside/before:avoid keeps logo and page-number
   together on the same PDF page. */

.footer {
    flex-shrink: 0;
    padding-top: 15px;
    padding-bottom: 10px;
    display: flex;
    flex-direction: row;
    justify-content: space-between;
    align-items: center;
    border-top: 1px solid rgba(0, 32, 80, 0.2);
    page-break-inside: avoid;
    page-break-before: avoid;
}

.footer img {
    max-height: 44px;
    width: auto;
    flex-shrink: 0;
    display: block;
}

.page-number {
    font-size: 24px;
    font-weight: 700;
    flex-shrink: 0;
    white-space: nowrap;
}

/* inline footer is always used — position:fixed + counter(page) is unreliable
   in WeasyPrint; Python-supplied page numbers via inline footers are used instead */

/* ── Stats Container ─────────────────────────────────────────────────────── */
/* gap: shorthand is WeasyPrint <54 unsafe — use margin-right on children     */
.stats-container {
    display: flex;
    margin-bottom: 25px;
    flex-wrap: wrap;
}

.stat-item {
    margin-right: 40px;
    margin-bottom: 10px;
}

.stat-item h3 {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 6px;
    opacity: 0.85;
}

.stat-item .value {
    font-size: 34px;
    font-weight: 700;
    color: #002050;
}

/* ── Content Box ─────────────────────────────────────────────────────────── */
.content-box {
    border: 2px solid #e0e0e0;
    border-radius: 20px;
    overflow: hidden;
    display: flex;
    margin-bottom: 25px;
}

.summary-section {
    background-color: #f0f4f8;
    padding: 25px;
    flex: 0 0 260px;
    border-right: 2px solid #e0e0e0;
}

.summary-section h2 {
    font-size: 16px;
    font-weight: 700;
    margin-bottom: 15px;
    letter-spacing: 1px;
}

.summary-section ul {
    list-style: none;
}

.summary-section li {
    font-size: 12px;
    font-weight: 400;
    line-height: 1.5;
    margin-bottom: 8px;
    padding-left: 18px;
    position: relative;
}

.summary-section li::before {
    content: "•";
    position: absolute;
    left: 0;
    font-size: 16px;
}

/* ── Tables ──────────────────────────────────────────────────────────────── */
.table-section {
    flex: 1;
    padding: 0;
    overflow: hidden;
}

table {
    width: 100%;
    border-collapse: collapse;
}

thead {
    background-color: #002050;
}

thead th {
    padding: 10px 14px;
    text-align: left;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    white-space: nowrap;
    color: #ffffff;
}

tbody tr {
    background-color: transparent;
}

/* Removed Status Pills */


tbody tr:nth-child(even) {
    background-color: rgba(0, 32, 80, 0.04);
}

tbody td {
    padding: 9px 14px;
    font-size: 12px;
    font-weight: 500;
    vertical-align: middle;
    white-space: nowrap;
}

.table-container {
    width: 100%;
    border-radius: 15px;
    overflow: hidden;
    margin-bottom: 20px;
}

.table-container thead {
    background-color: #002050;
}


.table-container tbody tr {
    background-color: #ffffff;
}

.table-container tbody tr:not(:last-child) {
    border-bottom: 1px solid rgba(0, 0, 0, 0.08);
}

.table-container tbody td {
    padding: 5px 8px;
    font-size: 10.5px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Prevent rows from breaking across pages */
.table-container tr {
    page-break-inside: avoid;
}

/* Prevent table itself from overflowing into footer */
.table-container {
    overflow: hidden;
}

/* ── Two-column trend layout ──────────────────────────────────────────────── */
/* Pure-percentage widths — WeasyPrint cannot mix % and px inside calc()      */
.trend-columns {
    display: -webkit-flex;
    display: flex;
}

.trend-column {
    width: 49%;
    margin: 0 0.5%;
}

/* ── Metric Cards ─────────────────────────────────────────────────────────── */
/* Pure-percentage widths for WeasyPrint compatibility                         */
.metrics-grid {
    display: -webkit-flex;
    display: flex;
    flex-wrap: wrap;
    margin-bottom: 9px;
}

.metric-card {
    width: 32%;
    margin: 0 0.5% 1% 0.5%;
    border-radius: 15px;
    padding: 18px 22px;
    display: flex;
    align-items: center;
}

.metric-card.primary {
    background-color: #f0f4f8;
}

.metric-card.secondary {
    background-color: #e8edf5;
}

.metric-label {
    flex: 0 0 175px;
    padding-right: 18px;
}

.metric-label h2 {
    font-size: 17px;
    font-weight: 700;
    line-height: 1.2;
    text-transform: uppercase;
}

.metric-content {
    flex: 1;
    border-left: 2px solid rgba(0, 32, 80, 0.2);
    padding-left: 18px;
}

.metric-value {
    font-size: 24px;
    font-weight: 700;
    color: #002050;
    margin-bottom: 4px;
    line-height: 1.1;
    word-break: break-word;
    overflow-wrap: break-word;
}

.metric-subtitle {
    font-size: 11px;
    font-weight: 700;
    margin-bottom: 3px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    opacity: 0.8;
}

.metric-description {
    font-size: 10px;
    font-weight: 400;
    line-height: 1.4;
    opacity: 0.85;
}

/* ── Reliability Card ─────────────────────────────────────────────────────── */
/* gap: removed — WeasyPrint <54 unsafe; spacing done via margin-right         */
.reliability-card {
    background-color: #f0f4f8;
    border-radius: 15px;
    padding: 25px 30px;
    display: flex;
    align-items: center;
    margin-bottom: 25px;
}

.reliability-label {
    flex: 0 0 auto;
    margin-right: 30px;
}

.reliability-label h2 {
    font-size: 26px;
    font-weight: 700;
    line-height: 1.2;
    text-transform: uppercase;
    color: #002050;
}

.reliability-metrics {
    flex: 1;
    display: flex;
    gap: 0;
    padding-left: 30px;
    border-left: 2px solid rgba(0, 32, 80, 0.2);
}

.reliability-item {
    flex: 1;
    text-align: center;
    padding: 0 20px;
}

.reliability-item:not(:last-child) {
    border-right: 2px solid rgba(0, 32, 80, 0.15);
}

.reliability-value {
    font-size: 34px;
    font-weight: 700;
    line-height: 1;
    margin-bottom: 8px;
    color: #002050;
}

.reliability-description {
    font-size: 12px;
    font-weight: 500;
    line-height: 1.3;
    opacity: 0.9;
}

/* ── Reliability KPI grid ─────────────────────────────────────────────────── */
.reliability-kpi-grid {
    display: -webkit-flex;
    display: flex;
    flex-wrap: wrap;
    margin-bottom: 25px;
}

.reliability-kpi-card {
    width: 32%;
    margin: 0 0.5% 1% 0.5%;
    background-color: #f0f4f8;
    border-radius: 15px;
    padding: 30px 24px;
    text-align: center;
    box-sizing: border-box;
}

.reliability-kpi-value {
    font-size: 42px;
    font-weight: 700;
    color: #002050;
    line-height: 1;
    margin-bottom: 10px;
}

.reliability-kpi-label {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    opacity: 0.85;
    line-height: 1.4;
}

.reliability-highlight {
    background-color: #e8edf5;
    border-radius: 15px;
    padding: 25px 30px;
    margin-bottom: 20px;
    text-align: center;
}

.reliability-highlight h2 {
    font-size: 20px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #002050;
    margin-bottom: 6px;
}

.reliability-highlight p {
    font-size: 12px;
    font-weight: 400;
    opacity: 0.75;
    line-height: 1.5;
}

/* ── Section Title ────────────────────────────────────────────────────────── */
.section-title {
    font-size: 24px;
    font-weight: 400;
    margin-bottom: 15px;
}

/* ── Gaps/Improvements Grid ───────────────────────────────────────────────── */
/* Pure-percentage 3-column flex — WeasyPrint-safe                            */
.sections-grid {
    display: -webkit-flex;
    display: flex;
    flex-wrap: wrap;
    margin-bottom: 20px;
}

.section-card {
    width: 32%;
    margin: 0 0.5% 1% 0.5%;
    background-color: #ececec;
    border-radius: 15px;
    padding: 20px;
    min-height: 160px;
    box-sizing: border-box;
}

.section-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 12px;
}

.section-card-title {
    font-size: 15px;
    font-weight: 700;
    color: #002050;
    line-height: 1.2;
    flex: 1;
}

.section-icon {
    width: 28px;
    height: 28px;
    background-color: #002050;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #ffffff;
    font-weight: 700;
    font-size: 14px;
    flex-shrink: 0;
    margin-left: 10px;
}

.section-content {
    color: #000000;
    font-size: 11px;
    line-height: 1.5;
    font-weight: 400;
}

.section-content ul {
    list-style: none;
    padding: 0;
}

.section-content li {
    margin-bottom: 6px;
    padding-left: 12px;
    position: relative;
}

.section-content li::before {
    content: "•";
    position: absolute;
    left: 0;
    font-weight: 700;
}

/* ── Cover Page (Modern) ──────────────────────────────────────────────────── */
/* Layout: thin white accent bar (left) + full content column (right)         */
.cover-page {
    padding: 0;
    display: -webkit-flex;
    display: flex;
    flex-direction: row;
    min-height: 210mm;
    page-break-after: always;
    background-color: #002050;
    color: #ffffff;
}

.cover-left-accent {
    width: 10px;
    background-color: rgba(255, 255, 255, 0.25);
    flex-shrink: 0;
}

.cover-body {
    flex: 1;
    padding: 48px 60px;
    display: -webkit-flex;
    display: flex;
    flex-direction: column;
}

/* Top strip: company name left, date right */
.cover-top-strip {
    display: -webkit-flex;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 48px;
}

.cover-top-company {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2.5px;
    opacity: 0.55;
}

.cover-top-date {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2.5px;
    opacity: 0.55;
}

/* Logo */
.cover-logo-wrap {
    margin-bottom: 52px;
}

.cover-logo-wrap img {
    max-height: 72px;
    max-width: 280px;
    width: auto;
}

/* Main title block — takes remaining vertical space */
.cover-title-block {
    -webkit-flex: 1;
    flex: 1;
}

.cover-eyebrow {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 3.5px;
    color: rgba(255, 255, 255, 0.65);
    margin-bottom: 20px;
}

.cover-main-title {
    font-size: 68px;
    font-weight: 800;
    line-height: 1.0;
    text-transform: uppercase;
    margin: 0 0 6px 0;
    letter-spacing: -1px;
}

.cover-main-title-accent {
    color: #ffffff;
    opacity: 0.75;
}

.cover-accent-rule {
    width: 70px;
    height: 5px;
    background-color: rgba(255, 255, 255, 0.4);
    border-radius: 3px;
    margin: 28px 0;
}

.cover-subtitle-text {
    font-size: 15px;
    font-weight: 400;
    line-height: 1.6;
    opacity: 0.65;
    max-width: 480px;
}

/* Bottom footer strip */
.cover-footer {
    display: -webkit-flex;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-top: 20px;
    margin-top: 40px;
    border-top: 1px solid rgba(255, 255, 255, 0.15);
}

.cover-footer img {
    max-width: 130px;
    height: auto;
}

.cover-footer-period {
    font-size: 12px;
    font-weight: 600;
    opacity: 0.6;
    letter-spacing: 1px;
}

/* ── Chart placeholder ────────────────────────────────────────────────────── */
.chart-container {
    background-color: #f0f4f8;
    border-radius: 15px;
    padding: 20px;
    margin-bottom: 20px;
    min-height: 250px;
    display: flex;
    align-items: center;
    justify-content: center;
}

.chart-image {
    max-width: 100%;
    height: auto;
}

/* ── Intro text ───────────────────────────────────────────────────────────── */
.intro-text {
    font-size: 13px;
    font-weight: 400;
    line-height: 1.6;
    margin-bottom: 20px;
}

/* ── Table of Contents ────────────────────────────────────────────────────── */
.toc-container {
    margin-bottom: 30px;
}

.toc-row {
    display: flex;
    align-items: baseline;
    padding: 11px 0;
    border-bottom: 1px solid rgba(0, 0, 0, 0.1);
}

.toc-row:last-child {
    border-bottom: none;
}

.toc-number {
    font-size: 13px;
    font-weight: 600;
    color: #002050;
    flex: 0 0 30px;
}

.toc-title {
    font-size: 15px;
    font-weight: 500;
    flex: 1;
}

.toc-dots {
    flex: 0 1 120px;
    border-bottom: 1px dotted rgba(0, 0, 0, 0.3);
    margin: 0 12px;
    margin-bottom: 4px;
}

.toc-page {
    font-size: 18px;
    font-weight: 700;
    color: #002050;
    flex: 0 0 36px;
    text-align: right;
}
"""

LANDSCAPE_STYLES = """
@page {
    size: A4 landscape;
}
"""

PORTRAIT_STYLES = """
@page {
    size: A4 portrait;
}

/* Force every page div (including landscape-forced ones) to portrait dimensions */
.page,
.page-landscape {
    width: 210mm !important;
    height: 297mm !important;
    padding: 18px 28px !important;
}

/* Cover and back pages are also portrait height */
.cover-page {
    min-height: 297mm !important;
    height: 297mm !important;
}

/* Tighten table cells so wide tables fit in 210mm portrait width */
.table-container thead th {
    font-size: 9px !important;
    padding: 6px 6px !important;
}

.table-container tbody td {
    font-size: 9px !important;
    padding: 4px 6px !important;
    white-space: normal !important;
}

/* Metric cards — 2 per row in portrait instead of 3 */
.metric-card {
    width: 47% !important;
    margin: 0 1% 2% 1% !important;
}

.metric-value {
    font-size: 22px !important;
}

/* Reliability KPI cards — 2 per row */
.reliability-kpi-card {
    width: 47% !important;
    margin: 0 1% 2% 1% !important;
}

/* Infrastructure summary cards — 2 per row */
.summary-card {
    width: 47% !important;
    margin: 0 1% 2% 1% !important;
}
"""


# =============================================================================
# TABLE PAGINATION HELPER
# =============================================================================

def _paginate_table(rows_data, header_html, page_title, context, start_page, max_rows=12, landscape=False, note_html=''):
    """
    Split a potentially long table into multiple .page divs of max_rows rows.

    max_rows=12 keeps each page safely under 297mm even when cell text wraps.
    Set landscape=True to render in A4 landscape (height 210mm, width 297mm).

    Returns (html_string, pages_used).  Caller must increment page_number by
    pages_used instead of 1 so subsequent section numbers stay accurate.
    """
    items = list(rows_data) if rows_data else []
    chunks = [items[i:i + max_rows] for i in range(0, max(len(items), 1), max_rows)]

    page_class = 'page page-landscape' if landscape else 'page'

    pages_html = ""
    for idx, chunk in enumerate(chunks):
        pnum = start_page + idx
        suffix = " (continued)" if idx > 0 else ""
        rows_html = "".join(chunk)        # chunk already contains rendered <tr> strings
        pages_html += f"""
    <div class="{page_class}">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">{page_title}{suffix}</h1>
            {note_html if idx == 0 else ''}

            <div class="table-container">
                <table>
                    {header_html}
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{pnum}</div>
        </div>
    </div>"""

    return pages_html, len(chunks)


# =============================================================================
# HTML TEMPLATES FOR EACH SECTION
# =============================================================================

def render_cover_page(_data, context):
    """Render modern cover page HTML"""
    company_name = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    report_title = context.get('report_title', 'Monthly Performance Report')
    report_subtitle = context.get('report_subtitle', '')
    report_date = context.get('report_date', '')

    # Split title into first word(s) + last word for accent colouring.
    # e.g. "Monthly Performance Report" → "Monthly Performance" white + "Report" yellow
    words = report_title.split()
    if len(words) > 1:
        title_main = ' '.join(words[:-1])
        title_accent = words[-1]
    else:
        title_main = report_title
        title_accent = ''

    subtitle_html = (
        f'<div class="cover-subtitle-text">{report_subtitle} &bull; {context.get("report_scope", "")}</div>'
        if report_subtitle else
        f'<div class="cover-subtitle-text">{company_name} &bull; {context.get("report_scope", "")}</div>'
    )

    return f"""
    <div class="cover-page">
        <div class="cover-left-accent"></div>

        <div class="cover-body">

            <div class="cover-top-strip">
                <div class="cover-top-company">{company_name}</div>
                <div class="cover-top-date">{report_date}</div>
            </div>

            <div class="cover-logo-wrap">
                <img src="{context.get('logo_gray_url', '')}" alt="Company Logo" />
            </div>

            <div class="cover-title-block">
                <div class="cover-eyebrow">Performance Monitoring Tool</div>

                <h1 class="cover-main-title">
                    {title_main}<br/>
                    <span class="cover-main-title-accent">{title_accent}</span>
                </h1>

                <div class="cover-accent-rule"></div>

                {subtitle_html}
            </div>

            <div class="cover-footer">
                <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
                <div class="cover-footer-period">{report_date}</div>
            </div>

        </div>
    </div>
    """


_TOC_MAX_PER_PAGE = 12


def render_table_of_contents(entries, context, page_number):
    """Render auto-generated table of contents HTML.

    Paginates automatically when entries exceed _TOC_MAX_PER_PAGE per page.
    Returns (html, pages_used) so generate_html can track the real page offset.
    """
    import math
    chunks = [entries[i:i + _TOC_MAX_PER_PAGE] for i in range(0, max(len(entries), 1), _TOC_MAX_PER_PAGE)]
    pages_html = ""

    for chunk_idx, chunk in enumerate(chunks):
        pnum = page_number + chunk_idx
        suffix = " (continued)" if chunk_idx > 0 else ""
        rows_html = ""
        for i, entry in enumerate(chunk, start=chunk_idx * _TOC_MAX_PER_PAGE + 1):
            rows_html += f"""
        <div class="toc-row">
            <span class="toc-number">{i:02d}</span>
            <span class="toc-title">{entry['title']}</span>
            <span class="toc-dots"></span>
            <span class="toc-page">{entry['page']}</span>
        </div>
            """

        pages_html += f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">Contents{suffix}</h1>

            <div class="toc-container">
                {rows_html}
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{pnum}</div>
        </div>
    </div>
    """

    return pages_html, len(chunks)


def render_infrastructure_overview(data, context, page_number):
    """Render infrastructure overview HTML"""
    feeders_html = ""
    for feeder in data.get('feeders', []):
        feeders_html += f"""
        <tr>
            <td>{feeder['name']}</td>
            <td>{feeder['voltage']}</td>
            <td>{feeder['band']}</td>
            <td>{feeder['district']}</td>
            <td>{feeder['transformer_count']}</td>
        </tr>
        """

    summary_points = context.get('summary_points', [])
    summary_html = ""
    for point in summary_points:
        summary_html += f"<li>{point}</li>"

    feeders_11kv = data.get('feeders_11kv', 0)
    feeders_33kv = data.get('feeders_33kv', 0)

    # Only show a voltage stat box when that type actually exists in the result
    # set — avoids showing "33kV Feeders: 0" when the user filtered to 11kV only
    voltage_11kv_html = f"""
            <div class="stat-item">
                <h3>11kV Feeders</h3>
                <div class="value">{feeders_11kv}</div>
            </div>""" if feeders_11kv > 0 else ""

    voltage_33kv_html = f"""
            <div class="stat-item">
                <h3>33kV Feeders</h3>
                <div class="value">{feeders_33kv}</div>
            </div>""" if feeders_33kv > 0 else ""

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">Infrastructure Overview</h1>

            <div class="stats-container">
                <div class="stat-item">
                    <h3>Total Feeders Monitored</h3>
                    <div class="value">{data.get('total_feeders', 0)}</div>
                </div>
                {voltage_11kv_html}
                {voltage_33kv_html}
                <div class="stat-item">
                    <h3>D/Transformers Monitored</h3>
                    <div class="value">{data.get('total_transformers', 0)}</div>
                </div>
                <div class="stat-item">
                    <h3>Onboarded Substations</h3>
                    <div class="value">{data.get('total_substations', 0)}</div>
                </div>
            </div>

            <div class="content-box">
                <div class="summary-section">
                    <h2>PERFORMANCE SUMMARY</h2>
                    <ul>
                        {summary_html}
                    </ul>
                </div>

                <div class="table-section">
                    <table>
                        <thead>
                            <tr>
                                <th>Feeder Name</th>
                                <th>Voltage</th>
                                <th>Band</th>
                                <th>District</th>
                                <th>NO. D/T</th>
                            </tr>
                        </thead>
                        <tbody>
                            {feeders_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_technical_metrics(data, context, page_number, config=None):
    """Render technical metrics cards HTML"""
    config = config or {}
    selected_metrics = config.get('metrics', [
        'hours_of_supply', 'average_load', 'energy_delivered',
        'daily_average_consumption', 'total_interruptions', 'load_shedding_count'
    ])

    metric_definitions = {
        'hours_of_supply': {
            'label': 'HOURS OF<br/>SUPPLY',
            'value': f"{data.get('hours_of_supply', 0):,.2f} Hrs",
            'subtitle': 'Average per Feeder per Day',
            'description': 'Average hours of supply per feeder per day',
        },
        'average_load': {
            'label': 'AVERAGE<br/>LOAD',
            'value': f"{data.get('average_load', 0):,.2f} MW",
            'subtitle': 'Average',
            'description': 'Average load across all monitored feeders',
        },
        'peak_load': {
            'label': 'PEAK<br/>LOAD',
            'value': f"{data.get('peak_load', 0):,.2f} MW",
            'subtitle': 'Maximum',
            'description': 'Maximum load recorded during the period',
        },
        'energy_delivered': {
            'label': 'ENERGY<br/>DELIVERED',
            'value': f"{data.get('energy_delivered', 0):,.2f} MWh",
            'subtitle': 'Total (Meter + System estimate)',
            'description': (
                'Total energy delivered through monitored feeders'
                + (
                    f'<div style="display:flex;gap:6px;margin-top:6px;">'
                    f'<div style="flex:1;background:rgba(0,32,80,0.07);border-radius:6px;'
                    f'padding:5px 8px;text-align:center;">'
                    f'<div style="font-size:8px;font-weight:700;opacity:0.6;margin-bottom:2px;">33kV</div>'
                    f'<div style="font-size:10px;font-weight:700;color:#002050;">{data["energy_33kv"]:,.1f} MWh</div>'
                    f'</div>'
                    f'<div style="flex:1;background:rgba(0,32,80,0.07);border-radius:6px;'
                    f'padding:5px 8px;text-align:center;">'
                    f'<div style="font-size:8px;font-weight:700;opacity:0.6;margin-bottom:2px;">11kV</div>'
                    f'<div style="font-size:10px;font-weight:700;color:#002050;">{data["energy_11kv"]:,.1f} MWh</div>'
                    f'</div>'
                    f'</div>'
                    if data.get('energy_33kv') is not None and data.get('energy_11kv') is not None
                    else ''
                )
            ),
        },
        'daily_average_consumption': {
            'label': 'DAILY AVG<br/>CONSUMPTION',
            'value': f"{data.get('daily_average_consumption', 0):,.2f} MWh",
            'subtitle': 'Average per Day',
            'description': 'Average daily energy consumption across the period',
        },
        'total_interruptions': {
            'label': 'TOTAL<br/>INTERRUPTIONS',
            'value': f"{data.get('total_interruptions', 0):,} Times",
            'subtitle': 'Count',
            'description': 'Total number of feeder interruptions in the period',
        },
        'load_shedding_count': {
            'label': 'LOAD SHEDDING<br/>COUNT',
            'value': f"{data.get('load_shedding_count', 0):,} Times",
            'subtitle': 'Count',
            'description': 'Interruptions classified as load shedding (L/S)',
        },
    }

    cards_html = ""
    for i, metric_key in enumerate(selected_metrics):
        if metric_key not in metric_definitions:
            continue
        metric = metric_definitions[metric_key]
        # Alternate card background colours across the 2-column grid
        card_class = 'primary' if i % 2 == 0 else 'secondary'
        cards_html += f"""
        <div class="metric-card {card_class}">
            <div class="metric-label">
                <h2>{metric['label']}</h2>
            </div>
            <div class="metric-content">
                <div class="metric-value">{metric['value']}</div>
                <div class="metric-subtitle">{metric['subtitle']}</div>
                <div class="metric-description">{metric['description']}</div>
            </div>
        </div>
        """

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">Technical Overview</h1>

            <div class="metrics-grid">
                {cards_html}
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_system_reliability(data, context, page_number):
    """Render system reliability section HTML — expanded KPI card layout"""
    cum_hours = data.get('cumulative_interruption_hours', 0)
    avg_duration = data.get('avg_duration_of_interruption', 0)
    avg_tat = data.get('avg_turnaround_time', 0)

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">System Reliability</h1>

            <div class="reliability-highlight">
                <h2>System Reliability Summary</h2>
                <p>Key reliability metrics for the reporting period across all monitored feeders</p>
            </div>

            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{cum_hours} hrs</div>
                    <div class="reliability-kpi-label">Cumulative Hours<br/>of Interruption</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{avg_duration} hrs</div>
                    <div class="reliability-kpi-label">Average Duration<br/>of Interruption</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{avg_tat} hrs</div>
                    <div class="reliability-kpi-label">Average Turnaround<br/>Time (Local Faults)</div>
                </div>
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_interruption_breakdown(data, context, page_number):
    """Render interruption breakdown — paginates if more than 20 rows."""
    header_html = """
        <thead>
            <tr>
                <th>Interruption Type</th>
                <th style="text-align:right;">Count</th>
                <th style="text-align:right;">Total Hours</th>
                <th style="text-align:right;">Avg Duration</th>
            </tr>
        </thead>"""

    row_strings = []
    for item in (data or []):
        row_strings.append(f"""
        <tr>
            <td>{item['type']}</td>
            <td style="text-align:right;">{item['count']}</td>
            <td style="text-align:right;">{item['total_hours']} hrs</td>
            <td style="text-align:right;">{item['avg_duration']} hrs</td>
        </tr>""")

    return _paginate_table(row_strings, header_html, "Interruption Breakdown",
                           context, page_number, max_rows=15)


def render_feeder_performance_table(data, context, page_number):
    """Render feeder performance table — paginates at 20 rows per page."""
    header_html = """
        <colgroup>
            <col style="width:30%">
            <col style="width:6%">
            <col style="width:14%">
            <col style="width:12%">
            <col style="width:12%">
            <col style="width:13%">
            <col style="width:13%">
        </colgroup>
        <thead>
            <tr>
                <th>Feeder Name</th>
                <th>Band</th>
                <th style="text-align:right;">Avg Supply (hrs)</th>
                <th style="text-align:right;">Availability</th>
                <th style="text-align:right;">Duration (hrs)</th>
                <th style="text-align:right;">Peak Load</th>
                <th style="text-align:right; line-height: 1.2;">
                    Energy (MWh)<br>
                    <span style="font-size:7px; font-weight:normal; color:#4caf50;">&#9679; METER</span> &nbsp;
                    <span style="font-size:7px; font-weight:normal; color:#6c9bd1;">&#9679; SYSTEM</span>
                </th>
            </tr>
        </thead>"""

    row_strings = []
    current_band = None
    for feeder in (data or []):
        # Insert a band group header row whenever the band changes
        band = feeder['band']
        if band != current_band:
            current_band = band
            row_strings.append(f"""
        <tr style="background-color:#002050;">
            <td colspan="7" style="
                font-weight:700;
                font-size:11px;
                letter-spacing:1.5px;
                text-transform:uppercase;
                color:#ffffff;
                padding:6px 10px;
                border-bottom:2px solid rgba(255,255,255,0.2);
            ">&#9658; Band {band}</td>
        </tr>""")
        # Color code the energy source
        source_label = feeder.get('energy_source', 'system').upper()
        dot_color = '#4caf50' if source_label == 'METER' else '#6c9bd1'

        # Flag minigrids/solar feeders
        feeder_name = feeder['name']
        is_minigrid = any(kw in feeder_name.upper() for kw in ('SOLAR', 'MINIGRID', 'MINI-GRID'))
        name_html = (
            f'{feeder_name} <span style="font-size:8px;font-weight:700;color:#c62828;'
            f'background:#fce4ec;padding:1px 4px;border-radius:3px;">MINIGRID</span>'
            if is_minigrid else feeder_name
        )

        row_strings.append(f"""
        <tr>
            <td>{name_html}</td>
            <td>{feeder['band']}</td>
            <td style="text-align:right;">{float(feeder['hours_of_supply']):,.2f} hrs</td>
            <td style="text-align:right;">{float(feeder['availability_percentage']):,.1f}%</td>
            <td style="text-align:right;">{float(feeder['duration_hours']):,.2f} hrs</td>
            <td style="text-align:right;">{float(feeder['peak_load']):,.2f} MW</td>
            <td style="text-align:right; white-space:nowrap;">
                <span style="color:{dot_color}; font-size:10px;">&#9679;</span> {float(feeder['energy_delivered']):,.2f} MWh
            </td>
        </tr>""")

    return _paginate_table(row_strings, header_html, "Feeder Performance",
                           context, page_number, max_rows=15)


def render_service_band_summary(data, context, page_number):
    """Render service band summary HTML with summary stat cards"""
    rows_html = ""
    total_feeders = 0
    total_interruptions = 0
    weighted_supply_sum = 0.0

    for band in data:
        fc = band.get('feeder_count', 0)
        intr = band.get('interruptions', 0)
        hrs = band.get('hours_of_supply', 0)
        total_feeders += fc
        total_interruptions += intr
        try:
            weighted_supply_sum += float(hrs) * fc
        except (ValueError, TypeError):
            pass
        rows_html += f"""
        <tr>
            <td>Band {band['band']}</td>
            <td style="text-align:right;">{fc}</td>
            <td style="text-align:right;">{hrs} hrs</td>
            <td style="text-align:right;">{intr}</td>
        </tr>
        """

    avg_supply = round(weighted_supply_sum / total_feeders, 2) if total_feeders > 0 else 0

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Service Band Summary</h1>

        <div class="stats-container">
            <div class="stat-item">
                <h3>Total Feeders</h3>
                <div class="value">{total_feeders}</div>
            </div>
            <div class="stat-item">
                <h3>Avg Hours of Supply</h3>
                <div class="value">{avg_supply} hrs</div>
            </div>
            <div class="stat-item">
                <h3>Total Interruptions</h3>
                <div class="value">{total_interruptions}</div>
            </div>
        </div>

        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Service Band</th>
                        <th style="text-align:right;">Feeders</th>
                        <th style="text-align:right;">Avg Supply (hrs/day)</th>
                        <th style="text-align:right;">Interruptions</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_state_performance_table(data, context, page_number):
    """Render state performance table — paginated so rows never overflow the page."""
    header_html = """
        <colgroup>
            <col style="width:28%">
            <col style="width:12%">
            <col style="width:18%">
            <col style="width:14%">
            <col style="width:14%">
            <col style="width:14%">
        </colgroup>
        <thead>
            <tr>
                <th>State</th>
                <th style="text-align:right;">Feeders</th>
                <th style="text-align:right;">Avg Supply (hrs)</th>
                <th style="text-align:right;">Availability</th>
                <th style="text-align:right;">Interruptions</th>
                <th style="text-align:right;">Peak Load</th>
            </tr>
        </thead>"""

    row_strings = []
    for state in (data or []):
        row_strings.append(f"""
        <tr>
            <td>{state['state_name']}</td>
            <td style="text-align:right;">{state['feeder_count']:,}</td>
            <td style="text-align:right;">{float(state['hours_of_supply']):,.2f} hrs</td>
            <td style="text-align:right;">{float(state['availability_percentage']):,.1f}%</td>
            <td style="text-align:right;">{state['interruptions']:,}</td>
            <td style="text-align:right;">{float(state['peak_load']):,.2f} MW</td>
        </tr>""")

    return _paginate_table(row_strings, header_html, 'State Performance', context, page_number, max_rows=20)


def render_district_performance_table(data, context, page_number):
    """Render district performance table — paginated so rows never overflow the page."""
    header_html = """
        <colgroup>
            <col style="width:24%">
            <col style="width:16%">
            <col style="width:10%">
            <col style="width:16%">
            <col style="width:12%">
            <col style="width:11%">
            <col style="width:11%">
        </colgroup>
        <thead>
            <tr>
                <th>District</th>
                <th>State</th>
                <th style="text-align:right;">Feeders</th>
                <th style="text-align:right;">Avg Supply (hrs)</th>
                <th style="text-align:right;">Availability</th>
                <th style="text-align:right;">Interruptions</th>
                <th style="text-align:right;">Peak Load</th>
            </tr>
        </thead>"""

    row_strings = []
    for district in (data or []):
        row_strings.append(f"""
        <tr>
            <td>{district['district_name']}</td>
            <td>{district['state_name']}</td>
            <td style="text-align:right;">{district['feeder_count']:,}</td>
            <td style="text-align:right;">{float(district['hours_of_supply']):,.2f} hrs</td>
            <td style="text-align:right;">{float(district['availability_percentage']):,.1f}%</td>
            <td style="text-align:right;">{district['interruptions']:,}</td>
            <td style="text-align:right;">{float(district['peak_load']):,.2f} MW</td>
        </tr>""")

    return _paginate_table(row_strings, header_html, 'District Performance', context, page_number, max_rows=18)


def render_custom_text(data, context, page_number):
    """Render custom text section HTML"""
    title = data.get('title', 'Notes')
    content = data.get('content', '')

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">{title}</h1>

        <div class="intro-text">
            {content}
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_gaps_improvements(data, context, page_number):
    """Render gaps and improvements section HTML"""
    sections = data.get('sections', [])

    cards_html = ""
    for i, section in enumerate(sections):
        letter = chr(65 + i)  # A, B, C, etc.
        items_html = ""
        for item in section.get('content', []):
            items_html += f"<li>{item}</li>"

        cards_html += f"""
        <div class="section-card">
            <div class="section-header">
                <h3 class="section-card-title">{section.get('title', '')}</h3>
                <div class="section-icon">{letter}</div>
            </div>
            <div class="section-content">
                <ul>
                    {items_html}
                </ul>
            </div>
        </div>
        """

    intro_text = data.get('intro_text', '')

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Gaps and Improvement Areas</h1>

        <p class="intro-text">{intro_text}</p>

        <div class="sections-grid">
            {cards_html}
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def _split_trend_rows(data, key_date, key_value, unit):
    """Split trend data into two HTML column strings for a 2-column layout."""
    items = data if isinstance(data, list) else []
    mid = (len(items) + 1) // 2
    left, right = items[:mid], items[mid:]

    def make_rows(chunk):
        html = ""
        for item in chunk:
            html += f"""
            <tr>
                <td>{item.get(key_date, '')}</td>
                <td style="text-align:right;">{item.get(key_value, 0)} {unit}</td>
            </tr>"""
        return html

    return make_rows(left), make_rows(right)


# =============================================================================
# SVG CHART HELPERS
# =============================================================================

def _render_svg_line_chart(
    data_points, value_key, unit,
    color='#002050', fill='rgba(0,32,80,0.08)',
    prev_data_points=None, curr_label='Current', prev_label='Previous',
):
    """
    Inline SVG line chart.  Works in both Playwright and WeasyPrint.

    When prev_data_points is supplied a second dashed line is drawn for
    the previous period using the same y-scale so the two can be compared
    visually.  Both series are indexed by position (Day 1, Day 2, …) so
    they always share the same x-axis regardless of calendar differences.
    """
    curr_items = data_points if isinstance(data_points, list) else []
    prev_items = prev_data_points if isinstance(prev_data_points, list) else []
    has_prev   = bool(prev_items)

    if not curr_items:
        return '<div style="text-align:center;padding:30px;color:#aaa;font-size:11px;">No data available</div>'

    PREV_COLOR = '#6c9bd1'

    W, H = 780, 155
    # Reserve right margin for legend when comparison is active
    ml, mr, mt, mb = 52, (115 if has_prev else 15), 12, 32
    pw = W - ml - mr
    ph = H - mt - mb

    curr_vals = [float(d.get(value_key) or 0) for d in curr_items]
    prev_vals = [float(d.get(value_key) or 0) for d in prev_items] if has_prev else []

    all_vals  = curr_vals + prev_vals
    v_min, v_max = min(all_vals), max(all_vals)
    if v_max == v_min:
        v_max = v_min + 1
    span  = v_max - v_min
    y_min = max(0, v_min - span * 0.05)
    y_max = v_max + span * 0.12

    n = len(curr_vals)

    def xp(i, total=None):
        total = total or n
        return ml + (i / (total - 1)) * pw if total > 1 else ml + pw / 2

    def yp(v):
        return mt + ph - ((v - y_min) / (y_max - y_min)) * ph

    # Current period polyline + fill
    pts_curr  = ' '.join(f"{xp(i):.1f},{yp(v):.1f}" for i, v in enumerate(curr_vals))
    fill_pts  = f"{xp(0):.1f},{mt + ph:.1f} {pts_curr} {xp(n - 1):.1f},{mt + ph:.1f}"

    # Previous period polyline (mapped to same x-axis by position)
    prev_line_svg = ''
    if has_prev:
        pts_prev = ' '.join(
            f"{xp(i, n):.1f},{yp(v):.1f}"   # x mapped via current-period scale
            for i, v in enumerate(prev_vals)
        )
        prev_line_svg = (
            f'<polyline points="{pts_prev}" fill="none" stroke="{PREV_COLOR}" '
            f'stroke-width="1.8" stroke-dasharray="5,3" '
            f'stroke-linejoin="round" stroke-linecap="round" opacity="0.8"/>'
        )

    # Y-axis gridlines + labels (5 ticks)
    ticks_svg = ''
    for k in range(5):
        tv = y_min + (y_max - y_min) * k / 4
        ty = yp(tv)
        ticks_svg += (
            f'<line x1="{ml}" y1="{ty:.1f}" x2="{W - mr}" y2="{ty:.1f}" '
            f'stroke="#e4e8ef" stroke-width="1"/>'
            f'<text x="{ml - 4}" y="{ty:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" fill="#888" font-size="9" '
            f'font-family="Arial,sans-serif">{tv:.1f}</text>'
        )

    # X-axis day-index labels (max 10, shows day number not date)
    step = max(1, n // 10)
    xlabels_svg = ''
    for i in range(0, n, step):
        label = str(curr_items[i].get('date', ''))[-5:]  # MM-DD of current period
        xlabels_svg += (
            f'<text x="{xp(i):.1f}" y="{mt + ph + 14}" text-anchor="middle" '
            f'fill="#888" font-size="9" font-family="Arial,sans-serif">{label}</text>'
        )

    # Dots on current line (only when ≤ 62 points)
    dots_svg = ''
    if n <= 62:
        for i, v in enumerate(curr_vals):
            dots_svg += f'<circle cx="{xp(i):.1f}" cy="{yp(v):.1f}" r="2.5" fill="{color}"/>'

    unit_label = (
        f'<text x="{ml}" y="{mt - 2}" text-anchor="start" fill="#aaa" '
        f'font-size="9" font-family="Arial,sans-serif">({unit})</text>'
    )

    # Legend (top-right, only when comparison is active)
    legend_svg = ''
    if has_prev:
        lx = W - mr + 8
        legend_svg = (
            # Current period — solid line swatch
            f'<line x1="{lx}" y1="{mt + 10}" x2="{lx + 18}" y2="{mt + 10}" '
            f'stroke="{color}" stroke-width="2.5"/>'
            f'<text x="{lx + 22}" y="{mt + 13}" fill="#444" font-size="9" '
            f'font-family="Arial,sans-serif" font-weight="600">{curr_label}</text>'
            # Previous period — dashed line swatch
            f'<line x1="{lx}" y1="{mt + 26}" x2="{lx + 18}" y2="{mt + 26}" '
            f'stroke="{PREV_COLOR}" stroke-width="1.8" stroke-dasharray="5,3" opacity="0.8"/>'
            f'<text x="{lx + 22}" y="{mt + 29}" fill="#888" font-size="9" '
            f'font-family="Arial,sans-serif">{prev_label}</text>'
        )

    return (
        f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block;overflow:visible;" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polygon points="{fill_pts}" fill="{fill}"/>'
        f'{ticks_svg}'
        f'<line x1="{ml}" y1="{mt + ph}" x2="{W - mr}" y2="{mt + ph}" stroke="#ccc" stroke-width="1"/>'
        f'{prev_line_svg}'
        f'<polyline points="{pts_curr}" fill="none" stroke="{color}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'{dots_svg}'
        f'{xlabels_svg}'
        f'{unit_label}'
        f'{legend_svg}'
        f'</svg>'
    )


def _render_svg_bar_chart(data_points, value_key, unit, color='#002050'):
    """Generate an inline SVG bar chart. Works in both Playwright and WeasyPrint."""
    items = data_points if isinstance(data_points, list) else []
    if not items:
        return '<div style="text-align:center;padding:30px;color:#aaa;font-size:11px;">No data available</div>'

    W, H = 780, 155
    ml, mr, mt, mb = 52, 15, 12, 32
    pw = W - ml - mr
    ph = H - mt - mb

    values = [float(d.get(value_key) or 0) for d in items]
    n = len(values)

    y_max = (max(values) if values else 1) * 1.12 or 1

    def yp(v):
        return mt + ph - (v / y_max) * ph

    bar_w = pw / n * 0.72
    bar_gap = pw / n * 0.28

    ticks_svg = ''
    for k in range(5):
        tv = y_max * k / 4
        ty = yp(tv)
        ticks_svg += (
            f'<line x1="{ml}" y1="{ty:.1f}" x2="{W - mr}" y2="{ty:.1f}" '
            f'stroke="#e4e8ef" stroke-width="1"/>'
            f'<text x="{ml - 4}" y="{ty:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" fill="#888" font-size="9" '
            f'font-family="Arial,sans-serif">{tv:.1f}</text>'
        )

    bars_svg = ''
    xlabels_svg = ''
    step = max(1, n // 10)
    for i, v in enumerate(items):
        val = float(v.get(value_key) or 0)
        bx = ml + i * (pw / n) + bar_gap / 2
        bh = max(0, (val / y_max) * ph)
        by = mt + ph - bh
        bars_svg += f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{color}" rx="2" opacity="0.85"/>'
        if i % step == 0:
            label = str(v.get('date', ''))[-5:]
            xlabels_svg += (
                f'<text x="{bx + bar_w / 2:.1f}" y="{mt + ph + 14}" text-anchor="middle" '
                f'fill="#888" font-size="9" font-family="Arial,sans-serif">{label}</text>'
            )

    unit_label = (
        f'<text x="{ml}" y="{mt - 2}" text-anchor="start" fill="#aaa" '
        f'font-size="9" font-family="Arial,sans-serif">({unit})</text>'
    )

    return (
        f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block;overflow:visible;" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{ticks_svg}'
        f'<line x1="{ml}" y1="{mt + ph}" x2="{W - mr}" y2="{mt + ph}" stroke="#ccc" stroke-width="1"/>'
        f'{bars_svg}'
        f'{xlabels_svg}'
        f'{unit_label}'
        f'</svg>'
    )


def _trend_summary_row(values, unit, prev_values=None, prev_label='Prev period'):
    """
    Mini HTML summary strip: Min / Avg / Max / Trend arrow / vs Previous.

    When prev_values is supplied a fifth card shows the % change between
    the previous-period average and the current-period average.
    """
    if not values:
        return ''
    mn  = min(values)
    mx  = max(values)
    avg = sum(values) / len(values)

    # Internal trend: first-half avg vs second-half avg
    mid        = len(values) // 2
    first_avg  = sum(values[:mid]) / mid if mid else avg
    second_avg = sum(values[mid:]) / (len(values) - mid) if (len(values) - mid) else avg
    arrow       = '&#9650;' if second_avg > first_avg else ('&#9660;' if second_avg < first_avg else '&#9654;')
    trend_color = '#2e7d32' if second_avg > first_avg else ('#c62828' if second_avg < first_avg else '#555')

    # Period-over-period delta card
    vs_card = ''
    if prev_values:
        prev_avg = sum(prev_values) / len(prev_values)
        if prev_avg != 0:
            delta_pct = ((avg - prev_avg) / abs(prev_avg)) * 100
            sign      = '+' if delta_pct >= 0 else ''
            d_color   = '#2e7d32' if delta_pct >= 0 else '#c62828'
            d_arrow   = '&#9650;' if delta_pct > 0 else ('&#9660;' if delta_pct < 0 else '&#9654;')
            vs_card = f"""
  <div style="flex:1;text-align:center;background:#eef2f8;border-radius:8px;padding:8px 4px;margin-left:8px;border:1px solid #d0d8e8;">
    <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.6;margin-bottom:3px;">vs {prev_label}</div>
    <div style="font-size:16px;font-weight:700;color:{d_color};">{d_arrow} {sign}{delta_pct:.1f}%</div>
    <div style="font-size:9px;opacity:0.5;">prev avg {prev_avg:.2f} {unit}</div>
  </div>"""

    return f"""
<div style="display:flex;gap:0;margin-top:10px;">
  <div style="flex:1;text-align:center;background:#f0f4f8;border-radius:8px;padding:8px 4px;margin-right:8px;">
    <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.6;margin-bottom:3px;">Min</div>
    <div style="font-size:16px;font-weight:700;color:#002050;">{mn:.2f} <span style="font-size:9px;font-weight:400;">{unit}</span></div>
  </div>
  <div style="flex:1;text-align:center;background:#f0f4f8;border-radius:8px;padding:8px 4px;margin-right:8px;">
    <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.6;margin-bottom:3px;">Avg</div>
    <div style="font-size:16px;font-weight:700;color:#002050;">{avg:.2f} <span style="font-size:9px;font-weight:400;">{unit}</span></div>
  </div>
  <div style="flex:1;text-align:center;background:#f0f4f8;border-radius:8px;padding:8px 4px;margin-right:8px;">
    <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.6;margin-bottom:3px;">Max</div>
    <div style="font-size:16px;font-weight:700;color:#002050;">{mx:.2f} <span style="font-size:9px;font-weight:400;">{unit}</span></div>
  </div>
  <div style="flex:1;text-align:center;background:#f0f4f8;border-radius:8px;padding:8px 4px;">
    <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.6;margin-bottom:3px;">Trend</div>
    <div style="font-size:20px;font-weight:700;color:{trend_color};">{arrow}</div>
  </div>
  {vs_card}
</div>"""


def render_hours_of_supply_chart(data, context, page_number):
    """Render hours of supply: SVG line chart + summary strip + two-column data table."""
    # data is now a dict {current, previous, curr_label, prev_label, …}
    curr_items  = data.get('current', []) if isinstance(data, dict) else (data or [])
    prev_items  = data.get('previous', []) if isinstance(data, dict) else []
    curr_label  = data.get('curr_label', 'Current')  if isinstance(data, dict) else 'Current'
    prev_label  = data.get('prev_label', 'Previous') if isinstance(data, dict) else 'Previous'

    values      = [float(d.get('hours') or 0) for d in curr_items]
    prev_values = [float(d.get('hours') or 0) for d in prev_items]

    chart_type = data.get('_chart_type', 'line') if isinstance(data, dict) else 'line'
    if chart_type == 'bar':
        chart_svg = _render_svg_bar_chart(curr_items, 'hours', 'hrs')
    elif chart_type == 'table_only':
        chart_svg = ''
    else:
        chart_svg = _render_svg_line_chart(
            curr_items, 'hours', 'hrs',
            prev_data_points=prev_items, curr_label=curr_label, prev_label=prev_label,
        )
    summary_html = _trend_summary_row(values, 'hrs', prev_values=prev_values, prev_label=prev_label)
    left_rows, right_rows = _split_trend_rows(curr_items, 'date', 'hours', 'hrs')

    col_header = """
        <thead>
            <tr>
                <th>Date</th>
                <th style="text-align:right;">Hrs of Supply</th>
            </tr>
        </thead>"""

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">Hours of Supply Trend</h1>

            {f'<div style="background:#f8fafc;border-radius:12px;padding:12px 14px;margin-bottom:8px;">{chart_svg}</div>' if chart_svg else ''}

            {summary_html}

            <div style="margin-top:12px;">
                <div class="trend-columns">
                    <div class="trend-column">
                        <div class="table-container">
                            <table>{col_header}<tbody>{left_rows}</tbody></table>
                        </div>
                    </div>
                    <div class="trend-column">
                        <div class="table-container">
                            <table>{col_header}<tbody>{right_rows}</tbody></table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_load_trend_chart(data, context, page_number):
    """Render load trend: SVG line chart + summary strip + two-column data table."""
    curr_items  = data.get('current', []) if isinstance(data, dict) else (data or [])
    prev_items  = data.get('previous', []) if isinstance(data, dict) else []
    curr_label  = data.get('curr_label', 'Current')  if isinstance(data, dict) else 'Current'
    prev_label  = data.get('prev_label', 'Previous') if isinstance(data, dict) else 'Previous'

    values      = [float(d.get('value') or 0) for d in curr_items]
    prev_values = [float(d.get('value') or 0) for d in prev_items]

    chart_type = data.get('_chart_type', 'line') if isinstance(data, dict) else 'line'
    if chart_type == 'bar':
        chart_svg = _render_svg_bar_chart(curr_items, 'value', 'MW')
    elif chart_type == 'table_only':
        chart_svg = ''
    else:
        chart_svg = _render_svg_line_chart(
            curr_items, 'value', 'MW', color='#1a5276',
            prev_data_points=prev_items, curr_label=curr_label, prev_label=prev_label,
        )
    summary_html = _trend_summary_row(values, 'MW', prev_values=prev_values, prev_label=prev_label)
    left_rows, right_rows = _split_trend_rows(curr_items, 'date', 'value', 'MW')

    col_header = """
        <thead>
            <tr>
                <th>Date</th>
                <th style="text-align:right;">Load (MW)</th>
            </tr>
        </thead>"""

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">Load Trend</h1>

            {f'<div style="background:#f8fafc;border-radius:12px;padding:12px 14px;margin-bottom:8px;">{chart_svg}</div>' if chart_svg else ''}

            {summary_html}

            <div style="margin-top:12px;">
                <div class="trend-columns">
                    <div class="trend-column">
                        <div class="table-container">
                            <table>{col_header}<tbody>{left_rows}</tbody></table>
                        </div>
                    </div>
                    <div class="trend-column">
                        <div class="table-container">
                            <table>{col_header}<tbody>{right_rows}</tbody></table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_energy_delivered_chart(data, context, page_number):
    """Render energy delivered: SVG bar chart + summary strip + two-column data table."""
    curr_items  = data.get('current', []) if isinstance(data, dict) else (data or [])
    prev_items  = data.get('previous', []) if isinstance(data, dict) else []
    prev_label  = data.get('prev_label', 'Previous') if isinstance(data, dict) else 'Previous'

    values      = [float(d.get('value') or 0) for d in curr_items]
    prev_values = [float(d.get('value') or 0) for d in prev_items]

    chart_type = data.get('_chart_type', 'bar') if isinstance(data, dict) else 'bar'
    if chart_type == 'line':
        chart_svg = _render_svg_line_chart(curr_items, 'value', 'MWh')
    elif chart_type == 'table_only':
        chart_svg = ''
    else:
        chart_svg = _render_svg_bar_chart(curr_items, 'value', 'MWh')
    summary_html = _trend_summary_row(values, 'MWh', prev_values=prev_values, prev_label=prev_label)
    left_rows, right_rows = _split_trend_rows(curr_items, 'date', 'value', 'MWh')

    col_header = """
        <thead>
            <tr>
                <th>Date</th>
                <th style="text-align:right;">Energy (MWh)</th>
            </tr>
        </thead>"""

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">Energy Delivered Trend</h1>

            {f'<div style="background:#f8fafc;border-radius:12px;padding:12px 14px;margin-bottom:8px;">{chart_svg}</div>' if chart_svg else ''}

            {summary_html}

            <div style="margin-top:12px;">
                <div class="trend-columns">
                    <div class="trend-column">
                        <div class="table-container">
                            <table>{col_header}<tbody>{left_rows}</tbody></table>
                        </div>
                    </div>
                    <div class="trend-column">
                        <div class="table-container">
                            <table>{col_header}<tbody>{right_rows}</tbody></table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


# =============================================================================
# HR SECTION RENDERERS
# =============================================================================

def render_hr_overview(data, context, page_number):
    """Render HR overview KPI cards."""
    total        = data.get('total_staff', 0)
    male         = data.get('male_count', 0)
    female       = data.get('female_count', 0)
    depts        = data.get('departments_count', 0)
    attr_count   = data.get('attrition_count', 0)
    attr_rate    = data.get('attrition_rate', 0.0)
    new_hires    = data.get('new_hires', 0)

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">HR Overview</h1>
            <div class="reliability-highlight">
                <h2>Human Resources Summary</h2>
                <p>Workforce snapshot for the reporting period</p>
            </div>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{total}</div>
                    <div class="reliability-kpi-label">Total Active<br/>Staff</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{male}</div>
                    <div class="reliability-kpi-label">Male<br/>Employees</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{female}</div>
                    <div class="reliability-kpi-label">Female<br/>Employees</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{depts}</div>
                    <div class="reliability-kpi-label">Active<br/>Departments</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{attr_count}</div>
                    <div class="reliability-kpi-label">Attritions<br/>This Period</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{attr_rate}%</div>
                    <div class="reliability-kpi-label">Attrition<br/>Rate</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{new_hires}</div>
                    <div class="reliability-kpi-label">New Hires<br/>This Period</div>
                </div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_staff_metrics(data, context, page_number):
    """Render staff metrics: tenure + grade breakdown table."""
    total        = data.get('total_staff', 0)
    avg_tenure   = data.get('avg_tenure_years', 0.0)
    grade_rows   = data.get('grade_breakdown', [])

    row_html = "".join(
        f"<tr><td>{r.get('grade') or '—'}</td><td style='text-align:right'>{r.get('count', 0)}</td></tr>"
        for r in grade_rows
    )

    header_html = """
        <thead>
            <tr>
                <th>Grade</th>
                <th style="text-align:right">Headcount</th>
            </tr>
        </thead>"""

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Staff Metrics</h1>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{total}</div>
                    <div class="reliability-kpi-label">Total Active Staff</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{avg_tenure} yrs</div>
                    <div class="reliability-kpi-label">Average Tenure</div>
                </div>
            </div>
            <h2 style="margin:16px 0 8px;font-size:13px;">Grade Breakdown</h2>
            <div class="table-container">
                <table>
                    {header_html}
                    <tbody>{row_html}</tbody>
                </table>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_wage_bill_analysis(data, context, page_number):
    """Render wage bill: summary cards + paginated department breakdown."""
    total_bill   = data.get('total_wage_bill', 0.0)
    avg_salary   = data.get('avg_salary', 0.0)
    dept_rows    = data.get('department_breakdown', [])

    header_html = """
        <thead>
            <tr>
                <th>Department</th>
                <th style="text-align:right">Headcount</th>
                <th style="text-align:right">Total Wages (₦)</th>
                <th style="text-align:right">Share (%)</th>
            </tr>
        </thead>"""

    rows = [
        f"<tr><td>{r.get('department','—')}</td>"
        f"<td style='text-align:right'>{r.get('headcount',0)}</td>"
        f"<td style='text-align:right'>{r.get('total_wages',0):,.0f}</td>"
        f"<td style='text-align:right'>{r.get('percentage',0.0)}</td></tr>"
        for r in dept_rows
    ]

    # First page — summary cards + first chunk of table
    summary_html = f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Wage Bill Analysis</h1>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{total_bill:,.0f}</div>
                    <div class="reliability-kpi-label">Total Wage Bill</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{avg_salary:,.0f}</div>
                    <div class="reliability-kpi-label">Average Salary</div>
                </div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>"""

    table_html, table_pages = _paginate_table(
        rows, header_html, 'Wage Bill by Department', context, page_number + 1
    )

    return summary_html + table_html, 1 + table_pages


def render_department_headcount(data, context, page_number):
    """Render department headcount as a paginated table."""
    rows_data = data if isinstance(data, list) else []

    header_html = """
        <thead>
            <tr>
                <th>Department</th>
                <th style="text-align:right">Headcount</th>
                <th style="text-align:right">Share (%)</th>
            </tr>
        </thead>"""

    rows = [
        f"<tr><td>{r.get('department','—')}</td>"
        f"<td style='text-align:right'>{r.get('total',0)}</td>"
        f"<td style='text-align:right'>{r.get('percentage',0.0)}</td></tr>"
        for r in rows_data
    ]

    return _paginate_table(rows, header_html, 'Department Headcount', context, page_number)


def render_attrition_analysis(data, context, page_number):
    """Render attrition summary cards + department breakdown table."""
    total_exits  = data.get('total_exits', 0)
    attr_rate    = data.get('attrition_rate', 0.0)
    dept_rows    = data.get('by_department', [])

    header_html = """
        <thead>
            <tr>
                <th>Department</th>
                <th style="text-align:right">Exits</th>
            </tr>
        </thead>"""

    rows = [
        f"<tr><td>{r.get('department','—')}</td>"
        f"<td style='text-align:right'>{r.get('exits',0)}</td></tr>"
        for r in dept_rows
    ]

    summary_html = f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Attrition Analysis</h1>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{total_exits}</div>
                    <div class="reliability-kpi-label">Total Exits<br/>This Period</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{attr_rate}%</div>
                    <div class="reliability-kpi-label">Attrition<br/>Rate</div>
                </div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>"""

    if rows:
        table_html, table_pages = _paginate_table(
            rows, header_html, 'Attrition by Department', context, page_number + 1
        )
        return summary_html + table_html, 1 + table_pages

    return summary_html


def render_recruitment_summary(data, context, page_number):
    """Render recruitment summary: new hires by department and grade."""
    total_hires  = data.get('total_new_hires', 0)
    dept_rows    = data.get('by_department', [])
    grade_rows   = data.get('by_grade', [])

    dept_row_html = "".join(
        f"<tr><td>{r.get('department','—')}</td><td style='text-align:right'>{r.get('count',0)}</td></tr>"
        for r in dept_rows
    )
    grade_row_html = "".join(
        f"<tr><td>{r.get('grade') or '—'}</td><td style='text-align:right'>{r.get('count',0)}</td></tr>"
        for r in grade_rows
    )

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Recruitment Summary</h1>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{total_hires}</div>
                    <div class="reliability-kpi-label">New Hires<br/>This Period</div>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;">
                <div>
                    <h2 style="font-size:13px;margin-bottom:8px;">By Department</h2>
                    <div class="table-container">
                        <table>
                            <thead><tr><th>Department</th><th style="text-align:right">Hires</th></tr></thead>
                            <tbody>{dept_row_html}</tbody>
                        </table>
                    </div>
                </div>
                <div>
                    <h2 style="font-size:13px;margin-bottom:8px;">By Grade</h2>
                    <div class="table-container">
                        <table>
                            <thead><tr><th>Grade</th><th style="text-align:right">Hires</th></tr></thead>
                            <tbody>{grade_row_html}</tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


# =============================================================================
# COMMERCIAL SECTION RENDERERS
# =============================================================================

def render_commercial_overview(data, context, page_number):
    """Render commercial overview KPI cards."""
    total_c      = data.get('total_customers', 0)
    coverage     = data.get('coverage_rate', 0.0)
    billed_kwh   = data.get('total_billed_kwh', 0.0)
    billed_amt   = data.get('total_billed_amount', 0.0)
    arpu         = data.get('arpu', 0.0)
    atc          = data.get('atc_loss', 0)
    mdi_rev      = data.get('mdi_revenue', 0.0)
    mdni_rev     = data.get('mdni_revenue', 0.0)
    mdi_c        = data.get('mdi_count', 0)
    mdni_c       = data.get('mdni_count', 0)

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Commercial Overview</h1>
            <div class="reliability-highlight">
                <h2>Commercial Performance Summary</h2>
                <p>Billing, coverage, and revenue metrics for the reporting period</p>
            </div>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{total_c:,}</div>
                    <div class="reliability-kpi-label">Total<br/>Customers</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{coverage}%</div>
                    <div class="reliability-kpi-label">Reading<br/>Coverage</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{billed_kwh:,.0f}</div>
                    <div class="reliability-kpi-label">Total Billed<br/>(kWh)</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{billed_amt:,.0f}</div>
                    <div class="reliability-kpi-label">Total Billed<br/>Amount</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{float(arpu):,.0f}</div>
                    <div class="reliability-kpi-label">ARPU</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{atc if atc is not None else '—'}{'%' if atc is not None else ''}</div>
                    <div class="reliability-kpi-label">AT&amp;C<br/>Loss</div>
                </div>
            </div>
            <h2 style="margin:16px 0 8px;font-size:13px;">MDI vs MDNI Split</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Type</th>
                            <th style="text-align:right">Customers</th>
                            <th style="text-align:right">Revenue (₦)</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr><td>MDI</td><td style="text-align:right">{mdi_c:,}</td><td style="text-align:right">{mdi_rev:,.0f}</td></tr>
                        <tr><td>MDNI</td><td style="text-align:right">{mdni_c:,}</td><td style="text-align:right">{mdni_rev:,.0f}</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_commercial_coverage(data, context, page_number):
    """Render reading coverage detail with estimated revenue at risk."""
    total    = data.get('total_customers', 0)
    read     = data.get('customers_read', 0)
    unread   = data.get('customers_unread', 0)
    rate     = data.get('coverage_rate', 0.0)
    billed   = data.get('total_billed_amount', 0.0)
    est_rev  = data.get('estimated_revenue', 0.0)
    est_kwh  = data.get('estimated_kwh', 0.0)

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Reading Coverage</h1>
            <div class="reliability-highlight">
                <h2>Meter Reading Coverage</h2>
                <p>Customer reading status and estimated revenue at risk from unread meters</p>
            </div>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{total:,}</div>
                    <div class="reliability-kpi-label">Total<br/>Customers</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{read:,}</div>
                    <div class="reliability-kpi-label">Customers<br/>Read</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{unread:,}</div>
                    <div class="reliability-kpi-label">Customers<br/>Unread</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{rate}%</div>
                    <div class="reliability-kpi-label">Coverage<br/>Rate</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{billed:,.0f}</div>
                    <div class="reliability-kpi-label">Actual Billed<br/>Revenue</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{est_rev:,.0f}</div>
                    <div class="reliability-kpi-label">Estimated Revenue<br/>at Risk</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{est_kwh:,.0f}</div>
                    <div class="reliability-kpi-label">Estimated Unbilled<br/>(kWh)</div>
                </div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_commercial_energy(data, context, page_number):
    """Render energy analysis: delivered vs consumed vs billed + AT&C."""
    delivered   = data.get('energy_delivered_mwh', 0.0)
    mode        = data.get('energy_delivered_mode', '—')
    consumed    = data.get('energy_consumed_kwh', 0.0)
    billed_kwh  = data.get('total_billed_kwh', 0.0)
    efficiency  = data.get('billing_efficiency', 0)
    atc         = data.get('atc_loss', 0)

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Energy Analysis</h1>
            <div class="reliability-highlight">
                <h2>Energy Flow &amp; Losses</h2>
                <p>Energy delivered, consumed, and billed with AT&amp;C loss analysis</p>
            </div>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{delivered:,.1f}</div>
                    <div class="reliability-kpi-label">Energy Delivered<br/>(MWh) [{mode}]</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{consumed:,.0f}</div>
                    <div class="reliability-kpi-label">Energy Consumed<br/>(kWh)</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{billed_kwh:,.0f}</div>
                    <div class="reliability-kpi-label">Energy Billed<br/>(kWh)</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{efficiency if efficiency is not None else '—'}{'%' if efficiency is not None else ''}</div>
                    <div class="reliability-kpi-label">Billing<br/>Efficiency</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{atc if atc is not None else '—'}{'%' if atc is not None else ''}</div>
                    <div class="reliability-kpi-label">AT&amp;C<br/>Loss</div>
                </div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_revenue_by_feeder(data, context, page_number):
    """Render revenue by feeder as a paginated table."""
    rows_data = data if isinstance(data, list) else []

    header_html = """
        <thead>
            <tr>
                <th>Feeder</th>
                <th style="text-align:right">Readings</th>
                <th style="text-align:right">Billed (kWh)</th>
                <th style="text-align:right">Revenue (₦)</th>
            </tr>
        </thead>"""

    rows = [
        f"<tr><td>{r.get('feeder','—')}</td>"
        f"<td style='text-align:right'>{r.get('readings',0)}</td>"
        f"<td style='text-align:right'>{r.get('total_billed_kwh',0):,.0f}</td>"
        f"<td style='text-align:right'>{r.get('total_billed_amount',0):,.0f}</td></tr>"
        for r in rows_data
    ]

    return _paginate_table(rows, header_html, 'Revenue by Feeder', context, page_number)


def render_revenue_by_district(data, context, page_number):
    """Render revenue by district as a paginated table."""
    rows_data = data if isinstance(data, list) else []

    header_html = """
        <thead>
            <tr>
                <th>District</th>
                <th style="text-align:right">Billed (kWh)</th>
                <th style="text-align:right">Revenue (₦)</th>
            </tr>
        </thead>"""

    rows = [
        f"<tr><td>{r.get('district','—')}</td>"
        f"<td style='text-align:right'>{r.get('total_billed_kwh',0):,.0f}</td>"
        f"<td style='text-align:right'>{r.get('total_billed_amount',0):,.0f}</td></tr>"
        for r in rows_data
    ]

    return _paginate_table(rows, header_html, 'Revenue by District', context, page_number)


def render_customer_type_summary(data, context, page_number):
    """Render MDI vs MDNI summary cards."""
    mdi  = data.get('mdi', {})
    mdni = data.get('mdni', {})

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Customer Type Summary</h1>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{mdi.get('count',0):,}</div>
                    <div class="reliability-kpi-label">MDI<br/>Customers</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{mdi.get('total_billed_kwh',0):,.0f}</div>
                    <div class="reliability-kpi-label">MDI Billed<br/>(kWh)</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{mdi.get('total_billed_amount',0):,.0f}</div>
                    <div class="reliability-kpi-label">MDI<br/>Revenue</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{mdni.get('count',0):,}</div>
                    <div class="reliability-kpi-label">MDNI<br/>Customers</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">{mdni.get('total_billed_kwh',0):,.0f}</div>
                    <div class="reliability-kpi-label">MDNI Billed<br/>(kWh)</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{mdni.get('total_billed_amount',0):,.0f}</div>
                    <div class="reliability-kpi-label">MDNI<br/>Revenue</div>
                </div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


# =============================================================================
# COMMERCIAL COMPARISON SECTION RENDERERS
# =============================================================================

def render_commercial_comparison_summary(data, context, page_number):
    """KPI summary page for a customer consumption comparison."""
    totals   = data.get('totals', {})
    trend_d  = data.get('trend_distribution', {})
    cur_per  = data.get('current_period', {})
    prev_per = data.get('previous_period', {})
    ctype    = data.get('customer_type', 'all')
    scope    = data.get('scope', {})

    curr_kwh  = totals.get('current_consumption_kwh', 0) or 0
    prev_kwh  = totals.get('previous_consumption_kwh', 0) or 0
    var_pct   = totals.get('variance_pct')
    curr_amt  = totals.get('current_billed_amount', 0) or 0
    prev_amt  = totals.get('previous_billed_amount', 0) or 0
    bv        = totals.get('billing_variance', 0) or 0
    ret       = data.get('customers_returned', 0)

    var_str   = (f"{var_pct:+.1f}%" if var_pct is not None else "—")
    bv_color  = "#22c55e" if bv >= 0 else "#ef4444"

    scope_label = scope.get('label') or 'KEDCO-wide'
    ctype_label = {'MDI': 'MDI', 'MDNI': 'MDNI', 'all': 'MDI + MDNI'}.get(ctype, ctype)

    trend_rows = "".join(
        f'<tr><td>{label}</td><td style="text-align:right">{count}</td></tr>'
        for label, count in trend_d.items() if count > 0
    )

    var_color = "#22c55e" if (var_pct or 0) >= 0 else "#ef4444"
    bv_color  = "#22c55e" if bv >= 0 else "#ef4444"

    trend_colors = {
        'Major Positive Trend': '#15803d',
        'Positive Trend':       '#22c55e',
        'Moderated Trend':      '#f59e0b',
        'Declined Trend':       '#f97316',
        'Major Declined Trend': '#ef4444',
        'No Previous Data':     '#94a3b8',
        'No Current Data':      '#94a3b8',
    }
    trend_badges = "".join(
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'padding:7px 12px;border-radius:7px;margin-bottom:5px;'
        f'background:rgba(0,32,80,0.04);">'
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span style="width:8px;height:8px;border-radius:50%;background:{trend_colors.get(label,"#94a3b8")};display:inline-block;flex-shrink:0;"></span>'
        f'<span style="font-size:10.5px;color:#1e293b;">{label}</span>'
        f'</div>'
        f'<span style="font-size:12px;font-weight:700;color:#002050;">{count}</span>'
        f'</div>'
        for label, count in trend_d.items() if count > 0
    )

    def _kpi_card(label, current_val, prev_val, variance, variance_color, prefix=''):
        return f"""
        <div style="background:#f8fafc;border-radius:10px;padding:14px 16px;
                    border:1px solid rgba(0,32,80,0.08);">
            <div style="font-size:8px;font-weight:700;text-transform:uppercase;
                        letter-spacing:1px;color:#002050;opacity:0.55;margin-bottom:8px;">
                {label}
            </div>
            <div style="font-size:18px;font-weight:800;color:#002050;margin-bottom:4px;">
                {prefix}{current_val}
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:9px;color:#64748b;">prev {prefix}{prev_val}</span>
                <span style="font-size:9px;font-weight:700;color:{variance_color};">{variance}</span>
            </div>
        </div>"""

    kwh_var_str = var_str
    bv_str = f"&#8358;{abs(bv):,.0f}" + (" surplus" if bv >= 0 else " deficit")

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Consumption Comparison Summary</h1>

            <!-- Period header -->
            <div style="background:#002050;border-radius:10px;padding:14px 20px;
                        margin-bottom:16px;display:flex;align-items:center;gap:16px;">
                <div style="flex:1;">
                    <div style="font-size:8px;font-weight:700;letter-spacing:1px;
                                text-transform:uppercase;color:rgba(255,255,255,0.55);margin-bottom:3px;">
                        Current Period
                    </div>
                    <div style="font-size:12px;font-weight:700;color:#fff;">
                        {cur_per.get('from_date')} to {cur_per.get('to_date')}
                    </div>
                </div>
                <div style="color:rgba(255,255,255,0.4);font-size:18px;">vs</div>
                <div style="flex:1;text-align:right;">
                    <div style="font-size:8px;font-weight:700;letter-spacing:1px;
                                text-transform:uppercase;color:rgba(255,255,255,0.55);margin-bottom:3px;">
                        Previous Period
                    </div>
                    <div style="font-size:12px;font-weight:700;color:#fff;">
                        {prev_per.get('from_date')} to {prev_per.get('to_date')}
                    </div>
                </div>
                <div style="margin-left:16px;background:rgba(255,255,255,0.1);
                            border-radius:8px;padding:8px 14px;text-align:center;">
                    <div style="font-size:8px;font-weight:700;letter-spacing:1px;
                                text-transform:uppercase;color:rgba(255,255,255,0.55);margin-bottom:3px;">
                        {ctype_label} Customers
                    </div>
                    <div style="font-size:14px;font-weight:800;color:#fff;">{ret:,}</div>
                </div>
                <div style="background:rgba(255,255,255,0.1);border-radius:8px;
                            padding:8px 14px;text-align:center;">
                    <div style="font-size:8px;font-weight:700;letter-spacing:1px;
                                text-transform:uppercase;color:rgba(255,255,255,0.55);margin-bottom:3px;">
                        Scope
                    </div>
                    <div style="font-size:11px;font-weight:700;color:#fff;">{scope_label}</div>
                </div>
            </div>

            <!-- KPI cards row -->
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px;">
                {_kpi_card('Consumption (kWh)', f'{curr_kwh:,.0f}', f'{prev_kwh:,.0f}', kwh_var_str, var_color)}
                {_kpi_card('Billed Amount', f'{curr_amt:,.0f}', f'{prev_amt:,.0f}', ("+" if bv >= 0 else "") + f"&#8358;{bv:,.0f}", bv_color, prefix='&#8358;')}
                <div style="background:#f8fafc;border-radius:10px;padding:14px 16px;
                            border:1px solid rgba(0,32,80,0.08);">
                    <div style="font-size:8px;font-weight:700;text-transform:uppercase;
                                letter-spacing:1px;color:#002050;opacity:0.55;margin-bottom:8px;">
                        Trend Distribution
                    </div>
                    {trend_badges}
                </div>
            </div>

        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_commercial_comparison_table(data, context, page_number):
    """Paginated customer comparison table — returns (html, pages_used)."""
    customers = data.get('customers', [])

    header_html = """
        <thead>
            <tr>
                <th>Customer</th>
                <th>Account No</th>
                <th>Feeder</th>
                <th style="text-align:right">Curr kWh</th>
                <th style="text-align:right">Prev kWh</th>
                <th style="text-align:right">Var %</th>
                <th style="text-align:right">Billed Var &#8358;</th>
                <th>Trend</th>
            </tr>
        </thead>
    """

    TREND_COLORS = {
        'Major Positive Trend': '#15803d',
        'Positive Trend':       '#22c55e',
        'Moderated Trend':      '#f59e0b',
        'Declined Trend':       '#f97316',
        'Major Declined Trend': '#ef4444',
        'No Previous Data':     '#94a3b8',
    }

    rows = []
    for c in customers:
        vp     = c.get('variance_pct')
        bv     = c.get('billing_variance', 0) or 0
        trend  = c.get('trend', 'No Previous Data')
        color  = TREND_COLORS.get(trend, '#94a3b8')
        vp_str = f"{vp:+.1f}%" if vp is not None else "—"
        rows.append(
            f'<tr>'
            f'<td>{c.get("customer_name","—")}</td>'
            f'<td>{c.get("account_no","—")}</td>'
            f'<td>{c.get("feeder","—")}</td>'
            f'<td style="text-align:right">{c.get("current_consumption_kwh",0):,.1f}</td>'
            f'<td style="text-align:right">{c.get("previous_consumption_kwh",0):,.1f}</td>'
            f'<td style="text-align:right;color:{color};font-weight:600">{vp_str}</td>'
            f'<td style="text-align:right;color:{"#22c55e" if bv >= 0 else "#ef4444"}">{bv:+,.0f}</td>'
            f'<td style="color:{color};font-weight:600">{trend}</td>'
            f'</tr>'
        )

    return _paginate_table(
        rows_data=rows,
        header_html=header_html,
        page_title="Customer Comparison Detail",
        context=context,
        start_page=page_number,
        max_rows=14,
        landscape=True,
    )


def render_commercial_comparison_insights(data, context, page_number):
    """ARIA insights page for a comparison report."""
    insights = data.get('ai_insights', {})

    aria_badge = """
        <div style="display:inline-flex;align-items:center;gap:8px;
                    background:#002050;color:#fff;border-radius:8px;
                    padding:6px 14px;margin-bottom:14px;">
            <span style="font-size:13px;font-weight:800;letter-spacing:1px;">ARIA</span>
            <span style="font-size:9px;font-weight:400;opacity:0.75;letter-spacing:0.5px;">
                Automated Raven Intelligence Assistance
            </span>
        </div>"""

    if not insights or 'error' in insights:
        return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">ARIA Insights</h1>
            {aria_badge}
            <p style="margin-top:16px;color:#94a3b8;font-size:11px;">
                ARIA insights were not available for this report.
            </p>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """

    headline        = insights.get('headline', '')
    summary         = insights.get('summary', '')
    notable         = insights.get('notable_trends', [])
    watch_list      = insights.get('watch_list', [])
    recommendations = insights.get('recommendations', [])

    notable_html = "".join(
        f'<div style="display:flex;gap:8px;margin-bottom:7px;align-items:flex-start;">'
        f'<span style="min-width:6px;height:6px;margin-top:4px;border-radius:50%;'
        f'background:#002050;display:inline-block;flex-shrink:0;"></span>'
        f'<span style="font-size:10.5px;color:#1e293b;line-height:1.5;">{t}</span></div>'
        for t in notable
    )

    recs_html = "".join(
        f'<div style="display:flex;gap:8px;margin-bottom:7px;align-items:flex-start;">'
        f'<span style="min-width:6px;height:6px;margin-top:4px;border-radius:50%;'
        f'background:#1a6b3c;display:inline-block;flex-shrink:0;"></span>'
        f'<span style="font-size:10.5px;color:#1e293b;line-height:1.5;">{r}</span></div>'
        for r in recommendations
    )

    watch_rows = "".join(
        f'<tr>'
        f'<td style="width:28%"><strong style="font-size:10px;">{w.get("customer_name","—")}</strong><br/>'
        f'<span style="color:#64748b;font-size:9px;">{w.get("account_no","")}</span></td>'
        f'<td style="font-size:10px;line-height:1.4;color:#1e293b;">{w.get("reason","")}</td>'
        f'</tr>'
        for w in watch_list
    )

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">ARIA Insights</h1>
            {aria_badge}

            <!-- Headline card -->
            <div style="background:#002050;border-radius:10px;padding:16px 20px;margin-bottom:16px;">
                <div style="font-size:8px;font-weight:700;letter-spacing:1.5px;
                            text-transform:uppercase;color:rgba(255,255,255,0.6);margin-bottom:6px;">
                    Key Finding
                </div>
                <div style="font-size:12.5px;font-weight:700;color:#fff;line-height:1.5;">
                    {headline}
                </div>
                <div style="font-size:10px;color:rgba(255,255,255,0.75);margin-top:8px;line-height:1.5;">
                    {summary}
                </div>
            </div>

            <!-- 2-col layout -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">

                <!-- Left: Notable Trends + Recommendations -->
                <div>
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;
                                letter-spacing:1px;color:#002050;opacity:0.6;margin-bottom:8px;">
                        Notable Trends
                    </div>
                    <div style="margin-bottom:14px;">{notable_html}</div>

                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;
                                letter-spacing:1px;color:#002050;opacity:0.6;margin-bottom:8px;">
                        Recommendations
                    </div>
                    <div>{recs_html}</div>
                </div>

                <!-- Right: Watch List -->
                <div>
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;
                                letter-spacing:1px;color:#002050;opacity:0.6;margin-bottom:8px;">
                        Watch List
                    </div>
                    <div class="table-container">
                        <table>
                            <thead>
                                <tr>
                                    <th style="width:28%">Customer</th>
                                    <th>Reason</th>
                                </tr>
                            </thead>
                            <tbody>{watch_rows}</tbody>
                        </table>
                    </div>
                </div>

            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


# =============================================================================
# FINANCIAL SECTION RENDERERS
# =============================================================================

def render_financial_overview(data, context, page_number):
    """Render financial overview cost breakdown KPI cards."""
    opex     = data.get('opex', 0.0)
    hq_opex  = data.get('hq_opex', 0.0)
    salaries = data.get('salaries', 0.0)
    nbet     = data.get('nbet_invoice', 0.0)
    mo       = data.get('mo_invoice', 0.0)
    total    = data.get('total_cost', 0.0)

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Financial Overview</h1>
            <div class="reliability-highlight">
                <h2>Cost Summary</h2>
                <p>Expenditure breakdown for the reporting period</p>
            </div>
            <div class="reliability-kpi-grid">
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{opex:,.0f}</div>
                    <div class="reliability-kpi-label">District<br/>OPEX</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{hq_opex:,.0f}</div>
                    <div class="reliability-kpi-label">HQ<br/>OPEX</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{salaries:,.0f}</div>
                    <div class="reliability-kpi-label">Salaries</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{nbet:,.0f}</div>
                    <div class="reliability-kpi-label">NBET<br/>Invoice</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{mo:,.0f}</div>
                    <div class="reliability-kpi-label">Market Operator<br/>Invoice</div>
                </div>
                <div class="reliability-kpi-card">
                    <div class="reliability-kpi-value">₦{total:,.0f}</div>
                    <div class="reliability-kpi-label">Total<br/>Cost</div>
                </div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_opex_by_category(data, context, page_number):
    """Render OPEX by category as a paginated table."""
    rows_data = data if isinstance(data, list) else []

    header_html = """
        <thead>
            <tr>
                <th>Category</th>
                <th style="text-align:right">Transactions</th>
                <th style="text-align:right">Total (₦)</th>
                <th style="text-align:right">Share (%)</th>
            </tr>
        </thead>"""

    rows = [
        f"<tr><td>{r.get('category','—')}</td>"
        f"<td style='text-align:right'>{r.get('count',0)}</td>"
        f"<td style='text-align:right'>{r.get('total',0):,.0f}</td>"
        f"<td style='text-align:right'>{r.get('percentage',0.0)}</td></tr>"
        for r in rows_data
    ]

    return _paginate_table(rows, header_html, 'OPEX by Category', context, page_number)


def render_opex_by_district(data, context, page_number):
    """Render OPEX by district as a paginated table."""
    rows_data = data if isinstance(data, list) else []

    header_html = """
        <thead>
            <tr>
                <th>District</th>
                <th style="text-align:right">Transactions</th>
                <th style="text-align:right">Total (₦)</th>
                <th style="text-align:right">Share (%)</th>
            </tr>
        </thead>"""

    rows = [
        f"<tr><td>{r.get('district','—')}</td>"
        f"<td style='text-align:right'>{r.get('count',0)}</td>"
        f"<td style='text-align:right'>{r.get('total',0):,.0f}</td>"
        f"<td style='text-align:right'>{r.get('percentage',0.0)}</td></tr>"
        for r in rows_data
    ]

    return _paginate_table(rows, header_html, 'OPEX by District', context, page_number)


# =============================================================================
# SEGMENT / DISPATCH COMPLIANCE SECTION RENDERERS
# =============================================================================

_STATUS_COLORS = {
    'exceeding':    ('#e8f5e9', '#2e7d32', 'Exceeding'),
    'on_target':    ('#e3f2fd', '#1565c0', 'On Target'),
    'below_target': ('#fff3e0', '#e65100', 'Below Target'),
    'poor':         ('#fce4ec', '#ad1457', 'Poor'),
    'critical':     ('#ffebee', '#c62828', 'Critical'),
}


def _status_badge(status: str) -> str:
    bg, fg, label = _STATUS_COLORS.get(status, ('#f5f5f5', '#555', status.title()))
    return (
        f'<span style="display:inline-block;font-size:8px;font-weight:700;'
        f'padding:2px 7px;border-radius:4px;background:{bg};color:{fg};'
        f'white-space:nowrap;">{label}</span>'
    )


def render_segment_compliance_summary(data, context, page_number):
    """
    One-page compliance breakdown by MDI / Non-MDI Band A / Non-MDI Non-Band A.
    Shows total feeders, avg supply, avg % achieved, and status distribution bars.
    """
    segments = data if isinstance(data, list) else []

    STATUS_ORDER = ['exceeding', 'on_target', 'below_target', 'poor', 'critical']

    cards_html = ''
    for seg in segments:
        name       = seg.get('segment', '')
        total      = seg.get('total', 0)
        avg_s      = seg.get('avg_supply', 0)
        avg_pct    = seg.get('avg_pct_achieved', 0)
        total_nrg  = seg.get('total_energy_mwh', 0)

        status_rows = ''
        for st in STATUS_ORDER:
            sd = seg.get(st, {'count': 0, 'pct': 0})
            bg, fg, lbl = _STATUS_COLORS.get(st, ('#f5f5f5', '#555', st))
            bar_w = min(sd['pct'], 100)
            status_rows += f"""
            <div style="display:flex;align-items:center;margin-bottom:5px;">
                <div style="width:140px;font-size:9px;font-weight:600;color:{fg};">{lbl}</div>
                <div style="flex:1;background:#eff0f1;border-radius:4px;height:10px;
                            margin:0 8px;overflow:hidden;">
                    <div style="width:{bar_w}%;background:{fg};height:10px;border-radius:4px;"></div>
                </div>
                <div style="width:40px;font-size:9.5px;font-weight:700;text-align:right;color:{fg};">
                    {sd['pct']}%
                </div>
                <div style="width:28px;font-size:9px;opacity:0.55;text-align:right;">{sd['count']}</div>
            </div>"""

        cards_html += f"""
        <div style="flex:1;background:#f8f9fa;border-radius:12px;padding:16px 18px;
                    margin-right:10px;border:1px solid rgba(0,32,80,0.1);min-width:0;">
            <div style="font-size:11px;font-weight:700;color:#002050;margin-bottom:10px;
                        border-bottom:2px solid #002050;padding-bottom:6px;">{name}</div>
            <div style="display:flex;margin-bottom:12px;">
                <div style="flex:1;text-align:center;">
                    <div style="font-size:8px;font-weight:700;text-transform:uppercase;
                                opacity:0.5;margin-bottom:3px;">Feeders</div>
                    <div style="font-size:22px;font-weight:800;color:#002050;">{total}</div>
                </div>
                <div style="flex:1;text-align:center;">
                    <div style="font-size:8px;font-weight:700;text-transform:uppercase;
                                opacity:0.5;margin-bottom:3px;">Avg Supply</div>
                    <div style="font-size:22px;font-weight:800;color:#002050;">{avg_s}
                        <span style="font-size:10px;font-weight:400;opacity:0.6;"> hrs</span>
                    </div>
                </div>
                <div style="flex:1;text-align:center;">
                    <div style="font-size:8px;font-weight:700;text-transform:uppercase;
                                opacity:0.5;margin-bottom:3px;">Avg Achieved</div>
                    <div style="font-size:22px;font-weight:800;color:#002050;">{avg_pct}
                        <span style="font-size:10px;font-weight:400;opacity:0.6;">%</span>
                    </div>
                </div>
                <div style="flex:1;text-align:center;">
                    <div style="font-size:8px;font-weight:700;text-transform:uppercase;
                                opacity:0.5;margin-bottom:3px;">Energy Delivered</div>
                    <div style="font-size:16px;font-weight:800;color:#002050;">{total_nrg:,.1f}
                        <span style="font-size:9px;font-weight:400;opacity:0.6;"> MWh</span>
                    </div>
                </div>
            </div>
            {status_rows}
        </div>"""

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Compliance by Business Segment</h1>
            <div style="font-size:10px;opacity:0.6;margin-bottom:14px;">
                Targets: Band A = 20 hrs &nbsp;|&nbsp; Band B = 16 hrs &nbsp;|&nbsp;
                Band C = 12 hrs &nbsp;|&nbsp; Band D/E = 8 hrs &nbsp;|&nbsp;
                Segments derived from MDI customer connections
            </div>
            <div style="display:flex;overflow:hidden;">
                {cards_html}
                <div style="width:0;flex-shrink:0;"></div>
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>"""


def render_feeder_segment_compliance(data, context, page_number):
    """
    Paginated feeder dispatch compliance table grouped by segment.
    Columns: Feeder | Segment | Band | Target | Actual | Gap | % Achieved | Status
    """
    feeders = data if isinstance(data, list) else []

    header_html = """
        <colgroup>
            <col style="width:22%">
            <col style="width:15%">
            <col style="width:5%">
            <col style="width:9%">
            <col style="width:9%">
            <col style="width:7%">
            <col style="width:9%">
            <col style="width:12%">
            <col style="width:12%">
        </colgroup>
        <thead>
            <tr>
                <th>Feeder</th>
                <th>Segment</th>
                <th>Band</th>
                <th style="text-align:right;">Target (hrs)</th>
                <th style="text-align:right;">Actual (hrs)</th>
                <th style="text-align:right;">Gap</th>
                <th style="text-align:right;">% Achieved</th>
                <th style="text-align:right;">Energy (MWh)</th>
                <th style="text-align:center;">Status</th>
            </tr>
        </thead>"""

    current_segment = None
    row_strings = []
    for f in feeders:
        seg = f.get('segment', '')
        if seg != current_segment:
            current_segment = seg
            row_strings.append(f"""
        <tr style="background-color:#002050;">
            <td colspan="9" style="font-weight:700;font-size:10px;letter-spacing:1px;
                text-transform:uppercase;color:#ffffff;padding:6px 10px;">
                &#9658; {seg}
            </td>
        </tr>""")

        gap     = float(f.get('gap', 0) or 0)
        pct     = float(f.get('pct_achieved', 0) or 0)
        target  = float(f.get('target_hours', 0) or 0)
        actual  = float(f.get('hours_of_supply', 0) or 0)
        energy  = float(f.get('energy_delivered', 0) or 0)
        gap_str = f'+{gap:.2f}' if gap >= 0 else f'{gap:.2f}'
        gap_col = 'color:#2e7d32;font-weight:700' if gap >= 0 else 'color:#c62828;font-weight:700'

        row_strings.append(f"""
        <tr>
            <td>{f.get('name', '—')}</td>
            <td style="font-size:9px;opacity:0.75;">{seg}</td>
            <td style="text-align:center;">{f.get('band', '—')}</td>
            <td style="text-align:right;">{target:.1f}</td>
            <td style="text-align:right;">{actual:.2f}</td>
            <td style="text-align:right;{gap_col}">{gap_str}</td>
            <td style="text-align:right;font-weight:700;">{pct:.1f}%</td>
            <td style="text-align:right;">{energy:,.2f}</td>
            <td style="text-align:center;">{_status_badge(f.get('status', 'critical'))}</td>
        </tr>""")

    return _paginate_table(row_strings, header_html, 'Feeder Compliance by Segment',
                           context, page_number, max_rows=15)





def render_energy_by_segment_pl(data, context, page_number):
    """P&L energy split — MDI vs Non-MDI with GWh amounts and % share."""
    total    = float(data.get('total_energy_mwh', 0) or 0)
    segments = data.get('segments', [])

    # Build visual bars for each segment
    bars_html = ''
    seg_colors = ['#1565c0', '#2e7d32', '#e65100', '#6a1b9a']
    for i, seg in enumerate(segments):
        color   = seg_colors[i % len(seg_colors)]
        bar_w   = min(seg['pct'], 100)
        bars_html += f"""
        <div style="margin-bottom:18px;">
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px;">
                <div style="font-size:12px;font-weight:700;color:#002050;">{seg['label']}</div>
                <div style="font-size:11px;font-weight:600;color:{color};">{seg['pct']}%</div>
            </div>
            <div style="background:#eff0f1;border-radius:6px;height:20px;overflow:hidden;">
                <div style="width:{bar_w}%;background:{color};height:20px;border-radius:6px;
                             display:flex;align-items:center;padding-left:8px;">
                    <span style="font-size:9px;font-weight:700;color:#fff;white-space:nowrap;">
                        {seg['energy_mwh']:,.2f} MWh
                    </span>
                </div>
            </div>
            <div style="font-size:9px;opacity:0.55;margin-top:3px;">{seg['feeders']} feeders</div>
        </div>"""

    # Total KPI strip
    kpi_strip = f"""
    <div style="display:flex;background:#f0f4f8;border-radius:12px;padding:14px 18px;
                margin-bottom:20px;">
        <div style="flex:1;text-align:center;border-right:1px solid rgba(0,32,80,0.15);">
            <div style="font-size:8px;font-weight:700;text-transform:uppercase;opacity:0.5;margin-bottom:4px;">
                Total Energy Delivered</div>
            <div style="font-size:26px;font-weight:800;color:#002050;">{total:,.2f}
                <span style="font-size:11px;font-weight:400;opacity:0.6;"> MWh</span>
            </div>
        </div>
        <div style="flex:1;text-align:center;border-right:1px solid rgba(0,32,80,0.15);">
            <div style="font-size:8px;font-weight:700;text-transform:uppercase;opacity:0.5;margin-bottom:4px;">
                MDI Energy</div>
            <div style="font-size:26px;font-weight:800;color:#1565c0;">{data.get('mdi_pct', 0)}%</div>
        </div>
        <div style="flex:1;text-align:center;">
            <div style="font-size:8px;font-weight:700;text-transform:uppercase;opacity:0.5;margin-bottom:4px;">
                Non-MDI Energy</div>
            <div style="font-size:26px;font-weight:800;color:#2e7d32;">{data.get('mdni_pct', 0)}%</div>
        </div>
    </div>"""

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Energy Delivered by P&amp;L Segment</h1>
            {kpi_strip}
            <div style="max-width:500px;">
                {bars_html}
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>"""


def render_segment_voltage_energy(data, context, page_number):
    """Energy by segment × voltage (MDI 33kV / MDI 11kV / Non-MDI 33kV / Non-MDI 11kV)."""
    rows        = data.get('rows', [])
    total       = float(data.get('total_energy_mwh', 0) or 0)
    mdi_total   = float(data.get('mdi_total', 0) or 0)
    mdni_total  = float(data.get('mdni_total', 0) or 0)

    COLOR_MAP = {
        'MDI 33kV':     '#1565c0',
        'MDI 11kV':     '#42a5f5',
        'Non-MDI 33kV': '#2e7d32',
        'Non-MDI 11kV': '#81c784',
    }

    bars_html = ''
    for row in rows:
        color = COLOR_MAP.get(row['label'], '#888')
        bar_w = min(row['pct'], 100)
        bars_html += f"""
        <div style="margin-bottom:14px;">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                <div style="font-size:11px;font-weight:700;color:#002050;">{row['label']}</div>
                <div style="font-size:11px;font-weight:600;color:{color};">
                    {row['energy_mwh']:,.2f} MWh &nbsp; ({row['pct']}%)
                </div>
            </div>
            <div style="background:#eff0f1;border-radius:5px;height:16px;overflow:hidden;">
                <div style="width:{bar_w}%;background:{color};height:16px;border-radius:5px;"></div>
            </div>
        </div>"""

    summary = f"""
    <div style="display:flex;gap:0;margin-bottom:20px;">
        <div style="flex:1;background:#e3f2fd;border-radius:10px;padding:14px 16px;margin-right:10px;">
            <div style="font-size:9px;font-weight:700;text-transform:uppercase;opacity:0.6;margin-bottom:4px;">MDI Total</div>
            <div style="font-size:22px;font-weight:800;color:#1565c0;">{mdi_total:,.2f}
                <span style="font-size:10px;font-weight:400;opacity:0.6;"> MWh</span>
            </div>
        </div>
        <div style="flex:1;background:#e8f5e9;border-radius:10px;padding:14px 16px;margin-right:10px;">
            <div style="font-size:9px;font-weight:700;text-transform:uppercase;opacity:0.6;margin-bottom:4px;">Non-MDI Total</div>
            <div style="font-size:22px;font-weight:800;color:#2e7d32;">{mdni_total:,.2f}
                <span style="font-size:10px;font-weight:400;opacity:0.6;"> MWh</span>
            </div>
        </div>
        <div style="flex:1;background:#f0f4f8;border-radius:10px;padding:14px 16px;">
            <div style="font-size:9px;font-weight:700;text-transform:uppercase;opacity:0.6;margin-bottom:4px;">Grand Total</div>
            <div style="font-size:22px;font-weight:800;color:#002050;">{total:,.2f}
                <span style="font-size:10px;font-weight:400;opacity:0.6;"> MWh</span>
            </div>
        </div>
    </div>"""

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Energy by Segment &amp; Voltage Level</h1>
            {summary}
            <div style="max-width:520px;">
                {bars_html}
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>"""


def render_energy_md_nmd_mix(data, context, page_number):
    """MD vs NMD % energy mix — target (60/40) vs actual."""
    md_target   = float(data.get('md_target_pct', 60) or 60)
    nmd_target  = float(data.get('nmd_target_pct', 40) or 40)
    md_actual   = float(data.get('md_actual_pct', 0) or 0)
    nmd_actual  = float(data.get('nmd_actual_pct', 0) or 0)
    md_gap      = float(data.get('md_gap_pct', 0) or 0)
    md_energy   = float(data.get('md_energy_mwh', 0) or 0)
    nmd_energy  = float(data.get('nmd_energy_mwh', 0) or 0)
    total       = float(data.get('total_energy_mwh', 0) or 0)

    gap_color = '#2e7d32' if md_gap >= 0 else '#c62828'
    gap_sign  = '+' if md_gap >= 0 else ''

    def _bar(label, target_pct, actual_pct, color):
        return f"""
        <div style="margin-bottom:22px;">
            <div style="font-size:12px;font-weight:700;color:#002050;margin-bottom:8px;">{label}</div>
            <div style="margin-bottom:6px;">
                <div style="display:flex;justify-content:space-between;font-size:9px;opacity:0.6;margin-bottom:3px;">
                    <span>Target ({target_pct:.0f}%)</span>
                </div>
                <div style="background:#eff0f1;border-radius:5px;height:12px;overflow:hidden;">
                    <div style="width:{min(target_pct,100)}%;background:rgba(0,32,80,0.2);
                                height:12px;border-radius:5px;"></div>
                </div>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;font-size:9px;margin-bottom:3px;">
                    <span style="font-weight:600;color:{color};">Actual ({actual_pct:.1f}%)</span>
                    <span style="color:{color};font-weight:700;">{actual_pct:,.2f} MWh</span>
                </div>
                <div style="background:#eff0f1;border-radius:5px;height:16px;overflow:hidden;">
                    <div style="width:{min(actual_pct,100)}%;background:{color};
                                height:16px;border-radius:5px;"></div>
                </div>
            </div>
        </div>"""

    md_bar  = _bar('MD (MDI)', md_target,  md_actual,  '#1565c0')
    nmd_bar = _bar('NMD (Non-MDI)', nmd_target, nmd_actual, '#2e7d32')

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">MD vs NMD Energy Mix</h1>
            <div style="display:flex;gap:0;margin-bottom:22px;">
                <div style="flex:1;background:#f0f4f8;border-radius:10px;padding:14px 16px;margin-right:10px;text-align:center;">
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;opacity:0.5;margin-bottom:4px;">Total Energy</div>
                    <div style="font-size:24px;font-weight:800;color:#002050;">{total:,.2f}
                        <span style="font-size:10px;font-weight:400;opacity:0.6;"> MWh</span>
                    </div>
                </div>
                <div style="flex:1;background:#e3f2fd;border-radius:10px;padding:14px 16px;margin-right:10px;text-align:center;">
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;opacity:0.5;margin-bottom:4px;">MD Actual</div>
                    <div style="font-size:24px;font-weight:800;color:#1565c0;">{md_actual:.1f}%</div>
                    <div style="font-size:9px;opacity:0.5;">Target: {md_target:.0f}%</div>
                </div>
                <div style="flex:1;background:#e8f5e9;border-radius:10px;padding:14px 16px;margin-right:10px;text-align:center;">
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;opacity:0.5;margin-bottom:4px;">NMD Actual</div>
                    <div style="font-size:24px;font-weight:800;color:#2e7d32;">{nmd_actual:.1f}%</div>
                    <div style="font-size:9px;opacity:0.5;">Target: {nmd_target:.0f}%</div>
                </div>
                <div style="flex:1;background:#f0f4f8;border-radius:10px;padding:14px 16px;text-align:center;">
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;opacity:0.5;margin-bottom:4px;">MD Gap vs Target</div>
                    <div style="font-size:24px;font-weight:800;color:{gap_color};">{gap_sign}{md_gap:.1f}%</div>
                </div>
            </div>
            <div style="max-width:500px;">
                {md_bar}
                {nmd_bar}
            </div>
            <div style="font-size:9px;opacity:0.5;margin-top:12px;">
                Target mix: MD 60% / NMD 40% &nbsp;|&nbsp; Segments derived from MDI customer connections
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>"""


def render_segment_compliance_trend(data, context, page_number):
    """
    SVG line chart — daily % of feeders on-target-or-better per segment.
    """
    trend    = data.get('trend', [])
    segments = data.get('segments', [])

    if not trend:
        content = f"""
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Segment Compliance Trend</h1>
            <p style="margin-top:24px;opacity:0.5;">No daily data available for this period.</p>"""
        footer_logo = context.get('footer_logo_url', '')
        return f'<div class="page"><div class="page-content">{content}</div><div class="footer"><img src="{footer_logo}" alt="Powered by EMRC"/><div class="page-number">{page_number}</div></div></div>'

    SEG_COLORS = {
        'MDI':                '#1565c0',
        'Non-MDI Band A':     '#2e7d32',
        'Non-MDI Non-Band A': '#e65100',
    }

    W, H = 760, 180
    ml, mr, mt, mb = 42, 15, 12, 32
    pw = W - ml - mr
    ph = H - mt - mb
    n  = len(trend)

    def xp(i):
        return ml + (i / (n - 1)) * pw if n > 1 else ml + pw / 2

    def yp(v):
        return mt + ph - (v / 100) * ph

    # Y gridlines
    ticks_svg = ''
    for k in range(6):
        tv = k * 20
        ty = yp(tv)
        ticks_svg += (
            f'<line x1="{ml}" y1="{ty:.1f}" x2="{W - mr}" y2="{ty:.1f}" '
            f'stroke="#e4e8ef" stroke-width="1"/>'
            f'<text x="{ml - 4}" y="{ty:.1f}" text-anchor="end" dominant-baseline="middle" '
            f'fill="#888" font-size="9" font-family="Arial,sans-serif">{tv}%</text>'
        )

    # X labels
    step = max(1, n // 10)
    xlabels_svg = ''
    for i in range(0, n, step):
        label = str(trend[i].get('date', ''))[-5:]
        xlabels_svg += (
            f'<text x="{xp(i):.1f}" y="{mt + ph + 14}" text-anchor="middle" '
            f'fill="#888" font-size="9" font-family="Arial,sans-serif">{label}</text>'
        )

    # Lines per segment
    lines_svg = ''
    legend_svg = ''
    lx = W - mr + 8
    for li, seg in enumerate(segments):
        color  = SEG_COLORS.get(seg, '#888')
        values = [d.get(seg) for d in trend]
        valid  = [v for v in values if v is not None]
        if not valid:
            continue
        pts = ' '.join(
            f"{xp(i):.1f},{yp(v):.1f}"
            for i, v in enumerate(values) if v is not None
        )
        ly = mt + 10 + li * 16
        lines_svg  += (
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        legend_svg += (
            f'<line x1="{lx}" y1="{ly}" x2="{lx + 16}" y2="{ly}" '
            f'stroke="{color}" stroke-width="2.2"/>'
            f'<text x="{lx + 20}" y="{ly + 3}" fill="#444" font-size="9" '
            f'font-family="Arial,sans-serif" font-weight="600">{seg}</text>'
        )

    svg = (
        f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block;overflow:visible;" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{ticks_svg}'
        f'<line x1="{ml}" y1="{mt + ph}" x2="{W - mr}" y2="{mt + ph}" stroke="#ccc" stroke-width="1"/>'
        f'{lines_svg}'
        f'{xlabels_svg}'
        f'{legend_svg}'
        f'</svg>'
    )

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>
            <h1 class="page-title">Segment Compliance Trend</h1>
            <div style="font-size:10px;opacity:0.6;margin-bottom:10px;">
                % of feeders achieving &ge;95% of band supply target per day, by segment
            </div>
            <div style="background:#f8fafc;border-radius:12px;padding:12px 14px;">
                {svg}
            </div>
        </div>
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>"""



# =============================================================================
# DSO COMPLIANCE SECTION RENDERERS
# =============================================================================

def render_dso_compliance_overview(data, context, page_number):
    """Render DSO compliance KPI summary — compliant vs non-compliant stations."""
    total     = data.get('total_stations', 0)
    compliant = data.get('compliant_count', 0)
    non_comp  = data.get('non_compliant_count', 0)
    rate      = data.get('compliance_rate', 0.0)
    period    = data.get('period', '')

    # Simple inline donut-like SVG for compliance rate
    pct = min(max(rate, 0), 100)
    arc_color = '#2e7d32' if pct >= 80 else ('#f57c00' if pct >= 50 else '#c62828')

    # SVG donut chart (pure SVG, no JS)
    r = 45
    cx, cy = 60, 60
    circumference = 2 * 3.14159 * r
    dash = circumference * pct / 100
    gap  = circumference - dash

    donut_svg = f"""
<svg width="120" height="120" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#e0e5ee" stroke-width="14"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{arc_color}" stroke-width="14"
          stroke-dasharray="{dash:.1f} {gap:.1f}"
          stroke-dashoffset="{circumference / 4:.1f}"
          stroke-linecap="round"/>
  <text x="{cx}" y="{cy - 5}" text-anchor="middle" font-size="15" font-weight="700"
        fill="#002050" font-family="Arial,sans-serif">{pct:.0f}%</text>
  <text x="{cx}" y="{cy + 12}" text-anchor="middle" font-size="9" fill="#888"
        font-family="Arial,sans-serif">Compliant</text>
</svg>"""

    # Network-wide submission totals
    net_exp_h   = data.get('net_exp_hourly', 0)
    net_dso_h   = data.get('net_dso_hourly', 0)
    net_adm_h   = data.get('net_admin_hourly', 0)
    net_exp_e   = data.get('net_exp_energy', 0)
    net_dso_e   = data.get('net_dso_energy', 0)
    net_adm_e   = data.get('net_admin_energy', 0)

    dso_h_pct  = round(net_dso_h / net_exp_h * 100, 1) if net_exp_h else 0
    adm_h_pct  = round(net_adm_h / net_exp_h * 100, 1) if net_exp_h else 0
    dso_e_pct  = round(net_dso_e / net_exp_e * 100, 1) if net_exp_e else 0
    adm_e_pct  = round(net_adm_e / net_exp_e * 100, 1) if net_exp_e else 0

    def _bar(filled_pct, color):
        filled = min(filled_pct, 100)
        return (
            f'<div style="background:#dde3ed;border-radius:4px;height:7px;margin-top:5px;">'
            f'<div style="width:{filled}%;background:{color};height:7px;border-radius:4px;"></div>'
            f'</div>'
        )

    def _submission_card(title, expected, dso_count, dso_pct, admin_count, admin_pct):
        return f"""
        <div style="flex:1;background:#f0f4f8;border-radius:12px;padding:16px;margin-right:10px;">
            <div style="font-size:9px;font-weight:700;text-transform:uppercase;
                        letter-spacing:0.5px;color:#002050;opacity:0.6;margin-bottom:10px;">{title}</div>
            <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
                <span style="font-size:9px;opacity:0.5;">Expected</span>
                <span style="font-size:10px;font-weight:700;color:#002050;">{expected:,}</span>
            </div>
            <div style="display:flex;justify-content:space-between;margin-bottom:2px;">
                <span style="font-size:9px;color:#002050;font-weight:600;">DSO Submitted</span>
                <span style="font-size:10px;font-weight:700;color:#002050;">{dso_count:,} &nbsp;({dso_pct}%)</span>
            </div>
            {_bar(dso_pct, '#002050')}
            <div style="display:flex;justify-content:space-between;margin-top:8px;margin-bottom:2px;">
                <span style="font-size:9px;color:#8b1a1a;font-weight:600;">Admin Override</span>
                <span style="font-size:10px;font-weight:700;color:#8b1a1a;">{admin_count:,} &nbsp;({admin_pct}%)</span>
            </div>
            {_bar(admin_pct, '#8b1a1a')}
        </div>"""

    hourly_card = _submission_card(
        'Hourly Load Submissions', net_exp_h, net_dso_h, dso_h_pct, net_adm_h, adm_h_pct)
    energy_card = _submission_card(
        'Energy Reading Submissions', net_exp_e, net_dso_e, dso_e_pct, net_adm_e, adm_e_pct)

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">DSO Compliance Overview</h1>

            <div style="font-size:11px;opacity:0.65;margin-bottom:14px;">
                Reporting period: {period} &nbsp;|&nbsp; Compliance threshold: 80% DSO submissions received
            </div>

            <!-- Row 1: Donut + station KPI cards -->
            <div style="display:flex;align-items:center;margin-bottom:16px;">
                <div style="flex-shrink:0;margin-right:28px;">{donut_svg}</div>
                <div style="display:flex;flex:1;gap:0;">
                    <div style="flex:1;background:#f0f4f8;border-radius:12px;padding:16px;text-align:center;margin-right:10px;">
                        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.6;margin-bottom:6px;">Total Stations</div>
                        <div style="font-size:30px;font-weight:700;color:#002050;">{total}</div>
                        <div style="font-size:9px;opacity:0.5;">with 11kV feeders</div>
                    </div>
                    <div style="flex:1;background:#e8f5e9;border-radius:12px;padding:16px;text-align:center;margin-right:10px;">
                        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.7;margin-bottom:6px;color:#2e7d32;">Compliant</div>
                        <div style="font-size:30px;font-weight:700;color:#2e7d32;">{compliant}</div>
                        <div style="font-size:9px;opacity:0.5;">&#8805; 80% DSO submissions</div>
                    </div>
                    <div style="flex:1;background:#fce4ec;border-radius:12px;padding:16px;text-align:center;">
                        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.7;margin-bottom:6px;color:#c62828;">Non-Compliant</div>
                        <div style="font-size:30px;font-weight:700;color:#c62828;">{non_comp}</div>
                        <div style="font-size:9px;opacity:0.5;">below threshold</div>
                    </div>
                </div>
            </div>

            <!-- Row 2: Submission breakdown cards -->
            <div style="font-size:11px;font-weight:600;color:#002050;margin-bottom:8px;">
                Network-Wide Submission Summary
            </div>
            <div style="display:flex;margin-bottom:16px;">
                {hourly_card}
                {energy_card}
            </div>
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_dso_compliance_table(data, context, page_number):
    """Render per-station DSO compliance breakdown table (paginated)."""
    stations = data.get('stations', [])

    legend_bar = (
        '<div style="display:flex;align-items:center;gap:20px;margin-bottom:8px;'
        'font-size:9px;font-weight:600;color:#444;">'
        '<span style="color:#555;font-weight:400;margin-right:4px;">Status legend:</span>'
        '<span><span style="color:#2e7d32;font-size:13px;">&#9679;</span>&nbsp;Compliant '
        '(Hourly &amp; Energy &ge;80%)</span>'
        '<span><span style="color:#f57c00;font-size:13px;">&#9679;</span>&nbsp;Partial '
        '(one metric &ge;80%)</span>'
        '<span><span style="color:#c62828;font-size:13px;">&#9679;</span>&nbsp;Non-Compliant '
        '(both below 80%)</span>'
        '</div>'
    )

    # 7 data columns — no energy DSO/Admin (too noisy); status is a dot only
    header_html = f"""
        <colgroup>
            <col style="width:24%">
            <col style="width:9%">
            <col style="width:6%">
            <col style="width:13%">
            <col style="width:11%">
            <col style="width:10%">
            <col style="width:13%">
            <col style="width:8%">
        </colgroup>
        <thead>
            <tr>
                <th rowspan="2" style="padding:4px 6px;">Station</th>
                <th rowspan="2" style="text-align:center;padding:4px 6px;">Type</th>
                <th rowspan="2" style="text-align:right;padding:4px 6px;">Fdrs</th>
                <th colspan="3" style="text-align:center;padding:4px 6px;
                    border-bottom:1px solid rgba(255,255,255,0.2);">Hourly Load</th>
                <th rowspan="2" style="text-align:right;padding:4px 6px;">Energy Comp%</th>
                <th rowspan="2" style="text-align:center;padding:4px 6px;">&#11044;</th>
            </tr>
            <tr>
                <th style="text-align:right;padding:4px 6px;">Comp%</th>
                <th style="text-align:right;padding:4px 6px;">DSO</th>
                <th style="text-align:right;padding:4px 6px;">Admin</th>
            </tr>
        </thead>"""

    row_strings = []
    for s in stations:
        h_pct = s['hourly_pct']
        e_pct = s['energy_pct']
        h_color = '#2e7d32' if h_pct >= 80 else ('#f57c00' if h_pct >= 50 else '#c62828')
        e_color = '#2e7d32' if e_pct >= 80 else ('#f57c00' if e_pct >= 50 else '#c62828')

        both_compliant = s['is_compliant']
        one_compliant  = (h_pct >= 80) != (e_pct >= 80)
        dot_color = '#2e7d32' if both_compliant else ('#f57c00' if one_compliant else '#c62828')

        p = 'padding:3px 6px;'
        stype = 'Trans.' if 'trans' in s['station_type'].lower() else 'Inj.'
        row_strings.append(f"""
        <tr>
            <td style="{p}font-weight:600;">{s['station_name']}</td>
            <td style="{p}text-align:center;font-size:9px;">{stype}</td>
            <td style="{p}text-align:right;">{s['feeder_count']}</td>
            <td style="{p}text-align:right;font-weight:700;color:{h_color};">{h_pct}%</td>
            <td style="{p}text-align:right;">{s['dso_hourly']:,}</td>
            <td style="{p}text-align:right;opacity:0.75;">{s['admin_hourly']:,}</td>
            <td style="{p}text-align:right;font-weight:700;color:{e_color};">{e_pct}%</td>
            <td style="{p}text-align:center;font-size:14px;color:{dot_color};">&#9679;</td>
        </tr>""")

    return _paginate_table(
        row_strings, header_html,
        'DSO Submission Compliance by Station',
        context, page_number, max_rows=17, landscape=True,
        note_html=legend_bar,
    )


def render_back_page(context):
    """Render the always-last branded back/closing page."""
    company_name = context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')
    report_date  = context.get('report_date', '')
    return f"""
    <div class="cover-page" style="justify-content: center; align-items: center; text-align: center;">
        <div class="cover-left-accent"></div>
        <div class="cover-body" style="justify-content: center; align-items: center;">

            <div class="cover-logo-wrap" style="margin-bottom: 40px;">
                <img src="{context.get('logo_gray_url', '')}" alt="Company Logo" />
            </div>

            <div style="margin-bottom: 32px;">
                <div class="cover-eyebrow" style="text-align:center; margin-bottom: 20px;">End of Report</div>
                <h2 style="font-size:36px; font-weight:800; color:#ffffff; text-transform:uppercase;
                            letter-spacing:-0.5px; margin:0 0 8px 0; line-height:1.1;">
                    {company_name}
                </h2>
                <div class="cover-accent-rule" style="margin: 24px auto;"></div>
                <div style="font-size:13px; color:#ffffff; opacity:0.55; letter-spacing:1.5px;
                             text-transform:uppercase; font-weight:600;">
                    {report_date}
                </div>
            </div>

            <div style="font-size:11px; color:#ffffff; opacity:0.35; text-transform:uppercase;
                         letter-spacing:2px; margin-top: 60px;">
                Powered by RAVEN &mdash; Performance Monitoring Tool
            </div>

        </div>
    </div>
    """



# =============================================================================
# THEME HELPERS
# =============================================================================

def _hex_to_rgb(hex_color):
    """Convert a hex color string to an (r, g, b) tuple."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c * 2 for c in hex_color)
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def _build_theme_css(primary_color, accent_color, text_color):
    """Generate CSS overrides that apply the user-selected theme across the report."""
    try:
        r, g, b = _hex_to_rgb(primary_color)
        primary_light  = f"rgba({r}, {g}, {b}, 0.07)"
        primary_stripe = f"rgba({r}, {g}, {b}, 0.04)"
    except Exception:
        primary_light  = "rgba(0, 32, 80, 0.07)"
        primary_stripe = "rgba(0, 32, 80, 0.04)"

    return f"""
/* ── Custom Theme ──────────────────────────────────────────────────────── */
body {{ color: {text_color}; }}

/* Table headers */
thead {{ background-color: {primary_color} !important; }}
thead th {{ background-color: {primary_color} !important; color: #ffffff !important; }}
tbody tr:nth-child(even) {{ background-color: {primary_stripe}; }}

/* Cover / back page */
.cover-page {{ background-color: {primary_color}; }}

/* Primary text elements */
.stat-item .value,
.metric-value,
.reliability-kpi-value,
.reliability-value,
.toc-number,
.toc-page,
.page-number,
.section-card-title,
.company-name,
.date,
.page-title,
.section-title {{ color: {text_color}; }}

/* Section icons */
.section-icon {{ background-color: {primary_color}; }}

/* Accent / border elements */
.footer {{ border-top-color: {accent_color}; }}
.metric-content {{ border-left-color: {accent_color}; }}
.reliability-metrics {{ border-left-color: {accent_color}; }}
.reliability-item:not(:last-child) {{ border-right-color: {accent_color}; }}
.toc-dots {{ border-bottom-color: {accent_color}; }}
.cover-accent-rule {{ background-color: {accent_color}; }}
.cover-footer {{ border-top-color: {accent_color}; }}
.content-box {{ border-color: {accent_color}; }}
.summary-section {{ border-right-color: {accent_color}; }}

/* Light-primary backgrounds */
.metric-card.primary,
.reliability-kpi-card,
.reliability-highlight,
.chart-container {{ background-color: {primary_light}; }}

/* Table of contents */
.toc-row {{ border-bottom-color: {accent_color}; }}
"""


def _inject_description(html, description, primary_color):
    """Insert a styled description block immediately after the first page-title heading."""
    import re as _re
    if not description:
        return html
    desc_box = (
        f'<div style="font-size:11px;color:{primary_color};opacity:0.8;'
        f'line-height:1.6;margin-bottom:14px;padding:10px 16px;'
        f'background:rgba(0,0,0,0.04);border-left:3px solid {primary_color};'
        f'border-radius:0 8px 8px 0;">{description}</div>'
    )
    return _re.sub(
        r'(<h1 class="page-title">.*?</h1>)',
        r'' + desc_box,
        html,
        count=1,
        flags=_re.DOTALL,
    )


# =============================================================================
# MAIN PDF GENERATOR CLASS
# =============================================================================

class PDFGenerator:
    """Generate PDF reports from section data"""

    SECTION_RENDERERS = {
        'cover_page': render_cover_page,
        'table_of_contents': render_table_of_contents,
        'infrastructure_overview': render_infrastructure_overview,
        'technical_metrics': render_technical_metrics,
        'system_reliability': render_system_reliability,
        'interruption_breakdown': render_interruption_breakdown,
        'hours_of_supply_chart': render_hours_of_supply_chart,
        'load_trend_chart': render_load_trend_chart,
        'energy_delivered_chart': render_energy_delivered_chart,
        'feeder_performance_table': render_feeder_performance_table,
        'state_performance_table': render_state_performance_table,
        'district_performance_table': render_district_performance_table,
        'service_band_summary': render_service_band_summary,
        'custom_text': render_custom_text,
        'gaps_improvements': render_gaps_improvements,
        # HR
        'hr_overview':          render_hr_overview,
        'staff_metrics':        render_staff_metrics,
        'wage_bill_analysis':   render_wage_bill_analysis,
        'department_headcount': render_department_headcount,
        'attrition_analysis':   render_attrition_analysis,
        'recruitment_summary':  render_recruitment_summary,
        # Commercial
        'commercial_overview':   render_commercial_overview,
        'commercial_coverage':   render_commercial_coverage,
        'commercial_energy':     render_commercial_energy,
        'revenue_by_district':   render_revenue_by_district,
        'revenue_by_feeder':     render_revenue_by_feeder,
        'customer_type_summary': render_customer_type_summary,
        # Commercial Comparison
        'commercial_comparison_summary':  render_commercial_comparison_summary,
        'commercial_comparison_table':    render_commercial_comparison_table,
        'commercial_comparison_insights': render_commercial_comparison_insights,
        # Financial
        'financial_overview': render_financial_overview,
        'opex_by_category':   render_opex_by_category,
        'opex_by_district':   render_opex_by_district,
        # DSO Compliance
        'dso_compliance_overview':     render_dso_compliance_overview,
        'dso_compliance_table':         render_dso_compliance_table,
        # Segment / Dispatch Compliance
        'segment_compliance_summary':   render_segment_compliance_summary,
        'feeder_segment_compliance':    render_feeder_segment_compliance,
        'energy_by_segment_pl':         render_energy_by_segment_pl,
        'segment_voltage_energy':       render_segment_voltage_energy,
        'energy_md_nmd_mix':            render_energy_md_nmd_mix,
        'segment_compliance_trend':     render_segment_compliance_trend,
    }

    def __init__(self, report_config, data_service):
        """
        Initialize PDF generator.

        report_config = {
            'report_title': 'Monthly Performance Report',
            'report_subtitle': '',
            'orientation': 'portrait',
            'company_name': 'KEDCO',
            'sections': [
                {'section_type': 'cover_page', 'config': {}},
                {'section_type': 'table_of_contents', 'config': {}},
                {'section_type': 'technical_metrics', 'config': {'metrics': [...]}},
            ]
        }
        """
        self.report_config = report_config
        self.data_service = data_service
        self.orientation = report_config.get('orientation', 'landscape')

        # Theme — extract with defaults so missing keys never cause KeyErrors
        raw_theme = report_config.get('theme') or {}
        self.theme = {
            'primary_color': raw_theme.get('primary_color') or '#002050',
            'accent_color':  raw_theme.get('accent_color')  or 'rgba(0, 32, 80, 0.2)',
            'text_color':    raw_theme.get('text_color')    or '#002050',
        }

        # Determine scope
        filters = self.data_service.filters
        v_level = filters.get('voltage_level')
        if v_level and str(v_level).lower() == '11kv':
            scope_label = "11kV Report"
        elif v_level and str(v_level).lower() == '33kv':
            scope_label = "33kV Report"
        elif filters.get('feeders'):
            scope_label = f"Selected Feeders ({len(filters['feeders'])})"
        elif filters.get('substations'):
            scope_label = f"Selected Substations ({len(filters['substations'])})"
        elif filters.get('districts'):
            scope_label = f"Selected Districts ({len(filters['districts'])})"
        elif filters.get('states'):
            scope_label = f"Selected States ({len(filters['states'])})"
        else:
            scope_label = "KEDCO Wide Report"

        # Build context
        self.context = {
            'company_name': report_config.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY'),
            'report_title': report_config.get('report_title', 'Monthly Performance Report'),
            'report_subtitle': report_config.get('report_subtitle', ''),
            'report_scope': scope_label,
            'report_date': self._format_report_date(),
            'logo_url': self._get_static_url('reports/images/kedco_logo.png'),
            'logo_gray_url': self._get_static_url('reports/images/kedco_gray_logo.png'),
            'footer_logo_url': self._get_static_url('reports/images/footer_logo.png'),
            # Theme colors available to all renderers via context
            'primary_color': self.theme['primary_color'],
            'accent_color':  self.theme['accent_color'],
            'text_color':    self.theme['text_color'],
        }

    def _format_report_date(self):
        """Format the report period label for the cover page.

        Rules (in order):
          single day          → "31 October 2025"
          full calendar month → "October 2025"
          7 days (1 week)     → "Week 12, 2025"
          multi-week range    → "Week 12 – Week 15, 2025"
          any other range     → "01 Jan 2025 – 31 Mar 2025"
        """
        import calendar as _cal
        from_date = self.data_service.from_date
        to_date   = self.data_service.to_date
        days      = (to_date - from_date).days + 1

        # Single day
        if from_date == to_date:
            return from_date.strftime('%d %B %Y')

        # Full calendar month
        last_day = _cal.monthrange(from_date.year, from_date.month)[1]
        if (from_date.day == 1
                and from_date.month == to_date.month
                and from_date.year == to_date.year
                and to_date.day == last_day):
            return from_date.strftime('%B %Y')

        # Week(s)
        if days <= 49:  # up to 7 weeks — show week numbers
            w_start = from_date.isocalendar()[1]
            w_end   = to_date.isocalendar()[1]
            year    = from_date.year
            if w_start == w_end:
                return f"Week {w_start}, {year}"
            return f"Week {w_start} \u2013 Week {w_end}, {year}"

        # Long range
        return f"{from_date.strftime('%d %b %Y')} \u2013 {to_date.strftime('%d %b %Y')}"

    def _get_static_url(self, path):
        """Return image as a base64 data URI so it renders in both HTML preview
        and PDF regardless of whether the static-file server is reachable."""
        import os

        # Try each directory listed in STATICFILES_DIRS first
        static_dirs = getattr(settings, 'STATICFILES_DIRS', [])
        candidates = [os.path.join(d, path) for d in static_dirs]
        # Also try STATIC_ROOT as a fallback
        static_root = getattr(settings, 'STATIC_ROOT', None)
        if static_root:
            candidates.append(os.path.join(static_root, path))

        for abs_path in candidates:
            if os.path.isfile(abs_path):
                ext = os.path.splitext(abs_path)[1].lower().lstrip('.')
                mime = {'png': 'image/png', 'jpg': 'image/jpeg',
                        'jpeg': 'image/jpeg', 'svg': 'image/svg+xml',
                        'gif': 'image/gif'}.get(ext, 'image/png')
                with open(abs_path, 'rb') as f:
                    data = base64.b64encode(f.read()).decode('utf-8')
                return f"data:{mime};base64,{data}"

        # Last resort: fall back to the HTTP URL (may not load in preview)
        static_url = getattr(settings, 'STATIC_URL', '/static/')
        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        logger.warning("Static file not found on disk: %s — falling back to HTTP URL", path)
        return f"{base_url.rstrip('/')}{static_url}{path}"

    def generate_html(self):
        """Generate full HTML document. Inline per-section footers are used for
        both HTML preview and PDF — reliable across all WeasyPrint versions.

        Two-pass approach ensures TOC page numbers are always accurate:
          Pass 1 — render cover + all content sections, tracking real page numbers
                   as paginated sections may consume more than one page each.
          Pass 2 — render TOC with the accurate page numbers collected in pass 1,
                   then assemble: cover → TOC → content.
        """
        sections = list(self.report_config.get('sections', []))

        # Auto-inject TOC as the second section (after cover page) if not already present
        section_types = [s.get('section_type') for s in sections]
        if 'table_of_contents' not in section_types:
            insert_at = 1 if 'cover_page' in section_types else 0
            sections.insert(insert_at, {'section_type': 'table_of_contents', 'config': {}})

        cover_section = next((s for s in sections if s.get('section_type') == 'cover_page'), None)
        content_sections = [s for s in sections if s.get('section_type') not in ('cover_page', 'table_of_contents')]

        # --- Pass 1: render cover (page 1) ---
        cover_html = ""
        page_number = 1
        if cover_section:
            if self.orientation == 'portrait':
                from reports.management_report import render_mgmt_cover
                cover_html = render_mgmt_cover(self.context)
            else:
                data = self.data_service.get_all_section_data('cover_page', cover_section.get('config', {}))
                cover_html = self.SECTION_RENDERERS['cover_page'](data, self.context)
            page_number = 2  # TOC is always page 2

        toc_page_number = page_number
        # Compute how many TOC pages there will be so content page numbers are correct.
        # Every content section gets one entry — unknown sections are skipped but rare.
        import math
        toc_pages = max(1, math.ceil(len(content_sections) / _TOC_MAX_PER_PAGE))
        page_number += toc_pages  # content starts after all TOC pages

        # --- Pass 1 (cont): render content sections, recording actual start pages ---
        content_html = ""
        toc_entries = []

        for section in content_sections:
            section_type = section.get('section_type')
            config = section.get('config', {})

            if section_type not in self.SECTION_RENDERERS:
                logger.warning(f"Unknown section type: {section_type}")
                page_number += 1
                continue

            data = self.data_service.get_all_section_data(section_type, config)

            if section_type == 'infrastructure_overview':
                self.context['summary_points'] = config.get('summary_points', [])

            # Inject per-section config hints into data so renderers can use them
            section_description = config.get('section_description', '')
            chart_type = config.get('chart_type', '')
            if isinstance(data, dict):
                if chart_type:
                    data['_chart_type'] = chart_type

            # Record this section's actual starting page for the TOC
            display_name = SECTION_DISPLAY_NAMES.get(section_type, section_type.replace('_', ' ').title())
            toc_entries.append({'title': display_name, 'page': page_number})

            renderer = self.SECTION_RENDERERS[section_type]

            if section_type == 'technical_metrics':
                result = renderer(data, self.context, page_number, config)
            else:
                result = renderer(data, self.context, page_number)

            # Inject description block after page title (all section types)
            if section_description:
                primary = self.theme['primary_color']
                if isinstance(result, tuple):
                    result = (_inject_description(result[0], section_description, primary), result[1])
                else:
                    result = _inject_description(result, section_description, primary)

            # Renderers may return (html, pages_used) for paginated sections
            if isinstance(result, tuple):
                content_html += result[0]
                page_number += result[1]
            else:
                content_html += result
                page_number += 1

        # --- Pass 2: render TOC with accurate page numbers, then assemble ---
        toc_html, _ = render_table_of_contents(toc_entries, self.context, toc_page_number)
        back_html   = render_back_page(self.context)
        sections_html = cover_html + toc_html + content_html + back_html

        theme_css = _build_theme_css(
            self.theme['primary_color'],
            self.theme['accent_color'],
            self.theme['text_color'],
        )

        orientation_css = PORTRAIT_STYLES if self.orientation == 'portrait' else LANDSCAPE_STYLES

        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{self.context['report_title']}</title>
            <style>
                {BASE_STYLES}
                {orientation_css}
                {theme_css}
            </style>
        </head>
        <body>
            {sections_html}
        </body>
        </html>
        """

        return html

    def generate_pdf(self):
        """Generate PDF.

        Uses Playwright (headless Chromium) when available — output is identical
        to the browser live preview.  Falls back to WeasyPrint if Playwright is
        not installed or its browser binary is missing.
        """
        html_content = self.generate_html()

        if PLAYWRIGHT_AVAILABLE:
            try:
                return self._generate_pdf_playwright(html_content)
            except Exception as pw_err:
                logger.warning(
                    "Playwright PDF generation failed (%s), falling back to WeasyPrint.", pw_err
                )

        if WEASYPRINT_AVAILABLE:
            return self._generate_pdf_weasyprint(html_content)

        raise RuntimeError(
            "No PDF engine available. "
            "Install Playwright:  pip install playwright && playwright install chromium"
        )

    def _generate_pdf_playwright(self, html_content):
        """Render HTML to PDF using headless Chromium (Playwright)."""
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(html_content, wait_until='domcontentloaded')
            pdf_bytes = page.pdf(
                format='A4',
                landscape=(self.orientation == 'landscape'),
                print_background=True,
                margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'},
            )
            browser.close()
        return io.BytesIO(pdf_bytes)

    def _generate_pdf_weasyprint(self, html_content):
        """Render HTML to PDF using WeasyPrint (fallback)."""
        font_config = FontConfiguration()
        pdf_buffer = io.BytesIO()
        WeasyHTML(string=html_content).write_pdf(pdf_buffer, font_config=font_config)
        pdf_buffer.seek(0)
        return pdf_buffer

    def generate_pdf_base64(self):
        """Generate PDF and return as base64 string"""
        pdf_buffer = self.generate_pdf()
        return base64.b64encode(pdf_buffer.read()).decode('utf-8')
