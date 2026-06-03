# notifications/signals.py
"""
Signal handlers that translate domain events across all apps into notifications.

Each handler follows the same pattern:
  1. Decide who to notify (role list or specific user)
  2. Call NotificationService.notify / notify_role / notify_user
  3. Keep it short — no business logic here

Adding a new trigger:
  - Connect a post_save / custom signal to the relevant model
  - Call NotificationService with the right category + target_roles
"""
from django.db.models.signals import post_save
from django.dispatch import Signal, receiver

from .services import NotificationService

# ── Custom signals (cross-app events that don't map 1:1 to model saves) ───────
report_generated = Signal()     # sender=report_instance, user=requesting_user
band_changed = Signal()         # feeder_id, feeder_name, old_band, new_band

# ── Report type → frontend print-view path ────────────────────────────────────
_REPORT_TYPE_URL = {
    'technical':   '/technical-report',
    'commercial':  '/commercial-report',
    'financial':   '/financial-report',
    'hr':          '/staff/staff-overview',
    'executive':   '/overview/overviewpage',
    'compliance':  '/regulatory/performance-framework-2024',
    'general':     '/overview/overviewpage',
    'performance': '/overview/overviewpage',
}


# ══════════════════════════════════════════════════════════════════════════════
#  USERS APP
# ══════════════════════════════════════════════════════════════════════════════

@receiver(post_save, sender='users.User')
def on_user_created(sender, instance, created, **kwargs):
    if not created:
        return
    NotificationService.notify_role(
        title="New user registered",
        message=f"A new user '{instance.get_full_name() or instance.username}' "
                f"has been added with the role '{instance.get_role_display()}'.",
        category='system',
        roles=['super_admin', 'admin'],
        priority='low',
        action_url='/admin/users/all',
        metadata={'user_id': instance.id, 'role': instance.role},
    )


@receiver(post_save, sender='users.User')
def on_user_role_changed(sender, instance, created, **kwargs):
    if created:
        return
    # Only fire when the role field actually changed.
    # We compare against the DB snapshot stored by the update_fields sentinel.
    # If update_fields is set and 'role' is not in it, skip.
    update_fields = kwargs.get('update_fields') or []
    if update_fields and 'role' not in update_fields:
        return

    NotificationService.notify_role(
        title="User role updated",
        message=f"The role for '{instance.get_full_name() or instance.username}' "
                f"has been updated to '{instance.get_role_display()}'.",
        category='system',
        roles=['super_admin', 'admin'],
        priority='low',
        action_url='/admin/users/all',
        metadata={'user_id': instance.id, 'new_role': instance.role},
    )


# ══════════════════════════════════════════════════════════════════════════════
#  COMMERCIAL APP
# ══════════════════════════════════════════════════════════════════════════════

@receiver(post_save, sender='commercial.CommercialCustomer')
def on_commercial_customer_created(sender, instance, created, **kwargs):
    if not created:
        return
    NotificationService.notify_role(
        title="New commercial customer added",
        message=f"Customer '{getattr(instance, 'name', str(instance))}' has been added to the system.",
        category='commercial',
        roles=['super_admin', 'admin', 'manager'],
        priority='low',
        action_url='/commercial/commercial-customer',
        metadata={'customer_id': instance.pk},
    )


@receiver(post_save, sender='commercial.MeterReading')
def on_meter_reading_uploaded(sender, instance, created, **kwargs):
    if not created:
        return
    NotificationService.notify_role(
        title="New meter reading uploaded",
        message="A new meter reading has been recorded.",
        category='commercial',
        roles=['super_admin', 'admin', 'manager'],
        priority='low',
        action_url='/commercial/commercial-overview',
        metadata={'reading_id': instance.pk},
    )


# ══════════════════════════════════════════════════════════════════════════════
#  FINANCIAL APP
# ══════════════════════════════════════════════════════════════════════════════

@receiver(post_save, sender='financial.NBETInvoice')
def on_nbet_invoice_created(sender, instance, created, **kwargs):
    if not created:
        return
    NotificationService.notify_role(
        title="New NBET invoice created",
        message="A new NBET invoice has been created.",
        category='financial',
        roles=['super_admin', 'admin', 'manager'],
        priority='medium',
        action_url='/financial/financial-overview',
        metadata={'invoice_id': instance.pk},
    )


@receiver(post_save, sender='financial.MOInvoice')
def on_mo_invoice_created(sender, instance, created, **kwargs):
    if not created:
        return
    NotificationService.notify_role(
        title="New MO invoice created",
        message="A new Market Operator invoice has been created.",
        category='financial',
        roles=['super_admin', 'admin', 'manager'],
        priority='medium',
        action_url='/financial/financial-overview',
        metadata={'invoice_id': instance.pk},
    )


@receiver(post_save, sender='financial.Opex')
def on_opex_created(sender, instance, created, **kwargs):
    if not created:
        return
    NotificationService.notify_role(
        title="New OPEX entry recorded",
        message="A new OPEX entry has been added to the financial records.",
        category='financial',
        roles=['super_admin', 'admin'],
        priority='low',
        action_url='/financial/financial-overview',
        metadata={'opex_id': instance.pk},
    )


# ══════════════════════════════════════════════════════════════════════════════
#  HR APP
# ══════════════════════════════════════════════════════════════════════════════

@receiver(post_save, sender='hr.Staff')
def on_staff_created(sender, instance, created, **kwargs):
    if not created:
        return
    staff_name = getattr(instance, 'name', None) or getattr(instance, 'full_name', None) or str(instance)
    NotificationService.notify_role(
        title="New staff member added",
        message=f"'{staff_name}' has been added to the HR records.",
        category='hr',
        roles=['super_admin', 'admin', 'manager'],
        priority='low',
        action_url='/staff/staff-overview',
        metadata={'staff_id': instance.pk},
    )


@receiver(post_save, sender='financial.SalaryPayment')
def on_salary_payment_created(sender, instance, created, **kwargs):
    if not created:
        return
    NotificationService.notify_role(
        title="Salary payment recorded",
        message="A new salary payment has been recorded.",
        category='financial',
        roles=['super_admin', 'admin'],
        priority='medium',
        action_url='/financial/financial-overview',
        metadata={'salary_id': instance.pk},
    )


# ══════════════════════════════════════════════════════════════════════════════
#  TECHNICAL APP
# ══════════════════════════════════════════════════════════════════════════════

@receiver(post_save, sender='technical.FeederInterruption')
def on_feeder_interruption(sender, instance, created, **kwargs):
    if not created:
        return
    feeder_name = getattr(instance, 'feeder', None)
    feeder_label = str(feeder_name) if feeder_name else 'Unknown feeder'
    NotificationService.notify_role(
        title="Feeder interruption logged",
        message=f"An interruption has been recorded for '{feeder_label}'.",
        category='technical',
        roles=['super_admin', 'admin', 'manager'],
        priority='high',
        action_url='/technical/technical-overview',
        metadata={'interruption_id': str(instance.pk)},
    )


# ══════════════════════════════════════════════════════════════════════════════
#  REPORTS APP  (custom signal — fired from reports/views.py)
# ══════════════════════════════════════════════════════════════════════════════

@receiver(report_generated)
def on_report_generated(_sender, user, report_type, report_title, report_object_id='', **kwargs):
    """
    Notify the requesting user that their report is ready.
    Routes to the correct print-view page based on report category.
    """
    action_url = _REPORT_TYPE_URL.get(report_type, '/overview/overviewpage')
    NotificationService.notify_user(
        title=f"Report ready: {report_title}",
        message=f"Your '{report_title}' report has been generated and is ready to view.",
        category='report',
        user=user,
        priority='high',
        send_email=False,   # In-app only — user chose to generate it, they know it's ready
        action_url=action_url,
        metadata={
            'report_type': report_type,
            'report_object_id': report_object_id,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
#  NOTIFICATIONS APP — Announcement dispatch
# ══════════════════════════════════════════════════════════════════════════════

@receiver(post_save, sender='notifications.Announcement')
def on_announcement_saved(sender, instance, created, **kwargs):
    """Fan out a new announcement to all target users immediately after creation."""
    if not created:
        return
    if not instance.is_active:
        return
    NotificationService.broadcast_announcement(instance)


# ══════════════════════════════════════════════════════════════════════════════
#  BAND CHANGE  (custom signal — fired from technical app when band is updated)
# ══════════════════════════════════════════════════════════════════════════════

@receiver(band_changed)
def on_band_changed(_sender, feeder_id, feeder_name, old_band, new_band, **kwargs):
    NotificationService.notify_band_change(
        feeder_id=feeder_id,
        feeder_name=feeder_name,
        old_band=old_band,
        new_band=new_band,
    )
