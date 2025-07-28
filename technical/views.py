# technical/views.py
from rest_framework import viewsets
from .models import *
from .serializers import *
from commercial.mixins import FeederFilteredQuerySetMixin
from commercial.date_filters import get_date_range_from_request
from rest_framework.views import APIView
from rest_framework.response import Response
from technical.metrics import (
    get_average_hours_of_supply,
    get_average_interruption_duration,
    get_peak_load,
    get_top_or_bottom_loaded_feeders,
)
from django.db.models.functions import TruncMonth
from commercial.utils import get_filtered_feeders
from django.db.models import Avg
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from datetime import timedelta
from django.http import Http404
import pytz # type: ignore





class EnergyDeliveredViewSet(viewsets.ModelViewSet):
    serializer_class = EnergyDeliveredSerializer

    def get_queryset(self):
        feeders = get_filtered_feeders(self.request)
        date_from, date_to = get_date_range_from_request(self.request, 'date')

        qs = EnergyDelivered.objects.filter(feeder__in=feeders)

        if date_from and date_to:
            qs = qs.filter(date__range=(date_from, date_to))
        elif date_from:
            qs = qs.filter(date__gte=date_from)
        elif date_to:
            qs = qs.filter(date__lte=date_to)

        return qs


# class HourlyLoadViewSet(viewsets.ModelViewSet):
#     serializer_class = HourlyLoadSerializer

#     def get_queryset(self):
#         feeders = get_filtered_feeders(self.request)
#         date_from, date_to = get_date_range_from_request(self.request, 'date')

#         qs = HourlyLoad.objects.filter(feeder__in=feeders)

#         if date_from and date_to:
#             qs = qs.filter(date__range=(date_from, date_to))
#         elif date_from:
#             qs = qs.filter(date__gte=date_from)
#         elif date_to:
#             qs = qs.filter(date__lte=date_to)

#         return qs

class HourlyLoadViewSet(viewsets.ModelViewSet):
    serializer_class = HourlyLoadSerializer

    def get_queryset(self):
        feeders = get_filtered_feeders(self.request)
        date_from, date_to = get_date_range_from_request(self.request, 'date')

        qs = HourlyLoad.objects.filter(feeder__in=feeders)

        if date_from and date_to:
            qs = qs.filter(date__range=(date_from, date_to))
        elif date_from:
            qs = qs.filter(date__gte=date_from)
        elif date_to:
            qs = qs.filter(date__lte=date_to)

        return qs

    @action(detail=False, methods=['post'], url_path='bulk-update')
    def bulk_update(self, request):
        try:
            records = request.data.get('records', [])
            print(f"🔄 Received {len(records)} records for bulk update")
            
            if not records or not isinstance(records, list):
                return Response(
                    {"error": "Missing or invalid 'records' array"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Track operations
            inserted_count = 0
            updated_count = 0
            skipped_count = 0
            errors = []

            # Collect records for bulk operations
            records_to_update = []
            records_to_create = []
            
            # Cache feeders to avoid repeated lookups
            feeder_cache = {}

            with transaction.atomic():
                # First pass: Validate and prepare all records
                validated_records = []
                
                for i, record in enumerate(records):
                    try:
                        # Extract and validate fields
                        feeder_id = record.get('feeder')
                        date_str = record.get('date') 
                        hour = record.get('hour')
                        load_mw = record.get('load_mw')

                        if feeder_id is None or date_str is None or hour is None or load_mw is None:
                            errors.append(f"Record {i}: Missing required fields")
                            continue

                        # Get feeder (with caching)
                        if feeder_id not in feeder_cache:
                            try:
                                feeder_cache[feeder_id] = Feeder.objects.get(slug=feeder_id)
                            except Feeder.DoesNotExist:
                                try:
                                    feeder_cache[feeder_id] = Feeder.objects.get(name=feeder_id)
                                except Feeder.DoesNotExist:
                                    errors.append(f"Record {i}: Feeder '{feeder_id}' not found")
                                    continue
                        
                        feeder = feeder_cache[feeder_id]

                        # Parse date
                        try:
                            if 'T' in date_str:
                                date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00')).date()
                                date_obj = date_obj + timedelta(days=1)
                            else:
                                date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                        except ValueError:
                            errors.append(f"Record {i}: Invalid date format '{date_str}'")
                            continue

                        # Validate hour
                        if not (0 <= hour <= 23):
                            errors.append(f"Record {i}: Invalid hour {hour}")
                            continue

                        validated_records.append({
                            'feeder': feeder,
                            'date': date_obj,
                            'hour': hour,
                            'load_mw': load_mw,
                            'index': i
                        })

                    except Exception as e:
                        errors.append(f"Record {i}: Validation error - {str(e)}")

                if not validated_records:
                    return Response({
                        "success": False,
                        "errors": errors,
                        "summary": {"inserted": 0, "updated": 0, "skipped": 0}
                    })

                # Second pass: Bulk check existing records
                existing_lookup = {}
                lookup_conditions = []
                
                for vr in validated_records:
                    lookup_conditions.append(
                        Q(feeder=vr['feeder']) & Q(date=vr['date']) & Q(hour=vr['hour'])
                    )
                
                # Single query to get all existing records
                if lookup_conditions:
                    combined_q = lookup_conditions[0]
                    for condition in lookup_conditions[1:]:
                        combined_q |= condition
                    
                    existing_records = HourlyLoad.objects.filter(combined_q)
                    
                    # Build lookup map for O(1) access
                    for record in existing_records:
                        key = (record.feeder.id, record.date, record.hour)
                        existing_lookup[key] = record

                print(f"📊 Found {len(existing_lookup)} existing records to check")

                # Third pass: Prepare bulk operations
                for vr in validated_records:
                    key = (vr['feeder'].id, vr['date'], vr['hour'])
                    
                    if key in existing_lookup:
                        # Record exists - check if update needed
                        existing_record = existing_lookup[key]
                        if existing_record.load_mw != vr['load_mw']:
                            existing_record.load_mw = vr['load_mw']
                            records_to_update.append(existing_record)
                        else:
                            skipped_count += 1
                    else:
                        # Record doesn't exist - prepare for creation
                        new_record = HourlyLoad(
                            feeder=vr['feeder'],
                            date=vr['date'],
                            hour=vr['hour'],
                            load_mw=vr['load_mw']
                        )
                        records_to_create.append(new_record)

                # Execute bulk operations
                if records_to_create:
                    HourlyLoad.objects.bulk_create(records_to_create)
                    inserted_count = len(records_to_create)
                    print(f"✅ Bulk created {inserted_count} new records")

                if records_to_update:
                    HourlyLoad.objects.bulk_update(records_to_update, ['load_mw'])
                    updated_count = len(records_to_update)
                    print(f"✅ Bulk updated {updated_count} existing records")

            # Response
            response_data = {
                "success": True,
                "summary": {
                    "inserted": inserted_count,
                    "updated": updated_count,
                    "skipped": skipped_count,
                    "total_processed": inserted_count + updated_count + skipped_count,
                    "total_records_sent": len(records)
                }
            }

            if errors:
                response_data["errors"] = errors
                response_data["error_count"] = len(errors)

            print(f"🎉 Bulk operation completed: {inserted_count} inserted, {updated_count} updated, {skipped_count} skipped")
            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"💥 Bulk update error: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": "Internal server error", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class FeederInterruptionViewSet(viewsets.ModelViewSet):
    serializer_class = FeederInterruptionSerializer

    def get_queryset(self):
        feeders = get_filtered_feeders(self.request)
        
        # 🔧 Skip date filtering for the custom feeder slug action
        if hasattr(self, 'action') and self.action == 'handle_by_feeder_slug':
            return FeederInterruption.objects.filter(feeder__in=feeders)
        
        # Only apply date filtering for normal list/detail views
        try:
            date_from, date_to = get_date_range_from_request(self.request)
            qs = FeederInterruption.objects.filter(feeder__in=feeders)
            
            if date_from and date_to:
                qs = qs.filter(occurred_at__date__range=(date_from, date_to))
            elif date_from:
                qs = qs.filter(occurred_at__date__gte=date_from) 
            elif date_to:
                qs = qs.filter(occurred_at__date__lte=date_to)
                
            return qs
        except (ValueError, KeyError):
            # If date filtering fails, return all records for this feeder
            return FeederInterruption.objects.filter(feeder__in=feeders)

    def _find_interruption_by_time(self, slug, occurred_at):
        try:
            # Parse occurred_at as UTC
            occurred_at_dt = timezone.datetime.fromisoformat(occurred_at.replace('Z', ''))
            occurred_at_dt = timezone.make_aware(occurred_at_dt, timezone=pytz.UTC)
            return FeederInterruption.objects.get(
                feeder__slug=slug,
                occurred_at=occurred_at_dt
            )
        except FeederInterruption.DoesNotExist:
            raise Http404("No interruption found for the given feeder and time")

    @action(detail=False, methods=['get', 'patch', 'put', 'delete', 'post'], url_path='feeder/(?P<slug>[^/.]+)')
    def handle_by_feeder_slug(self, request, slug=None):
        if request.method == 'GET':
            occurred_at = request.GET.get('occurred_at')
            if occurred_at:
                interruption = self._find_interruption_by_time(slug, occurred_at)
            else:
                interruption = get_object_or_404(FeederInterruption, feeder__slug=slug)
            serializer = self.get_serializer(interruption)
            return Response(serializer.data)

        elif request.method in ['PATCH', 'PUT']:
            occurred_at = request.GET.get('occurred_at')
            if occurred_at:
                try:
                    interruption = self._find_interruption_by_time(slug, occurred_at)
                    print(f"✅ Found interruption for update: {interruption.id}")
                except Http404:
                    return Response({"error": "Interruption not found"}, status=status.HTTP_404_NOT_FOUND)
            else:
                interruption = get_object_or_404(FeederInterruption, feeder__slug=slug)
            
            # 🔧 CRITICAL FIX: Ensure Django knows this is an existing object
            interruption._state.adding = False
            
            # Update only the fields we can actually set
            data = request.data.copy()
            
            # Don't update occurred_at to avoid duplicate key issues
            if 'occurred_at' in data:
                del data['occurred_at']
            
            # Update fields manually (skip duration_hours - it's calculated)
            if 'description' in data:
                interruption.description = data['description']
                if 'restored_at' in data:
                    if data['restored_at']:
                        restored_dt = parse_datetime(data['restored_at'].replace('Z', ''))
                        if restored_dt and restored_dt.tzinfo is None:
                            # 🔧 FIXED: Subtract 1 hour to compensate for Django's automatic conversion
                            restored_dt = restored_dt - timezone.timedelta(hours=1)
                            restored_dt = timezone.make_aware(restored_dt, timezone=pytz.UTC)
                        interruption.restored_at = restored_dt
                    else:
                        interruption.restored_at = None
            if 'interruption_type' in data:
                interruption.interruption_type = data['interruption_type']
            
            # 🔧 REMOVED: duration_hours assignment - it's a calculated property
            # Duration will be automatically calculated based on occurred_at and restored_at
            
            # Save with force_update to ensure UPDATE operation
            try:
                interruption.save(force_update=True)
                print(f"✅ Successfully updated interruption: {interruption.id}")
            except Exception as e:
                print(f"❌ Error saving: {e}")
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            serializer = self.get_serializer(interruption)
            return Response(serializer.data)

        elif request.method == 'DELETE':
            occurred_at = request.GET.get('occurred_at')
            if occurred_at:
                interruption = self._find_interruption_by_time(slug, occurred_at)
            else:
                interruption = get_object_or_404(FeederInterruption, feeder__slug=slug)
            interruption.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        elif request.method == 'POST':
            feeder = get_object_or_404(Feeder, slug=slug)
            data = request.data.copy()
            data['feeder'] = feeder.pk
            
            # 🔧 Use get_or_create to avoid duplicates
            occurred_at = data.get('occurred_at')
            if occurred_at:
                # Parse the occurred_at properly
                occurred_at_dt = parse_datetime(occurred_at.replace('Z', ''))
                if occurred_at_dt and occurred_at_dt.tzinfo is None:
                    occurred_at_dt = timezone.make_aware(occurred_at_dt, timezone=pytz.UTC)
                
                # Try to get existing record or create new one
                interruption, created = FeederInterruption.objects.get_or_create(
                    feeder=feeder,
                    occurred_at=occurred_at_dt,
                    interruption_type=data.get('interruption_type', ''),
                    defaults={
                        'description': data.get('description', ''),
                        'restored_at': parse_datetime(data.get('restored_at', '').replace('Z', '')) if data.get('restored_at') else None,
                        'duration_hours': data.get('duration_hours', 0)
                    }
                )
                
                if not created:
                    # Update existing record
                    serializer = self.get_serializer(interruption, data=data, partial=False)
                    serializer.is_valid(raise_exception=True)
                    serializer.save()
                else:
                    # Return newly created record
                    serializer = self.get_serializer(interruption)
                
                return Response(serializer.data, status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED)
            
            # Create new record (fallback)
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _find_interruption_by_time(self, slug, occurred_at_str):
        try:
            # Parse and make timezone aware
            occurred_at_dt = parse_datetime(occurred_at_str.replace('Z', ''))
            if occurred_at_dt and occurred_at_dt.tzinfo is None:
                occurred_at_dt = timezone.make_aware(occurred_at_dt, timezone=pytz.UTC)
            
            print(f"🔍 Looking for interruption with time: {occurred_at_dt}")
            
            # 🔧 DEBUG: Check what records exist for this feeder
            all_interruptions = FeederInterruption.objects.filter(feeder__slug=slug)
            print(f"🔍 All interruptions for {slug}:")
            for intr in all_interruptions:
                print(f"   - ID: {intr.id}, Time: {intr.occurred_at}, Type: {intr.interruption_type}")
            
            # Use wider time range
            time_tolerance = timezone.timedelta(hours=2)  # 🔧 Much wider range
            candidates = FeederInterruption.objects.filter(
                feeder__slug=slug,
                occurred_at__range=(
                    occurred_at_dt - time_tolerance,
                    occurred_at_dt + time_tolerance
                )
            )
            
            print(f"🔍 Found {candidates.count()} candidates within ±2 hours")
            for candidate in candidates:
                print(f"   - Candidate: {candidate.occurred_at}")
            
            if candidates.exists():
                return candidates.first()
            else:
                raise FeederInterruption.DoesNotExist()
            
        except Exception as e:
            print(f"❌ Error in _find_interruption_by_time: {e}")
            raise Http404(f"No interruption found: {str(e)}")

    

class DailyHoursOfSupplyViewSet(viewsets.ModelViewSet):
    serializer_class = DailyHoursOfSupplySerializer

    def get_queryset(self):
        feeders = get_filtered_feeders(self.request)
        date_from, date_to = get_date_range_from_request(self.request, 'date')

        qs = DailyHoursOfSupply.objects.filter(feeder__in=feeders)

        if date_from and date_to:
            qs = qs.filter(date__range=(date_from, date_to))
        elif date_from:
            qs = qs.filter(date__gte=date_from)
        elif date_to:
            qs = qs.filter(date__lte=date_to)

        return qs


class TechnicalMetricsView(APIView):
    def get(self, request):
        top_n = int(request.GET.get('top_n', 5))
        bottom_n = int(request.GET.get('bottom_n', 5))

        data = {
            "average_hours_of_supply": round(get_average_hours_of_supply(request), 2),
            "average_interruption_duration": round(get_average_interruption_duration(request), 2),
            "peak_load": round(get_peak_load(request), 2),
            "top_loaded_feeders": get_top_or_bottom_loaded_feeders(request, top=True, limit=top_n),
            "least_loaded_feeders": get_top_or_bottom_loaded_feeders(request, top=False, limit=bottom_n)
        }
        return Response(data)



class TechnicalMonthlySummaryView(APIView):
    def get(self, request):
        feeders = get_filtered_feeders(request)
        date_from, date_to = get_date_range_from_request(request, 'date')

        supply_qs = DailyHoursOfSupply.objects.filter(feeder__in=feeders)
        if date_from and date_to:
            supply_qs = supply_qs.filter(date__range=(date_from, date_to))
        elif date_from:
            supply_qs = supply_qs.filter(date__gte=date_from)
        elif date_to:
            supply_qs = supply_qs.filter(date__lte=date_to)

        supply_monthly = supply_qs.annotate(month=TruncMonth('date')).values('month').annotate(
            avg_hours=Avg('hours_supplied')
        ).order_by('month')

        interruption_qs = FeederInterruption.objects.filter(feeder__in=feeders)
        if date_from and date_to:
            interruption_qs = interruption_qs.filter(occurred_at__date__range=(date_from, date_to))
        elif date_from:
            interruption_qs = interruption_qs.filter(occurred_at__date__gte=date_from)
        elif date_to:
            interruption_qs = interruption_qs.filter(occurred_at__date__lte=date_to)

        data = []
        for month in supply_monthly:
            month_date = month['month']
            avg_hours = month['avg_hours']

            inter_q = interruption_qs.filter(occurred_at__month=month_date.month, occurred_at__year=month_date.year)
            durations = [
                (i.restored_at - i.occurred_at).total_seconds() / 3600 for i in inter_q
            ]
            avg_interrupt = sum(durations) / len(durations) if durations else 0

            data.append({
                "month": month_date.strftime("%Y-%m"),
                "average_hours_of_supply": round(avg_hours, 2) if avg_hours else 0,
                "average_interruption_duration": round(avg_interrupt, 2),
            })

        return Response(data)


from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Sum, Count
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore

from technical.models import EnergyDelivered, HourlyLoad, FeederInterruption
from common.models import Feeder


def get_month_range(year, month):
    start = datetime(year, month, 1)
    end = start + relativedelta(months=1) - timedelta(days=1)
    return start.date(), end.date()


def delta(current, previous):
    if previous == 0:
        return 0
    return round(((current - previous) / previous) * 100, 2)


def calculate_hours_of_supply(from_date, to_date):
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date),
        load_mw__gt=0
    ).values('feeder', 'date').annotate(
        count=Count('hour')
    ).aggregate(avg=Avg('count'))['avg'] or 0
    return round(hours, 2)


def get_avg_interruption_duration(from_date, to_date):
    qs = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False
    )
    total_hours = sum(i.duration_hours for i in qs)
    count = qs.count()
    return round(total_hours / count, 2) if count else 0


@api_view(["GET"])
def technical_overview_view(request):
    year = int(request.GET.get("year", datetime.now().year))
    month = int(request.GET.get("month", datetime.now().month))
    start_date, end_date = get_month_range(year, month)
    prev_dt = datetime(year, month, 1) - relativedelta(months=1)
    prev_start, prev_end = get_month_range(prev_dt.year, prev_dt.month)

    def get_avg(model, field, from_date, to_date):
        return model.objects.filter(date__range=(from_date, to_date)).aggregate(avg=Avg(field))["avg"] or 0

    def get_sum(model, field, from_date, to_date):
        return model.objects.filter(date__range=(from_date, to_date)).aggregate(total=Sum(field))["total"] or 0

    def get_metric_with_history(calc_fn):
        history = []
        for i in range(4, 0, -1):
            dt = datetime(year, month, 1) - relativedelta(months=i)
            m_start, m_end = get_month_range(dt.year, dt.month)
            value = calc_fn(m_start, m_end)
            history.append({"month": m_start.strftime("%b"), "value": value})
        current = calc_fn(start_date, end_date)
        prev = calc_fn(prev_start, prev_end)
        return {
            "current": current,
            "delta": delta(current, prev),
            "history": history[::-1]
        }

    energy_now = get_sum(EnergyDelivered, "energy_mwh", start_date, end_date)
    energy_prev = get_sum(EnergyDelivered, "energy_mwh", prev_start, prev_end)

    load_now = get_avg(HourlyLoad, "load_mw", start_date, end_date)
    load_prev = get_avg(HourlyLoad, "load_mw", prev_start, prev_end)

    interruptions_now = FeederInterruption.objects.filter(
        occurred_at__date__range=(start_date, end_date)
    ).count()
    interruptions_prev = FeederInterruption.objects.filter(
        occurred_at__date__range=(prev_start, prev_end)
    ).count()

    supply_hours = get_metric_with_history(calculate_hours_of_supply)
    interruption_duration = get_metric_with_history(get_avg_interruption_duration)
    turnaround_time = interruption_duration  # Same as requested

    feeders_now = Feeder.objects.count()
    feeders_prev = 180  # mock
    customer_count = 5_000_000  # mock

    breakdown = {
        "feeder_count": {"value": feeders_now, "delta": delta(feeders_now, feeders_prev)},
        "avg_daily_interruptions": {"value": interruptions_now, "delta": delta(interruptions_now, interruptions_prev)},
        "avg_turnaround": {"value": turnaround_time["current"], "delta": turnaround_time["delta"]},
        "customer_count": {"value": customer_count, "delta": -5}
    }

    def interruption_breakdown_for(month_offset):
        dt = datetime(year, month, 1) - relativedelta(months=month_offset)
        m_start, m_end = get_month_range(dt.year, dt.month)
        interruptions = FeederInterruption.objects.filter(
            occurred_at__date__range=(m_start, m_end)
        )
        type_totals = {}
        for itype, _ in FeederInterruption.INTERRUPTION_TYPES:
            hours = sum(
                i.duration_hours
                for i in interruptions.filter(interruption_type=itype)
                if i.restored_at
            )
            type_totals[itype] = round(hours, 2)
        return {
            "month": m_start.strftime("%B"),
            "total": round(sum(type_totals.values()), 2),
            "delta": 2.5 + month_offset,
            "breakdown": type_totals
        }

    interruptions_data = [interruption_breakdown_for(i) for i in range(4)]

    trend_series = []
    if "date" in request.GET:
        trend_date = request.GET["date"]
        trend_qs = HourlyLoad.objects.filter(date=trend_date).values('hour').annotate(
            avg_load=Avg('load_mw')
        ).order_by('hour')
        trend_series = [{"hour": entry["hour"], "value": round(entry["avg_load"], 2)} for entry in trend_qs]

    return Response({
        "highlight_metrics": {
            "energy_delivered": {"value": float(energy_now), "delta": delta(energy_now, energy_prev)},
            "average_load": {"value": float(load_now), "delta": delta(load_now, load_prev)},
            "interruptions": {"value": interruptions_now, "delta": delta(interruptions_now, interruptions_prev)},
        },
        "supply_and_quality": {
            "supply_hours": supply_hours,
            "interruption_duration": interruption_duration,
            "turnaround_time": turnaround_time
        },
        "technical_breakdown": breakdown,
        "interruption_sources": interruptions_data,
        "load_trend": {
            "unit": "MW",
            "date": request.GET.get("date"),
            "series": trend_series
        }
    })



# technical/views.py (add to existing file)

from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib

from analytics.models import MonthlyTechnicalSummary
from common.models import State, Band, Feeder
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily
from commercial.models import Customer
from financial.models import Opex, SalaryPayment, NBETInvoice, MOInvoice
import logging

logger = logging.getLogger(__name__)


@api_view(["GET"])
def technical_service_band_summary(request):
    """
    Technical summary for all service bands with optional state filtering.
    
    Query Parameters:
    - year, month: Specific month (defaults to current month)
    - from, to: Date range (alternative to year/month)
    - state: State name for filtering (optional)
    """
    
    # Parse date parameters
    target_date = _parse_date_params(request)
    
    # Parse state filter
    state_filter = _parse_state_filter(request)
    
    # Try cache first
    cache_key = _get_band_cache_key(target_date, state_filter)
    cached_response = cache.get(cache_key)
    if cached_response:
        return Response(cached_response)
    
    # Calculate month boundaries
    month_start, month_end = _get_month_range(target_date.year, target_date.month)
    
    # Get all service bands
    bands = Band.objects.all().order_by('name')
    
    band_data = []
    
    for band in bands:
        try:
            # Try to get from summary first
            band_metrics = _get_band_metrics_from_summary(band, target_date, state_filter)
            
            if not band_metrics:
                # Fallback to real-time calculation
                band_metrics = _calculate_band_metrics_realtime(
                    band, month_start, month_end, state_filter
                )
            
            if band_metrics:  # Only include bands with data
                band_data.append({
                    "band": band.name,
                    "band_description": band.description,
                    "metrics": band_metrics
                })
                
        except Exception as e:
            logger.error(f"Error calculating metrics for band {band.name}: {str(e)}")
            continue
    
    response_data = {
        "period": f"{target_date.strftime('%Y-%m')}",
        "state_filter": state_filter.name if state_filter else None,
        "bands": band_data
    }
    
    # Cache for 10 minutes (current month) or 1 hour (historical)
    current_month = datetime.now().date().replace(day=1)
    cache_timeout = 600 if target_date >= current_month else 3600
    
    cache.set(cache_key, response_data, cache_timeout)
    
    return Response(response_data)


def _parse_date_params(request):
    """Parse date parameters and return target month date"""
    try:
        year = int(request.GET.get("year", datetime.now().year))
        month = int(request.GET.get("month", datetime.now().month))
        return datetime(year, month, 1).date()
    except (TypeError, ValueError):
        return datetime.now().date().replace(day=1)


def _parse_state_filter(request):
    """Parse and validate state filter parameter"""
    state_name = request.GET.get('state')
    if state_name:
        try:
            return State.objects.get(name__iexact=state_name)
        except State.DoesNotExist:
            pass
    return None


def _get_band_cache_key(target_date, state_filter):
    """Generate cache key for band technical summary"""
    state_str = f"_state_{state_filter.id}" if state_filter else ""
    cache_str = f"band_tech_{target_date.strftime('%Y_%m')}{state_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_month_range(year, month):
    """Get start and end dates for a given year/month"""
    start = datetime(year, month, 1).date()
    if month == 12:
        end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1).date() - timedelta(days=1)
    return start, end


def _get_band_metrics_from_summary(band, target_date, state_filter):
    """
    Try to get band metrics from pre-calculated summary data.
    Returns None if summary data is not available.
    """
    # For now, we don't have band-level summaries, so return None
    # This would be implemented when we create MonthlyBandTechnicalSummary model
    return None


def _calculate_band_metrics_realtime(band, month_start, month_end, state_filter):
    """
    Calculate band metrics in real-time when summary data is not available.
    """
    
    # Get feeders for this band with optional state filtering
    feeders_query = Feeder.objects.filter(band=band)
    if state_filter:
        feeders_query = feeders_query.filter(business_district__state=state_filter)
    
    feeders = feeders_query.select_related('business_district__state')
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    if not feeder_ids:
        return None
    
    # 1. Total Cost Calculation
    total_cost = _calculate_band_total_cost(feeder_ids, month_start, month_end, state_filter)
    
    # 2. Interruption Metrics
    interruption_metrics = _calculate_band_interruption_metrics(feeder_ids, month_start, month_end)
    
    # 3. Infrastructure Metrics
    infrastructure_metrics = _calculate_band_infrastructure_metrics(feeder_ids, month_start, month_end)
    
    return {
        "total_cost": float(total_cost),
        "duration_of_interruption": interruption_metrics['avg_duration'],
        "turnaround_time": interruption_metrics['avg_turnaround_time'],
        "feeder_tripping_rate": interruption_metrics['tripping_rate'],
        "number_of_feeders": infrastructure_metrics['feeder_count'],
        "customer_count": infrastructure_metrics['customer_count'],
        "average_peak_load": infrastructure_metrics['avg_peak_load'],
        "_source": "realtime"
    }


def _calculate_band_total_cost(feeder_ids, month_start, month_end, state_filter):
    """Calculate total cost for the band"""
    from decimal import Decimal
    
    total_cost = Decimal('0')
    
    try:
        # OPEX costs - allocate proportionally based on feeder count
        if state_filter:
            # Get district-level OPEX for the state
            districts = state_filter.districts.all()
            opex_costs = Opex.objects.filter(
                district__in=districts,
                date__range=(month_start, month_end)
            ).aggregate(
                total=Sum("credit") + Sum("debit")
            )["total"] or Decimal("0")
            
            # Get total feeders in state vs feeders in this band
            total_state_feeders = Feeder.objects.filter(
                business_district__state=state_filter
            ).count()
            
            if total_state_feeders > 0:
                band_proportion = len(feeder_ids) / total_state_feeders
                total_cost += opex_costs * Decimal(str(band_proportion))
        
        # Salary costs - allocate proportionally
        if state_filter:
            salary_costs = SalaryPayment.objects.filter(
                district__state=state_filter,
                month__year=month_start.year,
                month__month=month_start.month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            # Allocate based on feeder proportion
            total_state_feeders = Feeder.objects.filter(
                business_district__state=state_filter
            ).count()
            
            if total_state_feeders > 0:
                band_proportion = len(feeder_ids) / total_state_feeders
                total_cost += salary_costs * Decimal(str(band_proportion))
        
        # NBET and MO costs - allocate proportionally at national level
        nbet_costs = NBETInvoice.objects.filter(
            month__year=month_start.year,
            month__month=month_start.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        mo_costs = MOInvoice.objects.filter(
            month__year=month_start.year,
            month__month=month_start.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Allocate based on national feeder proportion
        total_national_feeders = Feeder.objects.count()
        if total_national_feeders > 0:
            national_proportion = len(feeder_ids) / total_national_feeders
            total_cost += (nbet_costs + mo_costs) * Decimal(str(national_proportion))
            
    except Exception as e:
        logger.error(f"Error calculating band total cost: {str(e)}")
        total_cost = Decimal('0')
    
    return total_cost


def _calculate_band_interruption_metrics(feeder_ids, month_start, month_end):
    """Calculate interruption-related metrics for the band"""
    
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(month_start, month_end)
    )
    
    total_interruptions = interruptions.count()
    
    if total_interruptions == 0:
        return {
            'avg_duration': 0.0,
            'avg_turnaround_time': 0.0,
            'tripping_rate': 0.0
        }
    
    # Calculate average duration for restored interruptions
    restored_interruptions = interruptions.filter(restored_at__isnull=False)
    
    if restored_interruptions.exists():
        total_duration = sum(
            (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            for interruption in restored_interruptions
        )
        avg_duration = total_duration / restored_interruptions.count()
    else:
        avg_duration = 0.0
    
    # Feeder tripping rate = total interruptions / number of feeders / days in month
    days_in_month = (month_end - month_start).days + 1
    feeder_count = len(feeder_ids)
    
    if feeder_count > 0:
        tripping_rate = total_interruptions / feeder_count / days_in_month
    else:
        tripping_rate = 0.0
    
    return {
        'avg_duration': round(avg_duration, 2),
        'avg_turnaround_time': round(avg_duration, 2),  # Same as duration for restoration
        'tripping_rate': round(tripping_rate, 4)
    }


def _calculate_band_infrastructure_metrics(feeder_ids, month_start, month_end):
    """Calculate infrastructure-related metrics for the band"""
    
    # Feeder count
    feeder_count = len(feeder_ids)
    
    # Customer count
    customer_count = Customer.objects.filter(
        transformer__feeder_id__in=feeder_ids
    ).count()
    
    # Average peak load
    peak_loads = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(month_start, month_end)
    ).values('feeder', 'date').annotate(
        daily_peak=Max('load_mw')
    )
    
    if peak_loads.exists():
        avg_peak_load = peak_loads.aggregate(
            avg=Avg('daily_peak')
        )['avg'] or 0.0
    else:
        avg_peak_load = 0.0
    
    return {
        'feeder_count': feeder_count,
        'customer_count': customer_count,
        'avg_peak_load': round(float(avg_peak_load), 2)
    }


from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Sum, Max
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore

from technical.models import HourlyLoad, FeederInterruption
from common.models import Feeder


def get_date_range_from_request(request):
    mode = request.GET.get("mode", "monthly")
    if mode == "range":
        try:
            from_date = datetime.strptime(request.GET["from_date"], "%Y-%m-%d").date()
            to_date = datetime.strptime(request.GET["to_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing from_date or to_date for range mode")
    else:
        try:
            year = int(request.GET["year"])
            month = int(request.GET["month"])
            from_date = datetime(year, month, 1).date()
            to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing year or month for monthly mode")

    return from_date, to_date


def calculate_avg_supply(from_date, to_date, feeder_ids):
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), load_mw__gt=0, feeder_id__in=feeder_ids
    ).values("feeder", "date").annotate(count=Count("hour")).aggregate(avg=Avg("count"))
    return round(hours["avg"] or 0, 2)


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False,
        feeder_id__in=feeder_ids
    )
    total_hours = sum(i.duration_hours for i in interruptions)
    count = interruptions.count()
    return round(total_hours / count, 2) if count else 0


# technical/views.py (replace the existing all_states_technical_summary function)

from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.core.cache import cache
from datetime import datetime, timedelta
import hashlib

from analytics.models import MonthlyTechnicalSummary
from common.models import State
from technical.models import HourlyLoad, FeederInterruption
from commercial.models import Customer


@api_view(["GET"])
def all_states_technical_summary(request):
    """
    Optimized technical summary for all states using pre-calculated data where possible.
    Falls back to real-time calculation when summary data is missing.
    """
    from_date, to_date = get_date_range_from_request(request)
    
    # Debug logging
    print(f"DEBUG: Request params: {dict(request.GET)}")
    print(f"DEBUG: Calculated date range: {from_date} to {to_date}")
    
    # Try cache first
    cache_key = _get_states_cache_key(from_date, to_date)
    print(f"DEBUG: Cache key: {cache_key}")
    
    cached_response = cache.get(cache_key)
    if cached_response:
        print("DEBUG: Returning cached response")
        return Response(cached_response)
    
    print("DEBUG: No cache found, calculating fresh data")
    
    # Get states that have feeders (exclude states with no infrastructure)
    states_with_feeders = State.objects.filter(
        districts__feeders__isnull=False
    ).distinct().order_by('name')
    
    overview = []
    
    for state in states_with_feeders:
        try:
            # Try to use summary data first
            state_metrics = _get_state_metrics_from_summary(state, from_date, to_date)
            
            if not state_metrics:
                # Fallback to real-time calculation
                state_metrics = _calculate_state_metrics_realtime(state, from_date, to_date)
            
            if state_metrics:  # Only include states with data
                overview.append({
                    "state": state.name,
                    "metrics": state_metrics
                })
                
        except Exception as e:
            # Log error but continue with other states
            print(f"Error calculating metrics for state {state.name}: {str(e)}")
            continue
    
    response_data = {"overview": overview}
    
    # Cache for 10 minutes (shorter than monthly summaries since this aggregates across months)
    cache.set(cache_key, response_data, 600)
    print(f"DEBUG: Cached response with key: {cache_key}")
    
    return Response(response_data)


def _get_states_cache_key(from_date, to_date):
    """Generate cache key for states technical summary"""
    date_str = f"{from_date}_{to_date}"
    hash_key = hashlib.md5(date_str.encode()).hexdigest()[:8]
    return f"states_technical_{hash_key}"


def _get_state_metrics_from_summary(state, from_date, to_date):
    """
    Try to get state metrics from pre-calculated summary data.
    Returns None if summary data is not available for the date range.
    """
    # Convert date range to months
    months = _get_months_in_range(from_date, to_date)
    
    # Get state-level summaries for these months
    state_summaries = MonthlyTechnicalSummary.objects.filter(
        state=state,
        business_district__isnull=True,  # State-level only
        feeder__isnull=True,
        month__in=months,
        has_complete_data=True
    )
    
    # If we don't have summaries for all months, fall back to real-time
    if state_summaries.count() != len(months):
        return None
    
    # Aggregate across months (weighted by days in month where applicable)
    total_days = (to_date - from_date).days + 1
    
    # Calculate weighted averages and totals
    total_supply_hours = state_summaries.aggregate(
        total=Sum('total_supply_hours')
    )['total'] or 0
    
    total_interruption_hours = state_summaries.aggregate(
        total=Sum('total_interruption_hours')
    )['total'] or 0
    
    total_interruptions = state_summaries.aggregate(
        total=Sum('total_interruptions')
    )['total'] or 0
    
    # Get the most recent summary for current values (feeder count, customer count, peak load)
    latest_summary = state_summaries.order_by('-month').first()
    
    if not latest_summary:
        return None
    
    # Calculate averages
    avg_supply = total_supply_hours / len(months) if len(months) > 0 else 0
    avg_duration = total_interruption_hours / total_interruptions if total_interruptions > 0 else 0
    
    # Feeder Tripping Count (FTC) - total interruptions in the period
    ftc = total_interruptions
    
    return {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround": round(float(avg_duration), 2),  # Same as duration for restoration
        "ftc": int(ftc),
        "feeder_count": latest_summary.active_feeder_count,
        "peak_load": float(latest_summary.max_peak_load),
        "customer_population": latest_summary.total_customer_count,
        "_source": "summary"
    }


def _calculate_state_metrics_realtime(state, from_date, to_date):
    """
    Calculate state metrics in real-time when summary data is not available.
    This is the corrected version of the original calculation logic.
    """
    # Get all feeders in this state
    feeders = Feeder.objects.filter(business_district__state=state)
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    if not feeder_ids:
        return None
    
    # 1. Average Supply Hours
    # Use DailyHoursOfSupply if available, otherwise calculate from HourlyLoad
    try:
        from technical.models import DailyHoursOfSupply
        daily_supply = DailyHoursOfSupply.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        )
        
        if daily_supply.exists():
            avg_supply = daily_supply.aggregate(avg=Avg('hours_supplied'))['avg'] or 0
        else:
            # Fallback: count hours with load > 0
            hourly_supply = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(from_date, to_date),
                load_mw__gt=0
            ).values('feeder', 'date').annotate(
                daily_hours=Count('hour')
            )
            avg_supply = hourly_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
            
    except ImportError:
        # DailyHoursOfSupply doesn't exist, use hourly method
        hourly_supply = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date),
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        avg_supply = hourly_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
    
    # 2. Average Interruption Duration & FTC
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(from_date, to_date)
    )
    
    # Total interruptions count (FTC - Feeder Tripping Count)
    ftc = interruptions.count()
    
    # Average duration for restored interruptions only
    restored_interruptions = interruptions.filter(restored_at__isnull=False)
    
    if restored_interruptions.exists():
        total_duration = sum(
            (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            for interruption in restored_interruptions
        )
        avg_duration = total_duration / restored_interruptions.count()
    else:
        avg_duration = 0
    
    # 3. Peak Load
    peak_load = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(from_date, to_date)
    ).aggregate(peak=Max("load_mw"))["peak"] or 0
    
    # 4. Customer Population
    customer_population = Customer.objects.filter(
        transformer__feeder_id__in=feeder_ids
    ).count()
    
    # 5. Feeder Count
    feeder_count = len(feeder_ids)
    
    return {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround": round(float(avg_duration), 2),  # Same as duration
        "ftc": ftc,
        "feeder_count": feeder_count,
        "peak_load": float(peak_load),
        "customer_population": customer_population,
        "_source": "realtime"
    }


def _get_months_in_range(from_date, to_date):
    """Get list of first-of-month dates that fall within the date range"""
    months = []
    current = from_date.replace(day=1)
    
    while current <= to_date:
        months.append(current)
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    
    return months


# Keep the original helper functions if they exist elsewhere, or define them:
def get_date_range_from_request(request):
    """
    Extract date range from request parameters with proper parameter handling.
    """
    # Check for explicit from/to dates
    from_date_str = request.GET.get('from')
    to_date_str = request.GET.get('to')
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d').date()
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d').date()
            return from_date, to_date
        except ValueError:
            pass  # Fall through to other methods
    
    # Check for year/month parameters
    year = request.GET.get('year')
    month = request.GET.get('month')
    
    if year and month:
        try:
            year = int(year)
            month = int(month)
            from_date = datetime(year, month, 1).date()
            
            # Calculate last day of the month
            if month == 12:
                to_date = datetime(year + 1, 1, 1).date() - timedelta(days=1)
            else:
                to_date = datetime(year, month + 1, 1).date() - timedelta(days=1)
            
            return from_date, to_date
        except (ValueError, TypeError):
            pass  # Fall through to default
    
    # Check for single month parameter (current year)
    if month:
        try:
            current_year = datetime.now().year
            month = int(month)
            from_date = datetime(current_year, month, 1).date()
            
            if month == 12:
                to_date = datetime(current_year + 1, 1, 1).date() - timedelta(days=1)
            else:
                to_date = datetime(current_year, month + 1, 1).date() - timedelta(days=1)
            
            return from_date, to_date
        except (ValueError, TypeError):
            pass
    
    # Check for single year parameter (entire year)
    if year:
        try:
            year = int(year)
            from_date = datetime(year, 1, 1).date()
            to_date = datetime(year, 12, 31).date()
            return from_date, to_date
        except (ValueError, TypeError):
            pass
    
    # Default to current month
    today = datetime.now().date()
    from_date = today.replace(day=1)
    if today.month == 12:
        to_date = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        to_date = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    
    return from_date, to_date


from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Sum, Count, Max
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore

from technical.models import HourlyLoad, FeederInterruption, EnergyDelivered
from common.models import Feeder


def get_month_range(year, month):
    start = datetime(year, month, 1)
    end = start + relativedelta(months=1) - timedelta(days=1)
    return start.date(), end.date()


def delta(current, previous):
    if previous == 0:
        return 0
    return round(((current - previous) / previous) * 100, 2)


def get_metric_with_history(model_fn, feeder_ids, year, month):
    history = []
    for i in range(4, 0, -1):
        dt = datetime(year, month, 1) - relativedelta(months=i)
        m_start, m_end = get_month_range(dt.year, dt.month)
        val = model_fn(m_start, m_end, feeder_ids)
        history.append(round(val, 2))

    current_start, current_end = get_month_range(year, month)
    current = model_fn(current_start, current_end, feeder_ids)
    prev = model_fn(*get_month_range((datetime(year, month, 1) - relativedelta(months=1)).year, (datetime(year, month, 1) - relativedelta(months=1)).month), feeder_ids)

    return {
        "current": round(current, 2),
        "delta": delta(current, prev),
        "history": history[::-1] + [round(current, 2)]
    }


def calculate_avg_supply(from_date, to_date, feeder_ids):
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), load_mw__gt=0, feeder_id__in=feeder_ids
    ).values("feeder", "date").annotate(count=Count("hour")).aggregate(avg=Avg("count"))
    return hours["avg"] or 0


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False,
        feeder_id__in=feeder_ids
    )
    total_hours = sum(i.duration_hours for i in interruptions)
    count = interruptions.count()
    return total_hours / count if count else 0


def calculate_interruptions(from_date, to_date, feeder_ids):
    return FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).count()


def calculate_energy_delivered(from_date, to_date, feeder_ids):
    return EnergyDelivered.objects.filter(
        date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).aggregate(total=Sum("energy_mwh"))['total'] or 0


def calculate_feeder_count(_, __, feeder_ids):
    return len(feeder_ids)


# technical/views.py (replace the existing state_technical_summary function)

from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib

from analytics.models import MonthlyTechnicalSummary
from common.models import State, Feeder
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily, FeederEnergyMonthly
from commercial.models import Customer


@api_view(["GET"])
def state_technical_summary(request):
    """
    Optimized technical summary for a specific state using pre-calculated data where possible.
    Falls back to real-time calculation when summary data is missing.
    """
    state_name = request.GET.get("state")
    if not state_name:
        return Response({"error": "State parameter is required"}, status=400)
    
    # Get state object
    state = get_object_or_404(State, name__iexact=state_name)
    
    year = int(request.GET.get("year", datetime.now().year))
    month = int(request.GET.get("month", datetime.now().month))
    day = request.GET.get("date")
    
    # Try cache first
    cache_key = _get_state_cache_key(state_name, year, month, day)
    cached_response = cache.get(cache_key)
    if cached_response:
        return Response(cached_response)
    
    # Get feeders for this state
    feeders = Feeder.objects.filter(business_district__state=state).select_related(
        'substation', 'business_district'
    )
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    if not feeder_ids:
        return Response({"error": f"No feeders found for state {state_name}"}, status=404)
    
    # Calculate month boundaries
    month_start, month_end = get_month_range(year, month)
    
    # Get top and bottom feeders (optimized)
    top_feeders, bottom_feeders = _get_top_bottom_feeders(feeder_ids, month_start, month_end)
    
    # Get load trend for specific day
    load_trend = _get_load_trend_for_day(feeder_ids, day) if day else []
    
    # Get metrics using optimized calculation
    metrics = _get_state_metrics_optimized(state, year, month, feeder_ids)
    
    response_data = {
        "state": state_name,
        "period": f"{year}-{month:02d}",
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders,
        "load_trend": {
            "date": day,
            "unit": "MW",
            "series": load_trend
        },
        "metrics": metrics
    }
    
    # Cache for 15 minutes (current month) or 1 hour (historical months)
    current_month = datetime.now().replace(day=1).date()
    target_month = datetime(year, month, 1).date()
    cache_timeout = 900 if target_month >= current_month else 3600
    
    cache.set(cache_key, response_data, cache_timeout)
    
    return Response(response_data)


def _get_state_cache_key(state_name, year, month, day=None):
    """Generate cache key for state technical summary"""
    day_str = f"_day_{day}" if day else ""
    cache_str = f"state_tech_{state_name}_{year}_{month}{day_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_top_bottom_feeders(feeder_ids, month_start, month_end):
    """Get top 5 and bottom 5 feeders by peak load - optimized query"""
    
    # Single query to get all feeder peaks
    peak_data = HourlyLoad.objects.filter(
        date__range=(month_start, month_end), 
        feeder_id__in=feeder_ids
    ).values(
        "feeder__name",
        "feeder__substation__name", 
        "feeder__voltage_level",
        "feeder_id"
    ).annotate(
        peak=Max("load_mw")
    ).order_by("-peak")
    
    # Convert to list for slicing
    peak_list = list(peak_data)
    
    if not peak_list:
        return [], []
    
    # Get top 5 and bottom 5
    top_5 = peak_list[:5]
    bottom_5 = peak_list[-5:] if len(peak_list) >= 5 else []
    
    def format_feeder_data(feeder_data):
        return [
            {
                "feeder": item["feeder__name"],
                "substation": item["feeder__substation__name"],
                "voltage_level": item["feeder__voltage_level"],
                "peak": round(float(item["peak"] or 0), 2),
                "feeder_id": item["feeder_id"]
            }
            for item in feeder_data
        ]
    
    return format_feeder_data(top_5), format_feeder_data(bottom_5)


def _get_load_trend_for_day(feeder_ids, day):
    """Get hourly load trend for a specific day - optimized"""
    if not day:
        return []
    
    try:
        # Parse date
        if isinstance(day, str):
            day_date = datetime.strptime(day, "%Y-%m-%d").date()
        else:
            day_date = day
        
        # Single optimized query
        trend_data = HourlyLoad.objects.filter(
            date=day_date, 
            feeder_id__in=feeder_ids
        ).values("hour").annotate(
            avg_load=Avg("load_mw")
        ).order_by("hour")
        
        return [
            {
                "hour": item["hour"], 
                "value": round(float(item["avg_load"] or 0), 2)
            }
            for item in trend_data
        ]
        
    except (ValueError, TypeError) as e:
        print(f"Error parsing day {day}: {str(e)}")
        return []


def _get_state_metrics_optimized(state, year, month, feeder_ids):
    """Get state metrics using summary data where possible, with history"""
    
    target_month = datetime(year, month, 1).date()
    
    # Try to get from summary first
    try:
        summary = MonthlyTechnicalSummary.objects.get(
            state=state,
            business_district__isnull=True,
            feeder__isnull=True,
            month=target_month,
            has_complete_data=True
        )
        
        # Get historical data (4 previous months) from summaries
        history_months = []
        for i in range(1, 5):
            hist_month = target_month - relativedelta(months=i)
            history_months.append(hist_month)
        
        historical_summaries = MonthlyTechnicalSummary.objects.filter(
            state=state,
            business_district__isnull=True,
            feeder__isnull=True,
            month__in=history_months,
            has_complete_data=True
        ).order_by('month')
        
        # Build metrics with history from summaries
        metrics = _build_metrics_from_summary(summary, historical_summaries, target_month)
        metrics['_source'] = 'summary'
        
        return metrics
        
    except MonthlyTechnicalSummary.DoesNotExist:
        # Fallback to real-time calculation
        return _calculate_state_metrics_realtime(state, year, month, feeder_ids)


def _build_metrics_from_summary(current_summary, historical_summaries, target_month):
    """Build metrics response from summary data"""
    
    # Create history data
    history_data = {}
    for summary in historical_summaries:
        month_name = summary.month.strftime("%b")
        history_data[summary.month] = {
            "month": month_name,
            "avg_supply": float(summary.avg_hours_of_supply),
            "avg_duration": float(summary.avg_interruption_duration),
            "turnaround_time": float(summary.avg_fault_turnaround_time),
            "interruptions": summary.total_interruptions,
            "energy_delivered": float(summary.total_energy_delivered),
            "feeder_count": summary.active_feeder_count,
        }
    
    # Sort history by month
    sorted_history = sorted(history_data.items(), key=lambda x: x[0])
    history_list = [data for _, data in sorted_history]
    
    # Calculate deltas (current vs previous month)
    previous_month = target_month - relativedelta(months=1)
    previous_data = history_data.get(previous_month, {})
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    current_metrics = {
        "avg_supply": {
            "current": float(current_summary.avg_hours_of_supply),
            "delta": calc_delta(
                float(current_summary.avg_hours_of_supply),
                previous_data.get("avg_supply", 0)
            ),
            "history": history_list
        },
        "avg_duration": {
            "current": float(current_summary.avg_interruption_duration),
            "delta": calc_delta(
                float(current_summary.avg_interruption_duration),
                previous_data.get("avg_duration", 0)
            ),
            "history": history_list
        },
        "turnaround_time": {
            "current": float(current_summary.avg_fault_turnaround_time),
            "delta": calc_delta(
                float(current_summary.avg_fault_turnaround_time),
                previous_data.get("turnaround_time", 0)
            ),
            "history": history_list
        },
        "interruptions": {
            "current": current_summary.total_interruptions,
            "delta": calc_delta(
                current_summary.total_interruptions,
                previous_data.get("interruptions", 0)
            ),
            "history": history_list
        },
        "energy_delivered": {
            "current": float(current_summary.total_energy_delivered),
            "delta": calc_delta(
                float(current_summary.total_energy_delivered),
                previous_data.get("energy_delivered", 0)
            ),
            "history": history_list
        },
        "feeder_count": {
            "current": current_summary.active_feeder_count,
            "delta": calc_delta(
                current_summary.active_feeder_count,
                previous_data.get("feeder_count", 0)
            ),
            "history": history_list
        }
    }
    
    return current_metrics


def _calculate_state_metrics_realtime(state, year, month, feeder_ids):
    """Calculate state metrics in real-time when summary data unavailable"""
    
    target_month = datetime(year, month, 1).date()
    month_start, month_end = get_month_range(year, month)
    
    # Calculate current month metrics
    current_metrics = _calculate_single_month_metrics(feeder_ids, month_start, month_end)
    
    # Calculate historical metrics (4 previous months)
    history_data = []
    for i in range(1, 5):
        hist_date = target_month - relativedelta(months=i)
        hist_start, hist_end = get_month_range(hist_date.year, hist_date.month)
        hist_metrics = _calculate_single_month_metrics(feeder_ids, hist_start, hist_end)
        
        history_data.append({
            "month": hist_date.strftime("%b"),
            **{k: v for k, v in hist_metrics.items()}
        })
    
    # Reverse to get chronological order (oldest to newest)
    history_data.reverse()
    
    # Calculate deltas (current vs previous month)
    prev_metrics = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Format with history and deltas
    metrics = {}
    for key, current_val in current_metrics.items():
        prev_val = prev_metrics.get(key, 0)
        metrics[key] = {
            "current": current_val,
            "delta": calc_delta(current_val, prev_val),
            "history": history_data
        }
    
    metrics['_source'] = 'realtime'
    return metrics


def _calculate_single_month_metrics(feeder_ids, month_start, month_end):
    """Calculate metrics for a single month"""
    
    # Average supply hours
    hourly_supply = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(month_start, month_end),
        load_mw__gt=0
    ).values('feeder', 'date').annotate(
        daily_hours=Count('hour')
    )
    avg_supply = hourly_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
    
    # Interruption metrics
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(month_start, month_end)
    )
    
    total_interruptions = interruptions.count()
    
    # Average duration for restored interruptions only
    restored_interruptions = interruptions.filter(restored_at__isnull=False)
    if restored_interruptions.exists():
        total_duration = sum(
            (int.restored_at - int.occurred_at).total_seconds() / 3600
            for int in restored_interruptions
        )
        avg_duration = total_duration / restored_interruptions.count()
    else:
        avg_duration = 0
    
    # Energy delivered
    try:
        # Try monthly aggregates first
        monthly_energy = FeederEnergyMonthly.objects.filter(
            feeder_id__in=feeder_ids,
            period=month_start
        ).aggregate(total=Sum('energy_mwh'))['total'] or 0
        
        if monthly_energy == 0:
            # Fallback to daily aggregation
            monthly_energy = FeederEnergyDaily.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(month_start, month_end)
            ).aggregate(total=Sum('energy_mwh'))['total'] or 0
            
    except Exception:
        monthly_energy = 0
    
    return {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround_time": round(float(avg_duration), 2),  # Same as duration
        "interruptions": total_interruptions,
        "energy_delivered": float(monthly_energy),
        "feeder_count": len(feeder_ids),
    }


def get_month_range(year, month):
    """Get start and end dates for a given year/month"""
    start = datetime(year, month, 1).date()
    if month == 12:
        end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1).date() - timedelta(days=1)
    return start, end



from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Max, Avg
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore

from technical.models import HourlyLoad, FeederInterruption
from common.models import Feeder


def get_date_range(request):
    mode = request.GET.get("mode", "monthly")
    if mode == "range":
        from_date = datetime.strptime(request.GET.get("from_date"), "%Y-%m-%d").date()
        to_date = datetime.strptime(request.GET.get("to_date"), "%Y-%m-%d").date()
    else:
        year = int(request.GET.get("year", datetime.today().year))
        month = int(request.GET.get("month", datetime.today().month))
        from_date = datetime(year, month, 1).date()
        to_date = (from_date + relativedelta(months=1)) - timedelta(days=1)
    return from_date, to_date

from common.models import BusinessDistrict
@api_view(["GET"])
def all_business_districts_technical_summary(request):
    state = request.GET.get("state")
    from_date, to_date = get_date_range(request)

    if not state:
        return Response({"error": "State parameter is required"}, status=400)

    # Get all business districts in the state that have feeders
    districts = BusinessDistrict.objects.filter(
        state__name__iexact=state,
        feeders__isnull=False  # Only districts that have feeders
    ).distinct()

    response_data = []

    for district in districts:
        # Get all feeders in this business district
        feeders = Feeder.objects.filter(business_district=district)
        feeder_ids = list(feeders.values_list("id", flat=True))
        
        if not feeder_ids:
            continue  # Skip districts with no feeders

        # Calculate average hours of supply
        # Method 1: Using DailyHoursOfSupply if available
        daily_supply_hours = DailyHoursOfSupply.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        ).aggregate(avg_hours=Avg("hours_supplied"))["avg_hours"]
        
        # Method 2: Fallback to HourlyLoad calculation if daily data not available
        if daily_supply_hours is None:
            hours_of_supply = HourlyLoad.objects.filter(
                date__range=(from_date, to_date),
                feeder_id__in=feeder_ids,
                load_mw__gt=0  # Only count hours where there was actual load
            ).values("feeder", "date").annotate(
                hours_count=Count("hour")
            ).aggregate(avg_hours=Avg("hours_count"))["avg_hours"] or 0
        else:
            hours_of_supply = daily_supply_hours

        # Get all interruptions in the date range
        interruptions = FeederInterruption.objects.filter(
            occurred_at__date__range=(from_date, to_date),
            feeder_id__in=feeder_ids
        )

        # Calculate duration metrics (only for restored interruptions)
        restored_interruptions = interruptions.filter(restored_at__isnull=False)
        
        if restored_interruptions.exists():
            # Calculate total duration and average
            total_duration = sum(i.duration_hours for i in restored_interruptions)
            interruption_count = restored_interruptions.count()
            avg_duration = round(total_duration / interruption_count, 2) if interruption_count else 0
        else:
            avg_duration = 0

        # Turnaround time calculation
        # This should be the average time to restore service after an interruption
        turnaround_time = avg_duration

        # Feeder Tripping Count (FTC) - Total number of interruptions/trips
        # This should be the actual count of interruptions, not a fixed value
        ftc = interruptions.count()
        
        # Alternative: FTC per feeder (normalized)
        ftc_per_feeder = round(ftc / len(feeder_ids), 2) if feeder_ids else 0

        # Get feeder count
        feeder_count = len(feeder_ids)

        # Calculate peak load for the district
        peak_load = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        ).aggregate(max_load=Max("load_mw"))["max_load"] or 0

        # Calculate customer population for this district
        customer_population = Customer.objects.filter(
            transformer__feeder__business_district=district
        ).count()

        # Calculate daily interruptions (average per day)
        date_range_days = (to_date - from_date).days + 1
        daily_interruptions = round(ftc / date_range_days, 2) if date_range_days > 0 else 0

        # Energy delivered for this district
        try:
            # Try monthly aggregates first
            energy_delivered = FeederEnergyMonthly.objects.filter(
                feeder_id__in=feeder_ids,
                period__range=(from_date.replace(day=1), to_date.replace(day=1))
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or 0
            
            if energy_delivered == 0:
                # Fallback to daily aggregation
                energy_delivered = FeederEnergyDaily.objects.filter(
                    feeder_id__in=feeder_ids,
                    date__range=(from_date, to_date)
                ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or 0
        except Exception as e:
            print(f"Error calculating energy delivered for {district.name}: {e}")
            energy_delivered = 0

        # Debug logging
        print(f"District: {district.name}")
        print(f"  Feeders: {feeder_count}")
        print(f"  Total interruptions: {ftc}")
        print(f"  Avg supply hours: {hours_of_supply}")
        print(f"  Peak load: {peak_load}")
        print("---")

        response_data.append({
            "district": district.name,
            "metrics": {
                "avg_supply": round(float(hours_of_supply), 2),
                "duration": avg_duration,
                "turnaround_time": turnaround_time,
                "ftc": ftc,  # Actual feeder tripping count
                "ftc_per_feeder": ftc_per_feeder,  # Normalized per feeder
                "daily_interruptions": daily_interruptions,
                "feeder_count": feeder_count,
                "peak_load": round(float(peak_load), 2),
                "customer_population": customer_population,
                "energy_delivered": round(float(energy_delivered), 2),
            }
        })

    return Response({"districts": response_data})




from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Count, Max
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore

from technical.models import HourlyLoad, FeederInterruption
from common.models import Feeder


def get_month_range(year, month):
    start = datetime(year, month, 1)
    end = (start + relativedelta(months=1)) - timedelta(days=1)
    return start.date(), end.date()


def delta(current, previous):
    if previous == 0:
        return 0
    return round(((current - previous) / previous) * 100, 2)


def get_metric_with_history(calc_fn, feeder_ids, year, month):
    history = []
    for i in range(4, 0, -1):
        dt = datetime(year, month, 1) - relativedelta(months=i)
        start, end = get_month_range(dt.year, dt.month)
        val = calc_fn(start, end, feeder_ids)
        history.append(round(val, 2))
        

    current_start, current_end = get_month_range(year, month)
    current = calc_fn(current_start, current_end, feeder_ids)

    prev_month = datetime(year, month, 1) - relativedelta(months=1)
    prev_start, prev_end = get_month_range(prev_month.year, prev_month.month)
    previous = calc_fn(prev_start, prev_end, feeder_ids)

    return {
        "current": round(current, 2),
        "delta": delta(current, previous),
        "history": history,
    }


def calculate_avg_supply(from_date, to_date, feeder_ids):
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), feeder_id__in=feeder_ids, load_mw__gt=0
    ).values("feeder", "date").annotate(hour_count=Count("hour")).aggregate(avg=Avg("hour_count"))
    return hours["avg"] or 0


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False,
        feeder_id__in=feeder_ids
    )
    total_duration = sum(i.duration_hours for i in interruptions)
    return total_duration / interruptions.count() if interruptions.exists() else 0


def calculate_avg_interruptions(from_date, to_date, feeder_ids):
    days = (to_date - from_date).days or 1
    total = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).count()
    return total / days


def calculate_faults(from_date, to_date, feeder_ids):
    return FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).count()


def calculate_feeder_count(_, __, feeder_ids):
    return len(feeder_ids)


@api_view(["GET"])
def business_district_technical_summary(request):
    district = request.GET.get("district")
    year = int(request.GET.get("year", datetime.now().year))
    month = int(request.GET.get("month", datetime.now().month))

    feeders = Feeder.objects.filter(business_district__name=district)
    feeder_ids = feeders.values_list("id", flat=True)

    start_date, end_date = get_month_range(year, month)

    # Top & Bottom Peak Feeders
    peak_queryset = HourlyLoad.objects.filter(
        date__range=(start_date, end_date),
        feeder_id__in=feeder_ids
    ).values(
        "feeder__name", "feeder__voltage_level"
    ).annotate(peak=Max("load_mw")).order_by("-peak")

    top_feeders = [
        {
            "feeder": obj["feeder__name"],
            "voltage_level": obj["feeder__voltage_level"],
            "peak": obj["peak"]
        } for obj in peak_queryset[:5]
    ]

    bottom_feeders = [
        {
            "feeder": obj["feeder__name"],
            "voltage_level": obj["feeder__voltage_level"],
            "peak": obj["peak"]
        } for obj in list(peak_queryset.reverse())[:5]
    ]

    return Response({
        "metrics": {
            "avg_supply": get_metric_with_history(calculate_avg_supply, feeder_ids, year, month),
            "duration": get_metric_with_history(calculate_avg_interruption_duration, feeder_ids, year, month),
            "turnaround_time": get_metric_with_history(calculate_avg_interruption_duration, feeder_ids, year, month),
            "interruptions": get_metric_with_history(calculate_avg_interruptions, feeder_ids, year, month),
            "faults": get_metric_with_history(calculate_faults, feeder_ids, year, month),
            "feeder_count": get_metric_with_history(calculate_feeder_count, feeder_ids, year, month),
        },
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders
    })


from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .metrics import get_feeder_availability_summary
from .serializers import FeederAvailabilitySerializer

class FeederAvailabilityOverview(APIView):

    def get(self, request):
        month = request.GET.get("month")
        year = request.GET.get("year")
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        state = request.GET.get("state")
        business_district = request.GET.get("business_district")

        data = get_feeder_availability_summary(
            month=month,
            year=year,
            from_date=from_date,
            to_date=to_date,
            state=state,
            business_district=business_district,
        )

        serializer = FeederAvailabilitySerializer(data, many=True)
        return Response(serializer.data)

# technical/views.py (add to existing file)

from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
import logging

from analytics.models import MonthlyTechnicalSummary
from common.models import State, Band, Feeder, DistributionTransformer
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily
from commercial.models import Customer, MonthlyCommercialSummary
from financial.models import Opex, SalaryPayment, NBETInvoice, MOInvoice

logger = logging.getLogger(__name__)


@api_view(["GET"])
def technical_service_band_summary(request):
    """
    Technical summary for all service bands with optional state filtering.
    
    Returns metrics for each service band including:
    - Total cost (NBET + MO allocation based on energy delivered)
    - Duration of interruption (average hours including ongoing outages)
    - Turnaround time (same as duration)
    - Feeder tripping count (total fault interruptions excluding load shedding/maintenance)
    - Number of feeders in the band
    - Customer count (from billing data)
    - Average peak load
    
    Query Parameters:
    - year, month: Specific month (defaults to current month)
    - from, to: Date range (alternative to year/month)
    - state: State name for filtering (optional)
    """
    
    # Parse date parameters
    target_date = _parse_date_params(request)
    
    # Parse state filter
    state_filter = _parse_state_filter(request)
    
    # Try cache first
    cache_key = _get_band_cache_key(target_date, state_filter)
    cached_response = cache.get(cache_key)
    if cached_response:
        return Response(cached_response)
    
    # Calculate month boundaries
    month_start, month_end = _get_month_range(target_date.year, target_date.month)
    
    # Get all service bands
    bands = Band.objects.all().order_by('name')
    
    band_data = []
    
    for band in bands:
        try:
            # Try to get from summary first
            band_metrics = _get_band_metrics_from_summary(band, target_date, state_filter)
            
            if not band_metrics:
                # Fallback to real-time calculation
                band_metrics = _calculate_band_metrics_realtime(
                    band, month_start, month_end, state_filter
                )
            
            # Always include the band, even with zero data
            band_data.append({
                "band": band.name,
                "band_description": band.description,
                "metrics": band_metrics or {
                    "total_cost": 0.0,
                    "duration_of_interruption": 0.0,
                    "turnaround_time": 0.0,
                    "feeder_tripping_rate": 0.0,
                    "number_of_feeders": 0,
                    "customer_count": 0,
                    "average_peak_load": 0.0,
                    "_source": "no_data"
                }
            })
                
        except Exception as e:
            logger.error(f"Error calculating metrics for band {band.name}: {str(e)}")
            # Include band with zero metrics on error
            band_data.append({
                "band": band.name,
                "band_description": band.description,
                "metrics": {
                    "total_cost": 0.0,
                    "duration_of_interruption": 0.0,
                    "turnaround_time": 0.0,
                    "feeder_tripping_count": 0,
                    "number_of_feeders": 0,
                    "customer_count": 0,
                    "average_peak_load": 0.0,
                    "_source": "error"
                }
            })
    
    response_data = {
        "period": f"{target_date.strftime('%Y-%m')}",
        "state_filter": state_filter.name if state_filter else None,
        "bands": band_data,
        "metadata": {
            "total_bands": len(band_data),
            "bands_with_data": len([b for b in band_data if b["metrics"]["_source"] not in ["no_data", "no_feeders", "error", "calculation_error"]]),
            "bands_without_data": len([b for b in band_data if b["metrics"]["_source"] in ["no_data", "no_feeders", "error", "calculation_error"]])
        }
    }
    
    # Cache for 10 minutes (current month) or 1 hour (historical)
    current_month = datetime.now().date().replace(day=1)
    cache_timeout = 600 if target_date >= current_month else 3600
    
    cache.set(cache_key, response_data, cache_timeout)
    
    return Response(response_data)


def _parse_date_params(request):
    """Parse date parameters and return target month date"""
    try:
        year = int(request.GET.get("year", datetime.now().year))
        month = int(request.GET.get("month", datetime.now().month))
        return datetime(year, month, 1).date()
    except (TypeError, ValueError):
        return datetime.now().date().replace(day=1)


def _parse_state_filter(request):
    """Parse and validate state filter parameter"""
    state_name = request.GET.get('state')
    if state_name:
        try:
            return State.objects.get(name__iexact=state_name)
        except State.DoesNotExist:
            pass
    return None


def _get_band_cache_key(target_date, state_filter):
    """Generate cache key for band technical summary"""
    state_str = f"_state_{state_filter.id}" if state_filter else ""
    cache_str = f"band_tech_{target_date.strftime('%Y_%m')}{state_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_month_range(year, month):
    """Get start and end dates for a given year/month"""
    start = datetime(year, month, 1).date()
    if month == 12:
        end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1).date() - timedelta(days=1)
    return start, end


def _get_band_metrics_from_summary(band, target_date, state_filter):
    """
    Try to get band metrics from pre-calculated summary data.
    Returns None if summary data is not available.
    """
    # For now, we don't have band-level summaries, so return None
    # This would be implemented when we create MonthlyBandTechnicalSummary model
    return None


def _calculate_band_metrics_realtime(band, month_start, month_end, state_filter):
    """
    Calculate band metrics in real-time when summary data is not available.
    Always returns metrics object, with zeros when no data available.
    """
    
    # Get feeders for this band with optional state filtering
    feeders_query = Feeder.objects.filter(band=band)
    if state_filter:
        feeders_query = feeders_query.filter(business_district__state=state_filter)
    
    feeders = feeders_query.select_related('business_district__state')
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    # Initialize default metrics (all zeros)
    default_metrics = {
        "total_cost": 0.0,
        "duration_of_interruption": 0.0,
        "turnaround_time": 0.0,
        "feeder_tripping_count": 0,
        "number_of_feeders": len(feeder_ids),  # Always show feeder count
        "customer_count": 0,
        "average_peak_load": 0.0,
        "_source": "realtime"
    }
    
    # If no feeders, return default metrics
    if not feeder_ids:
        default_metrics["_source"] = "no_feeders"
        return default_metrics
    
    try:
        # 1. Total Cost Calculation
        total_cost = _calculate_band_total_cost(feeder_ids, month_start, month_end, state_filter)
        
        # 2. Interruption Metrics
        interruption_metrics = _calculate_band_interruption_metrics(feeder_ids, month_start, month_end)
        
        # 3. Infrastructure Metrics
        infrastructure_metrics = _calculate_band_infrastructure_metrics(feeder_ids, month_start, month_end)
        
        return {
            "total_cost": float(total_cost),
            "duration_of_interruption": interruption_metrics['avg_duration'],
            "turnaround_time": interruption_metrics['avg_turnaround_time'],
            "feeder_tripping_count": interruption_metrics['tripping_count'],
            "number_of_feeders": infrastructure_metrics['feeder_count'],
            "customer_count": infrastructure_metrics['customer_count'],
            "average_peak_load": infrastructure_metrics['avg_peak_load'],
            "_source": "realtime"
        }
        
    except Exception as e:
        logger.error(f"Error in band metrics calculation: {str(e)}")
        default_metrics["_source"] = "calculation_error"
        return default_metrics


def _calculate_band_total_cost(feeder_ids, month_start, month_end, state_filter):
    """Calculate total cost for the band using energy-based allocation"""
    from decimal import Decimal
    
    # Return 0 if no feeders
    if not feeder_ids:
        return Decimal('0')
    
    try:
        # Get total energy delivered by all feeders (for calculating shares)
        if state_filter:
            # Get all feeders in the state for total energy calculation
            all_state_feeders = Feeder.objects.filter(
                business_district__state=state_filter
            ).values_list('id', flat=True)
            
            total_energy_query = FeederEnergyDaily.objects.filter(
                feeder_id__in=all_state_feeders,
                date__range=(month_start, month_end)
            )
        else:
            # National level - all feeders
            total_energy_query = FeederEnergyDaily.objects.filter(
                date__range=(month_start, month_end)
            )
        
        total_energy_delivered = total_energy_query.aggregate(
            total=Sum('energy_mwh')
        )['total'] or Decimal('0')
        
        # If no total energy data, return 0 (don't fail)
        if total_energy_delivered == 0:
            return Decimal('0')
        
        # Get energy delivered by feeders in this band
        band_energy_delivered = FeederEnergyDaily.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(month_start, month_end)
        ).aggregate(
            total=Sum('energy_mwh')
        )['total'] or Decimal('0')
        
        # Calculate band's share of total energy
        if total_energy_delivered > 0 and band_energy_delivered > 0:
            band_energy_share = band_energy_delivered / total_energy_delivered
        else:
            return Decimal('0')
        
        # Get NBET costs for the month
        nbet_costs = NBETInvoice.objects.filter(
            month__year=month_start.year,
            month__month=month_start.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Get MO costs for the month
        mo_costs = MOInvoice.objects.filter(
            month__year=month_start.year,
            month__month=month_start.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Allocate costs based on energy share
        total_cost = (nbet_costs + mo_costs) * band_energy_share
        
        return total_cost
        
    except Exception as e:
        logger.error(f"Error calculating band total cost: {str(e)}")
        return Decimal('0')


def _calculate_band_interruption_metrics(feeder_ids, month_start, month_end):
    """Calculate interruption-related metrics for the band with improved logic"""
    
    # Get all interruptions for the band's feeders
    all_interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(month_start, month_end)
    )
    
    # Filter out load shedding and maintenance for tripping rate
    load_shedding_types = ['L/S', 'L/S GS', '330KV L/S', 'T/LS']
    maintenance_types = ['MTNC', 'MTCE', '132KV MTCE', 'permit']
    excluded_types = load_shedding_types + maintenance_types
    
    fault_interruptions = all_interruptions.exclude(
        interruption_type__in=excluded_types
    )
    
    total_fault_interruptions = fault_interruptions.count()
    total_all_interruptions = all_interruptions.count()
    
    if total_all_interruptions == 0:
        return {
            'avg_duration': 0.0,
            'avg_turnaround_time': 0.0,
            'tripping_count': 0
        }
    
    # Calculate duration including ongoing interruptions
    total_duration_hours = 0.0
    interruption_count = 0
    
    period_end = datetime.combine(month_end, datetime.max.time())
    
    for interruption in all_interruptions:
        if interruption.restored_at:
            # Resolved interruption - use actual duration
            duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
        else:
            # Ongoing interruption - calculate duration to end of period
            duration = (period_end - interruption.occurred_at).total_seconds() / 3600
        
        total_duration_hours += duration
        interruption_count += 1
    
    # Calculate average duration
    avg_duration = total_duration_hours / interruption_count if interruption_count > 0 else 0.0
    
    # Feeder tripping count = total fault interruptions (excludes load shedding and maintenance)
    tripping_count = total_fault_interruptions
    
    return {
        'avg_duration': round(avg_duration, 2),
        'avg_turnaround_time': round(avg_duration, 2),  # Same as duration for restoration
        'tripping_count': tripping_count
    }


def _calculate_band_infrastructure_metrics(feeder_ids, month_start, month_end):
    """Calculate infrastructure-related metrics for the band using commercial data"""
    from commercial.models import MonthlyCommercialSummary
    
    # Feeder count
    feeder_count = len(feeder_ids)
    
    # Get customer count from commercial data (customers actually billed)
    # Get all transformers connected to feeders in this band
    from common.models import DistributionTransformer
    
    transformer_ids = DistributionTransformer.objects.filter(
        feeder_id__in=feeder_ids
    ).values_list('id', flat=True)
    
    # Get customer count from monthly commercial summary
    month_date = month_start.replace(day=1)  # Ensure it's first day of month
    
    customer_count = MonthlyCommercialSummary.objects.filter(
        transformer_id__in=transformer_ids,
        month=month_date
    ).aggregate(
        total_customers=Sum('customers_billed')
    )['total_customers'] or 0
    
    # Average peak load calculation
    peak_loads = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(month_start, month_end)
    ).values('feeder', 'date').annotate(
        daily_peak=Max('load_mw')
    )
    
    if peak_loads.exists():
        avg_peak_load = peak_loads.aggregate(
            avg=Avg('daily_peak')
        )['avg'] or 0.0
    else:
        avg_peak_load = 0.0
    
    return {
        'feeder_count': feeder_count,
        'customer_count': customer_count,
        'avg_peak_load': round(float(avg_peak_load), 2)
    }


from .metrics import get_transformer_availability_summary

class TransformerAvailabilityOverview(APIView):
    def get(self, request):
        feeder_slug = request.GET.get("feeder")
        month = request.GET.get("month")
        year = request.GET.get("year")
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        data = get_transformer_availability_summary(
            feeder_slug=feeder_slug,
            month=month,
            year=year,
            from_date=from_date,
            to_date=to_date,
        )
        return Response(data)