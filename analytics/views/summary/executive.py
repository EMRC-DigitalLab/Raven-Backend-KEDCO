# analytics/views/summary/executive.py
from calendar import monthrange
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _prorated_revenue(gcr_total, from_date, to_date):
    """
    The GCR total row gives two real numbers: the FULL MONTH target bill
    value (expected_bill_value, static regardless of date) and the ACTUAL
    bill value from energy really delivered so far (mtd_bill_value, a fact,
    not a projection). Neither of those is "what we should have billed by
    today if on pace with target" -- that requires pro-rating the full-month
    target by how far into the month we are, which nothing computed before
    this. Previously the two existing fields were surfaced as "Expected
    Revenue (MTD)" and "Projected Revenue (MTD)" respectively -- both
    mislabeled (one isn't MTD-scoped at all, the other isn't a projection).
    """
    days_in_month = monthrange(from_date.year, from_date.month)[1]
    days_elapsed = (to_date - from_date).days + 1
    pace_fraction = min(days_elapsed / days_in_month, 1.0)

    target_full_month_bn = round(gcr_total['expected_bill_value'] / 1e9, 3)
    actual_mtd_bn = round(gcr_total['mtd_bill_value'] / 1e9, 3)
    expected_mtd_bn = round(target_full_month_bn * pace_fraction, 3)
    gap_mtd_bn = round(expected_mtd_bn - actual_mtd_bn, 3)

    return {
        'target_full_month_bn': target_full_month_bn,
        'expected_mtd_bn': expected_mtd_bn,
        'actual_mtd_bn': actual_mtd_bn,
        'gap_mtd_bn': gap_mtd_bn,
    }


def _month_range(year, month):
    """First-of-month to last-of-month, clamped to today for the current month."""
    from_date = date(year, month, 1)
    to_date = (from_date + relativedelta(months=1)) - timedelta(days=1)
    today = date.today()
    if to_date > today:
        to_date = today
    return from_date, to_date


def _gap_comparison(current, previous):
    """
    Compares this month's revenue gap to last month's. Returns a dict with
    both a human-readable string AND explicit machine-readable fields, so
    the frontend never has to parse "higher"/"lower" out of prose to decide
    a color/tone — that was fragile and rightly flagged. A SMALLER gap is
    an improvement (closer to/ahead of target); a LARGER gap is worsening,
    regardless of whether the gap itself is positive (behind target) or
    negative (ahead of target).
    """
    if not previous:
        return {'text': None, 'pct': None, 'trend': None}
    delta_pct = ((current - previous) / abs(previous)) * 100
    if abs(round(delta_pct, 1)) == 0:
        trend = 'unchanged'
    else:
        trend = 'worsening' if delta_pct > 0 else 'improving'
    direction_word = 'higher' if delta_pct >= 0 else 'lower'
    return {
        'text': f"{abs(round(delta_pct, 1))}% {direction_word} than last month",
        'pct': round(delta_pct, 1),
        'trend': trend,
    }


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
        from technical.services.fault_analytics import compute_fault_financial_exposure

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
        fault_exposure = compute_fault_financial_exposure(from_date, to_date)

        # Same-length window in the previous month, for a fair MTD-vs-MTD comparison
        days_elapsed = (to_date - from_date).days + 1
        prev_anchor = from_date - relativedelta(months=1)
        prev_from, prev_to = _month_range(prev_anchor.year, prev_anchor.month)
        prev_to = min(prev_to, prev_from + timedelta(days=days_elapsed - 1))
        prev_gcr_total = next(
            r for r in TMOService(prev_from, prev_to).get_gcr()['rows'] if r['segment'] == 'Total'
        )

        revenue = _prorated_revenue(gcr_total, from_date, to_date)
        prev_revenue = _prorated_revenue(prev_gcr_total, prev_from, prev_to)
        gap_comparison = _gap_comparison(revenue['gap_mtd_bn'], prev_revenue['gap_mtd_bn'])

        return Response({
            'period': {'from': str(from_date), 'to': str(to_date)},
            # What we'd have billed by TODAY if on pace with the full-month
            # target (target pro-rated by days elapsed / days in month).
            'expectedRevenueMTD': revenue['expected_mtd_bn'],
            # Real revenue from energy actually delivered so far — a fact,
            # not a projection (kept the old field name for compatibility;
            # the value itself was already correct, only mislabeled).
            'projectedRevenueMTD': revenue['actual_mtd_bn'],
            'actualRevenueMTD': revenue['actual_mtd_bn'],
            # The full month's static target bill value, not date-scoped —
            # this is what used to be surfaced (wrongly) as "Expected Revenue (MTD)".
            'targetRevenueFullMonth': revenue['target_full_month_bn'],
            'revenueGapMTD': revenue['gap_mtd_bn'],
            'revenueGapDeltaText': gap_comparison['text'],
            'revenueGapDeltaPct': gap_comparison['pct'],
            'revenueGapTrend': gap_comparison['trend'],
            'energyDeliveredMTD': gcr_total['consumed_gwh'],
            'energyTargetMTD': gcr_total['target_gwh'],
            # High-level only (total + per-party estimated exposure) — full
            # per-fault breakdown lives at /api/technical/fault-financial-exposure/.
            # Estimate, not an official penalty figure — see that endpoint's
            # docstring for the methodology.
            'totalFaults33kv': fault_exposure['total_faults'],
            'faultsByParty': fault_exposure['faults_by_party'],
            'kedcoEstimatedExposureNaira': fault_exposure['kedco_estimated_exposure_naira'],
            'tcnEstimatedExposureNaira': fault_exposure['tcn_estimated_exposure_naira'],
            'gencoEstimatedExposureNaira': fault_exposure['genco_estimated_exposure_naira'],
            # supplyTarget is 100% by construction — compliance_pct >= 100 is what
            # _compliance() itself treats as "on target" for a single feeder; there's
            # no separately-configured network-wide target elsewhere in the system.
            'supplyCompliance': compliance_summary['compliance_rate_pct'],
            'supplyTarget': 100.0,
        })
