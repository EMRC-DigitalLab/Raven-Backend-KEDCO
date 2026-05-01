# Frontend Handoff — Backend Changes Summary

> Prepared for the frontend team.
> All changes are on the `development` branch.

---

## 1. Report Engine — Yellow Colour Removed

All yellow (`#fcd300`) has been removed from the PDF report generator.

**What changed visually:**

| Element | Before | After |
|---|---|---|
| Cover page accent bar | Yellow | Subtle white (25% opacity) |
| Cover eyebrow text | Yellow | White (65% opacity) |
| Cover title accent word | Yellow | White (75% opacity) |
| Cover rule bar | Yellow | White (40% opacity) |
| Table of contents numbers & page numbers | Yellow | Navy `#002050` |
| System Reliability values | Yellow | Navy `#002050` |
| Feeder table band header text | Yellow | White (on navy background) |
| Energy source "SYSTEM" dot | Yellow | Light blue `#6c9bd1` |

No API changes. This is purely visual inside the generated PDF.

---

## 2. Report Engine — Real Charts on Trend Sections

The three trend sections in technical reports now render **actual SVG charts** instead of just data tables.

**Affected section types:**
- `hours_of_supply_chart`
- `load_trend_chart`
- `energy_delivered_chart`

**What each page now shows:**
1. **SVG chart** — line chart (hours of supply, load) or bar chart (energy delivered)
2. **Summary strip** — Min / Avg / Max / Trend arrow cards
3. **Data table** — the existing two-column daily data table (unchanged)

Charts are pure SVG — no JavaScript, no external libraries. They render correctly in both Playwright (primary PDF engine) and WeasyPrint (fallback).

No API changes. The section types remain the same.

---

## 3. Report Engine — Period-over-Period Comparison on Trend Charts

When any trend section is rendered, the chart **automatically shows the previous period alongside the current one** for comparison.

**How it works:**
- Selected period: e.g. March 1–31, 2026
- Previous period: automatically calculated as the same-length window immediately before (Feb 1–28, 2026)
- No extra parameters needed from the frontend — it's always automatic

**What the chart shows:**
- **Solid navy line** = selected period
- **Dashed light-blue line** = previous period
- **Legend** top-right corner with period labels (e.g. "Mar 2026" / "Feb 2026")

**Summary strip now has a 5th card:**
- "vs Previous" — shows `▲ +4.2%` or `▼ -7.1%` comparing current-period average to previous-period average, colour-coded green/red

No API changes.

---

## 4. DSO Compliance — New Report Sections

Two new section types have been added to the technical report category for DSO submission compliance.

### What it tracks

For each **injection substation** (station_type = `injection` only, not transmission), and its **onboarded 11kV feeders**:

- **Hourly Load compliance** — expected submissions = feeders × days × 24 hours. Actual vs expected shown as a percentage.
- **Energy Reading compliance** — expected = feeders × days. Actual vs expected shown as a percentage.
- Each broken down by **DSO submissions** vs **admin override submissions**.
- Stations flagged **Compliant** (both ≥ 80%) or **Non-Compliant** (either below 80%).

### New section types

| Section type | Description |
|---|---|
| `dso_compliance_overview` | Summary page with donut chart, KPI cards, top non-compliant stations |
| `dso_compliance_table` | Paginated landscape table — one row per injection station |

### How to add to a report template

Use the existing report template builder. Both section types appear under **category: `technical`**.

### New report category

A new category `compliance` has been added to `ReportTemplate.CATEGORY_CHOICES`. This is available for creating dedicated compliance report templates (e.g. a report that contains only the DSO compliance sections, or mixed with HR compliance in future).

> **Migration required:** Run `python manage.py migrate` on the server before using.

---

## 5. Data Backfill API — Trigger Historical Sync from Frontend

Two new REST endpoints allow admins to trigger and monitor a historical data pull from DataNest (external MySQL) into Raven without touching the server terminal.

> **Who can trigger:** Only users with `role = admin` or `role = super_admin`.
> Any authenticated user can poll job status.

---

### 5a. Trigger a Backfill

```
POST /api/technical/sync/backfill/
```

**Headers:**
```
Authorization: Bearer <token>
Content-Type: application/json
```

**Request body:**
```json
{
  "start_date": "2026-04-01",
  "end_date":   "2026-04-30",
  "hourly": true,
  "energy": true
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `start_date` | string (YYYY-MM-DD) | Yes | Start of the range to backfill |
| `end_date` | string (YYYY-MM-DD) | Yes | End of the range to backfill |
| `hourly` | boolean | No (default `true`) | Sync hourly load readings |
| `energy` | boolean | No (default `true`) | Sync energy (meter) readings |

**Success response — 202 Accepted:**
```json
{
  "job_id": "3f8a2c1d-4b5e-4f6a-8c9d-0e1f2a3b4c5d",
  "status_url": "/api/technical/sync/backfill/3f8a2c1d-4b5e-4f6a-8c9d-0e1f2a3b4c5d/",
  "message": "Backfill queued for 2026-04-01 → 2026-04-30 (hourly=True, energy=True). Poll status_url for progress."
}
```

**Error responses:**

| HTTP | Reason |
|---|---|
| 403 | User is not admin or super_admin |
| 400 | Missing or invalid dates, end before start, range > 366 days, both hourly and energy set to false |

---

### 5b. Poll Job Progress

```
GET /api/technical/sync/backfill/<job_id>/
```

Poll this URL repeatedly (e.g. every 3–5 seconds) until `state` is `SUCCESS` or `FAILURE`.

**While running — response:**
```json
{
  "job_id": "3f8a2c1d-...",
  "state": "PROGRESS",
  "pct": 43,
  "current_range": "2026-04-15 → 2026-04-21",
  "totals": {
    "hourly": { "created": 5200, "updated": 120, "deleted": 0, "errored": 0, "errors": [] },
    "energy": { "created": 210,  "updated": 8,   "deleted": 0, "errored": 0, "errors": [] }
  },
  "result": null,
  "error": null
}
```

**When complete — response:**
```json
{
  "job_id": "3f8a2c1d-...",
  "state": "SUCCESS",
  "pct": 100,
  "current_range": null,
  "totals": {
    "hourly": { "created": 17280, "updated": 340, "deleted": 0, "errored": 0, "errors": [] },
    "energy": { "created": 720,   "updated": 15,  "deleted": 0, "errored": 0, "errors": [] }
  },
  "result": {
    "status": "done",
    "start_date": "2026-04-01",
    "end_date": "2026-04-30",
    "totals": { ... }
  },
  "error": null
}
```

**If failed — response:**
```json
{
  "job_id": "3f8a2c1d-...",
  "state": "FAILURE",
  "pct": 0,
  "error": "Connection refused to external database"
}
```

**All possible `state` values:**

| State | Meaning |
|---|---|
| `PENDING` | Job is queued, not started yet |
| `STARTED` | Job has started |
| `PROGRESS` | Running — `pct` and `current_range` are populated |
| `SUCCESS` | Completed successfully |
| `FAILURE` | Failed — check `error` field |

---

### 5c. Suggested Frontend UX Flow

1. Admin opens a "Backfill Data" panel, picks a date range, clicks **Run Backfill**
2. Frontend calls `POST /api/technical/sync/backfill/` — gets back a `job_id`
3. Frontend starts polling `GET /api/technical/sync/backfill/<job_id>/` every 4 seconds
4. Show a progress bar using `pct` and the `current_range` label
5. When `state === 'SUCCESS'`, show the final totals (created/updated counts)
6. When `state === 'FAILURE'`, show the `error` message

---

## 6. Existing Sync Status Endpoint (unchanged)

This endpoint already existed and has not been changed. It shows whether Raven is in sync with DataNest for the last 7 days.

```
GET /api/technical/sync-status/
```

Returns per-source reconciliation: `hourly_load`, `meter_readings`, `interruptions` — each with `datanest_count`, `raven_count`, `diff`, `pct_synced`, and `status` (`synced` / `partial` / `out_of_sync` / `error`).

---

## Summary of New / Changed Endpoints

| Method | URL | Auth | Description |
|---|---|---|---|
| `POST` | `/api/technical/sync/backfill/` | admin / super_admin | Trigger historical data backfill |
| `GET` | `/api/technical/sync/backfill/<job_id>/` | any authenticated | Poll backfill job progress |
| `GET` | `/api/technical/sync-status/` | any authenticated | Existing — DataNest reconciliation (unchanged) |

---

## Files Changed (for reference)

| File | What changed |
|---|---|
| `reports/pdf_generator.py` | Removed yellow, added SVG chart helpers, upgraded 3 trend renderers, added DSO compliance renderers |
| `reports/services.py` | Added DSO compliance section definitions, `get_dso_compliance_data()`, previous-period trend fetching |
| `reports/models.py` | Added `compliance` category, added `dso_compliance_overview` and `dso_compliance_table` section type choices |
| `technical/sync/hourly_load.py` | `run_sync()` now accepts optional `override_start` / `override_end` date params |
| `technical/sync/meter_readings.py` | Same as above |
| `technical/tasks.py` | Added `backfill_technical_data_task` Celery task |
| `technical/views/sync_backfill.py` | **New file** — trigger and status endpoints |
| `technical/urls.py` | Registered the two new backfill URLs |
| `technical/management/commands/backfill_april_data.py` | **New file** — CLI fallback command (terminal use only) |
