# reports/financial_service.py
"""
Financial Report Data Service — delegates all calculations to financial/metrics.py.
Single source of truth: financial.metrics functions are shared by both the
dashboard views and this report service.
"""
from datetime import datetime

from financial.metrics import (
    get_financial_overview,
    get_opex_by_category,
    get_opex_by_district,
)


class FinancialReportDataService:
    """Fetches data for Financial report sections by delegating to financial.metrics."""

    def __init__(self, filters):
        """
        filters = {
            'from_date'   : 'YYYY-MM-DD',
            'to_date'     : 'YYYY-MM-DD',
            'district_ids': [uuid, ...],   # optional
            'state_ids'   : [uuid, ...],   # optional (not used yet — district is lowest scope)
        }
        """
        self.filters      = filters
        self.from_date    = self._parse_date(filters.get('from_date'))
        self.to_date      = self._parse_date(filters.get('to_date'))
        self.district_ids = filters.get('district_ids') or None

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

    def section_financial_overview(self):
        return get_financial_overview(self.from_date, self.to_date, self.district_ids)

    def section_opex_by_category(self):
        return get_opex_by_category(self.from_date, self.to_date, self.district_ids)

    def section_opex_by_district(self):
        return get_opex_by_district(self.from_date, self.to_date, self.district_ids)

    # =========================================================================
    # MASTER DISPATCHER
    # =========================================================================

    def get_all_section_data(self, section_type, config=None):  # noqa: ARG002
        dispatch = {
            'financial_overview': self.section_financial_overview,
            'opex_by_category':   self.section_opex_by_category,
            'opex_by_district':   self.section_opex_by_district,
        }
        method = dispatch.get(section_type)
        return method() if method else {}
