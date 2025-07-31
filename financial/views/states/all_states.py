from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Sum
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from financial.models import Opex, SalaryPayment, NBETInvoice, MOInvoice, MYTOTariff
from common.models import State, Feeder, DistributionTransformer
from commercial.models import MonthlyCommercialSummary
from technical.models import EnergyDelivered, FeederEnergyMonthly, FeederEnergyDaily
from datetime import timedelta
from dateutil.relativedelta import relativedelta # type: ignore


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


def get_energy_delivered_for_state(state, year, month):
    """Get energy delivered for a state using optimized queries"""
    try:
        # Get all feeders for this state
        state_feeders = list(Feeder.objects.filter(business_district__state=state))
        
        if not state_feeders:
            return Decimal(0)
        
        # Create period start date
        period_start = date(year, month, 1)
        
        # Try monthly aggregates first (most efficient)
        delivered = FeederEnergyMonthly.objects.filter(
            feeder__in=state_feeders,
            period=period_start
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered:
            return safe_decimal(delivered)
        
        # Fallback to daily aggregation
        period_end = period_start + relativedelta(months=1) - timedelta(days=1)
        delivered = FeederEnergyDaily.objects.filter(
            feeder__in=state_feeders,
            date__gte=period_start,
            date__lte=period_end
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered:
            return safe_decimal(delivered)
        
        # Final fallback to EnergyDelivered
        delivered = EnergyDelivered.objects.filter(
            feeder__business_district__state=state,
            date__year=year,
            date__month=month
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return safe_decimal(delivered)
        
    except Exception:
        return Decimal(0)


class FinancialAllStatesView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        target_month = date(year, month, 1)

        results = []

        for state in State.objects.all():
            # Pre-fetch transformers for this state (more efficient than going through sales reps)
            state_transformers = list(DistributionTransformer.objects.filter(
                feeder__business_district__state=state
            ))

            # --- Total Cost Calculation ---
            # OPEX (both credit and debit)
            opex_data = Opex.objects.filter(
                date__year=year,
                date__month=month,
                district__state=state
            ).aggregate(
                credit_total=Sum("credit"),
                debit_total=Sum("debit")
            )
            
            opex_total = safe_decimal(opex_data["credit_total"]) + safe_decimal(opex_data["debit_total"])

            # Salaries for this state
            salary_total = SalaryPayment.objects.filter(
                month=target_month,
                district__state=state
            ).aggregate(total=Sum("amount"))["total"]
            salary_total = safe_decimal(salary_total)

            # Get energy share for proportional allocation of NBET/MO invoices
            state_energy_delivered = get_energy_delivered_for_state(state, year, month)
            
            # Total energy delivered across all states for proportional calculation
            total_energy_delivered = EnergyDelivered.objects.filter(
                date__year=year,
                date__month=month
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
            total_energy_delivered = safe_decimal(total_energy_delivered)
            
            # Calculate energy share for this state
            energy_share = (state_energy_delivered / total_energy_delivered) if total_energy_delivered > 0 else Decimal(0)

            # NBET Invoice - allocated proportionally based on energy share
            nbet_total_global = NBETInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"]
            nbet_total_global = safe_decimal(nbet_total_global)
            nbet_allocated = nbet_total_global * energy_share
            
            # MO Invoice - allocated proportionally based on energy share
            mo_total_global = MOInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"]
            mo_total_global = safe_decimal(mo_total_global)
            mo_allocated = mo_total_global * energy_share

            # Total cost for this state
            total_cost = opex_total + salary_total + nbet_allocated + mo_allocated

            # --- Revenue and Collections (Direct transformer access) ---
            if state_transformers:
                commercial_data = MonthlyCommercialSummary.objects.filter(
                    month=target_month,
                    transformer__in=state_transformers
                ).aggregate(
                    revenue_billed=Sum("revenue_billed"),
                    collections=Sum("revenue_collected")
                )
                
                revenue_billed = safe_decimal(commercial_data["revenue_billed"])
                collections = safe_decimal(commercial_data["collections"])
            else:
                revenue_billed = Decimal(0)
                collections = Decimal(0)

            # --- Tariff Calculations ---
            # Get MYTO tariff (latest applicable rate)
            myto_tariff_obj = MYTOTariff.objects.filter(
                effective_date__lte=target_month
            ).order_by("-effective_date").first()
            
            myto_tariff = safe_decimal(myto_tariff_obj.rate_per_kwh) if myto_tariff_obj else Decimal("60.0")

            # Calculate actual tariff collected (Collections / Energy in kWh)
            if state_energy_delivered > 0:
                # Convert MWh to kWh for tariff calculation
                energy_delivered_kwh = state_energy_delivered * Decimal(1000)
                actual_tariff = collections / energy_delivered_kwh
            else:
                actual_tariff = Decimal(0)

            # Tariff Loss = MYTO Tariff - Actual Tariff Collected
            tariff_loss = myto_tariff - actual_tariff

            # --- Compile State Metrics ---
            results.append({
                "state": state.name,
                "total_cost": safe_round(total_cost),
                "revenue_billed": safe_round(revenue_billed),
                "collections": safe_round(collections),
                "myto_allowed_tariff": safe_round(myto_tariff, 4),  # Keep 4 decimal places for tariff
                "actual_tariff_collected": safe_round(actual_tariff, 4),
                "tariff_loss": safe_round(tariff_loss, 4)
            })

        return Response(results, status=status.HTTP_200_OK)