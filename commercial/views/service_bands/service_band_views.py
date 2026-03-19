"""
commercial/views/service_bands/service_band_views.py

GET /api/commercial/bands/
Returns all service bands (A–E) with commercial metrics.

GET /api/commercial/bands/<slug>/
Full metrics for a single service band.
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
from common.models import Band


def _band_metrics(band, customers_qs, readings_qs, date_range):
    """
    Compute all commercial metrics for a single service band.
    Band is a FK on Feeder — so we filter via feeder__band.
    """
    c_qs = customers_qs.filter(feeder__band=band)
    r_qs = readings_qs.filter(customer__feeder__band=band)

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
    daily_delivered_mwh  = calc_energy_delivered(feeder_ids)
    delivered_kwh_period = round(float(daily_delivered_mwh) * 1000 * date_range['days'], 2)

    billing_efficiency, atc_loss = calc_atc_loss(
        billing['total_billed_kwh'], daily_delivered_mwh, date_range['days']
    )
    arpu = calc_arpu(billing['total_billed_amount'], coverage['read'])

    mdi_mgrs  = MeterManager.objects.filter(
        manager_type='MDI', assignments__feeder__band=band
    ).distinct().count()
    mdni_mgrs = MeterManager.objects.filter(
        manager_type='MDNI', assignments__feeder__band=band
    ).distinct().count()

    return {
        'band': {
            'slug': band.slug,
            'name': band.name,
            'description': band.description or '',
        },
        'customers': {
            'total': metric(total_mdi + total_mdni, explanation=f'Total MDI and MDNI customers on Band {band.name} feeders.'),
            'mdi':   metric(total_mdi,  explanation=f'MDI customers on Band {band.name} feeders.'),
            'mdni':  metric(total_mdni, explanation=f'MDNI customers on Band {band.name} feeders.'),
        },
        'energy': {
            'actual_billed_kwh': metric(
                float(billing['total_billed_kwh']), unit='kWh',
                explanation=f'Actual energy billed from real readings on Band {band.name} feeders for this period.',
            ),
            'estimated_billed_kwh': metric(
                float(estimated['estimated_kwh']), unit='kWh', mode='estimated',
                explanation=f'Estimated energy for unread customers on Band {band.name} feeders.',
            ),
            'total_projected_billed_kwh': metric(
                float(billing['total_billed_kwh'] + estimated['estimated_kwh']), unit='kWh', mode='estimated',
                explanation=f'Actual + estimated energy for Band {band.name}.',
            ),
            'daily_billed_kwh_estimate': metric(
                float(daily_kwh), unit='kWh/day', mode='estimated',
                explanation=f'Daily energy billed estimate from actual readings on Band {band.name} feeders.',
            ),
            'daily_energy_delivered_mwh': metric(
                float(daily_delivered_mwh), unit='MWh/day', mode='estimated',
                explanation=f'Daily energy delivered estimate for Band {band.name} — avg of last 90 days of feeder technical readings.',
            ),
            'energy_delivered_vs_billed': metric(
                {
                    'delivered_kwh':        delivered_kwh_period,
                    'actual_billed_kwh':    float(billing['total_billed_kwh']),
                    'projected_billed_kwh': float(billing['total_billed_kwh'] + estimated['estimated_kwh']),
                    'gap_kwh':              round(delivered_kwh_period - float(billing['total_billed_kwh']), 2),
                },
                unit='kWh', mode='estimated',
                explanation=f'Energy delivered vs billed for Band {band.name}. Gap = delivered minus actual billed.',
            ),
        },
        'revenue': {
            'actual_energy_charge':    metric(float(billing['energy_charge']), unit='NGN', explanation=f'Actual energy charge from real readings on Band {band.name}.'),
            'estimated_energy_charge': metric(float(estimated['estimated_energy_charge']), unit='NGN', mode='estimated', explanation=f'Estimated energy charge for unread customers on Band {band.name}.'),
            'actual_vat':              metric(float(billing['vat']), unit='NGN', explanation=f'7.5% VAT on actual energy charge for Band {band.name}.'),
            'actual_total_billed':     metric(float(billing['total_billed_amount']), unit='NGN', explanation=f'Total billed from real readings on Band {band.name} including VAT.'),
            'estimated_revenue':       metric(float(estimated['estimated_revenue']), unit='NGN', mode='estimated', explanation=f'Estimated revenue at risk from unread customers on Band {band.name}.'),
            'total_projected_revenue': metric(
                float(billing['total_billed_amount'] + estimated['estimated_revenue']), unit='NGN', mode='estimated',
                explanation=f'Total projected revenue for Band {band.name} — actual + estimated.',
            ),
            'mdi_revenue_split':  metric(mdi_split,  unit='%', explanation=f'% of actual billed revenue from MDI customers on Band {band.name}.'),
            'mdni_revenue_split': metric(mdni_split, unit='%', explanation=f'% of actual billed revenue from MDNI customers on Band {band.name}.'),
            'arpu': metric(float(arpu), unit='NGN', explanation=f'Average Revenue Per Customer on Band {band.name}.'),
        },
        'performance': {
            'coverage_rate':      metric(coverage['rate'], unit='%', explanation=f'% of customers on Band {band.name} with a reading in this period.'),
            'customers_read':     metric(coverage['read'], explanation=f'Customers read on Band {band.name} in this period.'),
            'unread_customers':   metric(coverage['unread'], explanation=f'Customers not read on Band {band.name} in this period.'),
            'billing_efficiency': metric(billing_efficiency, unit='%', mode='estimated', explanation=f'Energy billed / energy delivered x 100 for Band {band.name}.'),
            'atc_loss':           metric(atc_loss, unit='%', mode='estimated', explanation=f'AT&C loss for Band {band.name} — 100 minus billing efficiency.'),
        },
        'managers': {
            'total_mdi_managers':  metric(mdi_mgrs,  explanation=f'MDI field officers assigned to Band {band.name} feeders.'),
            'total_mdni_managers': metric(mdni_mgrs, explanation=f'MDNI field officers assigned to Band {band.name} feeders.'),
        },
    }


@api_view(['GET'])
def all_bands(request):
    """List all service bands with commercial metrics — fixed ~13 queries total."""
    date_range   = parse_date_range(request)
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    bands = list(Band.objects.all().order_by('name'))

    # ── One query builds feeder→band map; all bulk fns use it ────────────────
    f2d = feeder_dim_map(customers_qs, 'feeder__band_id')

    billing_data   = bulk_billing(readings_qs, f2d)
    type_billing   = bulk_billing_by_type(readings_qs, f2d)
    ctype_counts   = bulk_customer_types(customers_qs, f2d)
    coverage_data  = bulk_coverage(customers_qs, readings_qs, f2d)
    estimated_data = bulk_estimated_billing(customers_qs, coverage_data, date_range, f2d)
    managers_data  = bulk_managers(f2d)
    energy_data    = bulk_energy_delivered(list(f2d.keys()), f2d)

    # ── Assemble ──────────────────────────────────────────────────────────────
    days    = date_range['days']
    results = []
    for band in bands:
        bid  = band.id
        b    = billing_data.get(bid, empty_billing())
        e    = estimated_data.get(bid, empty_estimated())
        cov  = coverage_data.get(bid, empty_coverage())
        ct   = ctype_counts.get(bid, {'MDI': 0, 'MDNI': 0})
        mgrs = managers_data.get(bid, {'mdi': 0, 'mdni': 0})

        daily_mwh     = energy_data.get(bid, ZERO)
        delivered_kwh = round(float(daily_mwh) * 1000 * days, 2)
        daily_kwh     = round(float(b['total_billed_kwh']) / days, 4) if days else 0

        total_rev  = b['total_billed_amount']
        mdi_amt    = type_billing.get((bid, 'MDI'),  ZERO)
        mdni_amt   = type_billing.get((bid, 'MDNI'), ZERO)
        mdi_split  = round(float(mdi_amt)  / float(total_rev) * 100, 2) if total_rev else 0
        mdni_split = round(float(mdni_amt) / float(total_rev) * 100, 2) if total_rev else 0

        billing_eff, atc_loss = calc_atc_loss(b['total_billed_kwh'], daily_mwh, days)
        arpu = calc_arpu(b['total_billed_amount'], cov['read'])

        results.append({
            'band': {'slug': band.slug, 'name': band.name, 'description': band.description or ''},
            'customers': {
                'total': metric(ct['MDI'] + ct['MDNI'], explanation=f'Total MDI and MDNI customers on Band {band.name} feeders.'),
                'mdi':   metric(ct['MDI'],              explanation=f'MDI customers on Band {band.name} feeders.'),
                'mdni':  metric(ct['MDNI'],             explanation=f'MDNI customers on Band {band.name} feeders.'),
            },
            'energy': {
                'actual_billed_kwh':          metric(float(b['total_billed_kwh']), unit='kWh', explanation=f'Actual energy billed from real readings on Band {band.name} feeders for this period.'),
                'estimated_billed_kwh':       metric(float(e['estimated_kwh']), unit='kWh', mode='estimated', explanation=f'Estimated energy for unread customers on Band {band.name} feeders.'),
                'total_projected_billed_kwh': metric(float(b['total_billed_kwh'] + e['estimated_kwh']), unit='kWh', mode='estimated', explanation=f'Actual + estimated energy for Band {band.name}.'),
                'daily_billed_kwh_estimate':  metric(float(daily_kwh), unit='kWh/day', mode='estimated', explanation=f'Daily energy billed estimate from actual readings on Band {band.name} feeders.'),
                'daily_energy_delivered_mwh': metric(float(daily_mwh), unit='MWh/day', mode='estimated', explanation=f'Daily energy delivered estimate for Band {band.name} — avg of last 90 days of feeder technical readings.'),
                'energy_delivered_vs_billed': metric(
                    {'delivered_kwh': delivered_kwh, 'actual_billed_kwh': float(b['total_billed_kwh']),
                     'projected_billed_kwh': float(b['total_billed_kwh'] + e['estimated_kwh']),
                     'gap_kwh': round(delivered_kwh - float(b['total_billed_kwh']), 2)},
                    unit='kWh', mode='estimated', explanation=f'Energy delivered vs billed for Band {band.name}. Gap = delivered minus actual billed.',
                ),
            },
            'revenue': {
                'actual_energy_charge':    metric(float(b['energy_charge']), unit='NGN', explanation=f'Actual energy charge from real readings on Band {band.name}.'),
                'estimated_energy_charge': metric(float(e['estimated_energy_charge']), unit='NGN', mode='estimated', explanation=f'Estimated energy charge for unread customers on Band {band.name}.'),
                'actual_vat':              metric(float(b['vat']), unit='NGN', explanation=f'7.5% VAT on actual energy charge for Band {band.name}.'),
                'actual_total_billed':     metric(float(b['total_billed_amount']), unit='NGN', explanation=f'Total billed from real readings on Band {band.name} including VAT.'),
                'estimated_revenue':       metric(float(e['estimated_revenue']), unit='NGN', mode='estimated', explanation=f'Estimated revenue at risk from unread customers on Band {band.name}.'),
                'total_projected_revenue': metric(float(b['total_billed_amount'] + e['estimated_revenue']), unit='NGN', mode='estimated', explanation=f'Total projected revenue for Band {band.name} — actual + estimated.'),
                'mdi_revenue_split':       metric(mdi_split,   unit='%', explanation=f'% of actual billed revenue from MDI customers on Band {band.name}.'),
                'mdni_revenue_split':      metric(mdni_split,  unit='%', explanation=f'% of actual billed revenue from MDNI customers on Band {band.name}.'),
                'arpu':                    metric(float(arpu), unit='NGN', explanation=f'Average Revenue Per Customer on Band {band.name}.'),
            },
            'performance': {
                'coverage_rate':      metric(cov['rate'],  unit='%', explanation=f'% of customers on Band {band.name} with a reading in this period.'),
                'customers_read':     metric(cov['read'],            explanation=f'Customers read on Band {band.name} in this period.'),
                'unread_customers':   metric(cov['unread'],          explanation=f'Customers not read on Band {band.name} in this period.'),
                'billing_efficiency': metric(billing_eff, unit='%', mode='estimated', explanation=f'Energy billed / energy delivered x 100 for Band {band.name}.'),
                'atc_loss':           metric(atc_loss,   unit='%', mode='estimated', explanation=f'AT&C loss for Band {band.name} — 100 minus billing efficiency.'),
            },
            'managers': {
                'total_mdi_managers':  metric(mgrs['mdi'],  explanation=f'MDI field officers assigned to Band {band.name} feeders.'),
                'total_mdni_managers': metric(mgrs['mdni'], explanation=f'MDNI field officers assigned to Band {band.name} feeders.'),
            },
        })

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
        },
        'count': len(results),
        'bands': results,
    })


@api_view(['GET'])
def single_band(request, slug):
    """Full commercial metrics for one service band."""
    try:
        band = Band.objects.get(slug=slug)
    except Band.DoesNotExist:
        return Response({'error': f'Band "{slug}" not found.'}, status=status.HTTP_404_NOT_FOUND)

    date_range   = parse_date_range(request)
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    data = _band_metrics(band, customers_qs, readings_qs, date_range)
    data['period'] = {
        'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
        'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
    }
    return Response(data)
