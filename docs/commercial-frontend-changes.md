# Commercial Module — Frontend Changes Required

Based on the model and analytics updates made to the KEDCO commercial module (MDI/MDNI),
the following frontend changes are needed across the Raven dashboard.

---

## 1. Billing Split — `actual_billed_kwh` vs `estimated_billed_kwh`

### What changed
The API now returns two separate billing fields instead of a single combined figure.

| Old field | New field | Meaning |
|---|---|---|
| `actual_billed_kwh` (was total) | `actual_billed_kwh` | Only real metered reads — no estimation involved |
| *(did not exist)* | `estimated_billed_kwh` | DataNest-estimated reads + Raven's own projections for unread customers |

The combined total is: `actual_billed_kwh + estimated_billed_kwh`

### Affected pages
Overview, States, Districts, Feeders, Service Bands — both list and detail levels.

### Action required
Wherever billed energy is displayed, show the two values distinctly.
Suggested UI: stacked bar chart or two separate rows labelled **"Actual Billed"** and **"Estimated Billed"**.
The sum can still be shown as a total where needed.

---

## 2. Coverage Rate — Denominator Is Now `readable`, Not `total`

### What changed
Faulty and missing meters are now excluded from the coverage denominator.
The rate now means: *"of customers whose meters can physically be read, how many were read?"*

A new `readable` count is returned in the API — customers excluding those with `meter_status` of `faulty` or `missing`.

### Affected pages
Overview, States, Districts, Feeders, Service Bands — both list and detail levels.

### Action required

- **Coverage rate value** — updates automatically, no field name change needed.
- **`unread_customers` count** — now reflects unread *readable* customers, not all customers.
- **Display label** — change from:
  > "X of Y customers read"

  to:
  > "X of Y readable customers read"

- **Optional** — surface the `readable` count alongside `total` so users understand
  why the denominator may be lower than the total customer count on that feeder/district.

---

## 3. Customer Detail — `meter_status` Field

### What changed
`CommercialCustomer` now has an explicit `meter_status` field with four possible values:

| Value | Meaning |
|---|---|
| `active` | Meter is functioning normally |
| `faulty` | Meter is logged as faulty |
| `missing` | Meter is missing |
| `bypassed` | Customer is bypassing the meter |

`is_bypass` is now **derived** from `meter_status === 'bypassed'` — it is no longer manually toggled.

### Affected pages
Customer detail page.

### Action required

- Display `meter_status` as a status badge on the customer detail page.
- **Remove** any separate bypass toggle UI — bypass is now read-only and reflects meter status.
- Faulty and missing customers should be visually flagged (e.g. warning badge) to communicate
  that they are excluded from coverage analytics.

---

## 4. Meter Reading Detail — Three New Fields

### What changed
`MeterReading` now carries three additional fields:

| Field | Possible values | Meaning |
|---|---|---|
| `submission_status` | `on_time` / `late` / `missed` | Whether the reading was submitted on schedule |
| `fault_source` | `meter` / `access` / `customer` / `null` | Reason a reading was not taken, if applicable |
| `estimation_method` | string or empty (`""`) | Empty = real read; non-empty = how the value was estimated |

### Affected pages
Reading history list, reading detail view.

### Action required

- Show `submission_status` as a badge on each reading row (On Time / Late / Missed).
- Show `fault_source` when present — this explains why a reading is late or missed.
- Where billed kWh is shown, indicate whether it is an actual read or an estimate.
  Use `estimation_method !== ""` as the flag:
  - Empty → **Actual Read**
  - Non-empty → **Estimated** (show the method string as a tooltip or sub-label)

---

## Summary — Pages Affected

| Page | Changes needed |
|---|---|
| Overview dashboard | Billing split labels; coverage denominator label |
| State detail | Billing split labels; coverage denominator label |
| District list + detail | Billing split labels; coverage denominator label |
| Feeder list + detail | Billing split labels; coverage denominator label |
| Service band list + detail | Billing split labels; coverage denominator label |
| Customer detail | Add `meter_status` badge; remove bypass toggle |
| Reading history / detail | Add `submission_status`, `fault_source`, `estimation_method` indicators |

---

---

# Commercial Module — Automated Sync Engine

Documents the DataNest → Raven automated sync built for the commercial module (MDI/MDNI).

---

## Overview

All commercial data is mastered in DataNest and pulled into Raven automatically via Celery.
There are four independent sync tasks, each with its own cadence and strategy.

| Data | Table (DataNest) | Model (Raven) | Strategy | Cadence |
|---|---|---|---|---|
| Meter readings | `meter_readings` | `MeterReading` | Incremental (watermark on `created_at`) | Every **5 minutes** |
| Customers | `customer_information` | `CommercialCustomer` | Incremental (watermark on `updated_at`) | Every **1 hour** at :35 |
| Managers + assignments | `feeder_managers` + `feeder_manager_assignments` | `MeterManager` + `MeterManagerAssignment` | Full refresh | Every **1 hour** at :40 |
| Tariff rates | `tariff_rates` | `TariffRate` | Full refresh (MDI/MDNI only) | Every **1 hour** at :45 |

---

## How Incremental Sync Works

Each incremental sync uses a **watermark** — the `window_end` timestamp of the last successful run,
stored in `DataSyncLog`. The next run pulls only records created/updated after that watermark,
with a look-back buffer to catch late submissions:

- **Readings** — 4-hour look-back. Field officers may submit readings hours after they are taken.
- **Customers** — 2-hour look-back. Covers clock skew between servers.

On first ever run (no watermark), both tasks bootstrap with a 7-day pull.

---

## Reliability Guarantees

| Guarantee | How it is achieved |
|---|---|
| No duplicates | Upsert by `external_id` (unique per table). `(customer, reading_date)` conflict on readings falls back to update. |
| No missed records | Look-back window overlaps with the previous run — every record is seen at least once. |
| Failure recovery | If a run fails, the watermark does not advance. The next run retries from the same point. |
| Full audit trail | Every run writes a `DataSyncLog` row: started, completed, records fetched/created/updated/skipped/errored, error messages. |
| No overlapping runs | Celery task retries on failure (max 3 for readings, max 2 for others) with a delay before retrying. |
| Queue isolation | All commercial tasks run on the `datanest_sync` queue — isolated from `notifications` and `analytics` workers. |

---

## Files

| File | Purpose |
|---|---|
| `commercial/sync/readings.py` | Readings sync service — incremental by `created_at` |
| `commercial/sync/customers.py` | Customers sync service — incremental by `updated_at` |
| `commercial/sync/managers.py` | Managers + assignments sync service — full refresh |
| `commercial/sync/tariff_rates.py` | Tariff rates sync service — full refresh, skips MD2 |
| `commercial/tasks.py` | Four Celery tasks wrapping the sync services |
| `commercial/views/sync_status.py` | `GET /api/commercial/sync-status/` reconciliation endpoint |
| `raven/celery.py` | Beat schedule entries + queue routing |

---

## Sync Status API

**Endpoint:** `GET /api/commercial/sync-status/`
**Auth:** Required

Compares DataNest vs Raven counts for all four data types and returns an overall health status.

### Response shape

```json
{
  "overall_status": "synced",
  "module": "commercial",
  "reconciliation_window_days": 30,
  "window_start": "2026-03-27",
  "window_end": "2026-04-26",
  "last_checked": "2026-04-26T14:00:00Z",
  "sources": {
    "meter_readings": {
      "label": "Meter Readings",
      "status": "synced",
      "datanest_count": 420,
      "raven_count": 420,
      "diff": 0,
      "pct_synced": 100.0,
      "last_sync_at": "2026-04-26T13:55:00Z",
      "last_sync_status": "success",
      "last_sync_created": 12,
      "last_sync_updated": 0,
      "last_sync_errored": 0,
      "last_sync_error": null
    },
    "customers":    { "..." },
    "managers":     { "..." },
    "tariff_rates": { "..." }
  }
}
```

### Status values

| Status | Meaning |
|---|---|
| `synced` | DataNest and Raven counts match exactly |
| `partial` | Difference is within 1% or 5 records — acceptable lag |
| `out_of_sync` | Significant gap — sync may have failed or stalled |
| `error` | Could not reach DataNest or query failed |

---

## Deployment

No manual steps required. The CI/CD pipeline (`deploy-prod.sh`) runs
`docker compose down` → `docker compose up -d` on every merge to `main`.

This restarts all three Celery containers automatically:

- `celery-beat` — picks up the new schedule from `raven/celery.py`
- `celery-sync` — already listens on `datanest_sync` queue, picks up `commercial.tasks.*`
- `celery-worker` — unaffected (handles notifications/analytics only)

Within **5 minutes** of deployment, the first `commercial_readings` sync fires and a
`DataSyncLog` entry appears confirming the engine is live.
