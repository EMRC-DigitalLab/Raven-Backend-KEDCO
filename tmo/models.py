# tmo/models.py
from django.db import models


class TMOMonthlySegmentTarget(models.Model):
    """
    Monthly energy and revenue targets per P&L segment (MDI / MDNI).
    Set by management; compared against EnergyDelivered actuals in the TMO dashboard.
    """
    SEGMENT_CHOICES = [
        ('MDI',  'MD Industrial'),
        ('MDNI', 'MD Non-Industrial'),
    ]

    segment = models.CharField(max_length=10, choices=SEGMENT_CHOICES, db_index=True)
    year    = models.PositiveSmallIntegerField()
    month   = models.PositiveSmallIntegerField()

    target_energy_mwh     = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    target_revenue_ngn    = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    target_collection_ngn = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('segment', 'year', 'month')
        ordering = ['-year', '-month', 'segment']

    def __str__(self):
        return f"{self.segment} {self.year}-{self.month:02d}"
