# commercial/views/districts/all_districts.py
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.models import *
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from commercial.serializers import *
from common.models import BusinessDistrict, Feeder
from technical.models import FeederEnergyDaily, FeederEnergyMonthly


@api_view(["GET"])
def commercial_all_business_districts_view(request):
    state_name = request.query_params.get("state")
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))

    period_start = date(year, month, 1)

    if not state_name:
        return Response({"error": "State parameter is required"}, status=400)

    districts = BusinessDistrict.objects.filter(state__name__iexact=state_name)
    result = []

    for district in districts:
        # Get all feeders in this business district specifically
        district_feeders = Feeder.objects.filter(business_district=district)
        
        # Get all transformers in this business district via feeders
        transformers = DistributionTransformer.objects.filter(
            feeder__in=district_feeders
        )
        
        # Get commercial summaries for transformers in this district
        summaries = MonthlyCommercialSummary.objects.filter(
            transformer__in=transformers,
            month=period_start  # Use exact month match
        )

        # Aggregate the commercial data
        totals = summaries.aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        )

        # Energy Delivered from technical models - use district_feeders specifically
        try:
            # Try monthly aggregates first
            delivered_mwh = FeederEnergyMonthly.objects.filter(
                feeder__in=district_feeders,  # Use specific feeders for this district
                period=period_start,
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
            
            if delivered_mwh == 0:
                # Fallback to daily aggregation
                period_end = period_start + relativedelta(months=1) - timedelta(days=1)
                delivered_mwh = FeederEnergyDaily.objects.filter(
                    feeder__in=district_feeders,  # Use specific feeders for this district
                    date__gte=period_start,
                    date__lte=period_end,
                ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
        except Exception as e:
            print(f"Error calculating delivered energy for {district.name}: {e}")
            delivered_mwh = Decimal(0)

        # Energy Billed from commercial models - use district_feeders specifically
        try:
            billed_mwh = MonthlyEnergyBilled.objects.filter(
                feeder__in=district_feeders,  # Use specific feeders for this district
                month=period_start,
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
        except Exception as e:
            print(f"Error calculating billed energy for {district.name}: {e}")
            billed_mwh = Decimal(0)

        # Extract values with safe defaults
        revenue_billed = totals["revenue_billed"] or Decimal(0)
        revenue_collected = totals["revenue_collected"] or Decimal(0)
        customers_billed = totals["customers_billed"] or 0
        customers_responded = totals["customers_responded"] or 0

        # Calculate efficiency metrics with proper error handling
        try:
            # Billing efficiency = (Energy Billed / Energy Delivered) * 100
            billing_eff = (billed_mwh / delivered_mwh * 100) if delivered_mwh > 0 else Decimal(0)
            
            # Collection efficiency = (Revenue Collected / Revenue Billed) * 100
            collection_eff = (revenue_collected / revenue_billed * 100) if revenue_billed > 0 else Decimal(0)
            
            # AT&C losses = 100% - (Billing Efficiency * Collection Efficiency / 100)
            atcc = Decimal(100) - (billing_eff * collection_eff / 100)
            
            # Energy collected = Energy Delivered * Collection Efficiency / 100
            # This represents the energy equivalent of what was actually collected
            energy_collected = billed_mwh * collection_eff / 100
            
        except (ZeroDivisionError, InvalidOperation) as e:
            print(f"Error calculating efficiency metrics for {district.name}: {e}")
            billing_eff = collection_eff = atcc = energy_collected = Decimal(0)

        # Customer metrics
        try:
            response_rate = (Decimal(customers_responded) / Decimal(customers_billed) * 100) if customers_billed > 0 else Decimal(0)
            revenue_per_customer = (revenue_billed / Decimal(customers_billed)) if customers_billed > 0 else Decimal(0)
            collections_per_customer = (revenue_collected / Decimal(customers_billed)) if customers_billed > 0 else Decimal(0)
        except (ZeroDivisionError, InvalidOperation) as e:
            print(f"Error calculating customer metrics for {district.name}: {e}")
            response_rate = revenue_per_customer = collections_per_customer = Decimal(0)

        # Round all decimal values appropriately
        def safe_round(value, places=2):
            try:
                return float(value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
            except (AttributeError, InvalidOperation):
                return float(value) if value else 0.0

        # Debug logging to help identify issues
        print(f"District: {district.name}")
        print(f"  Feeders count: {district_feeders.count()}")
        print(f"  Energy delivered: {delivered_mwh}")
        print(f"  Energy billed: {billed_mwh}")
        print(f"  Billing efficiency: {billing_eff}")
        print(f"  Revenue billed: {revenue_billed}")
        print(f"  Revenue collected: {revenue_collected}")
        print("---")

        result.append({
            "name": district.name,
            "energy_delivered": safe_round(delivered_mwh),
            "energy_billed": safe_round(billed_mwh),
            "energy_collected": safe_round(energy_collected),
            "revenue_billed": safe_round(revenue_billed),
            "revenue_collected": safe_round(revenue_collected),
            "billing_efficiency": safe_round(billing_eff),
            "collection_efficiency": safe_round(collection_eff),
            "atcc": safe_round(atcc),
            "customer_response_rate": safe_round(response_rate),
            "revenue_billed_per_customer": safe_round(revenue_per_customer),
            "collections_per_customer": safe_round(collections_per_customer),
            "customers_billed": customers_billed,
            "customers_responded": customers_responded,
        })

    return Response(result)
