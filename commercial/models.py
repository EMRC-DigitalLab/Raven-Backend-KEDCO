# commercial/models.py
from django.db import models
from django.utils import timezone

from common.models import Band, DistributionTransformer, Feeder, UUIDModel


class Customer(UUIDModel, models.Model):
    METERING_TYPE_CHOICES = [
        ('MD1', 'MD1'),
        ('MD2', 'MD2'),
        ('Non-MD', 'Non-MD'),
    ]

    CATEGORY_CHOICES = [
        ('Prepaid', 'Prepaid'),
        ('Postpaid', 'Postpaid'),
        ('Unmetered', 'Unmetered'),
    ]

    name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    metering_type = models.CharField(max_length=20, choices=METERING_TYPE_CHOICES)
    band = models.ForeignKey(Band, on_delete=models.SET_NULL, null=True, blank=True)
    transformer = models.ForeignKey(DistributionTransformer, on_delete=models.PROTECT)

    joined_date = models.DateField(default=timezone.now)

    def __str__(self):
        return self.name


class MonthlyEnergyBilled(UUIDModel, models.Model):
    feeder = models.ForeignKey('common.Feeder', on_delete=models.CASCADE)
    month = models.DateField()
    energy_mwh = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('feeder', 'month')


class MonthlyCustomerStats(UUIDModel, models.Model):
    feeder = models.ForeignKey('common.Feeder', on_delete=models.CASCADE)
    month = models.DateField()
    customer_count = models.PositiveIntegerField()
    customers_billed = models.PositiveIntegerField()
    customer_response_count = models.PositiveIntegerField()

    class Meta:
        unique_together = ('feeder', 'month')


class SalesRepresentative(UUIDModel, models.Model):
    name = models.CharField(max_length=255)
    assigned_transformers = models.ManyToManyField(DistributionTransformer)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class SalesRepPerformance(UUIDModel, models.Model):
    sales_rep = models.ForeignKey(SalesRepresentative, on_delete=models.CASCADE)
    month = models.DateField()
    outstanding_billed = models.DecimalField(max_digits=15, decimal_places=2)
    current_billed = models.DecimalField(max_digits=15, decimal_places=2)
    collections = models.DecimalField(max_digits=15, decimal_places=2)
    daily_run_rate = models.DecimalField(max_digits=15, decimal_places=2)
    collections_on_outstanding = models.DecimalField(max_digits=15, decimal_places=2)
    active_accounts = models.PositiveIntegerField()
    suspended_accounts = models.PositiveIntegerField()

class MonthlyRevenueBilled(UUIDModel, models.Model):
    sales_rep = models.ForeignKey(SalesRepresentative, on_delete=models.CASCADE)
    transformer = models.ForeignKey(DistributionTransformer, on_delete=models.CASCADE)
    month = models.DateField()  # Always use first day of month (e.g., 2025-03-01)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    customers_billed = models.PositiveIntegerField(
        default=0,
        help_text="Number of customers billed in this month"
    )

    class Meta:
        unique_together = ('sales_rep', 'transformer', 'month')
        ordering = ['-month', 'sales_rep', 'transformer']

    def __str__(self):
        return f"{self.sales_rep.name} - {self.transformer.name} - {self.month.strftime('%Y-%m')} - ₦{self.amount} ({self.customers_billed} customers)"

    def average_revenue_per_customer(self):
        """Calculate average revenue per customer"""
        if self.customers_billed > 0:
            return self.amount / self.customers_billed
        return 0

    @property
    def revenue_per_customer(self):
        """Property for easy access to average revenue per customer"""
        return self.average_revenue_per_customer()
    
    
class DailyCollection(UUIDModel, models.Model):
    COLLECTION_TYPE_CHOICES = (
        ('Prepaid', 'Prepaid'),
        ('Postpaid', 'Postpaid'),
    )

    VENDOR_CHOICES = [
        ('BuyPower.ng', 'BuyPower.ng'),
        ('Banahim.net', 'Banahim.net'),
        ('Bank',        'Bank'),
        ('Cash',        'Cash'),
        ('POS',         'POS'),
        ('powershop.ng','powershop.ng'),
        ('Remita',      'Remita'),
    ]

    sales_rep = models.ForeignKey(SalesRepresentative, on_delete=models.CASCADE)
    transformer = models.ForeignKey(DistributionTransformer, on_delete=models.CASCADE)
    date = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    collection_type = models.CharField(max_length=10, choices=COLLECTION_TYPE_CHOICES)
    vendor_name = models.CharField(
        max_length=20,
        choices=VENDOR_CHOICES,
        help_text="Vendor through which collection was made"
    )
    customers_collected = models.PositiveIntegerField(
        default=0,
        help_text="Number of customers who made payments/collections on this date"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("sales_rep", "transformer", "date", "collection_type", "vendor_name")
        ordering = ['-date', 'sales_rep', 'transformer']

    def __str__(self):
        return f"{self.sales_rep.name} - {self.transformer.name} - {self.date} - ₦{self.amount} ({self.customers_collected} customers)"

    def average_collection_per_customer(self):
        """Calculate average collection per customer"""
        if self.customers_collected > 0:
            return self.amount / self.customers_collected
        return 0

    @property
    def collection_per_customer(self):
        """Property for easy access to average collection per customer"""
        return self.average_collection_per_customer()


class MonthlyCommercialSummary(UUIDModel):
    sales_rep = models.ForeignKey(SalesRepresentative, on_delete=models.CASCADE)
    transformer = models.ForeignKey(DistributionTransformer, on_delete=models.CASCADE)
    month = models.DateField()

    customers_billed = models.PositiveIntegerField(default=0)
    customers_responded = models.PositiveIntegerField(default=0)

    revenue_billed = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    revenue_collected = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("sales_rep", "transformer", "month")

    def __str__(self):
        return f"{self.sales_rep.name} - {self.transformer.name} - {self.month.strftime('%Y-%m')}"