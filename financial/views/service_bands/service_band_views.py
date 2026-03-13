from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from commercial.models import MonthlyCommercialSummary, SalesRepresentative
from common.models import Band, Feeder
from financial.models import *
from financial.models import Opex
from financial.serializers import *
from technical.models import EnergyDelivered


class FinancialServiceBandMetricsView(APIView):
    def get(self, request):
        try:
            year = int(request.GET.get("year"))
            month = int(request.GET.get("month"))
        except (TypeError, ValueError):
            return Response({"error": "Invalid or missing 'year' or 'month' parameters."},
                            status=status.HTTP_400_BAD_REQUEST)

        state_name = request.GET.get("state")
        selected_date = date(year, month, 1)
        selected_end = selected_date + relativedelta(months=1)

        bands = Band.objects.all()
        results = []

        for band in bands:
            # Get feeders for the band (filtered by state if provided)
            feeders = Feeder.objects.filter(band=band)
            if state_name:
                feeders = feeders.filter(business_district__state__name__iexact=state_name)

            if not feeders.exists():
                continue

            # Get distinct business districts tied to the feeders
            district_ids = feeders.values_list("business_district_id", flat=True).distinct()

            # --- Total Cost Calculation (All cost components) ---
            # OPEX (both credit and debit) from relevant districts
            opex_data = Opex.objects.filter(
                district__in=district_ids,
                date__year=year,
                date__month=month
            ).aggregate(
                credit_total=Sum("credit"),
                debit_total=Sum("debit")
            )
            
            opex_total = Decimal(opex_data["credit_total"] or 0) + Decimal(opex_data["debit_total"] or 0)

            # Salaries for the districts
            salary_total = SalaryPayment.objects.filter(
                district__in=district_ids,
                month=selected_date
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # --- Energy-based NBET/MO Allocation ---
            # Get band's energy share for proportional allocation
            band_energy = EnergyDelivered.objects.filter(
                feeder__in=feeders,
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # Total energy delivered across all feeders for the month
            total_energy = EnergyDelivered.objects.filter(
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # Calculate energy share (0-1)
            energy_share = (band_energy / total_energy) if total_energy > 0 else Decimal("0")

            # NBET Invoice (allocated proportionally)
            nbet_total = NBETInvoice.objects.filter(
                month=selected_date
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            nbet_allocated = nbet_total * energy_share

            # MO Invoice (allocated proportionally)
            mo_total = MOInvoice.objects.filter(
                month=selected_date
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            mo_allocated = mo_total * energy_share

            # Total cost = OPEX + Salaries + Allocated NBET + Allocated MO
            total_cost = opex_total + salary_total + nbet_allocated + mo_allocated

            # --- Revenue and Collections ---
            # Get all sales reps tied to feeders via transformers
            sales_reps = SalesRepresentative.objects.filter(
                assigned_transformers__feeder__in=feeders
            ).distinct()

            # Aggregate commercial revenue & collections
            commercial = MonthlyCommercialSummary.objects.filter(
                sales_rep__in=sales_reps,
                month=selected_date
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected")
            )

            revenue_billed = commercial["revenue_billed"] or Decimal("0")
            revenue_collected = commercial["revenue_collected"] or Decimal("0")

            # --- Real Tariff Calculations ---
            # Get MYTO tariff (latest applicable)
            myto_tariff_obj = MYTOTariff.objects.filter(
                effective_date__lte=selected_date
            ).order_by("-effective_date").first()
            
            myto_tariff = myto_tariff_obj.rate_per_kwh if myto_tariff_obj else Decimal("60")

            # Calculate actual tariff collected (Collections / Energy in kWh)
            if band_energy > 0:
                band_energy_kwh = band_energy * 1000  # Convert MWh to kWh
                actual_tariff_collected = revenue_collected / band_energy_kwh
            else:
                actual_tariff_collected = Decimal("0")

            # Tariff Loss = MYTO Tariff - Actual Tariff Collected
            tariff_loss = myto_tariff - actual_tariff_collected

            results.append({
                "band": band.name,
                "total_cost": float(round(total_cost, 2)),
                "revenue_billed": float(round(revenue_billed, 2)),
                "collections": float(round(revenue_collected, 2)),
                "myto_allowed_tariff": f"{round(myto_tariff, 2)}",
                "actual_tariff_collected": f"{round(actual_tariff_collected, 2)}",
                "tariff_loss": f"{round(tariff_loss, 2)}"
            })

        return Response(results, status=status.HTTP_200_OK)