from decimal import Decimal
from datetime import date
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from commercial.models import *
from commercial.serializers import *
from common.models import  Band
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered


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
            # Shared filter across models
            band_filter = {"feeder__band": band}

            if state:
                band_filter["feeder__business_district__state__name"] = state


            # ENERGY DELIVERED (Daily)
            energy_delivered = EnergyDelivered.objects.filter(
                date__gte=month_start, date__lt=month_end,
                **band_filter
            ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")

            # ENERGY BILLED (Monthly)
            energy_billed = MonthlyEnergyBilled.objects.filter(
                month=month_start,
                **band_filter
            ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")

            # COMMERCIAL SUMMARY (Monthly)
            commercial_data = MonthlyCommercialSummary.objects.filter(
                month=month_start,
                sales_rep__assigned_transformers__feeder__band=band,
                sales_rep__assigned_transformers__feeder__business_district__state__name=state if state else None
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected"),
                customers_billed=Sum("customers_billed"),
                customers_responded=Sum("customers_responded")
            )

            revenue_billed = commercial_data["revenue_billed"] or Decimal("0")
            revenue_collected = commercial_data["revenue_collected"] or Decimal("0")
            customers_billed = commercial_data["customers_billed"] or 0
            customers_responded = commercial_data["customers_responded"] or 0

            # Derived Metrics
            billing_eff = (energy_billed / energy_delivered * 100) if energy_delivered else 0
            collection_eff = (revenue_collected / revenue_billed * 100) if revenue_billed else 0
            atc_c = 100 - (billing_eff * collection_eff / 100) if billing_eff and collection_eff else 100
            response_rate = (customers_responded / customers_billed * 100) if customers_billed else 0
            energy_collected = energy_billed * (Decimal(collection_eff) / Decimal("100")) if energy_billed else 0

            results.append({
                "band": band.name,
                "energy_delivered": round(energy_delivered, 2),
                "energy_billed": round(energy_billed, 2),
                "energy_collected": round(energy_collected, 2),
                "atc_c": round(atc_c, 2),
                "billing_efficiency": round(billing_eff, 2),
                "collection_efficiency": round(collection_eff, 2),
                "customer_response_rate": round(response_rate, 2)
            })

        return Response(results, status=status.HTTP_200_OK)
