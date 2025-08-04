from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from dateutil.relativedelta import relativedelta
import calendar
from django.db.models import Sum, Q
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.decorators import api_view
from financial.models import Opex, HQOpex, SalaryPayment, NBETInvoice, MOInvoice, MYTOTariff
from common.models import Feeder, DistributionTransformer
from commercial.models import MonthlyCommercialSummary, DailyCollection
from technical.models import EnergyDelivered, FeederEnergyMonthly, FeederEnergyDaily


def safe_decimal(value):
    """Convert value to Decimal safely"""
    try:
        return Decimal(str(value or 0))
    except (TypeError, ValueError):
        return Decimal(0)


def safe_float(value):
    """Convert value to float safely"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def calculate_delta(current, previous):
    """Calculate percentage change between current and previous values"""
    if previous and previous != 0:
        return round(((current - previous) / previous) * 100, 2)
    return None


def parse_date_params(request):
    """Parse and determine date filtering mode and ranges"""
    mode = request.query_params.get("mode", "monthly")
    from_date_str = request.query_params.get("from_date")
    to_date_str = request.query_params.get("to_date")
    year = int(request.query_params.get("year", date.today().year))
    month = request.query_params.get("month")
    
    # Handle legacy year/month parameters for backward compatibility
    if not from_date_str or not to_date_str:
        today = date.today()
        if mode == "yearly":
            from_date = date(year, 1, 1)
            to_date = date(year + 1, 1, 1)
        elif mode == "weekly":
            # Get start of current week (Monday)
            days_since_monday = today.weekday()
            from_date = today - relativedelta(days=days_since_monday)
            to_date = from_date + relativedelta(days=7)
        elif mode == "daily":
            from_date = today
            to_date = today + relativedelta(days=1)
        else:  # monthly
            if month:
                from_date = date(year, int(month), 1)
                to_date = from_date + relativedelta(months=1)
            else:
                from_date = date(today.year, today.month, 1)
                to_date = from_date + relativedelta(months=1)
    else:
        # Parse provided dates
        from_datetime = parse_datetime(from_date_str)
        to_datetime = parse_datetime(to_date_str)
        
        if not from_datetime or not to_datetime:
            raise ValueError("Invalid date format. Use ISO format: YYYY-MM-DDTHH:MM:SS.sssZ")
        
        from_date = from_datetime.date()
        to_date = to_datetime.date()
        
        # Ensure to_date is exclusive (add 1 day if it seems to be inclusive)
        if to_date == from_date:
            to_date = to_date + relativedelta(days=1)
        
        # Auto-detect mode if not explicitly set to custom
        if mode != "custom":
            date_diff = (to_date - from_date).days
            if date_diff == 1:
                mode = "daily"
            elif date_diff == 7:
                mode = "weekly"  
            elif date_diff >= 28 and date_diff <= 31:
                mode = "monthly"
            elif date_diff >= 365 and date_diff <= 366:
                mode = "yearly"
            else:
                mode = "custom"
    
    return mode, from_date, to_date


def get_historical_periods(mode, from_date, to_date, count=4):
    """Get historical periods based on mode and date range"""
    periods = []
    current_from = from_date
    current_to = to_date
    
    if mode == "daily":
        for i in range(count):
            period_from = current_from - relativedelta(days=i+1)
            period_to = period_from + relativedelta(days=1)
            periods.insert(0, (period_from, period_to))
    elif mode == "weekly":
        for i in range(count):
            period_from = current_from - relativedelta(weeks=i+1)
            period_to = period_from + relativedelta(weeks=1)
            periods.insert(0, (period_from, period_to))
    elif mode == "monthly":
        for i in range(count):
            period_from = current_from - relativedelta(months=i+1)
            period_to = period_from + relativedelta(months=1)
            periods.insert(0, (period_from, period_to))
    elif mode == "yearly":
        for i in range(count):
            period_from = current_from - relativedelta(years=i+1)
            period_to = period_from + relativedelta(years=1)
            periods.insert(0, (period_from, period_to))
    else:  # custom
        # Calculate the range difference and create similar periods
        date_diff = (to_date - from_date).days
        for i in range(count):
            period_from = current_from - relativedelta(days=date_diff * (i+1))
            period_to = period_from + relativedelta(days=date_diff)
            periods.insert(0, (period_from, period_to))
    
    return periods


def format_period_label(mode, from_date, to_date, index=None):
    """Format period label based on mode"""
    if mode == "daily":
        return from_date.strftime("%a")  # Mon, Tue, Wed, Thu, Fri, Sat, Sun
    elif mode == "weekly":
        week_num = from_date.isocalendar()[1]
        return f"Wk{week_num}"
    elif mode == "monthly":
        return from_date.strftime("%b")  # Jan, Feb, etc.
    elif mode == "yearly":
        return str(from_date.year)  # 2023, 2024, 2025, etc.
    else:  # custom
        if index is not None:
            return f"P{index + 1}"
        else:
            return f"{from_date.strftime('%m/%d')}-{to_date.strftime('%m/%d')}"


def get_energy_delivered_optimized(energy_filter, start_date, end_date):
    """Get energy delivered using optimized queries"""
    try:
        # For single month periods, try monthly aggregates first
        if (end_date - start_date).days <= 32:
            delivered = FeederEnergyMonthly.objects.filter(
                energy_filter & Q(period=start_date)
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
            
            if delivered:
                return safe_decimal(delivered)
        
        # Try daily aggregation
        end_date_actual = end_date - relativedelta(days=1)
        delivered = FeederEnergyDaily.objects.filter(
            energy_filter & Q(date__gte=start_date, date__lte=end_date_actual)
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered:
            return safe_decimal(delivered)
        
        # Fallback to EnergyDelivered
        delivered = EnergyDelivered.objects.filter(
            energy_filter & Q(date__gte=start_date, date__lt=end_date)
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return safe_decimal(delivered)
        
    except Exception:
        return Decimal(0)


@api_view(["GET"])
def financial_overview_view(request):
    # ─── 1) PARSE DATE PARAMETERS ──────────────────────────────────────────────
    try:
        mode, from_date, to_date = parse_date_params(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    # ─── 2) LOCATION FILTERS ───────────────────────────────────────────────────
    state_name = request.query_params.get("state")
    district_name = request.query_params.get("business_district")
    feeder_slug = request.query_params.get("feeder")
    transformer_slug = request.query_params.get("transformer")

    # Pre-fetch location entities and build filters
    location_info = {}
    commercial_transformers = []
    energy_feeders = []
    
    if transformer_slug:
        # Transformer-level filtering (highest precedence)
        try:
            transformer = DistributionTransformer.objects.select_related(
                'feeder__business_district'
            ).get(slug=transformer_slug)
            commercial_transformers = [transformer]
            energy_feeders = [transformer.feeder]
            location_info = {
                'type': 'transformer',
                'transformer': transformer,
                'feeder': transformer.feeder,
                'district': transformer.feeder.business_district
            }
        except DistributionTransformer.DoesNotExist:
            return Response({"error": "Transformer not found"}, status=400)
            
    elif feeder_slug:
        # Feeder-level filtering
        try:
            feeder = Feeder.objects.select_related('business_district').get(slug=feeder_slug)
            commercial_transformers = list(DistributionTransformer.objects.filter(feeder=feeder))
            energy_feeders = [feeder]
            location_info = {
                'type': 'feeder',
                'feeder': feeder,
                'district': feeder.business_district
            }
        except Feeder.DoesNotExist:
            return Response({"error": "Feeder not found"}, status=400)
            
    elif district_name:
        # Business district filtering
        feeders = list(Feeder.objects.filter(business_district__name__iexact=district_name))
        commercial_transformers = list(DistributionTransformer.objects.filter(feeder__in=feeders))
        energy_feeders = feeders
        location_info = {'type': 'district', 'name': district_name}
        
    elif state_name:
        # State filtering
        feeders = list(Feeder.objects.filter(business_district__state__name__iexact=state_name))
        commercial_transformers = list(DistributionTransformer.objects.filter(feeder__in=feeders))
        energy_feeders = feeders
        location_info = {'type': 'state', 'name': state_name}
    else:
        # Global filtering
        commercial_transformers = None  # Will use no filter
        energy_feeders = None  # Will use no filter
        location_info = {'type': 'global'}

    def get_energy_share(start_date, end_date):
        """Calculate energy share for proportional allocation"""
        if location_info['type'] == 'global':
            return Decimal(1)  # Global gets 100% share
            
        # Energy delivered for filtered scope
        if energy_feeders:
            filtered_energy = get_energy_delivered_optimized(
                Q(feeder__in=energy_feeders), start_date, end_date
            )
        else:
            filtered_energy = Decimal(0)
        
        # Total energy delivered across all feeders
        total_energy = get_energy_delivered_optimized(Q(), start_date, end_date)
        
        # Calculate share (0-1)
        if total_energy > 0:
            return filtered_energy / total_energy
        return Decimal(0)

    def get_costs_for_period(start_date, end_date):
        """Get all cost components for a given period"""
        # Calculate energy share for proportional allocation
        energy_share = get_energy_share(start_date, end_date)
        
        # Initialize transformer share for transformer-level filtering
        transformer_share = Decimal(1)
        
        if location_info['type'] == 'transformer':
            # Transformer gets equal share of feeder's costs
            feeder_transformers_count = DistributionTransformer.objects.filter(
                feeder=location_info['transformer'].feeder
            ).count()
            transformer_share = Decimal(1) / Decimal(feeder_transformers_count) if feeder_transformers_count > 0 else Decimal(1)

        # OPEX calculation (sum of both debit and credit) - combining Opex and HQOpex
        opex_total = Decimal(0)
        hq_opex_total = Decimal(0)
        
        # Initialize shares for transformer/feeder level filtering
        transformer_district_share = Decimal(1)
        feeder_district_share = Decimal(1)
        
        if location_info['type'] in ['transformer', 'feeder']:
            # Use district-level OPEX
            district = location_info.get('district')
            if district:
                # Regular OPEX
                opex_data = Opex.objects.filter(
                    district=district,
                    date__gte=start_date,
                    date__lt=end_date
                ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
                
                district_opex = safe_decimal(opex_data["debit"]) + safe_decimal(opex_data["credit"])
                
                # HQ OPEX is allocated proportionally to all districts
                hq_opex_data = HQOpex.objects.filter(
                    date__gte=start_date,
                    date__lt=end_date
                ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
                
                total_hq_opex = safe_decimal(hq_opex_data["debit"]) + safe_decimal(hq_opex_data["credit"])
                
                # Allocate HQ OPEX based on energy share
                hq_opex_allocated = total_hq_opex * energy_share
                
                if location_info['type'] == 'transformer':
                    # Transformer gets proportional share based on transformer count in district
                    district_transformers_count = DistributionTransformer.objects.filter(
                        feeder__business_district=district
                    ).count()
                    transformer_district_share = Decimal(1) / Decimal(district_transformers_count) if district_transformers_count > 0 else Decimal(1)
                    opex_total = district_opex * transformer_district_share
                    hq_opex_total = hq_opex_allocated * transformer_district_share
                else:
                    # Feeder gets proportional share based on feeder count in district
                    district_feeders_count = Feeder.objects.filter(business_district=district).count()
                    feeder_district_share = Decimal(1) / Decimal(district_feeders_count) if district_feeders_count > 0 else Decimal(1)
                    opex_total = district_opex * feeder_district_share
                    hq_opex_total = hq_opex_allocated * feeder_district_share
                    
        elif location_info['type'] == 'district':
            # District-level OPEX
            opex_data = Opex.objects.filter(
                district__name__iexact=location_info['name'],
                date__gte=start_date,
                date__lt=end_date
            ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            opex_total = safe_decimal(opex_data["debit"]) + safe_decimal(opex_data["credit"])
            
            # Allocate HQ OPEX proportionally
            hq_opex_data = HQOpex.objects.filter(
                date__gte=start_date,
                date__lt=end_date
            ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            total_hq_opex = safe_decimal(hq_opex_data["debit"]) + safe_decimal(hq_opex_data["credit"])
            hq_opex_total = total_hq_opex * energy_share
            
        elif location_info['type'] == 'state':
            # State-level OPEX
            opex_data = Opex.objects.filter(
                district__state__name__iexact=location_info['name'],
                date__gte=start_date,
                date__lt=end_date
            ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            opex_total = safe_decimal(opex_data["debit"]) + safe_decimal(opex_data["credit"])
            
            # Allocate HQ OPEX proportionally
            hq_opex_data = HQOpex.objects.filter(
                date__gte=start_date,
                date__lt=end_date
            ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            total_hq_opex = safe_decimal(hq_opex_data["debit"]) + safe_decimal(hq_opex_data["credit"])
            hq_opex_total = total_hq_opex * energy_share
            
        else:
            # Global OPEX
            opex_data = Opex.objects.filter(
                date__gte=start_date,
                date__lt=end_date
            ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            opex_total = safe_decimal(opex_data["debit"]) + safe_decimal(opex_data["credit"])
            
            hq_opex_data = HQOpex.objects.filter(
                date__gte=start_date,
                date__lt=end_date
            ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
            hq_opex_total = safe_decimal(hq_opex_data["debit"]) + safe_decimal(hq_opex_data["credit"])

        # Combined OPEX total (Opex + HQOpex)
        combined_opex_total = opex_total + hq_opex_total

        # Salaries calculation (handle periods shorter than a month)
        salary_period_start = start_date.replace(day=1)  # Always use first of month for salary queries
        salary_period_end = end_date.replace(day=1) + relativedelta(months=1) if end_date.day > 1 else end_date.replace(day=1)
        
        # Calculate proration factor for periods shorter than a month
        period_days = (end_date - start_date).days
        if period_days < 28:  # Shorter than a typical month
            # Get the number of days in the month containing the period
            month_days = calendar.monthrange(start_date.year, start_date.month)[1]
            proration_factor = Decimal(period_days) / Decimal(month_days)
        else:
            proration_factor = Decimal(1)  # No proration for longer periods
            
        if location_info['type'] in ['transformer', 'feeder']:
            # Use district-level salaries
            district = location_info.get('district')
            if district:
                district_salary_total = SalaryPayment.objects.filter(
                    district=district,
                    month__gte=salary_period_start,
                    month__lt=salary_period_end
                ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
                
                # Apply proration and location-specific share
                district_salary_total = district_salary_total * proration_factor
                
                if location_info['type'] == 'transformer':
                    salary_total = district_salary_total * transformer_district_share
                else:
                    salary_total = district_salary_total * feeder_district_share
            else:
                salary_total = Decimal(0)
                
        elif location_info['type'] == 'district':
            district_salary_total = SalaryPayment.objects.filter(
                district__name__iexact=location_info['name'],
                month__gte=salary_period_start,
                month__lt=salary_period_end
            ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
            salary_total = district_salary_total * proration_factor
            
        elif location_info['type'] == 'state':
            state_salary_total = SalaryPayment.objects.filter(
                district__state__name__iexact=location_info['name'],
                month__gte=salary_period_start,
                month__lt=salary_period_end
            ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
            salary_total = state_salary_total * proration_factor
            
        else:
            # Global salaries
            global_salary_total = SalaryPayment.objects.filter(
                month__gte=salary_period_start,
                month__lt=salary_period_end
            ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
            salary_total = global_salary_total * proration_factor

        # NBET Invoice (allocated proportionally based on energy share)
        # Handle periods shorter than a month by prorating
        nbet_period_start = start_date.replace(day=1)
        nbet_period_end = end_date.replace(day=1) + relativedelta(months=1) if end_date.day > 1 else end_date.replace(day=1)
        
        nbet_monthly_total = NBETInvoice.objects.filter(
            month__gte=nbet_period_start,
            month__lt=nbet_period_end
        ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
        
        nbet_allocated = (nbet_monthly_total * proration_factor) * energy_share

        # MO Invoice (allocated proportionally based on energy share)
        mo_monthly_total = MOInvoice.objects.filter(
            month__gte=nbet_period_start,
            month__lt=nbet_period_end
        ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
        
        mo_allocated = (mo_monthly_total * proration_factor) * energy_share

        total_cost = combined_opex_total + salary_total + nbet_allocated + mo_allocated

        return {
            "opex": safe_float(opex_total),
            "hq_opex": safe_float(hq_opex_total),
            "disco_opex": safe_float(combined_opex_total),  # Combined OPEX for backward compatibility
            "salaries": safe_float(salary_total),
            "nbet": safe_float(nbet_allocated),
            "mo": safe_float(mo_allocated),
            "total": safe_float(total_cost),
            "energy_share": safe_float(energy_share)
        }

    def get_revenue_for_period(start_date, end_date):
        """Get revenue data for a given period"""
        # For daily/weekly modes, use DailyCollection for more accurate data
        if mode in ["daily", "weekly"] and (end_date - start_date).days <= 7:
            if commercial_transformers is not None:
                # Filtered by specific transformers
                daily_revenue = DailyCollection.objects.filter(
                    transformer__in=commercial_transformers,
                    date__gte=start_date,
                    date__lt=end_date
                ).aggregate(revenue_collected=Sum("amount"))
                
                # For billed, we still need MonthlyCommercialSummary
                monthly_revenue = MonthlyCommercialSummary.objects.filter(
                    transformer__in=commercial_transformers,
                    month__gte=start_date.replace(day=1),
                    month__lt=end_date
                ).aggregate(revenue_billed=Sum("revenue_billed"))
                
                return {
                    "billed": safe_float(monthly_revenue["revenue_billed"]) * ((end_date - start_date).days / 30.0),  # Prorate monthly to period
                    "collected": safe_float(daily_revenue["revenue_collected"])
                }
            else:
                # Global revenue
                daily_revenue = DailyCollection.objects.filter(
                    date__gte=start_date,
                    date__lt=end_date
                ).aggregate(revenue_collected=Sum("amount"))
                
                monthly_revenue = MonthlyCommercialSummary.objects.filter(
                    month__gte=start_date.replace(day=1),
                    month__lt=end_date
                ).aggregate(revenue_billed=Sum("revenue_billed"))
                
                return {
                    "billed": safe_float(monthly_revenue["revenue_billed"]) * ((end_date - start_date).days / 30.0),  # Prorate monthly to period
                    "collected": safe_float(daily_revenue["revenue_collected"])
                }
        else:
            # Use MonthlyCommercialSummary for longer periods
            if commercial_transformers is not None:
                # Filtered by specific transformers
                revenue_data = MonthlyCommercialSummary.objects.filter(
                    transformer__in=commercial_transformers,
                    month__gte=start_date,
                    month__lt=end_date
                ).aggregate(
                    revenue_billed=Sum("revenue_billed"),
                    revenue_collected=Sum("revenue_collected"),
                )
            else:
                # Global revenue
                revenue_data = MonthlyCommercialSummary.objects.filter(
                    month__gte=start_date,
                    month__lt=end_date
                ).aggregate(
                    revenue_billed=Sum("revenue_billed"),
                    revenue_collected=Sum("revenue_collected"),
                )
                
            return {
                "billed": safe_float(revenue_data["revenue_billed"]),
                "collected": safe_float(revenue_data["revenue_collected"])
            }

    # ─── 3) GET CURRENT AND PREVIOUS PERIOD DATA ──────────────────────────────
    # Get previous period for delta calculations
    historical_periods = get_historical_periods(mode, from_date, to_date, count=1)
    prev_start, prev_end = historical_periods[0] if historical_periods else (from_date, to_date)
    
    current_costs = get_costs_for_period(from_date, to_date)
    prev_costs = get_costs_for_period(prev_start, prev_end)
    
    # ─── 4) OPERATING EXPENDITURE (Current Period + Delta) ─────────────────────
    operating_expenditure = {
        "value": current_costs["total"],
        "delta": calculate_delta(current_costs["total"], prev_costs["total"])
    }

    # ─── 5) PROFIT MARGIN (Current Period + Delta) ─────────────────────────────
    current_revenue = get_revenue_for_period(from_date, to_date)
    prev_revenue = get_revenue_for_period(prev_start, prev_end)

    current_profit = current_revenue["collected"] - current_costs["total"]
    current_profit_margin = (current_profit / current_revenue["collected"] * 100) if current_revenue["collected"] else 0

    prev_profit = prev_revenue["collected"] - prev_costs["total"]
    prev_profit_margin = (prev_profit / prev_revenue["collected"] * 100) if prev_revenue["collected"] else 0

    profit_margin = {
        "value": round(current_profit_margin, 2),
        "delta": calculate_delta(current_profit_margin, prev_profit_margin)
    }

    # ─── 6) HISTORICAL DATA (4 PREVIOUS PERIODS EXCLUDING CURRENT) ────────────
    history_periods = get_historical_periods(mode, from_date, to_date, count=4)
    
    total_cost_history = []
    revenue_billed_history = []
    collections_history = []
    
    prev_total_cost = None
    prev_revenue_billed = None
    prev_collections = None

    for i, (period_start, period_end) in enumerate(history_periods):
        period_costs = get_costs_for_period(period_start, period_end)
        period_revenue = get_revenue_for_period(period_start, period_end)
        
        total_cost_delta = calculate_delta(period_costs["total"], prev_total_cost) if prev_total_cost is not None else None
        revenue_billed_delta = calculate_delta(period_revenue["billed"], prev_revenue_billed) if prev_revenue_billed is not None else None
        collections_delta = calculate_delta(period_revenue["collected"], prev_collections) if prev_collections is not None else None
        
        period_label = format_period_label(mode, period_start, period_end, i)
        
        total_cost_history.append({
            "month": period_label,
            "value": period_costs["total"],
            "delta": total_cost_delta
        })
        
        revenue_billed_history.append({
            "month": period_label,
            "value": period_revenue["billed"],
            "delta": revenue_billed_delta
        })
        
        collections_history.append({
            "month": period_label,
            "value": period_revenue["collected"],
            "delta": collections_delta
        })
        
        prev_total_cost = period_costs["total"]
        prev_revenue_billed = period_revenue["billed"]
        prev_collections = period_revenue["collected"]

    # ─── 7) PERIOD COLLECTIONS FOR YEAR/LONGER PERIODS ───────────────────────
    # Generate appropriate periods based on mode
    yearly_periods = []
    
    if mode == "yearly":
        # Generate monthly collections for the year
        year = from_date.year
        for m in range(1, 13):
            month_start = date(year, m, 1)
            month_end = month_start + relativedelta(months=1)
            
            # Only include months that are within our date range
            if month_start >= from_date and month_start < to_date:
                month_revenue = get_revenue_for_period(month_start, month_end)
                
                yearly_periods.append({
                    "month": month_start.strftime("%b"),
                    "collections": month_revenue["collected"],
                    "billed": month_revenue["billed"]
                })
    elif mode == "monthly":
        # Generate daily collections for the month
        current_date = from_date
        while current_date < to_date:
            day_end = min(current_date + relativedelta(days=1), to_date)
            
            day_revenue = get_revenue_for_period(current_date, day_end)
            
            yearly_periods.append({
                "month": current_date.strftime("%d"),
                "collections": day_revenue["collected"],
                "billed": day_revenue["billed"]
            })
            
            current_date += relativedelta(days=1)
    elif mode == "weekly":
        # Generate daily collections for the week
        current_date = from_date
        day_count = 0
        while current_date < to_date and day_count < 7:
            day_end = min(current_date + relativedelta(days=1), to_date)
            
            day_revenue = get_revenue_for_period(current_date, day_end)
            
            yearly_periods.append({
                "month": current_date.strftime("%a"),  # Mon, Tue, Wed, etc.
                "collections": day_revenue["collected"],
                "billed": day_revenue["billed"]
            })
            
            current_date += relativedelta(days=1)
            day_count += 1
    elif mode == "daily":
        # Single day - show daily data only
        yearly_periods.append({
            "month": from_date.strftime("%a"),
            "collections": current_revenue["collected"],
            "billed": current_revenue["billed"]
        })
    else:
        # For custom ranges, show breakdown based on duration
        date_diff = (to_date - from_date).days
        if date_diff <= 7:
            # Show daily breakdown for short custom ranges
            current_date = from_date
            while current_date < to_date:
                day_end = min(current_date + relativedelta(days=1), to_date)
                
                day_revenue = get_revenue_for_period(current_date, day_end)
                
                yearly_periods.append({
                    "month": current_date.strftime("%m/%d"),
                    "collections": day_revenue["collected"],
                    "billed": day_revenue["billed"]
                })
                
                current_date += relativedelta(days=1)
        else:
            # Show weekly breakdown for longer custom ranges
            current_date = from_date
            week_num = 1
            while current_date < to_date:
                week_end = min(current_date + relativedelta(days=7), to_date)
                
                week_revenue = get_revenue_for_period(current_date, week_end)
                
                yearly_periods.append({
                    "month": f"W{week_num}",
                    "collections": week_revenue["collected"],
                    "billed": week_revenue["billed"]
                })
                
                current_date += relativedelta(days=7)
                week_num += 1

    # ─── 8) COLLECTIONS BY VENDOR ──────────────────────────────────────────────
    if commercial_transformers is not None:
        vendor_collections = DailyCollection.objects.filter(
            transformer__in=commercial_transformers,
            date__gte=from_date, 
            date__lt=to_date
        ).values("vendor_name").annotate(amount=Sum("amount"))
    else:
        vendor_collections = DailyCollection.objects.filter(
            date__gte=from_date, 
            date__lt=to_date
        ).values("vendor_name").annotate(amount=Sum("amount"))
    
    if vendor_collections.exists():
        collections_by_vendor = [
            {"vendor": row["vendor_name"], "amount": safe_float(row["amount"])}
            for row in vendor_collections
        ]
    else:
        collections_by_vendor = [
            {"vendor": "Cash", "amount": current_revenue["collected"]}
        ]

    # ─── 9) OPEX BREAKDOWN WITH SEPARATE CATEGORIES ───────────────────────────
    def get_opex_breakdown():
        """Get OPEX breakdown by Opex and HQOpex with previous period comparison"""
        current_start = from_date
        current_end = to_date
        previous_start = prev_start
        previous_end = prev_end
        
        # Current period breakdown
        def get_opex_by_category(queryset, is_hq=False):
            data = queryset.values("opex_category__name").annotate(
                total=Sum("credit") + Sum("debit")
            )
            return {
                (row["opex_category__name"] or "Uncategorized"): {
                    "amount": safe_float(row["total"]),
                    "type": "HQ OPEX" if is_hq else "District OPEX"
                }
                for row in data
            }
        
        # Build current period queries based on location filter
        if location_info['type'] in ['transformer', 'feeder']:
            district = location_info.get('district')
            if district:
                current_opex_qs = Opex.objects.filter(district=district, date__gte=current_start, date__lt=current_end)
                prev_opex_qs = Opex.objects.filter(district=district, date__gte=previous_start, date__lt=previous_end)
            else:
                current_opex_qs = Opex.objects.none()
                prev_opex_qs = Opex.objects.none()
        elif location_info['type'] == 'district':
            current_opex_qs = Opex.objects.filter(district__name__iexact=location_info['name'], date__gte=current_start, date__lt=current_end)
            prev_opex_qs = Opex.objects.filter(district__name__iexact=location_info['name'], date__gte=previous_start, date__lt=previous_end)
        elif location_info['type'] == 'state':
            current_opex_qs = Opex.objects.filter(district__state__name__iexact=location_info['name'], date__gte=current_start, date__lt=current_end)
            prev_opex_qs = Opex.objects.filter(district__state__name__iexact=location_info['name'], date__gte=previous_start, date__lt=previous_end)
        else:
            current_opex_qs = Opex.objects.filter(date__gte=current_start, date__lt=current_end)
            prev_opex_qs = Opex.objects.filter(date__gte=previous_start, date__lt=previous_end)
        
        # HQ OPEX queries (always global, allocated proportionally)
        current_hq_qs = HQOpex.objects.filter(date__gte=current_start, date__lt=current_end)
        prev_hq_qs = HQOpex.objects.filter(date__gte=previous_start, date__lt=previous_end)
        
        # Get current period data
        current_opex_breakdown = get_opex_by_category(current_opex_qs, is_hq=False)
        current_hq_breakdown = get_opex_by_category(current_hq_qs, is_hq=True)
        
        # Get previous period data
        prev_opex_breakdown = get_opex_by_category(prev_opex_qs, is_hq=False)
        prev_hq_breakdown = get_opex_by_category(prev_hq_qs, is_hq=True)
        
        # Combine all categories and calculate deltas
        all_categories = set(list(current_opex_breakdown.keys()) + list(current_hq_breakdown.keys()) + 
                           list(prev_opex_breakdown.keys()) + list(prev_hq_breakdown.keys()))
        
        # Prepare results
        all_opex = []
        opex_only = []
        hq_opex_only = []
        
        for category in all_categories:
            # Current amounts
            current_opex_amount = current_opex_breakdown.get(category, {}).get("amount", 0)
            current_hq_amount = current_hq_breakdown.get(category, {}).get("amount", 0)
            
            # Previous amounts
            prev_opex_amount = prev_opex_breakdown.get(category, {}).get("amount", 0)
            prev_hq_amount = prev_hq_breakdown.get(category, {}).get("amount", 0)
            
            # Combined amounts (Opex + HQOpex combined for "all")
            current_total = current_opex_amount + current_hq_amount
            prev_total = prev_opex_amount + prev_hq_amount
            
            # Calculate deltas
            total_delta = calculate_delta(current_total, prev_total) if prev_total > 0 else None
            opex_delta = calculate_delta(current_opex_amount, prev_opex_amount) if prev_opex_amount > 0 else None
            hq_delta = calculate_delta(current_hq_amount, prev_hq_amount) if prev_hq_amount > 0 else None
            
            # Add to results if there's any current activity
            if current_total > 0:
                all_opex.append({
                    "category": category,
                    "amount": current_total,
                    "delta": total_delta
                })
            
            if current_opex_amount > 0:
                opex_only.append({
                    "category": category,
                    "amount": current_opex_amount,
                    "delta": opex_delta
                })
            
            if current_hq_amount > 0:
                hq_opex_only.append({
                    "category": category,
                    "amount": current_hq_amount,
                    "delta": hq_delta
                })
        
        # Sort by amount (descending)
        all_opex.sort(key=lambda x: x["amount"], reverse=True)
        opex_only.sort(key=lambda x: x["amount"], reverse=True)
        hq_opex_only.sort(key=lambda x: x["amount"], reverse=True)
        
        return {
            "all": all_opex,
            "opex": opex_only,
            "hq_opex": hq_opex_only,
        }
    
    opex_breakdown = get_opex_breakdown()

    # ─── 10) HISTORICAL COSTS BREAKDOWN (4 PERIODS INCLUDING CURRENT) ─────────
    periods_including_current = get_historical_periods(mode, from_date, to_date, count=3)
    periods_including_current.append((from_date, to_date))  # Add current period
    
    historical_costs = []
    prev_month_total = None
    
    for i, (period_start, period_end) in enumerate(periods_including_current):
        period_costs = get_costs_for_period(period_start, period_end)
        
        month_total = period_costs["total"]
        month_delta = calculate_delta(month_total, prev_month_total) if prev_month_total is not None else None
        
        period_label = format_period_label(mode, period_start, period_end, i)
        
        historical_costs.append({
            "month": period_label,
            "nbet": period_costs["nbet"],
            "mo": period_costs["mo"],
            "salaries": period_costs["salaries"],
            "disco_opex": period_costs["disco_opex"],  # This now includes both Opex and HQOpex combined
            "total": month_total,
            "delta": month_delta,
            "energy_share": period_costs["energy_share"]
        })
        
        prev_month_total = month_total

    # ─── 11) HISTORICAL TARIFFS (4 PERIODS INCLUDING CURRENT) ─────────────────
    historical_tariffs = []
    
    for i, (period_start, period_end) in enumerate(periods_including_current):
        # Energy delivered for tariff calculations
        if energy_feeders:
            energy_delivered = get_energy_delivered_optimized(
                Q(feeder__in=energy_feeders), period_start, period_end
            )
        else:
            energy_delivered = get_energy_delivered_optimized(Q(), period_start, period_end)

        # Commercial data for tariff calculations
        period_revenue = get_revenue_for_period(period_start, period_end)
        
        revenue_billed = safe_decimal(period_revenue["billed"])
        revenue_collected = safe_decimal(period_revenue["collected"])

        # Calculate tariffs (₦/kWh)
        # Convert MWh to kWh for tariff calculation
        energy_delivered_kwh = energy_delivered * Decimal(1000)
        
        # Billing Tariff = Revenue Billed / Energy Delivered (in kWh)
        billing_tariff = (revenue_billed / energy_delivered_kwh) if energy_delivered_kwh > 0 else Decimal(0)
        
        # Collection Tariff = Revenue Collected / Energy Delivered (in kWh) 
        collection_tariff = (revenue_collected / energy_delivered_kwh) if energy_delivered_kwh > 0 else Decimal(0)
        
        # Tariff Loss = Billing Tariff - Collection Tariff
        tariff_loss = billing_tariff - collection_tariff

        # Get MYTO tariff (assuming single band for now, can be enhanced)
        myto_tariff_obj = MYTOTariff.objects.filter(
            effective_date__lte=period_start
        ).order_by("-effective_date").first()
        myto_tariff = safe_float(myto_tariff_obj.rate_per_kwh) if myto_tariff_obj else 60.0

        period_label = format_period_label(mode, period_start, period_end, i)
        
        historical_tariffs.append({
            "month": period_label,
            "myto_tariff": myto_tariff,
            "billing_tariff": safe_float(billing_tariff),
            "collection_tariff": safe_float(collection_tariff),
            "tariff_loss": safe_float(tariff_loss),
        })

    # ─── 12) BUILD RESPONSE ─────────────────────────────────────────────────────
    return Response({
        "operating_expenditure": operating_expenditure,
        "profit_margin": profit_margin,
        "total_cost": {
            "current": current_costs["total"],
            "delta": calculate_delta(current_costs["total"], prev_costs["total"]),
            "history": total_cost_history
        },
        "revenue_billed": {
            "current": current_revenue["billed"],
            "delta": calculate_delta(current_revenue["billed"], prev_revenue["billed"]),
            "history": revenue_billed_history
        },
        "collections": {
            "current": current_revenue["collected"],
            "delta": calculate_delta(current_revenue["collected"], prev_revenue["collected"]),
            "history": collections_history
        },
        "monthly_collections_year": yearly_periods,  # Renamed but maintains structure
        "collections_by_vendor": collections_by_vendor,
        "opex_breakdown": opex_breakdown,
        "historical_costs": historical_costs,
        "historical_tariffs": historical_tariffs,
        "filter_info": {
            "mode": mode,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "level": location_info['type'],
            "transformer": transformer_slug,
            "feeder": feeder_slug,
            "district": district_name,
            "state": state_name,
            "current_energy_share": current_costs.get("energy_share", 0)
        }
    })