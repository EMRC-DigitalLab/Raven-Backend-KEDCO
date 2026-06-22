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
            'and average turnaround time. Scope by feeder/district/state.'
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
    'query_feeder_ranking': {
        'name': 'query_feeder_ranking',
        'description': (
            'Rank feeders by hours_of_supply or energy_delivered for a period. '
            'Great for "which feeders had the best/worst supply?" questions.'
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
            },
            'required': ['start_date', 'end_date'],
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
    'technical':      ['query_technical', 'query_feeder_ranking', 'query_feeder_records', 'query_hourly_load'],
    'financial':      ['query_financial'],
    'hr':             ['query_hr', 'query_executive_kpis'],
    'energy_account': ['query_energy_account'],
    'grid_lens':      ['query_grid_lens'],
    'overview':       [],
    'regulatory':     [],
}

_ALWAYS_ON = ['list_locations', 'web_search']

# ── Function dispatch ─────────────────────────────────────────────────────────

def _get_function(name: str):
    from aria.tools import commercial, technical, financial, hr, energy_account, grid_lens, common_tools
    dispatch = {
        'query_commercial':             commercial.query_commercial,
        'query_top_commercial_feeders': commercial.query_top_commercial_feeders,
        'query_technical':              technical.query_technical,
        'query_feeder_ranking':         technical.query_feeder_ranking,
        'query_feeder_records':         technical.query_feeder_records,
        'query_hourly_load':            technical.query_hourly_load,
        'query_financial':              financial.query_financial,
        'query_hr':                     hr.query_hr,
        'query_executive_kpis':         hr.query_executive_kpis,
        'query_energy_account':         energy_account.query_energy_account,
        'query_grid_lens':              grid_lens.query_grid_lens,
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
