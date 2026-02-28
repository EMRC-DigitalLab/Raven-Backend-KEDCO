# reports/pdf_generator.py
"""
PDF generation service using WeasyPrint.
"""
from django.conf import settings
try:
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration
    WEASYPRINT_AVAILABLE = True
except (OSError, ImportError) as e:
    # GTK or other dependencies missing
    HTML = None
    FontConfiguration = None
    WEASYPRINT_AVAILABLE = False
    print(f"WARNING: WeasyPrint could not be imported. PDF generation will not work. Error: {e}")

import io
import base64
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
    background-color: #002050;
    color: #ffffff;
    font-size: 12px;
    line-height: 1.4;
}

/* ── Page layout ─────────────────────────────────────────────────────────── */
/* Flex column ensures footer is always pushed to the physical bottom of each
   page regardless of how much content is on it.  min-height: 297mm pins the
   container to a full A4 portrait page; WeasyPrint will break at natural
   overflow points if content is taller than one page.                        */

.page {
    width: 100%;
    min-height: 297mm;
    padding: 25px 40px;
    page-break-after: always;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
}

.page:last-child {
    page-break-after: avoid;
}

.page-landscape {
    min-height: 210mm;
    padding: 20px 35px;
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

/* ── Footer ──────────────────────────────────────────────────────────────── */
/* HTML preview: margin-top:auto in flex column keeps footer at section bottom */

.footer {
    margin-top: auto;
    padding-top: 20px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    border-top: 1px solid rgba(255, 255, 255, 0.15);
}

.footer img {
    max-width: 150px;
    height: auto;
}

.page-number {
    font-size: 24px;
    font-weight: 600;
}

/* ── PDF-mode fixed footer ────────────────────────────────────────────────── */
/* WeasyPrint repeats position:fixed elements on every physical page.         */
/* .footer-fixed is ONLY shown when <body class="pdf-mode"> is set, which     */
/* generate_pdf() does. In HTML preview mode it stays hidden.                 */

.footer-fixed {
    display: none;
}

.pdf-mode .page {
    padding-bottom: 65px;
}

.pdf-mode .footer,
.pdf-mode .cover-footer {
    display: none;
}

.pdf-mode .footer-fixed {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 12px 40px 15px;
    display: -webkit-flex;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 1px solid rgba(255, 255, 255, 0.15);
    background-color: #002050;
}

.pdf-mode .footer-fixed img {
    max-width: 150px;
    height: auto;
}

/* CSS counter auto-increments per physical page in WeasyPrint */
.pdf-mode .footer-fixed .page-number::after {
    content: counter(page);
    font-size: 24px;
    font-weight: 600;
}

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
    border: 2px solid #ffffff;
    border-radius: 20px;
    overflow: hidden;
    display: flex;
    margin-bottom: 25px;
}

.summary-section {
    background-color: #1e2f4a;
    padding: 25px;
    flex: 0 0 260px;
    border-right: 2px solid #636363;
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
    background-color: #005bd5;
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

tbody tr:nth-child(even) {
    background-color: rgba(255, 255, 255, 0.04);
}

tbody td {
    padding: 9px 14px;
    font-size: 12px;
    font-weight: 500;
    vertical-align: middle;
}

.table-container {
    width: 100%;
    border-radius: 15px;
    overflow: hidden;
    margin-bottom: 20px;
}

.table-container thead {
    background-color: #005bd5;
}

.table-container tbody tr {
    background-color: #1e2f4a;
}

.table-container tbody tr:not(:last-child) {
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.table-container tbody td {
    padding: 11px 16px;
    font-size: 13px;
    font-weight: 500;
}

/* Prevent rows from breaking across pages */
.table-container tr {
    page-break-inside: avoid;
}

/* ── Two-column trend layout ──────────────────────────────────────────────── */
.trend-columns {
    display: -webkit-flex;
    display: flex;
    margin-left: -10px;
    margin-right: -10px;
}

.trend-column {
    width: calc(50% - 20px);
    margin: 0 10px;
}

/* ── Metric Cards ─────────────────────────────────────────────────────────── */
/* Use flexbox+wrap instead of CSS Grid for WeasyPrint compatibility           */
.metrics-grid {
    display: -webkit-flex;
    display: flex;
    flex-wrap: wrap;
    margin-left: -6px;
    margin-right: -6px;
    margin-bottom: 9px;
}

.metric-card {
    width: calc(50% - 12px);
    margin: 6px;
    border-radius: 15px;
    padding: 18px 22px;
    display: flex;
    align-items: center;
}

.metric-card.primary {
    background-color: #1e2f4a;
}

.metric-card.secondary {
    background-color: #14396a;
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
    border-left: 2px solid rgba(255, 255, 255, 0.25);
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
    background-color: #1e2f4a;
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
    color: #cbffcb;
}

.reliability-metrics {
    flex: 1;
    display: flex;
    gap: 0;
    padding-left: 30px;
    border-left: 2px solid rgba(255, 255, 255, 0.25);
}

.reliability-item {
    flex: 1;
    text-align: center;
    padding: 0 20px;
}

.reliability-item:not(:last-child) {
    border-right: 2px solid rgba(255, 255, 255, 0.2);
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
    margin-left: -8px;
    margin-right: -8px;
    margin-bottom: 25px;
}

.reliability-kpi-card {
    width: calc(33.33% - 16px);
    margin: 8px;
    background-color: #1e2f4a;
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
    background-color: #0a3d6b;
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
    color: #cbffcb;
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
/* 3-column flex layout — WeasyPrint-safe (no CSS Grid, no gap shorthand)     */
.sections-grid {
    display: -webkit-flex;
    display: flex;
    flex-wrap: wrap;
    margin-left: -7px;
    margin-right: -7px;
    margin-bottom: 20px;
}

.section-card {
    width: calc(33.33% - 14px);
    margin: 7px;
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

/* ── Cover Page ───────────────────────────────────────────────────────────── */
.cover-page {
    padding: 60px 80px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    min-height: 297mm;
}

.cover-main-content {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-right: 40px;
}

.cover-title-section {
    flex: 1;
}

.cover-title-section h1 {
    font-size: 64px;
    font-weight: 400;
    line-height: 1.1;
    margin: 0;
}

.cover-subtitle {
    font-size: 64px;
    font-weight: 400;
    line-height: 1.1;
}

.cover-performance {
    color: #fcd300;
}

.cover-logo-section {
    display: flex;
    align-items: center;
    margin-left: 50px;
}

.cover-logo-section img {
    max-width: 300px;
    height: auto;
}

.cover-footer {
    padding-top: 20px;
    border-top: 1px solid rgba(255, 255, 255, 0.15);
}

.cover-footer img {
    max-width: 150px;
    height: auto;
}

/* ── Chart placeholder ────────────────────────────────────────────────────── */
.chart-container {
    background-color: #1e2f4a;
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
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
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
    border-bottom: 1px dotted rgba(255, 255, 255, 0.35);
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
# HTML TEMPLATES FOR EACH SECTION
# =============================================================================

def render_cover_page(_data, context):
    """Render cover page HTML"""
    return f"""
    <div class="page cover-page">
        <div class="header">
            <div class="company-name">{context.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <div class="cover-main-content">
            <div class="cover-title-section">
                <h1>{context.get('report_title', 'Monthly Performance Report')}</h1>
                <div class="cover-subtitle">
                    <span class="cover-performance">{context.get('report_subtitle', '')}</span>
                </div>
            </div>

            <div class="cover-logo-section">
                <img src="{context.get('logo_gray_url', '')}" alt="Company Logo" />
            </div>
        </div>

        <div class="cover-footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
        </div>
    </div>
    """


def render_table_of_contents(entries, context, page_number):
    """Render auto-generated table of contents HTML"""
    rows_html = ""
    for i, entry in enumerate(entries, start=1):
        rows_html += f"""
        <div class="toc-row">
            <span class="toc-number">{i:02d}</span>
            <span class="toc-title">{entry['title']}</span>
            <span class="toc-dots"></span>
            <span class="toc-page">{entry['page']}</span>
        </div>
        """

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Contents</h1>

        <div class="toc-container">
            {rows_html}
        </div>

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


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
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
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
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Technical Overview</h1>

        <div class="metrics-grid">
            {cards_html}
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
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
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

        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
            <div class="page-number">{page_number}</div>
        </div>
    </div>
    """


def render_interruption_breakdown(data, context, page_number):
    """Render interruption breakdown table HTML"""
    rows_html = ""
    for item in data:
        rows_html += f"""
        <tr>
            <td>{item['type']}</td>
            <td style="text-align:right;">{item['count']}</td>
            <td style="text-align:right;">{item['total_hours']} hrs</td>
            <td style="text-align:right;">{item['avg_duration']} hrs</td>
        </tr>
        """

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Interruption Breakdown</h1>

        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Interruption Type</th>
                        <th style="text-align:right;">Count</th>
                        <th style="text-align:right;">Total Hours</th>
                        <th style="text-align:right;">Avg Duration</th>
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


def render_feeder_performance_table(data, context, page_number):
    """Render feeder performance table HTML"""
    rows_html = ""
    for feeder in data:
        rows_html += f"""
        <tr>
            <td>{feeder['name']}</td>
            <td>{feeder['band']}</td>
            <td style="text-align:right;">{feeder['hours_of_supply']} hrs</td>
            <td style="text-align:right;">{feeder['availability_percentage']}%</td>
            <td style="text-align:right;">{feeder['interruptions']}</td>
            <td style="text-align:right;">{feeder['peak_load']} MW</td>
            <td style="text-align:right;">{feeder['energy_delivered']} MWh</td>
        </tr>
        """

    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">Feeder Performance</h1>

        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Feeder Name</th>
                        <th>Band</th>
                        <th style="text-align:right;">Avg Supply (hrs)</th>
                        <th style="text-align:right;">Availability</th>
                        <th style="text-align:right;">Interruptions</th>
                        <th style="text-align:right;">Peak Load</th>
                        <th style="text-align:right;">Energy (MWh)</th>
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
            <div class="company-name">{context.get('company_name', '')}</div>
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
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">State Performance</h1>

        <div class="table-container">
            <table>
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
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>

        <h1 class="page-title">District Performance</h1>

        <div class="table-container">
            <table>
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
            <div class="company-name">{context.get('company_name', '')}</div>
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
            <div class="company-name">{context.get('company_name', '')}</div>
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
            <div class="company-name">{context.get('company_name', '')}</div>
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
            <div class="company-name">{context.get('company_name', '')}</div>
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
            <div class="company-name">{context.get('company_name', '')}</div>
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
        self.orientation = report_config.get('orientation', 'portrait')

        # Build context
        self.context = {
            'company_name': report_config.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY'),
            'report_title': report_config.get('report_title', 'Monthly Performance Report'),
            'report_subtitle': report_config.get('report_subtitle', ''),
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
        """Get full URL for static files"""
        static_url = getattr(settings, 'STATIC_URL', '/static/')
        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        return f"{base_url.rstrip('/')}{static_url}{path}"

    def _build_toc_entries(self, sections):
        """
        Pre-compute page numbers and build TOC entry list.

        Sections excluded from the TOC listing:
            - cover_page   (not a content section)
            - table_of_contents  (self-referential)
        """
        toc_entries = []
        for page_number, section in enumerate(sections, start=1):
            section_type = section.get('section_type')
            if section_type in ('cover_page', 'table_of_contents'):
                continue
            display_name = SECTION_DISPLAY_NAMES.get(section_type, section_type.replace('_', ' ').title())
            toc_entries.append({
                'title': display_name,
                'page': page_number,
            })
        return toc_entries

    def generate_html(self, for_pdf=False):
        """Generate full HTML document.

        for_pdf=True  → adds pdf-mode body class so the CSS fixed footer fires;
                        used by generate_pdf() so every physical page has a footer.
        for_pdf=False → normal HTML preview; inline per-section footers are used.
        """
        sections = list(self.report_config.get('sections', []))

        # Auto-inject TOC as the second section (after cover page) if not already present
        section_types = [s.get('section_type') for s in sections]
        if 'table_of_contents' not in section_types:
            insert_at = 1 if 'cover_page' in section_types else 0
            sections.insert(insert_at, {'section_type': 'table_of_contents', 'config': {}})

        # Pre-build TOC entries so they are ready when the TOC section is rendered
        toc_entries = self._build_toc_entries(sections)

        sections_html = ""
        page_number = 1

        for section in sections:
            section_type = section.get('section_type')
            config = section.get('config', {})

            if section_type not in self.SECTION_RENDERERS:
                logger.warning(f"Unknown section type: {section_type}")
                page_number += 1
                continue

            # Get data for this section
            data = self.data_service.get_all_section_data(section_type, config)

            # Pass summary_points to context for infrastructure overview
            if section_type == 'infrastructure_overview':
                self.context['summary_points'] = config.get('summary_points', [])

            renderer = self.SECTION_RENDERERS[section_type]

            if section_type == 'cover_page':
                sections_html += renderer(data, self.context)
            elif section_type == 'table_of_contents':
                sections_html += renderer(toc_entries, self.context, page_number)
            elif section_type == 'technical_metrics':
                sections_html += renderer(data, self.context, page_number, config)
            else:
                sections_html += renderer(data, self.context, page_number)

            page_number += 1

        # Build full HTML
        orientation_css = LANDSCAPE_STYLES if self.orientation == 'landscape' else PORTRAIT_STYLES

        # pdf-mode body class activates the CSS fixed footer (position:fixed)
        # which WeasyPrint repeats on every physical page automatically.
        body_class = ' class="pdf-mode"' if for_pdf else ''

        # One global fixed-footer element; CSS shows it only in pdf-mode.
        fixed_footer_html = f"""
        <div class="footer-fixed">
            <img src="{self.context['footer_logo_url']}" alt="Powered by EMRC" />
            <div class="page-number"></div>
        </div>""" if for_pdf else ""

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
        <body{body_class}>
            {fixed_footer_html}
            {sections_html}
        </body>
        </html>
        """

        return html

    def generate_pdf(self):
        """Generate PDF from HTML"""
        if not WEASYPRINT_AVAILABLE:
            raise RuntimeError(
                "WeasyPrint is not available on this server. "
                "PDF generation requires WeasyPrint and its GTK dependencies. "
                "Please contact your system administrator."
            )

        html_content = self.generate_html(for_pdf=True)

        font_config = FontConfiguration()
        html = HTML(string=html_content)

        pdf_buffer = io.BytesIO()
        html.write_pdf(pdf_buffer, font_config=font_config)
        pdf_buffer.seek(0)

        return pdf_buffer

    def generate_pdf_base64(self):
        """Generate PDF and return as base64 string"""
        pdf_buffer = self.generate_pdf()
        return base64.b64encode(pdf_buffer.read()).decode('utf-8')
