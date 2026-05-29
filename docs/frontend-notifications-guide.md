# Raven Notification System — Frontend Integration Guide

**Target audience:** Frontend engineers (React + TypeScript)
**Base URL prefix:** `/api/notifications/`
**Last updated:** May 2026

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [API Endpoints Reference](#2-api-endpoints-reference)
3. [Notification Shape and Types](#3-notification-shape-and-types)
4. [Polling Strategy — Bell Badge](#4-polling-strategy--bell-badge)
5. [Priority Visual Treatment](#5-priority-visual-treatment)
6. [Category Icons](#6-category-icons)
7. [Notification Panel](#7-notification-panel)
8. [Read / Navigate Behaviour](#8-read--navigate-behaviour)
9. [Toast Notifications](#9-toast-notifications)
10. [Announcement Banner](#10-announcement-banner)
11. [Band Subscriptions](#11-band-subscriptions)
12. [Preferences Page](#12-preferences-page)
13. [DataNest Sync Notifications](#13-datanest-sync-notifications)
14. [TypeScript Types Reference](#14-typescript-types-reference)
15. [Complete Context/Hook Implementation](#15-complete-contexthook-implementation)

---

## 1. Architecture Overview

The notification system is pull-based. There are no WebSockets. The frontend polls a lightweight count endpoint every 30 seconds to keep the bell badge accurate, and fetches the full notification list only when the user opens the notification panel.

```
App lifecycle
├── On mount → fetch /announcements/ → show banner for active ones
├── On mount → start 30-second polling → GET /unread-count/ → update bell badge
├── User opens notification panel
│   └── GET /?limit=20&offset=0 → render list
├── User clicks a notification
│   ├── POST /<id>/read/
│   └── navigate to action_url (if present)
├── User clicks "Mark all read"
│   ├── POST /mark-all-read/
│   └── reset badge to 0
└── Notification panel closes → badge reflects real count again
```

**Backend responsibilities (never replicate on the frontend):**
- Role-based fan-out: users only receive notifications relevant to their role
- Preference checking: if a user disabled a category, no record is created for them
- Email queuing: handled server-side; the frontend does not trigger emails

---

## 2. API Endpoints Reference

All endpoints require `Authorization: Bearer <token>`.

### Notifications

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/notifications/` | Paginated list |
| `GET` | `/api/notifications/unread-count/` | Badge count only |
| `GET` | `/api/notifications/<id>/` | Single notification detail |
| `POST` | `/api/notifications/<id>/read/` | Mark one as read |
| `POST` | `/api/notifications/mark-all-read/` | Mark all as read |
| `DELETE` | `/api/notifications/<id>/` | Delete a notification |

### Preferences

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/notifications/preferences/` | Get current user's preferences |
| `PATCH` | `/api/notifications/preferences/` | Update preferences |

### Announcements

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/notifications/announcements/` | List active announcements for current user |

### Band Subscriptions

| Method | URL | Description |
|---|---|---|
| `GET` | `/api/notifications/band-subscriptions/` | List user's active subscriptions |
| `POST` | `/api/notifications/band-subscriptions/` | Subscribe to a feeder |
| `PATCH` | `/api/notifications/band-subscriptions/<id>/` | Update subscription settings |
| `DELETE` | `/api/notifications/band-subscriptions/<id>/` | Unsubscribe (sets `is_active=false`) |

### Report Sharing

| Method | URL | Description |
|---|---|---|
| `POST` | `/api/notifications/reports/share/` | Share a report with users |
| `GET` | `/api/notifications/reports/received/` | Reports shared with me |
| `GET` | `/api/notifications/reports/sent/` | Reports I have shared |

### GET /api/notifications/ — Query Parameters

```
?unread_only=true     filter to unread only
?category=technical   filter by category slug
?type=band_alert      filter by notification type
?limit=20             page size (default 20, max 100)
?offset=0             pagination offset
```

**Response shape:**
```json
{
  "count": 47,
  "unread_count": 5,
  "limit": 20,
  "offset": 0,
  "results": [ ...notifications ]
}
```

### GET /api/notifications/unread-count/

```json
{ "unread_count": 5 }
```

Note: The list endpoint also returns `unread_count` so you do not need two calls when opening the panel.

### POST /api/notifications/mark-all-read/

Optionally scope to a category:
```json
{ "category": "technical" }
```

Response: `{ "marked_read": 12 }`

---

## 3. Notification Shape and Types

```typescript
interface Notification {
  id: number;
  notification_type: 'action' | 'report' | 'announcement' | 'band_alert';
  notification_type_display: string;
  category: 'technical' | 'commercial' | 'financial' | 'hr' | 'analytics' | 'system' | 'report';
  category_display: string;
  priority: 'low' | 'medium' | 'high' | 'urgent';
  priority_display: string;
  title: string;
  message: string;
  action_url: string;        // empty string if no navigation target
  metadata: Record<string, unknown>;  // arbitrary extra data
  is_read: boolean;
  read_at: string | null;    // ISO 8601 or null
  sender_name: string;       // 'System' if no sender user
  created_at: string;        // ISO 8601
}
```

### Notification Types

| `notification_type` | When it appears | Toast? |
|---|---|---|
| `action` | Something happened in the system requiring attention | Depends on priority |
| `report` | Report generated or shared with user | Yes — high priority always |
| `announcement` | Admin broadcast to all or specific roles | Depends on priority |
| `band_alert` | Feeder band changed (subscriber only) | Yes — high priority |

### Categories

| `category` | Meaning |
|---|---|
| `technical` | Power supply, feeders, interruptions |
| `commercial` | Billing, collections, customer accounts |
| `financial` | Revenue, payments, financial metrics |
| `hr` | Staffing, HR events |
| `analytics` | Automated analytics events |
| `system` | DataNest sync, system health |
| `report` | Report generated or shared |

---

## 4. Polling Strategy — Bell Badge

Poll `GET /api/notifications/unread-count/` every 30 seconds. Do not poll the full list — it is heavier and not necessary unless the panel is open.

```typescript
// src/hooks/useNotificationBadge.ts
import { useState, useEffect, useRef, useCallback } from 'react';

const POLL_INTERVAL_MS = 30_000;

export function useNotificationBadge() {
  const [unreadCount, setUnreadCount] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchCount = useCallback(async () => {
    try {
      const res = await fetch('/api/notifications/unread-count/', {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
      });
      if (!res.ok) return;
      const data = await res.json();
      setUnreadCount(data.unread_count ?? 0);
    } catch {
      // Network error — fail silently, badge stays at last known value
    }
  }, []);

  useEffect(() => {
    fetchCount(); // Immediate fetch on mount

    intervalRef.current = setInterval(fetchCount, POLL_INTERVAL_MS);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchCount]);

  const resetCount = useCallback(() => setUnreadCount(0), []);
  const decrementCount = useCallback((by = 1) => {
    setUnreadCount(prev => Math.max(0, prev - by));
  }, []);

  return { unreadCount, resetCount, decrementCount, refetch: fetchCount };
}
```

**Bell badge component:**
```tsx
function NotificationBell({
  unreadCount,
  onClick,
}: {
  unreadCount: number;
  onClick: () => void;
}) {
  return (
    <button className="notification-bell" onClick={onClick} aria-label="Notifications">
      <BellIcon />
      {unreadCount > 0 && (
        <span className="badge" aria-label={`${unreadCount} unread notifications`}>
          {unreadCount > 99 ? '99+' : unreadCount}
        </span>
      )}
    </button>
  );
}
```

---

## 5. Priority Visual Treatment

Apply visual treatment based on the `priority` field. Never show toasts for `medium` or `low` — use silent badge updates only.

```typescript
type Priority = 'low' | 'medium' | 'high' | 'urgent';

const PRIORITY_CONFIG: Record<Priority, {
  color: string;
  bgColor: string;
  borderColor: string;
  showToast: boolean;
  toastDuration: number;   // ms
}> = {
  urgent: {
    color: '#ffffff',
    bgColor: '#dc2626',    // red-600
    borderColor: '#b91c1c',
    showToast: true,
    toastDuration: 0,      // 0 = stays until dismissed
  },
  high: {
    color: '#ffffff',
    bgColor: '#ea580c',    // orange-600
    borderColor: '#c2410c',
    showToast: true,
    toastDuration: 8_000,  // 8 seconds
  },
  medium: {
    color: '#92400e',
    bgColor: '#fef3c7',    // amber-100
    borderColor: '#f59e0b',
    showToast: false,
    toastDuration: 0,
  },
  low: {
    color: '#374151',
    bgColor: '#f3f4f6',    // gray-100
    borderColor: '#d1d5db',
    showToast: false,
    toastDuration: 0,
  },
};

function shouldShowToast(priority: Priority): boolean {
  return PRIORITY_CONFIG[priority].showToast;
}
```

**Priority badge in the notification list:**
```tsx
function PriorityBadge({ priority }: { priority: Priority }) {
  const config = PRIORITY_CONFIG[priority];
  return (
    <span
      className="priority-badge"
      style={{
        color: config.color,
        backgroundColor: config.bgColor,
        border: `1px solid ${config.borderColor}`,
      }}
    >
      {priority.toUpperCase()}
    </span>
  );
}
```

---

## 6. Category Icons

Use distinct icons per category. Below are recommended mappings using Heroicons names — swap for your icon library of choice.

```typescript
import {
  BoltIcon,
  ChartBarIcon,
  CurrencyDollarIcon,
  UsersIcon,
  ChartPieIcon,
  CogIcon,
  DocumentTextIcon,
} from '@heroicons/react/24/outline';

type Category = 'technical' | 'commercial' | 'financial' | 'hr' | 'analytics' | 'system' | 'report';

const CATEGORY_ICON: Record<Category, React.ComponentType<{ className?: string }>> = {
  technical:  BoltIcon,
  commercial: ChartBarIcon,
  financial:  CurrencyDollarIcon,
  hr:         UsersIcon,
  analytics:  ChartPieIcon,
  system:     CogIcon,
  report:     DocumentTextIcon,
};

const CATEGORY_COLOR: Record<Category, string> = {
  technical:  '#2563eb',  // blue-600
  commercial: '#16a34a',  // green-600
  financial:  '#7c3aed',  // violet-600
  hr:         '#db2777',  // pink-600
  analytics:  '#0891b2',  // cyan-600
  system:     '#6b7280',  // gray-500
  report:     '#b45309',  // amber-700
};

function CategoryIcon({
  category,
  className,
}: {
  category: Category;
  className?: string;
}) {
  const Icon = CATEGORY_ICON[category] ?? CogIcon;
  const color = CATEGORY_COLOR[category] ?? '#6b7280';
  return <Icon className={className} style={{ color }} />;
}
```

---

## 7. Notification Panel

The panel fetches the full notification list when opened. Use offset-based pagination — append results on scroll or provide a "Load more" button.

```tsx
// src/components/notifications/NotificationPanel.tsx
import { useState, useEffect, useCallback } from 'react';
import type { Notification } from '../../types/notifications';

const PAGE_SIZE = 20;

interface PanelState {
  notifications: Notification[];
  unreadCount: number;
  total: number;
  offset: number;
  loading: boolean;
  loadingMore: boolean;
}

function useNotificationPanel(isOpen: boolean) {
  const [state, setState] = useState<PanelState>({
    notifications: [],
    unreadCount: 0,
    total: 0,
    offset: 0,
    loading: false,
    loadingMore: false,
  });

  const fetchPage = useCallback(async (offset: number, append = false) => {
    setState(prev => ({ ...prev, loading: !append, loadingMore: append }));

    try {
      const res = await fetch(
        `/api/notifications/?limit=${PAGE_SIZE}&offset=${offset}`,
        { headers: { Authorization: `Bearer ${getAccessToken()}` } }
      );
      const data = await res.json();

      setState(prev => ({
        ...prev,
        notifications: append
          ? [...prev.notifications, ...data.results]
          : data.results,
        unreadCount: data.unread_count,
        total: data.count,
        offset: offset + data.results.length,
        loading: false,
        loadingMore: false,
      }));
    } catch {
      setState(prev => ({ ...prev, loading: false, loadingMore: false }));
    }
  }, []);

  // Fetch when panel opens
  useEffect(() => {
    if (isOpen) fetchPage(0);
  }, [isOpen, fetchPage]);

  const loadMore = useCallback(() => {
    if (state.notifications.length < state.total) {
      fetchPage(state.offset, true);
    }
  }, [state, fetchPage]);

  const markRead = useCallback(async (id: number) => {
    await fetch(`/api/notifications/${id}/read/`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    setState(prev => ({
      ...prev,
      notifications: prev.notifications.map(n =>
        n.id === id ? { ...n, is_read: true } : n
      ),
      unreadCount: Math.max(0, prev.unreadCount - 1),
    }));
  }, []);

  const markAllRead = useCallback(async () => {
    await fetch('/api/notifications/mark-all-read/', {
      method: 'POST',
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    setState(prev => ({
      ...prev,
      notifications: prev.notifications.map(n => ({ ...n, is_read: true })),
      unreadCount: 0,
    }));
  }, []);

  return { ...state, loadMore, markRead, markAllRead };
}
```

**Panel UI:**
```tsx
function NotificationPanel({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { notifications, unreadCount, total, loading, loadingMore, loadMore, markRead, markAllRead } =
    useNotificationPanel(isOpen);
  const navigate = useNavigate();

  if (!isOpen) return null;

  async function handleClick(notification: Notification) {
    if (!notification.is_read) {
      await markRead(notification.id);
    }
    if (notification.action_url) {
      navigate(notification.action_url);
      onClose();
    }
  }

  return (
    <div className="notification-panel" role="dialog" aria-label="Notifications">
      <div className="panel-header">
        <h2>Notifications</h2>
        <div className="panel-header-actions">
          {unreadCount > 0 && (
            <button onClick={markAllRead} className="btn-text">
              Mark all read
            </button>
          )}
          <button onClick={onClose} aria-label="Close">
            <XIcon />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="panel-loading">
          <Spinner />
        </div>
      ) : notifications.length === 0 ? (
        <div className="panel-empty">
          <BellSlashIcon />
          <p>No notifications yet</p>
        </div>
      ) : (
        <>
          <ul className="notification-list">
            {notifications.map(notification => (
              <NotificationItem
                key={notification.id}
                notification={notification}
                onClick={() => handleClick(notification)}
              />
            ))}
          </ul>

          {notifications.length < total && (
            <button
              className="load-more-btn"
              onClick={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? 'Loading...' : `Load more (${total - notifications.length} remaining)`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function NotificationItem({
  notification,
  onClick,
}: {
  notification: Notification;
  onClick: () => void;
}) {
  return (
    <li
      className={`notification-item ${!notification.is_read ? 'unread' : ''}`}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={e => e.key === 'Enter' && onClick()}
    >
      <div className="notification-icon">
        <CategoryIcon category={notification.category as Category} className="w-5 h-5" />
      </div>
      <div className="notification-body">
        <div className="notification-header">
          <span className="notification-title">{notification.title}</span>
          <PriorityBadge priority={notification.priority as Priority} />
        </div>
        <p className="notification-message">{notification.message}</p>
        <div className="notification-meta">
          <span className="notification-time">
            {formatRelativeTime(notification.created_at)}
          </span>
          <span className="notification-sender">{notification.sender_name}</span>
        </div>
      </div>
      {!notification.is_read && <span className="unread-dot" aria-hidden />}
    </li>
  );
}
```

---

## 8. Read / Navigate Behaviour

Clicking a notification must do two things atomically from the user's perspective:

1. Call `POST /api/notifications/<id>/read/` (fire-and-forget is fine — do not await before navigating)
2. Navigate to `action_url` if it is a non-empty string

```typescript
async function handleNotificationClick(notification: Notification, navigate: NavigateFunction) {
  // Fire read request — do not block navigation on this
  if (!notification.is_read) {
    fetch(`/api/notifications/${notification.id}/read/`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    }).catch(() => {/* fail silently */});
  }

  if (notification.action_url) {
    navigate(notification.action_url);
  }
}
```

The `action_url` values returned by the backend are frontend routes (e.g., `/technical/interruptions`, `/reports/performance/abc123`). They are not absolute URLs. Use your router's `navigate()` function, not `window.location.href`.

---

## 9. Toast Notifications

Show toasts only for `urgent` and `high` priority notifications. `medium` and `low` update the badge silently.

The current implementation is poll-based (no WebSockets), so new high-priority notifications will only appear in the next 30-second poll cycle. If real-time toasts are required, the team should consider adding a WebSocket channel or Server-Sent Events endpoint. For the current architecture, checking for new high-priority notifications after each poll is the correct approach.

```typescript
// src/hooks/useNotificationPollingWithToasts.ts
import { useRef, useCallback } from 'react';
import { toast } from 'your-toast-library';   // e.g. react-hot-toast, sonner

export function useNotificationPollingWithToasts() {
  const lastSeenIdRef = useRef<number>(0);

  const checkForNewHighPriority = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/notifications/?unread_only=true&limit=10`,
        { headers: { Authorization: `Bearer ${getAccessToken()}` } }
      );
      const data = await res.json();
      const notifications: Notification[] = data.results ?? [];

      // Process notifications newer than last seen
      const newNotifications = notifications.filter(
        n => n.id > lastSeenIdRef.current
      );

      if (newNotifications.length > 0) {
        // Update last seen to the highest ID
        lastSeenIdRef.current = Math.max(...newNotifications.map(n => n.id));

        // Show toasts for high/urgent only
        newNotifications
          .filter(n => n.priority === 'urgent' || n.priority === 'high')
          .forEach(n => showPriorityToast(n));
      }

      return data.unread_count ?? 0;
    } catch {
      return null;
    }
  }, []);

  return { checkForNewHighPriority };
}

function showPriorityToast(notification: Notification) {
  const config = PRIORITY_CONFIG[notification.priority as Priority];
  const duration = config.toastDuration === 0 ? Infinity : config.toastDuration;

  toast.custom(
    (t) => (
      <div
        className={`toast-notification priority-${notification.priority}`}
        style={{ borderLeft: `4px solid ${config.borderColor}` }}
      >
        <CategoryIcon category={notification.category as Category} className="toast-icon" />
        <div className="toast-body">
          <strong>{notification.title}</strong>
          <p>{notification.message}</p>
        </div>
        <button onClick={() => toast.dismiss(t.id)} aria-label="Dismiss">
          <XIcon />
        </button>
      </div>
    ),
    { duration }
  );
}
```

**Integrate with the 30-second poll:**
```typescript
const { checkForNewHighPriority } = useNotificationPollingWithToasts();
const { unreadCount } = useNotificationBadge();

useEffect(() => {
  const interval = setInterval(async () => {
    await checkForNewHighPriority();
  }, 30_000);
  return () => clearInterval(interval);
}, [checkForNewHighPriority]);
```

---

## 10. Announcement Banner

Fetch active announcements on app load and show a dismissible top banner. Users should be able to dismiss individual announcements for the current session.

```typescript
// src/hooks/useAnnouncements.ts
import { useState, useEffect } from 'react';

interface Announcement {
  id: number;
  title: string;
  message: string;
  announcement_type: 'feature' | 'bugfix' | 'maintenance' | 'general';
  announcement_type_display: string;
  created_at: string;
  is_active: boolean;
}

export function useAnnouncements() {
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  useEffect(() => {
    fetch('/api/notifications/announcements/', {
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    })
      .then(r => r.json())
      .then(data => setAnnouncements(data.results ?? []))
      .catch(() => {/* fail silently */});
  }, []);

  const dismiss = (id: number) => {
    setDismissed(prev => new Set([...prev, id]));
  };

  const visible = announcements.filter(a => !dismissed.has(a.id));

  return { announcements: visible, dismiss };
}
```

```tsx
const ANNOUNCEMENT_TYPE_STYLES: Record<string, { bg: string; border: string; icon: string }> = {
  feature:     { bg: '#eff6ff', border: '#3b82f6', icon: '✨' },
  bugfix:      { bg: '#f0fdf4', border: '#22c55e', icon: '🐛' },
  maintenance: { bg: '#fff7ed', border: '#f97316', icon: '🔧' },
  general:     { bg: '#f9fafb', border: '#6b7280', icon: '📢' },
};

function AnnouncementBanner({ announcement, onDismiss }: {
  announcement: Announcement;
  onDismiss: () => void;
}) {
  const style = ANNOUNCEMENT_TYPE_STYLES[announcement.announcement_type] ??
    ANNOUNCEMENT_TYPE_STYLES.general;

  return (
    <div
      className="announcement-banner"
      role="banner"
      style={{ backgroundColor: style.bg, borderBottom: `3px solid ${style.border}` }}
    >
      <span className="announcement-icon">{style.icon}</span>
      <div className="announcement-content">
        <strong>{announcement.title}</strong>
        <span> — {announcement.message}</span>
      </div>
      <button
        className="announcement-dismiss"
        onClick={onDismiss}
        aria-label="Dismiss announcement"
      >
        <XIcon />
      </button>
    </div>
  );
}

// In your app layout
function AppAnnouncementBanners() {
  const { announcements, dismiss } = useAnnouncements();

  return (
    <div className="announcement-banners-container">
      {announcements.map(a => (
        <AnnouncementBanner key={a.id} announcement={a} onDismiss={() => dismiss(a.id)} />
      ))}
    </div>
  );
}
```

---

## 11. Band Subscriptions

Users can subscribe to individual feeders to receive `band_alert` notifications when that feeder's band changes (e.g., Band A → Band B).

### 11.1 Fetching Subscriptions

```typescript
async function fetchBandSubscriptions(): Promise<BandSubscription[]> {
  const res = await fetch('/api/notifications/band-subscriptions/', {
    headers: { Authorization: `Bearer ${getAccessToken()}` },
  });
  const data = await res.json();
  return data;  // Array of BandSubscription objects
}
```

### 11.2 Subscribing to a Feeder

```typescript
interface BandSubscriptionCreatePayload {
  feeder_id: number;
  feeder_name: string;
  notify_in_app?: boolean;   // default true
  notify_email?: boolean;    // default false
}

async function subscribeFeeeder(payload: BandSubscriptionCreatePayload): Promise<BandSubscription> {
  const res = await fetch('/api/notifications/band-subscriptions/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getAccessToken()}`,
    },
    body: JSON.stringify(payload),
  });

  if (res.status === 400) {
    const errors = await res.json();
    // Duplicate subscription returns: {"non_field_errors": ["You are already subscribed to this feeder."]}
    throw new Error(Object.values(errors).flat().join(', '));
  }

  return res.json();
}
```

### 11.3 Subscription UI Component

```tsx
interface FeederOption {
  id: number;
  name: string;
}

function BandSubscriptionManager() {
  const [subscriptions, setSubscriptions] = useState<BandSubscription[]>([]);
  const [feeders, setFeeders] = useState<FeederOption[]>([]);
  const [selectedFeederId, setSelectedFeederId] = useState<number | null>(null);
  const [notifyEmail, setNotifyEmail] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Load existing subscriptions
    fetchBandSubscriptions().then(setSubscriptions);

    // Load available feeders from filter options
    fetch('/api/reports/filters/options/', {
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    })
      .then(r => r.json())
      .then(data => setFeeders(data.feeders ?? []));
  }, []);

  const alreadySubscribed = new Set(subscriptions.map(s => s.feeder_id));

  async function handleSubscribe() {
    if (!selectedFeederId) return;
    const feeder = feeders.find(f => f.id === selectedFeederId);
    if (!feeder) return;

    try {
      const sub = await subscribeFeeeder({
        feeder_id: feeder.id,
        feeder_name: feeder.name,
        notify_in_app: true,
        notify_email: notifyEmail,
      });
      setSubscriptions(prev => [...prev, sub]);
      setSelectedFeederId(null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to subscribe');
    }
  }

  async function handleUnsubscribe(id: number) {
    await fetch(`/api/notifications/band-subscriptions/${id}/`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    setSubscriptions(prev => prev.filter(s => s.id !== id));
  }

  async function handleToggleEmail(sub: BandSubscription) {
    const res = await fetch(`/api/notifications/band-subscriptions/${sub.id}/`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${getAccessToken()}`,
      },
      body: JSON.stringify({ notify_email: !sub.notify_email }),
    });
    const updated = await res.json();
    setSubscriptions(prev => prev.map(s => s.id === sub.id ? updated : s));
  }

  return (
    <div className="band-subscription-manager">
      <h3>Feeder Band Change Alerts</h3>

      {/* Add new subscription */}
      <div className="subscribe-form">
        <select
          value={selectedFeederId ?? ''}
          onChange={e => setSelectedFeederId(Number(e.target.value))}
        >
          <option value="">Select a feeder...</option>
          {feeders
            .filter(f => !alreadySubscribed.has(f.id))
            .map(f => (
              <option key={f.id} value={f.id}>{f.name}</option>
            ))
          }
        </select>
        <label>
          <input
            type="checkbox"
            checked={notifyEmail}
            onChange={e => setNotifyEmail(e.target.checked)}
          />
          Also notify via email
        </label>
        <button onClick={handleSubscribe} disabled={!selectedFeederId}>
          Subscribe
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {/* Existing subscriptions */}
      <ul className="subscription-list">
        {subscriptions.map(sub => (
          <li key={sub.id} className="subscription-item">
            <BoltIcon className="w-4 h-4" />
            <span>{sub.feeder_name}</span>
            <label>
              <input
                type="checkbox"
                checked={sub.notify_email}
                onChange={() => handleToggleEmail(sub)}
              />
              Email
            </label>
            <button onClick={() => handleUnsubscribe(sub.id)} className="btn-danger-sm">
              Unsubscribe
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

---

## 12. Preferences Page

The preferences endpoint returns toggles for `in_app` and `email` per category, plus a master `email_enabled` switch.

### 12.1 Preference Shape

```typescript
interface NotificationPreferences {
  // Per-category toggles
  commercial_in_app: boolean;
  commercial_email: boolean;
  financial_in_app: boolean;
  financial_email: boolean;
  technical_in_app: boolean;
  technical_email: boolean;
  hr_in_app: boolean;
  hr_email: boolean;
  analytics_in_app: boolean;
  analytics_email: boolean;
  report_in_app: boolean;
  report_email: boolean;
  announcement_in_app: boolean;
  announcement_email: boolean;
  band_alert_in_app: boolean;
  band_alert_email: boolean;
  // Master switch
  email_enabled: boolean;
}
```

### 12.2 Preferences Page Component

```tsx
const PREFERENCE_CATEGORIES = [
  { key: 'technical',    label: 'Technical',    description: 'Feeder outages, interruptions, restoration events' },
  { key: 'commercial',   label: 'Commercial',   description: 'Billing alerts, collection events, customer updates' },
  { key: 'financial',    label: 'Financial',    description: 'Revenue reports, payment alerts' },
  { key: 'hr',           label: 'HR',           description: 'Staffing events, HR notifications' },
  { key: 'analytics',    label: 'Analytics',    description: 'Automated analytics summaries' },
  { key: 'report',       label: 'Reports',      description: 'Report generation and sharing' },
  { key: 'announcement', label: 'Announcements', description: 'System-wide announcements from admins' },
  { key: 'band_alert',   label: 'Band Alerts',  description: 'Feeder band change alerts (requires subscription)' },
] as const;

function NotificationPreferencesPage() {
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch('/api/notifications/preferences/', {
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    })
      .then(r => r.json())
      .then(setPrefs);
  }, []);

  async function handleToggle(field: keyof NotificationPreferences) {
    if (!prefs) return;
    const newValue = !prefs[field];
    const updated = { ...prefs, [field]: newValue };
    setPrefs(updated);

    setSaving(true);
    try {
      await fetch('/api/notifications/preferences/', {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getAccessToken()}`,
        },
        body: JSON.stringify({ [field]: newValue }),
      });
    } catch {
      // Revert on error
      setPrefs(prefs);
    } finally {
      setSaving(false);
    }
  }

  if (!prefs) return <Spinner />;

  return (
    <div className="preferences-page">
      <h2>Notification Preferences</h2>

      {/* Master email switch */}
      <div className="master-switch">
        <div>
          <strong>Email notifications</strong>
          <p>Globally enable or disable all email notifications</p>
        </div>
        <Toggle
          checked={prefs.email_enabled}
          onChange={() => handleToggle('email_enabled')}
          disabled={saving}
        />
      </div>

      <table className="preferences-table">
        <thead>
          <tr>
            <th>Category</th>
            <th>In-app</th>
            <th>Email</th>
          </tr>
        </thead>
        <tbody>
          {PREFERENCE_CATEGORIES.map(({ key, label, description }) => (
            <tr key={key}>
              <td>
                <div className="pref-category">
                  <CategoryIcon category={key as Category} className="w-5 h-5" />
                  <div>
                    <strong>{label}</strong>
                    <p className="pref-description">{description}</p>
                  </div>
                </div>
              </td>
              <td>
                <Toggle
                  checked={prefs[`${key}_in_app` as keyof NotificationPreferences] as boolean}
                  onChange={() => handleToggle(`${key}_in_app` as keyof NotificationPreferences)}
                  disabled={saving}
                />
              </td>
              <td>
                <Toggle
                  checked={prefs[`${key}_email` as keyof NotificationPreferences] as boolean}
                  onChange={() => handleToggle(`${key}_email` as keyof NotificationPreferences)}
                  disabled={saving || !prefs.email_enabled}
                  title={!prefs.email_enabled ? 'Email notifications are globally disabled' : undefined}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {saving && <span className="saving-indicator">Saving...</span>}
    </div>
  );
}
```

---

## 13. DataNest Sync Notifications

DataNest sync events appear as `category: 'system'` notifications. They are targeted at `super_admin` and `admin` roles only — regular users will never see them.

**Priority mapping for sync events:**

| Sync outcome | `priority` | Show toast? |
|---|---|---|
| `error` | `high` | Yes |
| `partial` | `medium` | No |
| `success` (with changes) | `low` | No |

**Behaviour rules:**
- Show sync notifications in the notification panel like any other notification.
- Do not interrupt the user with a toast unless priority is `high` (error) or `urgent`.
- The `metadata` field contains `data_type`, `sync_status`, `records_created`, `records_updated`, and `records_errored` — use these to render a detail view if needed.
- `action_url` for sync notifications is `/technical/sync` — navigate there on click.

**Rendering sync notification metadata:**
```tsx
function SyncNotificationDetail({ notification }: { notification: Notification }) {
  const meta = notification.metadata as {
    data_type?: string;
    sync_status?: string;
    records_created?: number;
    records_updated?: number;
    records_errored?: number;
    error?: string;
  };

  if (notification.category !== 'system' || !meta.sync_status) return null;

  return (
    <div className="sync-detail">
      <dl>
        {meta.records_created !== undefined && (
          <>
            <dt>New records</dt>
            <dd>{meta.records_created}</dd>
          </>
        )}
        {meta.records_updated !== undefined && (
          <>
            <dt>Updated</dt>
            <dd>{meta.records_updated}</dd>
          </>
        )}
        {meta.records_errored !== undefined && meta.records_errored > 0 && (
          <>
            <dt>Errors</dt>
            <dd className="text-red-600">{meta.records_errored}</dd>
          </>
        )}
      </dl>
      {meta.error && (
        <pre className="sync-error-detail">{meta.error}</pre>
      )}
    </div>
  );
}
```

---

## 14. TypeScript Types Reference

```typescript
// src/types/notifications.ts

export type NotificationType = 'action' | 'report' | 'announcement' | 'band_alert';
export type NotificationCategory = 'technical' | 'commercial' | 'financial' | 'hr' | 'analytics' | 'system' | 'report';
export type NotificationPriority = 'low' | 'medium' | 'high' | 'urgent';
export type AnnouncementType = 'feature' | 'bugfix' | 'maintenance' | 'general';

export interface Notification {
  id: number;
  notification_type: NotificationType;
  notification_type_display: string;
  category: NotificationCategory;
  category_display: string;
  priority: NotificationPriority;
  priority_display: string;
  title: string;
  message: string;
  action_url: string;
  metadata: Record<string, unknown>;
  is_read: boolean;
  read_at: string | null;
  sender_name: string;
  created_at: string;
}

export interface NotificationListResponse {
  count: number;
  unread_count: number;
  limit: number;
  offset: number;
  results: Notification[];
}

export interface UnreadCountResponse {
  unread_count: number;
}

export interface MarkAllReadResponse {
  marked_read: number;
}

export interface NotificationPreferences {
  commercial_in_app: boolean;
  commercial_email: boolean;
  financial_in_app: boolean;
  financial_email: boolean;
  technical_in_app: boolean;
  technical_email: boolean;
  hr_in_app: boolean;
  hr_email: boolean;
  analytics_in_app: boolean;
  analytics_email: boolean;
  report_in_app: boolean;
  report_email: boolean;
  announcement_in_app: boolean;
  announcement_email: boolean;
  band_alert_in_app: boolean;
  band_alert_email: boolean;
  email_enabled: boolean;
}

export interface Announcement {
  id: number;
  title: string;
  message: string;
  announcement_type: AnnouncementType;
  announcement_type_display: string;
  target_roles: string[];
  created_by_name: string;
  created_at: string;
  is_active: boolean;
}

export interface AnnouncementListResponse {
  count: number;
  results: Announcement[];
}

export interface BandSubscription {
  id: number;
  feeder_id: number;
  feeder_name: string;
  notify_in_app: boolean;
  notify_email: boolean;
  is_active: boolean;
  created_at: string;
}

export interface ReportRecipient {
  id: number;
  report_type: string;
  report_title: string;
  report_object_id: string;
  sender_name: string;
  recipient_name: string;
  message: string;
  email_status: 'pending' | 'sent' | 'failed';
  email_sent_at: string | null;
  viewed_at: string | null;
  created_at: string;
}
```

---

## 15. Complete Context/Hook Implementation

A single React context that wires everything together — badge polling, panel state, announcements, and toast dispatch.

```tsx
// src/context/NotificationsContext.tsx
import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useRef,
  useCallback,
} from 'react';
import type {
  Notification,
  NotificationPreferences,
  Announcement,
} from '../types/notifications';

const POLL_INTERVAL_MS = 30_000;

interface NotificationsContextValue {
  // Badge
  unreadCount: number;

  // Panel
  panelOpen: boolean;
  openPanel: () => void;
  closePanel: () => void;
  notifications: Notification[];
  totalNotifications: number;
  panelLoading: boolean;
  loadMoreNotifications: () => void;

  // Actions
  markRead: (id: number) => Promise<void>;
  markAllRead: () => Promise<void>;

  // Announcements
  announcements: Announcement[];
  dismissAnnouncement: (id: number) => void;

  // Preferences
  preferences: NotificationPreferences | null;
  updatePreference: (field: keyof NotificationPreferences, value: boolean) => Promise<void>;
}

const NotificationsContext = createContext<NotificationsContextValue | null>(null);

export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  const [unreadCount, setUnreadCount] = useState(0);
  const [panelOpen, setPanelOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [totalNotifications, setTotalNotifications] = useState(0);
  const [panelOffset, setPanelOffset] = useState(0);
  const [panelLoading, setPanelLoading] = useState(false);
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [dismissedAnnouncements, setDismissedAnnouncements] = useState<Set<number>>(new Set());
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);

  const lastSeenIdRef = useRef(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function authHeaders() {
    return { Authorization: `Bearer ${localStorage.getItem('access_token') ?? ''}` };
  }

  // ── Badge polling ─────────────────────────────────────────────────────────

  const pollUnreadCount = useCallback(async () => {
    try {
      const res = await fetch('/api/notifications/unread-count/', {
        headers: authHeaders(),
      });
      if (!res.ok) return;
      const data = await res.json();
      setUnreadCount(data.unread_count ?? 0);

      // Check for new high-priority toasts (non-blocking)
      checkHighPriority();
    } catch { /* fail silently */ }
  }, []);

  async function checkHighPriority() {
    try {
      const res = await fetch('/api/notifications/?unread_only=true&limit=5', {
        headers: authHeaders(),
      });
      const data = await res.json();
      const fresh: Notification[] = (data.results ?? []).filter(
        (n: Notification) => n.id > lastSeenIdRef.current
      );
      if (fresh.length > 0) {
        lastSeenIdRef.current = Math.max(...fresh.map(n => n.id));
        fresh
          .filter(n => n.priority === 'urgent' || n.priority === 'high')
          .forEach(n => showPriorityToast(n));
      }
    } catch { /* fail silently */ }
  }

  useEffect(() => {
    pollUnreadCount();
    pollRef.current = setInterval(pollUnreadCount, POLL_INTERVAL_MS);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [pollUnreadCount]);

  // ── Announcements ─────────────────────────────────────────────────────────

  useEffect(() => {
    fetch('/api/notifications/announcements/', { headers: authHeaders() })
      .then(r => r.json())
      .then(data => setAnnouncements(data.results ?? []))
      .catch(() => {});
  }, []);

  function dismissAnnouncement(id: number) {
    setDismissedAnnouncements(prev => new Set([...prev, id]));
  }

  const visibleAnnouncements = announcements.filter(a => !dismissedAnnouncements.has(a.id));

  // ── Preferences ───────────────────────────────────────────────────────────

  useEffect(() => {
    fetch('/api/notifications/preferences/', { headers: authHeaders() })
      .then(r => r.json())
      .then(setPreferences)
      .catch(() => {});
  }, []);

  async function updatePreference(field: keyof NotificationPreferences, value: boolean) {
    if (!preferences) return;
    const updated = { ...preferences, [field]: value };
    setPreferences(updated);
    await fetch('/api/notifications/preferences/', {
      method: 'PATCH',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: value }),
    }).catch(() => setPreferences(preferences)); // revert on error
  }

  // ── Panel ─────────────────────────────────────────────────────────────────

  const fetchPanelPage = useCallback(async (offset: number, append = false) => {
    setPanelLoading(true);
    try {
      const res = await fetch(`/api/notifications/?limit=20&offset=${offset}`, {
        headers: authHeaders(),
      });
      const data = await res.json();
      setNotifications(prev =>
        append ? [...prev, ...(data.results ?? [])] : (data.results ?? [])
      );
      setTotalNotifications(data.count ?? 0);
      setUnreadCount(data.unread_count ?? 0);
      setPanelOffset(offset + (data.results?.length ?? 0));
    } finally {
      setPanelLoading(false);
    }
  }, []);

  function openPanel() {
    setPanelOpen(true);
    fetchPanelPage(0);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  function loadMoreNotifications() {
    if (notifications.length < totalNotifications) {
      fetchPanelPage(panelOffset, true);
    }
  }

  // ── Mark read ─────────────────────────────────────────────────────────────

  const markRead = useCallback(async (id: number) => {
    await fetch(`/api/notifications/${id}/read/`, {
      method: 'POST',
      headers: authHeaders(),
    }).catch(() => {});
    setNotifications(prev =>
      prev.map(n => n.id === id ? { ...n, is_read: true, read_at: new Date().toISOString() } : n)
    );
    setUnreadCount(prev => Math.max(0, prev - 1));
  }, []);

  const markAllRead = useCallback(async () => {
    await fetch('/api/notifications/mark-all-read/', {
      method: 'POST',
      headers: authHeaders(),
    }).catch(() => {});
    setNotifications(prev => prev.map(n => ({ ...n, is_read: true })));
    setUnreadCount(0);
  }, []);

  return (
    <NotificationsContext.Provider value={{
      unreadCount,
      panelOpen,
      openPanel,
      closePanel,
      notifications,
      totalNotifications,
      panelLoading,
      loadMoreNotifications,
      markRead,
      markAllRead,
      announcements: visibleAnnouncements,
      dismissAnnouncement,
      preferences,
      updatePreference,
    }}>
      {children}
    </NotificationsContext.Provider>
  );
}

export function useNotifications(): NotificationsContextValue {
  const ctx = useContext(NotificationsContext);
  if (!ctx) throw new Error('useNotifications must be used within NotificationsProvider');
  return ctx;
}
```

**Wrap your app:**
```tsx
// src/App.tsx
import { NotificationsProvider } from './context/NotificationsContext';

export default function App() {
  return (
    <NotificationsProvider>
      <AppAnnouncementBanners />
      <AppLayout />
    </NotificationsProvider>
  );
}
```

**Use anywhere:**
```tsx
function AppHeader() {
  const { unreadCount, openPanel } = useNotifications();
  return (
    <header>
      <NotificationBell unreadCount={unreadCount} onClick={openPanel} />
    </header>
  );
}
```

---

## Quick Reference

| Task | How |
|---|---|
| Poll badge count | `GET /api/notifications/unread-count/` every 30s |
| Fetch notification list | `GET /api/notifications/?limit=20&offset=0` |
| Filter unread only | `?unread_only=true` |
| Filter by category | `?category=technical` |
| Mark one read | `POST /api/notifications/<id>/read/` |
| Mark all read | `POST /api/notifications/mark-all-read/` |
| Delete a notification | `DELETE /api/notifications/<id>/` |
| Get/update preferences | `GET` and `PATCH /api/notifications/preferences/` |
| Fetch announcements on app load | `GET /api/notifications/announcements/` |
| Subscribe to feeder band alerts | `POST /api/notifications/band-subscriptions/` |
| Unsubscribe | `DELETE /api/notifications/band-subscriptions/<id>/` |
| Share a report | `POST /api/notifications/reports/share/` |
| Show toast for urgent/high | Check `priority` field after each poll |
| Do not filter by role on frontend | Backend handles fan-out; every record you receive is relevant |
| DataNest sync notifications | `category === 'system'`; toast only if priority `high` or `urgent` |
