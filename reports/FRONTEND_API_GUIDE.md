# Raven Reporting Engine — Frontend API Guide

---

## Overview

The reporting engine lets users build, preview, and export multi-module PDF reports.
The flow is always:

```
1. Fetch available sections  (what this user can see)
2. Fetch filter options       (states, districts, feeders, etc.)
3. Build a template           (pick sections, set order, supply filters)
4. Preview section data       (live data per section)
5. Generate PDF               (download the final report)
```

---

## Base URL

All endpoints are under `/api/reports/`.

---

## 1. Available Sections

### `GET /api/reports/sections/available/`

Returns the sections the logged-in user is allowed to add to a report.
Sections are filtered by the user's module access permissions.

**Response**

```json
{
  "sections": [
    {
      "section_type": "technical_metrics",
      "display_name": "Technical Metrics Cards",
      "description": "Key technical metrics displayed as cards",
      "category": "technical",
      "supports_chart": false,
      "config_options": {
        "metrics": {
          "type": "multi_select",
          "options": ["hours_of_supply", "average_load", "peak_load", "energy_delivered", "total_interruptions"],
          "default": ["hours_of_supply", "average_load", "energy_delivered", "total_interruptions"]
        }
      }
    },
    ...
  ],
  "categories": [
    { "id": "general",    "name": "General" },
    { "id": "technical",  "name": "Technical" },
    { "id": "hr",         "name": "Human Resources" },
    { "id": "commercial", "name": "Commercial" },
    { "id": "financial",  "name": "Financial" }
  ]
}
```

**Notes**
- `cover_page` and `table_of_contents` are always included (category `general`).
- If the user does not have access to a module (e.g. `hr`), none of that module's sections appear.
- `supports_chart: true` means the section can optionally render a chart in the PDF.

---

## 2. Filter Options

### `GET /api/reports/filters/`

Returns all available filter options for scoping a report.

**Response**

```json
{
  "states":       [{ "id": "uuid", "name": "Kano" }, ...],
  "districts":    [{ "id": "uuid", "name": "Kano Metro", "state_id": "uuid", "state__name": "Kano" }, ...],
  "substations":  [{ "id": "uuid", "name": "Dakata 33/11kV" }, ...],
  "bands":        [{ "id": "uuid", "name": "A", "description": "..." }, ...],
  "feeders":      [{ "id": "uuid", "name": "Dakata 11kV", "voltage_level": "11kv", "band__name": "A", "business_district__name": "Kano Metro" }, ...],
  "voltage_levels": [
    { "id": "11kv", "name": "11kV Feeders" },
    { "id": "33kv", "name": "33kV Feeders" }
  ]
}
```

---

## 3. Filters Object

Every data request (preview, PDF generation) requires a `filters` object.
All fields except `from_date` and `to_date` are optional.

```json
{
  "from_date":      "2025-01-01",
  "to_date":        "2025-01-31",

  "states":         ["uuid", "uuid"],
  "districts":      ["uuid", "uuid"],
  "substations":    ["uuid", "uuid"],
  "bands":          ["uuid", "uuid"],
  "feeders":        ["uuid", "uuid"],
  "voltage_level":  "11kv",

  "departments":    ["uuid", "uuid"],
  "grade_levels":   ["associate", "senior_manager"],

  "customer_type":  "MDI"
}
```

**Filter scoping rules**

| Filter key      | Affects modules          | Notes                                      |
|-----------------|--------------------------|--------------------------------------------|
| `states`        | Technical, HR, Financial | Narrows feeders by state                   |
| `districts`     | Technical, HR, Financial, Commercial | Narrows feeders and cost data by district |
| `substations`   | Technical                | Narrows feeders by substation              |
| `bands`         | Technical                | Narrows feeders by service band            |
| `feeders`       | Technical, Commercial    | Explicit feeder list                       |
| `voltage_level` | Technical, Commercial    | `"11kv"` or `"33kv"`                       |
| `departments`   | HR only                  | Filter staff by department UUID            |
| `grade_levels`  | HR only                  | e.g. `["associate", "manager"]`            |
| `customer_type` | Commercial only          | `"MDI"` or `"MDNI"`                        |

---

## 4. Section Types Reference

### General

| section_type        | Description                                      |
|---------------------|--------------------------------------------------|
| `cover_page`        | Branded cover — always first page                |
| `table_of_contents` | Auto-generated TOC — always second page          |
| `custom_text`       | Free text / notes block                          |
| `gaps_improvements` | Structured gaps and improvement areas block      |

### Technical

| section_type                | Description                                             |
|-----------------------------|---------------------------------------------------------|
| `infrastructure_overview`   | Feeder count, substations, transformer count            |
| `technical_metrics`         | KPI cards — hours of supply, load, energy, interruptions|
| `system_reliability`        | Interruption hours, avg duration, turnaround time       |
| `interruption_breakdown`    | Table of interruptions grouped by type                  |
| `feeder_performance_table`  | Per-feeder performance table                            |
| `state_performance_table`   | Performance grouped by state                            |
| `district_performance_table`| Performance grouped by business district                |
| `service_band_summary`      | Performance grouped by service band (A–E)               |
| `hours_of_supply_chart`     | Hours of supply trend chart data                        |
| `load_trend_chart`          | Load trend chart data                                   |
| `energy_delivered_chart`    | Energy delivered trend chart data                       |

### HR

| section_type          | Description                                              |
|-----------------------|----------------------------------------------------------|
| `hr_overview`         | Total staff, gender split, attrition rate, new hires     |
| `staff_metrics`       | Average tenure and grade breakdown                       |
| `wage_bill_analysis`  | Total wage bill, average salary, department breakdown    |
| `department_headcount`| Headcount per department with % share                    |
| `attrition_analysis`  | Exits and attrition rate for the period                  |
| `recruitment_summary` | New hires for the period by department and grade         |

### Commercial

| section_type           | Description                                             |
|------------------------|---------------------------------------------------------|
| `commercial_overview`  | Billing, coverage, AT&C loss, ARPU, MDI/MDNI split      |
| `revenue_by_district`  | Revenue and kWh breakdown per business district         |
| `customer_type_summary`| MDI vs MDNI headcount and revenue comparison            |

### Financial

| section_type         | Description                                               |
|----------------------|-----------------------------------------------------------|
| `financial_overview` | Total cost: OPEX + HQ OPEX + Salaries + NBET + MO        |
| `opex_by_category`   | District OPEX by category with % share                    |
| `opex_by_district`   | District OPEX per business district with % share          |

---

## 5. Preview a Single Section

### `POST /api/reports/preview/section/`

Use this for live preview while the user is building the report.

**Request**

```json
{
  "section_type": "technical_metrics",
  "config": {
    "metrics": ["hours_of_supply", "average_load", "energy_delivered"]
  },
  "filters": {
    "from_date": "2025-01-01",
    "to_date":   "2025-01-31",
    "states":    ["uuid"]
  }
}
```

**Response**

```json
{
  "section_type": "technical_metrics",
  "data": {
    "hours_of_supply": 18.4,
    "average_load": 12.3,
    "peak_load": 28.1,
    "energy_delivered": 9043.2,
    "daily_average_consumption": 291.7,
    "total_interruptions": 14,
    "load_shedding_count": 6
  }
}
```

---

## 6. Preview All Sections at Once

### `POST /api/reports/preview/`

Use this to hydrate a full report preview.

**Request**

```json
{
  "sections": [
    { "section_type": "cover_page",       "config": {} },
    { "section_type": "technical_metrics","config": {} },
    { "section_type": "hr_overview",      "config": {} }
  ],
  "filters": {
    "from_date": "2025-01-01",
    "to_date":   "2025-01-31"
  }
}
```

**Response**

```json
{
  "period": {
    "from_date": "2025-01-01",
    "to_date":   "2025-01-31",
    "days":      31
  },
  "sections": [
    { "section_type": "cover_page",        "config": {}, "data": { "from_date": "2025-01-01", "to_date": "2025-01-31", "period_days": 31 } },
    { "section_type": "technical_metrics", "config": {}, "data": { "hours_of_supply": 18.4, ... } },
    { "section_type": "hr_overview",       "config": {}, "data": { "total_staff": 312, "male_count": 274, ... } }
  ]
}
```

---

## 7. Section Data Shapes

### `hr_overview`
```json
{
  "total_staff":       312,
  "male_count":        274,
  "female_count":       38,
  "departments_count":  12,
  "attrition_count":     4,
  "attrition_rate":    1.28,
  "new_hires":           7
}
```

### `staff_metrics`
```json
{
  "total_staff":       312,
  "avg_tenure_years":  4.3,
  "grade_breakdown":   [{ "grade": "associate", "count": 120 }, ...]
}
```

### `wage_bill_analysis`
```json
{
  "total_wage_bill":     45200000.0,
  "avg_salary":           144871.8,
  "department_breakdown": [
    { "department": "Operations", "total_wages": 12400000.0, "headcount": 85, "percentage": 27.4 },
    ...
  ]
}
```

### `commercial_overview`
```json
{
  "total_customers":       1840,
  "customers_read":        1612,
  "customers_unread":       228,
  "coverage_rate":         87.6,
  "mdi_count":              142,
  "mdni_count":            1698,
  "total_billed_kwh":   4820300.0,
  "energy_charge":     96406000.0,
  "vat":                7230450.0,
  "total_billed_amount": 103636450.0,
  "estimated_kwh":      410200.0,
  "estimated_revenue":  9104800.0,
  "energy_delivered_mwh": 6140.2,
  "energy_delivered_mode": "mixed",
  "energy_consumed_kwh": 4900000.0,
  "billing_efficiency":  79.8,
  "atc_loss":            20.2,
  "arpu":              64264.55,
  "mdi_revenue":      28400000.0,
  "mdni_revenue":     75236450.0
}
```

### `financial_overview`
```json
{
  "opex":          12400000.0,
  "hq_opex":        3200000.0,
  "salaries":      45200000.0,
  "nbet_invoice":  18000000.0,
  "mo_invoice":     1200000.0,
  "total_cost":    80000000.0
}
```

### `revenue_by_district`
```json
[
  { "district": "Kano Metro",   "total_billed_kwh": 1820400.0, "total_billed_amount": 39204000.0 },
  { "district": "Kano North",   "total_billed_kwh":  940200.0, "total_billed_amount": 20234000.0 },
  ...
]
```

### `opex_by_category`
```json
[
  { "category": "Maintenance",   "total": 4800000.0, "count": 34, "percentage": 38.7 },
  { "category": "Uncategorised", "total": 2100000.0, "count": 12, "percentage": 16.9 },
  ...
]
```

---

## 8. Template Management

### `GET /api/reports/templates/`
List the user's own templates plus all public templates.

### `POST /api/reports/templates/`
Create a new template.

**Request**
```json
{
  "name": "Monthly Technical Report",
  "description": "Standard monthly ops report",
  "report_title": "October 2025 Operations Report",
  "report_subtitle": "Kano Distribution Network",
  "orientation": "portrait",
  "is_public": false,
  "default_filters": {
    "from_date": "2025-10-01",
    "to_date": "2025-10-31"
  },
  "sections": [
    { "section_type": "cover_page",        "order": 0, "is_enabled": true, "config": {} },
    { "section_type": "table_of_contents", "order": 1, "is_enabled": true, "config": {} },
    { "section_type": "technical_metrics", "order": 2, "is_enabled": true, "config": { "metrics": ["hours_of_supply", "energy_delivered"] } }
  ]
}
```

### `GET /api/reports/templates/<id>/`
Retrieve a template with all its sections.

### `PATCH /api/reports/templates/<id>/`
Update a template.

### `DELETE /api/reports/templates/<id>/`
Delete a template (own templates only).

### `POST /api/reports/templates/<id>/clone/`
Clone any accessible template to a new draft.

---

## 9. Section Management

### `POST /api/reports/templates/<id>/sections/`
Add a section to a template.
```json
{ "section_type": "hr_overview", "order": 3, "is_enabled": true, "config": {} }
```

### `PUT /api/reports/templates/<id>/sections/<section_id>/`
Update a section's config, title, or enabled state.

### `DELETE /api/reports/templates/<id>/sections/<section_id>/`
Remove a section.

### `POST /api/reports/templates/<id>/sections/reorder/`
Reorder sections.
```json
{ "section_order": ["uuid1", "uuid2", "uuid3"] }
```

---

## 10. Generate PDF

### `POST /api/reports/generate/`

Returns the PDF file as a binary download (`application/pdf`).

**Request**
```json
{
  "report_title":    "October 2025 Operations Report",
  "report_subtitle": "Kano Distribution Network",
  "orientation":     "portrait",
  "company_name":    "KANO ELECTRICITY DISTRIBUTION COMPANY",
  "save_history":    true,
  "sections": [
    { "section_type": "cover_page",         "config": {} },
    { "section_type": "table_of_contents",  "config": {} },
    { "section_type": "technical_metrics",  "config": { "metrics": ["hours_of_supply", "energy_delivered"] } },
    { "section_type": "hr_overview",        "config": {} },
    { "section_type": "commercial_overview","config": {} },
    { "section_type": "financial_overview", "config": {} }
  ],
  "filters": {
    "from_date":  "2025-10-01",
    "to_date":    "2025-10-31",
    "states":     [],
    "districts":  []
  }
}
```

**Response**
Binary PDF file download.
`Content-Disposition: attachment; filename="October_2025_Operations_Report.pdf"`

---

## 11. HTML Preview

### `POST /api/reports/preview/html/`

Returns the raw HTML that will be used to generate the PDF.
Render it in an `<iframe>` for a live preview.

Same request body as `/generate/`. Response:
```json
{ "html": "<html>...</html>" }
```

---

## 12. PDF Structure

Every generated PDF follows this fixed layout:

| Page   | Content                          |
|--------|----------------------------------|
| 1      | Cover page (dark branded, #002050)|
| 2+     | Table of Contents (auto-paginated)|
| Next   | Content sections in order         |

- The TOC is **always automatic** — page numbers are computed by the backend. Do not try to manage it.
- The cover page always uses the dark branded theme regardless of section theme.
- All content pages use a white background with dark blue text.
- Portrait orientation is standard; landscape is supported but not recommended.

---

## 13. Role-Based Access

| User role              | Accessible modules                                       |
|------------------------|----------------------------------------------------------|
| `super_admin` / `admin`| All modules (technical, hr, commercial, financial)       |
| Regular user           | Only modules granted via `UserSectionAccess` or `TemporaryAccess` |
| Unauthenticated        | General sections only (cover, TOC, custom text)          |

The `/sections/available/` endpoint always returns the correct filtered list for the requesting user. The frontend does not need to implement its own access logic.

---

## 14. Generated Reports History

### `GET /api/reports/history/`
Returns the list of previously generated reports for the current user.

```json
[
  {
    "id": "uuid",
    "report_title": "October 2025 Operations Report",
    "sections_included": ["cover_page", "technical_metrics", "hr_overview"],
    "created_at": "2025-10-31T14:22:00Z"
  }
]
```

---

## 15. Error Responses

All endpoints return standard error shapes:

```json
{ "error": "from_date and to_date filters are required" }
```

HTTP status codes used:
- `400` — bad request / missing required fields
- `403` — not allowed (e.g. deleting someone else's template)
- `404` — template or section not found
- `500` — data calculation error (check `error` message)
