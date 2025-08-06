# technical/models.py
from django.db import models
from common.models import UUIDModel, Feeder
from django.utils import timezone


class EnergyDelivered(UUIDModel, models.Model):
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE)
    date = models.DateField()
    energy_mwh = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('feeder', 'date')


class HourlyLoad(UUIDModel, models.Model):
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE)
    date = models.DateField()
    hour = models.PositiveSmallIntegerField()  # 0 to 23
    load_mw = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('feeder', 'date', 'hour')


from django.db import models
from common.models import UUIDModel, Feeder
from django.utils import timezone

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

    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE)
    interruption_type = models.CharField(max_length=100, choices=INTERRUPTION_TYPES)
    description = models.TextField(blank=True, null=True)
    occurred_at = models.DateTimeField()
    restored_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ("feeder", "occurred_at", "interruption_type")

    @property
    def duration_hours(self):
        """Get duration in hours, including unresolved interruptions"""
        if self.restored_at and self.occurred_at:
            return (self.restored_at - self.occurred_at).total_seconds() / 3600
        elif self.occurred_at:
            # For unresolved interruptions, calculate duration from occurrence to now
            return (timezone.now() - self.occurred_at).total_seconds() / 3600
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


# Utility functions for duration calculations

def calculate_interruption_metrics(interruptions, reference_time=None, exclude_load_shedding=False):
    """
    Calculate comprehensive interruption metrics for a queryset of interruptions
    
    Args:
        interruptions: QuerySet of FeederInterruption objects
        reference_time: datetime to calculate metrics at (defaults to now)
        exclude_load_shedding: bool, whether to exclude load shedding from fault calculations
        
    Returns:
        dict: Dictionary containing various metrics
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
            'breakdown_by_type': {}
        }
    
    total_count = interruptions.count()
    total_duration = 0
    resolved_count = 0
    unresolved_count = 0
    load_shedding_count = 0
    load_shedding_hours = 0
    fault_count = 0
    fault_hours = 0
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
        breakdown_by_type[interrupt_type] = breakdown_by_type.get(interrupt_type, {
            'count': 0,
            'duration': 0
        })
        breakdown_by_type[interrupt_type]['count'] += 1
        breakdown_by_type[interrupt_type]['duration'] += duration
        
        # Load shedding vs faults
        if interruption.is_load_shedding:
            load_shedding_count += 1
            load_shedding_hours += duration
        else:
            fault_count += 1
            fault_hours += duration
    
    # Calculate averages
    avg_duration = total_duration / total_count if total_count > 0 else 0
    avg_fault_duration = fault_hours / fault_count if fault_count > 0 else 0
    
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
        'breakdown_by_type': {
            k: {
                'count': v['count'],
                'duration': round(v['duration'], 2),
                'avg_duration': round(v['duration'] / v['count'], 2) if v['count'] > 0 else 0
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
