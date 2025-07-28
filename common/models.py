# common/models
from django.db import models
from django.utils.text import slugify
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

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


    class Meta:
        unique_together = ('name', 'substation')

    def __str__(self):
        return f"{self.name} - {self.substation}"


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



