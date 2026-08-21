# analytics/views/summary/executive.py
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _month_range(year, month):
    """First-of-month to last-of-month, clamped to today for the current month."""
    from_date = date(year, month, 1)
    to_date = (from_date + relativedelta(months=1)) - timedelta(days=1)
    today = date.today()
    if to_date > today:
        to_date = today
    return from_date, to_date


def _pct_delta_text(current, previous, noun='gap'):
    if not previous:
        return None
    delta = ((current - previous) / abs(previous)) * 100
    direction = 'higher' if delta >= 0 else 'lower'
    return f"{abs(round(delta, 1))}% {direction} than last month"


class ExecutiveSummaryAPIView(APIView):
    """
    GET /api/analytics/summary/executive/?mode=monthly&month=8&year=2026

    MTD executive KPI row: revenue (target vs actual, via TMO's tariff-based
    GCR calculation — energy × TMOMonthlySegmentTarget.average_tariff_per_kwh),
    energy delivered vs target, and network supply compliance.

    Open to any authenticated user — no role restriction.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from tmo.services import TMOService

        try:
            month = int(request.GET.get('month'))
            year = int(request.GET.get('year'))
        except (TypeError, ValueError):
            today = date.today()
            month, year = today.month, today.year

        from_date, to_date = _month_range(year, month)
        svc = TMOService(from_date, to_date)

        gcr_rows = svc.get_gcr()['rows']
        gcr_total = next(r for r in gcr_rows if r['segment'] == 'Total')
        compliance_summary = svc.get_supply_compliance()['summary']

        # Same-length window in the previous month, for a fair MTD-vs-MTD comparison
        prev_anchor = from_date - relativedelta(months=1)
        prev_from, prev_to = _month_range(prev_anchor.year, prev_anchor.month)
        days_elapsed = (to_date - from_date).days
        prev_to = min(prev_to, prev_from + timedelta(days=days_elapsed))
        prev_gcr_total = next(
            r for r in TMOService(prev_from, prev_to).get_gcr()['rows'] if r['segment'] == 'Total'
        )

        expected_revenue_bn = round(gcr_total['expected_bill_value'] / 1e9, 3)
        projected_revenue_bn = round(gcr_total['mtd_bill_value'] / 1e9, 3)
        revenue_gap_bn = round(gcr_total['gap_bill_value'] / 1e9, 3)
        prev_gap_bn = round(prev_gcr_total['gap_bill_value'] / 1e9, 3)

        return Response({
            'period': {'from': str(from_date), 'to': str(to_date)},
            'expectedRevenueMTD': expected_revenue_bn,
            'projectedRevenueMTD': projected_revenue_bn,
            'revenueGapMTD': revenue_gap_bn,
            'revenueGapDeltaText': _pct_delta_text(revenue_gap_bn, prev_gap_bn),
            'energyDeliveredMTD': gcr_total['consumed_gwh'],
            'energyTargetMTD': gcr_total['target_gwh'],
            # supplyTarget is 100% by construction — compliance_pct >= 100 is what
            # _compliance() itself treats as "on target" for a single feeder; there's
            # no separately-configured network-wide target elsewhere in the system.
            'supplyCompliance': compliance_summary['compliance_rate_pct'],
            'supplyTarget': 100.0,
        })
