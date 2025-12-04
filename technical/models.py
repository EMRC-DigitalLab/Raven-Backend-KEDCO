# technical/models.py
from django.db import models
from common.models import UUIDModel, Feeder
from django.utils import timezone


class CumulativeMeterReading(UUIDModel, models.Model):
    """
    Stores the raw cumulative meter readings as received from the meter.
    This is the source of truth from the physical meter.
    
    IMPORTANT: Store readings in the same units your meters use!
    - If meters report in MWh, use cumulative_mwh field
    - If meters report in kWh, convert and use cumulative_mwh (divide by 1000)
    """
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE, related_name='meter_readings')
    reading_date = models.DateField(db_index=True)
    cumulative_mwh = models.DecimalField(
        max_digits=15, 
        decimal_places=4,
        help_text="Cumulative meter reading in MWh (like an odometer)"
    )
    reading_time = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="Exact time of meter reading"
    )
    is_estimated = models.BooleanField(
        default=False,
        help_text="Flag if this reading is estimated (e.g., for missing data)"
    )
    notes = models.TextField(blank=True)
    
    class Meta:
        unique_together = ('feeder', 'reading_date')
        ordering = ['feeder', 'reading_date']
        indexes = [
            models.Index(fields=['feeder', 'reading_date']),
            models.Index(fields=['reading_date']),
        ]
    
    def __str__(self):
        return f"{self.feeder.name} - {self.reading_date} - {self.cumulative_mwh} MWh"
    
    def calculate_daily_consumption(self):
        """
        Calculate daily consumption by comparing with previous day's reading.
        Returns energy in MWh.
        """
        from datetime import timedelta
        
        previous_date = self.reading_date - timedelta(days=1)
        
        try:
            previous_reading = CumulativeMeterReading.objects.get(
                feeder=self.feeder,
                reading_date=previous_date
            )
            
            # Calculate difference in MWh
            consumption_mwh = self.cumulative_mwh - previous_reading.cumulative_mwh
            
            # Handle meter rollover or reset
            if consumption_mwh < 0:
                # Meter may have been reset or there's an error
                # You can implement rollover logic here if meters have a max value
                return None  # or raise an error
            
            return consumption_mwh
            
        except CumulativeMeterReading.DoesNotExist:
            # No previous reading exists
            return None
    
    def save(self, *args, **kwargs):
        """
        Override save to automatically create/update EnergyDelivered record
        """
        super().save(*args, **kwargs)
        
        # Calculate and store daily consumption
        daily_consumption = self.calculate_daily_consumption()
        
        if daily_consumption is not None and daily_consumption >= 0:
            # Create or update EnergyDelivered record
            EnergyDelivered.objects.update_or_create(
                feeder=self.feeder,
                date=self.reading_date,
                defaults={
                    'energy_mwh': daily_consumption
                }
            )


class EnergyDelivered(UUIDModel, models.Model):
    """
    Stores the ACTUAL daily energy consumption (not cumulative).
    This is calculated from the difference between consecutive meter readings.
    """
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE)
    date = models.DateField()
    energy_mwh = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="ACTUAL energy delivered on this date (not cumulative)"
    )
    
    # Add these fields to track data quality
    is_calculated = models.BooleanField(
        default=True,
        help_text="True if calculated from meter readings, False if manually entered"
    )
    calculation_method = models.CharField(
        max_length=50,
        default='meter_difference',
        choices=[
            ('meter_difference', 'Calculated from meter difference'),
            ('manual_entry', 'Manually entered'),
            ('estimated', 'Estimated from historical data'),
        ]
    )

    class Meta:
        unique_together = ('feeder', 'date')
        ordering = ['date', 'feeder']
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['feeder', 'date']),
        ]


class HourlyLoad(UUIDModel, models.Model):
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE)
    date = models.DateField()
    hour = models.PositiveSmallIntegerField()  # 0 to 23
    load_mw = models.DecimalField(max_digits=10, decimal_places=2)

    reading_time = models.TimeField(
        null=True, 
        blank=True,
        help_text="Precise time of reading (HH:MM:SS) for sub-hour accuracy"
    )

    class Meta:
        unique_together = ('feeder', 'date', 'hour')
        ordering = ['date', 'hour']
        indexes = [
            models.Index(fields=['date', 'hour']),
            models.Index(fields=['feeder', 'date']),
            models.Index(fields=['date']),
        ]

    def save(self, *args, **kwargs):
        # Auto-populate hour from reading_time if provided
        if self.reading_time and not self.hour:
            self.hour = self.reading_time.hour
        super().save(*args, **kwargs)


class FeederInterruption(UUIDModel, models.Model):
    INTERRUPTION_TYPES = [
        ("E/F", "Earth Fault"),
        ("O/C", "Overcurrent"),
        ("O/C & E/F", "Overcurrent and Earth Fault"),
        ("NO RI", "No RI"),
        ("N/A", "Not Specified"),
        ("L/S", "Load Shedding (L/S)"),
        ("O/S", "Overload/Overcurrent (O/S)"),
        ("T/F", "Transformer Fault (T/F)"),
        ("B/F", "Bus/Breakdown Fault (B/F)"),
        ("O/N", "Overheating/Overtemp (O/N)"),
        ("O/E", "Open Earth (O/E)"),
        ("P/O", "Phase Open (P/O)"),
        ("O/F", "Over Frequency (O/F)"),
        ("P/M", "Phase Missing/Phase Metering (P/M)"),
        ("O", "Other Faults/Operational Fault (O)"),
        ("T/S", "Trip/Surge Fault (T/S)"),
        ("L/S GS", "Load Shedding – General Supply (L/S GS)"),
        ("MTNC", "Maintenance (MTNC)"),
        ("OC & E/F", "Open Circuit & Earth Fault (OC & E/F)"),
        ("EM/D", "Emergency/Device (EM/D)"),
        ("330KV L/F", "330 kV Line Fault (330KV L/F)"),
        ("OFF", "Switch Off/Feeder Off (OFF)"),
        ("S/C", "Short Circuit (S/C)"),
        ("132KV E/F", "132 kV Earth Fault (132KV E/F)"),
        ("132KV L/F", "132 kV Line Fault (132KV L/F)"),
        ("330KV L/S", "330 kV Line Shelving/Load Shedding (330KV L/S)"),
        ("132KV CB/F", "132 kV Circuit Breaker Failure (132KV CB/F)"),
        ("D/C", "Double Circuit/Direct Current (D/C)"),
        ("MTCE", "Maintenance (MTCE)"),
        ("IN O/C", "Incoming Over Current/Infeeder OC (IN O/C)"),
        ("T/LS", "Thermal Load Shedding (T/LS)"),
        ("132KV MTCE", "132 kV Maintenance (132KV MTCE)"),
        ("LIM", "Lightning Impulse/Limit (LIM)"),
        ("tcn", "(tcn)"),
        ("fault", "Fault"),
        ("permit", "Permit"),
    ]

    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE, related_name='interruptions')
    interruption_type = models.CharField(max_length=100, choices=INTERRUPTION_TYPES, db_index=True)
    description = models.TextField(blank=True, null=True)
    occurred_at = models.DateTimeField(db_index=True)
    restored_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ("feeder", "occurred_at", "interruption_type")
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['occurred_at']),
            models.Index(fields=['interruption_type', 'occurred_at']),
            models.Index(fields=['feeder', 'occurred_at']),
        ]

    @property
    def duration_hours(self):
        """Get duration in hours, including unresolved interruptions"""
        if self.restored_at and self.occurred_at:
            # Make both naive for calculation to avoid timezone issues
            occurred = self.occurred_at
            restored = self.restored_at
            
            # Convert to naive if aware
            if occurred.tzinfo is not None:
                occurred = occurred.replace(tzinfo=None)
            if restored.tzinfo is not None:
                restored = restored.replace(tzinfo=None)
            
            return (restored - occurred).total_seconds() / 3600
        elif self.occurred_at:
            # For unresolved interruptions, calculate duration from occurrence to now
            occurred = self.occurred_at
            now = timezone.now()
            
            # Convert to naive if aware
            if occurred.tzinfo is not None:
                occurred = occurred.replace(tzinfo=None)
            if now.tzinfo is not None:
                now = now.replace(tzinfo=None)
            
            return (now - occurred).total_seconds() / 3600
        return 0
    
    @property
    def is_resolved(self):
        """Check if the interruption has been resolved"""
        return self.restored_at is not None
    
    @property
    def is_load_shedding(self):
        """Check if this is a load shedding interruption"""
        return self.interruption_type and 'L/S' in self.interruption_type
    
    def get_duration_hours_at_time(self, reference_time=None):
        """Get duration in hours at a specific reference time
        
        Args:
            reference_time: datetime to calculate duration to (defaults to now)
            
        Returns:
            float: Duration in hours
        """
        if reference_time is None:
            reference_time = timezone.now()
        
        if self.restored_at and self.restored_at <= reference_time:
            # Interruption was resolved before reference time
            return (self.restored_at - self.occurred_at).total_seconds() / 3600
        elif self.occurred_at <= reference_time:
            # Interruption was ongoing at reference time
            return (reference_time - self.occurred_at).total_seconds() / 3600
        else:
            # Interruption hadn't started yet at reference time
            return 0
    
    def __str__(self):
        status = "Resolved" if self.is_resolved else "Ongoing"
        return f"{self.feeder.name} - {self.interruption_type} - {status}"


# Utility function for duration calculations

def calculate_interruption_metrics(interruptions, reference_time=None):
    """
    Calculate comprehensive interruption metrics for a queryset of interruptions.
    
    IMPORTANT DISTINCTIONS:
    - Interruption Duration: ALL interruptions (including L/S and TCN)
    - Turnaround Time: ONLY local faults (excludes L/S and TCN issues)
    
    Args:
        interruptions: QuerySet of FeederInterruption objects
        reference_time: datetime to calculate metrics at (defaults to now)
        
    Returns:
        dict: Dictionary containing various metrics including:
            - total_interruptions: Total count of all interruptions
            - avg_duration_hours: Average duration of ALL interruptions
            - avg_turnaround_time: Average time to restore LOCAL faults only
            - turnaround_count: Count of local faults (excludes L/S and TCN)
            - breakdown_by_type: Detailed breakdown by fault type
    """
    if reference_time is None:
        reference_time = timezone.now()
    
    if not interruptions.exists():
        return {
            'total_interruptions': 0,
            'total_duration_hours': 0,
            'avg_duration_hours': 0,
            'resolved_count': 0,
            'unresolved_count': 0,
            'load_shedding_count': 0,
            'load_shedding_hours': 0,
            'fault_count': 0,
            'fault_hours': 0,
            'avg_fault_duration': 0,
            'avg_turnaround_time': 0,
            'turnaround_count': 0,
            'turnaround_hours': 0,
            'breakdown_by_type': {}
        }
    
    # Define interruption categories
    LOAD_SHEDDING_TYPES = ['L/S', 'L/S GS', '330KV L/S', 'T/LS']
    
    # TCN/Grid issues (not under our control)
    TCN_TYPES = ['132KV E/F', '132KV CB/F', '330KV L/F', '132KV L/F', 'tcn', '330KV L/S']
    
    # Combined exclusions for turnaround time
    TURNAROUND_EXCLUSIONS = set(LOAD_SHEDDING_TYPES + TCN_TYPES)
    
    total_count = interruptions.count()
    total_duration = 0
    resolved_count = 0
    unresolved_count = 0
    load_shedding_count = 0
    load_shedding_hours = 0
    fault_count = 0
    fault_hours = 0
    turnaround_hours = 0
    turnaround_count = 0
    breakdown_by_type = {}
    
    for interruption in interruptions:
        duration = interruption.get_duration_hours_at_time(reference_time)
        total_duration += duration
        
        # Count resolved vs unresolved
        if interruption.is_resolved:
            resolved_count += 1
        else:
            unresolved_count += 1
        
        # Categorize by type
        interrupt_type = interruption.interruption_type or 'Unknown'
        
        if interrupt_type not in breakdown_by_type:
            breakdown_by_type[interrupt_type] = {
                'count': 0,
                'duration': 0
            }
        
        breakdown_by_type[interrupt_type]['count'] += 1
        breakdown_by_type[interrupt_type]['duration'] += duration
        
        # Load shedding vs faults
        if interrupt_type in LOAD_SHEDDING_TYPES:
            load_shedding_count += 1
            load_shedding_hours += duration
        else:
            fault_count += 1
            fault_hours += duration
        
        # Turnaround time calculation (exclude L/S and TCN)
        if interrupt_type not in TURNAROUND_EXCLUSIONS:
            turnaround_hours += duration
            turnaround_count += 1
    
    # Calculate averages
    avg_duration = total_duration / total_count if total_count > 0 else 0
    avg_fault_duration = fault_hours / fault_count if fault_count > 0 else 0
    avg_turnaround = turnaround_hours / turnaround_count if turnaround_count > 0 else 0
    
    # Add average duration to each type in breakdown
    for k, v in breakdown_by_type.items():
        v['avg_duration'] = round(v['duration'] / v['count'], 2) if v['count'] > 0 else 0
    
    return {
        'total_interruptions': total_count,
        'total_duration_hours': round(total_duration, 2),
        'avg_duration_hours': round(avg_duration, 2),
        'resolved_count': resolved_count,
        'unresolved_count': unresolved_count,
        'load_shedding_count': load_shedding_count,
        'load_shedding_hours': round(load_shedding_hours, 2),
        'fault_count': fault_count,
        'fault_hours': round(fault_hours, 2),
        'avg_fault_duration': round(avg_fault_duration, 2),
        'avg_turnaround_time': round(avg_turnaround, 2),
        'turnaround_count': turnaround_count,
        'turnaround_hours': round(turnaround_hours, 2),
        'breakdown_by_type': {
            k: {
                'count': v['count'],
                'duration': round(v['duration'], 2),
                'avg_duration': v['avg_duration']
            }
            for k, v in breakdown_by_type.items()
        }
    }


class DailyHoursOfSupply(UUIDModel, models.Model):
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE)
    date = models.DateField()
    hours_supplied = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        unique_together = ('feeder', 'date')


class FeederEnergyDaily(UUIDModel, models.Model):
    """
    Pre-aggregated total energy delivered per feeder per day (in MWh).
    """
    feeder = models.ForeignKey(
        Feeder,
        on_delete=models.CASCADE,
        help_text="Which feeder this daily total applies to"
    )
    date = models.DateField(
        help_text="Date of the delivery"
    )
    energy_mwh = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        help_text="Total energy delivered (MWh) on that date"
    )

    class Meta:
        unique_together = ("feeder", "date")
        indexes = [
            models.Index(fields=["date", "feeder"]),
        ]
        ordering = ["-date", "feeder"]

    def __str__(self):
        return f"{self.feeder.name} | {self.date} → {self.energy_mwh} MWh"


class FeederEnergyMonthly(UUIDModel, models.Model):
    """
    Pre-aggregated total energy delivered per feeder per month (in MWh).
    """
    feeder = models.ForeignKey(
        Feeder,
        on_delete=models.CASCADE,
        help_text="Which feeder this monthly total applies to"
    )
    period = models.DateField(
        help_text="First day of the period month, e.g. 2025-07-01",
        db_index=True,
    )
    energy_mwh = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        help_text="Total energy delivered (MWh) in that month"
    )

    class Meta:
        unique_together = ("feeder", "period")
        indexes = [
            models.Index(fields=["period", "feeder"]),
        ]
        ordering = ["-period", "feeder"]

    def __str__(self):
        return f"{self.feeder.name} | {self.period:%Y-%m} → {self.energy_mwh} MWh"