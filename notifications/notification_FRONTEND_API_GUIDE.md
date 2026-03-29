# Notifications System — Frontend API Guide

**Base URL:** `https://{your-domain}/api/notifications/`
**Auth:** All endpoints require `Authorization: Bearer <access_token>` header.

---

## Table of Contents
1. [In-App Notifications](#1-in-app-notifications)
2. [Notification Preferences](#2-notification-preferences)
3. [Announcements (Feature / Changelog)](#3-announcements)
4. [Report Distribution](#4-report-distribution)
5. [Band Subscriptions](#5-band-subscriptions)
6. [Data Shapes Reference](#6-data-shapes-reference)
7. [Polling Strategy](#7-polling-strategy)
8. [Role Visibility Rules](#8-role-visibility-rules)

---

## 1. In-App Notifications

### List notifications
```
GET /api/notifications/
```
**Query params:**
| Param | Type | Description |
|-------|------|-------------|
| `unread_only` | `true/false` | Filter to unread only |
| `category` | string | `commercial`, `financial`, `technical`, `hr`, `analytics`, `system`, `report` |
| `type` | string | `action`, `report`, `announcement`, `band_alert` |
| `limit` | int | Page size (default `20`, max `100`) |
| `offset` | int | Pagination offset (default `0`) |

**Response `200`:**
```json
{
  "count": 42,
  "unread_count": 5,
  "limit": 20,
  "offset": 0,
  "results": [ ...Notification objects... ]
}
```

---

### Get unread count only (bell badge)
```
GET /api/notifications/unread-count/
```
Use this for the bell icon poll — it's lightweight, no full payload.

**Response `200`:**
```json
{ "unread_count": 5 }
```

---

### Get single notification
```
GET /api/notifications/<id>/
```
**Response `200`:** Single Notification object.
**Response `404`:** `{ "detail": "Not found." }`

---

### Mark single notification as read
```
POST /api/notifications/<id>/read/
```
No request body needed.

**Response `200`:**
```json
{ "status": "marked as read" }
```

---

### Mark all as read
```
POST /api/notifications/mark-all-read/
```
**Optional body** (JSON) to scope to a category:
```json
{ "category": "commercial" }
```
Omit body to mark everything read.

**Response `200`:**
```json
{ "marked_read": 12 }
```

---

### Delete a notification
```
DELETE /api/notifications/<id>/
```
**Response `204`:** No content.

---

## 2. Notification Preferences

Users control which categories trigger in-app notifications and which also send email.

### Get preferences
```
GET /api/notifications/preferences/
```
**Response `200`:** NotificationPreference object (see [Data Shapes](#6-data-shapes-reference)).

---

### Update preferences
```
PATCH /api/notifications/preferences/
```
Send only the fields you want to change (partial update).

**Example — turn on email for reports, turn off commercial in-app:**
```json
{
  "report_email": true,
  "commercial_in_app": false
}
```

**Available preference fields:**
| Field | Default | Description |
|-------|---------|-------------|
| `commercial_in_app` | `true` | In-app notifications for commercial events |
| `commercial_email` | `false` | Email for commercial events |
| `financial_in_app` | `true` | |
| `financial_email` | `false` | |
| `technical_in_app` | `true` | |
| `technical_email` | `false` | |
| `hr_in_app` | `true` | |
| `hr_email` | `false` | |
| `analytics_in_app` | `true` | |
| `analytics_email` | `false` | |
| `report_in_app` | `true` | |
| `report_email` | `true` | Email on by default for reports |
| `announcement_in_app` | `true` | |
| `announcement_email` | `false` | |
| `band_alert_in_app` | `true` | |
| `band_alert_email` | `false` | |
| `email_enabled` | `true` | **Master switch** — turns off ALL emails globally |

**Response `200`:** Updated NotificationPreference object.

---

## 3. Announcements

System-wide broadcasts from admins (new features, bug fixes, maintenance). These appear as a special card/banner in the UI.

### List announcements visible to current user
```
GET /api/notifications/announcements/
```
Returns only announcements the user's role can see (or global ones).

**Query params:** `limit`, `offset`

**Response `200`:**
```json
{
  "count": 3,
  "results": [ ...Announcement objects... ]
}
```

---

### Create announcement
```
POST /api/notifications/announcements/
```
**Requires role:** `admin` or `super_admin` — returns `403` for others.

**Request body:**
```json
{
  "title": "New Feature: Band Tracking",
  "message": "You can now subscribe to feeder band change alerts from your notification settings.",
  "announcement_type": "feature",
  "target_roles": ["admin", "manager"],
  "is_active": true
}
```

| Field | Required | Values |
|-------|----------|--------|
| `title` | Yes | string |
| `message` | Yes | string |
| `announcement_type` | No | `feature`, `bugfix`, `maintenance`, `general` |
| `target_roles` | No | Array of role strings. **Empty array = broadcast to all users** |
| `is_active` | No | `true` (default) |

> Fan-out to all target users happens automatically on the backend the moment this is created.

**Response `201`:** Announcement object.

---

## 4. Report Distribution

### Share a report with users
```
POST /api/notifications/reports/share/
```
Sends an in-app notification to each recipient AND emails the PDF attachment.

**Request body:**
```json
{
  "report_type": "executive",
  "report_title": "Monthly Executive Overview — January 2026",
  "report_object_id": "123",
  "report_file_path": "/absolute/path/to/report.pdf",
  "recipient_ids": [4, 7, 12],
  "message": "Please review before the Monday meeting."
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `report_type` | Yes | e.g. `executive`, `commercial`, `financial`, `hr` |
| `report_title` | Yes | Human-readable name shown in the notification |
| `report_object_id` | No | ID/slug of the report for deep-linking |
| `report_file_path` | No | Server path to PDF — if provided, attached to the email |
| `recipient_ids` | Yes | Array of user IDs (must be active users) |
| `message` | No | Personal message included in the email |

**Response `201`:**
```json
{
  "shared_with": 3,
  "recipients": [ ...ReportRecipient objects... ]
}
```

---

### Reports received by current user
```
GET /api/notifications/reports/received/
```
Lists all reports shared with the logged-in user. Automatically marks them as viewed.

**Query params:** `limit`, `offset`

**Response `200`:**
```json
{
  "count": 5,
  "results": [ ...ReportRecipient objects... ]
}
```

---

### Reports sent by current user
```
GET /api/notifications/reports/sent/
```
Lets the sender track delivery status for each recipient.

**Response `200`:**
```json
{
  "count": 8,
  "results": [ ...ReportRecipient objects... ]
}
```

---

## 5. Band Subscriptions

Users subscribe to specific feeders and receive an alert when the band changes (e.g. Band A → Band C).

### List current user's subscriptions
```
GET /api/notifications/band-subscriptions/
```
**Response `200`:** Array of BandSubscription objects.

---

### Subscribe to a feeder
```
POST /api/notifications/band-subscriptions/
```
**Request body:**
```json
{
  "feeder_id": 42,
  "feeder_name": "Kano North 11kV Feeder",
  "notify_in_app": true,
  "notify_email": false
}
```

**Response `201`:** BandSubscription object.
**Response `400`:** If already subscribed to that feeder.

---

### Update subscription (toggle email / in-app)
```
PATCH /api/notifications/band-subscriptions/<id>/
```
```json
{ "notify_email": true }
```
**Response `200`:** Updated BandSubscription object.

---

### Unsubscribe
```
DELETE /api/notifications/band-subscriptions/<id>/
```
Soft-delete (sets `is_active = false`).
**Response `204`:** No content.

---

## 6. Data Shapes Reference

### Notification Object
```json
{
  "id": 101,
  "notification_type": "action",
  "notification_type_display": "Action",
  "category": "commercial",
  "category_display": "Commercial",
  "priority": "medium",
  "priority_display": "Medium",
  "title": "New commercial customer added",
  "message": "Customer 'ABC Enterprises' has been added to the system.",
  "action_url": "/commercial/customers",
  "metadata": { "customer_id": 55 },
  "is_read": false,
  "read_at": null,
  "sender_name": "System",
  "created_at": "2026-03-29T10:45:00Z"
}
```

**`notification_type` values:**
| Value | Meaning |
|-------|---------|
| `action` | Something happened in the system |
| `report` | Report generated or shared |
| `announcement` | Feature/changelog broadcast |
| `band_alert` | Feeder band changed |

**`category` values:** `commercial`, `financial`, `technical`, `hr`, `analytics`, `system`, `report`

**`priority` values:** `low`, `medium`, `high`, `urgent`

---

### NotificationPreference Object
```json
{
  "commercial_in_app": true,
  "commercial_email": false,
  "financial_in_app": true,
  "financial_email": false,
  "technical_in_app": true,
  "technical_email": false,
  "hr_in_app": true,
  "hr_email": false,
  "analytics_in_app": true,
  "analytics_email": false,
  "report_in_app": true,
  "report_email": true,
  "announcement_in_app": true,
  "announcement_email": false,
  "band_alert_in_app": true,
  "band_alert_email": false,
  "email_enabled": true
}
```

---

### Announcement Object
```json
{
  "id": 5,
  "title": "Band Tracking is Live",
  "message": "You can now subscribe to feeder band alerts.",
  "announcement_type": "feature",
  "announcement_type_display": "New Feature",
  "target_roles": [],
  "created_by_name": "Abdullahi Musa",
  "created_at": "2026-03-29T09:00:00Z",
  "is_active": true
}
```

---

### ReportRecipient Object
```json
{
  "id": 22,
  "report_type": "executive",
  "report_title": "Monthly Executive Overview — January 2026",
  "report_object_id": "123",
  "sender_name": "Fatima Sule",
  "recipient_name": "Ibrahim Garba",
  "message": "Please review before Monday.",
  "email_status": "sent",
  "email_sent_at": "2026-03-29T11:00:00Z",
  "viewed_at": "2026-03-29T11:30:00Z",
  "created_at": "2026-03-29T10:55:00Z"
}
```

**`email_status` values:** `pending`, `sent`, `failed`

---

### BandSubscription Object
```json
{
  "id": 3,
  "feeder_id": 42,
  "feeder_name": "Kano North 11kV Feeder",
  "notify_in_app": true,
  "notify_email": false,
  "is_active": true,
  "created_at": "2026-03-29T08:00:00Z"
}
```

---

## 7. Polling Strategy

There is no WebSocket — use **interval polling** for the bell badge.

**Recommended approach:**
```
On login        → fetch full notification list
Every 30s       → GET /api/notifications/unread-count/ (lightweight, just the number)
On bell click   → GET /api/notifications/?limit=20 (load the dropdown)
On item click   → POST /api/notifications/<id>/read/ + navigate to action_url
On "clear all"  → POST /api/notifications/mark-all-read/
```

**Do NOT poll the full list every 30 seconds** — use `unread-count/` only. Load the full list on demand when the user opens the notification panel.

---

## 8. Role Visibility Rules

Notifications are created per-recipient on the backend — users **only ever receive their own**. The API never returns another user's notifications. No frontend filtering is needed.

**Who gets notified for each event:**

| Event | Notified Roles |
|-------|----------------|
| New user registered | `super_admin`, `admin` |
| User role changed | `super_admin`, `admin` |
| New commercial customer | `super_admin`, `admin`, `manager` |
| New meter reading | `super_admin`, `admin`, `manager` |
| New NBET invoice | `super_admin`, `admin`, `manager` |
| New MO invoice | `super_admin`, `admin`, `manager` |
| New OPEX entry | `super_admin`, `admin` |
| New staff added | `super_admin`, `admin`, `manager` |
| Salary payment recorded | `super_admin`, `admin` |
| Feeder interruption | `super_admin`, `admin`, `manager` |
| Report generated | The requesting user only |
| Report shared | The explicit recipient(s) only |
| Announcement | Target roles set by admin (or all users) |
| Band alert | Users who subscribed to that feeder only |

---

## Common Errors

| Status | Meaning |
|--------|---------|
| `401` | Missing or expired token |
| `403` | Action not allowed for your role (e.g. creating announcements as `staff`) |
| `404` | Notification not found or belongs to another user |
| `400` | Validation error — check response body for field-level errors |
