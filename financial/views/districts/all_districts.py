from datetime import date
from decimal import Decimal
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from financial.models import *
from financial.serializers import *
from common.models import State, BusinessDistrict
from commercial.models import MonthlyCommercialSummary, SalesRepresentative
from financial.models import Opex
from django.db.models import Sum
from rest_framework.response import Response
from datetime import date
from dateutil.relativedelta import relativedelta # type: ignore
from commercial.models import MonthlyCommercialSummary
from commercial.models import SalesRepresentative
from rest_framework import status
from technical.models import EnergyDelivered


class FinancialAllBusinessDistrictsView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        state_name = request.GET.get("state")

        if not state_name:
            return Response({"error": "state is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            state = State.objects.get(name__iexact=state_name)
        except State.DoesNotExist:
            return Response({"error": "State not found"}, status=status.HTTP_404_NOT_FOUND)

        target_month = date(year, month, 1)
        target_month_end = target_month + relativedelta(months=1)
        results = []

        districts = BusinessDistrict.objects.filter(state=state)

        for district in districts:
            # --- Total Cost Calculation (All cost components) ---
            # OPEX (both credit and debit)
            opex_data = Opex.objects.filter(
                district=district,
                date__year=year,
                date__month=month
            ).aggregate(
                credit_total=Sum("credit"),
                debit_total=Sum("debit")
            )
            
            opex_total = Decimal(opex_data["credit_total"] or 0) + Decimal(opex_data["debit_total"] or 0)

            # Salaries for the district
            salary_total = SalaryPayment.objects.filter(
                district=district,
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # --- Energy-based NBET/MO Allocation ---
            # Get district's energy share for proportional allocation
            district_energy = EnergyDelivered.objects.filter(
                feeder__business_district=district,
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # Total energy delivered across all feeders for the month
            total_energy = EnergyDelivered.objects.filter(
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # Calculate energy share (0-1)
            energy_share = (district_energy / total_energy) if total_energy > 0 else Decimal("0")

            # NBET Invoice (allocated proportionally)
            nbet_total = NBETInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            nbet_allocated = nbet_total * energy_share

            # MO Invoice (allocated proportionally)
            mo_total = MOInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            mo_allocated = mo_total * energy_share

            # Total cost = OPEX + Salaries + Allocated NBET + Allocated MO
            total_cost = opex_total + salary_total + nbet_allocated + mo_allocated

            # --- Revenue Billed and Collections ---
            sales_reps = SalesRepresentative.objects.filter(
                assigned_transformers__feeder__business_district=district
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

            # --- Real Tariff Loss Calculation ---
            # Get MYTO tariff (latest applicable)
            myto_tariff_obj = MYTOTariff.objects.filter(
                effective_date__lte=target_month
            ).order_by("-effective_date").first()
            
            myto_tariff = myto_tariff_obj.rate_per_kwh if myto_tariff_obj else Decimal("60")

            # Calculate actual tariff collected (Collections / Energy in kWh)
            if district_energy > 0:
                district_energy_kwh = district_energy * 1000  # Convert MWh to kWh
                actual_tariff_collected = collections / district_energy_kwh
                billing_tariff = revenue_billed / district_energy_kwh
            else:
                actual_tariff_collected = Decimal("0")
                billing_tariff = Decimal("0")

            # Tariff Loss = MYTO Tariff - Actual Tariff Collected
            tariff_loss = myto_tariff - actual_tariff_collected

            results.append({
                "district": district.name,
                "total_cost": float(round(total_cost, 2)),
                "revenue_billed": float(round(revenue_billed, 2)),
                "collections": float(round(collections, 2)),
                "tariff_loss": float(round(tariff_loss, 2))
            })

        return Response(results, status=status.HTTP_200_OK)