# financial/models.py
from django.db import models

from common.models import (
    Band,
    BusinessDistrict,
    DistributionTransformer,
    Feeder,
    UUIDModel,
)
from hr.models import Staff


class OpexCategory(UUIDModel, models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_special = models.BooleanField(default=False)  # Special OPEX types like Salaries, NBET Invoice

    def __str__(self):
        return self.name
    
class GLBreakdown(UUIDModel, models.Model):
    name = models.CharField(max_length=150, unique=True)

    def __str__(self):
        return self.name


class Opex(UUIDModel, models.Model):
    external_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
        help_text="External identifier from DataNest"
    )
    district = models.ForeignKey(
        BusinessDistrict,
        on_delete=models.CASCADE,
        help_text="Which business district this OPEX belongs to"
    )
    date = models.DateField(
        help_text="Date of the expense"
    )
    purpose = models.TextField(
        help_text="What the expenditure was for"
    )
    payee = models.CharField(
        max_length=200,
        help_text="Who was paid"
    )
    # i jsut added this enetity 
    transaction_id = models.PositiveIntegerField(unique=True, help_text="Transaction ID")

    gl_account_number = models.CharField(
        max_length=20,
        help_text="General ledger account number"
    )
    gl_breakdown = models.ForeignKey(
        GLBreakdown,
        on_delete=models.SET_NULL,
        null=True,
        help_text="GL breakdown mapping"
    )
    opex_category = models.ForeignKey(
        OpexCategory,
        on_delete=models.SET_NULL,
        null=True,
        help_text="Categorization of the OPEX (e.g. admin, technical, etc.)"
    )
    debit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.0,
        help_text="Amount sent from HQ (debit side)"
    )
    credit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.0,
        help_text="Actual expense amount (credit side)"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the record was created"
    )

    class Meta:
        ordering = ['-date']
        verbose_name = "OPEX"
        verbose_name_plural = "OPEX Records"

    def __str__(self):
        return (
            f"{self.district.name} | "
            f"{self.date:%Y-%m-%d} | "
            f"{self.opex_category.name if self.opex_category else 'Uncategorized'} | "
            f"₦{self.credit:,}"
        )


class HQOpex(UUIDModel, models.Model):
    external_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
        help_text="External identifier from DataNest"
    )
    date = models.DateField(
        help_text="Date of the HQ expense"
    )
    purpose = models.TextField(
        help_text="What the expenditure was for"
    )
    payee = models.CharField(
        max_length=200,
        help_text="Who was paid"
    )
    gl_account_number = models.CharField(
        max_length=20,
        help_text="General ledger account number"
    )
    gl_breakdown = models.ForeignKey(
        GLBreakdown,
        on_delete=models.SET_NULL,
        null=True,
        help_text="GL breakdown mapping"
    )
    opex_category = models.ForeignKey(
        OpexCategory,
        on_delete=models.SET_NULL,
        null=True,
        help_text="Categorization of the HQ OPEX (e.g. admin, ICT, audit)"
    )
    debit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.0,
        help_text="Amount sent out from HQ (debit)"
    )
    credit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.0,
        help_text="Actual expense amount (credit)"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the record was created"
    )

    class Meta:
        ordering = ['-date']
        verbose_name = "HQ OPEX"
        verbose_name_plural = "HQ OPEX Records"

    def __str__(self):
        return (
            f"HQ | "
            f"{self.date:%Y-%m-%d} | "
            f"{self.opex_category.name if self.opex_category else 'Uncategorized'} | "
            f"₦{self.credit:,}"
        )



class MonthlyRevenueBilled(UUIDModel, models.Model):
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE, related_name='financial_monthly_revenue_billed')
    month = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)



from django.db import models

from common.models import Band, BusinessDistrict, UUIDModel


class SalaryPayment(UUIDModel, models.Model):
    """
    Record when salaries for a given business district/month were paid,
    and exactly which staff member received the payment.
    """
    district = models.ForeignKey(
        BusinessDistrict,
        on_delete=models.CASCADE,
        help_text="Which business district this salary payment covers"
    )
    month = models.DateField(
        help_text="Year-month this salary run is for (e.g. 2025-07-01)"
    )
    staff = models.ForeignKey(
        Staff,
        on_delete=models.SET_NULL,
        null=True,
        help_text="Staff member who received this salary payment"
    )
    payment_date = models.DateField(
        help_text="Date salary was actually paid"
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text="Salary amount paid"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Record creation timestamp"
    )

    class Meta:
        unique_together = ('district', 'month', 'staff')
        ordering = ['-month', 'staff__full_name']

    def __str__(self):
        return (
            f"{self.month:%Y-%m} | "
            f"{self.staff.full_name if self.staff else 'Unknown Staff'} | "
            f"Paid on {self.payment_date:%Y-%m-%d} | ₦{self.amount:,}"
        )


class NBETInvoice(UUIDModel, models.Model):
    """
    Monthly NBET invoice amounts & status.
    """
    month = models.DateField(
        help_text="Invoice month (e.g. 2025-07-01)"
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text="NBET invoice amount"
    )
    is_paid = models.BooleanField(
        default=False,
        help_text="Whether this invoice has been paid"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Record creation timestamp"
    )

    class Meta:
        ordering = ['-month']

    def __str__(self):
        status = "Paid" if self.is_paid else "Unpaid"
        return f"NBET {self.month:%Y-%m}: ₦{self.amount:,} ({status})"


class MOInvoice(UUIDModel, models.Model):
    """
    Monthly Market Operator (MO) invoice amounts & status.
    """
    month = models.DateField(
        help_text="Invoice month (e.g. 2025-07-01)"
    )
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text="MO invoice amount"
    )
    is_paid = models.BooleanField(
        default=False,
        help_text="Whether this invoice has been paid"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Record creation timestamp"
    )

    class Meta:
        ordering = ['-month']

    def __str__(self):
        status = "Paid" if self.is_paid else "Unpaid"
        return f"MO {self.month:%Y-%m}: ₦{self.amount:,} ({status})"


class MYTOTariff(UUIDModel, models.Model):
    """
    Allowed MYTO tariff rates per service band.
    """
    band = models.ForeignKey(
        Band,
        on_delete=models.CASCADE,
        help_text="Service band (A-E)"
    )
    effective_date = models.DateField(
        help_text="Date this tariff rate took effect"
    )
    rate_per_kwh = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text="Allowed tariff (₦/kWh)"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Record creation timestamp"
    )

    class Meta:
        unique_together = ('band', 'effective_date')
        ordering = ['-effective_date']

    def __str__(self):
        return f"{self.band.name} @ ₦{self.rate_per_kwh}/kWh (from {self.effective_date:%Y-%m-%d})"



