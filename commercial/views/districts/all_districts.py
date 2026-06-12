"""
commercial/views/districts/all_districts.py

GET /api/commercial/districts/
Returns all business districts with commercial metrics.

GET /api/commercial/districts/<slug>/
Returns full metrics for a single business district.
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
    calc_energy_consumed,
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
    bulk_energy_consumed,
    bulk_managers,
    energy_per_feeder,
    rollup_energy,
    empty_billing,
    empty_coverage,
    empty_estimated,
    ZERO,
)
from commercial.models import CommercialCustomer, MeterManager, MeterReading
from common.models import BusinessDistrict, Feeder


def _district_metrics(district, customers_qs, readings_qs, date_range):
    """
    Compute all commercial metrics for a single business district.
    customers_qs and readings_qs are already globally filtered.
    We just add the district scope here.
    """
    c_qs = customers_qs.filter(feeder__business_district=district)
    r_qs = readings_qs.filter(customer__feeder__business_district=district)

    total_mdi    = c_qs.filter(customer_type='MDI').count()
    total_mdni   = c_qs.filter(customer_type='MDNI').count()
    bypass_count = c_qs.filter(is_bypass=True).count()

    billing     = calc_billing(r_qs, period_days=date_range['days'])
    billing_raw = calc_billing(r_qs)  # raw totals for energy gap (no period scaling)
    daily_kwh   = calc_daily_estimate(billing, date_range)
    coverage    = calc_coverage(c_qs, r_qs)
    estimated   = calc_estimated_billing(c_qs, coverage['read_ids'], date_range)

    mdi_billing  = calc_billing(r_qs.filter(reading_type='MDI'),  period_days=date_range['days'])
    mdni_billing = calc_billing(r_qs.filter(reading_type='MDNI'), period_days=date_range['days'])
    total_rev    = billing['total_billed_amount']
    mdi_split  = round(float(mdi_billing['total_billed_amount']) / float(total_rev) * 100, 2) if total_rev else 0
    mdni_split = round(float(mdni_billing['total_billed_amount']) / float(total_rev) * 100, 2) if total_rev else 0

    feeder_ids = list(c_qs.filter(feeder__isnull=False).values_list('feeder_id', flat=True).distinct())
    delivered            = calc_energy_delivered(feeder_ids, date_range)
    delivered_kwh_period = round(float(delivered['total_mwh']) * 1000, 2)
    daily_delivered_mwh  = round(float(delivered['total_mwh']) / date_range['days'], 4) if date_range['days'] else 0
    energy_consumed_kwh  = calc_energy_consumed(r_qs)

    billing_efficiency, atc_loss = calc_atc_loss(
        billing['total_billed_kwh'], delivered['total_mwh']
    )
    arpu = calc_arpu(billing['total_billed_amount'], coverage['read'])

    mdi_mgrs  = MeterManager.objects.filter(
        manager_type='MDI', assignments__feeder__business_district=district
    ).distinct().count()
    mdni_mgrs = MeterManager.objects.filter(
        manager_type='MDNI', assignments__feeder__business_district=district
    ).distinct().count()

    # ── By-feeder energy breakdown ────────────────────────────────────────────
    f2fdr = feeder_dim_map(c_qs, 'feeder_id')
    by_feeder_breakdown = []
    if f2fdr:
        pf_fdr   = energy_per_feeder(list(f2fdr.keys()), date_range)
        e_by_fdr = rollup_energy(pf_fdr, f2fdr)
        b_by_fdr = bulk_billing(r_qs, f2fdr, period_days=date_range["days"])
        c_by_fdr = bulk_energy_consumed(r_qs, f2fdr)
        for fdr in Feeder.objects.filter(id__in=list(f2fdr.keys())).order_by('name'):
            fid     = fdr.id
            ed_f    = e_by_fdr.get(fid, {'total_mwh': 0.0, 'mode': 'system'})
            b_f     = b_by_fdr.get(fid, {'total_billed_kwh': ZERO})
            del_kwh = round(float(ed_f['total_mwh']) * 1000, 2)
            _, atc  = calc_atc_loss(b_f['total_billed_kwh'], ed_f['total_mwh'])
            by_feeder_breakdown.append({
                'feeder': {'slug': fdr.slug, 'name': fdr.name},
                'energy_delivered_kwh': del_kwh,
                'energy_consumed_kwh':  float(c_by_fdr.get(fid, ZERO)),
                'actual_billed_kwh':    float(b_f['total_billed_kwh']),
                'atc_loss':             atc,
                'mode':                 ed_f['mode'],
            })

    return {
        'district': {
            'slug': district.slug,
            'name': district.name,
            'state': district.state.name if district.state else None,
        },
        'customers': {
            'total': metric(total_mdi + total_mdni, explanation='Total registered MDI and MDNI customers in this district.'),
            'mdi':          metric(total_mdi,    explanation='MDI customers in this district.'),
            'mdni':         metric(total_mdni,   explanation='MDNI customers in this district.'),
            'bypass_count': metric(bypass_count, explanation='Customers flagged for meter bypass / tampering in this district.'),
        },
        'energy': {
            'energy_consumed_kwh': metric(
                float(energy_consumed_kwh), unit='kWh',
                explanation='Total energy consumed = sum(present_reading - previous_reading) for all customers read in this period.',
            ),
            'actual_billed_kwh': metric(
                float(billing['actual_billed_kwh']), unit='kWh',
                explanation='Energy billed from real meter readings only (estimation_method is empty) in this district.',
            ),
            'estimated_billed_kwh': metric(
                float(billing['estimated_billed_kwh'] + estimated['estimated_kwh']), unit='kWh', mode='estimated',
                explanation='Estimated energy: DataNest-estimated readings + Raven projection for customers with no reading in this district.',
            ),
            'total_projected_billed_kwh': metric(
                float(billing['total_billed_kwh'] + estimated['estimated_kwh']), unit='kWh', mode='estimated',
                explanation='Actual + estimated energy for this district.',
            ),
            'daily_billed_kwh_estimate': metric(
                float(daily_kwh), unit='kWh/day', mode='estimated',
                explanation='Daily energy billed estimate from actual readings in this district.',
            ),
            'daily_energy_delivered_mwh': metric(
                float(daily_delivered_mwh), unit='MWh/day', mode=delivered['mode'],
                explanation='Average daily energy delivered — total_mwh / days. Source: meter or system fallback.',
            ),
            'energy_delivered_kwh': metric(
                delivered_kwh_period, unit='kWh', mode=delivered['mode'],
                explanation='Total energy delivered for the period from technical module.',
            ),
            'energy_delivered_vs_billed': metric(
                {
                    'delivered_kwh':        delivered_kwh_period,
                    'actual_billed_kwh':    float(billing_raw['total_billed_kwh']),
                    'projected_billed_kwh': float(billing_raw['total_billed_kwh'] + estimated['estimated_kwh']),
                    'gap_kwh':              round(delivered_kwh_period - float(billing_raw['total_billed_kwh']), 2),
                },
                unit='kWh', mode=delivered['mode'],
                explanation='Energy delivered vs billed for this district. Gap = delivered minus actual billed (raw, unscaled).',
            ),
        },
        'revenue': {
            'actual_energy_charge': metric(float(billing['energy_charge']), unit='NGN', explanation='Actual energy charge from real readings in this district.'),
            'estimated_energy_charge': metric(float(estimated['estimated_energy_charge']), unit='NGN', mode='estimated', explanation='Estimated energy charge for unread customers in this district.'),
            'actual_vat': metric(float(billing['vat']), unit='NGN', explanation='7.5% VAT on actual energy charge in this district.'),
            'actual_total_billed': metric(float(billing['total_billed_amount']), unit='NGN', explanation='Revenue confirmed from customers in this district who were physically read this period. Calculated as billed consumption x tariff rate, plus 7.5% VAT. Sourced directly from DataNest meter readings.'),
            'estimated_revenue': metric(float(estimated['estimated_revenue']), unit='NGN', mode='estimated', explanation='Revenue estimated for customers in this district who were NOT read this period. For each unread customer, we take their last known billing amount, divide by the days that reading covered to get a daily rate, then multiply by the days in this period. This figure shrinks as more customers get read.'),
            'total_projected_revenue': metric(
                float(billing['total_billed_amount'] + estimated['estimated_revenue']), unit='NGN', mode='estimated',
                explanation='The full revenue picture for this district: actual billed (read customers) + estimated (unread customers). This is what should be collected if every customer in this district were read and billed this period. As reading coverage improves, the actual portion grows and the estimated portion shrinks.',
            ),
            'mdi_revenue_split':  metric(mdi_split,  unit='%', explanation='% of actual billed revenue from MDI customers in this district.'),
            'mdni_revenue_split': metric(mdni_split, unit='%', explanation='% of actual billed revenue from MDNI customers in this district.'),
            'arpu': metric(float(arpu), unit='NGN', explanation='Average Revenue Per Customer in this district.'),
        },
        'performance': {
            'coverage_rate':    metric(coverage['rate'], unit='%', explanation='% of customers in this district with a reading in this period.'),
            'customers_read':   metric(coverage['read'], explanation='Customers read in this district in this period.'),
            'unread_customers': metric(coverage['unread'], explanation='Customers not read in this district in this period.'),
            'billing_efficiency': metric(billing_efficiency, unit='%', mode='estimated', explanation='Energy billed / energy delivered x 100 for this district.'),
            'atc_loss': metric(atc_loss, unit='%', mode='estimated', explanation='AT&C loss for this district — 100 minus billing efficiency.'),
        },
        'managers': {
            'total_mdi_managers':  metric(mdi_mgrs,  explanation='MDI field officers with assignments in this district.'),
            'total_mdni_managers': metric(mdni_mgrs, explanation='MDNI field officers with assignments in this district.'),
        },
        'energy_breakdown': {
            'by_feeder': by_feeder_breakdown,
        },
    }


@api_view(['GET'])
def all_districts(request):
    """List all business districts with commercial metrics — fixed ~13 queries total."""
    date_range   = parse_date_range(request)
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    state_slug   = request.GET.get('state', '').strip()
    districts_qs = BusinessDistrict.objects.exclude(state__name='Test State').select_related('state').order_by('name')
    if state_slug:
        districts_qs = districts_qs.filter(state__slug=state_slug)
    districts = list(districts_qs)

    # ── One query builds feeder→district map; all bulk fns use it ────────────
    f2d = feeder_dim_map(customers_qs, 'feeder__business_district_id')

    billing_data     = bulk_billing(readings_qs, f2d, period_days=date_range['days'])
    billing_raw_data = bulk_billing(readings_qs, f2d)  # raw for energy gap
    type_billing   = bulk_billing_by_type(readings_qs, f2d)
    ctype_counts   = bulk_customer_types(customers_qs, f2d)
    coverage_data  = bulk_coverage(customers_qs, readings_qs, f2d)
    estimated_data = bulk_estimated_billing(customers_qs, coverage_data, date_range, f2d)
    managers_data  = bulk_managers(f2d)
    energy_data    = bulk_energy_delivered(list(f2d.keys()), date_range, f2d)
    consumed_data  = bulk_energy_consumed(readings_qs, f2d)

    # ── Assemble ──────────────────────────────────────────────────────────────
    days    = date_range['days']
    results = []
    for district in districts:
        did   = district.id
        b     = billing_data.get(did, empty_billing())
        b_raw = billing_raw_data.get(did, empty_billing())
        e     = estimated_data.get(did, empty_estimated())
        cov  = coverage_data.get(did, empty_coverage())
        ct   = ctype_counts.get(did, {'MDI': 0, 'MDNI': 0})
        mgrs = managers_data.get(did, {'mdi': 0, 'mdni': 0})

        ed            = energy_data.get(did, {'total_mwh': 0.0, 'mode': 'system'})
        delivered_kwh = round(float(ed['total_mwh']) * 1000, 2)
        daily_mwh     = round(float(ed['total_mwh']) / days, 4) if days else 0
        daily_kwh     = round(float(b['total_billed_kwh']) / days, 4) if days else 0
        consumed_kwh  = float(consumed_data.get(did, ZERO))

        total_rev  = b['total_billed_amount']
        mdi_amt    = type_billing.get((did, 'MDI'),  ZERO)
        mdni_amt   = type_billing.get((did, 'MDNI'), ZERO)
        mdi_split  = round(float(mdi_amt)  / float(total_rev) * 100, 2) if total_rev else 0
        mdni_split = round(float(mdni_amt) / float(total_rev) * 100, 2) if total_rev else 0

        billing_eff, atc_loss = calc_atc_loss(b['total_billed_kwh'], ed['total_mwh'])
        arpu = calc_arpu(b['total_billed_amount'], cov['read'])

        results.append({
            'district': {'slug': district.slug, 'name': district.name, 'state': district.state.name if district.state else None},
            'customers': {
                'total': metric(ct['MDI'] + ct['MDNI'], explanation='Total registered MDI and MDNI customers in this district.'),
                'mdi':   metric(ct['MDI'],              explanation='MDI customers in this district.'),
                'mdni':  metric(ct['MDNI'],             explanation='MDNI customers in this district.'),
            },
            'energy': {
                'energy_consumed_kwh':        metric(consumed_kwh, unit='kWh', explanation='Total energy consumed = sum(present_reading - previous_reading) for all customers read in this period.'),
                'actual_billed_kwh':          metric(float(b['actual_billed_kwh']), unit='kWh', explanation='Energy billed from real meter readings only (estimation_method is empty) in this district.'),
                'estimated_billed_kwh':       metric(float(b['estimated_billed_kwh'] + e['estimated_kwh']), unit='kWh', mode='estimated', explanation='Estimated energy: DataNest-estimated readings + Raven projection for customers with no reading in this district.'),
                'total_projected_billed_kwh': metric(float(b['total_billed_kwh'] + e['estimated_kwh']), unit='kWh', mode='estimated', explanation='Actual + estimated energy for this district.'),
                'daily_billed_kwh_estimate':  metric(float(daily_kwh), unit='kWh/day', mode='estimated', explanation='Daily energy billed estimate from actual readings in this district.'),
                'daily_energy_delivered_mwh': metric(float(daily_mwh), unit='MWh/day', mode=ed['mode'], explanation='Average daily energy delivered — total_mwh / days. Source: meter or system fallback.'),
                'energy_delivered_kwh': metric(delivered_kwh, unit='kWh', mode=ed['mode'], explanation='Total energy delivered for the period from technical module.'),
                'energy_delivered_vs_billed': metric(
                    {'delivered_kwh': delivered_kwh, 'actual_billed_kwh': float(b_raw['total_billed_kwh']),
                     'projected_billed_kwh': float(b_raw['total_billed_kwh'] + e['estimated_kwh']),
                     'gap_kwh': round(delivered_kwh - float(b_raw['total_billed_kwh']), 2)},
                    unit='kWh', mode=ed['mode'], explanation='Energy delivered vs billed for this district. Gap = delivered minus actual billed (raw, unscaled).',
                ),
            },
            'revenue': {
                'actual_energy_charge':    metric(float(b['energy_charge']), unit='NGN', explanation='Actual energy charge from real readings in this district.'),
                'estimated_energy_charge': metric(float(e['estimated_energy_charge']), unit='NGN', mode='estimated', explanation='Estimated energy charge for unread customers in this district.'),
                'actual_vat':              metric(float(b['vat']), unit='NGN', explanation='7.5% VAT on actual energy charge in this district.'),
                'actual_total_billed':     metric(float(b['total_billed_amount']), unit='NGN', explanation='Revenue confirmed from customers in this district who were physically read this period. Calculated as billed consumption x tariff rate, plus 7.5% VAT. Sourced directly from DataNest meter readings.'),
                'estimated_revenue':       metric(float(e['estimated_revenue']), unit='NGN', mode='estimated', explanation='Revenue estimated for customers in this district who were NOT read this period. For each unread customer, we take their last known billing amount, divide by the days that reading covered to get a daily rate, then multiply by the days in this period. This figure shrinks as more customers get read.'),
                'total_projected_revenue': metric(float(b['total_billed_amount'] + e['estimated_revenue']), unit='NGN', mode='estimated', explanation='The full revenue picture for this district: actual billed (read customers) + estimated (unread customers). This is what should be collected if every customer in this district were read and billed this period. As reading coverage improves, the actual portion grows and the estimated portion shrinks.'),
                'mdi_revenue_split':       metric(mdi_split,   unit='%', explanation='% of actual billed revenue from MDI customers in this district.'),
                'mdni_revenue_split':      metric(mdni_split,  unit='%', explanation='% of actual billed revenue from MDNI customers in this district.'),
                'arpu':                    metric(float(arpu), unit='NGN', explanation='Average Revenue Per Customer in this district.'),
            },
            'performance': {
                'coverage_rate':      metric(cov['rate'],  unit='%', explanation='% of customers in this district with a reading in this period.'),
                'customers_read':     metric(cov['read'],            explanation='Customers read in this district in this period.'),
                'unread_customers':   metric(cov['unread'],          explanation='Customers not read in this district in this period.'),
                'billing_efficiency': metric(billing_eff, unit='%', mode='estimated', explanation='Energy billed / energy delivered x 100 for this district.'),
                'atc_loss':           metric(atc_loss,   unit='%', mode='estimated', explanation='AT&C loss for this district — 100 minus billing efficiency.'),
            },
            'managers': {
                'total_mdi_managers':  metric(mgrs['mdi'],  explanation='MDI field officers with assignments in this district.'),
                'total_mdni_managers': metric(mgrs['mdni'], explanation='MDNI field officers with assignments in this district.'),
            },
        })

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
        },
        'count':     len(results),
        'districts': results,
    })


@api_view(['GET'])
def single_district(request, slug):
    """Full commercial metrics for one business district."""
    try:
        district = BusinessDistrict.objects.get(slug=slug)
    except BusinessDistrict.DoesNotExist:
        return Response({'error': f'District "{slug}" not found.'}, status=status.HTTP_404_NOT_FOUND)

    date_range   = parse_date_range(request)
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    data = _district_metrics(district, customers_qs, readings_qs, date_range)
    data['period'] = {
        'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
        'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
    }
    return Response(data)
