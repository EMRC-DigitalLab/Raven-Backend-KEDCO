# reports/hr_service.py
"""
HR Report Data Service — delegates all calculations to hr/metrics.py.
Single source of truth: hr.metrics functions are shared by both the
dashboard views and this report service.
"""
from datetime import datetime

from hr.metrics import (
    get_active_staff,
    get_attrition_analysis,
    get_department_headcount,
    get_hr_overview,
    get_recruitment_summary,
    get_staff_metrics,
    get_wage_bill,
)


class HRReportDataService:
    """Fetches data for HR report sections by delegating to hr.metrics."""

    def __init__(self, filters):
        """
        filters = {
            'from_date'     : 'YYYY-MM-DD',
            'to_date'       : 'YYYY-MM-DD',
            'department_ids': [uuid, ...],   # optional
            'grade_levels'  : ['associate'], # optional
            'district_ids'  : [uuid, ...],   # optional
            'state_ids'     : [uuid, ...],   # optional
        }
        """
        self.filters = filters
        self.from_date = self._parse_date(filters.get('from_date'))
        self.to_date = self._parse_date(filters.get('to_date'))

        self.staff_qs = get_active_staff(
            to_date=self.to_date,
            department_ids=filters.get('department_ids'),
            grade_levels=filters.get('grade_levels'),
            district_ids=filters.get('district_ids'),
            state_ids=filters.get('state_ids'),
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

    def section_hr_overview(self):
        return get_hr_overview(self.staff_qs, self.from_date, self.to_date)

    def section_staff_metrics(self):
        return get_staff_metrics(self.staff_qs)

    def section_wage_bill(self):
        return get_wage_bill(self.staff_qs)

    def section_department_headcount(self):
        return get_department_headcount(self.staff_qs)

    def section_attrition_analysis(self):
        return get_attrition_analysis(self.from_date, self.to_date)

    def section_recruitment_summary(self):
        return get_recruitment_summary(self.from_date, self.to_date)

    # =========================================================================
    # MASTER DISPATCHER
    # =========================================================================

    def get_all_section_data(self, section_type, config=None):  # noqa: ARG002
        dispatch = {
            'hr_overview':          self.section_hr_overview,
            'staff_metrics':        self.section_staff_metrics,
            'wage_bill_analysis':   self.section_wage_bill,
            'department_headcount': self.section_department_headcount,
            'attrition_analysis':   self.section_attrition_analysis,
            'recruitment_summary':  self.section_recruitment_summary,
        }
        method = dispatch.get(section_type)
        return method() if method else {}
