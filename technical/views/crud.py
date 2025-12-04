# technical/views/crud.py
from rest_framework import viewsets
from technical.models import *
from technical.serializers import *
from commercial.date_filters import get_date_range_from_request
from rest_framework.response import Response
from commercial.utils import get_filtered_feeders
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
import datetime


class EnergyDeliveredViewSet(viewsets.ModelViewSet):
    serializer_class = EnergyDeliveredSerializer
    http_method_names = ['get', 'post', 'head', 'options']  # No PUT/PATCH/DELETE

    def get_queryset(self):
        feeders = get_filtered_feeders(self.request)
        date_from, date_to = get_date_range_from_request(self.request, 'date')
        qs = EnergyDelivered.objects.filter(feeder__in=feeders)
        if date_from and date_to:
            qs = qs.filter(date__range=(date_from, date_to))
        return qs.order_by('date')

    def create(self, request, *args, **kwargs):
        return self._handle_cumulative_data(request)

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        return self._handle_cumulative_data(request, is_bulk=True)

    @transaction.atomic
    def _handle_cumulative_data(self, request, is_bulk=False):
        data = request.data
        if is_bulk:
            if not isinstance(data, list):
                return Response({"error": "Expected list of records"}, status=400)
            records = data
        else:
            records = [data]

        created = []
        updated = []
        errors = []
        feeder_cache = {}

        for i, record in enumerate(records):
            try:
                # --- Resolve feeder ---
                feeder_slug = record.get('feeder')
                if not feeder_slug:
                    errors.append(f"Record {i}: missing 'feeder'")
                    continue

                if feeder_slug not in feeder_cache:
                    try:
                        feeder = Feeder.objects.get(slug=feeder_slug)
                    except Feeder.DoesNotExist:
                        try:
                            feeder = Feeder.objects.get(name=feeder_slug)
                        except Feeder.DoesNotExist:
                            errors.append(f"Record {i}: Feeder '{feeder_slug}' not found")
                            continue
                    feeder_cache[feeder_slug] = feeder
                else:
                    feeder = feeder_cache[feeder_slug]

                # --- Parse date ---
                date_str = record.get('date')
                try:
                    reading_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    errors.append(f"Record {i}: Invalid date '{date_str}'")
                    continue

                cumulative_mwh = record.get('energy_mwh')
                if cumulative_mwh is None:
                    errors.append(f"Record {i}: missing 'energy_mwh'")
                    continue

                # --- Upsert into CumulativeMeterReading ---
                obj, was_created = CumulativeMeterReading.objects.update_or_create(
                    feeder=feeder,
                    reading_date=reading_date,
                    reading_time=None,  # or parse if provided
                    defaults={
                        'cumulative_mwh': cumulative_mwh,
                        'is_estimated': False,
                        'notes': 'Imported via legacy energy-delivered endpoint'
                    }
                )

                if was_created:
                    created.append(obj.id)
                else:
                    updated.append(obj.id)

            except Exception as e:
                errors.append(f"Record {i}: {str(e)}")

        # --- Response ---
        summary = {
            "created": len(created),
            "updated": len(updated),
            "errors": len(errors),
            "total": len(records)
        }

        response = {"summary": summary}
        if errors:
            response["errors"] = errors[:50]

        status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(response, status=status_code)


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
                print('then I come into action')
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
                                date_obj = datetime.datetime.fromisoformat(date_str.replace('Z', '+00:00')).date()
                                date_obj = date_obj + timedelta(days=1)
                            else:
                                date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
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
            occurred_at_dt = timezone.datetime.datetime.fromisoformat(occurred_at.replace('Z', ''))
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
    
            # ✅ FIX: Update fields independently
            if 'description' in data:
                interruption.description = data['description']
    
            if 'restored_at' in data:  # ✅ Moved outside description check
                if data['restored_at']:
                    restored_dt = parse_datetime(data['restored_at'].replace('Z', ''))
                    if restored_dt and restored_dt.tzinfo is None:
                        # Subtract 1 hour to compensate for Django's automatic conversion
                        restored_dt = restored_dt - timezone.timedelta(hours=1)
                        restored_dt = timezone.make_aware(restored_dt, timezone=pytz.UTC)
                    interruption.restored_at = restored_dt
                else:
                    interruption.restored_at = None
    
            if 'interruption_type' in data:
                interruption.interruption_type = data['interruption_type']
    
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
