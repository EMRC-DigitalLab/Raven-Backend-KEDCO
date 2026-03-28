# Compare Engine — Frontend API Guide

---

## Overview

The Compare Engine lets you compare **any entities** (feeders, districts, states, bands)
against each other, or compare **the same entity across different time periods**.

It is fully role-based — users only see metrics for modules they have access to.
You never need to implement access logic on the frontend. Just read `metrics_denied`
and grey out / hide those fields.

---

## Base URL

All compare endpoints are under `/api/analytics/`.

---

## 1. What Can This User Compare?

### `GET /api/analytics/compare/available/`

Call this first to build your compare UI. Returns the exact metrics, entity types,
and granularities this user is allowed to use.

**Response**

```json
{
  "entity_types": [
    { "key": "state",    "label": "States",              "icon": "map" },
    { "key": "district", "label": "Business Districts",  "icon": "building" },
    { "key": "feeder",   "label": "Feeders",             "icon": "zap" },
    { "key": "band",     "label": "Service Bands (A–E)", "icon": "layers" }
  ],
  "granularities": [
    { "key": "daily",   "label": "Daily",   "description": "One data point per day" },
    { "key": "weekly",  "label": "Weekly",  "description": "One data point per calendar week" },
    { "key": "monthly", "label": "Monthly", "description": "One data point per calendar month" },
    { "key": "yearly",  "label": "Yearly",  "description": "One data point per year" }
  ],
  "feeder_types": [
    { "key": "11kv", "label": "11kV Feeders" },
    { "key": "33kv", "label": "33kV Feeders" }
  ],
  "compare_modes": [
    {
      "key": "entities",
      "label": "Compare Entities",
      "description": "Compare multiple feeders / districts / states against each other"
    },
    {
      "key": "periods",
      "label": "Compare Periods",
      "description": "Compare one entity across different time periods (today vs yesterday, etc.)"
    }
  ],
  "metrics": {
    "technical": [
      { "key": "hours_of_supply",           "label": "Hours of Supply",           "unit": "hrs/day", ... },
      { "key": "energy_delivered",          "label": "Energy Delivered",          "unit": "MWh",     ... },
      { "key": "peak_load",                 "label": "Peak Load",                 "unit": "MW",      ... },
      { "key": "total_interruptions",       "label": "Total Interruptions",       "unit": "count",   ... },
      { "key": "avg_interruption_duration", "label": "Avg Interruption Duration", "unit": "hrs",     ... },
      { "key": "interruption_hours",        "label": "Interruption Hours",        "unit": "hrs/day", ... },
      { "key": "turnaround_time",           "label": "Turnaround Time",           "unit": "hrs/day", ... },
      { "key": "feeder_count",              "label": "Feeder Count",              "unit": "count",   ... },
      { "key": "availability_pct",          "label": "Availability",              "unit": "%",       ... }
    ],
    "commercial": [
      { "key": "total_customers",   "label": "Total Customers",   "unit": "count" },
      { "key": "customers_read",    "label": "Customers Read",    "unit": "count" },
      { "key": "coverage_rate",     "label": "Coverage Rate",     "unit": "%" },
      { "key": "revenue_billed",    "label": "Revenue Billed",    "unit": "NGN" },
      { "key": "energy_billed_kwh", "label": "Energy Billed",     "unit": "kWh" },
      { "key": "billing_efficiency","label": "Billing Efficiency","unit": "%" },
      { "key": "atc_loss",          "label": "AT&C Loss",         "unit": "%" },
      { "key": "arpu",              "label": "ARPU",              "unit": "NGN" }
    ],
    "financial": [
      { "key": "total_opex",   "label": "Total OPEX",   "unit": "NGN" },
      { "key": "salary_cost",  "label": "Salary Cost",  "unit": "NGN" },
      { "key": "total_cost",   "label": "Total Cost",   "unit": "NGN" }
    ],
    "hr": [
      { "key": "total_staff",      "label": "Total Staff",    "unit": "count" },
      { "key": "attrition_rate",   "label": "Attrition Rate", "unit": "%" },
      { "key": "new_hires",        "label": "New Hires",      "unit": "count" },
      { "key": "avg_tenure_years", "label": "Avg Tenure",     "unit": "yrs" },
      { "key": "total_wage_bill",  "label": "Total Wage Bill","unit": "NGN" }
    ]
  },
  "accessible_modules": ["commercial", "technical"]
}
```

> **Note:** Only modules the user has access to appear in `metrics`.
> A user with only `technical` access will not see `commercial`, `financial`, or `hr` blocks at all.

---

## 2. Run a Comparison

### `POST /api/analytics/compare/`

Two modes. Pick one.

---

### Mode A — Entity Comparison

Compare multiple entities of the same type over the same time period.
Works for: feeder vs feeder, district vs district, state vs state, band vs band.

**Request**

```json
{
  "compare_mode": "entities",
  "entity_type":  "district",
  "entity_ids":   ["<uuid>", "<uuid>", "<uuid>"],
  "metrics":      ["hours_of_supply", "energy_delivered", "revenue_billed", "atc_loss"],
  "from_date":    "2025-01-01",
  "to_date":      "2025-03-31",
  "granularity":  "monthly",
  "feeder_type":  "11kv",
  "include_trend": true
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `compare_mode` | string | yes | `"entities"` |
| `entity_type` | string | yes | `state` \| `district` \| `feeder` \| `band` |
| `entity_ids` | array of UUIDs | yes | 2 or more entities to compare |
| `metrics` | array of strings | yes | Metric keys from `/available/` |
| `from_date` | `YYYY-MM-DD` | yes | Start of the period |
| `to_date` | `YYYY-MM-DD` | yes | End of the period |
| `granularity` | string | no | `daily` \| `weekly` \| `monthly` \| `yearly` — default `monthly` |
| `feeder_type` | string | no | `11kv` \| `33kv` — omit for both |
| `include_trend` | boolean | no | Whether to return bucketed trend data — default `true` |

**Response**

```json
{
  "compare_mode":    "entities",
  "entity_type":     "district",
  "granularity":     "monthly",
  "feeder_type":     "11kv",
  "period":          { "from_date": "2025-01-01", "to_date": "2025-03-31" },

  "metrics_returned": ["hours_of_supply", "energy_delivered", "revenue_billed"],
  "metrics_denied": [
    {
      "metric": "atc_loss",
      "module": "commercial",
      "reason": "No access to the commercial module"
    }
  ],

  "results": [
    {
      "entity": {
        "id":   "<uuid>",
        "name": "Kano Metro",
        "type": "district"
      },
      "data": {
        "hours_of_supply":  18.4,
        "energy_delivered": 4020.1,
        "revenue_billed":   96406000.0
      },
      "trend": [
        {
          "period":    "2025-01",
          "from_date": "2025-01-01",
          "to_date":   "2025-01-31",
          "hours_of_supply":  17.8,
          "energy_delivered": 3800.2,
          "revenue_billed":   31200000.0
        },
        {
          "period":    "2025-02",
          "from_date": "2025-02-01",
          "to_date":   "2025-02-28",
          "hours_of_supply":  18.6,
          "energy_delivered": 3950.4,
          "revenue_billed":   32800000.0
        },
        {
          "period":    "2025-03",
          "from_date": "2025-03-01",
          "to_date":   "2025-03-31",
          "hours_of_supply":  18.9,
          "energy_delivered": 4020.1,
          "revenue_billed":   32406000.0
        }
      ]
    },
    {
      "entity": {
        "id":   "<uuid>",
        "name": "Kano North",
        "type": "district"
      },
      "data": {
        "hours_of_supply":  14.2,
        "energy_delivered": 2810.5,
        "revenue_billed":   62000000.0
      },
      "trend": [ ... ]
    }
  ]
}
```

---

### Mode B — Period Comparison

Compare one entity across different time windows. Classic use case: today vs yesterday.

**Request**

```json
{
  "compare_mode": "periods",
  "entity_type":  "feeder",
  "entity_id":    "<uuid>",
  "metrics":      ["hours_of_supply", "peak_load", "total_interruptions"],
  "feeder_type":  "11kv",
  "periods": [
    { "label": "Today",      "from_date": "2025-03-28", "to_date": "2025-03-28" },
    { "label": "Yesterday",  "from_date": "2025-03-27", "to_date": "2025-03-27" },
    { "label": "Last Week",  "from_date": "2025-03-21", "to_date": "2025-03-27" },
    { "label": "Last Month", "from_date": "2025-02-01", "to_date": "2025-02-28" }
  ]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `compare_mode` | string | yes | `"periods"` |
| `entity_type` | string | yes | `state` \| `district` \| `feeder` \| `band` |
| `entity_id` | UUID | yes | The single entity to compare across periods |
| `metrics` | array of strings | yes | Metric keys from `/available/` |
| `periods` | array of objects | yes | Each: `{label, from_date, to_date}` |
| `feeder_type` | string | no | `11kv` \| `33kv` |

**Response**

```json
{
  "compare_mode": "periods",
  "entity_type":  "feeder",
  "entity": {
    "id":   "<uuid>",
    "name": "Dakata 11kV",
    "type": "feeder"
  },
  "feeder_type":      "11kv",
  "metrics_returned": ["hours_of_supply", "peak_load", "total_interruptions"],
  "metrics_denied":   [],

  "results": [
    {
      "period": {
        "label":     "Today",
        "from_date": "2025-03-28",
        "to_date":   "2025-03-28",
        "days":      1
      },
      "data": {
        "hours_of_supply":    18.2,
        "peak_load":          14.5,
        "total_interruptions": 1
      }
    },
    {
      "period": {
        "label":     "Yesterday",
        "from_date": "2025-03-27",
        "to_date":   "2025-03-27",
        "days":      1
      },
      "data": {
        "hours_of_supply":    16.8,
        "peak_load":          13.2,
        "total_interruptions": 2
      }
    },
    {
      "period": {
        "label":     "Last Week",
        "from_date": "2025-03-21",
        "to_date":   "2025-03-27",
        "days":      7
      },
      "data": {
        "hours_of_supply":    17.4,
        "peak_load":          15.1,
        "total_interruptions": 9
      }
    },
    {
      "period": {
        "label":     "Last Month",
        "from_date": "2025-02-01",
        "to_date":   "2025-02-28",
        "days":      28
      },
      "data": {
        "hours_of_supply":    16.1,
        "peak_load":          14.8,
        "total_interruptions": 34
      }
    }
  ]
}
```

---

## 3. Metric Reference

### Which metrics work at which entity level?

| Metric | state | district | feeder | band |
|---|:---:|:---:|:---:|:---:|
| `hours_of_supply` | ✓ | ✓ | ✓ | ✓ |
| `energy_delivered` | ✓ | ✓ | ✓ | ✓ |
| `peak_load` | ✓ | ✓ | ✓ | ✓ |
| `total_interruptions` | ✓ | ✓ | ✓ | ✓ |
| `avg_interruption_duration` | ✓ | ✓ | ✓ | ✓ |
| `interruption_hours` | ✓ | ✓ | ✓ | ✓ |
| `turnaround_time` | ✓ | ✓ | ✓ | ✓ |
| `feeder_count` | ✓ | ✓ | — | ✓ |
| `availability_pct` | ✓ | ✓ | ✓ | ✓ |
| `total_customers` | ✓ | ✓ | ✓ | ✓ |
| `customers_read` | ✓ | ✓ | ✓ | ✓ |
| `coverage_rate` | ✓ | ✓ | ✓ | ✓ |
| `revenue_billed` | ✓ | ✓ | ✓ | ✓ |
| `energy_billed_kwh` | ✓ | ✓ | ✓ | ✓ |
| `billing_efficiency` | ✓ | ✓ | ✓ | ✓ |
| `atc_loss` | ✓ | ✓ | ✓ | ✓ |
| `arpu` | ✓ | ✓ | ✓ | ✓ |
| `total_opex` | ✓ | ✓ | — | — |
| `salary_cost` | ✓ | ✓ | — | — |
| `total_cost` | ✓ | ✓ | — | — |
| `total_staff` | ✓ | ✓ | — | — |
| `attrition_rate` | ✓ | ✓ | — | — |
| `new_hires` | ✓ | ✓ | — | — |
| `avg_tenure_years` | ✓ | ✓ | — | — |
| `total_wage_bill` | ✓ | ✓ | — | — |

---

## 4. Access Control — How It Works

- Users with `super_admin` or `admin` role see **all metrics** from all modules.
- All other users see only metrics from modules they have been granted access to
  (`UserSectionAccess` or non-expired `TemporaryAccess`).
- If a user requests a metric they cannot access, it appears in `metrics_denied`
  with a reason. **The rest of the response is still returned normally.**
- The frontend should call `/available/` once on load and only render metrics
  that appear in that response. Do not hardcode the metric list.

---

## 5. Complete Examples

### Today vs Yesterday for a Feeder

```json
POST /api/analytics/compare/
{
  "compare_mode": "periods",
  "entity_type":  "feeder",
  "entity_id":    "<feeder-uuid>",
  "metrics":      ["hours_of_supply", "peak_load", "energy_delivered", "total_interruptions"],
  "periods": [
    { "label": "Today",     "from_date": "2025-03-28", "to_date": "2025-03-28" },
    { "label": "Yesterday", "from_date": "2025-03-27", "to_date": "2025-03-27" }
  ]
}
```

### All Bands Compared for March

```json
POST /api/analytics/compare/
{
  "compare_mode": "entities",
  "entity_type":  "band",
  "entity_ids":   ["<band-a-uuid>", "<band-b-uuid>", "<band-c-uuid>", "<band-d-uuid>", "<band-e-uuid>"],
  "metrics":      ["hours_of_supply", "availability_pct", "total_interruptions", "energy_delivered"],
  "from_date":    "2025-03-01",
  "to_date":      "2025-03-31",
  "granularity":  "daily",
  "include_trend": true
}
```

### Cross-Module District Comparison (admin only)

```json
POST /api/analytics/compare/
{
  "compare_mode": "entities",
  "entity_type":  "district",
  "entity_ids":   ["<kano-metro-uuid>", "<kano-north-uuid>", "<kaduna-south-uuid>"],
  "metrics":      [
    "hours_of_supply", "energy_delivered",
    "revenue_billed", "atc_loss", "coverage_rate",
    "total_cost", "salary_cost",
    "total_staff", "attrition_rate"
  ],
  "from_date":    "2025-01-01",
  "to_date":      "2025-12-31",
  "granularity":  "monthly"
}
```

### State-Level Q1 vs Q2 Comparison

```json
POST /api/analytics/compare/
{
  "compare_mode": "periods",
  "entity_type":  "state",
  "entity_id":    "<kano-state-uuid>",
  "metrics":      ["hours_of_supply", "energy_delivered", "revenue_billed", "total_staff"],
  "periods": [
    { "label": "Q1 2025", "from_date": "2025-01-01", "to_date": "2025-03-31" },
    { "label": "Q2 2025", "from_date": "2025-04-01", "to_date": "2025-06-30" }
  ]
}
```

---

## 6. Error Responses

```json
{ "error": "entity_ids must be a non-empty list." }
{ "error": "from_date and to_date are required." }
{ "error": "Invalid entity_type 'xyz'. Must be one of: state, district, feeder, band" }
{ "error": "No accessible metrics for this request." }
```

HTTP status codes:
- `400` — bad request / validation failure
- `200` — success (even if some metrics were denied — check `metrics_denied`)
