# Raven Report Engine — Frontend Integration Guide

> **Audience:** Frontend developers integrating with the Raven Backend (KEDCO).
> **Base URL:** Configured per environment via `BASE_URL` in Django settings.
> **Auth:** All endpoints require authentication (session / token — follow existing app pattern).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Core Concepts](#2-core-concepts)
3. [Endpoints](#3-endpoints)
   - [3.1 Generate Report Data (JSON)](#31-post-apireportsgeneratedata)
   - [3.2 Generate PDF (legacy server-side)](#32-post-apireportsgeneratepdf)
   - [3.3 Generate Management Report PDF](#33-post-apireportsgeneratemanagement)
   - [3.4 Generate Customer Comparison PDF](#34-post-apireportsgeneratemanagement-1)
   - [3.5 HTML Preview](#35-post-apireportsgeneratehtml-preview)
   - [3.6 Filter Options](#36-get-apireportsfiltersoptions)
   - [3.7 Available Sections](#37-get-apireportssectionsavailable)
   - [3.8 Template CRUD](#38-template-crud)
4. [Request Body Reference](#4-request-body-reference)
   - [4.1 Filters](#41-filters)
   - [4.2 Theme](#42-theme)
   - [4.3 Sections Array](#43-sections-array)
   - [4.4 Orientation](#44-orientation)
5. [All Section Types](#5-all-section-types-41-total)
   - [5.1 General](#51-general)
   - [5.2 Technical](#52-technical)
   - [5.3 Segment / Dispatch Compliance (MDI lens)](#53-segment--dispatch-compliance-mdi-lens)
   - [5.4 Human Resources](#54-human-resources)
   - [5.5 Commercial](#55-commercial)
   - [5.6 Financial](#56-financial)
   - [5.7 Comparison](#57-comparison)
6. [Response Format](#6-response-format)
7. [Management Report](#7-management-report)
8. [Self-Service Report Builder Pattern](#8-self-service-report-builder-pattern)
9. [RAG Status & Compliance Logic](#9-rag-status--compliance-logic)
10. [Error Handling](#10-error-handling)

---

## 1. Architecture Overview

```
Frontend
  │
  ├── POST /api/reports/generate/data/        ← Preferred: JSON data for client-side PDF
  ├── POST /api/reports/generate/management/  ← AI-narrative management report (PDF download)
  ├── POST /api/analytics/compare/customers/report/ ← Customer comparison PDF
  └── POST /api/reports/generate/pdf/        ← Legacy server-side PDF (still supported)
```

**Recommended integration pattern:**

1. Call `/generate/data/` → receive structured JSON
2. Frontend renders and generates PDF client-side (e.g. with react-pdf or similar)
3. The JSON response includes `orientation`, `theme`, and section data — use all of it

---

## 2. Core Concepts

### Sections
A report is an ordered array of **section objects**. Each section has a `section_type` and an optional `config`. The backend returns data for each section; the frontend renders it.

### Segments (MDI Lens)
Every feeder is classified into one of three business segments derived from commercial customer connections:
- **MDI** — feeder has at least one Maximum Demand Installation customer
- **Non-MDI Band A** — feeder band is A, no MDI customers
- **Non-MDI Non-Band A** — everything else

This lens is used by all segment/dispatch compliance sections.

### Band Supply Targets
Used for compliance % calculation (no migration required — computed at runtime):

| Band | Target (hrs/day) |
|------|-----------------|
| A    | 20.0            |
| B    | 16.0            |
| C    | 12.0            |
| D    | 8.0             |
| E    | 8.0             |

### Compliance Status
Computed as `(actual_supply / target) × 100`:

| Status       | Range        | Color    |
|-------------|--------------|----------|
| Exceeding    | ≥ 105%       | Green    |
| On Target    | 95% – 105%   | Blue     |
| Below Target | 85% – 94%    | Amber    |
| Poor         | 75% – 84%    | Pink     |
| Critical     | < 75%        | Red      |

---

## 3. Endpoints

### 3.1 `POST /api/reports/generate/data/`

**Primary endpoint.** Returns structured JSON for every requested section. The frontend uses this to render and generate PDFs client-side.

#### Request Body

```json
{
  "report_title": "May 2026 Performance Report",
  "report_subtitle": "11kV Feeders — KEDCO Wide",
  "orientation": "landscape",
  "company_name": "KANO ELECTRICITY DISTRIBUTION COMPANY",
  "include_ai_insights": false,
  "include_ai_summary": false,
  "save_history": true,
  "theme": {
    "primary_color": "#002050",
    "accent_color": "rgba(0, 32, 80, 0.2)",
    "text_color": "#002050"
  },
  "filters": {
    "from_date": "2026-05-01",
    "to_date": "2026-05-31",
    "states": [],
    "districts": [],
    "substations": [],
    "feeders": [],
    "bands": [],
    "voltage_level": "11kv"
  },
  "sections": [
    { "section_type": "cover_page", "config": {} },
    { "section_type": "table_of_contents", "config": {} },
    {
      "section_type": "technical_metrics",
      "config": {
        "metrics": ["hours_of_supply", "average_load", "energy_delivered", "total_interruptions"],
        "section_description": "Overview of key technical KPIs for the period."
      }
    },
    { "section_type": "segment_compliance_summary", "config": {} },
    { "section_type": "feeder_segment_compliance", "config": {} }
  ]
}
```

#### Response

```json
{
  "report_id": "uuid",
  "report_title": "May 2026 Performance Report",
  "report_subtitle": "...",
  "orientation": "landscape",
  "theme": { "primary_color": "#002050", "accent_color": "...", "text_color": "..." },
  "company_name": "KANO ELECTRICITY DISTRIBUTION COMPANY",
  "generated_at": "2026-06-08T12:00:00Z",
  "generated_by": "Full Name",
  "period": {
    "from_date": "2026-05-01",
    "to_date": "2026-05-31",
    "days": 31,
    "label": "May 2026"
  },
  "sections_denied": [],
  "sections": [
    {
      "section_type": "technical_metrics",
      "title": "",
      "config": { "metrics": ["hours_of_supply", "average_load", "energy_delivered", "total_interruptions"] },
      "data": {
        "hours_of_supply": 7.32,
        "average_load": 0.54,
        "energy_delivered": 57029.86,
        "total_interruptions": 5781
      },
      "ai_insights": null
    },
    {
      "section_type": "segment_compliance_summary",
      "title": "",
      "config": {},
      "data": [
        {
          "segment": "MDI",
          "total": 32,
          "avg_supply": 18.4,
          "avg_pct_achieved": 92.0,
          "total_energy_mwh": 21400.0,
          "exceeding":    { "count": 7,  "pct": 21.9 },
          "on_target":    { "count": 23, "pct": 71.9 },
          "below_target": { "count": 1,  "pct": 3.1  },
          "poor":         { "count": 1,  "pct": 3.1  },
          "critical":     { "count": 0,  "pct": 0.0  }
        },
        {
          "segment": "Non-MDI Band A",
          "total": 21,
          "avg_supply": 19.83,
          "avg_pct_achieved": 99.2,
          "total_energy_mwh": 8700.5,
          "exceeding":    { "count": 7,  "pct": 33.3 },
          "on_target":    { "count": 14, "pct": 66.7 },
          "below_target": { "count": 0,  "pct": 0.0  },
          "poor":         { "count": 0,  "pct": 0.0  },
          "critical":     { "count": 0,  "pct": 0.0  }
        },
        {
          "segment": "Non-MDI Non-Band A",
          "total": 72,
          "avg_supply": 4.2,
          "avg_pct_achieved": 42.0,
          "total_energy_mwh": 26929.36,
          "exceeding":    { "count": 17, "pct": 23.6 },
          "on_target":    { "count": 13, "pct": 18.1 },
          "below_target": { "count": 6,  "pct": 8.3  },
          "poor":         { "count": 7,  "pct": 9.7  },
          "critical":     { "count": 29, "pct": 40.3 }
        }
      ]
    },
    {
      "section_type": "feeder_segment_compliance",
      "data": [
        {
          "id": "uuid",
          "name": "CERAMIC",
          "band": "A",
          "district": "Kano Metro",
          "hours_of_supply": 23.03,
          "availability_percentage": 96.0,
          "energy_delivered": 2681.55,
          "peak_load": 4.80,
          "segment": "MDI",
          "target_hours": 20.0,
          "gap": 3.03,
          "pct_achieved": 115.2,
          "status": "exceeding"
        }
      ]
    }
  ],
  "ai_summary": null
}
```

---

### 3.2 `POST /api/reports/generate/pdf/`

Legacy server-side PDF generation. Same request body as `/generate/data/` (the `orientation` and `theme` fields are fully respected). Returns `application/pdf` as file download.

> **Note:** The `/generate/data/` + client-side rendering path is preferred. This endpoint is maintained for backward compatibility.

---

### 3.3 `POST /api/reports/generate/management/`

Generates a narrative **Management / Admin Report** PDF. Uses Claude Sonnet to write the full management commentary automatically.

#### Request Body

```json
{
  "report_title": "May 2026 11kV Feeder Performance Management Report",
  "report_subtitle": "Prepared for management review",
  "company_name": "KANO ELECTRICITY DISTRIBUTION COMPANY",
  "include_ai": true,
  "return_base64": false,
  "theme": {
    "primary_color": "#002050",
    "accent_color": "rgba(0, 32, 80, 0.2)",
    "text_color": "#002050"
  },
  "filters": {
    "from_date": "2026-05-01",
    "to_date": "2026-05-31",
    "voltage_level": "11kv"
  }
}
```

#### Key fields

| Field          | Type    | Default   | Description |
|----------------|---------|-----------|-------------|
| `include_ai`   | boolean | `true`    | Set `false` to skip Claude and use placeholder text (much faster, no API cost) |
| `return_base64`| boolean | `false`   | Return `{"pdf_base64": "...", "filename": "..."}` instead of file download |

#### Response (default — file download)

`Content-Type: application/pdf`
`Content-Disposition: attachment; filename="May_2026_Management_Report.pdf"`

#### Response (`return_base64: true`)

```json
{
  "pdf_base64": "JVBERi0xLjQK...",
  "filename": "May_2026_Management_Report.pdf"
}
```

#### Management Report Sections (auto-generated, no config needed)

| # | Section | Content |
|---|---------|---------|
| 1 | Cover page | Branded portrait cover |
| 2 | Executive Summary | 4 AI paragraphs + headline KPI strip + management priority callout |
| 3 | KPI Dashboard | Table: Current / Previous / Movement / AI interpretation / RAG status |
| 4 | Reliability Review | Interruption breakdown table + AI implications table |
| 5 | Feeder Performance | Top-10 strong feeders + weak/exception feeders with AI commentary |
| 6 | State Performance | State table with interruption intensity per feeder |
| 7 | Service Band Review | Band table with AI interpretations + recovery actions |
| 8 | Priority Issues | AI-generated issues with evidence / risk / response |
| 9 | Action Plan | AI-generated actions with team / timeline / output |
| 10 | Back page | Branded closing page |

---

### 3.4 `POST /api/analytics/compare/customers/report/`

Generates a customer consumption comparison PDF.

#### Request Body

```json
{
  "customer_type": "MDI",
  "period_mode": "monthly",
  "current_period": { "from_date": "2026-05-01", "to_date": "2026-05-31" },
  "previous_period": { "from_date": "2026-04-01", "to_date": "2026-04-30" },
  "scope_type": "district",
  "scope_id": "uuid",
  "top_n": 200,
  "sort_by": "current_consumption",
  "include_insights": true,
  "report_title": "MDI Consumption Comparison — May vs Apr 2026",
  "company_name": "KANO ELECTRICITY DISTRIBUTION COMPANY",
  "orientation": "landscape",
  "theme": { "primary_color": "#002050" }
}
```

#### Response

```json
{
  "pdf_base64": "JVBERi0xLjQK...",
  "filename": "MDI_Consumption_Comparison.pdf",
  "period_mode": "monthly",
  "customers_in_report": 147
}
```

---

### 3.5 `POST /api/reports/generate/html-preview/`

Returns raw HTML for iframe preview. Same body as `/generate/data/` but returns HTML string.

```json
{ "html": "<!DOCTYPE html>..." }
```

---

### 3.6 `GET /api/reports/filters/options/`

Returns all available filter values for building the filter UI.

```json
{
  "states":       [{ "id": "uuid", "name": "Kano" }],
  "districts":    [{ "id": "uuid", "name": "Kano Metro", "state__name": "Kano", "state_id": "uuid" }],
  "substations":  [{ "id": "uuid", "name": "NAIBAWA" }],
  "bands":        [{ "id": "uuid", "name": "A", "description": "..." }],
  "feeders":      [{ "id": "uuid", "name": "CERAMIC", "band__name": "A", "voltage_level": "33kv", "business_district__name": "Kano Metro" }],
  "voltage_levels": [
    { "id": "11kv", "name": "11kV Feeders" },
    { "id": "33kv", "name": "33kV Feeders" }
  ]
}
```

---

### 3.7 `GET /api/reports/sections/available/`

Returns section types the current user is permitted to use (role-based).

```json
{
  "sections": [
    {
      "section_type": "technical_metrics",
      "display_name": "Technical Metrics Cards",
      "description": "Key technical metrics displayed as cards",
      "category": "technical",
      "supports_chart": false,
      "config_options": { "metrics": { "type": "multi_select", "options": [...], "default": [...] } }
    }
  ],
  "categories": [
    { "id": "general",    "name": "General" },
    { "id": "technical",  "name": "Technical" },
    { "id": "hr",         "name": "Human Resources" },
    { "id": "commercial", "name": "Commercial" },
    { "id": "financial",  "name": "Financial" },
    { "id": "comparison", "name": "Comparisons" }
  ]
}
```

---

### 3.8 Template CRUD

| Method | URL | Description |
|--------|-----|-------------|
| `GET`  | `/api/reports/templates/` | List user's templates + public templates |
| `POST` | `/api/reports/templates/` | Create new template |
| `GET`  | `/api/reports/templates/{id}/` | Get template detail with sections |
| `PUT`/`PATCH` | `/api/reports/templates/{id}/` | Update template |
| `DELETE` | `/api/reports/templates/{id}/` | Delete own template |
| `POST` | `/api/reports/templates/{id}/clone/` | Clone a template |
| `POST` | `/api/reports/templates/{id}/sections/` | Add section to template |
| `PUT`/`DELETE` | `/api/reports/templates/{id}/sections/{section_id}/` | Update/delete section |
| `POST` | `/api/reports/templates/{id}/sections/reorder/` | Reorder sections |

---

## 4. Request Body Reference

### 4.1 Filters

All filter fields are optional. Omit to get all onboarded feeders.

```json
{
  "from_date": "2026-05-01",
  "to_date": "2026-05-31",
  "states":      ["uuid", "uuid"],
  "districts":   ["uuid"],
  "substations": ["uuid"],
  "feeders":     ["uuid"],
  "bands":       ["uuid"],
  "voltage_level": "11kv"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `from_date` | string `YYYY-MM-DD` | **Required** |
| `to_date` | string `YYYY-MM-DD` | **Required** |
| `states` | UUID array | Filter to feeders in these states |
| `districts` | UUID array | Filter to feeders in these districts |
| `substations` | UUID array | Filter to feeders under these substations |
| `feeders` | UUID array | Specific feeders only |
| `bands` | UUID array | Filter to feeders in these service bands |
| `voltage_level` | `"11kv"` \| `"33kv"` | Filter by voltage. Omit for both |

---

### 4.2 Theme

All fields optional. Defaults to navy `#002050` theme if omitted.

```json
{
  "primary_color": "#002050",
  "accent_color":  "rgba(0, 32, 80, 0.2)",
  "text_color":    "#002050"
}
```

| Field | What it controls |
|-------|-----------------|
| `primary_color` | Table headers, cover page background, section icons, cover accent strip |
| `accent_color` | Footer border, card borders, dividers, TOC dots |
| `text_color` | Body text, KPI values, page titles, page numbers |

> **Tip:** `primary_color` should be a full hex (`#rrggbb`). The backend derives lighter variants automatically for card backgrounds. `accent_color` can be `rgba()`.

---

### 4.3 Sections Array

Each section object:

```json
{
  "section_type": "technical_metrics",
  "title": "",
  "config": {
    "metrics": ["hours_of_supply", "energy_delivered"],
    "section_description": "Optional descriptive text shown under the section title.",
    "chart_type": "line"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `section_type` | string | One of the 41 section types listed in §5 |
| `title` | string | Optional override for the section title in the PDF |
| `config` | object | Section-specific options (see §5 for each section's config options) |
| `config.section_description` | string | **Global** — works on any section. Renders as a styled callout box below the section title |
| `config.chart_type` | string | **Chart sections only** — `"line"` \| `"bar"` \| `"table_only"` |

---

### 4.4 Orientation

```json
{ "orientation": "portrait" }
```

| Value | Page size | Best for |
|-------|-----------|----------|
| `"landscape"` (default) | 297mm × 210mm | Wide tables, dashboards |
| `"portrait"` | 210mm × 297mm | Management reports, narrative docs |

> **Note:** In portrait mode, all page divs (including those designed for landscape) are automatically overridden to portrait dimensions. Table fonts reduce to 9px and cells wrap to fit.

---

## 5. All Section Types (41 total)

### 5.1 General

| Section Type | Display Name | Config Options |
|-------------|-------------|----------------|
| `cover_page` | Cover Page | `show_logo`, `show_subtitle` |
| `table_of_contents` | Table of Contents | _(auto-generated)_ |
| `custom_text` | Custom Text/Notes | `title`, `content` (rich text) |
| `gaps_improvements` | Gaps and Improvement Areas | `sections` (list of `{title, content[]}`) |

---

### 5.2 Technical

| Section Type | Display Name | Config Options |
|-------------|-------------|----------------|
| `infrastructure_overview` | Infrastructure Overview | `show_feeder_table`, `summary_points[]` |
| `technical_metrics` | Technical Metrics Cards | `metrics[]` — choose from: `hours_of_supply`, `average_load`, `peak_load`, `energy_delivered`, `daily_average_consumption`, `total_interruptions`, `load_shedding_count` |
| `system_reliability` | System Reliability | `show_cumulative_hours`, `show_avg_duration`, `show_turnaround_time` |
| `interruption_breakdown` | Interruption Breakdown | `group_by`: `"type"` \| `"feeder"` \| `"day"` |
| `hours_of_supply_chart` | Hours of Supply Chart | `chart_type`: `"line"` \| `"bar"`, `group_by`: `"day"` \| `"week"` |
| `load_trend_chart` | Load Trend Chart | `chart_type`, `metric`: `"average_load"` \| `"peak_load"` |
| `energy_delivered_chart` | Energy Delivered Chart | `chart_type`: `"line"` \| `"bar"` |
| `feeder_performance_table` | Feeder Performance Table | `columns[]`, `sort_by` |
| `state_performance_table` | State Performance Table | _(paginated automatically)_ |
| `district_performance_table` | District Performance Table | _(paginated automatically)_ |
| `service_band_summary` | Service Band Summary | `show_chart` |
| `dso_compliance_overview` | DSO Compliance Overview | — |
| `dso_compliance_table` | DSO Compliance Table | _(landscape-forced, 17 rows/page)_ |

---

### 5.3 Segment / Dispatch Compliance (MDI Lens)

All 6 sections below classify feeders by business segment derived from MDI commercial customer connections.

| Section Type | Display Name | What it shows |
|-------------|-------------|---------------|
| `segment_compliance_summary` | Compliance by Business Segment | Side-by-side cards per segment: total feeders, avg supply (hrs), avg % achieved, total energy (MWh), compliance status bars (Exceeding→Critical) |
| `feeder_segment_compliance` | Feeder Compliance by Segment | Paginated table: Feeder \| Segment \| Band \| Target hrs \| Actual hrs \| Gap \| % Achieved \| Energy (MWh) \| Status badge |
| `energy_by_segment_pl` | Energy by P&L Segment | Total energy in MWh split MDI vs Non-MDI with % share — horizontal progress bars |
| `segment_voltage_energy` | Energy by Segment & Voltage | Energy in 4 buckets: MDI 33kV / MDI 11kV / Non-MDI 33kV / Non-MDI 11kV |
| `energy_md_nmd_mix` | MD vs NMD Energy Mix | MD vs NMD % share vs 60/40 target, with gap indicator |
| `segment_compliance_trend` | Segment Compliance Trend | SVG line chart — daily % of feeders on-target-or-better per segment over the period |

> **No config needed** for any of these sections. Segment classification and targets are computed server-side.

#### `feeder_segment_compliance` — data shape per feeder row

```json
{
  "id": "uuid",
  "name": "CERAMIC",
  "band": "A",
  "district": "Kano Metro",
  "hours_of_supply": 23.03,
  "availability_percentage": 96.0,
  "energy_delivered": 2681.55,
  "peak_load": 4.80,
  "energy_source": "meter",
  "segment": "MDI",
  "target_hours": 20.0,
  "gap": 3.03,
  "pct_achieved": 115.2,
  "status": "exceeding"
}
```

#### `energy_md_nmd_mix` — data shape

```json
{
  "total_energy_mwh": 57029.86,
  "md_target_pct": 60.0,
  "nmd_target_pct": 40.0,
  "md_actual_pct": 43.9,
  "nmd_actual_pct": 56.1,
  "md_energy_mwh": 25037.2,
  "nmd_energy_mwh": 31992.66,
  "md_gap_pct": -16.1
}
```

---

### 5.4 Human Resources

| Section Type | Display Name |
|-------------|-------------|
| `hr_overview` | HR Overview |
| `staff_metrics` | Staff Metrics |
| `wage_bill_analysis` | Wage Bill Analysis |
| `department_headcount` | Department Headcount |
| `attrition_analysis` | Attrition Analysis |
| `recruitment_summary` | Recruitment Summary |

> HR sections require the user to have `hr` module access.

---

### 5.5 Commercial

| Section Type | Display Name | Notes |
|-------------|-------------|-------|
| `commercial_overview` | Commercial Overview | Includes MDI/MDNI count and revenue split |
| `commercial_coverage` | Reading Coverage | Customers read vs unread, revenue at risk |
| `commercial_energy` | Energy Analysis | Delivered vs consumed vs billed, AT&C loss |
| `revenue_by_district` | Revenue by District | — |
| `revenue_by_feeder` | Revenue by Feeder | — |
| `customer_type_summary` | Customer Type Summary | MDI vs MDNI headcount and revenue |
| `customer_comparison` | Customer Comparison | See config below |

#### `customer_comparison` config

```json
{
  "customer_type": "MDI",
  "current_period":  { "from_date": "...", "to_date": "..." },
  "previous_period": { "from_date": "...", "to_date": "..." },
  "scope_type": "district",
  "scope_id": "uuid",
  "top_n": 50,
  "include_insights": false
}
```

---

### 5.6 Financial

| Section Type | Display Name |
|-------------|-------------|
| `financial_overview` | Financial Overview |
| `opex_by_category` | OPEX by Category |
| `opex_by_district` | OPEX by District |

> Financial sections require the user to have `financial` module access.

---

### 5.7 Comparison

| Section Type | Config |
|-------------|--------|
| `entity_comparison` | `entity_type`, `entity_ids[]`, `metrics[]`, `granularity`, `feeder_type`, `include_trend` |
| `period_comparison` | `entity_type`, `entity_id`, `metrics[]`, `periods[]`, `feeder_type` |

> Comparison sections require at least one module access. The compare engine enforces metric-level access internally.

---

## 6. Response Format

### `sections` array — per section

```json
{
  "section_type": "string",
  "title": "string",
  "config": {},
  "data": {},
  "ai_insights": null
}
```

The `data` field shape varies by `section_type`. See §5 for shapes of new segment sections. Existing section shapes are unchanged.

### `sections_denied`

If a user requests a section type they don't have access to, it is stripped and listed here:

```json
{ "sections_denied": ["hr_overview", "wage_bill_analysis"] }
```

---

## 7. Management Report

The management report (`POST /api/reports/generate/management/`) is a **separate report type** — not a composition of sections. It always generates:

- Portrait orientation
- AI-written narrative (executive summary, interpretations, action plans)
- RAG KPI dashboard (Red/Amber/Green)
- Priority issues and action plan tables

**Key integration points:**

```js
// Fast preview (no AI cost, placeholder text)
const res = await fetch('/api/reports/generate/management/', {
  method: 'POST',
  body: JSON.stringify({
    include_ai: false,
    return_base64: true,
    filters: { from_date: '...', to_date: '...' }
  })
})
const { pdf_base64, filename } = await res.json()
// Convert base64 to blob and trigger download

// Full AI report (recommended for final generation)
const res = await fetch('/api/reports/generate/management/', {
  method: 'POST',
  body: JSON.stringify({
    include_ai: true,
    return_base64: false,
    report_title: 'May 2026 Management Report',
    theme: { primary_color: '#002050' },
    filters: { from_date: '2026-05-01', to_date: '2026-05-31' }
  })
})
// Response is application/pdf — trigger download directly
```

---

## 8. Self-Service Report Builder Pattern

The report engine is fully composable. Users can build any report by selecting sections.

### Minimal working request

```json
{
  "report_title": "My Custom Report",
  "filters": { "from_date": "2026-05-01", "to_date": "2026-05-31" },
  "sections": [
    { "section_type": "cover_page", "config": {} },
    { "section_type": "technical_metrics", "config": {} }
  ]
}
```

### Full MDI/MDNI dispatch report

```json
{
  "report_title": "KEDCO Dispatch Compliance — May 2026",
  "orientation": "portrait",
  "theme": { "primary_color": "#002050" },
  "filters": {
    "from_date": "2026-05-01",
    "to_date": "2026-05-31",
    "voltage_level": "11kv"
  },
  "sections": [
    { "section_type": "cover_page", "config": {} },
    { "section_type": "table_of_contents", "config": {} },
    { "section_type": "technical_metrics", "config": { "metrics": ["hours_of_supply", "energy_delivered", "total_interruptions"] } },
    { "section_type": "energy_md_nmd_mix", "config": {} },
    { "section_type": "energy_by_segment_pl", "config": {} },
    { "section_type": "segment_voltage_energy", "config": {} },
    { "section_type": "segment_compliance_summary", "config": {} },
    { "section_type": "feeder_segment_compliance", "config": {} },
    { "section_type": "segment_compliance_trend", "config": {} }
  ]
}
```

### Template save/load flow

```
1. User builds report → POST /api/reports/templates/ (save sections + config)
2. User opens saved template → GET /api/reports/templates/{id}/
3. User clicks Generate → POST /api/reports/generate/data/ (pass template sections + fresh filters)
4. Frontend renders PDF from JSON response
```

---

## 9. RAG Status & Compliance Logic

The backend computes compliance status automatically. Frontend should render these status values:

| `status` string | Color (bg) | Color (text) | Label |
|----------------|-----------|-------------|-------|
| `"exceeding"` | `#e8f5e9` | `#2e7d32` | Exceeding |
| `"on_target"` | `#e3f2fd` | `#1565c0` | On Target |
| `"below_target"` | `#fff3e0` | `#e65100` | Below Target |
| `"poor"` | `#fce4ec` | `#ad1457` | Poor |
| `"critical"` | `#ffebee` | `#c62828` | Critical |

The management report also uses RAG for KPIs:

| `status` string | Meaning |
|----------------|---------|
| `"green"` | Performing well |
| `"amber"` | Requires monitoring |
| `"red"` | Urgent management attention |

---

## 10. Error Handling

All endpoints return standard error shapes:

```json
{ "error": "Human-readable error message" }
```

| HTTP Status | Meaning |
|-------------|---------|
| `400` | Bad request (missing required fields, invalid section type, invalid date) |
| `403` | Insufficient permissions for requested section(s) |
| `500` | Server error (AI timeout, PDF engine failure, data service error) |

### Section access denied

If the user lacks access to some sections, the request still succeeds — denied sections are stripped and listed in `sections_denied`. The PDF is generated with the permitted sections only.

### AI failure

If the AI call fails (timeout, API key issue), the management report gracefully falls back to placeholder text. The PDF is always generated — never blocked by AI failure.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-06-08 | Added 6 segment/dispatch compliance sections (MDI lens) |
| 2026-06-08 | Added management report endpoint |
| 2026-06-08 | Added portrait orientation support |
| 2026-06-08 | Added theme system (primary/accent/text colors) |
| 2026-06-08 | Added per-section `section_description` and `chart_type` config |
| 2026-06-08 | State and district tables now paginate (no more overflow) |
| 2026-06-08 | Comparison report (`/analytics/compare/customers/report/`) now respects `orientation` and `theme` |
