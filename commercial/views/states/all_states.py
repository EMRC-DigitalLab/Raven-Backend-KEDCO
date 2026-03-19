"""
commercial/views/states/all_states.py

GET /api/commercial/states/
Returns all states with commercial metrics — same KPIs as overview scoped per state.

GET /api/commercial/states/<slug>/
Returns full metrics for a single state.
"""

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from commercial.analytics_utils import (
    calc_arpu,
    calc_atc_loss,
    calc_billing,
    calc_coverage,
    calc_daily_estimate,
    calc_energy_delivered,
    calc_estimated_billing,
    customer_filter_kwargs,
    metric,
    parse_date_range,
    reading_filter_kwargs,
)
from commercial.bulk_analytics import (
    feeder_dim_map,
    bulk_billing,
    bulk_billing_by_type,
    bulk_customer_types,
    bulk_coverage,
    bulk_estimated_billing,
    bulk_energy_delivered,
    bulk_managers,
    empty_billing,
    empty_coverage,
    empty_estimated,
    ZERO,
)
from commercial.models import CommercialCustomer, MeterManager, MeterReading
from common.models import State


def _state_metrics(state, customers_qs, readings_qs, date_range):
    """
    Compute all commercial metrics for a single state.
    customers_qs and readings_qs are already globally filtered (type, feeder_type, etc.)
    We just add the state scope here.
    """
    c_qs = customers_qs.filter(feeder__business_district__state=state)
    r_qs = readings_qs.filter(customer__feeder__business_district__state=state)

    total_mdi  = c_qs.filter(customer_type='MDI').count()
    total_mdni = c_qs.filter(customer_type='MDNI').count()

    billing   = calc_billing(r_qs)
    daily_kwh = calc_daily_estimate(billing, date_range)
    coverage  = calc_coverage(c_qs, r_qs)
    estimated = calc_estimated_billing(c_qs, coverage['read_ids'], date_range)

    mdi_billing  = calc_billing(r_qs.filter(reading_type='MDI'))
    mdni_billing = calc_billing(r_qs.filter(reading_type='MDNI'))
    total_rev    = billing['total_billed_amount']
    mdi_split  = round(float(mdi_billing['total_billed_amount']) / float(total_rev) * 100, 2) if total_rev else 0
    mdni_split = round(float(mdni_billing['total_billed_amount']) / float(total_rev) * 100, 2) if total_rev else 0

    feeder_ids = list(c_qs.filter(feeder__isnull=False).values_list('feeder_id', flat=True).distinct())
    daily_delivered_mwh = calc_energy_delivered(feeder_ids)
    delivered_kwh_period = round(float(daily_delivered_mwh) * 1000 * date_range['days'], 2)

    billing_efficiency, atc_loss = calc_atc_loss(
        billing['total_billed_kwh'], daily_delivered_mwh, date_range['days']
    )
    arpu = calc_arpu(billing['total_billed_amount'], coverage['read'])

    mdi_mgrs  = MeterManager.objects.filter(
        manager_type='MDI', assignments__feeder__business_district__state=state
    ).distinct().count()
    mdni_mgrs = MeterManager.objects.filter(
        manager_type='MDNI', assignments__feeder__business_district__state=state
    ).distinct().count()

    return {
        'state': {'slug': state.slug, 'name': state.name},
        'customers': {
            'total': metric(total_mdi + total_mdni, explanation='Total registered MDI and MDNI customers in this state.'),
            'mdi':   metric(total_mdi,  explanation='MDI customers in this state.'),
            'mdni':  metric(total_mdni, explanation='MDNI customers in this state.'),
        },
        'energy': {
            'actual_billed_kwh': metric(
                float(billing['total_billed_kwh']), unit='kWh',
                explanation='Actual energy billed from real readings in this state for this period.',
            ),
            'estimated_billed_kwh': metric(
                float(estimated['estimated_kwh']), unit='kWh', mode='estimated',
                explanation='Estimated energy for unread customers in this state.',
            ),
            'total_projected_billed_kwh': metric(
                float(billing['total_billed_kwh'] + estimated['estimated_kwh']), unit='kWh', mode='estimated',
                explanation='Actual + estimated energy for this state.',
            ),
            'daily_billed_kwh_estimate': metric(
                float(daily_kwh), unit='kWh/day', mode='estimated',
                explanation='Daily energy billed estimate from actual readings in this state.',
            ),
            'daily_energy_delivered_mwh': metric(
                float(daily_delivered_mwh), unit='MWh/day', mode='estimated',
                explanation='Daily energy delivered estimate for this state — avg of last 90 days of feeder technical readings.',
            ),
            'energy_delivered_kwh': metric(
                delivered_kwh_period, unit='kWh', mode='estimated',
                explanation='Total energy delivered for the period in this state — daily_energy_delivered_mwh × 1000 × days.',
            ),
            'energy_delivered_vs_billed': metric(
                {
                    'delivered_kwh':        delivered_kwh_period,
                    'actual_billed_kwh':    float(billing['total_billed_kwh']),
                    'projected_billed_kwh': float(billing['total_billed_kwh'] + estimated['estimated_kwh']),
                    'gap_kwh':              round(delivered_kwh_period - float(billing['total_billed_kwh']), 2),
                },
                unit='kWh', mode='estimated',
                explanation='Energy delivered vs billed for this state. Gap = delivered minus actual billed.',
            ),
        },
        'revenue': {
            'actual_energy_charge': metric(float(billing['energy_charge']), unit='NGN', explanation='Actual energy charge from real readings in this state.'),
            'estimated_energy_charge': metric(float(estimated['estimated_energy_charge']), unit='NGN', mode='estimated', explanation='Estimated energy charge for unread customers in this state.'),
            'actual_vat': metric(float(billing['vat']), unit='NGN', explanation='7.5% VAT on actual energy charge in this state.'),
            'actual_total_billed': metric(float(billing['total_billed_amount']), unit='NGN', explanation='Total billed from real readings in this state including VAT.'),
            'estimated_revenue': metric(float(estimated['estimated_revenue']), unit='NGN', mode='estimated', explanation='Estimated revenue at risk from unread customers in this state.'),
            'total_projected_revenue': metric(
                float(billing['total_billed_amount'] + estimated['estimated_revenue']), unit='NGN', mode='estimated',
                explanation='Total projected revenue for this state — actual + estimated.',
            ),
            'mdi_revenue_split':  metric(mdi_split,  unit='%', explanation='% of actual billed revenue from MDI customers in this state.'),
            'mdni_revenue_split': metric(mdni_split, unit='%', explanation='% of actual billed revenue from MDNI customers in this state.'),
            'arpu': metric(float(arpu), unit='NGN', explanation='Average Revenue Per Customer in this state.'),
        },
        'performance': {
            'coverage_rate':    metric(coverage['rate'], unit='%', explanation='% of customers in this state with a reading in this period.'),
            'customers_read':   metric(coverage['read'], explanation='Customers read in this state in this period.'),
            'unread_customers': metric(coverage['unread'], explanation='Customers not read in this state in this period.'),
            'billing_efficiency': metric(billing_efficiency, unit='%', mode='estimated', explanation='Energy billed / energy delivered x 100 for this state.'),
            'atc_loss': metric(atc_loss, unit='%', mode='estimated', explanation='AT&C loss for this state — 100 minus billing efficiency.'),
        },
        'managers': {
            'total_mdi_managers':  metric(mdi_mgrs,  explanation='MDI field officers with assignments in this state.'),
            'total_mdni_managers': metric(mdni_mgrs, explanation='MDNI field officers with assignments in this state.'),
        },
    }


@api_view(['GET'])
def all_states(request):
    """List all states with commercial metrics — fixed ~13 queries total."""
    date_range   = parse_date_range(request)
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    states = list(State.objects.exclude(name='Test State').order_by('name'))

    # ── One query builds feeder→state map; all bulk fns use it ───────────────
    f2d = feeder_dim_map(customers_qs, 'feeder__business_district__state_id')

    billing_data   = bulk_billing(readings_qs, f2d)
    type_billing   = bulk_billing_by_type(readings_qs, f2d)
    ctype_counts   = bulk_customer_types(customers_qs, f2d)
    coverage_data  = bulk_coverage(customers_qs, readings_qs, f2d)
    estimated_data = bulk_estimated_billing(customers_qs, coverage_data, date_range, f2d)
    managers_data  = bulk_managers(f2d)
    energy_data    = bulk_energy_delivered(list(f2d.keys()), f2d)

    # ── Assemble per-state from pre-aggregated buckets ────────────────────────
    days    = date_range['days']
    results = []
    for state in states:
        sid  = state.id
        b    = billing_data.get(sid, empty_billing())
        e    = estimated_data.get(sid, empty_estimated())
        cov  = coverage_data.get(sid, empty_coverage())
        ct   = ctype_counts.get(sid, {'MDI': 0, 'MDNI': 0})
        mgrs = managers_data.get(sid, {'mdi': 0, 'mdni': 0})

        daily_mwh      = energy_data.get(sid, ZERO)
        delivered_kwh  = round(float(daily_mwh) * 1000 * days, 2)
        daily_kwh      = round(float(b['total_billed_kwh']) / days, 4) if days else 0

        total_rev  = b['total_billed_amount']
        mdi_amt    = type_billing.get((sid, 'MDI'),  ZERO)
        mdni_amt   = type_billing.get((sid, 'MDNI'), ZERO)
        mdi_split  = round(float(mdi_amt)  / float(total_rev) * 100, 2) if total_rev else 0
        mdni_split = round(float(mdni_amt) / float(total_rev) * 100, 2) if total_rev else 0

        billing_eff, atc_loss = calc_atc_loss(b['total_billed_kwh'], daily_mwh, days)
        arpu = calc_arpu(b['total_billed_amount'], cov['read'])

        results.append({
            'state': {'slug': state.slug, 'name': state.name},
            'customers': {
                'total': metric(ct['MDI'] + ct['MDNI'], explanation='Total registered MDI and MDNI customers in this state.'),
                'mdi':   metric(ct['MDI'],              explanation='MDI customers in this state.'),
                'mdni':  metric(ct['MDNI'],             explanation='MDNI customers in this state.'),
            },
            'energy': {
                'actual_billed_kwh': metric(float(b['total_billed_kwh']), unit='kWh', explanation='Actual energy billed from real readings in this state for this period.'),
                'estimated_billed_kwh': metric(float(e['estimated_kwh']), unit='kWh', mode='estimated', explanation='Estimated energy for unread customers in this state.'),
                'total_projected_billed_kwh': metric(float(b['total_billed_kwh'] + e['estimated_kwh']), unit='kWh', mode='estimated', explanation='Actual + estimated energy for this state.'),
                'daily_billed_kwh_estimate': metric(float(daily_kwh), unit='kWh/day', mode='estimated', explanation='Daily energy billed estimate from actual readings in this state.'),
                'daily_energy_delivered_mwh': metric(float(daily_mwh), unit='MWh/day', mode='estimated', explanation='Daily energy delivered estimate for this state — avg of last 90 days of feeder technical readings.'),
                'energy_delivered_kwh': metric(delivered_kwh, unit='kWh', mode='estimated', explanation='Total energy delivered for the period in this state — daily_energy_delivered_mwh × 1000 × days.'),
                'energy_delivered_vs_billed': metric(
                    {'delivered_kwh': delivered_kwh, 'actual_billed_kwh': float(b['total_billed_kwh']),
                     'projected_billed_kwh': float(b['total_billed_kwh'] + e['estimated_kwh']),
                     'gap_kwh': round(delivered_kwh - float(b['total_billed_kwh']), 2)},
                    unit='kWh', mode='estimated', explanation='Energy delivered vs billed for this state. Gap = delivered minus actual billed.',
                ),
            },
            'revenue': {
                'actual_energy_charge':    metric(float(b['energy_charge']), unit='NGN', explanation='Actual energy charge from real readings in this state.'),
                'estimated_energy_charge': metric(float(e['estimated_energy_charge']), unit='NGN', mode='estimated', explanation='Estimated energy charge for unread customers in this state.'),
                'actual_vat':              metric(float(b['vat']), unit='NGN', explanation='7.5% VAT on actual energy charge in this state.'),
                'actual_total_billed':     metric(float(b['total_billed_amount']), unit='NGN', explanation='Total billed from real readings in this state including VAT.'),
                'estimated_revenue':       metric(float(e['estimated_revenue']), unit='NGN', mode='estimated', explanation='Estimated revenue at risk from unread customers in this state.'),
                'total_projected_revenue': metric(float(b['total_billed_amount'] + e['estimated_revenue']), unit='NGN', mode='estimated', explanation='Total projected revenue for this state — actual + estimated.'),
                'mdi_revenue_split':       metric(mdi_split,       unit='%', explanation='% of actual billed revenue from MDI customers in this state.'),
                'mdni_revenue_split':      metric(mdni_split,      unit='%', explanation='% of actual billed revenue from MDNI customers in this state.'),
                'arpu':                    metric(float(arpu),     unit='NGN', explanation='Average Revenue Per Customer in this state.'),
            },
            'performance': {
                'coverage_rate':      metric(cov['rate'],    unit='%', explanation='% of customers in this state with a reading in this period.'),
                'customers_read':     metric(cov['read'],              explanation='Customers read in this state in this period.'),
                'unread_customers':   metric(cov['unread'],            explanation='Customers not read in this state in this period.'),
                'billing_efficiency': metric(billing_eff, unit='%', mode='estimated', explanation='Energy billed / energy delivered x 100 for this state.'),
                'atc_loss':           metric(atc_loss,   unit='%', mode='estimated', explanation='AT&C loss for this state — 100 minus billing efficiency.'),
            },
            'managers': {
                'total_mdi_managers':  metric(mgrs['mdi'],  explanation='MDI field officers with assignments in this state.'),
                'total_mdni_managers': metric(mgrs['mdni'], explanation='MDNI field officers with assignments in this state.'),
            },
        })

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
        },
        'count':  len(results),
        'states': results,
    })


@api_view(['GET'])
def single_state(request, slug):
    """Full commercial metrics for one state."""
    try:
        state = State.objects.get(slug=slug)
    except State.DoesNotExist:
        return Response({'error': f'State "{slug}" not found.'}, status=status.HTTP_404_NOT_FOUND)

    date_range   = parse_date_range(request)
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    data = _state_metrics(state, customers_qs, readings_qs, date_range)
    data['period'] = {
        'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
        'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
    }
    return Response(data)
