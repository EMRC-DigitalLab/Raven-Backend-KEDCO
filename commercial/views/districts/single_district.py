from decimal import Decimal
from datetime import date
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from commercial.models import *
from commercial.serializers import *
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered


class CustomerBusinessMetricsView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        state = request.GET.get("state")
        district = request.GET.get("business_district")

        # Build list of 5 months: current + 4 previous
        base_date = date(year, month, 1)
        month_list = [base_date - relativedelta(months=i) for i in reversed(range(5))]

        # Filter queryset by location
        qs = MonthlyCommercialSummary.objects.filter(month__in=month_list)

        if district:
            qs = qs.filter(sales_rep__assigned_transformers__feeder__business_district__name=district)
        elif state:
            qs = qs.filter(sales_rep__assigned_transformers__feeder__business_district__state__name=state)

        results = {
            "customer_response_rate": [],
            "customer_response_metric": [],
            "revenue_billed_per_customer": [],
            "collections_per_customer": []
        }

        for month in month_list:
            data = qs.filter(month=month).aggregate(
                total_customers_billed=Sum("customers_billed"),
                total_customers_responded=Sum("customers_responded"),
                total_revenue_billed=Sum("revenue_billed"),
                total_collections=Sum("revenue_collected")
            )

            # Calculate metrics
            billed = data["total_customers_billed"] or 0
            responded = data["total_customers_responded"] or 0
            revenue = data["total_revenue_billed"] or 0
            collected = data["total_collections"] or 0

            response_rate = round((responded / billed) * 100, 2) if billed else 0
            response_metric = round(responded / billed, 2) if billed else 0
            revenue_per_customer = round(revenue / billed / 1000, 2) if billed else 0  # in '000
            collection_per_customer = round(collected / billed / 1000, 2) if billed else 0  # in '000

            results["customer_response_rate"].append({
                "month": month.strftime("%b"),
                "value": f"{response_rate}%"
            })

            results["customer_response_metric"].append({
                "month": month.strftime("%b"),
                "value": response_metric
            })

            results["revenue_billed_per_customer"].append({
                "month": month.strftime("%b"),
                "value": revenue_per_customer
            })

            results["collections_per_customer"].append({
                "month": month.strftime("%b"),
                "value": collection_per_customer
            })



            energy_data = {
                "energy_delivered": [],
                "energy_billed": [],
                "energy_collected": [],
                "atcc": [],
                "billing_efficiency": [],
                "collection_efficiency": []
            }

            previous_values = {}

            for month in month_list:
                # Get month boundaries
                month_start = month
                next_month = month + relativedelta(months=1)

                # Filter by district or state
                if district:
                    feeder_filter = {
                        "feeder__business_district__name": district
                    }
                elif state:
                    feeder_filter = {
                        "feeder__business_district__state__name": state
                    }
                else:
                    feeder_filter = {}

                # Energy Delivered (Daily)
                ed = EnergyDelivered.objects.filter(
                    date__gte=month_start, date__lt=next_month,
                    **feeder_filter
                ).aggregate(total=Sum("energy_mwh"))["total"] or 0

                # Energy Billed (Monthly)
                eb = MonthlyEnergyBilled.objects.filter(
                    month=month,
                    **feeder_filter
                ).aggregate(total=Sum("energy_mwh"))["total"] or 0

                # Revenue Billed & Collected (Daily)

                # Get the relevant sales reps first
                rep_filter = {}
                if district:
                    rep_filter["assigned_transformers__feeder__business_district__name"] = district
                elif state:
                    rep_filter["assigned_transformers__feeder__business_district__state__name"] = state

                sales_reps = SalesRepresentative.objects.filter(**rep_filter).distinct()

                # Then filter summaries by those reps and month
                revenue_billed = MonthlyCommercialSummary.objects.filter(
                    sales_rep__in=sales_reps,
                    month=month
                ).aggregate(
                    billed=Sum("revenue_billed"),
                    collected=Sum("revenue_collected")
                )

                rb = revenue_billed["billed"] or 0
                rc = revenue_billed["collected"] or 0

            

                # Efficiency Metrics
                billing_eff = (eb / ed) * 100 if ed else 0
                collection_eff = (rc / rb) * 100 if rb else 0
                atcc = 100 - (billing_eff * collection_eff / 100) if billing_eff and collection_eff else 100
                ec = (Decimal(collection_eff) / Decimal("100")) * Decimal(eb)

                def format_metric(metric_name, value):
                    month_str = month.strftime("%b")
                    prev = previous_values.get(metric_name)
                    delta = round(((value - prev) / prev) * 100, 2) if prev and prev != 0 else None
                    previous_values[metric_name] = value
                    return {
                        "month": month_str,
                        "value": round(value, 2),
                        "delta": delta
                    }

                energy_data["energy_delivered"].append(format_metric("energy_delivered", ed))
                energy_data["energy_billed"].append(format_metric("energy_billed", eb))
                energy_data["energy_collected"].append(format_metric("energy_collected", ec))
                energy_data["billing_efficiency"].append(format_metric("billing_efficiency", billing_eff))
                energy_data["collection_efficiency"].append(format_metric("collection_efficiency", collection_eff))
                energy_data["atcc"].append(format_metric("atcc", atcc))

        
        results.update(energy_data)
        return Response(results, status=status.HTTP_200_OK)