from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from commercial.models import MonthlyCommercialSummary
from common.models import BusinessDistrict, DistributionTransformer, Feeder, State
from financial.models import MOInvoice, MYTOTariff, NBETInvoice, Opex, SalaryPayment
from technical.models import EnergyDelivered, FeederEnergyDaily, FeederEnergyMonthly


def safe_decimal(value):
    """Convert value to Decimal safely"""
    try:
        return Decimal(str(value or 0))
    except (TypeError, ValueError):
        return Decimal(0)


def safe_round(value, places=2):
    """Safely round decimal values to float"""
    try:
        if value is None:
            return 0.0
        return float(Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def get_energy_delivered_for_district(district, year, month):
    """Get energy delivered for a district using optimized queries"""
    try:
        # Get all feeders for this district
        district_feeders = list(Feeder.objects.filter(business_district=district))
        
        if not district_feeders:
            return Decimal(0)
        
        # Create period start date
        period_start = date(year, month, 1)
        
        # Try monthly aggregates first (most efficient)
        delivered = FeederEnergyMonthly.objects.filter(
            feeder__in=district_feeders,
            period=period_start
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered:
            return safe_decimal(delivered)
        
        # Fallback to daily aggregation
        period_end = period_start + relativedelta(months=1) - timedelta(days=1)
        delivered = FeederEnergyDaily.objects.filter(
            feeder__in=district_feeders,
            date__gte=period_start,
            date__lte=period_end
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered:
            return safe_decimal(delivered)
        
        # Final fallback to EnergyDelivered
        delivered = EnergyDelivered.objects.filter(
            feeder__business_district=district,
            date__year=year,
            date__month=month
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return safe_decimal(delivered)
        
    except Exception:
        return Decimal(0)


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
        results = []

        # Get all districts for the state
        districts = BusinessDistrict.objects.filter(state=state).prefetch_related('feeders')

        # Pre-calculate total energy for proportional allocation (single query)
        total_energy_delivered = EnergyDelivered.objects.filter(
            date__year=year,
            date__month=month
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        total_energy_delivered = safe_decimal(total_energy_delivered)

        # Get global NBET and MO totals (single queries)
        nbet_total_global = NBETInvoice.objects.filter(
            month=target_month
        ).aggregate(total=Sum("amount"))["total"]
        nbet_total_global = safe_decimal(nbet_total_global)

        mo_total_global = MOInvoice.objects.filter(
            month=target_month
        ).aggregate(total=Sum("amount"))["total"]
        mo_total_global = safe_decimal(mo_total_global)

        # Get MYTO tariff once (single query)
        myto_tariff_obj = MYTOTariff.objects.filter(
            effective_date__lte=target_month
        ).order_by("-effective_date").first()
        myto_tariff = safe_decimal(myto_tariff_obj.rate_per_kwh) if myto_tariff_obj else Decimal("60.0")

        for district in districts:
            # Pre-fetch transformers for this district (more efficient than going through sales reps)
            district_transformers = list(DistributionTransformer.objects.filter(
                feeder__business_district=district
            ))

            # --- Total Cost Calculation ---
            # OPEX (both credit and debit)
            opex_data = Opex.objects.filter(
                district=district,
                date__year=year,
                date__month=month
            ).aggregate(
                credit_total=Sum("credit"),
                debit_total=Sum("debit")
            )
            
            opex_total = safe_decimal(opex_data["credit_total"]) + safe_decimal(opex_data["debit_total"])

            # Salaries for the district
            salary_total = SalaryPayment.objects.filter(
                district=district,
                month=target_month
            ).aggregate(total=Sum("amount"))["total"]
            salary_total = safe_decimal(salary_total)

            # --- Energy-based NBET/MO Allocation ---
            # Get district's energy using optimized method
            district_energy = get_energy_delivered_for_district(district, year, month)

            # Calculate energy share (0-1) for proportional allocation
            energy_share = (district_energy / total_energy_delivered) if total_energy_delivered > 0 else Decimal(0)

            # Allocate NBET and MO invoices proportionally
            nbet_allocated = nbet_total_global * energy_share
            mo_allocated = mo_total_global * energy_share

            # Total cost = OPEX + Salaries + Allocated NBET + Allocated MO
            total_cost = opex_total + salary_total + nbet_allocated + mo_allocated

            # --- Revenue and Collections (Direct transformer access) ---
            if district_transformers:
                commercial_data = MonthlyCommercialSummary.objects.filter(
                    month=target_month,
                    transformer__in=district_transformers
                ).aggregate(
                    revenue_billed=Sum("revenue_billed"),
                    collections=Sum("revenue_collected")
                )
                
                revenue_billed = safe_decimal(commercial_data["revenue_billed"])
                collections = safe_decimal(commercial_data["collections"])
            else:
                revenue_billed = Decimal(0)
                collections = Decimal(0)

            # --- Tariff Loss Calculation ---
            # Calculate actual tariff collected (Collections / Energy in kWh)
            if district_energy > 0:
                # Convert MWh to kWh for tariff calculation
                district_energy_kwh = district_energy * Decimal(1000)
                actual_tariff_collected = collections / district_energy_kwh
                billing_tariff = revenue_billed / district_energy_kwh
            else:
                actual_tariff_collected = Decimal(0)
                billing_tariff = Decimal(0)

            # Tariff Loss = MYTO Tariff - Actual Tariff Collected
            tariff_loss = myto_tariff - actual_tariff_collected

            # --- Compile District Results ---
            results.append({
                "district": district.name,
                "total_cost": safe_round(total_cost),
                "revenue_billed": safe_round(revenue_billed),
                "collections": safe_round(collections),
                "tariff_loss": safe_round(tariff_loss, 4)  # More precision for tariff
            })

        return Response(results, status=status.HTTP_200_OK)