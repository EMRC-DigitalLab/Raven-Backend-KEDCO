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
    target_hours_per_day = models.DecimalField(
        max_digits=4, decimal_places=1, default=0,
        help_text="NERC/MYTO minimum hours of supply per day for this band (A=20, B=16, C=12, D=8, E=4)"
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

