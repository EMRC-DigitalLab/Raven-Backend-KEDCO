# commercial/views/states/single_state.py
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.analytics_utils import (
    calc_atc_loss,
    calc_billing,
    calc_coverage,
    calc_energy_delivered,
    compute_period_baseline,
    parse_date_range,
)
from commercial.models import CommercialCustomer, MeterReading
from common.models import State


def _prev_periods(date_range, count=4):
    """Return `count` previous periods before date_range, oldest first."""
    mode  = date_range['mode']
    start = date_range['start_date']
    periods = []
    for i in range(count, 0, -1):
        if mode == 'daily':
            s = start - timedelta(days=i)
            e = s
            label = s.strftime('%d %b')
        elif mode == 'weekly':
            s = start - timedelta(weeks=i)
            e = s + timedelta(days=6)
            label = f"{s.strftime('%d %b')} – {e.strftime('%d %b')}"
        elif mode == 'yearly':
            s = date(start.year - i, 1, 1)
            e = date(start.year - i, 12, 31)
            label = str(start.year - i)
        else:  # monthly
            ref = start - relativedelta(months=i)
            s   = date(ref.year, ref.month, 1)
            e   = s + relativedelta(months=1) - timedelta(days=1)
            label = s.strftime('%b %Y')
        periods.append({
            'start_date': s,
            'end_date':   e,
            'label':      label,
            'days':       (e - s).days + 1,
        })
    return periods


def _period_metrics(state, date_range):
    """
    Calculate all commercial metrics for a single period window scoped to a state.
    Uses the same MeterReading-based approach as the overview endpoint.
    """
    p_start = date_range['start_date']
    p_end   = date_range['end_date']
    days    = date_range['days']

    customers_qs = CommercialCustomer.objects.filter(
        feeder__commercial_is_onboarded=True,
        feeder__business_district__state=state,
    )

    readings_qs = MeterReading.objects.filter(
        customer__in=customers_qs,
        reading_date__gte=p_start,
        reading_date__lte=p_end,
    )

    baseline = compute_period_baseline(readings_qs, p_start, p_end)
    billing  = calc_billing(readings_qs, period_days=days, customer_baseline=baseline, period_start=p_start)
    coverage = calc_coverage(customers_qs, readings_qs)

    feeder_ids = list(
        customers_qs.filter(feeder__isnull=False)
        .values_list('feeder_id', flat=True).distinct()
    )
    delivered = calc_energy_delivered(feeder_ids, date_range)

    delivered_kwh = round(float(delivered['total_mwh']) * 1000, 2)
    billing_eff, atc_loss = calc_atc_loss(billing['total_billed_kwh'], delivered['total_mwh'])

    return {
        'period_label':       date_range['label'],
        'period_start':       str(p_start),
        'period_end':         str(p_end),
        'energy_delivered':   delivered_kwh,
        'energy_billed':      float(round(billing['total_billed_kwh'], 2)),
        'revenue_billed':     float(round(billing['total_billed_amount'], 2)),
        'revenue_collected':  float(round(billing['total_billed_amount'], 2)),
        'billing_efficiency': float(round(billing_eff, 2)),
        'atcc_losses':        float(round(atc_loss, 2)),
        'coverage_rate':      coverage['rate'],
        'customers_billed':   coverage['read'],
        'customers_responded': coverage['read'],
    }


def _compute_delta(current, previous):
    if not previous or previous == 0:
        return None
    try:
        return round((current - previous) / previous * 100, 2)
    except Exception:
        return None


@api_view(["GET"])
def commercial_state_metrics_view(request):
    state_name = request.query_params.get("state")
    state = State.objects.filter(name__iexact=state_name).first()
    if not state:
        return Response({"error": "Invalid state"}, status=400)

    # Current period — parsed from request params (mode/year/month/from_date)
    current_dr = parse_date_range(request)

    # 4 previous periods of the same type
    prev_drs = _prev_periods(current_dr, count=4)

    # Fetch metrics for all 5 periods
    trend_periods = [_period_metrics(state, dr) for dr in prev_drs]
    current_period = _period_metrics(state, current_dr)

    # Deltas: current vs immediately preceding period
    if trend_periods:
        prev = trend_periods[-1]
        current_period['deltas'] = {
            key: _compute_delta(current_period.get(key, 0), prev.get(key, 0))
            for key in (
                'energy_delivered', 'energy_billed', 'revenue_billed',
                'revenue_collected', 'billing_efficiency', 'atcc_losses',
                'coverage_rate',
            )
        }

    return Response({
        'mode':    current_dr['mode'],
        'current': current_period,
        'trend':   trend_periods,
    })
