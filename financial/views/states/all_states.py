from datetime import date
from decimal import Decimal
from django.db.models import Sum
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from financial.models import *
from financial.serializers import *
from common.models import State
from commercial.models import (
    MonthlyCommercialSummary,
    SalesRepresentative,
)
from financial.models import Opex
from django.db.models import Sum
from rest_framework.response import Response
from datetime import date
from commercial.models import MonthlyCommercialSummary
from commercial.models import SalesRepresentative
from rest_framework import status
from technical.models import EnergyDelivered



class FinancialAllStatesView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        target_month = date(year, month, 1)

        results = []

        for state in State.objects.all():
            # --- Total Cost (Complete cost calculation) ---
            # OPEX (both credit and debit)
            opex_data = Opex.objects.filter(
                date__year=year,
                date__month=month,
                district__state=state
            ).aggregate(
                credit_total=Sum("credit"),
                debit_total=Sum("debit")
            )
            
            opex_total = Decimal(opex_data["credit_total"] or 0) + Decimal(opex_data["debit_total"] or 0)

            # Salaries
            salary_total = SalaryPayment.objects.filter(
                month=target_month,
                district__state=state
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # NBET Invoice
            nbet_total = NBETInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            # MO Invoice
            mo_total = MOInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # Total cost
            total_cost = opex_total + salary_total + nbet_total + mo_total

            # --- Revenue Billed and Collections (from MonthlyCommercialSummary) ---
            sales_reps = SalesRepresentative.objects.filter(
                assigned_transformers__feeder__business_district__state=state
            ).distinct()

            commercial_data = MonthlyCommercialSummary.objects.filter(
                month=target_month,
                sales_rep__in=sales_reps
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                collections=Sum("revenue_collected")
            )

            revenue_billed = commercial_data["revenue_billed"] or Decimal("0")
            collections = commercial_data["collections"] or Decimal("0")

            # --- Real Tariff Calculations ---
            # Get energy delivered for tariff calculations
            energy_delivered = EnergyDelivered.objects.filter(
                feeder__business_district__state=state,
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # MYTO Tariff (get the latest applicable tariff)
            myto_tariff_obj = MYTOTariff.objects.filter(
                effective_date__lte=target_month
            ).order_by("-effective_date").first()
            
            myto_tariff = myto_tariff_obj.rate_per_kwh if myto_tariff_obj else Decimal("60")

            # Calculate actual tariff collected (Collections / Energy in kWh)
            if energy_delivered > 0:
                energy_delivered_kwh = energy_delivered * 1000  # Convert MWh to kWh
                actual_tariff = collections / energy_delivered_kwh
            else:
                actual_tariff = Decimal("0")

            # Tariff Loss = MYTO Tariff - Actual Tariff Collected
            tariff_loss = myto_tariff - actual_tariff

            # --- Compile State Metrics ---
            results.append({
                "state": state.name,
                "total_cost": round(total_cost, 2),
                "revenue_billed": round(revenue_billed, 2),
                "collections": round(collections, 2),
                "myto_allowed_tariff": f"{myto_tariff}",
                "actual_tariff_collected": f"{actual_tariff}",
                "tariff_loss": f"{tariff_loss}"
            })

        return Response(results, status=status.HTTP_200_OK)
