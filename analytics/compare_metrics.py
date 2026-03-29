"""
analytics/compare_metrics.py

Metrics registry for the Compare Engine.
Defines every comparable metric: which module it belongs to, display metadata,
and which entity types support it (state / district / feeder / band).

Access control: the module field is matched against the user's accessible sections
(UserSectionAccess + TemporaryAccess). Users only see metrics for modules they
have access to.
"""

METRICS_REGISTRY: dict = {

    # ── TECHNICAL ─────────────────────────────────────────────────────────────
    'hours_of_supply': {
        'module': 'technical',
        'label': 'Hours of Supply',
        'unit': 'hrs/day',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Average daily hours of electricity supply (0–24)',
    },
    'energy_delivered': {
        'module': 'technical',
        'label': 'Energy Delivered',
        'unit': 'MWh',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Total energy delivered — meter-primary with HourlyLoad fallback',
    },
    'peak_load': {
        'module': 'technical',
        'label': 'Peak Load',
        'unit': 'MW',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Maximum recorded load (MW) in the period',
    },
    'total_interruptions': {
        'module': 'technical',
        'label': 'Total Interruptions',
        'unit': 'count',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Feeder tripping count — all interruptions that occurred in the period',
    },
    'avg_interruption_duration': {
        'module': 'technical',
        'label': 'Avg Interruption Duration',
        'unit': 'hrs',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Average hours per interruption event (total duration / event count)',
    },
    'interruption_hours': {
        'module': 'technical',
        'label': 'Interruption Hours',
        'unit': 'hrs/day',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Average daily interruption hours (= 24 − hours_of_supply)',
    },
    'turnaround_time': {
        'module': 'technical',
        'label': 'Turnaround Time',
        'unit': 'hrs/day',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Avg local fault restoration time per day (excl. load shedding & TCN faults)',
    },
    'feeder_count': {
        'module': 'technical',
        'label': 'Feeder Count',
        'unit': 'count',
        'entity_types': ['state', 'district', 'band'],
        'description': 'Number of onboarded feeders',
    },
    'availability_pct': {
        'module': 'technical',
        'label': 'Availability',
        'unit': '%',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'System availability % = (hours_of_supply / 24) × 100',
    },

    # ── COMMERCIAL ────────────────────────────────────────────────────────────
    'total_customers': {
        'module': 'commercial',
        'label': 'Total Customers',
        'unit': 'count',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Total registered MDI + MDNI customers',
    },
    'customers_read': {
        'module': 'commercial',
        'label': 'Customers Read',
        'unit': 'count',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Customers with at least one meter reading in the period',
    },
    'coverage_rate': {
        'module': 'commercial',
        'label': 'Coverage Rate',
        'unit': '%',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': '% of customers with a reading in the period (customers_read / total × 100)',
    },
    'revenue_billed': {
        'module': 'commercial',
        'label': 'Revenue Billed',
        'unit': 'NGN',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Total billed revenue including VAT (energy_charge × 1.075)',
    },
    'energy_billed_kwh': {
        'module': 'commercial',
        'label': 'Energy Billed',
        'unit': 'kWh',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Total energy billed from actual meter readings (sum of billed_consumption)',
    },
    'billing_efficiency': {
        'module': 'commercial',
        'label': 'Billing Efficiency',
        'unit': '%',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Energy billed (kWh) / energy delivered (kWh) × 100',
    },
    'atc_loss': {
        'module': 'commercial',
        'label': 'AT&C Loss',
        'unit': '%',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Aggregate Technical & Commercial losses = 100 − billing_efficiency',
    },
    'arpu': {
        'module': 'commercial',
        'label': 'ARPU',
        'unit': 'NGN',
        'entity_types': ['state', 'district', 'feeder', 'band'],
        'description': 'Average Revenue Per Customer = revenue_billed / customers_read',
    },

    # ── FINANCIAL ─────────────────────────────────────────────────────────────
    'total_opex': {
        'module': 'financial',
        'label': 'Total OPEX',
        'unit': 'NGN',
        'entity_types': ['state', 'district'],
        'description': 'Total operational expenditure (Opex credit + debit) for the period',
    },
    'salary_cost': {
        'module': 'financial',
        'label': 'Salary Cost',
        'unit': 'NGN',
        'entity_types': ['state', 'district'],
        'description': 'Total salary payments (SalaryPayment) for months within the period',
    },
    'total_cost': {
        'module': 'financial',
        'label': 'Total Cost',
        'unit': 'NGN',
        'entity_types': ['state', 'district'],
        'description': 'OPEX + Salary Cost (district-attributable costs)',
    },

    # ── HR ────────────────────────────────────────────────────────────────────
    'total_staff': {
        'module': 'hr',
        'label': 'Total Staff',
        'unit': 'count',
        'entity_types': ['state', 'district'],
        'description': 'Active staff headcount as of to_date',
    },
    'attrition_rate': {
        'module': 'hr',
        'label': 'Attrition Rate',
        'unit': '%',
        'entity_types': ['state', 'district'],
        'description': 'Staff exits ÷ total staff × 100 for the period',
    },
    'new_hires': {
        'module': 'hr',
        'label': 'New Hires',
        'unit': 'count',
        'entity_types': ['state', 'district'],
        'description': 'Staff hired within the period (hire_date in range)',
    },
    'avg_tenure_years': {
        'module': 'hr',
        'label': 'Avg Tenure',
        'unit': 'yrs',
        'entity_types': ['state', 'district'],
        'description': 'Average years of service for currently active staff',
    },
    'total_wage_bill': {
        'module': 'hr',
        'label': 'Total Wage Bill',
        'unit': 'NGN',
        'entity_types': ['state', 'district'],
        'description': 'Sum of current monthly salaries for active staff',
    },
}

# Quick lookup: metric key → module name
METRIC_MODULE_MAP: dict[str, str] = {
    k: v['module'] for k, v in METRICS_REGISTRY.items()
}

# Metrics grouped by module (for the /available/ endpoint)
METRICS_BY_MODULE: dict[str, list] = {}
for _k, _v in METRICS_REGISTRY.items():
    METRICS_BY_MODULE.setdefault(_v['module'], []).append({'key': _k, **_v})
