from datetime import datetime
from decimal import Decimal

from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Avg, Count, Sum
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from commercial.models import *
from financial.models import *
from technical.models import *

from .models import *
from .serializers import *


class StateViewSet(viewsets.ModelViewSet):
    queryset = State.objects.all()
    serializer_class = StateSerializer

class BusinessDistrictViewSet(viewsets.ModelViewSet):
    queryset = BusinessDistrict.objects.all()
    serializer_class = BusinessDistrictSerializer

class InjectionSubstationViewSet(viewsets.ModelViewSet):
    queryset = InjectionSubstation.objects.all()
    serializer_class = InjectionSubstationSerializer

class FeederViewSet(viewsets.ModelViewSet):
    queryset = Feeder.objects.all()
    serializer_class = FeederSerializer

class DistributionTransformerViewSet(viewsets.ModelViewSet):
    queryset = DistributionTransformer.objects.all()
    serializer_class = DistributionTransformerSerializer

class BandViewSet(viewsets.ModelViewSet):
    queryset = Band.objects.all()
    serializer_class = BandSerializer





class OverviewAPIView(APIView):
    def get(self, request):
        try:
            month = int(request.GET.get("month"))
            year = int(request.GET.get("year"))
            target = datetime(year, month, 1)
        except (TypeError, ValueError):
            target = None

        from_date_str = request.GET.get("from")
        to_date_str = request.GET.get("to")
        from_date = datetime.strptime(from_date_str, "%Y-%m-%d") if from_date_str else None
        to_date = datetime.strptime(to_date_str, "%Y-%m-%d") if to_date_str else None

        if from_date and to_date:
            current = from_date.replace(day=1)
            months = []
            while current <= to_date:
                months.append(current)
                current += relativedelta(months=1)
        else:
            target = target or datetime.today().replace(day=1)
            months = [(target - relativedelta(months=i)).replace(day=1) for i in range(5)][::-1]

        overview_data = []

        for m in months:
            # Commercial data aggregation
            comm = MonthlyCommercialSummary.objects.filter(month=m).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected"),
                customers_billed=Sum("customers_billed"),
                customers_responded=Sum("customers_responded"),
            )
            
            # Energy data aggregation
            energy_delivered = EnergyDelivered.objects.filter(
                date__year=m.year, 
                date__month=m.month
            ).aggregate(energy=Sum("energy_mwh"))["energy"] or Decimal("0")
            
            energy_billed = MonthlyEnergyBilled.objects.filter(month=m).aggregate(
                energy=Sum("energy_mwh")
            )["energy"] or Decimal("0")

            # Extract commercial values first
            revenue_billed = comm['revenue_billed'] or Decimal("0")
            revenue_collected = comm['revenue_collected'] or Decimal("0")
            customers_billed = comm['customers_billed'] or 0
            customers_responded = comm['customers_responded'] or 0
            
            # Financial data - include all cost components
            opex_costs = Opex.objects.filter(
                date__year=m.year, 
                date__month=m.month
            ).aggregate(
                total_opex=Sum("credit") + Sum("debit")
            )["total_opex"] or Decimal("0")
            
            # Add other cost components - ensure proper date filtering
            salary_costs = SalaryPayment.objects.filter(
                month__year=m.year,
                month__month=m.month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            nbet_costs = NBETInvoice.objects.filter(
                month__year=m.year,
                month__month=m.month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            mo_costs = MOInvoice.objects.filter(
                month__year=m.year,
                month__month=m.month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            total_cost = opex_costs + salary_costs + nbet_costs + mo_costs
            
            # Check if we have any actual operational data for this month
            has_operational_data = (
                energy_delivered > 0 or 
                revenue_billed > 0 or 
                customers_billed > 0
            )
            
            # If no operational data exists, you might want to set total_cost to 0
            # or handle it differently based on business logic
            if not has_operational_data and total_cost == 0:
                # This month truly has no data
                total_cost = Decimal("0")

            # Technical metrics - improved calculation
            # Calculate average hours of supply more accurately
            hourly_loads = HourlyLoad.objects.filter(
                date__year=m.year, 
                date__month=m.month,
                load_mw__gt=0  # Only count hours with actual load
            ).values('feeder', 'date').annotate(
                daily_hours=Count('hour')
            )
            
            if hourly_loads.exists():
                avg_hours_supply = hourly_loads.aggregate(
                    avg=Avg('daily_hours')
                )["avg"] or 0
            else:
                avg_hours_supply = 0

            # Calculate interruption metrics properly
            interruptions = FeederInterruption.objects.filter(
                occurred_at__year=m.year, 
                occurred_at__month=m.month,
                restored_at__isnull=False
            )
            
            if interruptions.exists():
                total_duration = sum(
                    (interrupt.restored_at - interrupt.occurred_at).total_seconds() / 3600 
                    for interrupt in interruptions
                )
                avg_interruption_duration = total_duration / interruptions.count()
                avg_turnaround_time = avg_interruption_duration  # Same as interruption duration
            else:
                avg_interruption_duration = 0
                avg_turnaround_time = 0

            # Calculate efficiency metrics without tariff
            # Billing efficiency = Energy Billed / Energy Delivered
            billing_eff = (energy_billed / energy_delivered) if energy_delivered > 0 else Decimal("0")
            
            # Collection efficiency = Revenue Collected / Revenue Billed
            collection_eff = (revenue_collected / revenue_billed) if revenue_billed > 0 else Decimal("0")
            
            # AT&C Losses = 100% - (Billing Efficiency × Collection Efficiency)
            atc_losses = Decimal("1") - (billing_eff * collection_eff)
            
            # Energy collected = Energy Delivered × Collection Efficiency
            # This represents the energy equivalent of what was actually collected
            energy_collected = energy_billed * collection_eff
            
            # Customer response rate
            customer_response_rate = (customers_responded / customers_billed * 100) if customers_billed > 0 else 0

            overview_data.append({
                "month": m.strftime("%b"),
                "billing_efficiency": float(round(billing_eff * 100, 2)),
                "collection_efficiency": float(round(collection_eff * 100, 2)),
                "atcc": float(round(atc_losses * 100, 2)),  # AT&C losses as percentage
                "revenue_billed": float(revenue_billed),
                "revenue_collected": float(revenue_collected),
                "energy_billed": float(energy_billed),
                "energy_delivered": float(energy_delivered),
                "energy_collected": float(round(energy_collected, 2)),
                "customer_response_rate": round(customer_response_rate, 2),
                "total_cost": float(total_cost),
                "avg_hours_supply": round(avg_hours_supply, 2),
                "avg_interruption_duration": round(avg_interruption_duration, 2),
                "avg_turnaround_time": round(avg_turnaround_time, 2),
            })

        # Calculate deltas for current month vs previous month
        current = overview_data[-1] if overview_data else {}
        previous = overview_data[-2] if len(overview_data) > 1 else {}

        def calculate_delta(metric):
            """Calculate percentage change between current and previous values"""
            if (metric in current and metric in previous and 
                previous[metric] is not None and previous[metric] != 0):
                return round(((current[metric] - previous[metric]) / previous[metric]) * 100, 2)
            return None

        # Add delta calculations for all metrics
        metrics_to_track = [
            "atcc", "billing_efficiency", "collection_efficiency",
            "revenue_billed", "revenue_collected", "energy_billed", 
            "energy_delivered", "total_cost", "energy_collected",
            "customer_response_rate", "avg_hours_supply",
            "avg_interruption_duration", "avg_turnaround_time"
        ]

        for metric in metrics_to_track:
            current[f"delta_{metric}"] = calculate_delta(metric)

        return Response({
            "current": current,
            "history": overview_data[:-1]  # All months except current
        })
