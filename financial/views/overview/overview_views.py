from datetime import date
from decimal import Decimal
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum, Q
from rest_framework.response import Response
from rest_framework.decorators import api_view
from financial.models import *
from financial.serializers import *
from common.models import Feeder, DistributionTransformer
from commercial.models import MonthlyCommercialSummary, DailyCollection
from financial.models import Opex
from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.response import Response
from datetime import date
from dateutil.relativedelta import relativedelta # type: ignore
from commercial.models import MonthlyCommercialSummary
from django.db.models import Q
from technical.models import EnergyDelivered



@api_view(["GET"])
def financial_overview_view(request):
    # ─── 1) PARAMS & BASE FILTERS ──────────────────────────────────────────────
    year = int(request.query_params.get("year", date.today().year))
    month = request.query_params.get("month")
    state_name = request.query_params.get("state")
    district_name = request.query_params.get("business_district")
    feeder_slug = request.query_params.get("feeder")
    transformer_slug = request.query_params.get("transformer")

    # Ensure month is provided for proper calculations
    if not month:
        month = date.today().month
    else:
        month = int(month)

    # Set up date ranges
    selected_date = date(year, month, 1)
    selected_end = selected_date + relativedelta(months=1)
    prev_date = selected_date - relativedelta(months=1)
    prev_end = selected_date

    # Determine filtering level and build base filters
    commercial_base = Q()
    opex_base = Q()
    salary_base = Q()
    energy_base = Q()

    if transformer_slug:
        # Transformer-level filtering (highest precedence)
        try:
            transformer = DistributionTransformer.objects.get(slug=transformer_slug)
            commercial_base = Q(sales_rep__assigned_transformers=transformer)
            opex_base = Q(district=transformer.feeder.business_district)
            salary_base = Q(district=transformer.feeder.business_district)
            energy_base = Q(feeder=transformer.feeder)
        except DistributionTransformer.DoesNotExist:
            return Response({"error": "Transformer not found"}, status=400)
    elif feeder_slug:
        # Feeder-level filtering
        try:
            feeder = Feeder.objects.get(slug=feeder_slug)
            commercial_base = Q(sales_rep__assigned_transformers__feeder=feeder)
            opex_base = Q(district=feeder.business_district)
            salary_base = Q(district=feeder.business_district)
            energy_base = Q(feeder=feeder)
        except Feeder.DoesNotExist:
            return Response({"error": "Feeder not found"}, status=400)
    elif district_name:
        # Business district filtering
        commercial_base = Q(sales_rep__assigned_transformers__feeder__business_district__name__iexact=district_name)
        opex_base = Q(district__name__iexact=district_name)
        salary_base = Q(district__name__iexact=district_name)
        energy_base = Q(feeder__business_district__name__iexact=district_name)
    elif state_name:
        # State filtering
        commercial_base = Q(sales_rep__assigned_transformers__feeder__business_district__state__name__iexact=state_name)
        opex_base = Q(district__state__name__iexact=state_name)
        salary_base = Q(district__state__name__iexact=state_name)
        energy_base = Q(feeder__business_district__state__name__iexact=state_name)

    def calculate_delta(current, previous):
        """Calculate percentage change between current and previous values"""
        if previous and previous != 0:
            return round(((current - previous) / previous) * 100, 2)
        return None

    def get_energy_share(start_date, end_date, energy_filter):
        """Calculate energy share for proportional allocation"""
        # Energy delivered for filtered scope
        filtered_energy = EnergyDelivered.objects.filter(
            energy_filter & Q(date__gte=start_date, date__lt=end_date)
        ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")
        
        # Total energy delivered across all feeders
        total_energy = EnergyDelivered.objects.filter(
            date__gte=start_date, date__lt=end_date
        ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")
        
        # Calculate share (0-1)
        if total_energy > 0:
            return filtered_energy / total_energy
        return Decimal("0")

    def get_costs_for_period(start_date, end_date):
        """Get all cost components for a given period"""
        # For transformer and feeder level, we need to calculate transformer's share of feeder energy
        if transformer_slug:
            # Transformer share calculation
            # Since we don't have direct transformer energy data, we'll use a simplified approach:
            # 1. Get all transformers on the feeder
            # 2. Assume equal distribution (can be enhanced with actual transformer load data)
            transformer = DistributionTransformer.objects.get(slug=transformer_slug)
            feeder_transformers_count = DistributionTransformer.objects.filter(
                feeder=transformer.feeder
            ).count()
            
            # Transformer gets equal share of feeder's allocations
            transformer_share = Decimal("1") / Decimal(feeder_transformers_count) if feeder_transformers_count > 0 else Decimal("1")
            
            # Get feeder's energy share first
            feeder_energy_share = get_energy_share(start_date, end_date, Q(feeder=transformer.feeder))
            # Transformer's final share is its portion of the feeder's share
            energy_share = feeder_energy_share * transformer_share
        else:
            # Regular energy share calculation for feeder/district/state
            energy_share = get_energy_share(start_date, end_date, energy_base)

        # OPEX (both debit and credit) - transformer uses feeder's district OPEX
        opex_filter = opex_base & Q(date__gte=start_date, date__lt=end_date)
        opex_data = Opex.objects.filter(opex_filter).aggregate(
            debit=Sum("debit"), 
            credit=Sum("credit")
        )
        
        if transformer_slug:
            # Transformer gets proportional share of district OPEX based on transformer count
            transformer = DistributionTransformer.objects.get(slug=transformer_slug)
            district_transformers_count = DistributionTransformer.objects.filter(
                feeder__business_district=transformer.feeder.business_district
            ).count()
            transformer_opex_share = Decimal("1") / Decimal(district_transformers_count) if district_transformers_count > 0 else Decimal("1")
            
            opex_total = float(((opex_data["debit"] or 0) + (opex_data["credit"] or 0)) * transformer_opex_share)
        else:
            opex_total = float((opex_data["debit"] or 0) + (opex_data["credit"] or 0))

        # Salaries - transformer uses proportional share of district salaries
        salary_filter = salary_base & Q(month__gte=start_date, month__lt=end_date)
        district_salary_total = SalaryPayment.objects.filter(salary_filter).aggregate(
            total=Sum("amount"))["total"] or Decimal("0")
        
        if transformer_slug:
            salary_total = float(district_salary_total * transformer_opex_share)
        else:
            salary_total = float(district_salary_total)

        # NBET Invoice (allocated proportionally based on energy share)
        nbet_total_invoice = NBETInvoice.objects.filter(
            month__gte=start_date, month__lt=end_date
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        nbet_allocated = float(nbet_total_invoice * energy_share)

        # MO Invoice (allocated proportionally based on energy share)
        mo_total_invoice = MOInvoice.objects.filter(
            month__gte=start_date, month__lt=end_date
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        mo_allocated = float(mo_total_invoice * energy_share)

        total_cost = opex_total + salary_total + nbet_allocated + mo_allocated

        return {
            "opex": opex_total,
            "salaries": salary_total,
            "nbet": nbet_allocated,
            "mo": mo_allocated,
            "total": total_cost,
            "energy_share": float(energy_share),
            "transformer_opex_share": float(transformer_opex_share) if transformer_slug else None
        }

    def get_revenue_for_period(start_date, end_date):
        """Get revenue data for a given period"""
        commercial_filter = commercial_base & Q(month__gte=start_date, month__lt=end_date)
        revenue_data = MonthlyCommercialSummary.objects.filter(commercial_filter).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected"),
        )
        return {
            "billed": float(revenue_data["revenue_billed"] or 0),
            "collected": float(revenue_data["revenue_collected"] or 0)
        }

    # ─── 2) OPERATING EXPENDITURE (Selected Month + Delta) ─────────────────────
    current_costs = get_costs_for_period(selected_date, selected_end)
    prev_costs = get_costs_for_period(prev_date, selected_date)
    
    operating_expenditure = {
        "value": current_costs["total"],
        "delta": calculate_delta(current_costs["total"], prev_costs["total"])
    }

    # ─── 3) PROFIT MARGIN (Selected Month + Delta) ─────────────────────────────
    current_revenue = get_revenue_for_period(selected_date, selected_end)
    prev_revenue = get_revenue_for_period(prev_date, selected_date)

    current_profit = current_revenue["collected"] - current_costs["total"]
    current_profit_margin = (current_profit / current_revenue["collected"] * 100) if current_revenue["collected"] else 0

    prev_profit = prev_revenue["collected"] - prev_costs["total"]
    prev_profit_margin = (prev_profit / prev_revenue["collected"] * 100) if prev_revenue["collected"] else 0

    profit_margin = {
        "value": round(current_profit_margin, 2),
        "delta": calculate_delta(current_profit_margin, prev_profit_margin)
    }

    # ─── 4) HISTORICAL DATA (4 PREVIOUS MONTHS EXCLUDING SELECTED) ────────────
    history_periods = [selected_date - relativedelta(months=i) for i in range(4, 0, -1)]
    
    total_cost_history = []
    revenue_billed_history = []
    collections_history = []
    
    prev_total_cost = None
    prev_revenue_billed = None
    prev_collections = None

    for period_date in history_periods:
        period_end = period_date + relativedelta(months=1)
        
        period_costs = get_costs_for_period(period_date, period_end)
        period_revenue = get_revenue_for_period(period_date, period_end)
        
        total_cost_delta = calculate_delta(period_costs["total"], prev_total_cost) if prev_total_cost is not None else None
        revenue_billed_delta = calculate_delta(period_revenue["billed"], prev_revenue_billed) if prev_revenue_billed is not None else None
        collections_delta = calculate_delta(period_revenue["collected"], prev_collections) if prev_collections is not None else None
        
        total_cost_history.append({
            "month": period_date.strftime("%b"),
            "value": period_costs["total"],
            "delta": total_cost_delta
        })
        
        revenue_billed_history.append({
            "month": period_date.strftime("%b"),
            "value": period_revenue["billed"],
            "delta": revenue_billed_delta
        })
        
        collections_history.append({
            "month": period_date.strftime("%b"),
            "value": period_revenue["collected"],
            "delta": collections_delta
        })
        
        prev_total_cost = period_costs["total"]
        prev_revenue_billed = period_revenue["billed"]
        prev_collections = period_revenue["collected"]

    # ─── 5) MONTHLY COLLECTIONS FOR ENTIRE YEAR ───────────────────────────────
    yearly_commercial_filter = commercial_base & Q(month__year=year)
    monthly_collections_year = []
    
    for m in range(1, 13):
        month_filter = yearly_commercial_filter & Q(month__month=m)
        month_collections = MonthlyCommercialSummary.objects.filter(month_filter).aggregate(
            collections=Sum("revenue_collected")
        )["collections"] or 0
        
        monthly_collections_year.append({
            "month": date(year, m, 1).strftime("%b"),
            "collections": float(month_collections),
            "billed": 0
        })

    # ─── 6) COLLECTIONS BY VENDOR ──────────────────────────────────────────────
    vendor_collections = DailyCollection.objects.filter(
        date__gte=selected_date, 
        date__lt=selected_end
    ).values("vendor_name").annotate(amount=Sum("amount"))
    
    if vendor_collections.exists():
        collections_by_vendor = [
            {"vendor": row["vendor_name"], "amount": float(row["amount"] or 0)}
            for row in vendor_collections
        ]
    else:
        collections_by_vendor = [
            {"vendor": "Cash", "amount": current_revenue["collected"]}
        ]

    # ─── 7) OPEX BREAKDOWN (AFTER COLLECTIONS BY VENDOR) ──────────────────────
    current_opex_filter = opex_base & Q(date__gte=selected_date, date__lt=selected_end)
    prev_opex_filter = opex_base & Q(date__gte=prev_date, date__lt=selected_date)
    
    def get_opex_breakdown_by_type(qs_current, qs_previous, field_name):
        if field_name == 'both':
            current_data = qs_current.values("opex_category__name").annotate(
                total=Sum("credit") + Sum("debit")
            )
        else:
            current_data = qs_current.values("opex_category__name").annotate(
                total=Sum(field_name)
            )
        
        current_breakdown = {
            row["opex_category__name"] or "Uncategorized": float(row["total"] or 0)
            for row in current_data
        }
        
        if qs_previous is not None:
            if field_name == 'both':
                prev_data = qs_previous.values("opex_category__name").annotate(
                    total=Sum("credit") + Sum("debit")
                )
            else:
                prev_data = qs_previous.values("opex_category__name").annotate(
                    total=Sum(field_name)
                )
            
            prev_breakdown = {
                row["opex_category__name"] or "Uncategorized": float(row["total"] or 0)
                for row in prev_data
            }
        else:
            prev_breakdown = {}
        
        result = []
        for category, current_amount in current_breakdown.items():
            prev_amount = prev_breakdown.get(category, 0)
            delta = calculate_delta(current_amount, prev_amount) if prev_amount else None
            
            result.append({
                "category": category,
                "amount": current_amount,
                "delta": delta,
            })
        
        result.sort(key=lambda x: x["amount"], reverse=True)
        return result
    
    qs_current = Opex.objects.filter(current_opex_filter)
    qs_previous = Opex.objects.filter(prev_opex_filter) if prev_opex_filter else None
    
    opex_breakdown = {
        "all": get_opex_breakdown_by_type(qs_current, qs_previous, "both"),
        "credit_only": get_opex_breakdown_by_type(
            qs_current.filter(credit__gt=0), 
            qs_previous.filter(credit__gt=0) if qs_previous else None, 
            "credit"
        ),
        "debit_only": get_opex_breakdown_by_type(
            qs_current.filter(debit__gt=0), 
            qs_previous.filter(debit__gt=0) if qs_previous else None, 
            "debit"
        ),
    }

    # ─── 8) HISTORICAL COSTS BREAKDOWN (4 MONTHS INCLUDING SELECTED) ──────────
    periods_including_selected = [selected_date - relativedelta(months=i) for i in range(3, -1, -1)]
    historical_costs = []
    prev_month_total = None
    
    for period_date in periods_including_selected:
        period_end = period_date + relativedelta(months=1)
        period_costs = get_costs_for_period(period_date, period_end)
        
        month_total = period_costs["total"]
        month_delta = calculate_delta(month_total, prev_month_total) if prev_month_total is not None else None
        
        historical_costs.append({
            "month": period_date.strftime("%b"),
            "nbet": period_costs["nbet"],
            "mo": period_costs["mo"],
            "salaries": period_costs["salaries"],
            "disco_opex": period_costs["opex"],
            "total": month_total,
            "delta": month_delta,
            "energy_share": period_costs["energy_share"]  # For debugging/transparency
        })
        
        prev_month_total = month_total

    # ─── 9) HISTORICAL TARIFFS (4 MONTHS INCLUDING SELECTED) ───────────────────
    historical_tariffs = []
    
    for period_date in periods_including_selected:
        period_end = period_date + relativedelta(months=1)
        
        # Energy delivered for tariff calculations
        energy_delivered = EnergyDelivered.objects.filter(
            energy_base & Q(date__gte=period_date, date__lt=period_end)
        ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")

        # Commercial data for tariff calculations
        period_commercial_filter = commercial_base & Q(month__gte=period_date, month__lt=period_end)
        commercial_data = MonthlyCommercialSummary.objects.filter(period_commercial_filter).aggregate(
            revenue_billed=Sum("revenue_billed"), 
            revenue_collected=Sum("revenue_collected")
        )
        
        revenue_billed = commercial_data["revenue_billed"] or 0
        revenue_collected = commercial_data["revenue_collected"] or 0

        # Calculate tariffs
        billing_tariff = (revenue_billed / (energy_delivered * 1000)) if energy_delivered else 0
        collection_tariff = (revenue_collected / (energy_delivered * 1000)) if energy_delivered else 0
        tariff_loss = billing_tariff - collection_tariff

        # Get MYTO tariff
        myto_tariff_obj = MYTOTariff.objects.filter(
            effective_date__lte=period_date
        ).order_by("-effective_date").first()
        myto_tariff = float(myto_tariff_obj.rate_per_kwh) if myto_tariff_obj else 60.0

        historical_tariffs.append({
            "month": period_date.strftime("%b"),
            "myto_tariff": myto_tariff,
            "billing_tariff": round(float(billing_tariff), 2),
            "collection_tariff": round(float(collection_tariff), 2),
            "tariff_loss": round(float(tariff_loss), 2),
        })

    # ─── 10) BUILD RESPONSE IN FRONTEND ORDER ─────────────────────────────────
    return Response({
        # 1. Operating Expenditure (first display)
        "operating_expenditure": operating_expenditure,
        
        # 2. Profit Margin (second display)
        "profit_margin": profit_margin,
        
        # 3. Total Cost with history (third component)
        "total_cost": {
            "current": current_costs["total"],
            "delta": calculate_delta(current_costs["total"], prev_costs["total"]),
            "history": total_cost_history
        },
        
        # 4. Revenue Billed with history (fourth component)
        "revenue_billed": {
            "current": current_revenue["billed"],
            "delta": calculate_delta(current_revenue["billed"], prev_revenue["billed"]),
            "history": revenue_billed_history
        },
        
        # 5. Collections with history (fifth component)
        "collections": {
            "current": current_revenue["collected"],
            "delta": calculate_delta(current_revenue["collected"], prev_revenue["collected"]),
            "history": collections_history
        },
        
        # 6. Monthly collections line chart (sixth component)
        "monthly_collections_year": monthly_collections_year,
        
        # 7. Collections by vendor (seventh component)
        "collections_by_vendor": collections_by_vendor,
        
        # 8. OPEX breakdown (eighth component)
        "opex_breakdown": opex_breakdown,
        
        # 9. Historical costs breakdown (ninth component)
        "historical_costs": historical_costs,
        
        # 10. Historical tariffs (tenth component)
        "historical_tariffs": historical_tariffs,
        
        # Additional metadata for debugging/transparency
        "filter_info": {
            "level": "transformer" if transformer_slug else "feeder" if feeder_slug else "district" if district_name else "state" if state_name else "all",
            "transformer": transformer_slug,
            "feeder": feeder_slug,
            "district": district_name,
            "state": state_name,
            "current_energy_share": current_costs.get("energy_share", 0),
            "transformer_opex_share": current_costs.get("transformer_opex_share", None)
        }
    })