# backend/financial/admin.py

from django.contrib import admin

from .models import (
    GLBreakdown,
    HQOpex,
    MOInvoice,
    MonthlyRevenueBilled,
    MYTOTariff,
    NBETInvoice,
    Opex,
    OpexCategory,
    SalaryPayment,
)


@admin.register(OpexCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_special']
    search_fields = ['name']

@admin.register(GLBreakdown)
class GLBreakdownAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']

@admin.register(Opex)
class OpexAdmin(admin.ModelAdmin):
    list_display = ['date', 'district', 'opex_category', 'debit', 'credit']
    list_filter = ['opex_category', 'district', 'date']
    search_fields = ['purpose', 'payee', 'gl_account_number']

@admin.register(HQOpex)
class HQOpexAdmin(admin.ModelAdmin):
    list_display = ['date', 'opex_category', 'payee', 'credit']
    search_fields = ['purpose', 'payee', 'gl_account_number']
    list_filter = ['opex_category', 'gl_breakdown', 'date']


@admin.register(MonthlyRevenueBilled)
class MonthlyRevenueBilledAdmin(admin.ModelAdmin):
    list_display = ['feeder', 'month', 'amount']
    list_filter = ['month', 'feeder']
    search_fields = ['feeder__name']

@admin.register(SalaryPayment)
class SalaryPaymentAdmin(admin.ModelAdmin):
    list_display = ['district', 'staff', 'month', 'payment_date', 'amount']
    list_filter = ['month', 'district']
    search_fields = ['staff__full_name', 'district__name']

@admin.register(NBETInvoice)
class NBETInvoiceAdmin(admin.ModelAdmin):
    list_display = ['month', 'amount', 'is_paid', 'created_at']
    list_filter = ['is_paid', 'month']
    date_hierarchy = 'month'

@admin.register(MOInvoice)
class MOInvoiceAdmin(admin.ModelAdmin):
    list_display = ['month', 'amount', 'is_paid', 'created_at']
    list_filter = ['is_paid', 'month']
    date_hierarchy = 'month'

@admin.register(MYTOTariff)
class MYTOTariffAdmin(admin.ModelAdmin):
    list_display = ['band', 'effective_date', 'rate_per_kwh']
    list_filter = ['band', 'effective_date']
    search_fields = ['band__name']
