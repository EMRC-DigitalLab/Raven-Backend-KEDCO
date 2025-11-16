# commercial/views/service_bands/service_bands_views.py
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
from django.db.models import Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, FeederEnergyMonthly, FeederEnergyDaily
from common.models import Band, Feeder, DistributionTransformer


def safe_decimal(value):
    """Convert value to Decimal safely"""
    try:
        return Decimal(str(value or 0))
    except (TypeError, ValueError):
        return Decimal(0)


def safe_round(value, places=2):
    """Safely round decimal values"""
    try:
        if value is None:
            return 0.0
        return float(Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def get_energy_delivered_for_band(band, month_start, month_end, state=None):
    """Get energy delivered for a specific band using optimized queries"""
    try:
        # Build feeder filter
        feeder_filter = {"band": band}
        if state:
            feeder_filter["business_district__state__name"] = state
        
        band_feeders = list(Feeder.objects.filter(**feeder_filter))
        
        if not band_feeders:
            return Decimal(0)
        
        # Try monthly aggregates first (most efficient)
        delivered = FeederEnergyMonthly.objects.filter(
            feeder__in=band_feeders,
            period=month_start
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered:
            return safe_decimal(delivered)
        
        # Fallback to daily aggregation
        month_end_date = month_end - timedelta(days=1)
        delivered = FeederEnergyDaily.objects.filter(
            feeder__in=band_feeders,
            date__gte=month_start,
            date__lte=month_end_date
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered:
            return safe_decimal(delivered)
        
        # Final fallback to EnergyDelivered
        delivered = EnergyDelivered.objects.filter(
            feeder__in=band_feeders,
            date__gte=month_start,
            date__lt=month_end
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return safe_decimal(delivered)
        
    except Exception:
        return Decimal(0)


class ServiceBandMetricsView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        state = request.GET.get("state")

        # Target month
        month_start = date(year, month, 1)
        month_end = month_start + relativedelta(months=1)

        results = []

        for band in Band.objects.all().order_by("name"):
            # Build feeder filter for this band
            feeder_filter = {"band": band}
            if state:
                feeder_filter["business_district__state__name"] = state
            
            # Get feeders for this band (for efficient querying)
            band_feeders = list(Feeder.objects.filter(**feeder_filter))
            
            if not band_feeders:
                # No feeders for this band/state combination
                results.append({
                    "band": band.name,
                    "energy_delivered": 0.0,
                    "energy_billed": 0.0,
                    "energy_collected": 0.0,
                    "atcc": 0.0,
                    "billing_efficiency": 0.0,
                    "collection_efficiency": 0.0,
                    "customer_response_rate": 0.0
                })
                continue

            # Get transformers for this band
            transformer_filter = {"feeder__in": band_feeders}
            band_transformers = list(DistributionTransformer.objects.filter(**transformer_filter))

            # ENERGY DELIVERED (using optimized method)
            energy_delivered = get_energy_delivered_for_band(band, month_start, month_end, state)

            # ENERGY BILLED (Monthly)
            energy_billed = MonthlyEnergyBilled.objects.filter(
                feeder__in=band_feeders,
                month=month_start
            ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or 0
            energy_billed = safe_decimal(energy_billed)

            # COMMERCIAL SUMMARY (Monthly) - use direct transformer access
            commercial_data = MonthlyCommercialSummary.objects.filter(
                transformer__in=band_transformers,
                month=month_start
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected"),
                customers_billed=Sum("customers_billed"),
                customers_responded=Sum("customers_responded")
            )

            revenue_billed = safe_decimal(commercial_data["revenue_billed"])
            revenue_collected = safe_decimal(commercial_data["revenue_collected"])
            customers_billed = commercial_data["customers_billed"] or 0
            customers_responded = commercial_data["customers_responded"] or 0

            # Calculate metrics with proper error handling
            try:
                # Billing efficiency = (Energy Billed / Energy Delivered) * 100
                billing_eff = (energy_billed / energy_delivered * 100) if energy_delivered > 0 else Decimal(0)
                
                # Collection efficiency = (Revenue Collected / Revenue Billed) * 100
                collection_eff = (revenue_collected / revenue_billed * 100) if revenue_billed > 0 else Decimal(0)
                
                # Cap efficiencies at 100%
                billing_eff = min(billing_eff, Decimal(100))
                collection_eff = min(collection_eff, Decimal(100))
                
                # AT&C losses = 100% - (Billing Efficiency * Collection Efficiency / 100)
                atcc = Decimal(100) - (billing_eff * collection_eff / 100)
                atcc = max(atcc, Decimal(0))  # Ensure non-negative
                
                # Customer response rate = (Customers Responded / Customers Billed) * 100
                response_rate = (Decimal(customers_responded) / Decimal(customers_billed) * 100) if customers_billed > 0 else Decimal(0)
                
                # Energy collected = Energy Delivered * (Collection Efficiency / 100)
                energy_collected = energy_billed * (collection_eff / 100) if energy_delivered > 0 else Decimal(0)
                
            except Exception:
                billing_eff = collection_eff = atcc = response_rate = energy_collected = Decimal(0)

            results.append({
                "band": band.name,
                "energy_delivered": safe_round(energy_delivered),
                "energy_billed": safe_round(energy_billed),
                "energy_collected": safe_round(energy_collected),
                "atcc": safe_round(atcc),
                "billing_efficiency": safe_round(billing_eff),
                "collection_efficiency": safe_round(collection_eff),
                "customer_response_rate": safe_round(response_rate)
            })

        return Response(results, status=status.HTTP_200_OK)