# regulatory/models.py
from django.db import models

from common.models import Feeder, UUIDModel


class MonthlyEnergyOfftake(UUIDModel, models.Model):
    month = models.DateField()
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE)
    available_nomination_mwh = models.DecimalField(max_digits=12, decimal_places=2)
    energy_offtake_mwh = models.DecimalField(max_digits=12, decimal_places=2)
    offtake_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    nerc_target = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)


class MonthlyRevenueRecovery(UUIDModel, models.Model):
    month = models.DateField()
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE)
    allowed_tariff = models.DecimalField(max_digits=10, decimal_places=2)
    revenue_recovered = models.DecimalField(max_digits=15, decimal_places=2)
    recovery_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    nerc_target = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)


class MonthlyUSoASubmission(UUIDModel, models.Model):
    month = models.DateField()
    timeliness = models.DecimalField(max_digits=5, decimal_places=2)
    completeness = models.DecimalField(max_digits=5, decimal_places=2)
    accuracy = models.DecimalField(max_digits=5, decimal_places=2)
    nerc_target_timeliness = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    nerc_target_completeness = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    nerc_target_accuracy = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)


class MonthlyAPIStreamingRate(UUIDModel, models.Model):
    month = models.DateField()
    total_metered = models.IntegerField()
    total_unmetered = models.IntegerField()
    streaming_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    nerc_target = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)


class MonthlyEstimatedBillingCapping(UUIDModel, models.Model):
    month = models.DateField()
    estimated_billing_efficiency = models.DecimalField(max_digits=5, decimal_places=2)
    customers_billed_within_cap = models.DecimalField(max_digits=5, decimal_places=2)
    gross_energy_overbilled = models.DecimalField(max_digits=12, decimal_places=2)
    nerc_target_efficiency = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)


class MonthlyForumDecisionCompliance(UUIDModel, models.Model):
    month = models.DateField()
    decisions_issued = models.IntegerField()
    decisions_implemented = models.IntegerField()
    compliance_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    nerc_target = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)


class MonthlyNERCComplaintResolution(UUIDModel, models.Model):
    month = models.DateField()
    complaints_received = models.IntegerField()
    complaints_resolved = models.IntegerField()
    resolved_within_sla = models.DecimalField(max_digits=5, decimal_places=2)
    nerc_target = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
