# KEDCO Raven — Backfill API Guide

### For Technical and Commercial Modules

**Base URL (staging):** `https://staging.apiraven.raven-emrc.com/api/`
**Auth:** Bearer token in `Authorization` header
**All responses:** `application/json`

---

## Table of Contents

1. [What is a Backfill?](#1-what-is-a-backfill)
2. [When to Use Each Module](#2-when-to-use-each-module)
3. [Technical Backfill](#3-technical-backfill)
   - [Trigger](#31-trigger--post-apitechnicalsyncbackfill)
   - [Poll Status](#32-poll-status--get-apitechnicalsyncbackfilljob_id)
4. [Commercial Backfill](#4-commercial-backfill)
   - [Trigger](#41-trigger--post-apicommercialsyncbackfill)
   - [Poll Status](#42-poll-status--get-apicommercialsyncbackfilljob_id)
5. [Job States Reference](#5-job-states-reference)
6. [Polling Pattern](#6-polling-pattern)
7. [Error Responses](#7-error-responses)
8. [Common Scenarios](#8-common-scenarios)
9. [Quick Reference](#9-quick-reference)

---

## 1. What is a Backfill?

A backfill pulls historical data from **DataNest** (the source of truth) and writes it into **Raven's** local database for a specified date range.

This is needed when:
- Data was missing or corrupt for a past period and has since been corrected in DataNest.
- New readings were uploaded to DataNest after the nightly sync already ran.
- A computation field (like `billed_consumption`) was null on initial import and needs to be recomputed and stored.
- A feeder is newly onboarded and its historical data needs to be imported retroactively.

Backfills are **asynchronous** — they run as a background Celery task. The trigger endpoint returns a `job_id` immediately; you then poll the status endpoint until the job completes.

---

## 2. When to Use Each Module

| Module | Use for | Data pulled |
|---|---|---|
| **Technical** | Feeder-level energy and hourly load data | `hourly_readings`, `energy_data` tables |
| **Commercial** | Customer meter readings, customer records, managers, tariff rates | `readings`, `customers`, `managers`, `tariff_rates` tables |

Both modules draw from DataNest. Neither overwrites data that hasn't changed — they upsert (insert or update).

---

## 3. Technical Backfill

### 3.1 Trigger — `POST /api/technical/sync/backfill/`

Queues a background job to pull technical (feeder) data from DataNest for the given date range.

#### Permissions
Admin (`admin` or `super_admin` role) **or** any user with an active `technical` section access grant.

#### Request Body (JSON)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `start_date` | string `YYYY-MM-DD` | Yes | — | First day of the range to backfill |
| `end_date` | string `YYYY-MM-DD` | Yes | — | Last day of the range to backfill (inclusive) |
| `hourly` | boolean | No | `true` | Pull hourly load readings |
| `energy` | boolean | No | `true` | Pull daily energy data |

**Constraints:**
- `end_date` must be on or after `start_date`.
- Range cannot exceed **366 days** per request.
- At least one of `hourly` or `energy` must be `true`.

#### Example Request

```json
POST /api/technical/sync/backfill/
Authorization: Bearer <token>
Content-Type: application/json

{
  "start_date": "2026-04-01",
  "end_date":   "2026-04-30",
  "hourly": true,
  "energy": true
}
```

#### Success Response — `202 Accepted`

```json
{
  "job_id":     "3f8a2c1e-4b7d-4e9a-a1f2-0123456789ab",
  "status_url": "/api/technical/sync/backfill/3f8a2c1e-4b7d-4e9a-a1f2-0123456789ab/",
  "message":    "Backfill queued for 2026-04-01 → 2026-04-30 (hourly=True, energy=True). Poll status_url for progress."
}
```

| Field | Description |
|---|---|
| `job_id` | UUID of the Celery task — use this to poll progress |
| `status_url` | The exact URL to GET for job status |
| `message` | Human-readable confirmation |

---

### 3.2 Poll Status — `GET /api/technical/sync/backfill/<job_id>/`

Returns the current state of a queued or running backfill job.

#### Permissions
Any authenticated user.

#### Path Parameter

| Parameter | Description |
|---|---|
| `job_id` | The UUID returned by the trigger endpoint |

#### Response — `200 OK`

```json
{
  "job_id":        "3f8a2c1e-4b7d-4e9a-a1f2-0123456789ab",
  "state":         "PROGRESS",
  "pct":           60,
  "current_range": "2026-04-18 → 2026-04-24",
  "totals": {
    "hourly": { "synced": 1440, "errors": 0 },
    "energy": { "synced": 24,   "errors": 0 }
  },
  "result": null,
  "error":  null
}
```

| Field | Type | Description |
|---|---|---|
| `state` | string | Current job state — see [Job States](#5-job-states-reference) |
| `pct` | integer 0–100 | Percentage of the date range processed so far |
| `current_range` | string or null | The date window currently being processed (only during `PROGRESS`) |
| `totals` | object or null | Running totals of rows synced and errors per data type |
| `result` | object or null | Full summary — only populated when `state == "SUCCESS"` |
| `error` | string or null | Error message — only populated when `state == "FAILURE"` |

#### Example: Job Complete

```json
{
  "job_id": "3f8a2c1e-4b7d-4e9a-a1f2-0123456789ab",
  "state":  "SUCCESS",
  "pct":    100,
  "current_range": null,
  "totals": {
    "hourly": { "synced": 2880, "errors": 0 },
    "energy": { "synced": 60,   "errors": 0 }
  },
  "result": {
    "totals": {
      "hourly": { "synced": 2880, "errors": 0 },
      "energy": { "synced": 60,   "errors": 0 }
    }
  },
  "error": null
}
```

---

## 4. Commercial Backfill

### 4.1 Trigger — `POST /api/commercial/sync/backfill/`

Queues a background job to pull commercial (customer) data from DataNest for the given date range.

#### Permissions
Admin (`admin` or `super_admin` role) **or** any user with an active `commercial` section access grant.

#### Request Body (JSON)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `start_date` | string `YYYY-MM-DD` | Yes | — | First day of the range to backfill (applied to readings) |
| `end_date` | string `YYYY-MM-DD` | Yes | — | Last day of the range to backfill (inclusive) |
| `tables` | array of strings | No | all four | Which data tables to sync — see valid values below |

**Valid `tables` values:**

| Value | What it syncs |
|---|---|
| `readings` | Customer meter readings (consumption, tariff, billed amounts) |
| `customers` | Customer master records (name, account, meter number, address) |
| `managers` | Commercial account managers |
| `tariff_rates` | Tariff rate definitions |

**Constraints:**
- `end_date` must be on or after `start_date`.
- Range cannot exceed **366 days** per request.
- `tables` must contain at least one valid value. Invalid table names are silently dropped; the request fails only if no valid tables remain.
- The date range applies specifically to `readings`. Non-date-scoped tables (`customers`, `managers`, `tariff_rates`) are synced in full regardless of the date range.

#### Example Request — Full Backfill

```json
POST /api/commercial/sync/backfill/
Authorization: Bearer <token>
Content-Type: application/json

{
  "start_date": "2026-04-28",
  "end_date":   "2026-05-21"
}
```

#### Example Request — Readings Only

```json
POST /api/commercial/sync/backfill/
Authorization: Bearer <token>
Content-Type: application/json

{
  "start_date": "2026-05-11",
  "end_date":   "2026-05-11",
  "tables": ["readings"]
}
```

#### Success Response — `202 Accepted`

```json
{
  "job_id":     "7c4b1a9e-2d3f-4e8b-b5a6-9876543210cd",
  "status_url": "/api/commercial/sync/backfill/7c4b1a9e-2d3f-4e8b-b5a6-9876543210cd/",
  "message":    "Commercial backfill queued for 2026-04-28 to 2026-05-21 (tables=['readings', 'customers', 'managers', 'tariff_rates']). Poll status_url for progress."
}
```

| Field | Description |
|---|---|
| `job_id` | UUID of the Celery task — use this to poll progress |
| `status_url` | The exact URL to GET for job status |
| `message` | Human-readable confirmation including which tables were queued |

---

### 4.2 Poll Status — `GET /api/commercial/sync/backfill/<job_id>/`

Returns the current state of a queued or running commercial backfill job.

#### Permissions
Any authenticated user.

#### Path Parameter

| Parameter | Description |
|---|---|
| `job_id` | The UUID returned by the trigger endpoint |

#### Response — `200 OK`

```json
{
  "job_id":        "7c4b1a9e-2d3f-4e8b-b5a6-9876543210cd",
  "state":         "PROGRESS",
  "pct":           25,
  "current_table": "readings",
  "totals": {
    "readings":     { "synced": 162, "errors": 0 },
    "customers":    { "synced": 0,   "errors": 0 },
    "managers":     { "synced": 0,   "errors": 0 },
    "tariff_rates": { "synced": 0,   "errors": 0 }
  },
  "result": null,
  "error":  null
}
```

| Field | Type | Description |
|---|---|---|
| `state` | string | Current job state — see [Job States](#5-job-states-reference) |
| `pct` | integer 0–100 | Percentage of work completed so far |
| `current_table` | string or null | The table currently being synced (only during `PROGRESS`) |
| `totals` | object or null | Running per-table totals of rows synced and errors |
| `result` | object or null | Full summary — only populated when `state == "SUCCESS"` |
| `error` | string or null | Error message — only populated when `state == "FAILURE"` |

#### Example: Job Complete

```json
{
  "job_id": "7c4b1a9e-2d3f-4e8b-b5a6-9876543210cd",
  "state":  "SUCCESS",
  "pct":    100,
  "current_table": null,
  "totals": {
    "readings":     { "synced": 540,  "errors": 0 },
    "customers":    { "synced": 215,  "errors": 0 },
    "managers":     { "synced": 12,   "errors": 0 },
    "tariff_rates": { "synced": 8,    "errors": 0 }
  },
  "result": {
    "totals": {
      "readings":     { "synced": 540,  "errors": 0 },
      "customers":    { "synced": 215,  "errors": 0 },
      "managers":     { "synced": 12,   "errors": 0 },
      "tariff_rates": { "synced": 8,    "errors": 0 }
    }
  },
  "error": null
}
```

---

## 5. Job States Reference

Both modules use the same Celery state machine.

| State | Meaning | `pct` | `current_*` | `result` | `error` |
|---|---|---|---|---|---|
| `PENDING` | Job is queued but hasn't started yet | `0` | `null` | `null` | `null` |
| `PROGRESS` | Job is actively running | `0`–`99` | populated | `null` | `null` |
| `SUCCESS` | Job completed successfully | `100` | `null` | populated | `null` |
| `FAILURE` | Job crashed with an unhandled exception | `0` | `null` | `null` | populated |
| `REVOKED` | Job was cancelled (rare) | `0` | `null` | `null` | `null` |

> **`PENDING` can also mean the `job_id` doesn't exist.** If you pass a random UUID you'll get `PENDING` back — not a 404. Always use the `job_id` returned by the trigger endpoint.

---

## 6. Polling Pattern

The backfill runs asynchronously. Here's the recommended frontend flow:

```
1. POST /api/{module}/sync/backfill/
   → Save job_id and status_url from the 202 response

2. Every 3–5 seconds, GET /api/{module}/sync/backfill/{job_id}/
   → Check state field:
     - "PENDING"  → still waiting in the queue, keep polling
     - "PROGRESS" → show pct progress bar, show current_range / current_table
     - "SUCCESS"  → done — display totals from result
     - "FAILURE"  → show error message to the user
     - "REVOKED"  → inform the user the job was cancelled

3. Stop polling when state is SUCCESS, FAILURE, or REVOKED
```

**Recommended poll interval:** every 4 seconds. Most backfills complete within 1–5 minutes for a 30-day range; longer ranges can take 10–20 minutes.

**Show the user:**
- A progress bar driven by `pct`
- The active label: `current_range` (technical) or `current_table` (commercial)
- Running totals from `totals` so they can see rows accumulating in real time

---

## 7. Error Responses

### Trigger Endpoint (400 / 403)

```json
{ "error": "start_date is required and must be YYYY-MM-DD." }
{ "error": "end_date must be on or after start_date." }
{ "error": "Date range cannot exceed 366 days per request." }
{ "error": "At least one of hourly or energy must be true." }        // technical only
{ "error": "tables must include at least one of: ['customers', 'managers', 'readings', 'tariff_rates']." }  // commercial only
{ "error": "Only admin or super_admin users can trigger a backfill." }  // 403
```

### Status Endpoint

The status endpoint always returns `200` — it never 404s. An unknown `job_id` returns `state: "PENDING"`. A crashed job returns `state: "FAILURE"` with an `error` string describing the exception.

---

## 8. Common Scenarios

### Fix NULL `billed_consumption` on May 11 readings (production priority)

The May 11 batch was synced before the compute fix was deployed, leaving all 162 readings with `billed_consumption = null`. Run a targeted readings-only backfill:

```json
POST /api/commercial/sync/backfill/
{
  "start_date": "2026-05-11",
  "end_date":   "2026-05-11",
  "tables": ["readings"]
}
```

### Full commercial onboarding backfill (new feeder)

When a feeder is first commercially onboarded, pull all data since the onboarding date:

```json
POST /api/commercial/sync/backfill/
{
  "start_date": "2026-04-28",
  "end_date":   "2026-05-21"
}
```

This syncs all four tables: readings from `2026-04-28` onward, plus all current customer, manager, and tariff records.

### Re-sync a missed nightly run (technical)

If the nightly technical sync failed for a few days:

```json
POST /api/technical/sync/backfill/
{
  "start_date": "2026-05-17",
  "end_date":   "2026-05-20"
}
```

### Sync only tariff rates and customer records (no readings)

```json
POST /api/commercial/sync/backfill/
{
  "start_date": "2026-01-01",
  "end_date":   "2026-01-01",
  "tables": ["customers", "tariff_rates"]
}
```

The date range is required by the API but is only used for filtering readings. Since no readings table is in scope here, the dates are irrelevant — just pass any valid range.

### Hourly data only (no energy totals)

```json
POST /api/technical/sync/backfill/
{
  "start_date": "2026-05-01",
  "end_date":   "2026-05-21",
  "hourly": true,
  "energy": false
}
```

---

## 9. Quick Reference

### Technical Backfill

| | |
|---|---|
| **Trigger** | `POST /api/technical/sync/backfill/` |
| **Status** | `GET /api/technical/sync/backfill/<job_id>/` |
| **Auth to trigger** | `admin`, `super_admin`, or active `technical` section access |
| **Auth to poll** | Any authenticated user |
| **Max range** | 366 days |
| **Data synced** | Hourly feeder readings, daily energy data |
| **Progress field** | `current_range` (e.g. `"2026-04-18 → 2026-04-24"`) |
| **Queue** | `datanest_sync` |

### Commercial Backfill

| | |
|---|---|
| **Trigger** | `POST /api/commercial/sync/backfill/` |
| **Status** | `GET /api/commercial/sync/backfill/<job_id>/` |
| **Auth to trigger** | `admin`, `super_admin`, or active `commercial` section access |
| **Auth to poll** | Any authenticated user |
| **Max range** | 366 days |
| **Data synced** | `readings`, `customers`, `managers`, `tariff_rates` |
| **Progress field** | `current_table` (e.g. `"readings"`) |
| **Queue** | `datanest_sync` |
