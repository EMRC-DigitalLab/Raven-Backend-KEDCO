"""
analytics/views/comparisons/comparison_report.py

POST /api/analytics/compare/customers/report/

Generates a PDF report for a customer consumption comparison.
Uses the same parameters as the compare endpoint, runs the comparison,
optionally fetches AI insights, then builds a PDF using the existing
PDFGenerator engine.

Report structure (auto-injected cover + TOC + back page):
  1. Cover page
  2. Table of Contents
  3. Comparison Summary    — KPI cards + trend distribution
  4. Customer Detail       — paginated comparison table
  5. AI Insights           — only included if insights are available
  6. Back page
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from rest_framework.decorators import api_view
from rest_framework.response import Response

from analytics.services.ai_insights import get_comparison_insights
from analytics.services.compare_service import compare_customers, user_has_permission

logger = logging.getLogger(__name__)

VALID_CUSTOMER_TYPES = ('MDI', 'MDNI', 'all')
VALID_SCOPE_TYPES    = ('feeder', 'district', 'state', 'station')
VALID_SORT_BY        = ('current_consumption', 'variance_pct', 'decline')


# ─── Lightweight data service (wraps pre-computed comparison data) ──────────────

class ComparisonReportDataService:
    """
    Minimal data service adapter for PDFGenerator.

    PDFGenerator calls get_all_section_data(section_type, config) per section
    and reads .filters, .from_date, .to_date for scope/date label resolution.
    All comparison sections share the same pre-computed data dict.
    """

    def __init__(self, comparison: dict, current_from: date, current_to: date, scope_label: str):
        self.comparison  = comparison
        self.from_date   = current_from
        self.to_date     = current_to
        self.filters     = {'scope_label': scope_label}

    def get_all_section_data(self, section_type: str, config: dict = None) -> dict:
        return self.comparison


# ─── Period auto-resolution (mirrors compare_engine.py helper) ─────────────────

def _resolve_period_mode(mode: str):
    today = date.today()
    if mode == 'daily':
        yesterday = today - timedelta(days=1)
        return today, today, yesterday, yesterday
    if mode == 'monthly':
        month_start      = today.replace(day=1)
        prev_month_end   = month_start - timedelta(days=1)
        prev_month_start = prev_month_end.replace(day=1)
        return month_start, today, prev_month_start, prev_month_end
    # weekly (default)
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(weeks=1)
    last_sunday = this_monday - timedelta(days=1)
    return this_monday, today, last_monday, last_sunday


# ─── Report view ───────────────────────────────────────────────────────────────

@api_view(['POST'])
def customer_compare_report(request):
    """
    POST /api/analytics/compare/customers/report/

    Body mirrors POST /api/analytics/compare/customers/ with two additions:
        "include_insights": true          // optional — adds AI insights page (default true)
        "report_title":     "Weekly MDI Comparison"  // optional

    Returns:
        { "pdf_base64": "<base64-encoded PDF>", "filename": "..." }
    """
    from datetime import datetime

    from reports.pdf_generator import PDFGenerator

    if not user_has_permission(request.user, 'view_customer_comparison'):
        return Response(
            {'error': 'You do not have permission to access Customer Comparison.'},
            status=403,
        )

    body = request.data

    # ── Customer type ──────────────────────────────────────────────────────────
    customer_type = body.get('customer_type', 'all')
    if customer_type not in VALID_CUSTOMER_TYPES:
        return Response(
            {'error': f"customer_type must be one of: {', '.join(VALID_CUSTOMER_TYPES)}"},
            status=400,
        )

    # ── Period resolution ──────────────────────────────────────────────────────
    current_period  = body.get('current_period')
    previous_period = body.get('previous_period')

    if current_period and previous_period:
        try:
            current_from  = datetime.strptime(str(current_period['from_date'])[:10],  '%Y-%m-%d').date()
            current_to    = datetime.strptime(str(current_period['to_date'])[:10],    '%Y-%m-%d').date()
            previous_from = datetime.strptime(str(previous_period['from_date'])[:10], '%Y-%m-%d').date()
            previous_to   = datetime.strptime(str(previous_period['to_date'])[:10],   '%Y-%m-%d').date()
        except (KeyError, ValueError, TypeError):
            return Response(
                {'error': 'current_period and previous_period must include from_date and to_date in YYYY-MM-DD format.'},
                status=400,
            )
        if current_from > current_to or previous_from > previous_to:
            return Response({'error': 'from_date must be on or before to_date in each period.'}, status=400)
        period_mode = 'custom'
    else:
        period_mode   = body.get('period_mode', 'weekly')
        current_from, current_to, previous_from, previous_to = _resolve_period_mode(period_mode)

    # ── Scope ──────────────────────────────────────────────────────────────────
    scope_type = body.get('scope_type') or None
    scope_id   = body.get('scope_id')   or None

    if scope_type and scope_type not in VALID_SCOPE_TYPES:
        return Response(
            {'error': f"scope_type must be one of: {', '.join(VALID_SCOPE_TYPES)}"},
            status=400,
        )
    if scope_type and not scope_id:
        return Response({'error': 'scope_id is required when scope_type is provided.'}, status=400)

    # ── Options ────────────────────────────────────────────────────────────────
    customer_ids = body.get('customer_ids') or None
    sort_by      = body.get('sort_by', 'current_consumption')
    if sort_by not in VALID_SORT_BY:
        sort_by = 'current_consumption'

    try:
        positive_threshold = float(body.get('positive_threshold', 10.0))
        declined_threshold = float(body.get('declined_threshold', -30.0))
    except (TypeError, ValueError):
        positive_threshold, declined_threshold = 10.0, -30.0

    include_insights = bool(body.get('include_insights', True))

    # Reports always include every customer in scope — top_n is a dashboard
    # feature, not a report feature. Passing select_all=True bypasses the cap.
    # customer_ids takes precedence if a specific picker selection was sent.
    report_select_all = True if not customer_ids else False

    # ── Run comparison ─────────────────────────────────────────────────────────
    try:
        comparison = compare_customers(
            user               = request.user,
            current_from       = current_from,
            current_to         = current_to,
            previous_from      = previous_from,
            previous_to        = previous_to,
            customer_type      = customer_type,
            scope_type         = scope_type,
            scope_id           = scope_id,
            customer_ids       = customer_ids,
            select_all         = report_select_all,
            top_n              = 50,
            sort_by            = sort_by,
            positive_threshold = positive_threshold,
            declined_threshold = declined_threshold,
        )
    except Exception as exc:
        logger.exception("compare_customers failed during report generation: %s", exc)
        return Response({'error': str(exc)}, status=500)

    if 'error' in comparison:
        return Response(comparison, status=400)

    # ── AI insights (default on for reports) ───────────────────────────────────
    if include_insights:
        try:
            comparison['ai_insights'] = get_comparison_insights(
                comparison         = comparison,
                customer_type      = customer_type,
                current_from       = current_from,
                current_to         = current_to,
                previous_from      = previous_from,
                previous_to        = previous_to,
                scope_type         = scope_type,
                scope_id           = scope_id,
                positive_threshold = positive_threshold,
                declined_threshold = declined_threshold,
            )
        except Exception as exc:
            logger.warning("AI insights failed during report generation: %s", exc)
            comparison['ai_insights'] = None

    # ── Build report config ────────────────────────────────────────────────────
    ctype_label = {'MDI': 'MDI', 'MDNI': 'MDNI', 'all': 'MDI + MDNI'}.get(customer_type, customer_type)
    scope_label = comparison.get('scope', {}).get('label') or 'KEDCO-wide'

    def _fmt_period(from_d, to_d):
        if from_d == to_d:
            return from_d.strftime('%d %b')
        return f"{from_d.strftime('%d %b')} – {to_d.strftime('%d %b')}"

    curr_label   = _fmt_period(current_from, current_to)
    prev_label   = _fmt_period(previous_from, previous_to)
    year_label   = current_from.strftime('%Y')
    period_label = f"{curr_label} vs {prev_label} {year_label}"

    default_title = f"{ctype_label} Consumption Comparison — {period_label}"
    report_title  = body.get('report_title') or default_title
    filename      = report_title.replace(' ', '_').replace('—', '-')[:60] + '.pdf'

    sections = [
        {'section_type': 'cover_page',                    'config': {}},
        {'section_type': 'commercial_comparison_summary', 'config': {}},
    ]
    if include_insights and comparison.get('ai_insights') and 'error' not in comparison.get('ai_insights', {}):
        sections.append({'section_type': 'commercial_comparison_insights', 'config': {}})
    sections.append({'section_type': 'commercial_comparison_table', 'config': {}})

    report_config = {
        'report_title':    report_title,
        'report_subtitle': f"{ctype_label} customers · {scope_label}",
        'company_name':    body.get('company_name', 'KANO ELECTRICITY DISTRIBUTION COMPANY'),
        'orientation':     body.get('orientation', 'landscape'),
        'theme':           body.get('theme', {}),
        'sections':        sections,
    }

    data_service = ComparisonReportDataService(
        comparison   = comparison,
        current_from = current_from,
        current_to   = current_to,
        scope_label  = scope_label,
    )

    # ── Generate PDF ───────────────────────────────────────────────────────────
    try:
        generator  = PDFGenerator(report_config, data_service)
        pdf_base64 = generator.generate_pdf_base64()
    except Exception as exc:
        logger.exception("PDF generation failed: %s", exc)
        return Response({'error': f'PDF generation failed: {exc}'}, status=500)

    return Response({
        'pdf_base64': pdf_base64,
        'filename':   filename,
        'period_mode': period_mode,
        'customers_in_report': comparison.get('customers_returned', 0),
    })
