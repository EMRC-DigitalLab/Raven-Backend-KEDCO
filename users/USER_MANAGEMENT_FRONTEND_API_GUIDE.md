# User Management — Frontend API Guide

**Base URL:** `https://{your-domain}/api/users/` (identical routes are also mounted at `/api/auth/`)
**Auth:** All endpoints below require `Authorization: Bearer <access_token>` unless noted.

---

## Table of Contents
1. [What changed vs. the old contract](#1-what-changed-vs-the-old-contract)
2. [User Management](#2-user-management)
3. [My Profile (self-service)](#3-my-profile-self-service)
4. [Active Sessions](#4-active-sessions)
5. [Access Control — Role Permission Matrix](#5-access-control--role-permission-matrix)
6. [Auth changes (login/refresh/logout)](#6-auth-changes)
7. [Scope notes](#7-scope-notes)

---

## 1. What changed vs. the old contract

- **`GET /users/` is now paginated and filterable server-side.** It used to
  return a bare array of every user. It now returns
  `{count, next, previous, results: [...]}` and accepts `search`, `role`,
  `is_active`, `department`, `ordering`, `page`, `page_size` query params.
  Update the RTK Query hook to read `.results` and drive search/filter/pagination
  via these params instead of filtering the already-fetched array client-side.
- **Bulk activate/suspend/delete are now single atomic calls**, not N sequential
  PATCH/DELETE requests. See `bulk_status`/`bulk_delete` below.
- **Active Sessions and the Access Control matrix are now real** — both had
  zero backend before this.
- **Profile pictures are new** — `profile_picture` now appears on every user
  object (absolute URL, or `null`), and there's a new self-service `/me/`
  endpoint for a user to edit their own name/phone/picture.

---

## 2. User Management

### List / search / filter users
```
GET /users/
```
| Param | Type | Description |
|---|---|---|
| `search` | string | Matches username, email, first/last name, employee_id |
| `role` | `super_admin\|admin\|manager\|staff\|viewer` | Exact match |
| `is_active` | `true\|false` | Exact match |
| `department` | string | Exact match |
| `ordering` | string | `created_at`, `username`, `last_name` (prefix `-` for descending); default `-created_at` |
| `page` | int | Default `1` |
| `page_size` | int | Default `20`, max `100` |

**Response `200`:**
```json
{
  "count": 42,
  "next": "https://.../api/users/users/?page=2",
  "previous": null,
  "results": [
    {
      "id": 5, "username": "jdoe", "email": "jdoe@kedco.com",
      "first_name": "Jane", "last_name": "Doe", "department": "Commercial",
      "employee_id": "EMP-0042", "role": "manager", "is_active": true,
      "profile_picture": "https://.../media/profile_pictures/jdoe.jpg",
      "created_at": "2026-01-10T09:00:00Z"
    }
  ]
}
```

### Create user
```
POST /users/
```
Body (JSON or multipart — multipart still works but is no longer required, there's no file field on this endpoint besides `profile_picture` if you want to set it at creation time):
```json
{
  "username": "jdoe", "email": "jdoe@kedco.com", "password": "min 8 chars",
  "first_name": "Jane", "last_name": "Doe", "phone_number": "0801...",
  "department": "Commercial", "employee_id": "EMP-0042", "role": "manager"
}
```
`employee_id` is optional — omit or send `""`, both are treated as "not set"
(previously a second blank `employee_id` would 500 on a database uniqueness
error; that's fixed).

**403** if `role: "super_admin"` is requested by anyone who isn't themselves
a `super_admin` — an `admin` cannot mint another `super_admin`.

### Retrieve / update / delete a single user
```
GET/PATCH/DELETE /users/{id}/
```
Same as before, except: **you cannot deactivate (`is_active: false`) or
delete your own account** through this endpoint — `400` with a message if
you try. Use a different admin account for that.

### Bulk activate/suspend
```
PATCH /users/bulk_status/
{ "user_ids": [3, 5, 9], "is_active": false }
```
**Response `200`:**
```json
{ "updated": 2, "errors": 1, "error_details": [{"id": 5, "error": "Cannot change your own active status"}] }
```
Your own id in the list is skipped with an error entry rather than failing the whole batch.

### Bulk delete
```
DELETE /users/bulk_delete/
{ "user_ids": [3, 9] }
```
**Response `200`:** `{ "deleted": 2, "errors": 0 }` — same shape/self-guard as bulk_status.

---

## 3. My Profile (self-service)

Distinct from `GET /current-user/` (which answers "who am I + what can I
access", used for permission gating at app boot). `/me/` is the actual
profile-editing screen.

```
GET   /me/
PATCH /me/
```
**Editable fields:** `first_name`, `last_name`, `phone_number`, `profile_picture`.
Everything else (`username`, `email`, `department`, `employee_id`, `role`)
is returned but read-only here — those stay admin-controlled via `/users/{id}/`.

To upload a picture, PATCH as `multipart/form-data` with a `profile_picture`
file field. `GET`/`PATCH` responses include the current `profile_picture` as
an absolute URL (or `null` if unset).

---

## 4. Active Sessions

Backed by a real `UserSession` model tied to each issued refresh token
(tracks device/IP, survives token refresh as one continuous row). Requires
admin/manager permission (`IsAdminOrManager`).

### List active sessions
```
GET /sessions/
```
| Param | Description |
|---|---|
| `user` | Filter to one user's sessions by id |
| `search` | Matches the session owner's username/email/first/last name |

**Response `200`:** paginated, same envelope shape as `/users/`. Each item:
```json
{
  "id": 12, "user": 5, "username": "jdoe", "full_name": "Jane Doe", "role": "manager",
  "ip_address": "197.210.x.x", "user_agent": "Mozilla/5.0 ...",
  "device_label": "Chrome on Windows",
  "created_at": "2026-08-20T10:00:00Z", "last_seen_at": "2026-08-21T14:22:00Z",
  "is_active": true
}
```

### Force logout one session
```
POST /sessions/{id}/force_logout/
```
Admin-only (`IsAdminOnly` — stricter than list access). Blacklists that
session's refresh token immediately; the next refresh attempt with it
returns `401`.

### Block a user (deactivate account + kill every session)
```
POST /sessions/block_user/
{ "user_id": 5 }
```
Admin-only. Sets the account inactive **and** force-logs-out all of that
user's active sessions in one call — this is what the "Block User" button
should call (distinct from `force_logout`, which only kills one session and
leaves the account active).

---

## 5. Access Control — Role Permission Matrix

This is now a real, functional model — editing it actually changes what
users with that role can see (it's merged into the same permission
resolution used at login, alongside any per-user overrides).

### Read the matrix
```
GET /role-permissions/?role=manager   (role filter optional)
```
Open to any authenticated user. Each row:
```json
{
  "id": 3, "role": "manager", "role_display": "Manager",
  "section": 2, "section_name": "commercial", "section_display_name": "Commercial",
  "permissions": [{"id": 7, "name": "View", "codename": "view_commercial", ...}],
  "is_manager": true, "updated_by": 1, "updated_at": "2026-08-21T10:00:00Z"
}
```

### Save the whole matrix in one call
```
PUT /role-permissions/matrix/
{
  "changes": [
    {"role": "manager", "section": 2, "permission_ids": [7, 8], "is_manager": true},
    {"role": "staff", "section": 2, "permission_ids": [7], "is_manager": false}
  ]
}
```
Admin-only. Upserts each `(role, section)` row atomically. Also available:
standard `POST/PATCH/DELETE /role-permissions/{id}/` for single-row edits.

**Response `200`:** `{ "saved": 2, "errors": 0, "saved_data": [...] }`.

### What a single user actually sees
`GET /current-user/` → `permissions.sections[]` — each section now carries
`"access_type"`: `"role"` (from the matrix, the baseline), `"permanent"`
(a per-user grant added on top — see `/user-access/`, unchanged from before),
or `"temporary"` (time-limited, see `/temporary-access/`, unchanged).
Permissions from all applicable layers are unioned.

---

## 6. Auth changes

No URL or request/response shape changes to `/login/`, `/token/`,
`/token/refresh/`, `/logout/` — but two behavioral fixes:

- **Logout now actually invalidates the refresh token** (previously it
  silently no-opped — the token stayed valid for its full 7-day life).
- **Every login now creates an Active Sessions entry**, and every token
  refresh updates that same entry's `last_seen_at` rather than spawning a
  new one — nothing for the frontend to change here, this happens automatically.

---

## 7. Scope notes

- **Roles are still the fixed 5** (`super_admin/admin/manager/staff/viewer`) —
  there is no "create a custom role" capability. The matrix configures what
  each of those 5 roles can access; it does not let anyone invent a new role
  name. If the UI has a "Create Role" button, it should stay out until/unless
  this becomes a real backend feature.
- **"Role Templates"** (apply a canned permission preset to a role) can stay
  frontend-only — a named preset that just calls `PUT /role-permissions/matrix/`
  with the right `changes` payload. No backend storage was added for these.
