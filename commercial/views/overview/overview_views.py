from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from django.utils.dateparse import parse_date
from decimal import Decimal, InvalidOperation
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, FeederEnergyMonthly, FeederEnergyDaily
from common.models import Feeder, DistributionTransformer
from rest_framework.response import Response
from rest_framework.views import APIView


def get_feeder_energy_delivered(feeder, start_date, end_date=None):
    """Get energy delivered for a specific feeder using optimized queries"""
    try:
        # For monthly mode, try monthly aggregates first
        if end_date is None and isinstance(start_date, date):
            # Single month query
            delivered = FeederEnergyMonthly.objects.filter(
                feeder=feeder,
                period=start_date
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
            
            if delivered:
                return float(delivered)
            
            # Fallback to daily aggregation for the month
            month_start = start_date
            month_end = month_start + relativedelta(months=1) - timedelta(days=1)
            delivered = FeederEnergyDaily.objects.filter(
                feeder=feeder,
                date__gte=month_start,
                date__lte=month_end
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
            
            if delivered:
                return float(delivered)
        
        # For date ranges or fallback, use EnergyDelivered
        if end_date:
            delivered = EnergyDelivered.objects.filter(
                feeder=feeder,
                date__gte=start_date,
                date__lte=end_date
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        else:
            # Single date query
            delivered = EnergyDelivered.objects.filter(
                feeder=feeder,
                date__year=start_date.year,
                date__month=start_date.month
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return float(delivered or 0)
        
    except Exception:
        return 0.0


def get_commercial_overview_data(mode, year=None, month=None, week=None, from_date=None, to_date=None, feeder_slug=None, transformer_slug=None):
    # Validate feeder or transformer if provided
    feeder = None
    transformer = None
    filter_transformers = []
    
    if transformer_slug:
        # Transformer filtering takes precedence over feeder
        try:
            transformer = DistributionTransformer.objects.get(slug=transformer_slug)
            filter_transformers = [transformer]
            feeder = transformer.feeder  # Also set feeder for energy calculations
        except DistributionTransformer.DoesNotExist:
            raise ValueError(f"Transformer with slug '{transformer_slug}' not found")
    elif feeder_slug:
        try:
            feeder = Feeder.objects.get(slug=feeder_slug)
            filter_transformers = list(DistributionTransformer.objects.filter(feeder=feeder))
        except Feeder.DoesNotExist:
            raise ValueError(f"Feeder with slug '{feeder_slug}' not found")
    
    def generate_period_list(mode, reference_date, week_number=None):
        periods = []
        if mode == "yearly":
            return [date(reference_date.year - i, 1, 1) for i in range(4, -1, -1)]
        elif mode == "monthly":
            return [reference_date - relativedelta(months=i) for i in range(4, -1, -1)]
        elif mode == "weekly":
            if week_number is not None:
                # Find the first day of the year
                year_start = date(reference_date.year, 1, 1)
                # Adjust to the start of the specified week (week_number starts at 1)
                week_start = year_start + timedelta(days=(week_number - 1) * 7)
                return [week_start - timedelta(days=i * 7) for i in range(4, -1, -1)]
            return [reference_date - timedelta(days=i * 7) for i in range(4, -1, -1)]
        elif mode == "daily":
            return [reference_date - relativedelta(days=i) for i in range(4, -1, -1)]
        elif mode == "range":
            delta = relativedelta(to_date, from_date).days + 1
            return [to_date - relativedelta(days=i * delta) for i in range(4, -1, -1)]
        return periods

    # Determine reference date based on mode
    if mode == "monthly":
        if year is None or month is None:
            raise ValueError("Year and month are required for monthly mode")
        selected_date = date(year, month, 1)
    elif mode == "yearly":
        if year is None:
            raise ValueError("Year is required for yearly mode")
        selected_date = date(year, 1, 1)
    elif mode == "weekly":
        selected_date = parse_date(to_date) if to_date else date.today()
        week_number = int(week) if week is not None else selected_date.isocalendar().week
    elif mode == "daily":
        selected_date = parse_date(to_date) if to_date else date.today()
    elif mode == "range":
        to_date = parse_date(to_date) if to_date else date.today()
        from_date = parse_date(from_date) if from_date else to_date
        selected_date = to_date
    else:
        raise ValueError("Invalid mode")

    periods = generate_period_list(mode, selected_date, week_number=week)

    data = {
        "energy_delivered": [],
        "energy_billed": [],
        "energy_collected": [],
        "billing_efficiency": [],
        "collection_efficiency": [],
        "atcc": [],
        "customer_response_rate": [],
        "revenue_billed_per_customer": [],
        "collections_per_customer": [],
        "customer_response_metric": []
    }

    def append_with_delta(metric_list, value, label):
        value = Decimal(value)
        last_value = Decimal(str(metric_list[-1]["value"])) if metric_list else None

        if last_value and last_value != 0:
            delta = round(((value - last_value) / last_value) * Decimal("100"), 2)
        else:
            delta = None

        metric_list.append({
            "period": label,
            "value": float(value),
            "delta": float(delta) if delta is not None else None
        })

    for idx, period in enumerate(periods):
        if mode == "yearly":
            label = str(period.year)
            filter_kwargs = {"month__year": period.year}
            energy_delivered_filter = {"date__year": period.year}
        elif mode == "monthly":
            label = period.strftime("%b")
            filter_kwargs = {"month": period}
            energy_delivered_filter = {"date__year": period.year, "date__month": period.month}
        elif mode == "weekly":
            # Label as Week N, Week N-1, etc., based on the specified week
            week_num = week_number - idx if week_number is not None else period.isocalendar().week
            label = f"Week {week_num}"
            week_end = period + timedelta(days=6)
            filter_kwargs = {"month__gte": period, "month__lte": week_end}
            energy_delivered_filter = {"date__gte": period, "date__lte": week_end}
        elif mode == "daily":
            label = period.strftime("%b %d, %Y")
            filter_kwargs = {"month": period}
            energy_delivered_filter = {"date": period}
        else:  # range
            delta = relativedelta(to_date, from_date).days + 1
            label = f"{period.strftime('%b %d, %Y')} - {(period + relativedelta(days=delta-1)).strftime('%b %d, %Y')}"
            filter_kwargs = {"month__gte": period, "month__lte": period + relativedelta(days=delta-1)}
            energy_delivered_filter = {"date__gte": period, "date__lte": period + relativedelta(days=delta-1)}

        # Apply filtering if transformer or feeder is specified
        if filter_transformers:
            # Filter commercial summary by specific transformer(s)
            summary = MonthlyCommercialSummary.objects.filter(
                transformer__in=filter_transformers,
                **filter_kwargs
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected"),
                customers_billed=Sum("customers_billed"),
                customers_responded=Sum("customers_responded"),
            )
            
            # Filter energy billed by feeder (since energy billed is tracked at feeder level)
            energy_billed = MonthlyEnergyBilled.objects.filter(
                feeder=feeder,
                **filter_kwargs
            ).aggregate(energy=Sum("energy_mwh"))["energy"] or 0
            
            # Get energy delivered for feeder using optimized query
            if mode == "monthly":
                energy_delivered = get_feeder_energy_delivered(feeder, period)
            elif mode == "weekly" or mode == "range":
                start_date = period
                end_date = period + timedelta(days=6) if mode == "weekly" else period + relativedelta(days=delta-1)
                energy_delivered = get_feeder_energy_delivered(feeder, start_date, end_date)
            elif mode == "daily":
                energy_delivered = get_feeder_energy_delivered(feeder, period, period)
            else:  # yearly
                energy_delivered = EnergyDelivered.objects.filter(
                    feeder=feeder,
                    **energy_delivered_filter
                ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or 0
        else:
            # Global data (no feeder/transformer filter)
            summary = MonthlyCommercialSummary.objects.filter(**filter_kwargs).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected"),
                customers_billed=Sum("customers_billed"),
                customers_responded=Sum("customers_responded"),
            )
            
            energy_billed = MonthlyEnergyBilled.objects.filter(**filter_kwargs).aggregate(
                energy=Sum("energy_mwh")
            )["energy"] or 0
            
            energy_delivered = EnergyDelivered.objects.filter(**energy_delivered_filter).aggregate(
                Sum("energy_mwh")
            )["energy_mwh__sum"] or 0

        revenue_billed = summary["revenue_billed"] or 0
        revenue_collected = summary["revenue_collected"] or 0
        customers_billed = summary["customers_billed"] or 0
        customers_responded = summary["customers_responded"] or 0

        try:
            billing_eff = Decimal(energy_billed) / Decimal(energy_delivered) if energy_delivered else Decimal(0)
            collection_eff = Decimal(revenue_collected) / Decimal(revenue_billed) if revenue_billed else Decimal(0)
            energy_collected = Decimal(energy_delivered) * collection_eff if energy_delivered > 0 else Decimal("0")
            atcc = Decimal(1) - (billing_eff * collection_eff)

            # Cap efficiencies at 100% and ensure AT&C is non-negative
            billing_eff = min(billing_eff, Decimal(1))
            collection_eff = min(collection_eff, Decimal(1))
            atcc = max(atcc, Decimal(0))

            billing_eff_pct = round(billing_eff * Decimal("100"), 2)
            collection_eff_pct = round(collection_eff * Decimal("100"), 2)
            atcc_pct = round(atcc * Decimal("100"), 2)

            revenue_billed_pc = round(Decimal(revenue_billed) / Decimal(customers_billed), 2) if customers_billed else Decimal(0)
            collections_pc = round(Decimal(revenue_collected) / Decimal(customers_billed), 2) if customers_billed else Decimal(0)
            response_rate = round(Decimal(customers_responded) / Decimal(customers_billed) * Decimal("100"), 2) if customers_billed else Decimal(0)

            # Customer response metric = Collections Per Customer / Revenue Billed Per Customer
            response_metric = (
                round(collections_pc / revenue_billed_pc, 2)
                if revenue_billed_pc != 0
                else Decimal(0)
            )

        except (InvalidOperation, ZeroDivisionError):
            billing_eff_pct = collection_eff_pct = atcc_pct = Decimal(0)
            revenue_billed_pc = collections_pc = response_rate = response_metric = Decimal(0)
            energy_collected = Decimal(0)

        append_with_delta(data["energy_delivered"], energy_delivered, label)
        append_with_delta(data["energy_billed"], energy_billed, label)
        append_with_delta(data["energy_collected"], energy_collected, label)

        data["billing_efficiency"].append({"period": label, "value": float(billing_eff_pct)})
        data["collection_efficiency"].append({"period": label, "value": float(collection_eff_pct)})
        data["atcc"].append({"period": label, "value": float(atcc_pct)})

        append_with_delta(data["customer_response_rate"], response_rate, label)
        append_with_delta(data["revenue_billed_per_customer"], revenue_billed_pc, label)
        append_with_delta(data["collections_per_customer"], collections_pc, label)
        append_with_delta(data["customer_response_metric"], response_metric, label)

    return data


class CommercialOverviewAPIView(APIView):
    def get(self, request):
        # Parse query parameters
        mode = request.query_params.get('mode', 'monthly')
        year = int(request.query_params.get('year', datetime.today().year))
        month = int(request.query_params.get('month', datetime.today().month))
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')
        feeder_slug = request.query_params.get('feeder')  # Feeder filter
        transformer_slug = request.query_params.get('transformer')  # New transformer filter

        try:
            # Delegate to analytics logic
            data = get_commercial_overview_data(
                mode=mode,
                year=year,
                month=month,
                from_date=from_date,
                to_date=to_date,
                feeder_slug=feeder_slug,
                transformer_slug=transformer_slug
            )
            return Response(data)
            
        except ValueError as e:
            return Response({"error": str(e)}, status=400)