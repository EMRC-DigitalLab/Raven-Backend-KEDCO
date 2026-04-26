# Compare Engine — Frontend Integration Guide

All endpoints are under `/api/analytics/`.
Auth: Bearer token (JWT) in the `Authorization` header.

---

## 1. Customer Search (Dropdown Picker)

Use this to let users search and select specific customers before running a comparison.

**`GET /api/analytics/compare/customers/search/`**

### Query params

| Param | Type | Default | Description |
|---|---|---|---|
| `q` | string | `""` | Search by customer name or account number |
| `customer_type` | string | `all` | `MDI` \| `MDNI` \| `all` |
| `scope_type` | string | — | `feeder` \| `district` \| `state` \| `station` |
| `scope_id` | UUID | — | Required if `scope_type` is set |
| `limit` | int | `50` | Max results (capped at 200) |

### Response

```json
{
  "count": 12,
  "limit": 50,
  "customers": [
    {
      "id":            "uuid",
      "account_no":    "KEDCO/KMC/001234",
      "customer_name": "DANGOTE FLOUR MILLS",
      "customer_type": "MDI",
      "feeder":        "Bompai 1",
      "district":      "Kano Municipal",
      "is_bypass":     false
    }
  ]
}
```

---

## 2. Customer Consumption Comparison

**`POST /api/analytics/compare/customers/`**

### Request body

```json
{
  "customer_type":      "MDI",
  "period_mode":        "weekly",
  "current_period":     { "from_date": "2026-04-20", "to_date": "2026-04-26" },
  "previous_period":    { "from_date": "2026-04-13", "to_date": "2026-04-19" },
  "scope_type":         "feeder",
  "scope_id":           "<uuid>",
  "customer_ids":       ["<uuid>", "<uuid>"],
  "select_all":         false,
  "top_n":              50,
  "sort_by":            "current_consumption",
  "positive_threshold": 10.0,
  "declined_threshold": -30.0,
  "include_insights":   true
}
```

### Field reference

| Field | Required | Default | Notes |
|---|---|---|---|
| `customer_type` | No | `all` | `MDI` \| `MDNI` \| `all` |
| `period_mode` | No | `weekly` | Auto-resolves periods. Ignored if explicit periods are provided. `daily` \| `weekly` \| `monthly` |
| `current_period` | No* | — | `{ from_date, to_date }` in `YYYY-MM-DD`. Overrides `period_mode` when both provided. |
| `previous_period` | No* | — | Must be provided alongside `current_period` |
| `scope_type` | No | — | Filters to a geographic area |
| `scope_id` | No | — | UUID of the scoped entity. Required if `scope_type` is set |
| `customer_ids` | No | — | Array of customer UUIDs from the search picker. When set, `top_n` is ignored |
| `select_all` | No | `false` | Return all customers in scope with no cap |
| `top_n` | No | `50` | Max customers returned (capped at 200) |
| `sort_by` | No | `current_consumption` | `current_consumption` \| `variance_pct` \| `decline` |
| `positive_threshold` | No | `10.0` | % above which a customer is "Positive Trend" |
| `declined_threshold` | No | `-30.0` | % below which a customer is "Major Declined Trend" |
| `include_insights` | No | `false` | Set `true` to attach AI insights to the response. Adds ~3-5s latency on first call (cached after) |

### Period mode auto-resolution

If you omit explicit periods and just pass `period_mode`, the API resolves:

| `period_mode` | Current period | Previous period |
|---|---|---|
| `daily` | Today | Yesterday |
| `weekly` | This Mon – today | Last Mon – last Sun |
| `monthly` | This month 1st – today | Last full calendar month |

### Response

```json
{
  "customer_type": "MDI",
  "period_mode":   "weekly",
  "scope": {
    "type":  "feeder",
    "label": "Bompai 1"
  },
  "current_period": {
    "from_date": "2026-04-20",
    "to_date":   "2026-04-26",
    "days":      7
  },
  "previous_period": {
    "from_date": "2026-04-13",
    "to_date":   "2026-04-19",
    "days":      7
  },
  "totals": {
    "current_consumption_kwh":  142500.0,
    "previous_consumption_kwh": 138200.0,
    "variance_kwh":             4300.0,
    "variance_pct":             3.11,
    "current_billed_amount":    28500000.0,
    "previous_billed_amount":   27640000.0,
    "billing_variance":         860000.0
  },
  "trend_distribution": {
    "Major Positive Trend": 2,
    "Positive Trend":       8,
    "Moderated Trend":      24,
    "Declined Trend":       10,
    "Major Declined Trend": 4,
    "No Previous Data":     2
  },
  "total_customers_in_scope": 120,
  "customers_returned":       50,
  "bypass_count":             3,
  "methodology": {
    "positive_threshold_pct":  10.0,
    "declined_threshold_pct":  -30.0,
    "major_positive_pct":      50.0,
    "trend_labels": {
      "Major Positive Trend": ">= 50%",
      "Positive Trend":       ">= 10%",
      "Moderated Trend":      ">= -30% and < 10%",
      "Major Declined Trend": "< -30%"
    }
  },
  "customers": [
    {
      "account_no":               "KEDCO/KMC/001234",
      "customer_name":            "DANGOTE FLOUR MILLS",
      "customer_type":            "MDI",
      "meter_status":             "active",
      "feeder":                   "Bompai 1",
      "district":                 "Kano Municipal",
      "is_bypass":                false,
      "has_estimated_readings":   false,
      "current_consumption_kwh":  8400.0,
      "previous_consumption_kwh": 7200.0,
      "reading_count_current":    4,
      "reading_count_previous":   4,
      "current_billed_amount":    1680000.0,
      "previous_billed_amount":   1440000.0,
      "billing_variance":         240000.0,
      "variance_kwh":             1200.0,
      "variance_pct":             16.67,
      "trend":                    "Positive Trend"
    }
  ],
  "ai_insights": {
    "headline": "MDI consumption up 3.1% week-on-week driven by industrial sector recovery.",
    "summary": "Overall billed energy increased from 138,200 kWh to 142,500 kWh. Billing revenue grew by ₦860,000. Most customers held steady, though four showed significant declines worth investigating.",
    "notable_trends": [
      "Dangote Flour Mills led growth with a 16.7% increase — consistent with a known production ramp-up.",
      "4 customers declined more than 30%, representing a combined loss of 3,200 kWh vs the prior week.",
      "2 customers have no previous period data — likely newly onboarded or meter recently restored."
    ],
    "watch_list": [
      {
        "customer_name": "BCNN INDUSTRIES",
        "account_no":    "KEDCO/KMC/005678",
        "reason":        "Consumption dropped 45% — from 3,200 kWh to 1,760 kWh. Possible production shutdown or meter fault."
      }
    ],
    "recommendations": [
      "Visit the 4 major decliners for a physical meter inspection before the next reading cycle.",
      "Confirm Dangote Flour Mills' increase is reflected in a corresponding tariff billing update.",
      "Onboard the 2 customers with no previous data into the weekly reading schedule."
    ],
    "cached": false
  }
}
```

### Customer row fields

| Field | Type | Notes |
|---|---|---|
| `account_no` | string | Unique customer account number |
| `customer_name` | string | — |
| `customer_type` | string | `MDI` or `MDNI` |
| `meter_status` | string | `active` \| `faulty` \| `missing` \| `bypassed` |
| `feeder` | string \| null | Feeder name |
| `district` | string \| null | Business district name |
| `is_bypass` | bool | `true` if meter_status is `bypassed` |
| `has_estimated_readings` | bool | `true` if any reading in the current period was estimated rather than a real meter read |
| `current_consumption_kwh` | float | Total billed kWh in current period |
| `previous_consumption_kwh` | float | Total billed kWh in previous period |
| `reading_count_current` | int | Number of readings submitted in current period |
| `reading_count_previous` | int | Number of readings submitted in previous period |
| `current_billed_amount` | float | NGN billed in current period (kWh × tariff rate) |
| `previous_billed_amount` | float | NGN billed in previous period |
| `billing_variance` | float | `current_billed_amount − previous_billed_amount` (NGN) |
| `variance_kwh` | float \| null | `current − previous` kWh. `null` if no previous data |
| `variance_pct` | float \| null | % change. `null` if no previous baseline |
| `trend` | string | One of the five trend labels below |

### Trend labels

| Label | Meaning |
|---|---|
| `Major Positive Trend` | >= 50% increase |
| `Positive Trend` | >= `positive_threshold`% (default 10%) |
| `Moderated Trend` | >= `declined_threshold`% and < `positive_threshold`% |
| `Major Declined Trend` | < `declined_threshold`% (default -30%) |
| `No Previous Data` | No readings in the previous period to compare against |

Use `methodology.trend_labels` from the response to show accurate threshold values in a tooltip rather than hardcoding them.

---

## 3. Comparison PDF Report

**`POST /api/analytics/compare/customers/report/`**

Takes the same body as the compare endpoint. Returns a base64-encoded PDF.

### Request body

```json
{
  "customer_type":      "MDI",
  "period_mode":        "weekly",
  "scope_type":         "feeder",
  "scope_id":           "<uuid>",
  "top_n":              200,
  "sort_by":            "decline",
  "positive_threshold": 10.0,
  "declined_threshold": -30.0,
  "include_insights":   true,
  "report_title":       "Week 17 MDI Comparison — Bompai 1"
}
```

### Additional fields (report-only)

| Field | Default | Notes |
|---|---|---|
| `include_insights` | `true` | AI insights page is included by default in reports |
| `report_title` | Auto-generated | Overrides the cover page title |
| `top_n` | `200` | Default is higher for reports than for the table view |

### Response

```json
{
  "pdf_base64":          "<base64-encoded PDF bytes>",
  "filename":            "Week_17_MDI_Comparison_Bompai_1.pdf",
  "period_mode":         "weekly",
  "customers_in_report": 42
}
```

### How to trigger download in the browser

```js
const res = await fetch('/api/analytics/compare/customers/report/', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});

const { pdf_base64, filename } = await res.json();

const bytes  = Uint8Array.from(atob(pdf_base64), c => c.charCodeAt(0));
const blob   = new Blob([bytes], { type: 'application/pdf' });
const url    = URL.createObjectURL(blob);
const anchor = document.createElement('a');
anchor.href     = url;
anchor.download = filename;
anchor.click();
URL.revokeObjectURL(url);
```

### Report pages

| Page | Content |
|---|---|
| Cover | Title, subtitle (customer type + scope), period |
| Table of Contents | Auto-generated with accurate page numbers |
| Comparison Summary | 6 KPI cards (consumption, billing, variance) + trend distribution table |
| Customer Detail | Paginated table — customer name, account, feeder, kWh, variance %, billing variance ₦, trend label |
| AI Insights | Headline, narrative, notable trends, watch list, recommendations (only if `include_insights: true` and API key is configured) |
| Back page | Auto-generated |

---

## 4. UI Recommendations

### Comparison table

- Colour-code the `trend` column using the five labels:
  - `Major Positive Trend` → deep green
  - `Positive Trend` → green
  - `Moderated Trend` → amber
  - `Declined Trend` → orange
  - `Major Declined Trend` → red
  - `No Previous Data` → grey
- Show `has_estimated_readings` as a small badge (e.g. "Est.") on the kWh cell — use `methodology.trend_labels` in a tooltip to explain what it means.
- Show `meter_status` as a badge on the customer name: `faulty` and `missing` in red/orange.
- The `totals` block at the top of the response is ready to drive a summary bar above the table.

### Period selector

Offer three quick modes (`This week vs last`, `This month vs last`, `Today vs yesterday`) backed by `period_mode`. Also allow a custom date picker that sends explicit `current_period` / `previous_period`.

### Threshold controls

Expose `positive_threshold` and `declined_threshold` as optional sliders or inputs (default 10 / -30). Pull the labels from `methodology.trend_labels` to keep the tooltip copy in sync with the backend — never hardcode them in the frontend.

### AI insights

Show the `ai_insights` block in a collapsible panel or a side drawer. Indicate `cached: true` with a small "Cached" badge so users know the insights are from an earlier call. The `watch_list` pairs well with a highlighted card list rather than a table.
