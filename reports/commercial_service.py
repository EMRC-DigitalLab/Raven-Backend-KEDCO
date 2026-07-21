# reports/commercial_service.py
"""
Commercial Report Data Service — delegates all calculations to commercial/analytics_utils.py.
Single source of truth: analytics_utils functions are shared by both the
dashboard views and this report service.
"""
from datetime import datetime

from commercial.analytics_utils import (
    compute_period_baseline,
    get_commercial_coverage,
    get_commercial_energy,
    get_commercial_overview,
    get_customer_queryset,
    get_customer_type_summary,
    get_reading_queryset,
    get_revenue_by_district,
    get_revenue_by_feeder,
)


class CommercialReportDataService:
    """Fetches data for Commercial report sections by delegating to commercial.analytics_utils."""

    def __init__(self, filters):
        """
        filters = {
            'from_date'    : 'YYYY-MM-DD',
            'to_date'      : 'YYYY-MM-DD',
            'feeder_ids'   : [uuid, ...],   # optional
            'district_ids' : [uuid, ...],   # optional
            'state_ids'    : [uuid, ...],   # optional
            'customer_type': 'MDI'|'MDNI',  # optional
            'voltage_level': '11KV'|'33KV', # optional
        }
        """
        self.filters   = filters
        self.from_date = self._parse_date(filters.get('from_date'))
        self.to_date   = self._parse_date(filters.get('to_date'))

        self.feeder_ids = filters.get('feeder_ids') or []

        self.customer_qs = get_customer_queryset(
            feeder_ids=self.feeder_ids,
            district_ids=filters.get('district_ids'),
            state_ids=filters.get('state_ids'),
            customer_type=filters.get('customer_type'),
            voltage_level=filters.get('voltage_level'),
        )

        self.reading_qs = get_reading_queryset(
            self.customer_qs,
            self.from_date,
            self.to_date,
            reading_type=filters.get('customer_type'),  # align reading_type with customer_type filter
        )

    def _parse_date(self, date_val):
        if isinstance(date_val, str):
            date_str = date_val.split('T')[0].split(' ')[0].strip()
            return datetime.strptime(date_str, '%Y-%m-%d').date()
        elif isinstance(date_val, datetime):
            return date_val.date()
        return date_val

    # =========================================================================
    # SECTION DATA METHODS
    # =========================================================================

    def section_commercial_overview(self, normalize=False):
        # normalize=False: management reports show actual billed totals for the
        # period, not period-normalised projections.
        # Build an unfiltered reading_qs (all reading types) so MDI and MDNI
        # splits are always present regardless of any customer_type filter.
        from commercial.analytics_utils import get_reading_queryset
        full_reading_qs = get_reading_queryset(
            self.customer_qs, self.from_date, self.to_date
        )
        return get_commercial_overview(
            self.customer_qs,
            full_reading_qs,
            self.feeder_ids,
            self.from_date,
            self.to_date,
            normalize=normalize,
        )

    def section_revenue_by_district(self):
        return get_revenue_by_district(self.customer_qs, self.from_date, self.to_date)

    def section_customer_type_summary(self):
        period_days = (self.to_date - self.from_date).days + 1
        baseline    = compute_period_baseline(self.reading_qs, self.from_date, self.to_date)
        return get_customer_type_summary(
            self.customer_qs, self.reading_qs,
            period_days=period_days,
            period_start=self.from_date,
            customer_baseline=baseline,
        )

    def section_commercial_coverage(self):
        return get_commercial_coverage(
            self.customer_qs, self.reading_qs, self.from_date, self.to_date
        )

    def section_commercial_energy(self):
        return get_commercial_energy(
            self.reading_qs, self.feeder_ids, self.from_date, self.to_date
        )

    def section_revenue_by_feeder(self):
        return get_revenue_by_feeder(self.customer_qs, self.from_date, self.to_date)

    # =========================================================================
    # MASTER DISPATCHER
    # =========================================================================

    def get_all_section_data(self, section_type, config=None):  # noqa: ARG002
        dispatch = {
            'commercial_overview':    self.section_commercial_overview,
            'revenue_by_district':    self.section_revenue_by_district,
            'customer_type_summary':  self.section_customer_type_summary,
            'commercial_coverage':    self.section_commercial_coverage,
            'commercial_energy':      self.section_commercial_energy,
            'revenue_by_feeder':      self.section_revenue_by_feeder,
        }
        method = dispatch.get(section_type)
        return method() if method else {}
