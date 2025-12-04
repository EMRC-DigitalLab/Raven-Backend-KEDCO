# reports/pdf_generator.py
"""
PDF generation service using WeasyPrint.
"""
from django.template.loader import render_to_string
from django.conf import settings
# from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration
import os
import io
import base64
import logging

logger = logging.getLogger(__name__)


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

.page {
    width: 100%;
    min-height: 100vh;
    padding: 25px 40px;
    page-break-after: always;
    position: relative;
}

.page:last-child {
    page-break-after: avoid;
}

.page-landscape {
    padding: 20px 35px;
}

/* Header */
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

/* Page Title */
.page-title {
    font-size: 32px;
    font-weight: 400;
    margin-bottom: 20px;
}

/* Footer */
.footer {
    position: absolute;
    bottom: 25px;
    left: 40px;
    right: 40px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
}

.footer img {
    max-width: 150px;
    height: auto;
}

.page-number {
    font-size: 24px;
    font-weight: 600;
}

/* Stats Container */
.stats-container {
    display: flex;
    gap: 60px;
    margin-bottom: 25px;
}

.stat-item h3 {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 8px;
}

.stat-item .value {
    font-size: 36px;
    font-weight: 700;
}

/* Content Box */
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
    flex: 1;
    border-right: 2px solid #636363;
}

.summary-section h2 {
    font-size: 18px;
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

/* Tables */
.table-section {
    flex: 1;
    padding: 0;
}

table {
    width: 100%;
    border-collapse: collapse;
}

thead {
    background-color: #005bd5;
}

thead th {
    padding: 12px 15px;
    text-align: left;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
}

tbody tr {
    background-color: transparent;
}

tbody tr:nth-child(even) {
    background-color: rgba(255, 255, 255, 0.03);
}

tbody td {
    padding: 10px 15px;
    font-size: 12px;
    font-weight: 500;
}

.table-container {
    width: 100%;
    border-radius: 15px;
    overflow: hidden;
    margin-bottom: 20px;
}

.table-container tbody tr {
    background-color: #1e2f4a;
}

.table-container tbody tr:not(:last-child) {
    border-bottom: 2px solid #ffffff;
}

.table-container tbody td {
    padding: 12px 18px;
    font-size: 14px;
    font-weight: 600;
}

/* Metric Cards */
.metrics-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
    margin-bottom: 15px;
}

.metric-card {
    border-radius: 15px;
    padding: 18px 22px;
    display: flex;
    align-items: center;
    gap: 18px;
}

.metric-card.primary {
    background-color: #1e2f4a;
}

.metric-card.secondary {
    background-color: #14396a;
}

.metric-label {
    flex: 0 0 180px;
}

.metric-label h2 {
    font-size: 18px;
    font-weight: 700;
    line-height: 1.15;
    text-transform: uppercase;
}

.metric-content {
    flex: 1;
    border-left: 2px solid rgba(255, 255, 255, 0.3);
    padding-left: 18px;
}

.metric-value {
    font-size: 32px;
    font-weight: 700;
    color: #fcd300;
    margin-bottom: 4px;
    line-height: 1;
}

.metric-subtitle {
    font-size: 12px;
    font-weight: 700;
    margin-bottom: 3px;
}

.metric-description {
    font-size: 10px;
    font-weight: 400;
    line-height: 1.3;
    opacity: 0.9;
}

/* Reliability Card */
.reliability-card {
    background-color: #1e2f4a;
    border-radius: 15px;
    padding: 25px 30px;
    display: flex;
    align-items: center;
    gap: 30px;
    margin-bottom: 25px;
}

.reliability-label {
    flex: 0 0 auto;
}

.reliability-label h2 {
    font-size: 28px;
    font-weight: 700;
    line-height: 1.2;
    text-transform: uppercase;
    color: #cbffcb;
}

.reliability-metrics {
    flex: 1;
    display: flex;
    gap: 30px;
    padding-left: 30px;
    border-left: 2px solid rgba(255, 255, 255, 0.3);
}

.reliability-item {
    flex: 1;
    text-align: center;
}

.reliability-item:not(:last-child) {
    border-right: 2px solid rgba(255, 255, 255, 0.3);
    padding-right: 30px;
}

.reliability-value {
    font-size: 36px;
    font-weight: 700;
    line-height: 1;
    margin-bottom: 8px;
}

.reliability-description {
    font-size: 12px;
    font-weight: 600;
    line-height: 1.3;
}

/* Section Title */
.section-title {
    font-size: 24px;
    font-weight: 400;
    margin-bottom: 15px;
}

/* Gaps/Improvements Grid */
.sections-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 15px;
    margin-bottom: 20px;
}

.section-card {
    background-color: #ececec;
    border-radius: 15px;
    padding: 20px;
    min-height: 160px;
}

.section-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 12px;
}

.section-card-title {
    font-size: 16px;
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

/* Cover Page */
.cover-page {
    padding: 60px 80px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    min-height: 100vh;
}

.cover-main-content {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-right: 80px;
}

.cover-title-section {
    flex: 1;
}

.cover-title-section h1 {
    font-size: 72px;
    font-weight: 400;
    line-height: 1.1;
    margin: 0;
}

.cover-subtitle {
    font-size: 72px;
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
    max-width: 350px;
    height: auto;
}

/* Chart placeholder */
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

/* Intro text */
.intro-text {
    font-size: 13px;
    font-weight: 400;
    line-height: 1.6;
    margin-bottom: 20px;
}
"""

LANDSCAPE_STYLES = """
@page {
    size: landscape;
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

def render_cover_page(data, context):
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
        
        <div class="footer">
            <img src="{context.get('footer_logo_url', '')}" alt="Powered by EMRC" />
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
            'subtitle': 'Average',
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
            'subtitle': 'Sum of Consumption',
            'description': 'Total energy delivered through monitored feeders',
        },
        'daily_average_consumption': {
            'label': 'DAILY AVERAGE<br/>CONSUMPTION',
            'value': f"{data.get('daily_average_consumption', 0)} MWh",
            'subtitle': 'Average',
            'description': 'Average daily energy consumption',
        },
        'total_interruptions': {
            'label': 'TOTAL<br/>INTERRUPTIONS',
            'value': f"{data.get('total_interruptions', 0)} Times",
            'subtitle': 'Count',
            'description': 'Total number of feeder interruptions',
        },
        'load_shedding_count': {
            'label': 'LOAD SHEDDING<br/>COUNT',
            'value': f"{data.get('load_shedding_count', 0)} Times",
            'subtitle': 'Count',
            'description': 'Interruptions due to load shedding',
        },
    }
    
    cards_html = ""
    for i, metric_key in enumerate(selected_metrics):
        if metric_key not in metric_definitions:
            continue
        metric = metric_definitions[metric_key]
        card_class = 'primary' if i == 0 else 'secondary'
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
    """Render system reliability section HTML"""
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h1 class="page-title">System Reliability</h1>
        
        <div class="reliability-card">
            <div class="reliability-label">
                <h2>SYSTEM<br/>RELIABILITY</h2>
            </div>
            <div class="reliability-metrics">
                <div class="reliability-item">
                    <div class="reliability-value">{data.get('cumulative_interruption_hours', 0)} hrs</div>
                    <div class="reliability-description">Cumulative hours of interruption</div>
                </div>
                <div class="reliability-item">
                    <div class="reliability-value">{data.get('avg_duration_of_interruption', 0)} hrs</div>
                    <div class="reliability-description">Average Duration of interruption</div>
                </div>
                <div class="reliability-item">
                    <div class="reliability-value">{data.get('avg_turnaround_time', 0)} hrs</div>
                    <div class="reliability-description">Average Turnaround Time (Faults)</div>
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
    """Render interruption breakdown table HTML"""
    rows_html = ""
    for item in data:
        rows_html += f"""
        <tr>
            <td>{item['type']}</td>
            <td>{item['count']}</td>
            <td>{item['total_hours']}</td>
            <td>{item['avg_duration']}</td>
        </tr>
        """
    
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h2 class="section-title">Interruption Breakdown</h2>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Type</th>
                        <th>Count</th>
                        <th>Total Hours</th>
                        <th>Avg Duration</th>
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
            <td>{feeder['hours_of_supply']} hrs</td>
            <td>{feeder['availability_percentage']}%</td>
            <td>{feeder['interruptions']}</td>
            <td>{feeder['peak_load']} MW</td>
        </tr>
        """
    
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h2 class="section-title">Feeder Performance</h2>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Feeder Name</th>
                        <th>Band</th>
                        <th>Avg Supply</th>
                        <th>Availability</th>
                        <th>Interruptions</th>
                        <th>Peak Load</th>
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
    """Render service band summary HTML"""
    rows_html = ""
    for band in data:
        rows_html += f"""
        <tr>
            <td>Band {band['band']}</td>
            <td>{band['feeder_count']}</td>
            <td>{band['hours_of_supply']} hrs</td>
            <td>{band['interruptions']}</td>
        </tr>
        """
    
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h2 class="section-title">Service Band Summary</h2>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Service Band</th>
                        <th>Feeders</th>
                        <th>Avg Supply (hrs/day)</th>
                        <th>Interruptions</th>
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
            <td>{state['feeder_count']}</td>
            <td>{state['hours_of_supply']} hrs</td>
            <td>{state['availability_percentage']}%</td>
            <td>{state['interruptions']}</td>
            <td>{state['peak_load']} MW</td>
        </tr>
        """
    
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h2 class="section-title">State Performance</h2>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>State</th>
                        <th>Feeders</th>
                        <th>Avg Supply</th>
                        <th>Availability</th>
                        <th>Interruptions</th>
                        <th>Peak Load</th>
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
            <td>{district['feeder_count']}</td>
            <td>{district['hours_of_supply']} hrs</td>
            <td>{district['availability_percentage']}%</td>
            <td>{district['interruptions']}</td>
            <td>{district['peak_load']} MW</td>
        </tr>
        """
    
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h2 class="section-title">District Performance</h2>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>District</th>
                        <th>State</th>
                        <th>Feeders</th>
                        <th>Avg Supply</th>
                        <th>Availability</th>
                        <th>Interruptions</th>
                        <th>Peak Load</th>
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


def render_hours_of_supply_chart(data, context, page_number):
    """Render hours of supply chart/table HTML"""
    rows_html = ""
    if isinstance(data, list):
        for item in data:  # Show all days in range
            rows_html += f"""
            <tr>
                <td>{item.get('date', '')}</td>
                <td>{item.get('hours', 0)} hrs</td>
            </tr>
            """
    
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h2 class="section-title">Hours of Supply Trend</h2>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Hours of Supply</th>
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


def render_load_trend_chart(data, context, page_number):
    """Render load trend chart/table HTML"""
    rows_html = ""
    if isinstance(data, list):
        for item in data:  # Show all days in range
            rows_html += f"""
            <tr>
                <td>{item.get('date', '')}</td>
                <td>{item.get('value', 0)} MW</td>
            </tr>
            """
    
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h2 class="section-title">Load Trend</h2>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Load (MW)</th>
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


def render_energy_delivered_chart(data, context, page_number):
    """Render energy delivered chart/table HTML"""
    rows_html = ""
    if isinstance(data, list):
        for item in data:  # Show all days in range
            rows_html += f"""
            <tr>
                <td>{item.get('date', '')}</td>
                <td>{item.get('value', 0)} MWh</td>
            </tr>
            """
    
    return f"""
    <div class="page">
        <div class="header">
            <div class="company-name">{context.get('company_name', '')}</div>
            <div class="date">{context.get('report_date', '')}</div>
        </div>
        
        <h2 class="section-title">Energy Delivered Trend</h2>
        
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Energy (MWh)</th>
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


# =============================================================================
# MAIN PDF GENERATOR CLASS
# =============================================================================

class PDFGenerator:
    """Generate PDF reports from section data"""
    
    SECTION_RENDERERS = {
        'cover_page': render_cover_page,
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
        
        if from_date.month == to_date.month and from_date.year == to_date.year:
            return from_date.strftime('%B %Y')
        else:
            return f"{from_date.strftime('%b %d')} - {to_date.strftime('%b %d, %Y')}"
    
    def _get_static_url(self, path):
        """Get full URL for static files"""
        static_url = getattr(settings, 'STATIC_URL', '/static/')
        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        return f"{base_url.rstrip('/')}{static_url}{path}"
    
    def generate_html(self):
        """Generate full HTML document"""
        sections_html = ""
        page_number = 1
        
        for section in self.report_config.get('sections', []):
            section_type = section.get('section_type')
            config = section.get('config', {})
            
            if section_type not in self.SECTION_RENDERERS:
                logger.warning(f"Unknown section type: {section_type}")
                continue
            
            # Get data for this section
            data = self.data_service.get_all_section_data(section_type, config)
            
            # Add summary points to context for infrastructure overview
            if section_type == 'infrastructure_overview':
                self.context['summary_points'] = config.get('summary_points', [])
            
            # Render section
            renderer = self.SECTION_RENDERERS[section_type]
            
            if section_type == 'cover_page':
                sections_html += renderer(data, self.context)
            elif section_type == 'technical_metrics':
                sections_html += renderer(data, self.context, page_number, config)
            else:
                sections_html += renderer(data, self.context, page_number)
            
            page_number += 1
        
        # Build full HTML
        orientation_css = LANDSCAPE_STYLES if self.orientation == 'landscape' else PORTRAIT_STYLES
        
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
        """Generate PDF from HTML"""
        html_content = self.generate_html()
        
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