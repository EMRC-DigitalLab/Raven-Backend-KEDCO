# utils/admin_helpers.py
from django.utils.html import format_html
from decimal import Decimal

def format_decimal(value, format_spec=',.2f'):
    """Safely format Decimal for admin display"""
    if value is None:
        return '—'
    return format(float(value), format_spec)