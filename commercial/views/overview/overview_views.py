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
    customer_filter_kwargs,
    metric,
    parse_date_range,
    reading_filter_kwargs,
)
from commercial.models import CommercialCustomer, MeterManager, MeterReading


@api_view(['GET'])
def commercial_overview(request):
    date_range = parse_date_range(request)

    # ── Base querysets ────────────────────────────────────────────────────────
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    # ── Customer counts ───────────────────────────────────────────────────────
    total_mdi  = customers_qs.filter(customer_type='MDI').count()
    total_mdni = customers_qs.filter(customer_type='MDNI').count()

    # ── Billing (actual from real readings) ───────────────────────────────────
    billing = calc_billing(readings_qs)

    # ── Daily consumption estimate from actual readings ────────────────────────
    daily_billed_kwh = calc_daily_estimate(billing, date_range)

    # ── Coverage ──────────────────────────────────────────────────────────────
    coverage = calc_coverage(customers_qs, readings_qs)

    # ── Estimated billing for unread customers ────────────────────────────────
    estimated = calc_estimated_billing(customers_qs, coverage['read_ids'], date_range)

    # ── MDI vs MDNI revenue split (from actual readings) ──────────────────────
    mdi_billing  = calc_billing(readings_qs.filter(reading_type='MDI'))
    mdni_billing = calc_billing(readings_qs.filter(reading_type='MDNI'))
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

    # ── Meter managers ────────────────────────────────────────────────────────
    ctype  = request.GET.get('type', '').upper()
    mgr_qs = MeterManager.objects.all()
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
        },

        'energy': {
            'energy_consumed_kwh': metric(
                float(energy_consumed_kwh),
                unit='kWh',
                explanation='Total energy consumed = sum(present_reading - previous_reading) for all customers read in this period. MDI (Maximum Demand Industrial) customers only contribute actual metered values.',
            ),
            'actual_billed_kwh': metric(
                float(billing['total_billed_kwh']),
                unit='kWh',
                explanation='Actual energy billed from real meter readings submitted in this period.',
            ),
            'estimated_billed_kwh': metric(
                float(estimated['estimated_kwh']),
                unit='kWh',
                mode='estimated',
                explanation='Estimated energy for unread customers using last known daily avg (last_billed_consumption / 7) x days in period.',
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
                explanation='Energy delivered vs energy billed. Gap = delivered minus actual billed. Projected includes estimates for unread customers.',
            ),
        },

        'revenue': {
            'actual_energy_charge': metric(
                float(billing['energy_charge']),
                unit='NGN',
                explanation='Actual energy charge from real readings — billed_consumption x tariff_rate per customer.',
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
                explanation='7.5% VAT on actual energy charge.',
            ),
            'actual_total_billed': metric(
                float(billing['total_billed_amount']),
                unit='NGN',
                explanation='Total amount billed from real readings including VAT.',
            ),
            'estimated_revenue': metric(
                float(estimated['estimated_revenue']),
                unit='NGN',
                mode='estimated',
                explanation='Estimated revenue at risk — unread customers x last known daily billed amount (inc. VAT) x days in period.',
            ),
            'total_projected_revenue': metric(
                float(billing['total_billed_amount'] + estimated['estimated_revenue']),
                unit='NGN',
                mode='estimated',
                explanation='Actual billed + estimated for unread customers. Total KEDCO should be collecting if all customers were read.',
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
                explanation='Average Revenue Per Customer — actual total billed divided by customers read in this period.',
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
    })
