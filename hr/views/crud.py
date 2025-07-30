from rest_framework import viewsets
from rest_framework import viewsets, status
from hr.models import Department, Role, Staff
from hr.serializers import DepartmentSerializer, RoleSerializer, StaffSerializer
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter
from rest_framework.response import Response
from django.db.models import Avg, Count
from datetime import date
from rest_framework.decorators import action
from django.db import transaction
from common.models import BusinessDistrict as District
from datetime import date
from hr.models import Staff
from datetime import date, timedelta
from django.db.models import Count, Avg
from hr.models import Staff
from commercial.date_filters import get_date_range_from_request

def get_hr_summary(request):
    state = request.GET.get('state')
    district = request.GET.get('district')

    # Time filters
    month_from, month_to = get_date_range_from_request(request, 'month')
    today = date.today()

    qs = Staff.objects.all()

    if state:
        qs = qs.filter(state__slug=state)
    if district:
        qs = qs.filter(district__slug=district)

    if month_from and month_to:
        qs = qs.filter(hire_date__lte=month_to)
    elif month_from:
        qs = qs.filter(hire_date__gte=month_from)
    elif month_to:
        qs = qs.filter(hire_date__lte=month_to)

    active_qs = qs.filter(exit_date__isnull=True)

    # Previous period: compare to same length before month_from
    previous_qs = Staff.objects.all()
    if month_from and month_to:
        delta = month_to - month_from
        prev_start = month_from - delta - timedelta(days=1)
        prev_end = month_from - timedelta(days=1)
        previous_qs = previous_qs.filter(hire_date__lte=prev_end)

    return {
        "total_staff": qs.count(),
        "active_staff": active_qs.count(),
        "avg_salary": round(qs.aggregate(avg=Avg("salary"))["avg"] or 0, 2),
        "avg_age": round(qs.aggregate(avg=Avg("birth_date"))["avg"] and (
            (today - qs.aggregate(avg=Avg("birth_date"))["avg"]).days // 365
        ) or 0),

        "staff_by_department": list(
            qs.values("department__name").annotate(count=Count("id")).order_by("-count")
        ),
        "staff_by_role": list(
            qs.values("role__title").annotate(count=Count("id")).order_by("-count")
        ),
        "gender_distribution": list(
            qs.values("gender").annotate(count=Count("id"))
        ),

        "retention_rate": round((
            active_qs.count() / qs.count() * 100 if qs.count() else 0
        ), 2),
        "turnover_rate": round((
            qs.filter(exit_date__range=(month_from, month_to)).count() / qs.count() * 100
            if month_from and month_to and qs.count() else 0
        ), 2),

        "staff_change_vs_previous": qs.count() - previous_qs.count() if month_from and month_to else None
    }



class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    filter_backends = [SearchFilter]
    search_fields = ['name', 'slug']


class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    filter_backends = [SearchFilter]
    search_fields = ['title', 'slug', 'department__name']


# class StaffViewSet(viewsets.ModelViewSet):
#     queryset = Staff.objects.all()
#     serializer_class = StaffSerializer
#     filter_backends = [DjangoFilterBackend, SearchFilter]
#     # filterset_fields = [
#     #     'department', 'role', 'state', 'district', 'gender', 'grade', 'is_active'
#     # ]
#     filterset_fields = [
#         'department', 'role', 'state', 'district', 'gender', 'grade',
#     ]
#     search_fields = ['full_name', 'email', 'phone_number']

# class StaffViewSet(viewsets.ModelViewSet):
    queryset = Staff.objects.all()
    serializer_class = StaffSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['department', 'role', 'state', 'district', 'gender', 'grade']
    search_fields = ['full_name', 'email', 'phone_number']

    def resolve_district_slug_to_uuid(self, district_slug):
        """
        Treat incoming 'district' value as the slug (e.g. 'JG-NT'),
        look it up in BusinessDistrict.slug, and return its PK.
        """
        if not district_slug:
            print("🐍 ❌ No district slug provided")
            return None

        slug = district_slug.strip()
        try:
            district = District.objects.get(slug__iexact=slug)
            return district.id
        except District.DoesNotExist:
            print(f"🐍 ❌ District slug '{slug}' not found")
            return None

    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        staff_data = request.data.get('staff', [])
        print(f"🐍 Received {len(staff_data)} staff for bulk create")
        if not staff_data:
            return Response({'error': 'No staff data provided'}, status=status.HTTP_400_BAD_REQUEST)

        created, updated, errors = [], [], []
        with transaction.atomic():
            for idx, staff_item in enumerate(staff_data):
                try:
                    slug = staff_item.get('district')
                    pk = self.resolve_district_slug_to_uuid(slug)
                    if not pk:
                        errors.append({'index': idx, 'data': staff_item,
                                       'errors': f"District slug '{slug}' could not be resolved"})
                        continue
                    staff_item['district'] = pk
                    # Normalize hire_date to YYYY-MM-DD
                    if isinstance(staff_item.get('hire_date'), str) and 'T' in staff_item['hire_date']:
                        staff_item['hire_date'] = staff_item['hire_date'].split('T')[0]

                    existing = self.get_queryset().filter(
                        district=pk,
                        full_name=staff_item.get('full_name'),
                        hire_date=staff_item.get('hire_date')
                    ).first()

                    if existing:
                        serializer = self.get_serializer(existing, data=staff_item, partial=True)
                        if serializer.is_valid():
                            serializer.save()
                            updated.append(serializer.data)
                        else:
                            errors.append({'index': idx, 'data': staff_item, 'errors': serializer.errors})
                    else:
                        serializer = self.get_serializer(data=staff_item)
                        if serializer.is_valid():
                            serializer.save()
                            created.append(serializer.data)
                        else:
                            errors.append({'index': idx, 'data': staff_item, 'errors': serializer.errors})
                except Exception as e:
                    print(f"🐍 ❌ Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': staff_item, 'errors': str(e)})

        response_data = {'created': len(created), 'updated': len(updated), 'errors': len(errors),
                         'created_data': created, 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'])
    def bulk_update(self, request):
        staff_data = request.data.get('staff', [])
        print(f"🐍 Received {len(staff_data)} staff for bulk update")
        if not staff_data:
            return Response({'error': 'No staff data provided'}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []
        with transaction.atomic():
            for idx, staff_item in enumerate(staff_data):
                try:
                    comp = staff_item.get('_composite_key', {})
                    slug = comp.get('district') or staff_item.get('district')
                    pk = self.resolve_district_slug_to_uuid(slug)
                    if not pk:
                        errors.append({'index': idx, 'data': staff_item,
                                       'errors': f"District slug '{slug}' could not be resolved"})
                        continue
                    # Normalize dates
                    if comp and isinstance(comp.get('hire_date'), str) and 'T' in comp['hire_date']:
                        comp['hire_date'] = comp['hire_date'].split('T')[0]
                    if isinstance(staff_item.get('hire_date'), str) and 'T' in staff_item['hire_date']:
                        staff_item['hire_date'] = staff_item['hire_date'].split('T')[0]

                    if comp:
                        existing = self.get_queryset().filter(
                            district=pk,
                            full_name=comp.get('full_name'),
                            hire_date=comp.get('hire_date')
                        ).first()
                        data = {**staff_item, 'district': pk}
                        data.pop('_composite_key', None)
                    else:
                        existing = self.get_queryset().filter(
                            district=pk,
                            full_name=staff_item.get('full_name'),
                            hire_date=staff_item.get('hire_date')
                        ).first()
                        data = {**staff_item, 'district': pk}

                    if not existing:
                        errors.append({'index': idx, 'data': staff_item, 'errors': 'Staff not found for update'})
                        continue

                    serializer = self.get_serializer(existing, data=data, partial=True)
                    if serializer.is_valid():
                        serializer.save()
                        updated.append(serializer.data)
                    else:
                        errors.append({'index': idx, 'data': staff_item, 'errors': serializer.errors})
                except Exception as e:
                    print(f"🐍 ❌ Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': staff_item, 'errors': str(e)})

        response_data = {'updated': len(updated), 'errors': len(errors), 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        staff_data = request.data.get('staff', [])
        print(f"🐍 Received {len(staff_data)} staff for bulk delete")
        if not staff_data:
            return Response({'error': 'No staff data provided'}, status=status.HTTP_400_BAD_REQUEST)

        deleted, errors = 0, []
        with transaction.atomic():
            for idx, staff_item in enumerate(staff_data):
                try:
                    comp = staff_item.get('_composite_key', {})
                    slug = comp.get('district') or staff_item.get('district')
                    pk = self.resolve_district_slug_to_uuid(slug)
                    if not pk:
                        errors.append({'index': idx, 'data': staff_item,
                                       'errors': f"District slug '{slug}' could not be resolved"})
                        continue
                    # Normalize dates
                    if comp and isinstance(comp.get('hire_date'), str) and 'T' in comp['hire_date']:
                        comp['hire_date'] = comp['hire_date'].split('T')[0]
                    if isinstance(staff_item.get('hire_date'), str) and 'T' in staff_item['hire_date']:
                        staff_item['hire_date'] = staff_item['hire_date'].split('T')[0]

                    if comp:
                        existing = self.get_queryset().filter(
                            district=pk,
                            full_name=comp.get('full_name'),
                            hire_date=comp.get('hire_date')
                        ).first()
                    else:
                        existing = self.get_queryset().filter(
                            district=pk,
                            full_name=staff_item.get('full_name'),
                            hire_date=staff_item.get('hire_date')
                        ).first()

                    if existing:
                        existing.delete()
                        deleted += 1
                    else:
                        errors.append({'index': idx, 'data': staff_item, 'errors': 'Staff not found for deletion'})
                except Exception as e:
                    print(f"🐍 ❌ Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': staff_item, 'errors': str(e)})

        response_data = {'deleted': deleted, 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)
class StaffViewSet(viewsets.ModelViewSet):
    queryset = Staff.objects.all()
    serializer_class = StaffSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['department', 'role', 'state', 'district', 'gender', 'grade']
    search_fields = ['full_name', 'email', 'phone_number']

    def resolve_district_slug_to_uuid(self, district_slug):
        """
        Treat incoming 'district' value as the slug (e.g. 'JG-NT'),
        look it up in BusinessDistrict.slug, and return its PK.
        """
        if not district_slug:
            print("No district slug provided")
            return None

        slug = district_slug.strip()
        try:
            district = District.objects.get(slug__iexact=slug)
            print(f"District slug '{slug}' resolved to PK: {district.id}")
            return district.id
        except District.DoesNotExist:
            print(f"District slug '{slug}' not found")
            return None

    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        staff_data = request.data.get('staff', [])
        print(f"Received {len(staff_data)} staff for bulk create")
        if not staff_data:
            return Response({'error': 'No staff data provided'}, status=status.HTTP_400_BAD_REQUEST)

        created, updated, errors = [], [], []
        with transaction.atomic():
            for idx, staff_item in enumerate(staff_data):
                try:
                    print(f"Processing item {idx}: {staff_item}")
                    
                    # Normalize hire_date to YYYY-MM-DD
                    original_hire_date = staff_item.get('hire_date')
                    if isinstance(staff_item.get('hire_date'), str) and 'T' in staff_item['hire_date']:
                        staff_item['hire_date'] = staff_item['hire_date'].split('T')[0]
                    print(f"Hire date normalized: {original_hire_date} → {staff_item.get('hire_date')}")

                    # Search by district__slug + full_name + hire_date (using district slug through FK)
                    search_criteria = {
                        'district__slug': staff_item.get('district'),
                        'full_name': staff_item.get('full_name'),
                        'hire_date': staff_item.get('hire_date')
                    }
                    print(f"Looking for existing staff with: {search_criteria}")
                    
                    existing = self.get_queryset().filter(**search_criteria).first()
                    print(f"Found existing record: {existing}")
                    
                    # Only resolve district for saving, not for searching
                    slug = staff_item.get('district')
                    pk = self.resolve_district_slug_to_uuid(slug)
                    if not pk:
                        errors.append({'index': idx, 'data': staff_item,
                                       'errors': f"District slug '{slug}' could not be resolved"})
                        continue
                    
                    if existing:
                        print(f"UPDATING existing staff ID: {existing.id}")
                        update_data = {**staff_item, 'district': pk}
                        serializer = self.get_serializer(existing, data=update_data, partial=True)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            print(f"Successfully updated staff ID: {saved_instance.id}")
                            updated.append(serializer.data)
                        else:
                            print(f"Update validation failed: {serializer.errors}")
                            errors.append({'index': idx, 'data': staff_item, 'errors': serializer.errors})
                    else:
                        print(f"CREATING new staff record")
                        create_data = {**staff_item, 'district': pk}
                        serializer = self.get_serializer(data=create_data)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            print(f"Successfully created staff ID: {saved_instance.id}")
                            created.append(serializer.data)
                        else:
                            print(f"Create validation failed: {serializer.errors}")
                            errors.append({'index': idx, 'data': staff_item, 'errors': serializer.errors})
                except Exception as e:
                    print(f"Exception at {idx}: {e}")
                    import traceback
                    print(f"Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': staff_item, 'errors': str(e)})

        print(f"Final results: Created={len(created)}, Updated={len(updated)}, Errors={len(errors)}")
        response_data = {'created': len(created), 'updated': len(updated), 'errors': len(errors),
                         'created_data': created, 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'])
    def bulk_update(self, request):
        staff_data = request.data.get('staff', [])
        print(f"Received {len(staff_data)} staff for bulk update")
        if not staff_data:
            return Response({'error': 'No staff data provided'}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []
        with transaction.atomic():
            for idx, staff_item in enumerate(staff_data):
                try:
                    print(f"Processing UPDATE item {idx}: {staff_item}")
                    
                    # Normalize dates
                    comp = staff_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('hire_date'), str) and 'T' in comp['hire_date']:
                        comp['hire_date'] = comp['hire_date'].split('T')[0]
                    if isinstance(staff_item.get('hire_date'), str) and 'T' in staff_item['hire_date']:
                        staff_item['hire_date'] = staff_item['hire_date'].split('T')[0]

                    # Search using district__slug (through FK relationship)
                    if comp:
                        search_criteria = {
                            'district__slug': comp.get('district'),
                            'full_name': comp.get('full_name'),
                            'hire_date': comp.get('hire_date')
                        }
                        print(f"UPDATE: Looking for existing staff with composite key: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()
                        
                        # Only resolve district UUID for saving
                        slug = comp.get('district') or staff_item.get('district')
                        pk = self.resolve_district_slug_to_uuid(slug)
                        if not pk:
                            errors.append({'index': idx, 'data': staff_item,
                                           'errors': f"District slug '{slug}' could not be resolved"})
                            continue
                        
                        data = {**staff_item, 'district': pk}
                        data.pop('_composite_key', None)
                    else:
                        search_criteria = {
                            'district__slug': staff_item.get('district'),
                            'full_name': staff_item.get('full_name'),
                            'hire_date': staff_item.get('hire_date')
                        }
                        print(f"UPDATE: Looking for existing staff with direct fields: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()
                        
                        # Only resolve district UUID for saving
                        slug = staff_item.get('district')
                        pk = self.resolve_district_slug_to_uuid(slug)
                        if not pk:
                            errors.append({'index': idx, 'data': staff_item,
                                           'errors': f"District slug '{slug}' could not be resolved"})
                            continue
                        
                        data = {**staff_item, 'district': pk}

                    print(f"UPDATE: Found existing record: {existing}")

                    if not existing:
                        print(f"UPDATE: Staff not found for update")
                        errors.append({'index': idx, 'data': staff_item, 'errors': 'Staff not found for update'})
                        continue

                    serializer = self.get_serializer(existing, data=data, partial=True)
                    if serializer.is_valid():
                        saved_instance = serializer.save()
                        print(f"UPDATE: Successfully updated staff ID: {saved_instance.id}")
                        updated.append(serializer.data)
                    else:
                        print(f"UPDATE: Validation failed: {serializer.errors}")
                        errors.append({'index': idx, 'data': staff_item, 'errors': serializer.errors})
                except Exception as e:
                    print(f"UPDATE Exception at {idx}: {e}")
                    import traceback
                    print(f"UPDATE Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': staff_item, 'errors': str(e)})

        print(f"UPDATE Final results: Updated={len(updated)}, Errors={len(errors)}")
        response_data = {'updated': len(updated), 'errors': len(errors), 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        staff_data = request.data.get('staff', [])
        print(f"Received {len(staff_data)} staff for bulk delete")
        if not staff_data:
            return Response({'error': 'No staff data provided'}, status=status.HTTP_400_BAD_REQUEST)

        deleted, errors = 0, []
        with transaction.atomic():
            for idx, staff_item in enumerate(staff_data):
                try:
                    print(f"Processing DELETE item {idx}: {staff_item}")
                    
                    # Normalize dates
                    comp = staff_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('hire_date'), str) and 'T' in comp['hire_date']:
                        comp['hire_date'] = comp['hire_date'].split('T')[0]
                    if isinstance(staff_item.get('hire_date'), str) and 'T' in staff_item['hire_date']:
                        staff_item['hire_date'] = staff_item['hire_date'].split('T')[0]

                    # Search using district__slug (through FK relationship)
                    if comp:
                        search_criteria = {
                            'district__slug': comp.get('district'),
                            'full_name': comp.get('full_name'),
                            'hire_date': comp.get('hire_date')
                        }
                        print(f"DELETE: Looking for existing staff with composite key: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()
                    else:
                        search_criteria = {
                            'district__slug': staff_item.get('district'),
                            'full_name': staff_item.get('full_name'),
                            'hire_date': staff_item.get('hire_date')
                        }
                        print(f"DELETE: Looking for existing staff with direct fields: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()

                    print(f"DELETE: Found existing record: {existing}")

                    if existing:
                        existing.delete()
                        print(f"DELETE: Successfully deleted staff")
                        deleted += 1
                    else:
                        print(f"DELETE: Staff not found for deletion")
                        errors.append({'index': idx, 'data': staff_item, 'errors': 'Staff not found for deletion'})
                except Exception as e:
                    print(f"DELETE Exception at {idx}: {e}")
                    import traceback
                    print(f"DELETE Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': staff_item, 'errors': str(e)})

        print(f"DELETE Final results: Deleted={deleted}, Errors={len(errors)}")
        response_data = {'deleted': deleted, 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)