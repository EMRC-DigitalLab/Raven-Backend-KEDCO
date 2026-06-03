# commercial/views/transformers/transformer_views.py
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.analytics_utils import (
    calc_atc_loss,
    calc_billing,
    calc_coverage,
    calc_energy_delivered,
    parse_date_range,
)
from commercial.models import CommercialCustomer, DistributionTransformer, MeterReading
from common.models import Feeder


@api_view(["GET"])
def transformer_metrics_by_feeder_view(request):
    feeder_slug = request.GET.get("feeder")
    if not feeder_slug:
        return Response({"error": "Missing feeder slug"}, status=400)

    try:
        feeder = Feeder.objects.get(slug=feeder_slug)
    except Feeder.DoesNotExist:
        return Response({"error": "Feeder not found"}, status=404)

    date_range = parse_date_range(request)
    p_start    = date_range['start_date']
    p_end      = date_range['end_date']
    days       = date_range['days']

    transformers = DistributionTransformer.objects.filter(feeder=feeder)
    result = []

    for transformer in transformers:
        customers_qs = CommercialCustomer.objects.filter(
            feeder=feeder,
            feeder__commercial_is_onboarded=True,
            # narrow to customers whose transformer matches
            distribution_transformer=transformer,
        )
        readings_qs = MeterReading.objects.filter(
            customer__in=customers_qs,
            reading_date__gte=p_start,
            reading_date__lte=p_end,
        )

        billing   = calc_billing(readings_qs, period_days=days)
        coverage  = calc_coverage(customers_qs, readings_qs)
        delivered = calc_energy_delivered([feeder.id], date_range)

        delivered_kwh    = round(float(delivered['total_mwh']) * 1000, 2)
        billing_eff, atc = calc_atc_loss(billing['total_billed_kwh'], delivered['total_mwh'])

        result.append({
            'name':               transformer.name,
            'slug':               transformer.slug,
            'energy_delivered':   delivered_kwh,
            'energy_billed':      float(round(billing['total_billed_kwh'], 2)),
            'revenue_billed':     float(round(billing['total_billed_amount'], 2)),
            'billing_efficiency': float(round(billing_eff, 2)),
            'atcc':               float(round(atc, 2)),
            'coverage_rate':      coverage['rate'],
            'customers_read':     coverage['read'],
        })

    return Response({
        'period': {
            'mode':       date_range['mode'],
            'start_date': str(p_start),
            'end_date':   str(p_end),
            'label':      date_range['label'],
        },
        'transformers': result,
    })
