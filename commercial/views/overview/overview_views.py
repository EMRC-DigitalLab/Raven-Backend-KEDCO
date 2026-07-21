"""
commercial/views/overview/overview_views.py

GET /api/commercial/overview/

Query params:
  mode         : daily | weekly | monthly | yearly  (default: monthly)
  year, month  : int
  from_date    : YYYY-MM-DD
  type         : MDI | MDNI
  feeder_type  : 11kv | 33kv
  feeder_class : MDI | MDNI | NMD
"""

from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Count
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.analytics_utils import (
    calc_arpu,
    calc_atc_loss,
    calc_billing,
    calc_coverage,
    calc_daily_estimate,
    calc_energy_consumed,
    calc_energy_delivered,
    calc_estimated_billing,
    compute_period_baseline,
    customer_filter_kwargs,
    metric,
    parse_date_range,
    reading_filter_kwargs,
)
from commercial.bulk_analytics import (
    bulk_coverage,
    feeder_dim_map,
    bulk_billing,
    bulk_energy_consumed,
    energy_per_feeder,
    rollup_energy,
    ZERO,
)
from commercial.models import CommercialCustomer, MeterManager, MeterReading
from common.models import Band, BusinessDistrict, State


def _state_prev_periods(date_range, count=3):
    """Return `count` previous periods before date_range, oldest first."""
    mode  = date_range['mode']
    start = date_range['start_date']
    periods = []
    for i in range(count, 0, -1):
        if mode == 'daily':
            s = start - timedelta(days=i)
            e = s
            label = str(s)
        elif mode == 'weekly':
            s = start - timedelta(weeks=i)
            e = s + timedelta(days=6)
            label = f'{s} to {e}'
        elif mode == 'yearly':
            s = date(start.year - i, 1, 1)
            e = date(start.year - i, 12, 31)
            label = str(start.year - i)
        else:  # monthly
            ref = start - relativedelta(months=i)
            s   = date(ref.year, ref.month, 1)
            e   = s + relativedelta(months=1) - timedelta(days=1)
            label = s.strftime('%B %Y')
        periods.append({
            'start_date': s, 'end_date': e,
            'label': label, 'days': (e - s).days + 1,
            'mode': mode, 'is_current': False,
        })
    return periods


@api_view(['GET'])
def commercial_overview(request):
    date_range = parse_date_range(request)

    # ── Base querysets ────────────────────────────────────────────────────────
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    # ── Onboarding status ─────────────────────────────────────────────────────
    from common.models import Feeder
    onboarded_feeders = list(
        Feeder.objects
        .filter(commercial_is_onboarded=True)
        .order_by('commercial_onboarded_at', 'name')
        .values('name', 'slug', 'commercial_onboarded_at')
    )
    onboarding_info = {
        'total_onboarded_feeders': len(onboarded_feeders),
        'data_valid_from':         str(onboarded_feeders[0]['commercial_onboarded_at']) if onboarded_feeders else None,
    }

    # ── Customer counts ───────────────────────────────────────────────────────
    total_mdi    = customers_qs.filter(customer_type='MDI').count()
    total_mdni   = customers_qs.filter(customer_type='MDNI').count()
    bypass_count = customers_qs.filter(is_bypass=True).count()

    # ── Baseline assumption: derive estimated daily rate from surrounding months
    # for first-sync customers whose raw billed_consumption is inflated.
    # All billing figures are estimated — this applies across all periods.
    customer_baseline = compute_period_baseline(
        readings_qs, date_range['start_date'], date_range['end_date']
    )

    # ── Billing ───────────────────────────────────────────────────────────────
    p_start     = date_range['start_date']
    billing     = calc_billing(readings_qs, period_days=date_range['days'], customer_baseline=customer_baseline, period_start=p_start)
    # ── Daily consumption estimate from actual readings ────────────────────────
    daily_billed_kwh = calc_daily_estimate(billing, date_range)

    # ── Coverage ──────────────────────────────────────────────────────────────
    coverage = calc_coverage(customers_qs, readings_qs)

    # ── Estimated billing for unread customers ────────────────────────────────
    estimated = calc_estimated_billing(customers_qs, coverage['read_ids'], date_range)

    # ── MDI vs MDNI revenue split ─────────────────────────────────────────────
    mdi_billing  = calc_billing(readings_qs.filter(reading_type='MDI'),  period_days=date_range['days'], customer_baseline=customer_baseline, period_start=p_start)
    mdni_billing = calc_billing(readings_qs.filter(reading_type='MDNI'), period_days=date_range['days'], customer_baseline=customer_baseline, period_start=p_start)
    total_rev    = billing['total_billed_amount']
    mdi_split  = round(float(mdi_billing['total_billed_amount']) / float(total_rev) * 100, 2) if total_rev else 0
    mdni_split = round(float(mdni_billing['total_billed_amount']) / float(total_rev) * 100, 2) if total_rev else 0

    # ── Energy delivered (actual period sum from technical module) ────────────
    feeder_ids = list(
        customers_qs.filter(feeder__isnull=False)
        .values_list('feeder_id', flat=True).distinct()
    )
    delivered            = calc_energy_delivered(feeder_ids, date_range)
    delivered_kwh_period = round(float(delivered['total_mwh']) * 1000, 2)
    daily_energy_delivered_mwh = round(float(delivered['total_mwh']) / date_range['days'], 4) if date_range['days'] else 0

    # ── Energy consumed (present - previous from meter readings) ──────────────
    energy_consumed_kwh = calc_energy_consumed(readings_qs)

    # ── AT&C loss ─────────────────────────────────────────────────────────────
    billing_efficiency, atc_loss = calc_atc_loss(
        billing['total_billed_kwh'], delivered['total_mwh']
    )

    # ── ARPU ──────────────────────────────────────────────────────────────────
    arpu = calc_arpu(billing['total_billed_amount'], coverage['read'])

    # ── Total feeders with commercial customers ───────────────────────────────
    total_feeders = customers_qs.filter(feeder__isnull=False).values('feeder_id').distinct().count()

    # ── Energy breakdown by state / district / band ───────────────────────────
    f2s    = feeder_dim_map(customers_qs, 'feeder__business_district__state_id')
    f2dmap = feeder_dim_map(customers_qs, 'feeder__business_district_id')
    f2b    = feeder_dim_map(customers_qs, 'feeder__band_id')

    all_breakdown_feeder_ids = list(f2s.keys())
    pf_energy = energy_per_feeder(all_breakdown_feeder_ids, date_range)

    _pd = date_range['days']   # period_days — normalises billing to selected window

    energy_by_state    = rollup_energy(pf_energy, f2s)
    billing_by_state   = bulk_billing(readings_qs, f2s,    period_days=_pd, customer_baseline=customer_baseline, period_start=p_start)
    consumed_by_state  = bulk_energy_consumed(readings_qs, f2s)

    energy_by_district   = rollup_energy(pf_energy, f2dmap)
    billing_by_district  = bulk_billing(readings_qs, f2dmap, period_days=_pd, customer_baseline=customer_baseline, period_start=p_start)
    consumed_by_district = bulk_energy_consumed(readings_qs, f2dmap)

    energy_by_band   = rollup_energy(pf_energy, f2b)
    billing_by_band  = bulk_billing(readings_qs, f2b,    period_days=_pd, customer_baseline=customer_baseline, period_start=p_start)
    consumed_by_band = bulk_energy_consumed(readings_qs, f2b)

    _empty_b = {'total_billed_kwh': ZERO, 'total_billed_amount': ZERO}

    def _bd_row(ed, b, consumed):
        delivered_kwh = round(float(ed['total_mwh']) * 1000, 2)
        _, atc = calc_atc_loss(b['total_billed_kwh'], ed['total_mwh'])
        return {
            'energy_delivered_kwh': delivered_kwh,
            'energy_consumed_kwh':  float(consumed),
            'actual_billed_kwh':    float(b['total_billed_kwh']),
            'atc_loss':             atc,
            'mode':                 ed['mode'],
        }

    # ── State trend: current period coverage + 3 previous periods ────────────
    coverage_by_state = bulk_coverage(customers_qs, readings_qs, f2s)

    trend_periods = _state_prev_periods(date_range, count=3) + [{**date_range, 'is_current': True}]
    trend_start   = trend_periods[0]['start_date']
    trend_end     = trend_periods[-1]['end_date']

    # ONE readings query spanning all 4 periods
    trend_rows = list(
        MeterReading.objects
        .filter(
            customer__in=customers_qs,
            reading_date__gte=trend_start,
            reading_date__lte=trend_end,
            billed_consumption__isnull=False,
            tariff_rate__isnull=False,
        )
        .values(
            'reading_date',
            'customer_id',
            'customer__feeder__business_district__state_id',
            'billed_consumption',
            'tariff_rate',
        )
    )

    # Energy per trend period — reuse current-period result, 3 new calls for prev
    trend_energy_by_period = []
    for p in trend_periods:
        if p.get('is_current'):
            trend_energy_by_period.append(energy_by_state)
        else:
            p_dr = {'start_date': p['start_date'], 'end_date': p['end_date'], 'days': p['days']}
            trend_energy_by_period.append(rollup_energy(energy_per_feeder(all_breakdown_feeder_ids, p_dr), f2s))

    # Readable customers per state (fixed denominator)
    readable_by_state = {
        row['feeder__business_district__state_id']: row['n']
        for row in customers_qs
        .exclude(meter_status__in=('faulty', 'missing'))
        .filter(feeder__business_district__state__isnull=False)
        .values('feeder__business_district__state_id')
        .annotate(n=Count('id'))
    }

    # Bucket trend rows per period → per state in Python (no extra DB hits)
    _ZERO_D = Decimal('0')

    def _bucket_state(rows, p_start, p_end, p_days):
        # Scale raw billed_consumption to the period window (same logic as calc_billing)
        p_scale = Decimal(str(p_days)) / Decimal('30')
        acc = {}
        for r in rows:
            if not (p_start <= r['reading_date'] <= p_end):
                continue
            sid = r['customer__feeder__business_district__state_id']
            if sid is None:
                continue
            if sid not in acc:
                acc[sid] = {'kwh': _ZERO_D, 'ec': _ZERO_D, 'read_ids': set()}
            kwh  = Decimal(str(r['billed_consumption'])) * p_scale
            rate = Decimal(str(r['tariff_rate']))
            acc[sid]['kwh'] += kwh
            acc[sid]['ec']  += kwh * rate
            acc[sid]['read_ids'].add(r['customer_id'])
        return acc

    trend_bill_by_period = [
        _bucket_state(trend_rows, p['start_date'], p['end_date'], p['days'])
        for p in trend_periods
    ]

    by_state_breakdown = []
    for s_obj in State.objects.exclude(name='Test State').order_by('name'):
        sid = s_obj.id
        ed  = energy_by_state.get(sid, {'total_mwh': 0.0, 'mode': 'system'})
        b   = billing_by_state.get(sid, _empty_b)
        cov = coverage_by_state.get(sid, {'read': 0, 'readable': 0, 'rate': 0.0})

        base = _bd_row(ed, b, consumed_by_state.get(sid, ZERO))
        base['revenue_billed']     = float(b.get('total_billed_amount', ZERO))
        base['billing_efficiency'] = 0
        base['coverage_rate']      = cov['rate']

        state_trend = []
        for i, p in enumerate(trend_periods):
            bill = trend_bill_by_period[i].get(sid, {'kwh': _ZERO_D, 'ec': _ZERO_D, 'read_ids': set()})
            t_ed = trend_energy_by_period[i].get(sid, {'total_mwh': 0.0, 'mode': 'system'})
            ec   = float(round(bill['ec'], 2))
            vat  = round(ec * 0.075, 2)
            readable = readable_by_state.get(sid, 0)
            read_ct  = len(bill['read_ids'])
            state_trend.append({
                'period': {
                    'label':      p['label'],
                    'start_date': str(p['start_date']),
                    'end_date':   str(p['end_date']),
                    'is_current': p.get('is_current', False),
                },
                'energy_delivered_kwh': round(float(t_ed['total_mwh']) * 1000, 2),
                'actual_billed_kwh':    float(round(bill['kwh'], 2)),
                'revenue_billed':       round(ec + vat, 2),
                'billing_efficiency':   0,
                'atc_loss':             0,
                'coverage_rate':        round(read_ct / readable * 100, 2) if readable else 0.0,
            })

        by_state_breakdown.append({
            'state': {'slug': s_obj.slug, 'name': s_obj.name},
            **base,
            'trend': state_trend,
        })

    by_district_breakdown = []
    for d_obj in BusinessDistrict.objects.exclude(state__name='Test State').select_related('state').order_by('name'):
        did = d_obj.id
        ed  = energy_by_district.get(did, {'total_mwh': 0.0, 'mode': 'system'})
        b   = billing_by_district.get(did, _empty_b)
        by_district_breakdown.append({
            'district': {
                'slug':  d_obj.slug,
                'name':  d_obj.name,
                'state': d_obj.state.slug if d_obj.state else None,
            },
            **_bd_row(ed, b, consumed_by_district.get(did, ZERO)),
        })

    by_band_breakdown = []
    for band_obj in Band.objects.all().order_by('name'):
        bid = band_obj.id
        ed  = energy_by_band.get(bid, {'total_mwh': 0.0, 'mode': 'system'})
        b   = billing_by_band.get(bid, _empty_b)
        by_band_breakdown.append({
            'band': {'slug': band_obj.slug, 'name': band_obj.name},
            **_bd_row(ed, b, consumed_by_band.get(bid, ZERO)),
        })

    # ── Meter managers (only those assigned to commercially onboarded feeders) ──
    ctype  = request.GET.get('type', '').upper()
    mgr_qs = MeterManager.objects.filter(
        assignments__feeder__commercial_is_onboarded=True
    ).distinct()
    if ctype in ('MDI', 'MDNI'):
        mgr_qs = mgr_qs.filter(manager_type=ctype)
    total_mdi_managers  = mgr_qs.filter(manager_type='MDI').count()
    total_mdni_managers = mgr_qs.filter(manager_type='MDNI').count()

    # ── Response ──────────────────────────────────────────────────────────────
    return Response({
        'period': {
            'mode':       date_range['mode'],
            'start_date': str(date_range['start_date']),
            'end_date':   str(date_range['end_date']),
            'label':      date_range['label'],
            'days':       date_range['days'],
        },

        'customers': {
            'total': metric(
                total_mdi + total_mdni,
                explanation='Total registered MDI and MDNI customers across all feeders.',
            ),
            'mdi': metric(
                total_mdi,
                explanation='Customers classified as Maximum Demand Installation (MDI).',
            ),
            'mdni': metric(
                total_mdni,
                explanation='Customers classified as Non Maximum Demand (MDNI).',
            ),
            'bypass_count': metric(
                bypass_count,
                explanation='Customers flagged for meter bypass / tampering.',
            ),
        },

        'energy': {
            'energy_consumed_kwh': metric(
                float(energy_consumed_kwh),
                unit='kWh',
                explanation='Total energy consumed = sum(present_reading - previous_reading) for all customers read in this period. MDI (Maximum Demand Industrial) customers only contribute actual metered values.',
            ),
            'estimated_billed_kwh_read': metric(
                float(billing['total_billed_kwh']),
                unit='kWh',
                mode='estimated',
                explanation='Estimated energy billed from customers read this period.',
            ),
            'estimated_billed_kwh': metric(
                float(billing['estimated_billed_kwh'] + estimated['estimated_kwh']),
                unit='kWh',
                mode='estimated',
                explanation='Estimated energy: DataNest-estimated readings + Raven projection for customers with no reading this period.',
            ),
            'total_projected_billed_kwh': metric(
                float(billing['total_billed_kwh'] + estimated['estimated_kwh']),
                unit='kWh',
                mode='estimated',
                explanation='Actual billed + estimated for unread customers. Full projected energy KEDCO should be billing.',
            ),
            'daily_billed_kwh_estimate': metric(
                float(daily_billed_kwh),
                unit='kWh/day',
                mode='estimated',
                explanation='Daily energy billed estimate from actual readings only — total actual billed kWh divided by days in period.',
            ),
            'daily_energy_delivered_mwh': metric(
                float(daily_energy_delivered_mwh),
                unit='MWh/day',
                mode=delivered['mode'],
                explanation='Average daily energy delivered for the period — total_mwh divided by days. Source: meter if EnergyDelivered records exist and pass outlier check, system (HourlyLoad) otherwise.',
            ),
            'energy_delivered_kwh': metric(
                delivered_kwh_period,
                unit='kWh',
                mode=delivered['mode'],
                explanation='Total energy delivered for the period — EnergyDelivered meter sum × 1000. Falls back to HourlyLoad estimate if no valid meter data.',
            ),
            'energy_delivered_vs_billed': metric(
                {
                    'delivered_kwh':        delivered_kwh_period,
                    'actual_billed_kwh':    float(billing['total_billed_kwh']),
                    'projected_billed_kwh': float(billing['total_billed_kwh'] + estimated['estimated_kwh']),
                    'gap_kwh':              round(delivered_kwh_period - float(billing['total_billed_kwh']), 2),
                },
                unit='kWh',
                mode=delivered['mode'],
                explanation='Energy delivered vs energy billed. Gap = delivered minus period-normalised billed kWh. Projected adds estimates for unread customers.',
            ),
        },

        'revenue': {
            'actual_energy_charge': metric(
                float(billing['energy_charge']),
                unit='NGN',
                mode='estimated',
                explanation='Estimated energy charge from meter readings — billed_consumption x tariff_rate per customer, baseline-adjusted for first-sync periods.',
            ),
            'estimated_energy_charge': metric(
                float(estimated['estimated_energy_charge']),
                unit='NGN',
                mode='estimated',
                explanation='Estimated energy charge for unread customers based on their last known daily average.',
            ),
            'actual_vat': metric(
                float(billing['vat']),
                unit='NGN',
                mode='estimated',
                explanation='7.5% VAT on estimated energy charge.',
            ),
            'actual_total_billed': metric(
                float(billing['total_billed_amount']),
                unit='NGN',
                mode='estimated',
                explanation=(
                    'Estimated revenue from customers read this period. '
                    'Calculated as: billed consumption (kWh) x tariff rate per customer, plus 7.5% VAT. '
                    'First-sync customer values are baseline-adjusted from surrounding months.'
                ),
            ),
            'estimated_revenue': metric(
                float(estimated['estimated_revenue']),
                unit='NGN',
                mode='estimated',
                explanation=(
                    'Revenue estimated for customers who were NOT read this period. '
                    'For each unread customer, we take their last known billing amount, divide by the number of days '
                    'that reading covered to get a daily rate, then multiply by the days in this period. '
                    'Billing correction entries (negative consumption) are excluded. '
                    'This figure shrinks as more customers get read.'
                ),
            ),
            'total_projected_revenue': metric(
                float(billing['total_billed_amount'] + estimated['estimated_revenue']),
                unit='NGN',
                mode='estimated',
                explanation=(
                    'The full revenue picture: actual billed (from read customers) + estimated (for unread customers). '
                    'This is what KEDCO should expect to collect if every registered customer were read and billed this period. '
                    'As reading coverage improves, the actual portion grows and the estimated portion shrinks.'
                ),
            ),
            'mdi_revenue_split': metric(
                mdi_split,
                unit='%',
                explanation='Percentage of actual billed revenue contributed by MDI customers.',
            ),
            'mdni_revenue_split': metric(
                mdni_split,
                unit='%',
                explanation='Percentage of actual billed revenue contributed by MDNI customers.',
            ),
            'arpu': metric(
                float(arpu),
                unit='NGN',
                mode='estimated',
                explanation='Average Revenue Per Customer — estimated total billed divided by customers read in this period.',
            ),
        },

        'performance': {
            'coverage_rate': metric(
                coverage['rate'],
                unit='%',
                explanation='Percentage of registered customers who had at least one reading submitted in this period.',
            ),
            'customers_read': metric(
                coverage['read'],
                explanation='Number of customers with a reading submitted in this period.',
            ),
            'unread_customers': metric(
                coverage['unread'],
                explanation='Customers with no reading in this period — direct revenue leakage risk.',
            ),
            'billing_efficiency': metric(
                billing_efficiency,
                unit='%',
                mode='estimated',
                explanation='Percentage of energy delivered that was billed — (energy_billed / energy_delivered) x 100.',
            ),
            'atc_loss': metric(
                atc_loss,
                unit='%',
                mode='estimated',
                explanation='Aggregate Technical and Commercial loss — 100 minus billing efficiency. Energy delivered but not captured in billing.',
            ),
        },

        'managers': {
            'total_mdi_managers': metric(
                total_mdi_managers,
                explanation='Number of field officers assigned to read MDI meters.',
            ),
            'total_mdni_managers': metric(
                total_mdni_managers,
                explanation='Number of field officers assigned to read MDNI meters.',
            ),
        },

        # ── Actual snapshot ───────────────────────────────────────────────────
        # Single object the frontend can use for the 4 primary overview cards.
        # Values here are ALWAYS sourced from real meter readings for the
        # selected period — no estimates or projections included.
        # The 'revenue_mode' flag tells the frontend whether to label revenue
        # as "Actual" (full coverage) or "Partial Actual" (some customers unread).
        'actual_snapshot': {
            'energy_delivered_kwh': metric(
                delivered_kwh_period,
                unit='kWh',
                mode=delivered['mode'],
                explanation='Total energy delivered in the selected period. Mode indicates data source: actual (meter), system (HourlyLoad), or mixed.',
            ),
            'actual_revenue': metric(
                float(billing['total_billed_amount']),
                unit='NGN',
                mode='estimated',
                explanation='Estimated revenue from customers read this period, baseline-adjusted for first-sync periods.',
            ),
            'projected_revenue': metric(
                float(billing['total_billed_amount'] + estimated['estimated_revenue']),
                unit='NGN',
                mode='estimated',
                explanation='Full projected revenue: actual (read customers) + estimated (unread customers). Shrinks toward actual as coverage improves.',
            ),
            'customers_read': coverage['read'],
            'total_customers': coverage['read'] + coverage['unread'],
            'coverage_rate': coverage['rate'],
            'revenue_mode': 'estimated',
            'period_label': date_range['label'],
        },

        'total_feeders': total_feeders,
        'onboarding':    onboarding_info,

        'energy_breakdown': {
            'by_state':    by_state_breakdown,
            'by_district': by_district_breakdown,
            'by_band':     by_band_breakdown,
        },

        # ── Methodology ───────────────────────────────────────────────────────
        # Documents exactly how each key metric is derived so that any figure
        # can be traced back to its source data and formula.
        'methodology': {
            'billing': {
                'summary': (
                    'All billing figures are estimates. Every reading from DataNest is normalised '
                    'to the selected period window so that monthly, weekly, and daily views are '
                    'directly comparable.'
                ),
                'formula': 'period_kwh = billed_consumption ÷ billing_cycle_days × period_days',
                'billing_cycle_days': (
                    'For each customer, billing_cycle_days = days between their current reading '
                    'and their most recent prior reading in Raven\'s database (capped at 1 day minimum). '
                    'Lookup cutoff is always the period start date so the result is stable across all views.'
                ),
                'first_sync_customers': (
                    'Customers whose first DataNest reading falls within the selected period have no '
                    'prior reading in the database. Their raw billed_consumption therefore represents '
                    'accumulated consumption since meter installation — not a single billing cycle — '
                    'and cannot be divided by a meaningful billing_cycle_days. '
                    'For these customers, Raven derives an estimated daily rate from the 60-day window '
                    'immediately after the period end (forward baseline). If no data exists in that window, '
                    'it falls back to the 60-day window before the period start (backward baseline). '
                    'period_kwh = estimated_daily_kwh × period_days.'
                ),
                'fallback': (
                    'If a customer has no prior reading and no baseline data in either window, '
                    'billed_consumption ÷ 30 × period_days is used as a last resort.'
                ),
            },
            'revenue': {
                'summary': (
                    'Revenue is computed per customer then summed. '
                    'energy_charge = period_kwh × tariff_rate. '
                    'VAT = energy_charge × 7.5%. '
                    'total_billed = energy_charge + VAT.'
                ),
                'tariff_rate': (
                    'Each reading carries the tariff rate recorded in DataNest at the time of reading. '
                    'Raven uses that rate directly — no override is applied.'
                ),
                'estimated_revenue': (
                    'For customers not read this period, Raven projects revenue using their last known '
                    'daily rate: daily_kwh = last_billed_consumption ÷ interval_days, where interval_days '
                    'is the gap between their two most recent positive readings (falls back to the number '
                    'of days in that reading\'s calendar month if only one reading exists). '
                    'estimated_revenue = daily_kwh × tariff_rate × (1 + 0.075) × period_days.'
                ),
                'projected_revenue': (
                    'total_projected_revenue = actual_billed (from read customers) + estimated_revenue '
                    '(for unread customers). As field coverage improves, the estimated portion shrinks '
                    'and actual portion grows.'
                ),
            },
            'energy_gap': {
                'summary': (
                    'energy_gap = energy_delivered_kwh − billed_kwh_raw. '
                    'A positive gap means more energy left the grid than was billed — '
                    'the difference represents AT&C losses, unmetered consumption, or bypass. '
                    'A value close to zero indicates strong commercial discipline.'
                ),
                'billed_kwh_raw': (
                    'billed_kwh_raw is the period-normalised billed energy: each customer\'s '
                    'billed_consumption is scaled to the selected window using their billing cycle length '
                    '(billed_consumption ÷ billing_cycle_days × period_days). '
                    'This makes the comparison with energy_delivered period-consistent.'
                ),
                'energy_delivered': (
                    'Primary source: EnergyDelivered table (meter-based daily totals). '
                    'Feeders where the maximum single-day value exceeds 500 MWh are treated as outliers '
                    'and fall back to HourlyLoad avg_load × supply_hours. '
                    'The mode field indicates which source was used: meter, system, or mixed.'
                ),
            },
            'coverage_rate': {
                'formula': 'coverage_rate = customers_read ÷ readable_customers × 100',
                'readable_customers': (
                    'Total registered customers minus those with meter_status = faulty or missing. '
                    'Faulty and missing meters are excluded because they cannot physically be read '
                    'and should not penalise the field team\'s coverage score.'
                ),
            },
            'period_normalisation': {
                'summary': (
                    f'Selected period: {date_range["label"]} ({date_range["days"]} days, '
                    f'{str(date_range["start_date"])} to {str(date_range["end_date"])}). '
                    'All billing and revenue figures are scaled to this window so that a 31-day month '
                    'and a 28-day month produce comparable per-period totals.'
                ),
            },
        },
    })
