from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from django.db.models import (
    Sum, Count, Avg
)
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from commercial.models import *
from commercial.serializers import *
from commercial.utils import get_filtered_feeders
from common.models import DistributionTransformer
from commercial.models import (
    DailyCollection, MonthlyEnergyBilled
)
from technical.models import EnergyDelivered
from commercial.date_filters import get_date_range_from_request
from commercial.mixins import FeederFilteredQuerySetMixin
from commercial.utils import get_filtered_customers
from commercial.models import MonthlyRevenueBilled
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend



from decimal import Decimal, ROUND_HALF_UP

def round_two_places(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class CustomerViewSet(viewsets.ViewSet):
    """
    Custom ViewSet to return either customer details or just counts
    """

    def list(self, request):
        customers = get_filtered_customers(request)

        # Show full data only if explicitly asked for
        if request.GET.get("details") == "true":
            serializer = CustomerSerializer(customers, many=True)
            return Response(serializer.data)
        else:
            count = customers.count()
            return Response({"count": count})



class MonthlyRevenueBilledViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyRevenueBilledSerializer

    def get_queryset(self):
        queryset = MonthlyRevenueBilled.objects.all()
        
        # Custom location filtering for MonthlyRevenueBilled
        state_name = self.request.GET.get('state')
        district_name = self.request.GET.get('business_district')
        feeder_slug = self.request.GET.get('feeder')
        transformer_slug = self.request.GET.get('transformer')

        if transformer_slug:
            queryset = queryset.filter(transformer__slug=transformer_slug)
        elif feeder_slug:
            queryset = queryset.filter(transformer__feeder__slug=feeder_slug)
        elif district_name:
            queryset = queryset.filter(transformer__feeder__business_district__name__iexact=district_name)
        elif state_name:
            queryset = queryset.filter(transformer__feeder__business_district__state__name__iexact=state_name)

        # Date filtering
        month_from, month_to = get_date_range_from_request(self.request, 'month')

        if month_from and month_to:
            queryset = queryset.filter(month__range=(month_from, month_to))
        elif month_from:
            queryset = queryset.filter(month__gte=month_from)
        elif month_to:
            queryset = queryset.filter(month__lte=month_to)

        return queryset.select_related(
            'transformer', 'transformer__feeder', 'transformer__feeder__business_district',
            'transformer__feeder__business_district__state', 'sales_rep'
        )

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get revenue billing summary with aggregations"""
        queryset = self.get_queryset()
        
        # Basic aggregations
        summary_data = queryset.aggregate(
            total_amount=Sum('amount'),
            total_records=Count('id'),
            avg_amount=Avg('amount')
        )

        # Group by sales rep
        by_sales_rep = queryset.values(
            'sales_rep__name', 'sales_rep__slug'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by transformer
        by_transformer = queryset.values(
            'transformer__name', 'transformer__slug'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by business district
        by_district = queryset.values(
            'transformer__feeder__business_district__name'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by state
        by_state = queryset.values(
            'transformer__feeder__business_district__state__name'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        return Response({
            'summary': summary_data,
            'by_sales_rep': by_sales_rep,
            'by_transformer': by_transformer,
            'by_district': by_district,
            'by_state': by_state
        })

    # === COLLECTION TOOL INTEGRATION METHODS ===
    
    def resolve_sales_rep_id_to_uuid(self, sales_rep_id):
        """Convert sales_rep_id to SalesRepresentative UUID"""
        if not sales_rep_id or sales_rep_id.strip() == '':
            return None
            
        slug = sales_rep_id.strip()
        try:
            sales_rep = SalesRepresentative.objects.get(slug__iexact=slug)
            print(f"Sales rep '{slug}' resolved to PK: {sales_rep.id}")
            return sales_rep.id
        except SalesRepresentative.DoesNotExist:
            print(f"Sales rep '{slug}' not found")
            return None

    def resolve_dt_id_to_uuid(self, dt_id):
        """Convert dt_id to DistributionTransformer UUID"""
        if not dt_id or dt_id.strip() == '':
            return None
            
        slug = dt_id.strip()
        try:
            transformer = DistributionTransformer.objects.get(slug__iexact=slug)
            print(f"Transformer '{slug}' resolved to PK: {transformer.id}")
            return transformer.id
        except DistributionTransformer.DoesNotExist:
            print(f"Transformer '{slug}' not found")
            return None

    def validate_sales_rep_transformer_assignment(self, sales_rep_id, transformer_id):
        """Validate that sales_rep is assigned to the transformer"""
        try:
            sales_rep = SalesRepresentative.objects.get(id=sales_rep_id)
            transformer = DistributionTransformer.objects.get(id=transformer_id)
            
            if not sales_rep.assigned_transformers.filter(id=transformer_id).exists():
                return False, f"Sales rep '{sales_rep.slug}' is not assigned to transformer '{transformer.slug}'"
            return True, None
        except (SalesRepresentative.DoesNotExist, DistributionTransformer.DoesNotExist):
            return False, "Sales rep or transformer not found"

    def resolve_foreign_keys(self, revenue_item):
        """Resolve all foreign key references from IDs to UUIDs"""
        resolved_data = revenue_item.copy()
        
        # Resolve sales_rep_id
        sales_rep_id = revenue_item.get('sales_rep_id')
        if sales_rep_id:
            sales_rep_pk = self.resolve_sales_rep_id_to_uuid(sales_rep_id)
            if not sales_rep_pk:
                raise ValueError(f"Sales rep ID '{sales_rep_id}' could not be resolved")
            resolved_data['sales_rep'] = sales_rep_pk
        else:
            resolved_data['sales_rep'] = None
        
        # Remove sales_rep_id from final data
        resolved_data.pop('sales_rep_id', None)
        
        # Resolve dt_id to transformer
        dt_id = revenue_item.get('dt_id')
        if dt_id:
            transformer_pk = self.resolve_dt_id_to_uuid(dt_id)
            if not transformer_pk:
                raise ValueError(f"Transformer ID '{dt_id}' could not be resolved")
            resolved_data['transformer'] = transformer_pk
        else:
            resolved_data['transformer'] = None
            
        # Remove dt_id from final data
        resolved_data.pop('dt_id', None)
        
        # Validate assignment during CREATE only
        if resolved_data.get('sales_rep') and resolved_data.get('transformer'):
            is_valid, error_msg = self.validate_sales_rep_transformer_assignment(
                resolved_data['sales_rep'], resolved_data['transformer']
            )
            if not is_valid:
                raise ValueError(error_msg)
        
        return resolved_data

    @action(detail=False, methods=['post'], url_path='upsert-external')
    def upsert_external(self, request):
        external_id = request.data.get("external_id")
        if not external_id:
            return Response({"error": "external_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        instance = MonthlyRevenueBilled.objects.filter(external_id=external_id).first()
        serializer = self.get_serializer(instance, data=request.data, partial=bool(instance))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK if instance else status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='bulk_create')
    def bulk_create(self, request):
        # """Test endpoint to verify URL routing works"""
        # return Response({'message': 'URL routing works!', 'viewset': 'MonthlyRevenueBilledViewSet'})

        model = self.get_queryset().model
        print(f"🔍 Using model: {model}")
        print(f"🔍 Model fields: {[f.name for f in model._meta.fields]}")
        print(f"🔍 Model path: {model.__module__}.{model.__name__}")

        revenue_data = request.data.get('revenues', [])
        print(f"Received {len(revenue_data)} revenue records for bulk create")
        if not revenue_data:
            return Response({'error': 'No revenue data provided'}, status=status.HTTP_400_BAD_REQUEST)

        created, updated, errors = [], [], []
        with transaction.atomic():
            for idx, revenue_item in enumerate(revenue_data):
                try:
                    print(f"Processing revenue item {idx}: {revenue_item}")
                    
                    # Normalize date
                    if isinstance(revenue_item.get('month'), str) and 'T' in revenue_item['month']:
                        revenue_item['month'] = revenue_item['month'].split('T')[0]

                    # Smart search strategy
                    existing = None
                    external_id = revenue_item.get('external_id')
                    
                    if external_id:
                        # Search by external_id + month
                        precise_search = {
                            'external_id': external_id,
                            'month': revenue_item.get('month')
                        }
                        print(f"Searching by external_id + month: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        search_criteria = {
                            'month': revenue_item.get('month'),
                            'sales_rep__slug': revenue_item.get('sales_rep_id'),
                            'transformer__slug': revenue_item.get('dt_id', '')
                        }
                        print(f"Searching by composite key: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()
                    
                    # Resolve foreign keys
                    try:
                        resolved_data = self.resolve_foreign_keys(revenue_item)
                        print(f"Resolved foreign keys: sales_rep={resolved_data.get('sales_rep')}, transformer={resolved_data.get('transformer')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': revenue_item, 'errors': str(e)})
                        continue
                    
                    if existing:
                        print(f"UPDATING existing revenue ID: {existing.id}")
                        serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            updated.append(serializer.data)
                        else:
                            errors.append({'index': idx, 'data': revenue_item, 'errors': serializer.errors})
                    else:
                        print(f"CREATING new revenue record")
                        serializer = self.get_serializer(data=resolved_data)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            created.append(serializer.data)
                        else:
                            errors.append({'index': idx, 'data': revenue_item, 'errors': serializer.errors})
                            
                except Exception as e:
                    print(f"Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': revenue_item, 'errors': str(e)})

        response_data = {'created': len(created), 'updated': len(updated), 'errors': len(errors),
                         'created_data': created, 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'], url_path='bulk_update')
    def bulk_update(self, request):
        revenue_data = request.data.get('revenues', [])
        print(f"Received {len(revenue_data)} revenue records for bulk update")
        if not revenue_data:
            return Response({'error': 'No revenue data provided'}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []
        with transaction.atomic():
            for idx, revenue_item in enumerate(revenue_data):
                try:
                    print(f"Processing UPDATE revenue item {idx}: {revenue_item}")
                    
                    # Normalize dates
                    comp = revenue_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('month'), str) and 'T' in comp['month']:
                        comp['month'] = comp['month'].split('T')[0]
                    if isinstance(revenue_item.get('month'), str) and 'T' in revenue_item['month']:
                        revenue_item['month'] = revenue_item['month'].split('T')[0]

                    # Smart search for UPDATE
                    existing = None
                    
                    if comp:
                        data_to_resolve = {**revenue_item}
                        data_to_resolve.update(comp)
                        data_to_resolve.pop('_composite_key', None)
                    else:
                        data_to_resolve = revenue_item
                    
                    external_id = data_to_resolve.get('external_id')
                    if external_id:
                        # Search by external_id + month
                        precise_search = {
                            'external_id': external_id,
                            'month': data_to_resolve.get('month')
                        }
                        print(f"UPDATE: Searching by external_id + month: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'month': comp.get('month'),
                                'sales_rep__slug': comp.get('sales_rep_id'),
                                'transformer__slug': comp.get('dt_id', '')
                            }
                            print(f"UPDATE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'month': revenue_item.get('month'),
                                'sales_rep__slug': revenue_item.get('sales_rep_id'),
                                'transformer__slug': revenue_item.get('dt_id', '')
                            }
                            print(f"UPDATE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()

                    if not existing:
                        print(f"UPDATE: Revenue not found for update")
                        errors.append({'index': idx, 'data': revenue_item, 'errors': 'Revenue not found for update'})
                        continue

                    # Resolve foreign keys
                    try:
                        resolved_data = self.resolve_foreign_keys(data_to_resolve)
                        print(f"UPDATE: Resolved foreign keys: sales_rep={resolved_data.get('sales_rep')}, transformer={resolved_data.get('transformer')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': revenue_item, 'errors': str(e)})
                        continue

                    serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                    if serializer.is_valid():
                        saved_instance = serializer.save()
                        print(f"UPDATE: Successfully updated revenue ID: {saved_instance.id}")
                        updated.append(serializer.data)
                    else:
                        print(f"UPDATE: Validation failed: {serializer.errors}")
                        errors.append({'index': idx, 'data': revenue_item, 'errors': serializer.errors})
                        
                except Exception as e:
                    print(f"UPDATE Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': revenue_item, 'errors': str(e)})

        response_data = {'updated': len(updated), 'errors': len(errors), 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'], url_path='bulk_delete')
    def bulk_delete(self, request):
        revenue_data = request.data.get('revenues', [])
        print(f"Received {len(revenue_data)} revenue records for bulk delete")
        if not revenue_data:
            return Response({'error': 'No revenue data provided'}, status=status.HTTP_400_BAD_REQUEST)

        deleted, errors = 0, []
        with transaction.atomic():
            for idx, revenue_item in enumerate(revenue_data):
                try:
                    print(f"Processing DELETE revenue item {idx}: {revenue_item}")
                    
                    # Normalize dates
                    comp = revenue_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('month'), str) and 'T' in comp['month']:
                        comp['month'] = comp['month'].split('T')[0]
                    if isinstance(revenue_item.get('month'), str) and 'T' in revenue_item['month']:
                        revenue_item['month'] = revenue_item['month'].split('T')[0]

                    # Smart search for DELETE
                    existing = None
                    
                    if comp:
                        search_data = comp
                    else:
                        search_data = revenue_item
                    
                    external_id = search_data.get('external_id')
                    if external_id:
                        # Search by external_id + month
                        precise_search = {
                            'external_id': external_id,
                            'month': search_data.get('month')
                        }
                        print(f"DELETE: Searching by external_id + month: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'month': comp.get('month'),
                                'sales_rep__slug': comp.get('sales_rep_id'),
                                'transformer__slug': comp.get('dt_id', '')
                            }
                            print(f"DELETE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'month': revenue_item.get('month'),
                                'sales_rep__slug': revenue_item.get('sales_rep_id'),
                                'transformer__slug': revenue_item.get('dt_id', '')
                            }
                            print(f"DELETE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()

                    if existing:
                        existing.delete()
                        print(f"DELETE: Successfully deleted revenue")
                        deleted += 1
                    else:
                        print(f"DELETE: Revenue not found for deletion")
                        errors.append({'index': idx, 'data': revenue_item, 'errors': 'Revenue not found for deletion'})
                        
                except Exception as e:
                    print(f"DELETE Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': revenue_item, 'errors': str(e)})

        response_data = {'deleted': deleted, 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    

# class DailyCollectionViewSet(FeederFilteredQuerySetMixin, viewsets.ModelViewSet):
#     serializer_class = DailyCollectionSerializer

#     def get_queryset(self):
#         queryset = DailyCollection.objects.all()
#         queryset = self.filter_by_location(queryset)
#         date_from, date_to = get_date_range_from_request(self.request, 'date')

#         if date_from and date_to:
#             queryset = queryset.filter(date__range=(date_from, date_to))
#         elif date_from:
#             queryset = queryset.filter(date__gte=date_from)
#         elif date_to:
#             queryset = queryset.filter(date__lte=date_to)

#         # Additional filters
#         collection_type = self.request.GET.get('collection_type')
#         if collection_type:
#             queryset = queryset.filter(collection_type=collection_type)

#         vendor_name = self.request.GET.get('vendor_name')
#         if vendor_name:
#             queryset = queryset.filter(vendor_name=vendor_name)

#         sales_rep_slug = self.request.GET.get('sales_rep')
#         if sales_rep_slug:
#             queryset = queryset.filter(sales_rep__slug=sales_rep_slug)

#         transformer_slug = self.request.GET.get('transformer')
#         if transformer_slug:
#             queryset = queryset.filter(transformer__slug=transformer_slug)

#         return queryset.select_related(
#             'sales_rep', 'transformer', 'transformer__feeder', 
#             'transformer__feeder__business_district', 
#             'transformer__feeder__business_district__state'
#         )

#     def perform_create(self, serializer):
#         """Override to add any additional logic during creation"""
#         serializer.save()

#     @action(detail=False, methods=['get'])
#     def summary(self, request):
#         """Get collection summary with aggregations"""
#         queryset = self.get_queryset()
        
#         # Basic aggregations
#         summary_data = queryset.aggregate(
#             total_amount=Sum('amount'),
#             total_collections=Count('id'),
#             avg_collection=Avg('amount')
#         )

#         # Group by collection type
#         by_type = queryset.values('collection_type').annotate(
#             total=Sum('amount'),
#             count=Count('id')
#         ).order_by('-total')

#         # Group by vendor
#         by_vendor = queryset.values('vendor_name').annotate(
#             total=Sum('amount'),
#             count=Count('id')
#         ).order_by('-total')

#         # Group by sales rep
#         by_sales_rep = queryset.values(
#             'sales_rep__name', 'sales_rep__slug'
#         ).annotate(
#             total=Sum('amount'),
#             count=Count('id')
#         ).order_by('-total')

#         return Response({
#             'summary': summary_data,
#             'by_collection_type': by_type,
#             'by_vendor': by_vendor,
#             'by_sales_rep': by_sales_rep
#         })

class DailyCollectionViewSet(viewsets.ModelViewSet):
    serializer_class = DailyCollectionSerializer

    def get_queryset(self):
        queryset = DailyCollection.objects.all()
        # queryset = self.filter_by_location(queryset)
        date_from, date_to = get_date_range_from_request(self.request, 'date')

        if date_from and date_to:
            queryset = queryset.filter(date__range=(date_from, date_to))
        elif date_from:
            queryset = queryset.filter(date__gte=date_from)
        elif date_to:
            queryset = queryset.filter(date__lte=date_to)

        # Additional filters
        collection_type = self.request.GET.get('collection_type')
        if collection_type:
            queryset = queryset.filter(collection_type=collection_type)

        vendor_name = self.request.GET.get('vendor_name')
        if vendor_name:
            queryset = queryset.filter(vendor_name=vendor_name)

        sales_rep_slug = self.request.GET.get('sales_rep')
        if sales_rep_slug:
            queryset = queryset.filter(sales_rep__slug=sales_rep_slug)

        transformer_slug = self.request.GET.get('transformer')
        if transformer_slug:
            queryset = queryset.filter(transformer__slug=transformer_slug)

        return queryset.select_related(
            'sales_rep', 'transformer', 'transformer__feeder', 
            'transformer__feeder__business_district', 
            'transformer__feeder__business_district__state'
        )

    def perform_create(self, serializer):
        """Override to add any additional logic during creation"""
        serializer.save()

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get collection summary with aggregations"""
        queryset = self.get_queryset()
        
        # Basic aggregations
        summary_data = queryset.aggregate(
            total_amount=Sum('amount'),
            total_collections=Count('id'),
            avg_collection=Avg('amount')
        )

        # Group by collection type
        by_type = queryset.values('collection_type').annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by vendor
        by_vendor = queryset.values('vendor_name').annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by sales rep
        by_sales_rep = queryset.values(
            'sales_rep__name', 'sales_rep__slug'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        return Response({
            'summary': summary_data,
            'by_collection_type': by_type,
            'by_vendor': by_vendor,
            'by_sales_rep': by_sales_rep
        })

    # === COLLECTION TOOL INTEGRATION METHODS ===
    
    def resolve_sales_rep_id_to_uuid(self, sales_rep_id):
        """Convert sales_rep_id to SalesRepresentative UUID"""
        if not sales_rep_id or sales_rep_id.strip() == '':
            return None
            
        slug = sales_rep_id.strip()
        try:
            sales_rep = SalesRepresentative.objects.get(slug__iexact=slug)
            print(f"Sales rep '{slug}' resolved to PK: {sales_rep.id}")
            return sales_rep.id
        except SalesRepresentative.DoesNotExist:
            print(f"Sales rep '{slug}' not found")
            return None

    def resolve_dt_id_to_uuid(self, dt_id):
        """Convert dt_id to DistributionTransformer UUID"""
        if not dt_id or dt_id.strip() == '':
            return None
            
        slug = dt_id.strip()
        try:
            transformer = DistributionTransformer.objects.get(slug__iexact=slug)
            print(f"Transformer '{slug}' resolved to PK: {transformer.id}")
            return transformer.id
        except DistributionTransformer.DoesNotExist:
            print(f"Transformer '{slug}' not found")
            return None

    def validate_sales_rep_transformer_assignment(self, sales_rep_id, transformer_id):
        """Validate that sales_rep is assigned to the transformer"""
        try:
            sales_rep = SalesRepresentative.objects.get(id=sales_rep_id)
            transformer = DistributionTransformer.objects.get(id=transformer_id)
            
            if not sales_rep.assigned_transformers.filter(id=transformer_id).exists():
                return False, f"Sales rep '{sales_rep.slug}' is not assigned to transformer '{transformer.slug}'"
            return True, None
        except (SalesRepresentative.DoesNotExist, DistributionTransformer.DoesNotExist):
            return False, "Sales rep or transformer not found"

    def resolve_foreign_keys(self, collection_item):
        """Resolve all foreign key references from IDs to UUIDs"""
        resolved_data = collection_item.copy()
        
        # Resolve sales_rep_id
        sales_rep_id = collection_item.get('sales_rep_id')
        if sales_rep_id:
            sales_rep_pk = self.resolve_sales_rep_id_to_uuid(sales_rep_id)
            if not sales_rep_pk:
                raise ValueError(f"Sales rep ID '{sales_rep_id}' could not be resolved")
            resolved_data['sales_rep'] = sales_rep_pk
        else:
            resolved_data['sales_rep'] = None
        
        # Remove sales_rep_id from final data
        resolved_data.pop('sales_rep_id', None)
        
        # Resolve dt_id to transformer
        dt_id = collection_item.get('dt_id')
        if dt_id:
            transformer_pk = self.resolve_dt_id_to_uuid(dt_id)
            if not transformer_pk:
                raise ValueError(f"Transformer ID '{dt_id}' could not be resolved")
            resolved_data['transformer'] = transformer_pk
        else:
            resolved_data['transformer'] = None
            
        # Remove dt_id from final data
        resolved_data.pop('dt_id', None)
        
        # Validate assignment during CREATE only
        if resolved_data.get('sales_rep') and resolved_data.get('transformer'):
            is_valid, error_msg = self.validate_sales_rep_transformer_assignment(
                resolved_data['sales_rep'], resolved_data['transformer']
            )
            # if not is_valid:
            #     raise ValueError(error_msg)
        
        return resolved_data

    @action(detail=False, methods=['post'], url_path='upsert-external')
    def upsert_external(self, request):
        external_id = request.data.get("external_id")
        if not external_id:
            return Response({"error": "external_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        instance = DailyCollection.objects.filter(external_id=external_id).first()
        serializer = self.get_serializer(instance, data=request.data, partial=bool(instance))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK if instance else status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='bulk_create')
    def bulk_create(self, request):
        collection_data = request.data.get('collections', [])
        print(f"Received {len(collection_data)} collection records for bulk create")
        if not collection_data:
            return Response({'error': 'No collection data provided'}, status=status.HTTP_400_BAD_REQUEST)

        created, updated, errors = [], [], []
        with transaction.atomic():
            for idx, collection_item in enumerate(collection_data):
                try:
                    print(f"Processing collection item {idx}: {collection_item}")
                    
                    # Normalize date
                    if isinstance(collection_item.get('date'), str) and 'T' in collection_item['date']:
                        collection_item['date'] = collection_item['date'].split('T')[0]

                    # Smart search strategy
                    existing = None
                    external_id = collection_item.get('external_id')
                    
                    if external_id:
                        # Search by external_id + date
                        precise_search = {
                            'external_id': external_id,
                            'date': collection_item.get('date')
                        }
                        print(f"Searching by external_id + date: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        search_criteria = {
                            'date': collection_item.get('date'),
                            'sales_rep__slug': collection_item.get('sales_rep_id'),
                            'transformer__slug': collection_item.get('dt_id', ''),
                            'collection_type': collection_item.get('collection_type', ''),
                            'vendor_name': collection_item.get('vendor_name', '')
                        }
                        print(f"Searching by composite key: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()
                    
                    # Resolve foreign keys
                    try:
                        resolved_data = self.resolve_foreign_keys(collection_item)
                        print(f"Resolved foreign keys: sales_rep={resolved_data.get('sales_rep')}, transformer={resolved_data.get('transformer')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': collection_item, 'errors': str(e)})
                        continue
                    
                    if existing:
                        print(f"UPDATING existing collection ID: {existing.id}")
                        serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            updated.append(serializer.data)
                        else:
                            errors.append({'index': idx, 'data': collection_item, 'errors': serializer.errors})
                    else:
                        print(f"CREATING new collection record")
                        serializer = self.get_serializer(data=resolved_data)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            created.append(serializer.data)
                        else:
                            errors.append({'index': idx, 'data': collection_item, 'errors': serializer.errors})
                            
                except Exception as e:
                    print(f"Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': collection_item, 'errors': str(e)})

        response_data = {'created': len(created), 'updated': len(updated), 'errors': len(errors),
                         'created_data': created, 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'], url_path='bulk_update')
    def bulk_update(self, request):
        collection_data = request.data.get('collections', [])
        print(f"Received {len(collection_data)} collection records for bulk update")
        if not collection_data:
            return Response({'error': 'No collection data provided'}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []
        with transaction.atomic():
            for idx, collection_item in enumerate(collection_data):
                try:
                    print(f"Processing UPDATE collection item {idx}: {collection_item}")
                    
                    # Normalize dates
                    comp = collection_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('date'), str) and 'T' in comp['date']:
                        comp['date'] = comp['date'].split('T')[0]
                    if isinstance(collection_item.get('date'), str) and 'T' in collection_item['date']:
                        collection_item['date'] = collection_item['date'].split('T')[0]

                    # Smart search for UPDATE
                    existing = None
                    
                    if comp:
                        data_to_resolve = {**collection_item}
                        data_to_resolve.update(comp)
                        data_to_resolve.pop('_composite_key', None)
                    else:
                        data_to_resolve = collection_item
                    
                    external_id = data_to_resolve.get('external_id')
                    if external_id:
                        # Search by external_id + date
                        precise_search = {
                            'external_id': external_id,
                            'date': data_to_resolve.get('date')
                        }
                        print(f"UPDATE: Searching by external_id + date: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'date': comp.get('date'),
                                'sales_rep__slug': comp.get('sales_rep_id'),
                                'transformer__slug': comp.get('dt_id', ''),
                                'collection_type': comp.get('collection_type', ''),
                                'vendor_name': comp.get('vendor_name', '')
                            }
                            print(f"UPDATE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'date': collection_item.get('date'),
                                'sales_rep__slug': collection_item.get('sales_rep_id'),
                                'transformer__slug': collection_item.get('dt_id', ''),
                                'collection_type': collection_item.get('collection_type', ''),
                                'vendor_name': collection_item.get('vendor_name', '')
                            }
                            print(f"UPDATE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()

                    if not existing:
                        print(f"UPDATE: Collection not found for update")
                        errors.append({'index': idx, 'data': collection_item, 'errors': 'Collection not found for update'})
                        continue

                    # Resolve foreign keys
                    try:
                        resolved_data = self.resolve_foreign_keys(data_to_resolve)
                        print(f"UPDATE: Resolved foreign keys: sales_rep={resolved_data.get('sales_rep')}, transformer={resolved_data.get('transformer')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': collection_item, 'errors': str(e)})
                        continue

                    serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                    if serializer.is_valid():
                        saved_instance = serializer.save()
                        print(f"UPDATE: Successfully updated collection ID: {saved_instance.id}")
                        updated.append(serializer.data)
                    else:
                        print(f"UPDATE: Validation failed: {serializer.errors}")
                        errors.append({'index': idx, 'data': collection_item, 'errors': serializer.errors})
                        
                except Exception as e:
                    print(f"UPDATE Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': collection_item, 'errors': str(e)})

        response_data = {'updated': len(updated), 'errors': len(errors), 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'], url_path='bulk_delete')
    def bulk_delete(self, request):
        collection_data = request.data.get('collections', [])
        print(f"Received {len(collection_data)} collection records for bulk delete")
        if not collection_data:
            return Response({'error': 'No collection data provided'}, status=status.HTTP_400_BAD_REQUEST)

        deleted, errors = 0, []
        with transaction.atomic():
            for idx, collection_item in enumerate(collection_data):
                try:
                    print(f"Processing DELETE collection item {idx}: {collection_item}")
                    
                    # Normalize dates
                    comp = collection_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('date'), str) and 'T' in comp['date']:
                        comp['date'] = comp['date'].split('T')[0]
                    if isinstance(collection_item.get('date'), str) and 'T' in collection_item['date']:
                        collection_item['date'] = collection_item['date'].split('T')[0]

                    # Smart search for DELETE
                    existing = None
                    
                    if comp:
                        search_data = comp
                    else:
                        search_data = collection_item
                    
                    external_id = search_data.get('external_id')
                    if external_id:
                        # Search by external_id + date
                        precise_search = {
                            'external_id': external_id,
                            'date': search_data.get('date')
                        }
                        print(f"DELETE: Searching by external_id + date: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'date': comp.get('date'),
                                'sales_rep__slug': comp.get('sales_rep_id'),
                                'transformer__slug': comp.get('dt_id', ''),
                                'collection_type': comp.get('collection_type', ''),
                                'vendor_name': comp.get('vendor_name', '')
                            }
                            print(f"DELETE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'date': collection_item.get('date'),
                                'sales_rep__slug': collection_item.get('sales_rep_id'),
                                'transformer__slug': collection_item.get('dt_id', ''),
                                'collection_type': collection_item.get('collection_type', ''),
                                'vendor_name': collection_item.get('vendor_name', '')
                            }
                            print(f"DELETE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()

                    if existing:
                        existing.delete()
                        print(f"DELETE: Successfully deleted collection")
                        deleted += 1
                    else:
                        print(f"DELETE: Collection not found for deletion")
                        errors.append({'index': idx, 'data': collection_item, 'errors': 'Collection not found for deletion'})
                        
                except Exception as e:
                    print(f"DELETE Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': collection_item, 'errors': str(e)})

        response_data = {'deleted': deleted, 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)



class MonthlyEnergyBilledViewSet(FeederFilteredQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = MonthlyEnergyBilledSerializer

    def get_queryset(self):
        queryset = MonthlyEnergyBilled.objects.all()
        queryset = self.filter_by_location(queryset)
        month_from, month_to = get_date_range_from_request(self.request, 'month')

        if month_from and month_to:
            queryset = queryset.filter(month__range=(month_from, month_to))
        elif month_from:
            queryset = queryset.filter(month__gte=month_from)
        elif month_to:
            queryset = queryset.filter(month__lte=month_to)

        return queryset


class MonthlyCustomerStatsViewSet(FeederFilteredQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = MonthlyCustomerStatsSerializer

    def get_queryset(self):
        queryset = MonthlyCustomerStats.objects.all()
        queryset = self.filter_by_location(queryset)
        month_from, month_to = get_date_range_from_request(self.request, 'month')

        if month_from and month_to:
            queryset = queryset.filter(month__range=(month_from, month_to))
        elif month_from:
            queryset = queryset.filter(month__gte=month_from)
        elif month_to:
            queryset = queryset.filter(month__lte=month_to)

        return queryset


# Aggregated Metrics Endpoint
from rest_framework.views import APIView

class FeederMetricsView(APIView):
    def get(self, request):
        month_from = request.GET.get('month_from')
        month_to = request.GET.get('month_to')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        top_n = request.GET.get('top_n')
        bottom_n = request.GET.get('bottom_n')

        if month_from:
            month_from = datetime.strptime(month_from, "%Y-%m-%d").date()
        if month_to:
            month_to = datetime.strptime(month_to, "%Y-%m-%d").date()
        if date_from:
            date_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        if date_to:
            date_to = datetime.strptime(date_to, "%Y-%m-%d").date()

        feeders = get_filtered_feeders(request)
        metrics = []

        for feeder in feeders:
            energy_billed = MonthlyEnergyBilled.objects.filter(
                feeder=feeder,
                month__range=(month_from, month_to)
            ).aggregate(total=Sum('energy_mwh'))['total'] or 0

            energy_delivered = EnergyDelivered.objects.filter(
                feeder=feeder,
                date__range=(date_from, date_to)
            ).aggregate(total=Sum('energy_mwh'))['total'] or 0

            # revenue_collected = DailyRevenueCollected.objects.filter(
            #     feeder=feeder,
            #     date__range=(date_from, date_to)
            # ).aggregate(total=Sum('amount'))['total'] or 0
            revenue_collected=0

            revenue_billed = MonthlyRevenueBilled.objects.filter(
                feeder=feeder,
                month__range=(month_from, month_to)
            ).aggregate(total=Sum('amount'))['total'] or 0

            collection_eff = (revenue_collected / revenue_billed) * 100 if revenue_billed else 0
            billing_eff = (energy_billed / energy_delivered) * 100 if energy_delivered else 0
            atc_c = 100 - (billing_eff * collection_eff / 100)

            metrics.append({
                "feeder_id": feeder.id,
                "feeder": feeder.name,
                "energy_billed": energy_billed,
                "energy_delivered": energy_delivered,
                "revenue_collected": revenue_collected,
                "revenue_billed": revenue_billed,
                "collection_efficiency": round(collection_eff, 2),
                "billing_efficiency": round(billing_eff, 2),
                "atc_c": round(atc_c, 2),
            })

        # Sort and slice by ATC&C
        metrics.sort(key=lambda x: x['atc_c'])
        if top_n:
            metrics = metrics[:int(top_n)]
        elif bottom_n:
            metrics = metrics[-int(bottom_n):]

        return Response(metrics)    


class SalesRepresentativeViewSet(viewsets.ModelViewSet):
    queryset = SalesRepresentative.objects.all()
    serializer_class = SalesRepresentativeSerializer


# class SalesRepPerformanceViewSet(viewsets.ModelViewSet):
#     serializer_class = SalesRepPerformanceSerializer

#     def get_queryset(self):
#         qs = SalesRepPerformance.objects.all()
#         sales_rep_slug = self.request.GET.get('sales_rep')

#         if sales_rep_slug:
#             qs = qs.filter(sales_rep__slug=sales_rep_slug)

#         month_from, month_to = get_date_range_from_request(self.request, 'month')
#         if month_from and month_to:
#             qs = qs.filter(month__range=(month_from, month_to))
#         elif month_from:
#             qs = qs.filter(month__gte=month_from)
#         elif month_to:
#             qs = qs.filter(month__lte=month_to)

#         return qs

class SalesRepPerformanceViewSet(viewsets.ModelViewSet):
    serializer_class = SalesRepPerformanceSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['sales_rep', 'transformer', 'month']

    def get_queryset(self):
        qs = SalesRepPerformance.objects.all()
        sales_rep_slug = self.request.GET.get('sales_rep')

        if sales_rep_slug:
            qs = qs.filter(sales_rep__slug=sales_rep_slug)

        month_from, month_to = get_date_range_from_request(self.request, 'month')
        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)

        return qs

    # === COLLECTION TOOL INTEGRATION METHODS ===
    
    def resolve_sales_rep_id_to_uuid(self, sales_rep_id):
        """Convert sales_rep_id to SalesRepresentative UUID"""
        if not sales_rep_id or sales_rep_id.strip() == '':
            return None
            
        slug = sales_rep_id.strip()
        try:
            sales_rep = SalesRepresentative.objects.get(slug__iexact=slug)
            print(f"Sales rep '{slug}' resolved to PK: {sales_rep.id}")
            return sales_rep.id
        except SalesRepresentative.DoesNotExist:
            print(f"Sales rep '{slug}' not found")
            return None

    def resolve_dt_id_to_uuid(self, dt_id):
        """Convert dt_id to DistributionTransformer UUID"""
        if not dt_id or dt_id.strip() == '':
            return None
            
        slug = dt_id.strip()
        try:
            transformer = DistributionTransformer.objects.get(slug__iexact=slug)
            print(f"Transformer '{slug}' resolved to PK: {transformer.id}")
            return transformer.id
        except DistributionTransformer.DoesNotExist:
            print(f"Transformer '{slug}' not found")
            return None

    def validate_sales_rep_transformer_assignment(self, sales_rep_id, transformer_id):
        """Validate that sales_rep is assigned to the transformer"""
        try:
            sales_rep = SalesRepresentative.objects.get(id=sales_rep_id)
            transformer = DistributionTransformer.objects.get(id=transformer_id)
            
            if not sales_rep.assigned_transformers.filter(id=transformer_id).exists():
                return False, f"Sales rep '{sales_rep.slug}' is not assigned to transformer '{transformer.slug}'"
            return True, None
        except (SalesRepresentative.DoesNotExist, DistributionTransformer.DoesNotExist):
            return False, "Sales rep or transformer not found"

    def resolve_foreign_keys(self, performance_item):
        """Resolve all foreign key references from IDs to UUIDs"""
        resolved_data = performance_item.copy()
        
        # Resolve sales_rep_id
        sales_rep_id = performance_item.get('sales_rep_id')
        if sales_rep_id:
            sales_rep_pk = self.resolve_sales_rep_id_to_uuid(sales_rep_id)
            if not sales_rep_pk:
                raise ValueError(f"Sales rep ID '{sales_rep_id}' could not be resolved")
            resolved_data['sales_rep'] = sales_rep_pk
        else:
            resolved_data['sales_rep'] = None
        
        # Remove sales_rep_id from final data
        resolved_data.pop('sales_rep_id', None)
        
        # Resolve dt_id to transformer
        dt_id = performance_item.get('dt_id')
        if dt_id:
            transformer_pk = self.resolve_dt_id_to_uuid(dt_id)
            if not transformer_pk:
                raise ValueError(f"Transformer ID '{dt_id}' could not be resolved")
            resolved_data['transformer'] = transformer_pk
        else:
            resolved_data['transformer'] = None
            
        # Remove dt_id from final data
        resolved_data.pop('dt_id', None)
        
        # Validate assignment during CREATE only
        if resolved_data.get('sales_rep') and resolved_data.get('transformer'):
            is_valid, error_msg = self.validate_sales_rep_transformer_assignment(
                resolved_data['sales_rep'], resolved_data['transformer']
            )
            if not is_valid:
                raise ValueError(error_msg)
        
        return resolved_data

    @action(detail=False, methods=['post'], url_path='upsert-external')
    def upsert_external(self, request):
        external_id = request.data.get("external_id")
        if not external_id:
            return Response({"error": "external_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        instance = SalesRepPerformance.objects.filter(external_id=external_id).first()
        serializer = self.get_serializer(instance, data=request.data, partial=bool(instance))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK if instance else status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='bulk_create')
    def bulk_create(self, request):
        performance_data = request.data.get('performances', [])
        print(f"Received {len(performance_data)} performance records for bulk create")
        if not performance_data:
            return Response({'error': 'No performance data provided'}, status=status.HTTP_400_BAD_REQUEST)

        created, updated, errors = [], [], []
        with transaction.atomic():
            for idx, performance_item in enumerate(performance_data):
                try:
                    print(f"Processing performance item {idx}: {performance_item}")
                    
                    # Normalize date
                    if isinstance(performance_item.get('month'), str) and 'T' in performance_item['month']:
                        performance_item['month'] = performance_item['month'].split('T')[0]

                    # Smart search strategy
                    existing = None
                    external_id = performance_item.get('external_id')
                    
                    if external_id:
                        # Search by external_id + month
                        precise_search = {
                            'external_id': external_id,
                            'month': performance_item.get('month')
                        }
                        print(f"Searching by external_id + month: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        search_criteria = {
                            'month': performance_item.get('month'),
                            'sales_rep__slug': performance_item.get('sales_rep_id'),
                            'transformer__slug': performance_item.get('dt_id', '')
                        }
                        print(f"Searching by composite key: {search_criteria}")
                        existing = self.get_queryset().filter(**search_criteria).first()
                    
                    # Resolve foreign keys
                    try:
                        resolved_data = self.resolve_foreign_keys(performance_item)
                        print(f"Resolved foreign keys: sales_rep={resolved_data.get('sales_rep')}, transformer={resolved_data.get('transformer')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': performance_item, 'errors': str(e)})
                        continue
                    
                    if existing:
                        print(f"UPDATING existing performance ID: {existing.id}")
                        serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            updated.append(serializer.data)
                        else:
                            errors.append({'index': idx, 'data': performance_item, 'errors': serializer.errors})
                    else:
                        print(f"CREATING new performance record")
                        serializer = self.get_serializer(data=resolved_data)
                        if serializer.is_valid():
                            saved_instance = serializer.save()
                            created.append(serializer.data)
                        else:
                            errors.append({'index': idx, 'data': performance_item, 'errors': serializer.errors})
                            
                except Exception as e:
                    print(f"Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': performance_item, 'errors': str(e)})

        response_data = {'created': len(created), 'updated': len(updated), 'errors': len(errors),
                         'created_data': created, 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'], url_path='bulk_update')
    def bulk_update(self, request):
        performance_data = request.data.get('performances', [])
        print(f"Received {len(performance_data)} performance records for bulk update")
        if not performance_data:
            return Response({'error': 'No performance data provided'}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []
        with transaction.atomic():
            for idx, performance_item in enumerate(performance_data):
                try:
                    print(f"Processing UPDATE performance item {idx}: {performance_item}")
                    
                    # Normalize dates
                    comp = performance_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('month'), str) and 'T' in comp['month']:
                        comp['month'] = comp['month'].split('T')[0]
                    if isinstance(performance_item.get('month'), str) and 'T' in performance_item['month']:
                        performance_item['month'] = performance_item['month'].split('T')[0]

                    # Smart search for UPDATE
                    existing = None
                    
                    if comp:
                        data_to_resolve = {**performance_item}
                        data_to_resolve.update(comp)
                        data_to_resolve.pop('_composite_key', None)
                    else:
                        data_to_resolve = performance_item
                    
                    external_id = data_to_resolve.get('external_id')
                    if external_id:
                        # Search by external_id + month
                        precise_search = {
                            'external_id': external_id,
                            'month': data_to_resolve.get('month')
                        }
                        print(f"UPDATE: Searching by external_id + month: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'month': comp.get('month'),
                                'sales_rep__slug': comp.get('sales_rep_id'),
                                'transformer__slug': comp.get('dt_id', '')
                            }
                            print(f"UPDATE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'month': performance_item.get('month'),
                                'sales_rep__slug': performance_item.get('sales_rep_id'),
                                'transformer__slug': performance_item.get('dt_id', '')
                            }
                            print(f"UPDATE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()

                    if not existing:
                        print(f"UPDATE: Performance not found for update")
                        errors.append({'index': idx, 'data': performance_item, 'errors': 'Performance not found for update'})
                        continue

                    # Resolve foreign keys
                    try:
                        resolved_data = self.resolve_foreign_keys(data_to_resolve)
                        print(f"UPDATE: Resolved foreign keys: sales_rep={resolved_data.get('sales_rep')}, transformer={resolved_data.get('transformer')}")
                    except ValueError as e:
                        errors.append({'index': idx, 'data': performance_item, 'errors': str(e)})
                        continue

                    serializer = self.get_serializer(existing, data=resolved_data, partial=True)
                    if serializer.is_valid():
                        saved_instance = serializer.save()
                        print(f"UPDATE: Successfully updated performance ID: {saved_instance.id}")
                        updated.append(serializer.data)
                    else:
                        print(f"UPDATE: Validation failed: {serializer.errors}")
                        errors.append({'index': idx, 'data': performance_item, 'errors': serializer.errors})
                        
                except Exception as e:
                    print(f"UPDATE Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': performance_item, 'errors': str(e)})

        response_data = {'updated': len(updated), 'errors': len(errors), 'updated_data': updated}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'], url_path='bulk_delete')
    def bulk_delete(self, request):
        performance_data = request.data.get('performances', [])
        print(f"Received {len(performance_data)} performance records for bulk delete")
        if not performance_data:
            return Response({'error': 'No performance data provided'}, status=status.HTTP_400_BAD_REQUEST)

        deleted, errors = 0, []
        with transaction.atomic():
            for idx, performance_item in enumerate(performance_data):
                try:
                    print(f"Processing DELETE performance item {idx}: {performance_item}")
                    
                    # Normalize dates
                    comp = performance_item.get('_composite_key', {})
                    if comp and isinstance(comp.get('month'), str) and 'T' in comp['month']:
                        comp['month'] = comp['month'].split('T')[0]
                    if isinstance(performance_item.get('month'), str) and 'T' in performance_item['month']:
                        performance_item['month'] = performance_item['month'].split('T')[0]

                    # Smart search for DELETE
                    existing = None
                    
                    if comp:
                        search_data = comp
                    else:
                        search_data = performance_item
                    
                    external_id = search_data.get('external_id')
                    if external_id:
                        # Search by external_id + month
                        precise_search = {
                            'external_id': external_id,
                            'month': search_data.get('month')
                        }
                        print(f"DELETE: Searching by external_id + month: {precise_search}")
                        existing = self.get_queryset().filter(**precise_search).first()
                    
                    # Fallback to composite key search
                    if not existing:
                        if comp:
                            search_criteria = {
                                'month': comp.get('month'),
                                'sales_rep__slug': comp.get('sales_rep_id'),
                                'transformer__slug': comp.get('dt_id', '')
                            }
                            print(f"DELETE: Searching by composite key (comp): {search_criteria}")
                        else:
                            search_criteria = {
                                'month': performance_item.get('month'),
                                'sales_rep__slug': performance_item.get('sales_rep_id'),
                                'transformer__slug': performance_item.get('dt_id', '')
                            }
                            print(f"DELETE: Searching by composite key (direct): {search_criteria}")
                        
                        existing = self.get_queryset().filter(**search_criteria).first()

                    if existing:
                        existing.delete()
                        print(f"DELETE: Successfully deleted performance")
                        deleted += 1
                    else:
                        print(f"DELETE: Performance not found for deletion")
                        errors.append({'index': idx, 'data': performance_item, 'errors': 'Performance not found for deletion'})
                        
                except Exception as e:
                    print(f"DELETE Exception at {idx}: {e}")
                    errors.append({'index': idx, 'data': performance_item, 'errors': str(e)})

        response_data = {'deleted': deleted, 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)
