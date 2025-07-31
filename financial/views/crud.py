from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from financial.models import *
from financial.serializers import *
from common.mixins import DistrictLocationFilterMixin
from commercial.utils import get_filtered_feeders
from commercial.date_filters import get_date_range_from_request
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import action
from django.db import transaction
from common.models import BusinessDistrict as District


class OpexCategoryViewSet(viewsets.ModelViewSet):
    queryset = OpexCategory.objects.all()
    serializer_class = OpexCategorySerializer

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
