# hr/views/executive_kpis/performance_views.py
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from datetime import datetime
from decimal import Decimal

from ...models import ExecutiveKPIDefinition, ExecutivePerformance
from ...utils.kpi_utils import KPICalculator, KPIAlertManager
from ...serializers import KPIPerformanceUpdateSerializer


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_kpi_performance(request):
    """
    Update KPI performance data
    Expected payload:
    {
        "kpi_id": "uuid",
        "actual_value": 85.5,
        "period_date": "2025-09-01",
        "period_type": "monthly",
        "notes": "Performance improved due to...",
        "data_source": "manual_entry"
    }
    """
    try:
        # Validate input data
        serializer = KPIPerformanceUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors,
                'message': 'Invalid input data'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        validated_data = serializer.validated_data
        
        # Get KPI definition
        try:
            kpi_definition = ExecutiveKPIDefinition.objects.get(
                id=validated_data['kpi_id'],
                is_active=True
            )
        except ExecutiveKPIDefinition.DoesNotExist:
            return Response({
                'success': False,
                'error': 'KPI definition not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Create or update performance record
        performance, created = ExecutivePerformance.objects.update_or_create(
            kpi_definition=kpi_definition,
            period_date=validated_data['period_date'],
            period_type=validated_data.get('period_type', 'monthly'),
            state_id=validated_data.get('state'),
            business_district_id=validated_data.get('business_district'),
            defaults={
                'actual_value': validated_data['actual_value'],
                'notes': validated_data.get('notes', ''),
                'data_source': validated_data.get('data_source', 'api'),
                'created_by': request.user
            }
        )
        
        # Calculate status info
        status_info = KPICalculator.get_status_info(
            performance.actual_value, 
            kpi_definition
        )
        
        # Check for alerts
        alerts = KPIAlertManager.check_and_create_alerts(performance)
        
        return Response({
            'success': True,
            'data': {
                'id': str(performance.id),
                'created': created,
                'performance': {
                    'actual_value': float(performance.actual_value),
                    'progress': status_info['progress'],
                    'status': status_info['status_display'],
                    'period_date': performance.period_date.isoformat()
                },
                'alerts_created': len(alerts)
            },
            'message': f'KPI performance {"created" if created else "updated"} successfully'
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to update KPI performance'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def kpi_performance_history(request, kpi_id):
    """
    Get performance history for a specific KPI
    Query params:
    - months: number of months of history (default: 12)
    - period_type: monthly, quarterly, annually (default: monthly)
    """
    try:
        months = int(request.GET.get('months', 12))
        period_type = request.GET.get('period_type', 'monthly')
        
        # Get KPI definition
        try:
            kpi_definition = ExecutiveKPIDefinition.objects.get(
                id=kpi_id,
                is_active=True
            )
        except ExecutiveKPIDefinition.DoesNotExist:
            return Response({
                'success': False,
                'error': 'KPI definition not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Get performance history
        performances = ExecutivePerformance.objects.filter(
            kpi_definition=kpi_definition,
            period_type=period_type
        ).order_by('-period_date')[:months]
        
        history_data = []
        for performance in reversed(performances):  # Reverse to get chronological order
            status_info = KPICalculator.get_status_info(
                performance.actual_value,
                kpi_definition
            )
            
            history_data.append({
                'period_date': performance.period_date.isoformat(),
                'actual_value': float(performance.actual_value),
                'progress': status_info['progress'],
                'status': status_info['status'],
                'verified': performance.verified,
                'notes': performance.notes,
                'data_source': performance.data_source
            })
        
        return Response({
            'success': True,
            'data': {
                'kpi_info': {
                    'id': str(kpi_definition.id),
                    'name': kpi_definition.name,
                    'executive_role': kpi_definition.executive_role,
                    'target': kpi_definition.get_target_display(),
                    'unit': kpi_definition.unit
                },
                'history': history_data
            },
            'meta': {
                'months': months,
                'period_type': period_type,
                'records_count': len(history_data)
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to retrieve KPI performance history'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_kpi_performance(request, performance_id):
    """
    Delete a specific performance record
    """
    try:
        # Get performance record
        try:
            performance = ExecutivePerformance.objects.get(id=performance_id)
        except ExecutivePerformance.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Performance record not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Store info for response
        kpi_name = performance.kpi_definition.name
        period_date = performance.period_date.isoformat()
        
        # Delete the record
        performance.delete()
        
        return Response({
            'success': True,
            'message': f'Performance record deleted for {kpi_name} ({period_date})'
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to delete performance record'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_update_performance(request):
    """
    Bulk update multiple KPI performances at once
    Expected payload:
    {
        "performances": [
            {
                "kpi_id": "uuid",
                "actual_value": 85.5,
                "period_date": "2025-09-01",
                "period_type": "monthly"
            },
            ...
        ]
    }
    """
    try:
        performances_data = request.data.get('performances', [])
        
        if not performances_data:
            return Response({
                'success': False,
                'error': 'No performance data provided'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        results = []
        errors = []
        
        for i, performance_data in enumerate(performances_data):
            try:
                # Validate individual performance data
                serializer = KPIPerformanceUpdateSerializer(data=performance_data)
                if not serializer.is_valid():
                    errors.append({
                        'index': i,
                        'errors': serializer.errors
                    })
                    continue
                
                validated_data = serializer.validated_data
                
                # Get KPI definition
                kpi_definition = ExecutiveKPIDefinition.objects.get(
                    id=validated_data['kpi_id'],
                    is_active=True
                )
                
                # Create or update performance record
                performance, created = ExecutivePerformance.objects.update_or_create(
                    kpi_definition=kpi_definition,
                    period_date=validated_data['period_date'],
                    period_type=validated_data.get('period_type', 'monthly'),
                    defaults={
                        'actual_value': validated_data['actual_value'],
                        'notes': validated_data.get('notes', ''),
                        'data_source': validated_data.get('data_source', 'bulk_api'),
                        'created_by': request.user
                    }
                )
                
                results.append({
                    'index': i,
                    'kpi_name': kpi_definition.name,
                    'created': created,
                    'performance_id': str(performance.id)
                })
                
            except ExecutiveKPIDefinition.DoesNotExist:
                errors.append({
                    'index': i,
                    'error': 'KPI definition not found'
                })
            except Exception as e:
                errors.append({
                    'index': i,
                    'error': str(e)
                })
        
        return Response({
            'success': len(errors) == 0,
            'data': {
                'successful': results,
                'errors': errors
            },
            'summary': {
                'total_attempted': len(performances_data),
                'successful': len(results),
                'failed': len(errors)
            }
        }, status=status.HTTP_200_OK if len(errors) == 0 else status.HTTP_207_MULTI_STATUS)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to process bulk performance update'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)