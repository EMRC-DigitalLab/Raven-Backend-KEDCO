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
    'revenue_by_district':   'Revenue by District',
    'customer_type_summary': 'Customer Type Summary',
    # Financial
    'financial_overview': 'Financial Overview',
    'opex_by_category':   'OPEX by Category',
    'opex_by_district':   'OPEX by District',
}


# =============================================================================
# PDF STYLES
# =============================================================================

BASE_STYLES = """
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');

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
    font-family: 'Outfit', 'Helvetica', 'Arial', sans-serif;
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
    color: #fcd300;
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
    font-size: 30px;
    font-weight: 700;
    color: #fcd300;
    margin-bottom: 4px;
    line-height: 1;
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
    color: #fcd300;
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
    color: #fcd300;
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
/* Layout: 8 px yellow accent bar (left) + full content column (right)        */
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
    background-color: #fcd300;
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
    color: #fcd300;
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
    color: #fcd300;
}

.cover-accent-rule {
    width: 70px;
    height: 5px;
    background-color: #fcd300;
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
    color: #fcd300;
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
    color: #fcd300;
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
"""


# =============================================================================
# TABLE PAGINATION HELPER
# =============================================================================

def _paginate_table(rows_data, header_html, page_title, context, start_page, max_rows=12, landscape=False):
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
            'value': f"{data.get('hours_of_supply', 0)} Hrs",
            'subtitle': 'Average per Feeder per Day',
            'description': 'Average hours of supply per feeder per day',
        },
        'average_load': {
            'label': 'AVERAGE<br/>LOAD',
            'value': f"{data.get('average_load', 0)} MW",
            'subtitle': 'Average',
            'description': 'Average load across all monitored feeders',
        },
        'peak_load': {
            'label': 'PEAK<br/>LOAD',
            'value': f"{data.get('peak_load', 0)} MW",
            'subtitle': 'Maximum',
            'description': 'Maximum load recorded during the period',
        },
        'energy_delivered': {
            'label': 'ENERGY<br/>DELIVERED',
            'value': f"{data.get('energy_delivered', 0)} MWh",
            'subtitle': 'Total (Meter + System estimate)',
            'description': 'Total energy delivered through monitored feeders',
        },
        'daily_average_consumption': {
            'label': 'DAILY AVG<br/>CONSUMPTION',
            'value': f"{data.get('daily_average_consumption', 0)} MWh",
            'subtitle': 'Average per Day',
            'description': 'Average daily energy consumption across the period',
        },
        'total_interruptions': {
            'label': 'TOTAL<br/>INTERRUPTIONS',
            'value': f"{data.get('total_interruptions', 0)} Times",
            'subtitle': 'Count',
            'description': 'Total number of feeder interruptions in the period',
        },
        'load_shedding_count': {
            'label': 'LOAD SHEDDING<br/>COUNT',
            'value': f"{data.get('load_shedding_count', 0)} Times",
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
                    <span style="font-size:7px; font-weight:normal; color:#fcd300;">&#9679; SYSTEM</span>
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
                color:#fcd300;
                padding:6px 10px;
                border-bottom:2px solid rgba(255,255,255,0.2);
            ">&#9658; Band {band}</td>
        </tr>""")
        # Color code the energy source
        source_label = feeder.get('energy_source', 'system').upper()
        dot_color = '#4caf50' if source_label == 'METER' else '#fcd300'
        
        row_strings.append(f"""
        <tr>
            <td>{feeder['name']}</td>
            <td>{feeder['band']}</td>
            <td style="text-align:right;">{feeder['hours_of_supply']} hrs</td>
            <td style="text-align:right;">{feeder['availability_percentage']}%</td>
            <td style="text-align:right;">{feeder['duration_hours']} hrs</td>
            <td style="text-align:right;">{feeder['peak_load']} MW</td>
            <td style="text-align:right; white-space:nowrap;">
                <span style="color:{dot_color}; font-size:10px;">&#9679;</span> {feeder['energy_delivered']} MWh
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
    """Render state performance table HTML"""
    rows_html = ""
    for state in data:
        rows_html += f"""
        <tr>
            <td>{state['state_name']}</td>
            <td style="text-align:right;">{state['feeder_count']}</td>
            <td style="text-align:right;">{state['hours_of_supply']} hrs</td>
            <td style="text-align:right;">{state['availability_percentage']}%</td>
            <td style="text-align:right;">{state['interruptions']}</td>
            <td style="text-align:right;">{state['peak_load']} MW</td>
        </tr>
        """

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">State Performance</h1>

            <div class="table-container">
                <table>
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
                    </thead>
                    <tbody>
                        {rows_html}
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


def render_district_performance_table(data, context, page_number):
    """Render district performance table HTML"""
    rows_html = ""
    for district in data:
        rows_html += f"""
        <tr>
            <td>{district['district_name']}</td>
            <td>{district['state_name']}</td>
            <td style="text-align:right;">{district['feeder_count']}</td>
            <td style="text-align:right;">{district['hours_of_supply']} hrs</td>
            <td style="text-align:right;">{district['availability_percentage']}%</td>
            <td style="text-align:right;">{district['interruptions']}</td>
            <td style="text-align:right;">{district['peak_load']} MW</td>
        </tr>
        """

    return f"""
    <div class="page">
        <div class="page-content">
            <div class="header">
                <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
                <div class="date">{context.get('report_date', '')}</div>
            </div>

            <h1 class="page-title">District Performance</h1>

            <div class="table-container">
                <table>
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
                    </thead>
                    <tbody>
                        {rows_html}
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


def render_hours_of_supply_chart(data, context, page_number):
    """Render hours of supply trend table HTML — two-column layout"""
    left_rows, right_rows = _split_trend_rows(data, 'date', 'hours', 'hrs')

    col_header = """
        <thead>
            <tr>
                <th>Date</th>
                <th style="text-align:right;">Hrs of Supply</th>
            </tr>
        </thead>"""

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Hours of Supply Trend</h1>

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

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_load_trend_chart(data, context, page_number):
    """Render load trend table HTML — two-column layout"""
    left_rows, right_rows = _split_trend_rows(data, 'date', 'value', 'MW')

    col_header = """
        <thead>
            <tr>
                <th>Date</th>
                <th style="text-align:right;">Load (MW)</th>
            </tr>
        </thead>"""

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Load Trend</h1>

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

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_energy_delivered_chart(data, context, page_number):
    """Render energy delivered trend table HTML — two-column layout"""
    left_rows, right_rows = _split_trend_rows(data, 'date', 'value', 'MWh')

    col_header = """
        <thead>
            <tr>
                <th>Date</th>
                <th style="text-align:right;">Energy (MWh)</th>
            </tr>
        </thead>"""

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')} | {context.get('report_scope', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Energy Delivered Trend</h1>

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
        'revenue_by_district':   render_revenue_by_district,
        'customer_type_summary': render_customer_type_summary,
        # Financial
        'financial_overview': render_financial_overview,
        'opex_by_category':   render_opex_by_category,
        'opex_by_district':   render_opex_by_district,
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
        self.orientation = 'landscape'  # Always landscape — full-width tables require it

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
        }

    def _format_report_date(self):
        """Format the report date for display"""
        from_date = self.data_service.from_date
        to_date = self.data_service.to_date

        if from_date == to_date:
            return from_date.strftime('%d %B %Y')
        elif from_date.month == to_date.month and from_date.year == to_date.year:
            return from_date.strftime('%B %Y')
        else:
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

            # Record this section's actual starting page for the TOC
            display_name = SECTION_DISPLAY_NAMES.get(section_type, section_type.replace('_', ' ').title())
            toc_entries.append({'title': display_name, 'page': page_number})

            renderer = self.SECTION_RENDERERS[section_type]

            if section_type == 'technical_metrics':
                result = renderer(data, self.context, page_number, config)
            else:
                result = renderer(data, self.context, page_number)

            # Renderers may return (html, pages_used) for paginated sections
            if isinstance(result, tuple):
                content_html += result[0]
                page_number += result[1]
            else:
                content_html += result
                page_number += 1

        # --- Pass 2: render TOC with accurate page numbers, then assemble ---
        toc_html, _ = render_table_of_contents(toc_entries, self.context, toc_page_number)
        sections_html = cover_html + toc_html + content_html

        # Build full HTML — always landscape throughout
        orientation_css = LANDSCAPE_STYLES

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
            page.set_content(html_content, wait_until='networkidle')
            pdf_bytes = page.pdf(
                format='A4',
                landscape=True,
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
