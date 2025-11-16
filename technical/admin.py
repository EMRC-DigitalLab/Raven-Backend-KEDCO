from django.contrib import admin
from django.db.models import Max, Min, Avg, Sum
from django.utils.html import format_html
from .models import (
    EnergyDelivered,
    HourlyLoad,
    FeederInterruption,
    DailyHoursOfSupply,
    FeederEnergyDaily,
    FeederEnergyMonthly,
)


# Custom filters
class LoadRangeFilter(admin.SimpleListFilter):
    title = 'Load Range (MW)'
    parameter_name = 'load_range'

    def lookups(self, request, model_admin):
        return [
            ('0-5', '0-5 MW'),
            ('5-10', '5-10 MW'),
            ('10-20', '10-20 MW'),
            ('20-50', '20-50 MW'),
            ('50+', '50+ MW'),
        ]

    def queryset(self, request, queryset):
        if self.value() == '0-5':
            return queryset.filter(load_mw__gte=0, load_mw__lt=5)
        if self.value() == '5-10':
            return queryset.filter(load_mw__gte=5, load_mw__lt=10)
        if self.value() == '10-20':
            return queryset.filter(load_mw__gte=10, load_mw__lt=20)
        if self.value() == '20-50':
            return queryset.filter(load_mw__gte=20, load_mw__lt=50)
        if self.value() == '50+':
            return queryset.filter(load_mw__gte=50)


class PeakHourFilter(admin.SimpleListFilter):
    title = 'Time of Day'
    parameter_name = 'peak_hour'

    def lookups(self, request, model_admin):
        return [
            ('morning', 'Morning Peak (6-9 AM)'),
            ('afternoon', 'Afternoon (12-3 PM)'),
            ('evening', 'Evening Peak (6-10 PM)'),
            ('night', 'Night (10 PM - 6 AM)'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'morning':
            return queryset.filter(hour__gte=6, hour__lt=9)
        if self.value() == 'afternoon':
            return queryset.filter(hour__gte=12, hour__lt=15)
        if self.value() == 'evening':
            return queryset.filter(hour__gte=18, hour__lt=22)
        if self.value() == 'night':
            return queryset.filter(hour__gte=22) | queryset.filter(hour__lt=6)


class InterruptionStatusFilter(admin.SimpleListFilter):
    title = 'Status'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return [
            ('resolved', 'Resolved'),
            ('ongoing', 'Ongoing'),
            ('load_shedding', 'Load Shedding'),
            ('fault', 'Faults Only'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'resolved':
            return queryset.exclude(restored_at__isnull=True)
        if self.value() == 'ongoing':
            return queryset.filter(restored_at__isnull=True)
        if self.value() == 'load_shedding':
            return queryset.filter(interruption_type__icontains='L/S')
        if self.value() == 'fault':
            return queryset.exclude(interruption_type__icontains='L/S')


class DurationFilter(admin.SimpleListFilter):
    title = 'Duration'
    parameter_name = 'duration'

    def lookups(self, request, model_admin):
        return [
            ('short', '< 1 hour'),
            ('medium', '1-4 hours'),
            ('long', '4-12 hours'),
            ('very_long', '> 12 hours'),
        ]

    def queryset(self, request, queryset):
        from django.utils import timezone
        from datetime import timedelta
        
        now = timezone.now()
        filtered_ids = []
        
        for interruption in queryset:
            duration = interruption.duration_hours
            
            if self.value() == 'short' and duration < 1:
                filtered_ids.append(interruption.id)
            elif self.value() == 'medium' and 1 <= duration < 4:
                filtered_ids.append(interruption.id)
            elif self.value() == 'long' and 4 <= duration < 12:
                filtered_ids.append(interruption.id)
            elif self.value() == 'very_long' and duration >= 12:
                filtered_ids.append(interruption.id)
        
        return queryset.filter(id__in=filtered_ids)


# Admin classes
@admin.register(EnergyDelivered)
class EnergyDeliveredAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'date', 'energy_mwh', 'energy_colored']
    list_filter = ['feeder', 'date']
    date_hierarchy = 'date'
    search_fields = ['feeder__name']
    list_per_page = 50
    
    def energy_colored(self, obj):
        """Color code energy levels"""
        color = 'green' if obj.energy_mwh > 100 else 'orange' if obj.energy_mwh > 50 else 'red'
        return format_html(
            '<span style="color: {};">{} MWh</span>',
            color,
            obj.energy_mwh
        )
    energy_colored.short_description = 'Energy (Colored)'
    energy_colored.admin_order_field = 'energy_mwh'


@admin.register(HourlyLoad)
class HourlyLoadAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'date', 'hour', 'load_mw', 'load_colored', 'time_of_day']
    list_filter = ['feeder', 'date', LoadRangeFilter, PeakHourFilter]
    date_hierarchy = 'date'
    search_fields = ['feeder__name']
    list_per_page = 100
    
    # Enable actions
    actions = ['find_peak_loads', 'export_peak_summary']
    
    def load_colored(self, obj):
        """Color code load levels"""
        color = 'red' if obj.load_mw > 50 else 'orange' if obj.load_mw > 20 else 'green'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} MW</span>',
            color,
            obj.load_mw
        )
    load_colored.short_description = 'Load (Colored)'
    load_colored.admin_order_field = 'load_mw'
    
    def time_of_day(self, obj):
        """Display hour in 12-hour format"""
        hour_12 = obj.hour % 12 or 12
        period = 'AM' if obj.hour < 12 else 'PM'
        return f"{hour_12}:00 {period}"
    time_of_day.short_description = 'Time'
    
    def find_peak_loads(self, request, queryset):
        """Find peak loads in selected records"""
        if not queryset.exists():
            self.message_user(request, "No records selected", level='warning')
            return
        
        peak = queryset.order_by('-load_mw').first()
        stats = queryset.aggregate(
            max_load=Max('load_mw'),
            min_load=Min('load_mw'),
            avg_load=Avg('load_mw')
        )
        
        message = (
            f"Peak Load: {stats['max_load']} MW on {peak.date} at {peak.hour}:00 "
            f"(Feeder: {peak.feeder.name}) | "
            f"Average: {stats['avg_load']:.2f} MW | "
            f"Min: {stats['min_load']} MW"
        )
        self.message_user(request, message)
    find_peak_loads.short_description = "Find peak loads in selection"
    
    def export_peak_summary(self, request, queryset):
        """Export peak load summary by feeder"""
        from django.http import HttpResponse
        import csv
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="peak_loads.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Feeder', 'Peak Load (MW)', 'Date', 'Hour', 'Average Load (MW)'])
        
        # Group by feeder and find peaks
        feeders = queryset.values_list('feeder', flat=True).distinct()
        for feeder_id in feeders:
            feeder_data = queryset.filter(feeder_id=feeder_id)
            peak = feeder_data.order_by('-load_mw').first()
            avg = feeder_data.aggregate(avg=Avg('load_mw'))['avg']
            
            writer.writerow([
                peak.feeder.name,
                peak.load_mw,
                peak.date,
                f"{peak.hour}:00",
                f"{avg:.2f}"
            ])
        
        return response
    export_peak_summary.short_description = "Export peak summary by feeder"
    
    def changelist_view(self, request, extra_context=None):
        """Add summary statistics to changelist"""
        extra_context = extra_context or {}
    
        # Get current queryset based on filters
        cl = self.get_changelist_instance(request)
        queryset = cl.get_queryset(request)
    
        if queryset.exists():
            from django.db.models import Count
        
            stats = queryset.aggregate(
                max_load=Max('load_mw'),
                min_load=Min('load_mw'),
                avg_load=Avg('load_mw'),
                total_records=Count('id')  # Changed from Sum to Count
            )
        
            peak_record = queryset.order_by('-load_mw').first()
        
            extra_context['summary_stats'] = {
                'peak_load': stats['max_load'],
                'peak_date': peak_record.date if peak_record else None,
                'peak_hour': peak_record.hour if peak_record else None,
                'peak_feeder': peak_record.feeder.name if peak_record else None,
                'avg_load': stats['avg_load'],
                'min_load': stats['min_load'],
                'total_records': stats['total_records'],  # Now this will work
            }
    
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(FeederInterruption)
class FeederInterruptionAdmin(admin.ModelAdmin):
    list_display = [
        'feeder', 'interruption_type', 'occurred_at', 'restored_at', 
        'duration_display', 'status_badge'
    ]
    list_filter = [
        'feeder', 'interruption_type', InterruptionStatusFilter, 
        DurationFilter, 'occurred_at'
    ]
    date_hierarchy = 'occurred_at'
    search_fields = ['feeder__name', 'description', 'interruption_type']
    list_per_page = 50
    readonly_fields = ['duration_display', 'status_badge']
    
    actions = ['mark_as_resolved', 'calculate_interruption_stats']
    
    def duration_display(self, obj):
        """Display duration in a readable format"""
        hours = obj.duration_hours
        if hours < 1:
            return f"{hours * 60:.0f} minutes"
        elif hours < 24:
            return f"{hours:.2f} hours"
        else:
            days = hours / 24
            return f"{days:.1f} days ({hours:.1f} hours)"
    duration_display.short_description = 'Duration'
    
    def status_badge(self, obj):
        """Display status with color coding"""
        if obj.is_resolved:
            color = 'green'
            text = 'Resolved'
        else:
            color = 'red'
            text = 'Ongoing'
        
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color, text
        )
    status_badge.short_description = 'Status'
    
    def mark_as_resolved(self, request, queryset):
        """Mark selected interruptions as resolved"""
        from django.utils import timezone
        
        unresolved = queryset.filter(restored_at__isnull=True)
        count = unresolved.update(restored_at=timezone.now())
        
        self.message_user(
            request,
            f"{count} interruption(s) marked as resolved"
        )
    mark_as_resolved.short_description = "Mark selected as resolved"
    
    def calculate_interruption_stats(self, request, queryset):
        """Calculate statistics for selected interruptions"""
        from .models import calculate_interruption_metrics
        
        metrics = calculate_interruption_metrics(queryset)
        
        message = (
            f"Total: {metrics['total_interruptions']} | "
            f"Duration: {metrics['total_duration_hours']:.2f}h | "
            f"Avg: {metrics['avg_duration_hours']:.2f}h | "
            f"Faults: {metrics['fault_count']} ({metrics['fault_hours']:.2f}h) | "
            f"Load Shedding: {metrics['load_shedding_count']} ({metrics['load_shedding_hours']:.2f}h) | "
            f"Resolved: {metrics['resolved_count']} | "
            f"Ongoing: {metrics['unresolved_count']}"
        )
        self.message_user(request, message)
    calculate_interruption_stats.short_description = "Calculate interruption statistics"


@admin.register(DailyHoursOfSupply)
class DailyHoursOfSupplyAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'date', 'hours_supplied', 'availability_percentage']
    list_filter = ['feeder', 'date']
    date_hierarchy = 'date'
    search_fields = ['feeder__name']
    list_per_page = 50
    
    def availability_percentage(self, obj):
        """Calculate and display availability as percentage"""
        percentage = (obj.hours_supplied / 24) * 100
        color = 'green' if percentage > 80 else 'orange' if percentage > 50 else 'red'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color, percentage
        )
    availability_percentage.short_description = 'Availability'


@admin.register(FeederEnergyDaily)
class FeederEnergyDailyAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'date', 'energy_mwh', 'energy_trend']
    list_filter = ['feeder', 'date']
    date_hierarchy = 'date'
    search_fields = ['feeder__name']
    list_per_page = 50
    
    actions = ['calculate_daily_stats']
    
    def energy_trend(self, obj):
        """Show energy level with visual indicator"""
        # Get average for this feeder
        from django.db.models import Avg
        avg = FeederEnergyDaily.objects.filter(
            feeder=obj.feeder
        ).aggregate(avg=Avg('energy_mwh'))['avg'] or 0
        
        if obj.energy_mwh > avg * 1.2:
            icon = '↑'
            color = 'green'
        elif obj.energy_mwh < avg * 0.8:
            icon = '↓'
            color = 'red'
        else:
            icon = '→'
            color = 'gray'
        
        return format_html(
            '<span style="color: {}; font-size: 16px;">{}</span>',
            color, icon
        )
    energy_trend.short_description = 'Trend'
    
    def calculate_daily_stats(self, request, queryset):
        """Calculate statistics for selected daily records"""
        stats = queryset.aggregate(
            total=Sum('energy_mwh'),
            avg=Avg('energy_mwh'),
            max=Max('energy_mwh'),
            min=Min('energy_mwh')
        )
        
        message = (
            f"Total: {stats['total']:.2f} MWh | "
            f"Average: {stats['avg']:.2f} MWh | "
            f"Max: {stats['max']:.2f} MWh | "
            f"Min: {stats['min']:.2f} MWh"
        )
        self.message_user(request, message)
    calculate_daily_stats.short_description = "Calculate energy statistics"


@admin.register(FeederEnergyMonthly)
class FeederEnergyMonthlyAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'period', 'energy_mwh', 'month_display']
    list_filter = ['feeder', 'period']
    date_hierarchy = 'period'
    search_fields = ['feeder__name']
    list_per_page = 50
    
    def month_display(self, obj):
        """Display period in readable format"""
        return obj.period.strftime('%B %Y')
    month_display.short_description = 'Month'
    month_display.admin_order_field = 'period'