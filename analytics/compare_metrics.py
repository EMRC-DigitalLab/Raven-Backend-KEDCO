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

    # ── GRID LENS ─────────────────────────────────────────────────────────────
    # Loss decomposition metrics. Computed from EA + Stream B.
    # Supported at state / district / station — the three geographic scopes
    # where EA billing data is available. Access gated by the grid_lens section.

    'gl_ea_received_mwh': {
        'module': 'grid_lens',
        'label': 'EA Received (MWh)',
        'unit': 'MWh',
        'entity_types': ['state', 'district', 'station'],
        'description': (
            'TCN-agreed energy received — sum of EAMonthlyReturn.station_billing_mwh '
            'for all billing periods that fall within the comparison range.'
        ),
    },
    'gl_feeder_distributed_mwh': {
        'module': 'grid_lens',
        'label': 'Feeder Distributed — Stream B (MWh)',
        'unit': 'MWh',
        'entity_types': ['state', 'district', 'station'],
        'description': (
            'Stream B: total feeder technical energy from the monitoring system. '
            'Energy measured as it left the substation into distribution feeders.'
        ),
    },
    'gl_metering_gap_mwh': {
        'module': 'grid_lens',
        'label': 'Metering Gap (MWh)',
        'unit': 'MWh',
        'entity_types': ['state', 'district', 'station'],
        'description': (
            'EA received − feeder distributed. Energy unaccounted for between the '
            'TCN transformer meter and the feeder monitoring system. '
            'Includes transformer losses, auxiliary loads, and meter accuracy gaps.'
        ),
    },
    'gl_metering_gap_pct': {
        'module': 'grid_lens',
        'label': 'Metering Gap (%)',
        'unit': '%',
        'entity_types': ['state', 'district', 'station'],
        'description': 'metering_gap_mwh / ea_received_mwh × 100.',
    },
    'gl_transmission_efficiency': {
        'module': 'grid_lens',
        'label': 'Transmission Efficiency (%)',
        'unit': '%',
        'entity_types': ['state', 'district', 'station'],
        'description': (
            'feeder_distributed / ea_received × 100. '
            'Share of TCN-received energy that reaches the feeder distribution level.'
        ),
    },
    'gl_kv33_mwh': {
        'module': 'grid_lens',
        'label': '33kV Stream B (MWh)',
        'unit': 'MWh',
        'entity_types': ['state', 'district', 'station'],
        'description': 'Stream B energy from 33kV feeders for this entity and period.',
    },
    'gl_kv11_mwh': {
        'module': 'grid_lens',
        'label': '11kV Stream B (MWh)',
        'unit': 'MWh',
        'entity_types': ['state', 'district', 'station'],
        'description': 'Stream B energy from 11kV feeders for this entity and period.',
    },
    'gl_tier_gap_pct': {
        'module': 'grid_lens',
        'label': '33kV → 11kV Tier Gap (%)',
        'unit': '%',
        'entity_types': ['state', 'district', 'station'],
        'description': (
            '(33kV Stream B − 11kV Stream B) / 33kV Stream B × 100. '
            'Energy measured at the 33kV tier vs the downstream 11kV tier. '
            'Gap reflects distribution losses between voltage levels.'
        ),
    },
    'gl_stream_b_completeness': {
        'module': 'grid_lens',
        'label': 'Stream B Data Completeness (%)',
        'unit': '%',
        'entity_types': ['state', 'district', 'station'],
        'description': (
            'Average SCADA monitoring data completeness % across all feeder '
            'technical energy records in scope. Low completeness inflates the metering gap.'
        ),
    },

    # ── ENERGY ACCOUNT ────────────────────────────────────────────────────────
    # EA billing period = month + year. When comparing with from_date/to_date,
    # the EA fetcher aggregates all monthly returns whose billing month falls
    # within the requested date range.

    'ea_billing_mwh': {
        'module': 'energy_account',
        'label': 'EA Billing MWh',
        'unit': 'MWh',
        'entity_types': ['station', 'feeder'],
        'description': 'Total energy billed from the monthly return (station_billing_mwh). '
                       'This is the authoritative NBET invoice figure.',
    },
    'ea_stream_a_mwh': {
        'module': 'energy_account',
        'label': 'Stream A — Transformer Read (MWh)',
        'unit': 'MWh',
        'entity_types': ['station'],
        'description': 'Physical transformer meter billing read — the primary billing source '
                       '(sum of billing_mwh on transformer-type grid meter readings).',
    },
    'ea_stream_b_mwh': {
        'module': 'energy_account',
        'label': 'Stream B — Technical Energy (MWh)',
        'unit': 'MWh',
        'entity_types': ['station', 'feeder'],
        'description': 'Feeder technical energy from the monitoring system (SCADA/hourly load). '
                       'Used as cross-check against Stream A physical reads.',
    },
    'ea_stream_deviation_pct': {
        'module': 'energy_account',
        'label': 'Stream A vs B Deviation (%)',
        'unit': '%',
        'entity_types': ['station'],
        'description': '|Stream A − Stream B| / Stream B × 100. '
                       'Deviation >2% triggers the max_applied billing rule.',
    },
    'ea_kv33_mwh': {
        'module': 'energy_account',
        'label': '33kV Feeder Energy — Stream B (MWh)',
        'unit': 'MWh',
        'entity_types': ['station'],
        'description': 'Sum of Stream B technical energy from all 33kV feeders at the station. '
                       'Enables Transmission → 33kV comparison.',
    },
    'ea_kv11_mwh': {
        'module': 'energy_account',
        'label': '11kV Feeder Energy — Stream B (MWh)',
        'unit': 'MWh',
        'entity_types': ['station'],
        'description': 'Sum of Stream B technical energy from all 11kV feeders at the station. '
                       'Enables 33kV → 11kV downstream comparison.',
    },
    'ea_billing_naira': {
        'module': 'energy_account',
        'label': 'EA Billing Naira',
        'unit': 'NGN',
        'entity_types': ['station', 'feeder'],
        'description': 'Total billed Naira — station_billing_naira from the monthly return.',
    },
    'ea_data_completeness': {
        'module': 'energy_account',
        'label': 'EA Data Completeness (%)',
        'unit': '%',
        'entity_types': ['station', 'feeder'],
        'description': 'Average Stream B data completeness % across feeder technical energy records '
                       '(days_with_data / total_days_in_period × 100).',
    },
    'ea_late_rate': {
        'module': 'energy_account',
        'label': 'Late Submission Rate (%)',
        'unit': '%',
        'entity_types': ['station'],
        'description': 'Percentage of billing returns submitted after the day-5 cutoff — '
                       'late_count / total_returns × 100 across all periods in range.',
    },
    'ea_return_status': {
        'module': 'energy_account',
        'label': 'Return Status',
        'unit': '',
        'entity_types': ['station'],
        'description': 'Status of the most recent monthly return for the period '
                       '(draft / submitted / tcn_agreed / reconciled / …).',
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
