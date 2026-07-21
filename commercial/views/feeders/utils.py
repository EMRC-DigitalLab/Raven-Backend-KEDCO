# commercial/views/feeders/utils.py
from commercial.analytics_utils import calc_atc_loss, calc_billing, calc_energy_delivered, compute_period_baseline
from commercial.models import CommercialCustomer, MeterReading


def calculate_atcc_metrics(feeder, start_date, end_date):
    """
    Calculate AT&C metrics for a single feeder for the given date range.
    Uses MeterReading queryset + calc_billing(period_days) so the numbers
    are correctly proportioned to the actual period — daily, weekly, or monthly.
    """
    days = max((end_date - start_date).days, 1)
    date_range = {
        'start_date': start_date,
        'end_date':   end_date,
        'days':       days,
        'mode':       'custom',
    }

    customers_qs = CommercialCustomer.objects.filter(
        feeder=feeder,
        feeder__commercial_is_onboarded=True,
    )
    readings_qs = MeterReading.objects.filter(
        customer__in=customers_qs,
        reading_date__gte=start_date,
        reading_date__lte=end_date,
    )

    baseline  = compute_period_baseline(readings_qs, start_date, end_date)
    billing   = calc_billing(readings_qs, period_days=days, customer_baseline=baseline, period_start=start_date)
    delivered = calc_energy_delivered([feeder.id], date_range)

    delivered_kwh    = round(float(delivered['total_mwh']) * 1000, 2)
    billing_eff, atc = calc_atc_loss(billing['total_billed_kwh'], delivered['total_mwh'])

    return {
        'id':                  str(feeder.id),
        'name':                feeder.name,
        'state':               feeder.business_district.state.name if feeder.business_district else None,
        'business_district':   feeder.business_district.name if feeder.business_district else None,
        'substation':          feeder.substation.name if feeder.substation else None,
        'energy_delivered':    delivered_kwh,
        'energy_billed':       float(round(billing['total_billed_kwh'], 2)),
        'revenue_billed':      float(round(billing['total_billed_amount'], 2)),
        'billing_efficiency':  float(round(billing_eff, 2)),
        'atcc':                float(round(atc, 2)),
        'voltage_level':       feeder.voltage_level,
    }
