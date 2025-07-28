# financial/views.py
import random
from random import randint
from datetime import date, datetime, timedelta
from calendar import monthrange
from decimal import Decimal
from dateutil.relativedelta import relativedelta  # type: ignore

from django.db.models import (
    Sum, Q, Count
)
from django.utils.timezone import now

from django_filters.rest_framework import DjangoFilterBackend

from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework.decorators import action

from .models import *
from .serializers import *
from .metrics import get_financial_feeder_data

from common.mixins import DistrictLocationFilterMixin
from common.models import (
    Feeder, State, BusinessDistrict, Band, DistributionTransformer
)

from commercial.models import (
    MonthlyCommercialSummary,
    DailyEnergyDelivered,
    SalesRepresentative,
    DailyCollection
)
from commercial.serializers import SalesRepresentativeSerializer
from commercial.utils import get_filtered_feeders
from commercial.date_filters import get_date_range_from_request
from commercial.metrics import get_total_collections

from financial.models import Opex
from financial.metrics import (
    get_total_cost,
    get_total_revenue_billed,
    get_opex_breakdown,
    get_tariff_loss
)


from django.db.models import Sum
from django.utils.timezone import now
from rest_framework.decorators import api_view
from rest_framework.response import Response
from datetime import date
from dateutil.relativedelta import relativedelta # type: ignore
from commercial.models import MonthlyCommercialSummary, DailyEnergyDelivered
from django.db.models import Q
from .metrics import get_financial_feeder_data
from commercial.models import SalesRepresentative
from django.db.models import Count
from datetime import datetime, timedelta
from rest_framework import status
from commercial.serializers import SalesRepresentativeSerializer
from rest_framework.decorators import action
from django.db import transaction
from common.models import BusinessDistrict as District

from technical.models import (
    FeederEnergyMonthly,
    FeederEnergyDaily,
    EnergyDelivered
)



class OpexCategoryViewSet(viewsets.ModelViewSet):
    queryset = OpexCategory.objects.all()
    serializer_class = OpexCategorySerializer


# class OpexViewSet(DistrictLocationFilterMixin, viewsets.ModelViewSet):
#     # queryset = Expense.objects.all()
#     serializer_class = OpexSerializer
#     filter_backends = [DjangoFilterBackend]
#     filterset_fields = {'district', 'gl_breakdown', 'opex_category', 'date'}

#     def get_queryset(self):
#         qs = Opex.objects.all()
#         return self.filter_by_location(qs)

class OpexViewSet(DistrictLocationFilterMixin, viewsets.ModelViewSet):
    queryset = Opex.objects.all()
    serializer_class = OpexSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = {'district', 'gl_breakdown', 'opex_category', 'date'}

    def get_queryset(self):
        qs = Opex.objects.all()
        return self.filter_by_location(qs)
    
    @action(detail=False, methods=['post'], url_path='upsert-external')
    def upsert_external(self, request):
        external_id = request.data.get("external_id")
        if not external_id:
            return Response({"error": "external_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        instance = Opex.objects.filter(external_id=external_id).first()
        serializer = self.get_serializer(instance, data=request.data, partial=bool(instance))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK if instance else status.HTTP_201_CREATED)

    def resolve_district_slug_to_uuid(self, district_slug):
        """
        Treat incoming 'district' value as the slug (e.g. 'JG-NT'),
        look it up in District.slug, and return its PK.
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

    def resolve_gl_breakdown_name_to_uuid(self, gl_breakdown_name):
        """
        Convert GL breakdown name to UUID, creating if it doesn't exist.
        """
        if not gl_breakdown_name or gl_breakdown_name.strip() == '':
            return None
            
        name = gl_breakdown_name.strip()
        try:
            gl_breakdown = GLBreakdown.objects.get(name__iexact=name)
            print(f"GL breakdown '{name}' resolved to PK: {gl_breakdown.id}")
            return gl_breakdown.id
        except GLBreakdown.DoesNotExist:
            # Create new GL breakdown if it doesn't exist
            try:
                gl_breakdown = GLBreakdown.objects.create(name=name)
                print(f"Created new GL breakdown '{name}' with PK: {gl_breakdown.id}")
                return gl_breakdown.id
            except Exception as e:
                print(f"Failed to create GL breakdown '{name}': {e}")
                return None

    def resolve_opex_category_name_to_uuid(self, opex_category_name):
        """
        Convert OPEX category name to UUID, creating if it doesn't exist.
        """
        if not opex_category_name or opex_category_name.strip() == '':
            return None
            
        name = opex_category_name.strip()
        try:
            opex_category = OpexCategory.objects.get(name__iexact=name)
            print(f"OPEX category '{name}' resolved to PK: {opex_category.id}")
            return opex_category.id
        except OpexCategory.DoesNotExist:
            # Create new OPEX category if it doesn't exist
            try:
                opex_category = OpexCategory.objects.create(name=name)
                print(f"Created new OPEX category '{name}' with PK: {opex_category.id}")
                return opex_category.id
            except Exception as e:
                print(f"Failed to create OPEX category '{name}': {e}")
                return None

    def resolve_foreign_keys(self, opex_item):
        """
        Resolve all foreign key references from names to UUIDs.
        """
        resolved_data = opex_item.copy()
        
        # Resolve district
        slug = opex_item.get('district')
        district_pk = self.resolve_district_slug_to_uuid(slug)
        if not district_pk:
            raise ValueError(f"District slug '{slug}' could not be resolved")
        resolved_data['district'] = district_pk
        
        # Resolve GL breakdown
        gl_breakdown_name = opex_item.get('gl_breakdown')
        if gl_breakdown_name and gl_breakdown_name not in ['N/A', '']:
            gl_breakdown_pk = self.resolve_gl_breakdown_name_to_uuid(gl_breakdown_name)
            resolved_data['gl_breakdown'] = gl_breakdown_pk
        else:
            resolved_data['gl_breakdown'] = None
            
        # Resolve OPEX category
        opex_category_name = opex_item.get('opex_category')
        if opex_category_name and opex_category_name not in ['N/A', 'General', '']:
            opex_category_pk = self.resolve_opex_category_name_to_uuid(opex_category_name)
            resolved_data['opex_category'] = opex_category_pk
        else:
            # Create or get "General" category as default
            opex_category_pk = self.resolve_opex_category_name_to_uuid('General')
            resolved_data['opex_category'] = opex_category_pk
        
        return resolved_data

    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        opex_data = request.data.get('expenses', [])
        print(f"Received {len(opex_data)} expenses for bulk create")
        if not opex_data:
            return Response({'error': 'No expense data provided'}, status=status.HTTP_400_BAD_REQUEST)

        created, updated, errors = [], [], []
        with transaction.atomic():
            for idx, opex_item in enumerate(opex_data):
                try:
                    print(f"Processing expense item {idx}: {opex_item}")
                    
                    # Normalize date to YYYY-MM-DD
                    original_date = opex_item.get('date')
                    if isinstance(opex_item.get('date'), str) and 'T' in opex_item['date']:
                        opex_item['date'] = opex_item['date'].split('T')[0]
                    print(f"Date normalized: {original_date} → {opex_item.get('date')}")

                    # Smart search strategy:
                    # 1. First try transaction_id + district + date (MOST SPECIFIC)
                    # 2. Fall back to composite key search (backward compatibility)
                    existing = None
                    
                    transaction_id = opex_item.get('transaction_id')
                    if transaction_id:
                        # Search by transaction_id + district + date for precision
                        precise_search = {
                            'transaction_id': transaction_id,
                            'district__slug': opex_item.get('district'),
                            'date': opex_item.get('date')
                        }
                        print(f"Searching by transaction_id + district + date: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                        print(f"Found by precise search: {existing}")
                    
                    # If not found by precise search, try composite key search
                    if not existing:
                        search_criteria = {
                            'district__slug': opex_item.get('district'),
                            'date': opex_item.get('date'),
                            'purpose': opex_item.get('purpose', ''),
                            'payee': opex_item.get('payee', '')
                        }
                        print(f"Searching by composite key: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()
                        print(f"Found by composite key: {existing}")
                    
                    print(f"Final existing record: {existing}")
                    
                    # Resolve all foreign key references
                    try:
                        resolved_data = self.resolve_foreign_keys(opex_item)
                        print(f"Resolved foreign keys: district={resolved_data.get('district')}, "
                              f"gl_breakdown={resolved_data.get('gl_breakdown')}, "
                              f"opex_category={resolved_data.get('opex_category')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': opex_item, 'errors': str(e)})
                        continue
                    
                    if existing:
                        print(f"UPDATING existing expense ID: {existing.id}")
                        serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            print(f"Successfully updated expense ID: {saved_instance.id}")
                            updated.append(serializer.data)
                        else:
                            print(f"Update validation failed: {serializer.errors}")
                            errors.append({'index': idx, 'data': opex_item, 'errors': serializer.errors})
                    else:
                        print(f"CREATING new expense record")
                        serializer = self.get_serializer(data=resolved_data)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            print(f"Successfully created expense ID: {saved_instance.id}")
                            created.append(serializer.data)
                        else:
                            print(f"Create validation failed: {serializer.errors}")
                            errors.append({'index': idx, 'data': opex_item, 'errors': serializer.errors})
                except Exception as e:
                    print(f"Exception at {idx}: {e}")
                    import traceback
                    print(f"Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': opex_item, 'errors': str(e)})

        print(f"Final results: Created={len(created)}, Updated={len(updated)}, Errors={len(errors)}")
        response_data = {'created': len(created), 'updated': len(updated), 'errors': len(errors),
                         'created_data': created, 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'])
    def bulk_update(self, request):
        opex_data = request.data.get('expenses', [])
        print(f"Received {len(opex_data)} expenses for bulk update")
        if not opex_data:
            return Response({'error': 'No expense data provided'}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []
        with transaction.atomic():
            for idx, opex_item in enumerate(opex_data):
                try:
                    print(f"Processing UPDATE expense item {idx}: {opex_item}")
                    
                    # Normalize dates
                    comp = opex_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('date'), str) and 'T' in comp['date']:
                        comp['date'] = comp['date'].split('T')[0]
                    if isinstance(opex_item.get('date'), str) and 'T' in opex_item['date']:
                        opex_item['date'] = opex_item['date'].split('T')[0]

                    # Smart search for UPDATE:
                    # 1. First try transaction_id + district + date (MOST SPECIFIC)
                    # 2. Fall back to composite key search (backward compatibility)
                    existing = None
                    
                    if comp:
                        data_to_resolve = {**opex_item}
                        data_to_resolve.update(comp)
                        data_to_resolve.pop('_composite_key', None)
                    else:
                        data_to_resolve = opex_item
                    
                    transaction_id = data_to_resolve.get('transaction_id')
                    if transaction_id:
                        # Search by transaction_id + district + date for precision
                        precise_search = {
                            'transaction_id': transaction_id,
                            'district__slug': data_to_resolve.get('district'),
                            'date': data_to_resolve.get('date')
                        }
                        print(f"UPDATE: Searching by transaction_id + district + date: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                        print(f"UPDATE: Found by precise search: {existing}")
                    
                    # If not found by precise search, try composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'district__slug': comp.get('district'),
                                'date': comp.get('date'),
                                'purpose': comp.get('purpose', ''),
                                'payee': comp.get('payee', '')
                            }
                            print(f"UPDATE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'district__slug': opex_item.get('district'),
                                'date': opex_item.get('date'),
                                'purpose': opex_item.get('purpose', ''),
                                'payee': opex_item.get('payee', '')
                            }
                            print(f"UPDATE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()
                        print(f"UPDATE: Found by composite key: {existing}")

                    if not existing:
                        print(f"UPDATE: Expense not found for update")
                        errors.append({'index': idx, 'data': opex_item, 'errors': 'Expense not found for update'})
                        continue

                    # Resolve all foreign key references
                    try:
                        resolved_data = self.resolve_foreign_keys(data_to_resolve)
                        print(f"UPDATE: Resolved foreign keys: district={resolved_data.get('district')}, "
                              f"gl_breakdown={resolved_data.get('gl_breakdown')}, "
                              f"opex_category={resolved_data.get('opex_category')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': opex_item, 'errors': str(e)})
                        continue

                    serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                    if serializer.is_valid():
                        saved_instance = serializer.save()
                        print(f"UPDATE: Successfully updated expense ID: {saved_instance.id}")
                        updated.append(serializer.data)
                    else:
                        print(f"UPDATE: Validation failed: {serializer.errors}")
                        errors.append({'index': idx, 'data': opex_item, 'errors': serializer.errors})
                except Exception as e:
                    print(f"UPDATE Exception at {idx}: {e}")
                    import traceback
                    print(f"UPDATE Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': opex_item, 'errors': str(e)})

        print(f"UPDATE Final results: Updated={len(updated)}, Errors={len(errors)}")
        response_data = {'updated': len(updated), 'errors': len(errors), 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        opex_data = request.data.get('expenses', [])
        print(f"Received {len(opex_data)} expenses for bulk delete")
        if not opex_data:
            return Response({'error': 'No expense data provided'}, status=status.HTTP_400_BAD_REQUEST)

        deleted, errors = 0, []
        with transaction.atomic():
            for idx, opex_item in enumerate(opex_data):
                try:
                    print(f"Processing DELETE expense item {idx}: {opex_item}")
                    
                    # Normalize dates
                    comp = opex_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('date'), str) and 'T' in comp['date']:
                        comp['date'] = comp['date'].split('T')[0]
                    if isinstance(opex_item.get('date'), str) and 'T' in opex_item['date']:
                        opex_item['date'] = opex_item['date'].split('T')[0]

                    # Smart search for DELETE:
                    # 1. First try transaction_id if available (BEST)
                    # 2. Fall back to composite key search (backward compatibility)
                    existing = None
                    
                    if comp:
                        search_data = comp
                    else:
                        search_data = opex_item
                    
                    transaction_id = search_data.get('transaction_id')
                    if transaction_id:
                        print(f"DELETE: Searching by transaction_id: {transaction_id}")
                        existing = self.get_queryset().filter(transaction_id=transaction_id).first()
                        print(f"DELETE: Found by transaction_id: {existing}")
                    
                    # If not found by transaction_id, try composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'district__slug': comp.get('district'),
                                'date': comp.get('date'),
                                'purpose': comp.get('purpose', ''),
                                'payee': comp.get('payee', '')
                            }
                            print(f"DELETE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'district__slug': opex_item.get('district'),
                                'date': opex_item.get('date'),
                                'purpose': opex_item.get('purpose', ''),
                                'payee': opex_item.get('payee', '')
                            }
                            print(f"DELETE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()
                        print(f"DELETE: Found by composite key: {existing}")

                    if existing:
                        existing.delete()
                        print(f"DELETE: Successfully deleted expense")
                        deleted += 1
                    else:
                        print(f"DELETE: Expense not found for deletion")
                        errors.append({'index': idx, 'data': opex_item, 'errors': 'Expense not found for deletion'})
                except Exception as e:
                    print(f"DELETE Exception at {idx}: {e}")
                    import traceback
                    print(f"DELETE Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': opex_item, 'errors': str(e)})

        print(f"DELETE Final results: Deleted={deleted}, Errors={len(errors)}")
        response_data = {'deleted': deleted, 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)
    
# class HQOpexViewSet(viewsets.ModelViewSet):
#     serializer_class = HQOpexSerializer
#     queryset = HQOpex.objects.all()
#     filter_backends = [DjangoFilterBackend]
#     filterset_fields = ['gl_breakdown', 'opex_category', 'date']

#     @action(detail=False, methods=['post'], url_path='upsert-external')
#     def upsert_external(self, request):
#         external_id = request.data.get("external_id")
#         if not external_id:
#             return Response({"error": "external_id is required"}, status=status.HTTP_400_BAD_REQUEST)

#         instance = HQOpex.objects.filter(external_id=external_id).first()
#         serializer = self.get_serializer(instance, data=request.data, partial=bool(instance))
#         serializer.is_valid(raise_exception=True)
#         serializer.save()
#         return Response(serializer.data, status=status.HTTP_200_OK if instance else status.HTTP_201_CREATED)

class HQOpexViewSet(viewsets.ModelViewSet):
    serializer_class = HQOpexSerializer
    queryset = HQOpex.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['gl_breakdown', 'opex_category', 'date']

    def resolve_gl_breakdown_name_to_uuid(self, gl_breakdown_name):
        """
        Convert GL breakdown name to UUID, creating if it doesn't exist.
        """
        if not gl_breakdown_name or gl_breakdown_name.strip() == '':
            return None
            
        name = gl_breakdown_name.strip()
        try:
            gl_breakdown = GLBreakdown.objects.get(name__iexact=name)
            print(f"HQ GL breakdown '{name}' resolved to PK: {gl_breakdown.id}")
            return gl_breakdown.id
        except GLBreakdown.DoesNotExist:
            # Create new GL breakdown if it doesn't exist
            try:
                gl_breakdown = GLBreakdown.objects.create(name=name)
                print(f"Created new HQ GL breakdown '{name}' with PK: {gl_breakdown.id}")
                return gl_breakdown.id
            except Exception as e:
                print(f"Failed to create HQ GL breakdown '{name}': {e}")
                return None

    def resolve_opex_category_name_to_uuid(self, opex_category_name):
        """
        Convert OPEX category name to UUID, creating if it doesn't exist.
        """
        if not opex_category_name or opex_category_name.strip() == '':
            return None
            
        name = opex_category_name.strip()
        try:
            opex_category = OpexCategory.objects.get(name__iexact=name)
            print(f"HQ OPEX category '{name}' resolved to PK: {opex_category.id}")
            return opex_category.id
        except OpexCategory.DoesNotExist:
            # Create new OPEX category if it doesn't exist
            try:
                opex_category = OpexCategory.objects.create(name=name)
                print(f"Created new HQ OPEX category '{name}' with PK: {opex_category.id}")
                return opex_category.id
            except Exception as e:
                print(f"Failed to create HQ OPEX category '{name}': {e}")
                return None

    def resolve_hq_id_to_uuid(self, hq_id):
        """
        Convert HQ ID (like 'KN-HQ') to district UUID.
        """
        if not hq_id:
            print("No HQ ID provided")
            return None

        hq_slug = hq_id.strip()
        try:
            district = District.objects.get(slug__iexact=hq_slug)
            print(f"HQ ID '{hq_slug}' resolved to district PK: {district.id}")
            return district.id
        except District.DoesNotExist:
            print(f"HQ ID '{hq_slug}' not found in districts")
            return None

    def resolve_foreign_keys(self, hq_opex_item):
        """
        Resolve all foreign key references from names to UUIDs for HQ OPEX.
        """
        resolved_data = hq_opex_item.copy()
        
        # Resolve HQ ID to district
        hq_id = hq_opex_item.get('hq_id')
        if hq_id:
            district_pk = self.resolve_hq_id_to_uuid(hq_id)
            if not district_pk:
                raise ValueError(f"HQ ID '{hq_id}' could not be resolved")
            resolved_data['district'] = district_pk
        else:
            # Default to None if no hq_id provided
            resolved_data['district'] = None
        
        # Remove hq_id from final data since model expects district
        resolved_data.pop('hq_id', None)
        
        # Resolve GL breakdown
        gl_breakdown_name = hq_opex_item.get('gl_breakdown')
        if gl_breakdown_name and gl_breakdown_name not in ['N/A', '']:
            gl_breakdown_pk = self.resolve_gl_breakdown_name_to_uuid(gl_breakdown_name)
            resolved_data['gl_breakdown'] = gl_breakdown_pk
        else:
            resolved_data['gl_breakdown'] = None
            
        # Resolve OPEX category
        opex_category_name = hq_opex_item.get('opex_category')
        if opex_category_name and opex_category_name not in ['N/A', 'General', '']:
            opex_category_pk = self.resolve_opex_category_name_to_uuid(opex_category_name)
            resolved_data['opex_category'] = opex_category_pk
        else:
            # Create or get "General" category as default
            opex_category_pk = self.resolve_opex_category_name_to_uuid('General')
            resolved_data['opex_category'] = opex_category_pk
        
        return resolved_data

    @action(detail=False, methods=['post'], url_path='upsert-external')
    def upsert_external(self, request):
        external_id = request.data.get("external_id")
        if not external_id:
            return Response({"error": "external_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        instance = HQOpex.objects.filter(external_id=external_id).first()
        serializer = self.get_serializer(instance, data=request.data, partial=bool(instance))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK if instance else status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        hq_opex_data = request.data.get('expenses', [])
        print(f"Received {len(hq_opex_data)} HQ expenses for bulk create")
        if not hq_opex_data:
            return Response({'error': 'No HQ expense data provided'}, status=status.HTTP_400_BAD_REQUEST)

        created, updated, errors = [], [], []
        with transaction.atomic():
            for idx, hq_opex_item in enumerate(hq_opex_data):
                try:
                    print(f"Processing HQ expense item {idx}: {hq_opex_item}")
                    
                    # Normalize date to YYYY-MM-DD
                    original_date = hq_opex_item.get('date')
                    if isinstance(hq_opex_item.get('date'), str) and 'T' in hq_opex_item['date']:
                        hq_opex_item['date'] = hq_opex_item['date'].split('T')[0]
                    print(f"Date normalized: {original_date} → {hq_opex_item.get('date')}")

                    # Smart search strategy:
                    # 1. First try external_id + date (MOST SPECIFIC)
                    # 2. Fall back to composite key search (backward compatibility)
                    existing = None
                    
                    external_id = hq_opex_item.get('external_id')
                    if external_id:
                        # Search by external_id + date for precision
                        precise_search = {
                            'external_id': external_id,
                            'date': hq_opex_item.get('date')
                        }
                        print(f"Searching by external_id + date: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                        print(f"Found by precise search: {existing}")
                    
                    # If not found by precise search, try composite key search
                    if not existing:
                        search_criteria = {
                            'date': hq_opex_item.get('date'),
                            'purpose': hq_opex_item.get('purpose', ''),
                            'payee': hq_opex_item.get('payee', '')
                        }
                        print(f"Searching by composite key: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()
                        print(f"Found by composite key: {existing}")
                    
                    print(f"Final existing record: {existing}")
                    
                    # Resolve all foreign key references
                    try:
                        resolved_data = self.resolve_foreign_keys(hq_opex_item)
                        print(f"Resolved foreign keys: "
                              f"gl_breakdown={resolved_data.get('gl_breakdown')}, "
                              f"opex_category={resolved_data.get('opex_category')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': hq_opex_item, 'errors': str(e)})
                        continue
                    
                    if existing:
                        print(f"UPDATING existing HQ expense ID: {existing.id}")
                        serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            print(f"Successfully updated HQ expense ID: {saved_instance.id}")
                            updated.append(serializer.data)
                        else:
                            print(f"Update validation failed: {serializer.errors}")
                            errors.append({'index': idx, 'data': hq_opex_item, 'errors': serializer.errors})
                    else:
                        print(f"CREATING new HQ expense record")
                        serializer = self.get_serializer(data=resolved_data)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            print(f"Successfully created HQ expense ID: {saved_instance.id}")
                            created.append(serializer.data)
                        else:
                            print(f"Create validation failed: {serializer.errors}")
                            errors.append({'index': idx, 'data': hq_opex_item, 'errors': serializer.errors})
                except Exception as e:
                    print(f"Exception at {idx}: {e}")
                    import traceback
                    print(f"Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': hq_opex_item, 'errors': str(e)})

        print(f"Final results: Created={len(created)}, Updated={len(updated)}, Errors={len(errors)}")
        response_data = {'created': len(created), 'updated': len(updated), 'errors': len(errors),
                         'created_data': created, 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'])
    def bulk_update(self, request):
        hq_opex_data = request.data.get('expenses', [])
        print(f"Received {len(hq_opex_data)} HQ expenses for bulk update")
        if not hq_opex_data:
            return Response({'error': 'No HQ expense data provided'}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []
        with transaction.atomic():
            for idx, hq_opex_item in enumerate(hq_opex_data):
                try:
                    print(f"Processing UPDATE HQ expense item {idx}: {hq_opex_item}")
                    
                    # Normalize dates
                    comp = hq_opex_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('date'), str) and 'T' in comp['date']:
                        comp['date'] = comp['date'].split('T')[0]
                    if isinstance(hq_opex_item.get('date'), str) and 'T' in hq_opex_item['date']:
                        hq_opex_item['date'] = hq_opex_item['date'].split('T')[0]

                    # Smart search for UPDATE:
                    # 1. First try external_id + date (MOST SPECIFIC)
                    # 2. Fall back to composite key search (backward compatibility)
                    existing = None
                    
                    if comp:
                        data_to_resolve = {**hq_opex_item}
                        data_to_resolve.update(comp)
                        data_to_resolve.pop('_composite_key', None)
                    else:
                        data_to_resolve = hq_opex_item
                    
                    external_id = data_to_resolve.get('external_id')
                    if external_id:
                        # Search by external_id + date for precision
                        precise_search = {
                            'external_id': external_id,
                            'date': data_to_resolve.get('date')
                        }
                        print(f"UPDATE: Searching by external_id + date: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                        print(f"UPDATE: Found by precise search: {existing}")
                    
                    # If not found by precise search, try composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'date': comp.get('date'),
                                'purpose': comp.get('purpose', ''),
                                'payee': comp.get('payee', '')
                            }
                            print(f"UPDATE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'date': hq_opex_item.get('date'),
                                'purpose': hq_opex_item.get('purpose', ''),
                                'payee': hq_opex_item.get('payee', '')
                            }
                            print(f"UPDATE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()
                        print(f"UPDATE: Found by composite key: {existing}")

                    print(f"UPDATE: Found existing record: {existing}")

                    if not existing:
                        print(f"UPDATE: HQ expense not found for update")
                        errors.append({'index': idx, 'data': hq_opex_item, 'errors': 'HQ expense not found for update'})
                        continue

                    # Resolve all foreign key references
                    try:
                        resolved_data = self.resolve_foreign_keys(data_to_resolve)
                        print(f"UPDATE: Resolved foreign keys: "
                              f"gl_breakdown={resolved_data.get('gl_breakdown')}, "
                              f"opex_category={resolved_data.get('opex_category')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': hq_opex_item, 'errors': str(e)})
                        continue

                    serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                    if serializer.is_valid():
                        saved_instance = serializer.save()
                        print(f"UPDATE: Successfully updated HQ expense ID: {saved_instance.id}")
                        updated.append(serializer.data)
                    else:
                        print(f"UPDATE: Validation failed: {serializer.errors}")
                        errors.append({'index': idx, 'data': hq_opex_item, 'errors': serializer.errors})
                except Exception as e:
                    print(f"UPDATE Exception at {idx}: {e}")
                    import traceback
                    print(f"UPDATE Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': hq_opex_item, 'errors': str(e)})

        print(f"UPDATE Final results: Updated={len(updated)}, Errors={len(errors)}")
        response_data = {'updated': len(updated), 'errors': len(errors), 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        hq_opex_data = request.data.get('expenses', [])
        print(f"Received {len(hq_opex_data)} HQ expenses for bulk delete")
        if not hq_opex_data:
            return Response({'error': 'No HQ expense data provided'}, status=status.HTTP_400_BAD_REQUEST)

        deleted, errors = 0, []
        with transaction.atomic():
            for idx, hq_opex_item in enumerate(hq_opex_data):
                try:
                    print(f"Processing DELETE HQ expense item {idx}: {hq_opex_item}")
                    
                    # Normalize dates
                    comp = hq_opex_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('date'), str) and 'T' in comp['date']:
                        comp['date'] = comp['date'].split('T')[0]
                    if isinstance(hq_opex_item.get('date'), str) and 'T' in hq_opex_item['date']:
                        hq_opex_item['date'] = hq_opex_item['date'].split('T')[0]

                    # Smart search for DELETE:
                    # 1. First try external_id + date (MOST SPECIFIC)
                    # 2. Fall back to composite key search (backward compatibility)
                    existing = None
                    
                    if comp:
                        search_data = comp
                    else:
                        search_data = hq_opex_item
                    
                    external_id = search_data.get('external_id')
                    if external_id:
                        # Search by external_id + date for precision
                        precise_search = {
                            'external_id': external_id,
                            'date': search_data.get('date')
                        }
                        print(f"DELETE: Searching by external_id + date: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                        print(f"DELETE: Found by precise search: {existing}")
                    
                    # If not found by precise search, try composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'date': comp.get('date'),
                                'purpose': comp.get('purpose', ''),
                                'payee': comp.get('payee', '')
                            }
                            print(f"DELETE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'date': hq_opex_item.get('date'),
                                'purpose': hq_opex_item.get('purpose', ''),
                                'payee': hq_opex_item.get('payee', '')
                            }
                            print(f"DELETE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()
                        print(f"DELETE: Found by composite key: {existing}")

                    print(f"DELETE: Found existing record: {existing}")

                    if existing:
                        existing.delete()
                        print(f"DELETE: Successfully deleted HQ expense")
                        deleted += 1
                    else:
                        print(f"DELETE: HQ expense not found for deletion")
                        errors.append({'index': idx, 'data': hq_opex_item, 'errors': 'HQ expense not found for deletion'})
                except Exception as e:
                    print(f"DELETE Exception at {idx}: {e}")
                    import traceback
                    print(f"DELETE Full traceback: {traceback.format_exc()}")
                    errors.append({'index': idx, 'data': hq_opex_item, 'errors': str(e)})

        print(f"DELETE Final results: Deleted={deleted}, Errors={len(errors)}")
        response_data = {'deleted': deleted, 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)


class GLBreakdownViewSet(viewsets.ModelViewSet):
    queryset = GLBreakdown.objects.all()
    serializer_class = GLBreakdownSerializer


class MonthlyRevenueBilledViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyRevenueBilledSerializer

    def get_queryset(self):
        feeders = get_filtered_feeders(self.request)
        month_from, month_to = get_date_range_from_request(self.request, 'month')

        qs = MonthlyRevenueBilled.objects.filter(feeder__in=feeders)

        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)

        return qs
    
class SalaryPaymentViewSet(viewsets.ModelViewSet):
    queryset = SalaryPayment.objects.all()
    serializer_class = SalaryPaymentSerializer
    filterset_fields = ["district", "month", "staff"]


class FinancialSummaryView(APIView):
    def get(self, request):
        data = {
            "total_cost": round(get_total_cost(request), 2),
            "total_revenue_billed": round(get_total_revenue_billed(request), 2),
            "total_collections": round(get_total_collections(request), 2),
            "opex_breakdown": get_opex_breakdown(request),
            "tariff_loss_percentage": get_tariff_loss(request),
        }
        return Response(data)
    


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


@api_view(['GET'])
def financial_feeder_view(request):
    """
    Returns feeder-level financial metrics filtered by state or business district and date.
    Business district filter takes precedence.
    """
    data = get_financial_feeder_data(request)
    return Response(data)


def calculate_percentage_change(current_value, previous_value):
    """Calculate percentage change between two values"""
    if previous_value == 0:
        return 100 if current_value > 0 else 0
    return ((current_value - previous_value) / previous_value) * 100


@api_view(["GET"])
def sales_rep_performance_view(request, rep_id):
    try:
        rep = SalesRepresentative.objects.get(id=rep_id)
    except SalesRepresentative.DoesNotExist:
        return Response({"error": "Sales rep not found."}, status=status.HTTP_404_NOT_FOUND)

    mode = request.GET.get("mode", "monthly")
    year = int(request.GET.get("year", datetime.now().year))
    month = int(request.GET.get("month", datetime.now().month))
    transformer_name = request.GET.get("transformer")  # New parameter

    start_date = datetime(year, month, 1)
    end_date = (start_date + relativedelta(months=1)) - timedelta(days=1)
    
    # Previous month dates for delta calculations
    prev_month_start = start_date - relativedelta(months=1)
    prev_month_end = (prev_month_start + relativedelta(months=1)) - timedelta(days=1)

    # Base query filter for sales rep
    base_filter = {"sales_rep": rep}
    
    # If transformer is specified, add it to the filter
    transformer_obj = None
    if transformer_name:
        try:
            transformer_obj = DistributionTransformer.objects.get(
                name__iexact=transformer_name,
                id__in=rep.assigned_transformers.values_list('id', flat=True)
            )
            base_filter["transformer"] = transformer_obj
        except DistributionTransformer.DoesNotExist:
            return Response({
                "error": f"Transformer '{transformer_name}' not found or not assigned to this sales rep."
            }, status=status.HTTP_404_NOT_FOUND)
        except DistributionTransformer.MultipleObjectsReturned:
            return Response({
                "error": f"Multiple transformers found with name '{transformer_name}'. Please be more specific."
            }, status=status.HTTP_400_BAD_REQUEST)

    # Current month summary
    current_summary = MonthlyCommercialSummary.objects.filter(
        **base_filter,
        month__range=(start_date, end_date)
    ).aggregate(
        revenue_billed=Sum("revenue_billed"),
        revenue_collected=Sum("revenue_collected"),
        customers_billed=Sum("customers_billed"),
        customers_responded=Sum("customers_responded"),
    )

    # Previous month summary for delta calculations
    previous_summary = MonthlyCommercialSummary.objects.filter(
        **base_filter,
        month__range=(prev_month_start, prev_month_end)
    ).aggregate(
        revenue_billed=Sum("revenue_billed"),
        revenue_collected=Sum("revenue_collected"),
        customers_billed=Sum("customers_billed"),
        customers_responded=Sum("customers_responded"),
    )

    # Current month values
    revenue_billed = current_summary["revenue_billed"] or 0
    revenue_collected = current_summary["revenue_collected"] or 0
    customers_billed = current_summary["customers_billed"] or 0
    customers_responded = current_summary["customers_responded"] or 0
    outstanding_billed = revenue_billed - revenue_collected

    # Previous month values
    prev_revenue_billed = previous_summary["revenue_billed"] or 0
    prev_revenue_collected = previous_summary["revenue_collected"] or 0
    prev_customers_billed = previous_summary["customers_billed"] or 0
    prev_customers_responded = previous_summary["customers_responded"] or 0
    prev_outstanding_billed = prev_revenue_billed - prev_revenue_collected

    # Calculate additional metrics
    days_in_month = (end_date - start_date).days + 1
    daily_run_rate = revenue_collected / days_in_month if days_in_month > 0 else 0
    
    prev_days_in_month = (prev_month_end - prev_month_start).days + 1
    prev_daily_run_rate = prev_revenue_collected / prev_days_in_month if prev_days_in_month > 0 else 0
    
    collections_on_outstanding = 0  # Placeholder as requested
    prev_collections_on_outstanding = 0  # Placeholder
    
    # Using customers_billed as active accounts
    active_accounts = customers_billed
    prev_active_accounts = prev_customers_billed
    
    # Using customers_billed - customers_responded as suspended accounts
    suspended_accounts = max(0, customers_billed - customers_responded)
    prev_suspended_accounts = max(0, prev_customers_billed - prev_customers_responded)

    # Calculate deltas (percentage changes)
    revenue_billed_delta = calculate_percentage_change(revenue_billed, prev_revenue_billed)
    revenue_collected_delta = calculate_percentage_change(revenue_collected, prev_revenue_collected)
    outstanding_billed_delta = calculate_percentage_change(outstanding_billed, prev_outstanding_billed)
    daily_run_rate_delta = calculate_percentage_change(daily_run_rate, prev_daily_run_rate)
    collections_on_outstanding_delta = calculate_percentage_change(collections_on_outstanding, prev_collections_on_outstanding)
    active_accounts_delta = calculate_percentage_change(active_accounts, prev_active_accounts)
    suspended_accounts_delta = calculate_percentage_change(suspended_accounts, prev_suspended_accounts)

    # All-time summary (filtered by transformer if specified)
    all_time_summary = MonthlyCommercialSummary.objects.filter(**base_filter).aggregate(
        all_time_billed=Sum("revenue_billed"),
        all_time_collected=Sum("revenue_collected")
    )
    outstanding_all_time = (all_time_summary["all_time_billed"] or 0) - (all_time_summary["all_time_collected"] or 0)

    # ---- Previous 4 Months (excluding current month) ---- #
    monthly_summaries = []
    for i in range(1, 5):  # Start from 1 to exclude current month, go to 5 to get 4 months
        ref_date = start_date - relativedelta(months=i)
        month_start = ref_date.replace(day=1)
        month_end = (month_start + relativedelta(months=1)) - timedelta(days=1)

        summary = MonthlyCommercialSummary.objects.filter(
            **base_filter,
            month__range=(month_start, month_end)
        ).aggregate(
            revenue_billed=Sum("revenue_billed") or 0,
            revenue_collected=Sum("revenue_collected") or 0,
        )

        billed = summary["revenue_billed"] or 0
        collected = summary["revenue_collected"] or 0
        outstanding = billed - collected

        monthly_summaries.append({
            "month": month_start.strftime("%b"),
            "revenue_billed": billed,
            "revenue_collected": collected,
            "outstanding_billed": outstanding
        })

    monthly_summaries.reverse()  # Reverse to show oldest to newest

    # Prepare response data
    response_data = {
        "sales_rep": {
            "id": str(rep.id),
            "name": rep.name
        },
        "current": {
            "revenue_billed": {
                "value": revenue_billed,
                "delta": round(revenue_billed_delta, 2)
            },
            "revenue_collected": {
                "value": revenue_collected,
                "delta": round(revenue_collected_delta, 2)
            },
            "outstanding_billed": {
                "value": outstanding_billed,
                "delta": round(outstanding_billed_delta, 2)
            },
            "daily_run_rate": {
                "value": round(daily_run_rate, 2),
                "delta": round(daily_run_rate_delta, 2)
            },
            "collections_on_outstanding": {
                "value": collections_on_outstanding,
                "delta": round(collections_on_outstanding_delta, 2)
            },
            "active_accounts": {
                "value": active_accounts,
                "delta": round(active_accounts_delta, 2)
            },
            "suspended_accounts": {
                "value": suspended_accounts,
                "delta": round(suspended_accounts_delta, 2)
            }
        },
        "outstanding_all_time": outstanding_all_time,
        "previous_months": monthly_summaries
    }

    # Add transformer info to response if filtered
    if transformer_obj:
        response_data["filtered_by_transformer"] = {
            "id": str(transformer_obj.id),
            "name": transformer_obj.name,
            "feeder": transformer_obj.feeder.name if transformer_obj.feeder else None,
            "business_district": transformer_obj.feeder.business_district.name if transformer_obj.feeder and transformer_obj.feeder.business_district else None,
            "state": transformer_obj.feeder.business_district.state.name if transformer_obj.feeder and transformer_obj.feeder.business_district and transformer_obj.feeder.business_district.state else None
        }
    else:
        response_data["filtered_by_transformer"] = None

    return Response(response_data)

@api_view(["GET"])
def list_sales_reps(request):
    reps = SalesRepresentative.objects.select_related(
        
    ).prefetch_related(
        'assigned_transformers'  # Adjust field name as needed
    ).all()

    reps = reps.order_by('name')

    data = SalesRepresentativeSerializer(reps, many=True).data
    return Response(data)



class FinancialAllStatesView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        target_month = date(year, month, 1)

        results = []

        for state in State.objects.all():
            # --- Total Cost (Complete cost calculation) ---
            # OPEX (both credit and debit)
            opex_data = Opex.objects.filter(
                date__year=year,
                date__month=month,
                district__state=state
            ).aggregate(
                credit_total=Sum("credit"),
                debit_total=Sum("debit")
            )
            
            opex_total = Decimal(opex_data["credit_total"] or 0) + Decimal(opex_data["debit_total"] or 0)

            # Salaries
            salary_total = SalaryPayment.objects.filter(
                month=target_month,
                district__state=state
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # NBET Invoice
            nbet_total = NBETInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            # MO Invoice
            mo_total = MOInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # Total cost
            total_cost = opex_total + salary_total + nbet_total + mo_total

            # --- Revenue Billed and Collections (from MonthlyCommercialSummary) ---
            sales_reps = SalesRepresentative.objects.filter(
                assigned_transformers__feeder__business_district__state=state
            ).distinct()

            commercial_data = MonthlyCommercialSummary.objects.filter(
                month=target_month,
                sales_rep__in=sales_reps
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                collections=Sum("revenue_collected")
            )

            revenue_billed = commercial_data["revenue_billed"] or Decimal("0")
            collections = commercial_data["collections"] or Decimal("0")

            # --- Real Tariff Calculations ---
            # Get energy delivered for tariff calculations
            energy_delivered = EnergyDelivered.objects.filter(
                feeder__business_district__state=state,
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # MYTO Tariff (get the latest applicable tariff)
            myto_tariff_obj = MYTOTariff.objects.filter(
                effective_date__lte=target_month
            ).order_by("-effective_date").first()
            
            myto_tariff = myto_tariff_obj.rate_per_kwh if myto_tariff_obj else Decimal("60")

            # Calculate actual tariff collected (Collections / Energy in kWh)
            if energy_delivered > 0:
                energy_delivered_kwh = energy_delivered * 1000  # Convert MWh to kWh
                actual_tariff = collections / energy_delivered_kwh
            else:
                actual_tariff = Decimal("0")

            # Tariff Loss = MYTO Tariff - Actual Tariff Collected
            tariff_loss = myto_tariff - actual_tariff

            # --- Compile State Metrics ---
            results.append({
                "state": state.name,
                "total_cost": round(total_cost, 2),
                "revenue_billed": round(revenue_billed, 2),
                "collections": round(collections, 2),
                "myto_allowed_tariff": f"{myto_tariff}",
                "actual_tariff_collected": f"{actual_tariff}",
                "tariff_loss": f"{tariff_loss}"
            })

        return Response(results, status=status.HTTP_200_OK)


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
        target_month_end = target_month + relativedelta(months=1)
        results = []

        districts = BusinessDistrict.objects.filter(state=state)

        for district in districts:
            # --- Total Cost Calculation (All cost components) ---
            # OPEX (both credit and debit)
            opex_data = Opex.objects.filter(
                district=district,
                date__year=year,
                date__month=month
            ).aggregate(
                credit_total=Sum("credit"),
                debit_total=Sum("debit")
            )
            
            opex_total = Decimal(opex_data["credit_total"] or 0) + Decimal(opex_data["debit_total"] or 0)

            # Salaries for the district
            salary_total = SalaryPayment.objects.filter(
                district=district,
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # --- Energy-based NBET/MO Allocation ---
            # Get district's energy share for proportional allocation
            district_energy = EnergyDelivered.objects.filter(
                feeder__business_district=district,
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # Total energy delivered across all feeders for the month
            total_energy = EnergyDelivered.objects.filter(
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # Calculate energy share (0-1)
            energy_share = (district_energy / total_energy) if total_energy > 0 else Decimal("0")

            # NBET Invoice (allocated proportionally)
            nbet_total = NBETInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            nbet_allocated = nbet_total * energy_share

            # MO Invoice (allocated proportionally)
            mo_total = MOInvoice.objects.filter(
                month=target_month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            mo_allocated = mo_total * energy_share

            # Total cost = OPEX + Salaries + Allocated NBET + Allocated MO
            total_cost = opex_total + salary_total + nbet_allocated + mo_allocated

            # --- Revenue Billed and Collections ---
            sales_reps = SalesRepresentative.objects.filter(
                assigned_transformers__feeder__business_district=district
            ).distinct()

            commercial_data = MonthlyCommercialSummary.objects.filter(
                month=target_month,
                sales_rep__in=sales_reps
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                collections=Sum("revenue_collected")
            )

            revenue_billed = commercial_data["revenue_billed"] or Decimal("0")
            collections = commercial_data["collections"] or Decimal("0")

            # --- Real Tariff Loss Calculation ---
            # Get MYTO tariff (latest applicable)
            myto_tariff_obj = MYTOTariff.objects.filter(
                effective_date__lte=target_month
            ).order_by("-effective_date").first()
            
            myto_tariff = myto_tariff_obj.rate_per_kwh if myto_tariff_obj else Decimal("60")

            # Calculate actual tariff collected (Collections / Energy in kWh)
            if district_energy > 0:
                district_energy_kwh = district_energy * 1000  # Convert MWh to kWh
                actual_tariff_collected = collections / district_energy_kwh
                billing_tariff = revenue_billed / district_energy_kwh
            else:
                actual_tariff_collected = Decimal("0")
                billing_tariff = Decimal("0")

            # Tariff Loss = MYTO Tariff - Actual Tariff Collected
            tariff_loss = myto_tariff - actual_tariff_collected

            results.append({
                "district": district.name,
                "total_cost": float(round(total_cost, 2)),
                "revenue_billed": float(round(revenue_billed, 2)),
                "collections": float(round(collections, 2)),
                "tariff_loss": float(round(tariff_loss, 2))
            })

        return Response(results, status=status.HTTP_200_OK)

class FinancialServiceBandMetricsView(APIView):
    def get(self, request):
        try:
            year = int(request.GET.get("year"))
            month = int(request.GET.get("month"))
        except (TypeError, ValueError):
            return Response({"error": "Invalid or missing 'year' or 'month' parameters."},
                            status=status.HTTP_400_BAD_REQUEST)

        state_name = request.GET.get("state")
        selected_date = date(year, month, 1)
        selected_end = selected_date + relativedelta(months=1)

        bands = Band.objects.all()
        results = []

        for band in bands:
            # Get feeders for the band (filtered by state if provided)
            feeders = Feeder.objects.filter(band=band)
            if state_name:
                feeders = feeders.filter(business_district__state__name__iexact=state_name)

            if not feeders.exists():
                continue

            # Get distinct business districts tied to the feeders
            district_ids = feeders.values_list("business_district_id", flat=True).distinct()

            # --- Total Cost Calculation (All cost components) ---
            # OPEX (both credit and debit) from relevant districts
            opex_data = Opex.objects.filter(
                district__in=district_ids,
                date__year=year,
                date__month=month
            ).aggregate(
                credit_total=Sum("credit"),
                debit_total=Sum("debit")
            )
            
            opex_total = Decimal(opex_data["credit_total"] or 0) + Decimal(opex_data["debit_total"] or 0)

            # Salaries for the districts
            salary_total = SalaryPayment.objects.filter(
                district__in=district_ids,
                month=selected_date
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            # --- Energy-based NBET/MO Allocation ---
            # Get band's energy share for proportional allocation
            band_energy = EnergyDelivered.objects.filter(
                feeder__in=feeders,
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # Total energy delivered across all feeders for the month
            total_energy = EnergyDelivered.objects.filter(
                date__year=year,
                date__month=month
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or Decimal("0")

            # Calculate energy share (0-1)
            energy_share = (band_energy / total_energy) if total_energy > 0 else Decimal("0")

            # NBET Invoice (allocated proportionally)
            nbet_total = NBETInvoice.objects.filter(
                month=selected_date
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            nbet_allocated = nbet_total * energy_share

            # MO Invoice (allocated proportionally)
            mo_total = MOInvoice.objects.filter(
                month=selected_date
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            mo_allocated = mo_total * energy_share

            # Total cost = OPEX + Salaries + Allocated NBET + Allocated MO
            total_cost = opex_total + salary_total + nbet_allocated + mo_allocated

            # --- Revenue and Collections ---
            # Get all sales reps tied to feeders via transformers
            sales_reps = SalesRepresentative.objects.filter(
                assigned_transformers__feeder__in=feeders
            ).distinct()

            # Aggregate commercial revenue & collections
            commercial = MonthlyCommercialSummary.objects.filter(
                sales_rep__in=sales_reps,
                month=selected_date
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected")
            )

            revenue_billed = commercial["revenue_billed"] or Decimal("0")
            revenue_collected = commercial["revenue_collected"] or Decimal("0")

            # --- Real Tariff Calculations ---
            # Get MYTO tariff (latest applicable)
            myto_tariff_obj = MYTOTariff.objects.filter(
                effective_date__lte=selected_date
            ).order_by("-effective_date").first()
            
            myto_tariff = myto_tariff_obj.rate_per_kwh if myto_tariff_obj else Decimal("60")

            # Calculate actual tariff collected (Collections / Energy in kWh)
            if band_energy > 0:
                band_energy_kwh = band_energy * 1000  # Convert MWh to kWh
                actual_tariff_collected = revenue_collected / band_energy_kwh
            else:
                actual_tariff_collected = Decimal("0")

            # Tariff Loss = MYTO Tariff - Actual Tariff Collected
            tariff_loss = myto_tariff - actual_tariff_collected

            results.append({
                "band": band.name,
                "total_cost": float(round(total_cost, 2)),
                "revenue_billed": float(round(revenue_billed, 2)),
                "collections": float(round(revenue_collected, 2)),
                "myto_allowed_tariff": f"{round(myto_tariff, 2)}",
                "actual_tariff_collected": f"{round(actual_tariff_collected, 2)}",
                "tariff_loss": f"{round(tariff_loss, 2)}"
            })

        return Response(results, status=status.HTTP_200_OK)



class DailyCollectionsByMonthView(APIView):
    def get(self, request):
        try:
            year = int(request.GET.get("year"))
            month = int(request.GET.get("month"))
            start_date = date(year, month, 1)
        except (TypeError, ValueError):
            return Response({"error": "Valid 'year' and 'month' query parameters are required."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Get last day of the month
        next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_date = next_month - timedelta(days=1)

        results = []

        current_day = start_date
        while current_day <= end_date:
            total_collections = MonthlyCommercialSummary.objects.filter(
                month=current_day
            ).aggregate(
                total=Sum("revenue_collected")
            )["total"] or 0

            results.append({
                "day": current_day.day,
                "value": round(total_collections, 2)
            })

            current_day += timedelta(days=1)

        return Response(results, status=status.HTTP_200_OK)


@api_view(['GET'])
def financial_transformer_view(request):
    feeder_slug = request.GET.get("feeder")
    mode = request.GET.get("mode", "monthly")
    year = request.GET.get("year")
    month = request.GET.get("month")

    if not feeder_slug:
        return Response({"error": "Missing feeder slug."}, status=400)

    try:
        feeder = Feeder.objects.get(slug=feeder_slug)
    except Feeder.DoesNotExist:
        return Response({"error": "Feeder not found."}, status=404)

    # Handle date filters
    if mode == "monthly" and year and month:
        year = int(year)
        month = int(month)
        start_day = date(year, month, 1)
        end_day = date(year, month, monthrange(year, month)[1])
        date_from, date_to = start_day, end_day
    else:
        date_from, date_to = get_date_range_from_request(request, "date")

    transformer_data = []
    for transformer in feeder.transformers.all():
        reps = SalesRepresentative.objects.filter(
            assigned_transformers=transformer
        ).distinct()

        summary = MonthlyCommercialSummary.objects.filter(
            sales_rep__in=reps,
            month__range=(date_from, date_to)
        ).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected")
        )

        revenue_billed = summary["revenue_billed"] or 0
        revenue_collected = summary["revenue_collected"] or 0

        total_cost = Opex.objects.filter(
            district=feeder.business_district,
            date__range=(date_from, date_to)
        ).aggregate(total=Sum("credit"))["total"] or 0

        transformer_data.append({
            "transformer": transformer.name,
            "slug": transformer.slug,
            "total_cost": round(total_cost, 2),
            "revenue_billed": round(revenue_billed, 2),
            "revenue_collected": round(revenue_collected, 2),
            "atcc": 6
        })

    return Response({
        "feeder": feeder.name,
        "slug": feeder.slug,
        "transformers": transformer_data
    })