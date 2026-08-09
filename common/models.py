# common/models
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4)

    class Meta:
        abstract = True

class Band(UUIDModel, models.Model):
    name = models.CharField(max_length=50, unique=True)  # e.g., A, B
    description = models.TextField(blank=True)
    slug = models.SlugField(unique=True, blank=True)
    minimum_hours = models.DecimalField(
        max_digits=4, decimal_places=1, default=0,
        help_text="NERC mandated minimum service hours per day (e.g. 20 for Band A)"
    )
    priority_order = models.PositiveSmallIntegerField(
        default=99,
        help_text="Allocation priority: 1=highest (Band A), 5=lowest (Band E)"
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name



class State(UUIDModel, models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class BusinessDistrict(UUIDModel, models.Model):
    name = models.CharField(max_length=100)
    state = models.ForeignKey(State, on_delete=models.CASCADE, related_name='districts')
    slug = models.SlugField(unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


    class Meta:
        unique_together = ('name', 'state')

    def __str__(self):
        return f"{self.name} ({self.state.name})"


class InjectionSubstation(UUIDModel, models.Model):
    STATION_TYPE_CHOICES = [
        ('injection', 'Injection'),
        ('transmission', 'Transmission'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank=True)
    state = models.ForeignKey(
        'State', on_delete=models.CASCADE,
        related_name='injection_substations',
        null=True, blank=True,
        help_text="State this station belongs to"
    )
    station_type = models.CharField(
        max_length=15,
        choices=STATION_TYPE_CHOICES,
        default='injection',
        help_text="Whether this is an injection substation (33/11kV) or transmission station (132/33kV)"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active'
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name}"


class FeederManager(models.Manager):
    """Custom manager for Feeder model"""
    
    def onboarded(self):
        """Get only onboarded feeders"""
        return self.filter(is_onboarded=True)
    
    def not_onboarded(self):
        """Get only non-onboarded feeders"""
        return self.filter(is_onboarded=False)
    
    def by_substation_onboarded(self, substation):
        """Get onboarded feeders for a specific substation"""
        return self.filter(substation=substation, is_onboarded=True)
    

class Feeder(UUIDModel, models.Model):
    FEEDER_VOLTAGE_CHOICES = [
        ('11kv', '11kV'),
        ('33kv', '33kV'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    name = models.CharField(max_length=100)
    band = models.ForeignKey(Band, on_delete=models.SET_NULL, null=True)
    voltage_level = models.CharField(max_length=10, choices=FEEDER_VOLTAGE_CHOICES)
    substation = models.ForeignKey(InjectionSubstation, on_delete=models.CASCADE, related_name='feeders')
    business_district = models.ForeignKey('BusinessDistrict', on_delete=models.CASCADE, related_name='feeders', null=True, blank=True)
    slug = models.SlugField(unique=True, blank=True)
    feeder_class = models.CharField(
        max_length=5, blank=True, default='',
        help_text="Classification code e.g. '11K', '33K'"
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='active'
    )

    is_onboarded = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this feeder has been onboarded to the system"
    )
    onboarded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date and time when the feeder was onboarded"
    )
    onboarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='onboarded_feeders',
        help_text="User who onboarded this feeder"
    )

    commercial_is_onboarded = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this feeder is commercially onboarded (MDI/MDNI analytics active)"
    )
    commercial_onboarded_at = models.DateField(
        null=True,
        blank=True,
        help_text="Date from which commercial analytics data is valid for this feeder"
    )

    is_minigrid = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True for solar/minigrid feeders (e.g. Haske Solar) — tracked separately in TMO"
    )

    PL_SEGMENT_CHOICES = [
        ('MDI',     'MD Industrial'),
        ('MDNI',    'MD Non-Industrial'),
        ('Regions', 'Regions'),
    ]
    pl_segment = models.CharField(
        max_length=10,
        choices=PL_SEGMENT_CHOICES,
        null=True, blank=True,
        db_index=True,
        help_text="P&L segment: MDI / MDNI / Regions — imported from Feeders Segmentation Excel"
    )

    monitoring_end_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "If set, this feeder is under active monitoring until this date. "
            "Used for newly commissioned feeders tracked in the TMO dashboard."
        ),
    )

    objects = FeederManager()

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


    class Meta:
        unique_together = ('name', 'substation')
        indexes = [
            models.Index(fields=['is_onboarded']),
            models.Index(fields=['substation', 'is_onboarded']),
        ]

    def __str__(self):
        return f"{self.name} - {self.substation}"
    
    @classmethod
    def get_onboarded(cls):
        """Get only onboarded feeders"""
        return cls.objects.filter(is_onboarded=True)
    
    @classmethod
    def get_onboarded_count(cls):
        """Get count of onboarded feeders"""
        return cls.objects.filter(is_onboarded=True).count()


class DistributionTransformer(UUIDModel, models.Model):
    name = models.CharField(max_length=100)
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE, related_name='transformers')
    slug = models.SlugField(unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


    # class Meta:
    #     unique_together = ('name', 'feeder')

    def __str__(self):
        return f"{self.name} - {self.feeder}"


class PowerTransformer(UUIDModel, models.Model):
    """Power transformer at injection/transmission stations (33/11kV step-down).
    NOT the same as DistributionTransformer (street-level 11kV/415V)."""
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('maintenance', 'Maintenance'),
    ]

    name = models.CharField(max_length=255)
    capacity_mva = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Transformer capacity in MVA (e.g. 15.00, 7.50)"
    )
    voltage_rating = models.CharField(
        max_length=20, blank=True, default='33/11kV',
        help_text="e.g. '33/11kV'"
    )
    manufacturer = models.CharField(max_length=100, blank=True, default='')
    installation_year = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default='active'
    )
    slug = models.SlugField(unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.capacity_mva}MVA)"


class FeederTransformerMapping(UUIDModel, models.Model):
    """Maps feeders (typically 11kV) to power transformers at injection stations."""
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]

    feeder = models.ForeignKey(
        'Feeder', on_delete=models.CASCADE,
        related_name='transformer_mappings'
    )
    transformer = models.ForeignKey(
        'PowerTransformer', on_delete=models.CASCADE,
        related_name='feeder_mappings'
    )
    connection_type = models.CharField(
        max_length=50, blank=True, default='',
        help_text="e.g. 'primary', 'backup'"
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='active'
    )

    class Meta:
        unique_together = ('feeder', 'transformer')

    def __str__(self):
        return f"{self.feeder.name} → {self.transformer.name}"


class FeederSupplyRelationship(UUIDModel, models.Model):
    """Maps which feeder supplies which other feeder (33kV → 11kV supply chain)."""
    SUPPLY_TYPE_CHOICES = [
        ('primary', 'Primary'),
        ('backup', 'Backup'),
        ('emergency', 'Emergency'),
        ('parallel', 'Parallel'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('maintenance', 'Maintenance'),
    ]

    supplier_feeder = models.ForeignKey(
        'Feeder', on_delete=models.CASCADE,
        related_name='supplied_feeders',
        help_text="The feeder that supplies power (typically 33kV)"
    )
    supplied_feeder = models.ForeignKey(
        'Feeder', on_delete=models.CASCADE,
        related_name='supplier_feeders',
        help_text="The feeder that receives power (typically 11kV)"
    )
    supply_type = models.CharField(
        max_length=15,
        choices=SUPPLY_TYPE_CHOICES,
        default='primary'
    )
    supply_capacity_mw = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="MW capacity for this supply relationship"
    )
    priority_order = models.PositiveSmallIntegerField(
        default=1,
        help_text="1 = highest priority"
    )
    effective_from = models.DateTimeField(auto_now_add=True)
    effective_until = models.DateTimeField(
        null=True, blank=True,
        help_text="NULL = currently active"
    )
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default='active'
    )
    operational_notes = models.TextField(blank=True, default='')

    class Meta:
        indexes = [
            models.Index(fields=['supplier_feeder', 'status']),
            models.Index(fields=['supplied_feeder', 'status']),
        ]

    def __str__(self):
        return f"{self.supplier_feeder.name} → {self.supplied_feeder.name} ({self.supply_type})"


class FeederCouplingEvent(UUIDModel, models.Model):
    """
    A temporary network reconfiguration: when a feeder goes on fault, TCN
    couples (temporarily reassigns) some or all of its downstream network to
    a different, healthy feeder to keep customers supplied while it's fixed.

    Deliberately a separate model from FeederSupplyRelationship, which
    represents PERMANENT topology. Mixing temporary coupling into that table
    would make it impossible to tell later whether a past relationship
    change was a genuine correction or a temporary fault reroute — exactly
    the ambiguity that made reconciling TMO's numbers slow in the first
    place, when several genuine topology mistakes had to be found by hand.

    Always logged after the fact from TCN's own reporting, so start_date and
    end_date are always manually entered and fully backdatable — never
    assumed to be "today". A coupling can be left open (end_date=None)
    indefinitely until someone comes back and closes it, since TMO often
    won't know the end date at the time they log the start.
    """
    SCOPE_CHOICES = [
        ('all', 'All downstream feeders'),
        ('selected', 'Selected feeders only'),
    ]

    faulted_feeder = models.ForeignKey(
        'Feeder', on_delete=models.CASCADE,
        related_name='coupling_events_as_faulted',
        help_text="The feeder that went on fault and had its network coupled elsewhere"
    )
    coupled_to_feeder = models.ForeignKey(
        'Feeder', on_delete=models.CASCADE,
        related_name='coupling_events_as_coupled_to',
        help_text="The feeder that temporarily took over the faulted feeder's downstream network"
    )

    scope = models.CharField(
        max_length=10, choices=SCOPE_CHOICES, default='all',
        help_text="Whether ALL of the faulted feeder's downstream network moved, or only specific feeders"
    )
    selected_feeders = models.ManyToManyField(
        'Feeder', blank=True,
        related_name='coupling_events_as_selected_child',
        help_text="Only used when scope='selected' — the specific feeders that were coupled, "
                   "rather than the faulted feeder's entire downstream network"
    )

    start_date = models.DateField(
        help_text="The date the coupling actually started, per TCN's own report — "
                   "not the date this record was created"
    )
    end_date = models.DateField(
        null=True, blank=True,
        help_text="The date the coupling actually ended. Left blank while still open/ongoing."
    )

    notes = models.TextField(blank=True, default='')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='logged_coupling_events',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='closed_coupling_events',
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['faulted_feeder', 'start_date', 'end_date']),
            models.Index(fields=['coupled_to_feeder', 'start_date', 'end_date']),
        ]
        ordering = ['-start_date', '-created_at']

    def is_active_on(self, check_date):
        """True if this coupling covers the given date (inclusive)."""
        if self.start_date > check_date:
            return False
        if self.end_date is not None and self.end_date < check_date:
            return False
        return True

    def affected_feeder_ids(self):
        """
        The set of feeder IDs actually moved by this event.
        scope='selected' uses the explicit list chosen when logging it.
        scope='all' resolves against the faulted feeder's CURRENT active
        children — there's no dated snapshot of the topology itself to
        check against, so this is the best available answer. If the
        faulted feeder's real children were different on the historical
        date this coupling covers (e.g. a topology correction was made
        since), log this event with scope='selected' instead to be exact.
        """
        if self.scope == 'selected':
            return set(self.selected_feeders.values_list('id', flat=True))
        return set(
            FeederSupplyRelationship.objects.filter(
                supplier_feeder=self.faulted_feeder, status='active'
            ).values_list('supplied_feeder_id', flat=True)
        )

    def __str__(self):
        end = self.end_date or 'ongoing'
        return f"{self.faulted_feeder.name} → {self.coupled_to_feeder.name} ({self.start_date} to {end})"

