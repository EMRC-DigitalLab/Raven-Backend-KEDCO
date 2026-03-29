# Raven Grid View — Frontend API Guide

**Base URL:** `https://{your-domain}/api/grid-view/`
**Auth:** All requests require `Authorization: Bearer <access_token>` header.

---

## Table of Contents
1. [What is Grid View](#1-what-is-grid-view)
2. [The Single Endpoint](#2-the-single-endpoint)
3. [Query Parameters](#3-query-parameters)
4. [Response Shape](#4-response-shape)
5. [Columns Reference](#5-columns-reference)
6. [Grouping Behaviour](#6-grouping-behaviour)
7. [Role-Based Column Visibility](#7-role-based-column-visibility)
8. [Full Response Examples](#8-full-response-examples)
9. [Suggested UI Behaviour](#9-suggested-ui-behaviour)
10. [Error Reference](#10-error-reference)

---

## 1. What is Grid View

Grid View returns **all infrastructure and performance data in a flat tabular format** — think of it as an Excel sheet of the entire network.

The hierarchy is:

```
State  →  Business District  →  Feeder  →  Band
```

Users can **group upward** at any level:

| Level | Each row represents |
|-------|---------------------|
| `feeder` (default) | One individual feeder — most granular |
| `district` | One business district — feeders collapsed and aggregated |
| `state` | One state — everything collapsed to the top level |

Columns shown depend on which **sections** the logged-in user has access to. A user with only Technical access will never see Commercial or Financial columns.

---

## 2. The Single Endpoint

```
GET /api/grid-view/
```

This is the only endpoint. All behaviour is controlled via query parameters.

---

## 3. Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `group_by` | string | `feeder` | Grouping level: `feeder`, `district`, or `state` |
| `month` | string | current month | Period filter in `YYYY-MM` format e.g. `2026-01` |
| `state_id` | UUID | — | Limit rows to a specific state |
| `district_id` | UUID | — | Limit rows to a specific business district |
| `page` | int | `1` | Page number |
| `page_size` | int | `50` | Rows per page (max `200`) |

**Example requests:**
```
GET /api/grid-view/
GET /api/grid-view/?group_by=district&month=2026-01
GET /api/grid-view/?group_by=state&month=2025-12
GET /api/grid-view/?group_by=feeder&state_id=<uuid>&month=2026-02&page=2&page_size=100
```

---

## 4. Response Shape

Every response has this top-level structure:

```json
{
  "group_by": "feeder",
  "period": "2026-01",
  "sections_included": ["technical", "commercial", "financial"],
  "columns": [ ...column definitions... ],
  "rows": [ ...data rows... ],
  "total_rows": 45,
  "page": 1,
  "page_size": 50,
  "total_pages": 1
}
```

| Field | Type | Description |
|-------|------|-------------|
| `group_by` | string | The active grouping level (`feeder`, `district`, `state`) |
| `period` | string | The month this data covers e.g. `"2026-01"` |
| `sections_included` | array | Which data sections are present in the columns (based on the user's access) |
| `columns` | array | Column definitions — use this to render table headers dynamically |
| `rows` | array | The actual data rows |
| `total_rows` | int | Total number of rows across all pages |
| `page` | int | Current page |
| `page_size` | int | Rows per page |
| `total_pages` | int | Total pages |

---

## 5. Columns Reference

The `columns` array tells you **exactly which columns to render** for this user. Always drive your table headers from this array — do not hardcode columns.

### Column object shape
```json
{
  "key": "energy_delivered_mwh",
  "label": "Energy Delivered (MWh)",
  "group": "technical"
}
```

| Field | Description |
|-------|-------------|
| `key` | The key to look up in each row object |
| `label` | Human-readable header text to display |
| `group` | Column group: `infrastructure`, `technical`, `commercial`, `financial` |

### Infrastructure columns (always present)

| key | label |
|-----|-------|
| `state` | State |
| `district` | Business District |
| `feeder` | Feeder |
| `band` | Band |
| `voltage_level` | Voltage Level |
| `feeder_status` | Status |
| `is_onboarded` | Onboarded |

### Technical columns (users with `technical` section access)

| key | label |
|-----|-------|
| `energy_delivered_mwh` | Energy Delivered (MWh) |
| `avg_hours_of_supply` | Avg Hours of Supply |
| `avg_peak_load_mw` | Avg Peak Load (MW) |
| `total_interruptions` | Total Interruptions |
| `avg_interruption_duration` | Avg Interruption Duration (h) |
| `load_shedding_hours` | Load Shedding (h) |
| `avg_turnaround_time` | Avg Turnaround Time (h) |
| `saifi` | SAIFI |
| `saidi` | SAIDI |

### Commercial columns (users with `commercial` section access)

| key | label |
|-----|-------|
| `total_customers` | Total Customers |
| `meter_readings_count` | Meter Readings |
| `total_consumption_kwh` | Total Consumption (kWh) |

### Financial columns (users with `financial` section access)

| key | label |
|-----|-------|
| `total_opex` | Total OPEX (₦) |
| `total_salaries` | Total Salaries (₦) |

---

## 6. Grouping Behaviour

### `group_by=feeder` — one row per feeder
All columns are present. Infrastructure columns have exact values.

```json
{
  "state": "Kano",
  "district": "Kano Metro",
  "feeder": "Dawanau",
  "band": "A",
  "voltage_level": "11kV",
  "feeder_status": "active",
  "is_onboarded": true,
  "energy_delivered_mwh": 245.5,
  "avg_hours_of_supply": 18.2,
  "total_interruptions": 3,
  ...
}
```

---

### `group_by=district` — one row per business district
Feeders are collapsed. Numeric metrics are **aggregated** (see rules below).
Infrastructure columns `feeder`, `band`, `voltage_level`, `feeder_status`, `is_onboarded` will be `null`.
An extra `feeder_count` field is added.

```json
{
  "state": "Kano",
  "district": "Kano Metro",
  "feeder": null,
  "band": null,
  "voltage_level": null,
  "feeder_status": null,
  "is_onboarded": null,
  "feeder_count": 12,
  "energy_delivered_mwh": 2340.0,
  "avg_hours_of_supply": 16.3,
  "total_interruptions": 42,
  ...
}
```

---

### `group_by=state` — one row per state
Most compressed. Infrastructure columns `district`, `feeder`, `band`, `voltage_level`, `feeder_status`, `is_onboarded` will be `null`.
Extra `feeder_count` and `district_count` fields are added.

```json
{
  "state": "Kano",
  "district": null,
  "feeder": null,
  "band": null,
  "voltage_level": null,
  "feeder_status": null,
  "is_onboarded": null,
  "feeder_count": 45,
  "district_count": 5,
  "energy_delivered_mwh": 12450.0,
  "avg_hours_of_supply": 16.8,
  "total_interruptions": 187,
  ...
}
```

### Aggregation rules when grouping up

| Metric type | Aggregation |
|-------------|-------------|
| Energy, Load Shedding, OPEX, Salaries, Customers, Consumption, Readings | **Sum** |
| Avg Hours of Supply, Avg Peak Load, Avg Interruption Duration, Avg Turnaround Time, SAIFI, SAIDI | **Average** across feeders |
| Total Interruptions | **Sum** |

### `null` values in rows
A cell value of `null` means **no data available** for that feeder/period — either the monthly summary hasn't been calculated yet, or there were no transactions. Render as `—` in the table.

---

## 7. Role-Based Column Visibility

The backend handles this automatically. The frontend should **only render columns that appear in the `columns` array** returned by the API — never show a column that isn't in that list.

| User section access | Columns visible |
|--------------------|-----------------|
| No section access | `403` error — cannot access Grid View at all |
| `technical` only | Infrastructure + Technical columns |
| `commercial` only | Infrastructure + Commercial columns |
| `financial` only | Infrastructure + Financial columns |
| `technical` + `commercial` | Infrastructure + Technical + Commercial |
| `super_admin` | All columns |

---

## 8. Full Response Examples

### Feeder-level, Technical + Commercial user
```json
{
  "group_by": "feeder",
  "period": "2026-01",
  "sections_included": ["technical", "commercial"],
  "columns": [
    { "key": "state",                    "label": "State",                       "group": "infrastructure" },
    { "key": "district",                 "label": "Business District",           "group": "infrastructure" },
    { "key": "feeder",                   "label": "Feeder",                      "group": "infrastructure" },
    { "key": "band",                     "label": "Band",                        "group": "infrastructure" },
    { "key": "voltage_level",            "label": "Voltage Level",               "group": "infrastructure" },
    { "key": "feeder_status",            "label": "Status",                      "group": "infrastructure" },
    { "key": "is_onboarded",             "label": "Onboarded",                   "group": "infrastructure" },
    { "key": "energy_delivered_mwh",     "label": "Energy Delivered (MWh)",      "group": "technical" },
    { "key": "avg_hours_of_supply",      "label": "Avg Hours of Supply",         "group": "technical" },
    { "key": "avg_peak_load_mw",         "label": "Avg Peak Load (MW)",          "group": "technical" },
    { "key": "total_interruptions",      "label": "Total Interruptions",         "group": "technical" },
    { "key": "avg_interruption_duration","label": "Avg Interruption Duration (h)","group": "technical" },
    { "key": "load_shedding_hours",      "label": "Load Shedding (h)",           "group": "technical" },
    { "key": "avg_turnaround_time",      "label": "Avg Turnaround Time (h)",     "group": "technical" },
    { "key": "saifi",                    "label": "SAIFI",                       "group": "technical" },
    { "key": "saidi",                    "label": "SAIDI",                       "group": "technical" },
    { "key": "total_customers",          "label": "Total Customers",             "group": "commercial" },
    { "key": "meter_readings_count",     "label": "Meter Readings",              "group": "commercial" },
    { "key": "total_consumption_kwh",    "label": "Total Consumption (kWh)",     "group": "commercial" }
  ],
  "rows": [
    {
      "state": "Kano",
      "district": "Kano Metro",
      "feeder": "Dawanau",
      "band": "A",
      "voltage_level": "11kV",
      "feeder_status": "active",
      "is_onboarded": true,
      "energy_delivered_mwh": 245.5,
      "avg_hours_of_supply": 18.2,
      "avg_peak_load_mw": 4.1,
      "total_interruptions": 3,
      "avg_interruption_duration": 1.5,
      "load_shedding_hours": 2.0,
      "avg_turnaround_time": 0.8,
      "saifi": 0.025,
      "saidi": 0.038,
      "total_customers": 120,
      "meter_readings_count": 85,
      "total_consumption_kwh": 198400.0
    },
    {
      "state": "Kano",
      "district": "Kano Metro",
      "feeder": "Bompai",
      "band": "B",
      "voltage_level": "11kV",
      "feeder_status": "active",
      "is_onboarded": true,
      "energy_delivered_mwh": 198.0,
      "avg_hours_of_supply": 14.5,
      "avg_peak_load_mw": 3.2,
      "total_interruptions": 7,
      "avg_interruption_duration": 2.3,
      "load_shedding_hours": 5.0,
      "avg_turnaround_time": 1.2,
      "saifi": 0.058,
      "saidi": 0.134,
      "total_customers": 98,
      "meter_readings_count": 61,
      "total_consumption_kwh": 142300.0
    }
  ],
  "total_rows": 45,
  "page": 1,
  "page_size": 50,
  "total_pages": 1
}
```

---

### State-level, all sections
```json
{
  "group_by": "state",
  "period": "2026-01",
  "sections_included": ["technical", "commercial", "financial"],
  "columns": [ ...all columns... ],
  "rows": [
    {
      "state": "Kano",
      "district": null,
      "feeder": null,
      "band": null,
      "voltage_level": null,
      "feeder_status": null,
      "is_onboarded": null,
      "feeder_count": 45,
      "district_count": 5,
      "energy_delivered_mwh": 12450.0,
      "avg_hours_of_supply": 16.8,
      "avg_peak_load_mw": 3.9,
      "total_interruptions": 187,
      "avg_interruption_duration": 1.9,
      "load_shedding_hours": 98.5,
      "avg_turnaround_time": 1.1,
      "saifi": 0.042,
      "saidi": 0.079,
      "total_customers": 6200,
      "meter_readings_count": 4310,
      "total_consumption_kwh": 9870000.0,
      "total_opex": 58000000.0,
      "total_salaries": 12400000.0
    }
  ],
  "total_rows": 1,
  "page": 1,
  "page_size": 50,
  "total_pages": 1
}
```

---

## 9. Suggested UI Behaviour

### Controls to show
```
[ Feeder | District | State ]   ← group_by toggle (default: Feeder)
[ Month picker: Jan 2026 ]      ← month filter
[ State dropdown ]              ← optional filter
[ District dropdown ]           ← optional filter (populate after state is selected)
[ Export to CSV ]               ← frontend-side using the rows array
```

### Table rendering
1. On load, call `GET /api/grid-view/` with defaults.
2. Read `columns` from the response — render table headers from this list.
3. Render each object in `rows` as a table row, using `column.key` to look up values.
4. Display `null` values as `—`.
5. Use `column.group` to colour-code or group headers visually (e.g. a coloured header band for Technical, Commercial, Financial).

### Pagination
```
On mount         → fetch page 1, page_size 50
On next page     → increment page param, re-fetch
On page_size change → reset to page 1, re-fetch
```

### When group_by changes
Reset to page 1 and re-fetch. Do not cache rows from one group level and use them for another.

### Column group colour suggestion
| Group | Suggested colour |
|-------|-----------------|
| `infrastructure` | Neutral / grey |
| `technical` | Blue |
| `commercial` | Green |
| `financial` | Orange |

---

## 10. Error Reference

| Status | Meaning | What to show |
|--------|---------|--------------|
| `401` | Missing or expired token | Redirect to login |
| `403` | User has no section access at all | "You don't have access to Grid View. Contact your administrator." |
| `400` | Bad query param (e.g. invalid UUID) | Check the `detail` field in the response body |
| `200` with `total_rows: 0` | No feeders match the filters | "No data found for the selected filters." |
