# # technical/signals.py
# from django.db.models.signals import post_save, post_delete
# from django.dispatch import receiver
# from django.db.models import Sum
# from django.db import transaction
# from decimal import Decimal

# from .models import EnergyDelivered, FeederEnergyDaily, FeederEnergyMonthly


# @receiver(post_save, sender=EnergyDelivered)
# def update_energy_summaries_on_save(sender, instance, created, **kwargs):
#     """
#     Update daily & monthly energy summaries when EnergyDelivered is saved.
#     """
#     # If it's a new record, always update
#     if created:
#         pass  # proceed
#     else:
#         # For updates: only proceed if energy_mwh actually changed
#         try:
#             old = EnergyDelivered.objects.get(pk=instance.pk)
#             if old.energy_mwh == instance.energy_mwh:
#                 return  # no change
#         except EnergyDelivered.DoesNotExist:
#             pass  # shouldn't happen, but safe

#     with transaction.atomic():
#         # 1. Update Daily
#         FeederEnergyDaily.objects.update_or_create(
#             feeder=instance.feeder,
#             date=instance.date,
#             defaults={'energy_mwh': instance.energy_mwh}
#         )

#         # 2. Refresh Monthly
#         _refresh_monthly_summary(instance.feeder, instance.date)


# @receiver(post_delete, sender=EnergyDelivered)
# def update_energy_summaries_on_delete(sender, instance, **kwargs):
#     with transaction.atomic():
#         # Delete daily if no EnergyDelivered rows remain
#         if not EnergyDelivered.objects.filter(feeder=instance.feeder, date=instance.date).exists():
#             FeederEnergyDaily.objects.filter(feeder=instance.feeder, date=instance.date).delete()

#         # Always refresh monthly
#         _refresh_monthly_summary(instance.feeder, instance.date)


# def _refresh_monthly_summary(feeder, sample_date):
#     """Recompute monthly total from daily summaries"""
#     month_start = sample_date.replace(day=1)
#     total = FeederEnergyDaily.objects.filter(
#         feeder=feeder,
#         date__year=month_start.year,
#         date__month=month_start.month
#     ).aggregate(total=Sum('energy_mwh'))['total'] or Decimal('0')

#     FeederEnergyMonthly.objects.update_or_create(
#         feeder=feeder,
#         period=month_start,
#         defaults={'energy_mwh': total}
#     )