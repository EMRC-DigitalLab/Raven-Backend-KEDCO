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
    },
    {
      "key": "customers",
      "label": "Customer Consumption Comparison",
      "description": "Compare customer-level consumption between two time periods — surfaces top movers"
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

## 3. Customer Consumption Comparison

### `POST /api/analytics/compare/customers/`

Compare how much energy individual customers consumed across two time periods.
Returns the top N customers ranked by consumption, with variance and a trend label.

Use this to surface:
- Customers whose consumption spiked or collapsed between periods
- Possible illegal bypass / low-billed accounts (sudden drop)
- Top revenue contributors who should be prioritised for reading

---

**Request**

```json
{
  "customer_type":   "MDI",
  "current_period":  { "from_date": "2026-01-01", "to_date": "2026-01-31" },
  "previous_period": { "from_date": "2025-12-01", "to_date": "2025-12-31" },
  "scope_type":      "district",
  "scope_id":        "KN-IDU",
  "top_n":           20,
  "sort_by":         "current"
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `customer_type` | string | no | *(all)* | `"MDI"` or `"MDNI"` — omit for both |
| `current_period` | object | yes | — | `{ from_date, to_date }` — the period you care about |
| `previous_period` | object | yes | — | `{ from_date, to_date }` — the baseline to compare against |
| `scope_type` | string | no | *(system-wide)* | `"feeder"` `"district"` `"state"` — narrows the customer pool |
| `scope_id` | string | no (required if scope_type set) | — | Slug of the entity (e.g. `"KN-IDU"`, `"KN"`, `"kn-tam-coc"`) |
| `top_n` | integer | no | `10` | How many customers to return (max `100`) |
| `sort_by` | string | no | `"current"` | `"current"` sort by current-period consumption, `"variance"` sort by absolute change |

---

**Response**

```json
{
  "customer_type":   "MDI",
  "current_period":  { "from_date": "2026-01-01", "to_date": "2026-01-31" },
  "previous_period": { "from_date": "2025-12-01", "to_date": "2025-12-31" },
  "scope_type":      "district",
  "scope_id":        "KN-IDU",
  "top_n":           20,
  "sort_by":         "current",
  "count":           20,

  "customers": [
    {
      "rank":             1,
      "id":               "1011e552-e6b0-4c46-b342-4ea0abaa9b3f",
      "account_no":       "32/25/90/0517-01",
      "meter_number":     "252424078",
      "customer_name":    "DANGOTE CEMENT PLC",
      "customer_type":    "MDI",
      "is_bypass":        false,
      "feeder":   { "slug": "kn-tam-coc", "name": "COCA COLA" },
      "district": { "slug": "KN-IDU",     "name": "Kano Industrial" },
      "state":    { "slug": "KN",          "name": "Kano" },

      "current_kwh":  45200.0,
      "previous_kwh": 38600.0,
      "variance_kwh": 6600.0,
      "variance_pct": 17.1,
      "trend":        "Positive"
    },
    {
      "rank":             2,
      "id":               "...",
      "account_no":       "32/25/90/0518-01",
      "meter_number":     "252424079",
      "customer_name":    "WEST AFRICAN TANNERY",
      "customer_type":    "MDI",
      "is_bypass":        false,
      "feeder":   { "slug": "kn-tam-coc", "name": "COCA COLA" },
      "district": { "slug": "KN-IDU",     "name": "Kano Industrial" },
      "state":    { "slug": "KN",          "name": "Kano" },

      "current_kwh":  38100.0,
      "previous_kwh": 74000.0,
      "variance_kwh": -35900.0,
      "variance_pct": -48.5,
      "trend":        "Declined"
    }
  ]
}
```

---

### Response field reference

#### Top-level
| Field | Description |
|---|---|
| `count` | Actual number of customers returned (may be less than `top_n` if fewer customers exist) |
| `current_period` | The period that defines "current" consumption |
| `previous_period` | The baseline period |

#### Per customer
| Field | Type | Description |
|---|---|---|
| `rank` | integer | 1-based rank within the result set |
| `current_kwh` | number | Total billed consumption in the current period (kWh) |
| `previous_kwh` | number | Total billed consumption in the previous period (kWh) |
| `variance_kwh` | number | `current − previous` — positive = increased, negative = decreased |
| `variance_pct` | number | `(variance / previous) × 100` — `null` when previous is 0 |
| `trend` | string | Trend classification label (see below) |
| `is_bypass` | boolean | Whether this customer is flagged for meter bypass |

#### Trend labels

| Label | Condition | Suggested UI colour |
|---|---|---|
| `"Major Positive"` | `variance_pct ≥ 50%` | Green (dark) |
| `"Positive"` | `10% ≤ variance_pct < 50%` | Green |
| `"Moderated"` | `−10% < variance_pct < 10%` | Grey / neutral |
| `"Declined"` | `−50% < variance_pct ≤ −10%` | Amber |
| `"Major Declined"` | `variance_pct ≤ −50%` | Red |
| `"New"` | `previous_kwh = 0` | Blue (first reading ever) |
| `"No Data"` | `current_kwh = 0 and previous_kwh = 0` | Grey (dim) |

> **`is_bypass` + `"Major Declined"` together** is a strong signal of tampering. Consider highlighting this combination with a red badge.

---

### Error responses

```json
{ "error": "current_period.from_date and current_period.to_date are required." }
{ "error": "previous_period.from_date and previous_period.to_date are required." }
{ "error": "Invalid customer_type 'XYZ'. Must be MDI or MDNI." }
{ "error": "scope_id is required when scope_type is set." }
{ "error": "Invalid scope_type 'xyz'. Must be one of: feeder, district, state." }
```

---

## 4. Metric Reference

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

## 5. Access Control — How It Works

- Users with `super_admin` or `admin` role see **all metrics** from all modules.
- All other users see only metrics from modules they have been granted access to
  (`UserSectionAccess` or non-expired `TemporaryAccess`).
- If a user requests a metric they cannot access, it appears in `metrics_denied`
  with a reason. **The rest of the response is still returned normally.**
- The frontend should call `/available/` once on load and only render metrics
  that appear in that response. Do not hardcode the metric list.

---

## 6. Complete Examples

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

### Top 20 MDI Customers — January vs December (district-scoped)

```json
POST /api/analytics/compare/customers/
{
  "customer_type":   "MDI",
  "current_period":  { "from_date": "2026-01-01", "to_date": "2026-01-31" },
  "previous_period": { "from_date": "2025-12-01", "to_date": "2025-12-31" },
  "scope_type":      "district",
  "scope_id":        "KN-IDU",
  "top_n":           20,
  "sort_by":         "current"
}
```

### Top 10 All Customers by Variance — System-wide

```json
POST /api/analytics/compare/customers/
{
  "current_period":  { "from_date": "2026-01-01", "to_date": "2026-01-31" },
  "previous_period": { "from_date": "2025-12-01", "to_date": "2025-12-31" },
  "top_n":           10,
  "sort_by":         "variance"
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

## 7. Error Responses

```json
{ "error": "entity_ids must be a non-empty list." }
{ "error": "from_date and to_date are required." }
{ "error": "Invalid entity_type 'xyz'. Must be one of: state, district, feeder, band" }
{ "error": "No accessible metrics for this request." }
```

HTTP status codes:
- `400` — bad request / validation failure
- `200` — success (even if some metrics were denied — check `metrics_denied`)
