"""
Tool registry for ARIA.
Defines Anthropic-format tool schemas and maps them to Python functions.
Tools are gated per module — only tools for the user's accessible modules are included.
"""

import json
import traceback

# ── Tool definitions (Anthropic format) ──────────────────────────────────────

_DATE_PROPS = {
    'start_date': {
        'type': 'string',
        'description': 'Start date in YYYY-MM-DD format (e.g. 2025-01-01)',
    },
    'end_date': {
        'type': 'string',
        'description': 'End date in YYYY-MM-DD format (e.g. 2025-12-31)',
    },
}

_SCOPE_PROPS = {
    'feeder': {
        'type': 'string',
        'description': 'Feeder name or slug to filter by (optional)',
    },
    'district': {
        'type': 'string',
        'description': 'Business district name or slug to filter by (optional)',
    },
    'state': {
        'type': 'string',
        'description': 'State name or slug to filter by (optional)',
    },
}

TOOL_SCHEMAS = {
    # ── COMMERCIAL ────────────────────────────────────────────────────────────
    'query_commercial': {
        'name': 'query_commercial',
        'description': (
            'Query commercial performance data for a date range. Returns billing efficiency, '
            'customer counts (MDI/MDNI), total kWh billed, total naira billed, bypass meters, '
            'faulty meters, late submissions, and OCR/audit stats. Use feeder/district/state to scope.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                **_DATE_PROPS,
                **_SCOPE_PROPS,
            },
            'required': ['start_date', 'end_date'],
        },
    },
    'query_top_commercial_feeders': {
        'name': 'query_top_commercial_feeders',
        'description': (
            'Rank feeders by billed kWh consumption for a period. '
            'Useful for identifying highest-revenue or highest-consumption feeders.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                **_DATE_PROPS,
                'limit': {'type': 'integer', 'description': 'Number of top feeders to return (default 10)'},
            },
            'required': ['start_date', 'end_date'],
        },
    },

    # ── TECHNICAL ─────────────────────────────────────────────────────────────
    'query_technical': {
        'name': 'query_technical',
        'description': (
            'Query technical metrics: average hours of supply, total energy delivered (MWh), '
            'interruption counts and durations broken down by type (load shedding, TCN, DisCo faults), '
            'and average turnaround time. Scope by feeder/district/state/voltage_level/band.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                **_DATE_PROPS,
                **_SCOPE_PROPS,
                'voltage_level': {'type': 'string', 'description': 'Filter by voltage: "11kv" or "33kv"'},
                'band': {'type': 'string', 'description': 'Filter by service band: A, B, C, D, or E'},
            },
            'required': ['start_date', 'end_date'],
        },
    },
    'query_feeder_ranking': {
        'name': 'query_feeder_ranking',
        'description': (
            'Rank feeders by hours_of_supply or energy_delivered for a period. '
            'Supports filtering by voltage level (11kV/33kV) and service band. '
            'Great for "which 11kV feeders had the best supply?", "rank Band A feeders by energy", etc.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                **_DATE_PROPS,
                'metric': {
                    'type': 'string',
                    'enum': ['hours_of_supply', 'energy_delivered'],
                    'description': 'What to rank feeders by',
                },
                'limit': {'type': 'integer', 'description': 'Number of feeders to return (default 10)'},
                'district': _SCOPE_PROPS['district'],
                'state': _SCOPE_PROPS['state'],
                'voltage_level': {'type': 'string', 'description': 'Filter by voltage: "11kv" or "33kv"'},
                'band': {'type': 'string', 'description': 'Filter by service band: A, B, C, D, or E'},
            },
            'required': ['start_date', 'end_date'],
        },
    },
    'query_system_load': {
        'name': 'query_system_load',
        'description': (
            'Return total, peak, and average load (MW) aggregated across all feeders for a date range. '
            'Automatically breaks down by voltage level (11kV vs 33kV). '
            'Also returns a daily trend so you can see how load changed day by day. '
            'Use this for "what is the total load for all 11kV feeders?", '
            '"show me the system load trend for last month", '
            '"how does 33kV load compare to 11kV?" etc.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                **_DATE_PROPS,
                'voltage_level': {'type': 'string', 'description': 'Filter by voltage: "11kv" or "33kv" (optional — omit for all feeders)'},
                'district': _SCOPE_PROPS['district'],
                'state': _SCOPE_PROPS['state'],
                'band': {'type': 'string', 'description': 'Filter by service band: A, B, C, D, or E'},
            },
            'required': ['start_date', 'end_date'],
        },
    },
    'query_period_comparison': {
        'name': 'query_period_comparison',
        'description': (
            'Compare technical metrics between two date periods side by side. '
            'Returns hours of supply, energy delivered, peak load, and interruption stats for both periods, '
            'plus the percentage change for each metric. '
            'Use this for "compare this month vs last month", "how did energy delivery change quarter over quarter?", '
            '"is Band A supply improving compared to last year?", etc. '
            'Supports all scope filters: feeder, district, state, voltage_level, band.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'period1_start': {'type': 'string', 'description': 'Start of first period (YYYY-MM-DD)'},
                'period1_end':   {'type': 'string', 'description': 'End of first period (YYYY-MM-DD)'},
                'period2_start': {'type': 'string', 'description': 'Start of second period (YYYY-MM-DD)'},
                'period2_end':   {'type': 'string', 'description': 'End of second period (YYYY-MM-DD)'},
                **_SCOPE_PROPS,
                'voltage_level': {'type': 'string', 'description': 'Filter by voltage: "11kv" or "33kv"'},
                'band': {'type': 'string', 'description': 'Filter by service band: A, B, C, D, or E'},
            },
            'required': ['period1_start', 'period1_end', 'period2_start', 'period2_end'],
        },
    },
    'query_band_compliance': {
        'name': 'query_band_compliance',
        'description': (
            'Check Band A/B/C/D/E feeder compliance for a specific date. '
            'Returns which feeders met their minimum hours-of-supply threshold '
            '(A≥20hrs, B≥16hrs, C≥12hrs, D≥8hrs) and which ones failed. '
            'When daily HOS data is missing, automatically falls back to interruption records '
            'to infer likely supply hours. '
            'Use this for "are Band A feeders compliant?", "which Band B feeders failed yesterday?", etc. '
            'date defaults to yesterday if omitted.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'band': {
                    'type': 'string',
                    'description': 'Service band to check: A, B, C, D, or E',
                },
                'date': {
                    'type': 'string',
                    'description': 'Date in YYYY-MM-DD format (defaults to yesterday)',
                },
            },
            'required': ['band'],
        },
    },
    'query_feeder_records': {
        'name': 'query_feeder_records',
        'description': (
            'Return all-time highest and lowest recorded values for a specific feeder: '
            'peak hours of supply, worst hours of supply, highest/lowest energy delivered (MWh), '
            'and peak/lowest load (MW) with the exact date each record was set. '
            'Use this for questions like "what is the highest/lowest ever recorded for feeder X?" '
            'or "what is the all-time peak load on feeder Y?" — no date range needed.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'feeder': {
                    'type': 'string',
                    'description': 'Feeder name or slug (required)',
                },
            },
            'required': ['feeder'],
        },
    },
    'query_hourly_load': {
        'name': 'query_hourly_load',
        'description': (
            'Return hour-by-hour load (MW) readings for a feeder. '
            'Use last_hours to get recent readings (e.g. last_hours=1 for the last hour, last_hours=6 for last 6 hours). '
            'Use date (YYYY-MM-DD) for a specific day. Omit both to get today\'s readings. '
            'Also returns peak, average, and lowest load for the period. '
            'Use this for "what was the load in the last hour?", "show me today\'s load profile", etc.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'feeder': {
                    'type': 'string',
                    'description': 'Feeder name or slug (required)',
                },
                'date': {
                    'type': 'string',
                    'description': 'Specific date in YYYY-MM-DD format (optional — defaults to today)',
                },
                'last_hours': {
                    'type': 'integer',
                    'description': 'Return readings from the last N hours (e.g. 1, 3, 6, 12, 24). Overrides date if provided.',
                },
            },
            'required': ['feeder'],
        },
    },

    # ── FINANCIAL ─────────────────────────────────────────────────────────────
    'query_financial': {
        'name': 'query_financial',
        'description': (
            'Query financial data: district OPEX (by category), HQ OPEX, salary payments, '
            'NBET invoices, and MO invoices for a period. Filter by district or OPEX category name.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                **_DATE_PROPS,
                'district': _SCOPE_PROPS['district'],
                'category': {
                    'type': 'string',
                    'description': 'Filter by OPEX category name (e.g. "salary", "admin", "fuel") — optional',
                },
            },
            'required': ['start_date', 'end_date'],
        },
    },

    # ── HR ────────────────────────────────────────────────────────────────────
    'query_hr': {
        'name': 'query_hr',
        'description': (
            'Query HR metrics: active headcount, year-to-date attrition rate, staff breakdown '
            'by department/grade/gender, and estimated monthly wage bill. '
            'Pass as_of_date to get a snapshot at a specific date.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'as_of_date': {
                    'type': 'string',
                    'description': 'Date snapshot in YYYY-MM-DD format (default: today)',
                },
                'district': _SCOPE_PROPS['district'],
                'state': _SCOPE_PROPS['state'],
            },
        },
    },
    'query_executive_kpis': {
        'name': 'query_executive_kpis',
        'description': (
            'Return executive KPI definitions and their latest performance values. '
            'Optionally filter by executive_role: CFO, CTO, CCO, or CHRO.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'executive_role': {
                    'type': 'string',
                    'description': 'Filter by role: CFO, CTO, CCO, CHRO (optional — omit for all)',
                },
            },
        },
    },

    # ── ENERGY ACCOUNT ────────────────────────────────────────────────────────
    'query_energy_account': {
        'name': 'query_energy_account',
        'description': (
            'Query energy account data: monthly returns, grid meter readings, '
            'NBET billing rates, and Stream A/B energy aggregates for a period.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                **_DATE_PROPS,
                'station': {
                    'type': 'string',
                    'description': 'Injection substation name or slug to filter by (optional)',
                },
            },
            'required': ['start_date', 'end_date'],
        },
    },

    # ── GRID LENS ─────────────────────────────────────────────────────────────
    'query_grid_lens': {
        'name': 'query_grid_lens',
        'description': (
            'Query GridLens overview metrics for loss decomposition analysis '
            '(transmission losses, distribution losses, metering gaps, feeder energy allocation) '
            'from the monthly overview summaries.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {**_DATE_PROPS},
            'required': ['start_date', 'end_date'],
        },
    },

    # ── TMO ───────────────────────────────────────────────────────────────────
    'query_tmo_daily_allocation': {
        'name': 'query_tmo_daily_allocation',
        'description': (
            'Query TMO Daily Energy Allocation: actual GWh delivered vs target per day for the '
            'whole network, plus achievement % and status. This is the core TMO number everything '
            'else is checked against.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {**_DATE_PROPS},
            'required': ['start_date', 'end_date'],
        },
    },
    'query_tmo_segment_breakdown': {
        'name': 'query_tmo_segment_breakdown',
        'description': (
            'Query TMO energy delivered split by customer segment (MDI / MDNI / Regions) for a '
            'period, with each segment\'s share of the total.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {**_DATE_PROPS},
            'required': ['start_date', 'end_date'],
        },
    },
    'query_tmo_pear': {
        'name': 'query_tmo_pear',
        'description': (
            'Query TMO PEAR (Premium Energy Allocation Ratio): MD (MDI+MDNI) vs Non-MD share of '
            'energy for yesterday and month-to-date, against the configured target mix (default 60/40).'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'as_of_date': {'type': 'string', 'description': 'Date in YYYY-MM-DD format (optional, defaults to yesterday)'},
            },
        },
    },
    'query_tmo_voltage_breakdown': {
        'name': 'query_tmo_voltage_breakdown',
        'description': (
            'Query TMO energy delivered split by voltage level (33kV vs 11kV) per segment, for a period.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {**_DATE_PROPS},
            'required': ['start_date', 'end_date'],
        },
    },
    'query_tmo_overview': {
        'name': 'query_tmo_overview',
        'description': (
            'Query the TMO Technical Dashboard overview: total feeder count, target vs actual GWh '
            'for the period, and % of feeders meeting their minimum supply-hours target.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {**_DATE_PROPS},
            'required': ['start_date', 'end_date'],
        },
    },
    'query_tmo_feeder_composition': {
        'name': 'query_tmo_feeder_composition',
        'description': (
            'Explain exactly how a specific 33kV feeder\'s daily energy figure is built: its own '
            'raw meter reading, every downstream child feeder subtracted from it with each '
            'child\'s own value, why a child is or isn\'t subtracted, and the resulting net per '
            'day. Use for "how was X made up", "which feeders make up X", "what\'s the '
            'difference between X\'s raw reading and its reported total".'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'feeder': {'type': 'string', 'description': 'Feeder name or slug (required)'},
                **_DATE_PROPS,
            },
            'required': ['feeder', 'start_date', 'end_date'],
        },
    },
    'query_tmo_bulk_composition': {
        'name': 'query_tmo_bulk_composition',
        'description': (
            'List which feeders make up the TMO Daily Energy Allocation total for a period, '
            'ranked by contribution with each one\'s share %. Use for "which feeders make up '
            'this total", "what\'s driving the allocation number", "top contributors".'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                **_DATE_PROPS,
                'limit': {'type': 'integer', 'description': 'Number of top feeders to return (default 20)'},
            },
            'required': ['start_date', 'end_date'],
        },
    },

    # ── COMMON (all users) ────────────────────────────────────────────────────
    'list_locations': {
        'name': 'list_locations',
        'description': (
            'List available entities in Raven: feeders, districts, states, or substations. '
            'Use this when asked "which feeders are in X district?" or "list all states in the system".'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'entity': {
                    'type': 'string',
                    'enum': ['feeder', 'district', 'state', 'substation'],
                    'description': 'Type of entity to list',
                },
                'district': _SCOPE_PROPS['district'],
                'state': _SCOPE_PROPS['state'],
                'onboarded_only': {
                    'type': 'boolean',
                    'description': 'For feeders: only return onboarded feeders (default true)',
                },
            },
        },
    },
    'web_search': {
        'name': 'web_search',
        'description': (
            'Search the web for recent information: NERC orders, tariff updates, Nigerian electricity news, '
            'KEDCO press releases, DisCo industry updates, or any external energy sector context. '
            'Use this when the question involves current events, policy changes, or anything outside Raven data.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'Search query — be specific for best results',
                },
                'max_results': {
                    'type': 'integer',
                    'description': 'Number of results to return (default 5, max 10)',
                },
            },
            'required': ['query'],
        },
    },
}

# ── Module → tool names mapping ──────────────────────────────────────────────

_MODULE_TOOLS = {
    'commercial':     ['query_commercial', 'query_top_commercial_feeders'],
    'technical':      ['query_technical', 'query_feeder_ranking', 'query_feeder_records', 'query_hourly_load', 'query_band_compliance', 'query_system_load', 'query_period_comparison'],
    'financial':      ['query_financial'],
    'hr':             ['query_hr', 'query_executive_kpis'],
    'energy_account': ['query_energy_account'],
    'grid_lens':      ['query_grid_lens'],
    'tmo':            ['query_tmo_daily_allocation', 'query_tmo_segment_breakdown', 'query_tmo_pear', 'query_tmo_voltage_breakdown', 'query_tmo_overview', 'query_tmo_feeder_composition', 'query_tmo_bulk_composition'],
    'overview':       [],
    'regulatory':     [],
}

_ALWAYS_ON = ['list_locations', 'web_search']

# ── Function dispatch ─────────────────────────────────────────────────────────

def _get_function(name: str):
    from aria.tools import commercial, technical, financial, hr, energy_account, grid_lens, tmo, common_tools
    dispatch = {
        'query_commercial':             commercial.query_commercial,
        'query_top_commercial_feeders': commercial.query_top_commercial_feeders,
        'query_technical':              technical.query_technical,
        'query_feeder_ranking':         technical.query_feeder_ranking,
        'query_feeder_records':         technical.query_feeder_records,
        'query_hourly_load':            technical.query_hourly_load,
        'query_band_compliance':        technical.query_band_compliance,
        'query_system_load':            technical.query_system_load,
        'query_period_comparison':      technical.query_period_comparison,
        'query_financial':              financial.query_financial,
        'query_hr':                     hr.query_hr,
        'query_executive_kpis':         hr.query_executive_kpis,
        'query_energy_account':         energy_account.query_energy_account,
        'query_grid_lens':              grid_lens.query_grid_lens,
        'query_tmo_daily_allocation':   tmo.query_tmo_daily_allocation,
        'query_tmo_segment_breakdown':  tmo.query_tmo_segment_breakdown,
        'query_tmo_pear':               tmo.query_tmo_pear,
        'query_tmo_voltage_breakdown':  tmo.query_tmo_voltage_breakdown,
        'query_tmo_overview':           tmo.query_tmo_overview,
        'query_tmo_feeder_composition': tmo.query_tmo_feeder_composition,
        'query_tmo_bulk_composition':   tmo.query_tmo_bulk_composition,
        'list_locations':               common_tools.list_locations,
        'web_search':                   common_tools.web_search,
    }
    return dispatch.get(name)


# ── Public API ────────────────────────────────────────────────────────────────

def get_tools_for_modules(accessible_modules: set) -> list:
    """Return Anthropic tool schema list for the given accessible modules."""
    tool_names = set(_ALWAYS_ON)
    for module in accessible_modules:
        tool_names.update(_MODULE_TOOLS.get(module, []))
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]


def execute_tool(name: str, arguments: dict, accessible_modules: set) -> str:
    """Execute a tool by name and return a JSON string result."""
    # Security: verify this tool is allowed for the user's modules
    allowed = set(_ALWAYS_ON)
    for module in accessible_modules:
        allowed.update(_MODULE_TOOLS.get(module, []))

    if name not in allowed:
        return json.dumps({'error': f'Tool "{name}" is not available for your current access.'})

    fn = _get_function(name)
    if fn is None:
        return json.dumps({'error': f'Unknown tool: {name}'})

    try:
        result = fn(**arguments)
        return json.dumps(result, default=str)
    except Exception:
        return json.dumps({'error': traceback.format_exc(limit=3)})
