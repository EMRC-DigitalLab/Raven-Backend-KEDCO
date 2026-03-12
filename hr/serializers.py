# hr/serializers.py
from decimal import Decimal

from rest_framework import serializers

from .models import (
    Department,
    ExecutiveKPIAlert,
    ExecutiveKPIDefinition,
    ExecutivePerformance,
    Role,
    Staff,
)
from .utils.kpi_utils import KPICalculator


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = '__all__'


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = '__all__'


class StaffSerializer(serializers.ModelSerializer):
    age = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = Staff
        fields = '__all__'

    def get_age(self, obj):
        return obj.age()

    def get_is_active(self, obj):
        return obj.is_active()


class ExecutiveKPIDefinitionSerializer(serializers.ModelSerializer):
    """Serializer for ExecutiveKPIDefinition model"""
    
    target_display = serializers.SerializerMethodField()
    progress_calculation_method = serializers.SerializerMethodField()
    
    class Meta:
        model = ExecutiveKPIDefinition
        fields = [
            'id', 'executive_role', 'category', 'name', 'description',
            'data_type', 'unit', 'priority', 'target_value', 'target_min', 
            'target_max', 'is_range_target', 'is_reverse_polarity',
            'deadline', 'measurement_frequency', 'is_active',
            'target_display', 'progress_calculation_method',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'target_display', 'progress_calculation_method']
    
    def get_target_display(self, obj):
        """Get formatted target display string"""
        return obj.get_target_display()
    
    def get_progress_calculation_method(self, obj):
        """Describe how progress is calculated for this KPI"""
        if obj.is_range_target:
            if obj.is_reverse_polarity:
                return f"Lower is better. Target range: {obj.target_min}-{obj.target_max}{obj.unit}"
            else:
                return f"Higher is better. Target range: {obj.target_min}-{obj.target_max}{obj.unit}"
        else:
            if obj.is_reverse_polarity:
                return f"Lower is better. Target: {obj.target_value}{obj.unit}"
            else:
                return f"Higher is better. Target: {obj.target_value}{obj.unit}"


class ExecutivePerformanceSerializer(serializers.ModelSerializer):
    """Serializer for ExecutivePerformance model"""
    
    kpi_name = serializers.CharField(source='kpi_definition.name', read_only=True)
    kpi_unit = serializers.CharField(source='kpi_definition.unit', read_only=True)
    executive_role = serializers.CharField(source='kpi_definition.executive_role', read_only=True)
    progress_percentage = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    status_info = serializers.SerializerMethodField()
    target_variance = serializers.SerializerMethodField()
    formatted_value = serializers.SerializerMethodField()
    
    class Meta:
        model = ExecutivePerformance
        fields = [
            'id', 'kpi_definition', 'kpi_name', 'kpi_unit', 'executive_role',
            'period_date', 'period_type', 'actual_value', 'progress_percentage',
            'status', 'status_info', 'target_variance', 'formatted_value',
            'state', 'business_district', 'notes', 'data_source', 
            'verified', 'verified_by', 'verified_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'kpi_name', 'kpi_unit', 'executive_role', 'progress_percentage',
            'status', 'status_info', 'target_variance', 'formatted_value',
            'created_at', 'updated_at'
        ]
    
    def get_progress_percentage(self, obj):
        """Get progress percentage"""
        return obj.progress_percentage
    
    def get_status(self, obj):
        """Get current status"""
        return obj.status
    
    def get_status_info(self, obj):
        """Get comprehensive status information"""
        return KPICalculator.get_status_info(obj.actual_value, obj.kpi_definition)
    
    def get_target_variance(self, obj):
        """Get variance from target"""
        return obj.target_variance
    
    def get_formatted_value(self, obj):
        """Get formatted value for display"""
        return KPICalculator.format_kpi_value(obj.actual_value, obj.kpi_definition)


class ExecutiveKPIAlertSerializer(serializers.ModelSerializer):
    """Serializer for ExecutiveKPIAlert model"""
    
    kpi_name = serializers.CharField(source='kpi_definition.name', read_only=True)
    executive_role = serializers.CharField(source='kpi_definition.executive_role', read_only=True)
    alert_type_display = serializers.CharField(source='get_alert_type_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    acknowledged_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = ExecutiveKPIAlert
        fields = [
            'id', 'kpi_definition', 'kpi_name', 'executive_role',
            'alert_type', 'alert_type_display', 'message',
            'severity', 'severity_display', 'is_active', 'acknowledged',
            'acknowledged_by', 'acknowledged_by_name', 'acknowledged_at',
            'created_at'
        ]
        read_only_fields = [
            'id', 'kpi_name', 'executive_role', 'alert_type_display',
            'severity_display', 'acknowledged_by_name', 'created_at'
        ]
    
    def get_acknowledged_by_name(self, obj):
        """Get name of user who acknowledged the alert"""
        if obj.acknowledged_by:
            return f"{obj.acknowledged_by.first_name} {obj.acknowledged_by.last_name}".strip() or obj.acknowledged_by.username
        return None


class StaffWithExecutiveRoleSerializer(serializers.ModelSerializer):
    """Extended Staff serializer including executive role fields"""
    
    department_name = serializers.CharField(source='department.name', read_only=True)
    role_title = serializers.CharField(source='role.title', read_only=True)
    state_name = serializers.CharField(source='state.name', read_only=True)
    district_name = serializers.CharField(source='district.name', read_only=True)
    executive_role_display = serializers.CharField(source='get_executive_role_display', read_only=True)
    is_executive = serializers.SerializerMethodField()
    active_kpis_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Staff
        fields = [
            'id', 'full_name', 'email', 'phone_number', 'gender', 'birth_date',
            'salary', 'hire_date', 'exit_date', 'grade', 'is_active',
            'department', 'department_name', 'role', 'role_title',
            'state', 'state_name', 'district', 'district_name',
            'executive_role', 'executive_role_display', 'is_executive',
            'kpi_targets_set', 'performance_review_frequency', 'active_kpis_count'
        ]
        read_only_fields = [
            'id', 'department_name', 'role_title', 'state_name', 'district_name',
            'executive_role_display', 'is_executive', 'active_kpis_count'
        ]
    
    def get_is_executive(self, obj):
        """Check if staff member is an executive"""
        return bool(obj.executive_role)
    
    def get_active_kpis_count(self, obj):
        """Get count of active KPIs for this executive"""
        if obj.executive_role:
            return ExecutiveKPIDefinition.objects.filter(
                executive_role=obj.executive_role,
                is_active=True
            ).count()
        return 0


# Nested serializers for comprehensive API responses
class KPIWithLatestPerformanceSerializer(serializers.ModelSerializer):
    """KPI with its latest performance data"""
    
    latest_performance = serializers.SerializerMethodField()
    target_display = serializers.SerializerMethodField()
    status_summary = serializers.SerializerMethodField()
    
    class Meta:
        model = ExecutiveKPIDefinition
        fields = [
            'id', 'name', 'description', 'priority', 'unit', 'deadline',
            'target_display', 'status_summary', 'latest_performance'
        ]
        read_only_fields = ['id', 'target_display', 'status_summary', 'latest_performance']
    
    def get_latest_performance(self, obj):
        """Get latest performance record"""
        latest = ExecutivePerformance.objects.filter(
            kpi_definition=obj
        ).order_by('-period_date').first()
        
        if latest:
            return {
                'actual_value': float(latest.actual_value),
                'period_date': latest.period_date.isoformat(),
                'progress': latest.progress_percentage,
                'status': latest.status,
                'verified': latest.verified
            }
        return None
    
    def get_target_display(self, obj):
        """Get formatted target display"""
        return obj.get_target_display()
    
    def get_status_summary(self, obj):
        """Get status summary based on latest performance"""
        latest = ExecutivePerformance.objects.filter(
            kpi_definition=obj
        ).order_by('-period_date').first()
        
        if latest:
            return KPICalculator.get_status_info(latest.actual_value, obj)
        
        return {
            'progress': 0,
            'status': 'not_started',
            'status_display': 'Not Started',
            'color': '#6c757d',
            'priority': 5
        }


class ExecutiveDashboardSerializer(serializers.Serializer):
    """Serializer for executive dashboard data"""
    
    executive_role = serializers.CharField()
    role_name = serializers.CharField()
    total_kpis = serializers.IntegerField()
    summary_stats = serializers.DictField()
    recent_performances = serializers.ListField()
    alerts_count = serializers.IntegerField()
    categories = serializers.DictField()
    
    class Meta:
        fields = [
            'executive_role', 'role_name', 'total_kpis', 'summary_stats',
            'recent_performances', 'alerts_count', 'categories'
        ]


# Input serializers for API requests
class KPIPerformanceUpdateSerializer(serializers.Serializer):
    """Serializer for updating KPI performance"""
    
    kpi_id = serializers.UUIDField(required=True)
    actual_value = serializers.DecimalField(max_digits=20, decimal_places=4, required=True)
    period_date = serializers.DateField(required=True)
    period_type = serializers.ChoiceField(
        choices=[
            ('daily', 'Daily'),
            ('weekly', 'Weekly'), 
            ('monthly', 'Monthly'),
            ('quarterly', 'Quarterly'),
            ('annually', 'Annually')
        ],
        default='monthly'
    )
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    data_source = serializers.CharField(max_length=100, required=False, allow_blank=True)
    state = serializers.UUIDField(required=False, allow_null=True)
    business_district = serializers.UUIDField(required=False, allow_null=True)
    
    def validate_kpi_id(self, value):
        """Validate that KPI exists and is active"""
        try:
            kpi = ExecutiveKPIDefinition.objects.get(id=value, is_active=True)
            return value
        except ExecutiveKPIDefinition.DoesNotExist:
            raise serializers.ValidationError("KPI definition not found or inactive")
    
    def validate(self, data):
        """Cross-field validation"""
        from django.utils import timezone

        # Validate period_date is not in the future
        if data['period_date'] > timezone.now().date():
            raise serializers.ValidationError({
                'period_date': 'Performance date cannot be in the future'
            })
        
        return data


class KPIDefinitionCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating new KPI definitions"""
    
    class Meta:
        model = ExecutiveKPIDefinition
        fields = [
            'executive_role', 'category', 'name', 'description',
            'data_type', 'unit', 'priority', 'target_value', 
            'target_min', 'target_max', 'is_range_target',
            'is_reverse_polarity', 'deadline', 'measurement_frequency'
        ]
    
    def validate(self, data):
        """Validate KPI definition data"""
        # Ensure either target_value or target_min/max are provided
        if data.get('is_range_target'):
            if not (data.get('target_min') and data.get('target_max')):
                raise serializers.ValidationError({
                    'target_range': 'Both target_min and target_max are required for range targets'
                })
            if data['target_min'] >= data['target_max']:
                raise serializers.ValidationError({
                    'target_range': 'target_min must be less than target_max'
                })
        else:
            if not data.get('target_value'):
                raise serializers.ValidationError({
                    'target_value': 'target_value is required for single targets'
                })
        
        # Check for duplicate KPI names within the same executive role
        if ExecutiveKPIDefinition.objects.filter(
            executive_role=data['executive_role'],
            name=data['name']
        ).exists():
            raise serializers.ValidationError({
                'name': 'KPI with this name already exists for this executive role'
            })
        
        return data


class AlertAcknowledgeSerializer(serializers.Serializer):
    """Serializer for acknowledging alerts"""
    
    alert_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=50
    )
    acknowledge_message = serializers.CharField(
        max_length=500, 
        required=False, 
        allow_blank=True
    )
    
    def validate_alert_ids(self, value):
        """Validate that all alert IDs exist and are active"""
        existing_alerts = ExecutiveKPIAlert.objects.filter(
            id__in=value,
            is_active=True,
            acknowledged=False
        ).values_list('id', flat=True)
        
        if len(existing_alerts) != len(value):
            missing_alerts = set(value) - set(existing_alerts)
            raise serializers.ValidationError(
                f"Some alerts not found or already acknowledged: {missing_alerts}"
            )
        
        return value


# Response serializers for specific API endpoints
class CTOKPIResponseSerializer(serializers.Serializer):
    """Response serializer for CTO KPIs endpoint"""
    
    feedersUpgrade = serializers.DictField()
    gridOfftake = serializers.DictField()
    energyDelivery = serializers.DictField()
    slaCompliance = serializers.DictField()
    monthlyIGR = serializers.DictField()


class CCOKPIResponseSerializer(serializers.Serializer):
    """Response serializer for CCO KPIs endpoint"""
    
    billingEfficiency = serializers.DictField()
    collectionEfficiency = serializers.DictField()
    bandAGrowth = serializers.DictField()
    igrGeneration = serializers.DictField()


class CFOKPIResponseSerializer(serializers.Serializer):
    """Response serializer for CFO KPIs endpoint"""
    
    financialExcellence = serializers.DictField()


class CHROKPIResponseSerializer(serializers.Serializer):
    """Response serializer for CHRO KPIs endpoint"""
    
    humanResourceExcellence = serializers.DictField()


# Utility serializers for common data structures
class KPITrendDataSerializer(serializers.Serializer):
    """Serializer for KPI trend data"""
    
    month = serializers.CharField()
    month_name = serializers.CharField()
    value = serializers.FloatField(allow_null=True)
    target = serializers.FloatField(allow_null=True)
    target_min = serializers.FloatField(allow_null=True)
    target_max = serializers.FloatField(allow_null=True)
    status = serializers.CharField()


class KPIStatusInfoSerializer(serializers.Serializer):
    """Serializer for KPI status information"""
    
    progress = serializers.FloatField()
    status = serializers.CharField()
    status_display = serializers.CharField()
    color = serializers.CharField()
    priority = serializers.IntegerField()


class APIResponseSerializer(serializers.Serializer):
    """Generic API response serializer"""
    
    success = serializers.BooleanField()
    data = serializers.DictField(required=False)
    message = serializers.CharField(required=False)
    error = serializers.CharField(required=False)
    meta = serializers.DictField(required=False)


# Performance trend serializers
class KPIPerformanceTrendSerializer(serializers.Serializer):
    """Serializer for KPI performance trend data"""
    
    period_date = serializers.DateField()
    actual_value = serializers.FloatField()
    progress_percentage = serializers.FloatField()
    status = serializers.CharField()
    target_value = serializers.FloatField(allow_null=True)
    target_min = serializers.FloatField(allow_null=True)
    target_max = serializers.FloatField(allow_null=True)
    verified = serializers.BooleanField()


class KPIHistoryResponseSerializer(serializers.Serializer):
    """Response serializer for KPI performance history"""
    
    kpi_info = serializers.DictField()
    history = serializers.ListField(child=KPIPerformanceTrendSerializer())
    summary_stats = serializers.DictField(required=False)


# Bulk operations serializers
class BulkPerformanceUpdateSerializer(serializers.Serializer):
    """Serializer for bulk performance updates"""
    
    performances = serializers.ListField(
        child=KPIPerformanceUpdateSerializer(),
        min_length=1,
        max_length=100
    )
    
    def validate_performances(self, value):
        """Validate individual performance records"""
        # Additional bulk validation can go here
        return value


class BulkUpdateResponseSerializer(serializers.Serializer):
    """Response serializer for bulk updates"""
    
    successful = serializers.ListField()
    errors = serializers.ListField()
    summary = serializers.DictField()


# Executive summary serializers
class ExecutiveSummarySerializer(serializers.Serializer):
    """Serializer for executive summary reports"""
    
    executive_role = serializers.CharField()
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    generated_at = serializers.DateTimeField()
    summary = serializers.DictField()
    kpi_details = serializers.ListField()
    recommendations = serializers.ListField()
    performance_trends = serializers.DictField(required=False)


# Alert management serializers
class AlertSummarySerializer(serializers.Serializer):
    """Serializer for alert summary data"""
    
    total_alerts = serializers.IntegerField()
    critical_alerts = serializers.IntegerField()
    high_priority_alerts = serializers.IntegerField()
    recent_alerts = serializers.ListField()
    alerts_by_executive = serializers.DictField()


# KPI comparison serializers
class KPIComparisonSerializer(serializers.Serializer):
    """Serializer for comparing KPIs across executives"""
    
    kpi_name = serializers.CharField()
    executives = serializers.DictField()
    comparison_period = serializers.CharField()
    best_performer = serializers.CharField(allow_null=True)
    worst_performer = serializers.CharField(allow_null=True)
    average_performance = serializers.FloatField(allow_null=True)


# Dashboard widget serializers
class KPIDashboardWidgetSerializer(serializers.Serializer):
    """Serializer for dashboard widget data"""
    
    widget_type = serializers.CharField()
    title = serializers.CharField()
    data = serializers.DictField()
    config = serializers.DictField()
    last_updated = serializers.DateTimeField()


class ExecutiveDashboardWidgetSerializer(serializers.Serializer):
    """Serializer for executive-specific dashboard widgets"""
    
    executive_role = serializers.CharField()
    widgets = serializers.ListField(child=KPIDashboardWidgetSerializer())
    layout = serializers.DictField(required=False)