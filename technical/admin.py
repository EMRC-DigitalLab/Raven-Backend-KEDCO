# technical/admin.py
from datetime import timedelta

from django.contrib import admin
from django.db.models import Avg, Max, Min, Sum
from django.utils.html import format_html

from .models import (
    CumulativeMeterReading,
    DailyHoursOfSupply,
    EnergyDelivered,
    FaultTypeCategory,
    FeederEnergyDaily,
    FeederEnergyMonthly,
    FeederInterruption,
    HourlyLoad,
)


# Custom filters
class MeterReadingTypeFilter(admin.SimpleListFilter):
    title = 'Reading Type'
    parameter_name = 'reading_type'

    def lookups(self, request, model_admin):
        return [
            ('actual', 'Actual Readings'),
            ('estimated', 'Estimated Readings'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'actual':
            return queryset.filter(is_estimated=False)
        if self.value() == 'estimated':
            return queryset.filter(is_estimated=True)


class MeterReadingAnomalyFilter(admin.SimpleListFilter):
    title = 'Data Quality'
    parameter_name = 'anomaly'

    def lookups(self, request, model_admin):
        return [
            ('normal', 'Normal Readings'),
            ('potential_rollover', 'Potential Meter Rollover'),
            ('negative_consumption', 'Negative Consumption'),
            ('high_consumption', 'Unusually High Consumption'),
        ]

    def queryset(self, request, queryset):
        from datetime import timedelta
        
        if self.value() == 'normal':
            # Filter to show only normal readings (this is complex, simplified here)
            return queryset
        
        if self.value() in ['potential_rollover', 'negative_consumption', 'high_consumption']:
            filtered_ids = []
            
            for reading in queryset.select_related('feeder'):
                consumption = reading.calculate_daily_consumption()
                
                if consumption is None:
                    continue
                
                if self.value() == 'negative_consumption' and consumption < 0:
                    filtered_ids.append(reading.id)
                elif self.value() == 'potential_rollover' and consumption < 0:
                    filtered_ids.append(reading.id)
                elif self.value() == 'high_consumption':
                    # Get average for this feeder
                    avg_consumption = CumulativeMeterReading.objects.filter(
                        feeder=reading.feeder
                    ).aggregate(
                        avg=Avg('cumulative_mwh')
                    )['avg'] or 0
                    
                    if consumption > avg_consumption * 2:
                        filtered_ids.append(reading.id)
            
            return queryset.filter(id__in=filtered_ids)


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
        from django.db.models import ExpressionWrapper, F, fields
        from django.db.models.functions import Coalesce
        from django.utils import timezone

        # Annotate with duration in seconds
        queryset = queryset.annotate(
            duration_seconds=ExpressionWrapper(
                (Coalesce(F('restored_at'), timezone.now()) - F('occurred_at')),
                output_field=fields.DurationField()
            )
        )
        
        if self.value() == 'short':
            return queryset.filter(duration_seconds__lt=timedelta(hours=1))
        elif self.value() == 'medium':
            return queryset.filter(duration_seconds__gte=timedelta(hours=1), duration_seconds__lt=timedelta(hours=4))
        elif self.value() == 'long':
            return queryset.filter(duration_seconds__gte=timedelta(hours=4), duration_seconds__lt=timedelta(hours=12))
        elif self.value() == 'very_long':
            return queryset.filter(duration_seconds__gte=timedelta(hours=12))


class OverlappingInterruptionsFilter(admin.SimpleListFilter):
    """
    OPTIMIZED filter - checks overlaps ONLY within same feeder.
    Respects existing filters (date, feeder, etc.)
    """
    title = 'Overlapping Status'
    parameter_name = 'overlapping'

    def lookups(self, request, model_admin):
        return [
            ('has_overlaps', 'Has Overlapping Interruptions'),
            ('ongoing_multiple', 'Multiple Ongoing (Same Feeder)'),
        ]

    def queryset(self, request, queryset):
        from django.db import connection
        from django.utils import timezone
        
        if not self.value():
            return queryset
        
        if self.value() == 'has_overlaps':
            # Get IDs from current filtered queryset (respects date/feeder filters)
            current_ids = list(queryset.values_list('id', flat=True))
            
            if not current_ids:
                return queryset.none()
            
            # Only check overlaps for interruptions in the filtered set
            now = timezone.now()
            placeholders = ','.join(['%s'] * len(current_ids))
            
            sql = f"""
                SELECT DISTINCT t1.id
                FROM technical_feederinterruption t1
                INNER JOIN technical_feederinterruption t2 
                    ON t1.feeder_id = t2.feeder_id  
                    AND t1.id != t2.id
                WHERE t1.id IN ({placeholders})
                    AND (
                        t2.occurred_at < COALESCE(t1.restored_at, %s)
                        AND
                        (t2.restored_at > t1.occurred_at OR t2.restored_at IS NULL)
                    )
            """
            
            with connection.cursor() as cursor:
                cursor.execute(sql, current_ids + [now])
                overlapping_ids = [row[0] for row in cursor.fetchall()]
            
            return queryset.filter(id__in=overlapping_ids)
        
        elif self.value() == 'ongoing_multiple':
            # Find feeders with multiple ongoing interruptions
            # Only within the current filtered queryset
            from django.db.models import Count
            
            feeders_with_multiple = queryset.filter(
                restored_at__isnull=True
            ).values('feeder').annotate(
                ongoing_count=Count('id')
            ).filter(ongoing_count__gte=2).values_list('feeder', flat=True)
            
            return queryset.filter(
                feeder__in=list(feeders_with_multiple),
                restored_at__isnull=True
            )


# Admin classes
@admin.register(CumulativeMeterReading)
class CumulativeMeterReadingAdmin(admin.ModelAdmin):
    list_display = [
        'feeder', 'reading_date', 'cumulative_mwh_display', 
        'daily_consumption_display', 'reading_status', 'data_quality_indicator'
    ]
    list_filter = [
        'feeder', 'reading_date', 'is_estimated', 
        MeterReadingTypeFilter, MeterReadingAnomalyFilter
    ]
    date_hierarchy = 'reading_date'
    search_fields = ['feeder__name', 'notes']
    list_per_page = 50
    readonly_fields = ['daily_consumption_display', 'data_quality_indicator']
    
    fieldsets = (
        ('Meter Information', {
            'fields': ('feeder', 'reading_date', 'cumulative_mwh', 'reading_time')
        }),
        ('Data Quality', {
            'fields': ('is_estimated', 'notes')
        }),
        ('Calculated Fields', {
            'fields': ('daily_consumption_display', 'data_quality_indicator'),
            'classes': ('collapse',)
        }),
    )
    
    actions = [
        'validate_readings', 'calculate_consumption_stats', 
        'detect_anomalies', 'export_meter_readings'
    ]
    
    def cumulative_mwh_display(self, obj):
        """Display cumulative reading with formatting"""
        # Format the value first, then pass to format_html
        formatted_value = f'{float(obj.cumulative_mwh):,.4f} MWh'
        return format_html(
            '<span style="font-family: monospace; font-weight: bold;">{}</span>',
            formatted_value
        )
    cumulative_mwh_display.short_description = 'Cumulative Reading'
    cumulative_mwh_display.admin_order_field = 'cumulative_mwh'
    
    def daily_consumption_display(self, obj):
        """Display calculated daily consumption"""
        # Check if object has been saved (has an ID)
        if not obj.pk:
            return format_html(
                '<span style="color: gray; font-style: italic;">Save to calculate</span>'
            )
        
        try:
            consumption = obj.calculate_daily_consumption()
        except Exception as e:
            return format_html(
                '<span style="color: gray; font-style: italic;">Error: {}</span>',
                str(e)
            )
        
        if consumption is None:
            return format_html(
                '<span style="color: gray; font-style: italic;">No previous reading</span>'
            )
        
        # Format the consumption value BEFORE passing to format_html
        consumption_float = float(consumption)
        if consumption < 0:
            color = 'red'
            icon = '⚠️'
            text = f'{icon} {consumption_float:.2f} MWh (ANOMALY)'
        elif consumption > 1000:  # Adjust threshold as needed
            color = 'orange'
            icon = '⚡'
            text = f'{icon} {consumption_float:.2f} MWh (High)'
        else:
            color = 'green'
            icon = '✓'
            text = f'{icon} {consumption_float:.2f} MWh'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, text
        )
    daily_consumption_display.short_description = 'Daily Consumption'
    
    def reading_status(self, obj):
        """Display reading status badge"""
        if obj.is_estimated:
            color = 'orange'
            text = 'ESTIMATED'
        else:
            color = 'green'
            text = 'ACTUAL'
        
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px; font-weight: bold;">{}</span>',
            color, text
        )
    reading_status.short_description = 'Status'
    
    def data_quality_indicator(self, obj):
        """Display data quality indicator"""
        # Check if object has been saved (has an ID)
        if not obj.pk:
            return format_html(
                '<span style="color: gray;">⚪ Not saved yet</span>'
            )
        
        try:
            consumption = obj.calculate_daily_consumption()
        except Exception as e:
            error_msg = str(e)[:50]
            return format_html(
                '<span style="color: red;">❌ Error calculating: {}</span>',
                error_msg
            )
        
        if consumption is None:
            return format_html(
                '<span style="color: gray;">⚪ No previous data</span>'
            )
        
        issues = []
        
        # Check for negative consumption (potential meter rollover)
        if consumption < 0:
            issues.append('❌ Negative consumption detected')
        
        # Check for unusually high consumption
        try:
            from django.db.models import Avg
            avg_consumption = CumulativeMeterReading.objects.filter(
                feeder=obj.feeder
            ).exclude(id=obj.id).aggregate(avg=Avg('cumulative_mwh'))['avg'] or 0
            
            if consumption > avg_consumption * 3:
                issues.append('⚠️ Unusually high consumption')
        except Exception:
            pass  # Skip this check if there's an error
        
        # Check if reading is estimated
        if obj.is_estimated:
            issues.append('📊 Estimated reading')
        
        if not issues:
            return format_html('<span style="color: green;">✅ Normal</span>')
        else:
            issues_text = '<br>'.join(issues)
            return format_html(
                '<span style="color: orange;">{}</span>',
                issues_text
            )
    data_quality_indicator.short_description = 'Data Quality'
    
    def validate_readings(self, request, queryset):
        """Validate selected meter readings"""
        total = queryset.count()
        issues_found = 0
        negative_count = 0
        high_consumption_count = 0
        
        for reading in queryset:
            consumption = reading.calculate_daily_consumption()
            
            if consumption is not None:
                if consumption < 0:
                    issues_found += 1
                    negative_count += 1
                elif consumption > 1000:  # Adjust threshold
                    issues_found += 1
                    high_consumption_count += 1
        
        message = (
            f"Validated {total} readings. "
            f"Found {issues_found} potential issues: "
            f"{negative_count} negative consumption, "
            f"{high_consumption_count} unusually high consumption."
        )
        
        if issues_found > 0:
            self.message_user(request, message, level='warning')
        else:
            self.message_user(request, f"✅ All {total} readings validated successfully!")
    validate_readings.short_description = "Validate selected readings"
    
    def calculate_consumption_stats(self, request, queryset):
        """Calculate consumption statistics for selected readings"""
        consumptions = []
        
        for reading in queryset:
            consumption = reading.calculate_daily_consumption()
            if consumption is not None and consumption >= 0:
                consumptions.append(consumption)
        
        if not consumptions:
            self.message_user(request, "No valid consumption data found", level='warning')
            return
        
        import statistics
        from decimal import Decimal
        
        total = sum(consumptions)
        avg = statistics.mean(consumptions)
        median = statistics.median(consumptions)
        max_val = max(consumptions)
        min_val = min(consumptions)
        
        message = (
            f"Consumption Statistics ({len(consumptions)} readings):\n"
            f"Total: {float(total):,.2f} MWh | "
            f"Average: {float(avg):,.2f} MWh | "
            f"Median: {float(median):,.2f} MWh | "
            f"Max: {float(max_val):,.2f} MWh | "
            f"Min: {float(min_val):,.2f} MWh"
        )
        self.message_user(request, message)
    calculate_consumption_stats.short_description = "Calculate consumption statistics"
    
    def detect_anomalies(self, request, queryset):
        """Detect anomalies in selected readings"""
        anomalies = {
            'negative': [],
            'high': [],
            'gaps': [],
        }
        
        for reading in queryset.order_by('feeder', 'reading_date'):
            consumption = reading.calculate_daily_consumption()
            
            if consumption is not None:
                if consumption < 0:
                    anomalies['negative'].append(reading)
                elif consumption > 1000:  # Adjust threshold
                    anomalies['high'].append(reading)
        
        message_parts = [
            f"Anomaly Detection Results:",
            f"❌ Negative consumption: {len(anomalies['negative'])} readings",
            f"⚠️ High consumption: {len(anomalies['high'])} readings",
        ]
        
        message = '\n'.join(message_parts)
        level = 'warning' if sum(len(v) for v in anomalies.values()) > 0 else 'success'
        self.message_user(request, message, level=level)
    detect_anomalies.short_description = "Detect anomalies in readings"
    
    def export_meter_readings(self, request, queryset):
        """Export meter readings with consumption data to CSV"""
        import csv

        from django.http import HttpResponse
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="meter_readings.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Feeder', 'Date', 'Cumulative Reading (MWh)', 
            'Daily Consumption (MWh)', 'Reading Time', 'Is Estimated', 'Notes'
        ])
        
        for reading in queryset.order_by('feeder', 'reading_date'):
            consumption = reading.calculate_daily_consumption()
            consumption_str = f"{float(consumption):.2f}" if consumption is not None else "N/A"
            
            writer.writerow([
                reading.feeder.name,
                reading.reading_date,
                f"{float(reading.cumulative_mwh):.4f}",
                consumption_str,
                reading.reading_time or "N/A",
                "Yes" if reading.is_estimated else "No",
                reading.notes or ""
            ])
        
        return response
    export_meter_readings.short_description = "Export readings to CSV"
    
    def changelist_view(self, request, extra_context=None):
        """Add summary statistics to changelist"""
        extra_context = extra_context or {}
        
        # Get current queryset based on filters
        cl = self.get_changelist_instance(request)
        queryset = cl.get_queryset(request)
        
        if queryset.exists():
            from django.db.models import Count
            
            total_readings = queryset.count()
            estimated_count = queryset.filter(is_estimated=True).count()
            actual_count = total_readings - estimated_count
            
            # Calculate consumption stats
            consumptions = []
            anomaly_count = 0
            
            for reading in queryset[:100]:  # Limit to first 100 for performance
                consumption = reading.calculate_daily_consumption()
                if consumption is not None:
                    consumptions.append(consumption)
                    if consumption < 0 or consumption > 1000:
                        anomaly_count += 1
            
            avg_consumption = sum(consumptions) / len(consumptions) if consumptions else 0
            
            extra_context['summary_stats'] = {
                'total_readings': total_readings,
                'actual_readings': actual_count,
                'estimated_readings': estimated_count,
                'avg_daily_consumption': avg_consumption,
                'potential_anomalies': anomaly_count,
            }
        
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(EnergyDelivered)
class EnergyDeliveredAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'date', 'energy_mwh', 'energy_colored']
    list_filter = ['feeder', 'date']
    date_hierarchy = 'date'
    search_fields = ['feeder__name']
    list_per_page = 50
    
    actions = ['calculate_energy_stats', 'export_energy_data']
    
    def energy_colored(self, obj):
        """Color code energy levels"""
        color = 'green' if obj.energy_mwh > 100 else 'orange' if obj.energy_mwh > 50 else 'red'
        energy_text = f'{float(obj.energy_mwh)} MWh'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            energy_text
        )
    energy_colored.short_description = 'Energy (Colored)'
    energy_colored.admin_order_field = 'energy_mwh'
    
    def calculate_energy_stats(self, request, queryset):
        """Calculate statistics for selected energy records"""
        stats = queryset.aggregate(
            total=Sum('energy_mwh'),
            avg=Avg('energy_mwh'),
            max=Max('energy_mwh'),
            min=Min('energy_mwh')
        )
        
        message = (
            f"Energy Statistics ({queryset.count()} records):\n"
            f"Total: {float(stats['total']):.2f} MWh | "
            f"Average: {float(stats['avg']):.2f} MWh | "
            f"Max: {float(stats['max']):.2f} MWh | "
            f"Min: {float(stats['min']):.2f} MWh"
        )
        self.message_user(request, message)
    calculate_energy_stats.short_description = "Calculate energy statistics"
    
    def export_energy_data(self, request, queryset):
        """Export energy data to CSV"""
        import csv

        from django.http import HttpResponse
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="energy_delivered.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Feeder', 'Date', 'Energy (MWh)'])
        
        for record in queryset.order_by('feeder', 'date'):
            writer.writerow([
                record.feeder.name,
                record.date,
                f"{float(record.energy_mwh):.2f}"
            ])
        
        return response
    export_energy_data.short_description = "Export to CSV"


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
        load_text = f'{float(obj.load_mw)} MW'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            load_text
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
            f"Peak Load: {float(stats['max_load'])} MW on {peak.date} at {peak.hour}:00 "
            f"(Feeder: {peak.feeder.name}) | "
            f"Average: {float(stats['avg_load']):.2f} MW | "
            f"Min: {float(stats['min_load'])} MW"
        )
        self.message_user(request, message)
    find_peak_loads.short_description = "Find peak loads in selection"
    
    def export_peak_summary(self, request, queryset):
        """Export peak load summary by feeder"""
        import csv

        from django.http import HttpResponse
        
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
                float(peak.load_mw),
                peak.date,
                f"{peak.hour}:00",
                f"{float(avg):.2f}"
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
                total_records=Count('id')
            )
        
            peak_record = queryset.order_by('-load_mw').first()
        
            extra_context['summary_stats'] = {
                'peak_load': stats['max_load'],
                'peak_date': peak_record.date if peak_record else None,
                'peak_hour': peak_record.hour if peak_record else None,
                'peak_feeder': peak_record.feeder.name if peak_record else None,
                'avg_load': stats['avg_load'],
                'min_load': stats['min_load'],
                'total_records': stats['total_records'],
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
        DurationFilter, OverlappingInterruptionsFilter,
        'occurred_at'
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
        percentage = (float(obj.hours_supplied) / 24) * 100
        color = 'green' if percentage > 80 else 'orange' if percentage > 50 else 'red'
        percentage_text = f'{percentage:.1f}%'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, percentage_text
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
            f"Total: {float(stats['total']):.2f} MWh | "
            f"Average: {float(stats['avg']):.2f} MWh | "
            f"Max: {float(stats['max']):.2f} MWh | "
            f"Min: {float(stats['min']):.2f} MWh"
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


@admin.register(FaultTypeCategory)
class FaultTypeCategoryAdmin(admin.ModelAdmin):
    """
    Manage which interruption type codes belong to Load Shedding or Transmission.
    Any code NOT listed here is automatically classified as a DisCo fault.
    """
    list_display = ['code', 'label', 'category_badge']
    list_filter = ['category']
    search_fields = ['code', 'label']
    ordering = ['category', 'code']
    list_per_page = 50

    def category_badge(self, obj):
        colors = {
            'load_shedding': '#f59e0b',
            'transmission': '#3b82f6',
        }
        color = colors.get(obj.category, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 10px;border-radius:4px;font-weight:bold">{}</span>',
            color,
            obj.get_category_display(),
        )
    category_badge.short_description = 'Category'