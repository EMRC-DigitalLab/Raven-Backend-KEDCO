# common/models
from django.db import models
from django.utils.text import slugify
from django.conf import settings
from uuid import uuid4

class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4)

    class Meta:
        abstract = True

class Band(UUIDModel, models.Model):
    name = models.CharField(max_length=50, unique=True)  # e.g., A, B
    description = models.TextField(blank=True)
    slug = models.SlugField(unique=True, blank=True)

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
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank=True)

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

    name = models.CharField(max_length=100)
    band = models.ForeignKey(Band, on_delete=models.SET_NULL, null=True)
    voltage_level = models.CharField(max_length=10, choices=FEEDER_VOLTAGE_CHOICES)
    substation = models.ForeignKey(InjectionSubstation, on_delete=models.CASCADE, related_name='feeders')
    business_district = models.ForeignKey('BusinessDistrict', on_delete=models.CASCADE, related_name='feeders', null=True, blank=True)
    slug = models.SlugField(unique=True, blank=True)

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



