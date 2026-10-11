# cgrep SDR Dashboard — Complete Code & Data Flow Explanation

> This document is a deep, line-by-line walkthrough of the cgrep SDR monitor codebase located at `c:\projects\CGrep-v3`. It covers every major data flow, from the RTL-SDR hardware sweep all the way to the React 3-D UI, plus auth, RBAC, audit logging, activity tracking, chat, AI, and the admin map-annotation system.

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Repository Layout](#2-repository-layout)
3. [Database Layer](#3-database-layer-modelspy)
4. [Data Ingestion Pipeline](#4-data-ingestion-pipeline-sdr_collectorpy--memory_bufferpy)
5. [Backend API Server](#5-backend-api-server-serverpy)
6. [Authentication & RBAC](#6-authentication--rbac-auth_routerpy)
7. [Frontend Foundation](#7-frontend-foundation)
8. [App.jsx — The Main Dashboard](#8-appjsx--the-main-dashboard)
9. [RightPanel.jsx — Chat & AI](#9-rightpaneljsx--chat--ai)
10. [AdminPanel.jsx](#10-adminpaneljsx)
11. [SigintDashboard.jsx](#11-sigintdashboardjsx)
12. [AnalyticsDashboard.jsx](#12-analyticsdashboardjsx)
13. [Auth Pages](#13-auth-pages)
14. [Activity Tracking & Audit](#14-activity-tracking--audit)
15. [Map Annotations Flow](#15-map-annotations-flow)
16. [Complete Endpoint Map](#16-complete-endpoint-map)
17. [How to Run / Build](#17-how-to-run--build)

---

## 1. High-Level Architecture

The system is a **single-tenant RF spectrum monitoring dashboard** for the 88–108 MHz FM band (plus DAB+ 12A at 223.936 MHz). The architecture is:

```
RTL-SDR Hardware
       │
       ▼
sdr_collector.py (Python, runs on laptop with SDR)
       │  FFT sweep → peak detection → PostgreSQL
       ▼
PostgreSQL (models.py schema)
       │
       ▼
server.py (FastAPI + WebSocket)
       │  REST API + /ws/sdr + /ws/chat + /ws/control
       ▼
React SPA (Vite + Tailwind + React Three Fiber)
       │
       ├─ 3D map of Region (App.jsx)
       ├─ SIGINT bars (SigintDashboard.jsx)
       ├─ Analytics (AnalyticsDashboard.jsx)
       ├─ Chat/AI (RightPanel.jsx)
       └─ Admin panel (AdminPanel.jsx)
```

---

## 2. Repository Layout

```
c:\projects\CGrep-v3
├── server.py                 # FastAPI backend, all REST + WebSocket endpoints
├── auth_router.py            # Authentication, passkeys, QR, RBAC, audit logs
├── models.py                 # SQLAlchemy ORM models
├── memory_buffer.py          # In-memory ring buffer for latest SDR spectrum
├── sdr_collector.py          # RTL-SDR sweep collector (runs on SDR laptop)
├── safe_ai_tools.py          # Safe read-only SQL tool for the AI bot
├── ActivityTracker.js        # Frontend singleton activity tracker
├── start.py                  # Launcher entry point
├── admin_logout_all.py       # Admin helper for global logout
├── calculate.py              # FFT / calibration helpers
├── sweep20.py                # Earlier sweep script
├── sdr-dashboard/            # React/Vite frontend
│   ├── src/
│   │   ├── App.jsx           # Main dashboard + 3D map
│   │   ├── AppShell.jsx      # Auth router / login wrapper
│   │   ├── SigintDashboard.jsx
│   │   ├── AnalyticsDashboard.jsx
│   │   ├── RightPanel.jsx    # Chat
│   │   ├── ActivityTracker.js
│   │   ├── auth/
│   │   │   ├── AuthContext.jsx
│   │   │   ├── AdminPanel.jsx
│   │   │   ├── LoginPage.jsx
│   │   │   ├── QRScreen.jsx
│   │   │   ├── MobilePasskeyPrompt.jsx
│   │   │   ├── MobileVerifyPage.jsx
│   │   │   └── ProfileModal.jsx
│   │   ├── i18n/
│   │   │   ├── LanguageContext.jsx
│   │   │   └── translations.json
│   │   └── components/
│   │       └── ui/           # Tailwind/shadcn-style reusable components
│   ├── public/               # Static assets, population places JSON
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── eslint.config.js
└── static/                   # User uploads, avatars
```

---

## 3. Database Layer (`models.py`)

`c:\projects\CGrep-v3\models.py` is the single source of truth for the PostgreSQL schema.

### 3.1 Engine & Session

```python
@c:\projects\CGrep-v3\models.py:1-8
import os
from sqlalchemy import create_engine, Column, Integer, Float, DateTime, String, Boolean, ForeignKey, Text, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import uuid

Base = declarative_base()
```

The engine is created at the bottom of the file:

```python
@c:\projects\CGrep-v3\models.py:286-298
SQLALCHEMY_DATABASE_URL = f"postgresql://postgres:{DB_PASSWORD}@localhost:5432/sdr_db"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)
print("[models] Tables created (if not existing)")
```

The file auto-creates tables on import.

### 3.2 Core Tables

| Model | Purpose | Key Columns |
|-------|---------|-------------|
| `Role` | RBAC roles | `id`, `name`, `priority_level` |
| `Permission` | RBAC permissions | `id`, `name` |
| `User` | Accounts | `id`, `username`, `password_hash`, `is_admin`, `is_active`, `created_at`, `roles` (M2M), `permissions` (M2M) |
| `PasskeyCredential` | WebAuthn credentials | `id`, `user_id`, `credential_id`, `public_key`, `sign_count`, `device_name`, `last_login_*` |
| `AuthChallenge` | WebAuthn challenge nonce | `id`, `user_id`, `challenge`, `expires_at` |
| `PendingQRSession` | QR login session | `id`, `user_id`, `pin`, `next_token`, `expires_at`, `used` |
| `DeviceSession` | Per-browser session metadata | `id`, `user_id`, `session_id`, `first_seen`, `last_seen`, `ip_address`, `user_agent`, `request_count`, `device`, `browser`, `os`, `is_mobile`, `is_tablet`, `model`, `arch`, `raw` |
| `SweepData` | RF sweep records | `id`, `timestamp`, `frequency_mhz`, `power_dbfs`, `snr_db` |
| `SignalEvent` | Anomaly / interference events | `id`, `timestamp`, `frequency_mhz`, `power_dbfs`, `bandwidth_hz`, `duration_sec`, `confidence`, `event_type` |
| `StationOutage` | Aggregated outage windows | `id`, `frequency_mhz`, `start_time`, `end_time`, `severity`, `drop_count` |
| `ChatMessage` | Chat + AI messages | `id`, `room`, `user_id`, `sender_type`, `content`, `timestamp`, `file_url`, `file_type`, `file_name`, `file_size` |
| `MapAnnotation` | Admin map dots | `id`, `created_by`, `lon`, `lat`, `label`, `color`, `radius`, `created_at` |
| `ActivityLog` | Request/UI/WS events | `id`, `timestamp`, `user_id`, `session_id`, `category`, `event_type`, `method`, `path`, `status_code`, `duration_ms`, `ip_address`, `user_agent`, `payload` |
| `AuditLog` | Security audit | `id`, `timestamp`, `actor_id`, `action`, `resource_type`, `ip_address`, `user_agent`, `details` |

The `User` model is declared with the `Role` and `Permission` many-to-many helper tables:

```python
@c:\projects\CGrep-v3\models.py:120-180
user_roles = Table(...)
user_permissions = Table(...)
class User(Base):
    roles = relationship('Role', secondary=user_roles, back_populates='users')
    permissions = relationship('Permission', secondary=user_permissions, back_populates='users')
```

`is_admin` bypasses all RBAC checks.

---

## 4. Data Ingestion Pipeline (`sdr_collector.py` & `memory_buffer.py`)

### 4.1 `sdr_collector.py`

This script runs on the machine with the RTL-SDR dongle. It sweeps 87.5–108 MHz, performs FFT peak detection, and inserts rows into PostgreSQL.

**Configuration resolution (env → YAML → defaults):**

```python
@c:\projects\CGrep-v3\sdr_collector.py:102-121
```

**Sweep settings:**

```python
@c:\projects\CGrep-v3\sdr_collector.py:158-172
SWEEP_START_HZ = 87.5e6
SWEEP_STOP_HZ  = 108.0e6
SWEEP_STEP_HZ  = 2.4e6
SWEEP_FS       = 2.4e6
SWEEP_FFT_SIZE = 2048
```

**Main loop:**

1. `_open_sdr()` initializes `pyrtlsdr.RtlSdr`, sets sample rate, gain, AGC off.
2. `_sweep_once()` tunes the SDR across 2.4 MHz chunks, reads `2048*8` samples, computes FFT with a Blackman-Harris window, crops edges, and notches the DC spike.
3. `_find_peaks_advanced()` runs for each known station (DAB+ 12A + 17 FM stations), finds the peak in a ±10-bin window, computes SNR, bandwidth, and confidence z-score.
4. Detections are bulk-inserted into `SweepData` and high-confidence events into `SignalEvent`.

```python
@c:\projects\CGrep-v3\sdr_collector.py:464-577
```

### 4.2 `memory_buffer.py`

The dashboard backend needs real-time data without hammering PostgreSQL every WebSocket frame. `SDRMemoryBuffer` is a thread-safe ring buffer.

```python
@c:\projects\CGrep-v3\memory_buffer.py:16-43
class SDRMemoryBuffer:
    def __init__(self, window_seconds=60):
        self.buffer = deque(maxlen=window_seconds * 4)  # 4 Hz
        self.latest_spectrum = None
```

- `_sync_from_db()` runs every 5 seconds in a background thread.
- It uses a recursive CTE to grab the latest row per 100 kHz bucket plus the latest DAB+ row.
- `_process_sweep_data()` builds the exact JSON format consumed by `/ws/sdr`:
  - `peak_intensity`
  - `panoramic_peaks` (array of `{freq, dbfs, ts}`)
  - `station_scan` (array keyed by station index)
  - `is_interference`
  - `playing_idx`, `log_running`, `log_path`

```python
@c:\projects\CGrep-v3\memory_buffer.py:53-167
```

---

## 5. Backend API Server (`server.py`)

`c:\projects\CGrep-v3\server.py` is the FastAPI application. It wires all REST endpoints, WebSockets, middleware, and background tasks.

### 5.1 Bootstrap & middleware

- `app = FastAPI()`
- `app.add_middleware(...)` for security headers, CORS, and the activity-tracking middleware.
- `start_sdr_buffer()` is called at startup to begin the background memory sync.
- Static files are served from `sdr-dashboard/dist` for the SPA.

### 5.2 Security headers

The middleware adds `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, and a strict CSP.

### 5.3 Activity middleware

Every HTTP request is logged to `ActivityLog` except `/api/activity`, `/ws/sdr`, `/assets/*`, and `/favicon`. It records method, path, status, duration, user ID, session ID, IP, user agent, and a small payload.

```python
@c:\projects\CGrep-v3\server.py:1-200
```

### 5.4 SDR WebSocket `/ws/sdr`

This is the primary telemetry pipe to the dashboard.

```python
@c:\projects\CGrep-v3\server.py:600-800
```

On connection:
1. `get_current_user()` validates the JWT cookie.
2. If unauthenticated, the socket closes with code `4003`.
3. The server sends the latest spectrum from `_sdr_buffer.get_latest_spectrum()` immediately.
4. A background `asyncio` task polls every 250 ms and sends the buffer again if the spectrum changed.
5. On disconnect, the task is cancelled.

The message format is:

```json
{
  "peak_intensity": 0.75,
  "is_interference": false,
  "panoramic_peaks": [{"freq": 88.7, "dbfs": -55.2, "ts": 1710000000}],
  "station_scan": {"0": {"pwr": -55.2, "snr": 12.3, "ts": 1710000000}, ...},
  "playing_idx": null,
  "audio_quality": "good",
  "audio_state": "idle",
  "log_running": false,
  "log_path": ""
}
```

### 5.5 REST data endpoints

| Endpoint | Purpose | Notes |
|----------|---------|-------|
| `GET /api/history` | Last 100 sweep rows | `MobileHistoryTab` polls every 4s |
| `GET /api/export` | CSV export of sweep history | `from_ts` / `to_ts` query params |
| `GET /api/telemetry` | Server status: CPU, RAM, DB size, backup status, R2 config |
| `POST /api/backup/run` | Triggers a `pg_dump` + optional R2 upload |
| `GET /api/spectrum/latest` | Raw FFT data for analytics canvas |

### 5.6 Chat endpoints

- `GET /api/chat/history?room=...&limit=...`
- `POST /api/chat/send` — saves a message, broadcasts over `/ws/chat`.
- `GET /api/chat/users` — returns all users with online status.
- `POST /api/chat/upload` — accepts file uploads, saves to `static/chat_uploads/`, and sends a structured message.
- `DELETE /api/chat/clear?room=...` — clears all messages in a room.

### 5.7 AI chat endpoint `/api/chat/ai`

This is the GrepBot AI flow.

```python
@c:\projects\CGrep-v3\server.py:1600-1736
```

1. Saves the user message to `ChatMessage` with `sender_type='user'`.
2. Broadcasts it to the user's system room via WebSocket.
3. If no `GEMINI_API_KEY` is configured, returns an offline message.
4. Otherwise, it calls Google Gemini with a `query_rf_sweep_data` tool declaration.
5. The tool is implemented in `safe_ai_tools.py` and runs a read-only SQL query against the sweep tables.
6. The AI response is saved as `sender_type='ai'` and broadcast back.
7. Returns the AI message payload.

### 5.8 Admin endpoints

- `POST /api/admin/broadcast` — sends a system message to `global` or `system` rooms.
- `GET /api/admin/session-info`
- `POST /api/admin/logout-all`
- `GET /api/admin/activity-logs` — requires `view:audit_logs`.

These are mostly delegated to `auth_router.py` for RBAC and audit logging.

### 5.9 User profile

```python
@c:\projects\CGrep-v3\server.py:2000-2055
POST /api/user/profile
```

Updates `display_name` and optionally saves a base64 avatar to `static/avatars/{uuid}.jpg`.

### 5.10 Analytics endpoints

- `GET /api/analytics/outages?mode=24h|7d|14d&threshold=-65` — counts signal drops per station per time bucket.
- `GET /api/analytics/anomalies` — RF intent classifier.
- `GET /api/analytics/waterfall` — returns a 24-hour spectral occupancy matrix.

### 5.11 Map annotation endpoints

```python
@c:\projects\CGrep-v3\server.py:2056-2119
```

- `GET /api/map-annotations` — authenticated read.
- `POST /api/map-annotations` — admin-only; replaces the entire table with the posted list.
- `DELETE /api/map-annotations` — admin-only; clears all annotations.

### 5.12 SPA fallback

```python
@c:\projects\CGrep-v3\server.py:2493-2509
```

Any unknown path that is not `/api/*` returns `index.html` so the React router can handle it.

---

## 6. Authentication & RBAC (`auth_router.py`)

`c:\projects\CGrep-v3\auth_router.py` is mounted into the FastAPI app with a prefix.

### 6.1 JWT session

- JWT is stored in an **HttpOnly cookie** named `dri_session`.
- It contains `user_id`, `username`, `permissions`, `session_issued_at`, `global_invalidated_at`.
- `get_current_user()` decodes the cookie, validates `session_issued_at` against the global invalidation timestamp, checks `is_active`, and loads the user.

```python
@c:\projects\CGrep-v3\auth_router.py:1-150
```

### 6.2 Password flow

1. `POST /api/auth/verify-password` — checks `bcrypt` hash, rate-limits attempts.
2. If the user has no passkey, returns `needs_registration` + a `nextStepToken`.
3. If the user has passkeys, returns `needs_passkey` + a `nextStepToken`.

### 6.3 WebAuthn

- `POST /api/webauthn/register/generate` — returns `publicKey` options.
- `POST /api/webauthn/register/verify` — verifies the attestation and stores the credential.
- `POST /api/webauthn/authenticate/generate` — returns challenge options.
- `POST /api/webauthn/authenticate/verify` — verifies the assertion and issues the session cookie.

### 6.4 QR / mobile login

- `POST /api/qr/generate` — desktop creates a QR session with a 6-digit PIN.
- `GET /api/qr/status/{session_id}` and `/api/qr/poll/{session_id}` — desktop polling.
- `POST /api/qr/mobile-verify` — mobile scans the QR, enters the PIN, performs WebAuthn, and this endpoint verifies the PIN + biometric response.

### 6.5 RBAC

- `get_user_permissions(db, user_id)` returns a flat set of permission names from the user's roles + direct permissions; `is_admin` gets every permission.
- `require_permission('permission:name')` is a FastAPI dependency factory that returns `403` if the user lacks the permission.
- `manage:rbac`, `manage:users`, `view:audit_logs`, etc. are used throughout the admin endpoints.

```python
@c:\projects\CGrep-v3\auth_router.py:845-880
```

### 6.6 Audit logging

`log_audit_event()` writes a row to `AuditLog` with IP, user agent, action, resource type, and JSON details. It is called via `BackgroundTasks`.

```python
@c:\projects\CGrep-v3\auth_router.py:1131-1157
```

---

## 7. Frontend Foundation

### 7.1 Vite configuration

`c:\projects\CGrep-v3\sdr-dashboard\vite.config.js` configures the React plugin, `@tailwindcss/vite`, and an `@/` alias to `src/`. `jsconfig.json` supports clean imports.

### 7.2 Auth context

`c:\projects\CGrep-v3\sdr-dashboard\src\auth\AuthContext.jsx`:

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\auth\AuthContext.jsx:1-55
```

- `API` is `http://127.0.0.1:8000` in Vite dev mode, otherwise same-origin.
- `checkSession()` calls `GET /api/auth/me`.
- `logout()` calls `POST /api/auth/logout`.
- `hasPermission(perm)` returns true for admins or if `permissions` includes `perm`.

### 7.3 Internationalization

`c:\projects\CGrep-v3\sdr-dashboard\src\i18n\LanguageContext.jsx`:

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\i18n\LanguageContext.jsx:1-55
```

- Provides `lang`, `toggleLanguage`, and `t(key, params)`.
- `t()` supports string interpolation via `{param}`.
- Translations live in `c:\projects\CGrep-v3\sdr-dashboard\src\i18n\translations.json` for English and Regioni.

```json
@c:\projects\CGrep-v3\sdr-dashboard\src\i18n\translations.json:1-200
```

### 7.4 Activity tracking

`c:\projects\CGrep-v3\sdr-dashboard\src\ActivityTracker.js`:

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\ActivityTracker.js:1-111
```

- Singleton `ActivityTracker`.
- Session ID stored in `sessionStorage`.
- Batches UI events (max 20) and flushes every 5 seconds to `POST /api/activity`.
- Uses `navigator.sendBeacon` on `beforeunload`.
- Tracks `PAGE_LOAD`, `JS_ERROR`, and `UNHANDLED_PROMISE`.

### 7.5 App shell

`c:\projects\CGrep-v3\sdr-dashboard\src\AppShell.jsx`:

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\AppShell.jsx:1-95
```

- Wraps everything in `AuthProvider`.
- Shows `<LoadingScreen />` while session is loading.
- If `window.location.pathname` matches `/verify-qr/:sessionId`, renders `<MobileVerifyPage />`.
- Otherwise, logged-out users see `LoginPage`; logged-in users see `App`.
- `RequirePagePermission` gates pages by permission.

---

## 8. `App.jsx` — The Main Dashboard

`c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx` is the largest component (3050 lines). It renders the 3D Region map, the top bar, the side panels, the chat, analytics, admin, and the map annotation painter.

### 8.1 Imports and design tokens

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:1-30
```

- `tracker.start()` is called at module load so activity tracking begins immediately.
- Design tokens: `CYAN`, `AMBER`, `ORANGE`, `PURPLE`, `SLATE`, `VOID`, plus fonts.
- A global `<style>` block is injected for tactical glassmorphism, keyframes, and scrollbars.

### 8.2 Data constants

- `ANTENNA_HUBS` — 11 transmission cities with UV coordinates on the map.
- `RADIO_STATIONS` — 20 Regioni stations, must match `STATIONS` in `SigintDashboard.jsx`.
- `HUB_GEO` — lat/lon for weather lookups.

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:113-176
```

### 8.3 3D scene components

- `Canvas` from `@react-three/fiber` with `frameloop="demand"`, low-power GPU preference.
- `FixedCamera`, `WaveSurface`, `MainPeakModel`, `HubMarkers`, `AntennaModel`.
- Custom GLSL vertex shader for the terrain wave, driven by `uPeakIntensity`, `uHoverPosition`, `uCustomPeakPositions`, etc.

### 8.4 State management

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:2376-2449
```

Key state:
- `activeMainTab` — `'home' | 'dashboard' | 'analytics' | 'admin'`.
- `leftPanelTab`, `leftPanelExpanded`, `rightPanelExpanded`.
- `analyticsTab`, `adminTab`.
- `panoramicPeaks` — the real SDR data.
- `customPeaks` — manually placed antenna city markers.
- `annotationDots`, `annotationScale`, `showMapPainter`, `dotsReady`.
- `isInterferenceDetected`, `wsConnected`.
- `playingIdx`, `logRunning`, `logMode`, `logPath`.

### 8.5 WebSocket connection

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:2456-2552
```

- Opens `/ws/sdr`.
- Updates `peakIntRef` (mutable ref, no re-render) for the shader.
- Updates state only when values change (stringify compare for `panoramic_peaks`).
- Reconnects with exponential backoff; auth errors (1006/4001/4003) back off to 30 seconds.

### 8.6 Radio control functions

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:2554-2591
```

- `tuneStation(idx)` → `POST /radio/tune`.
- `stopStation()` → `POST /radio/untune`.
- `toggleLog()` → `POST /radio/log` with `{action: 'start'|'stop'}`.
- `toggleLogMode(mode)` → `POST /radio/log-mode`.

### 8.7 Rendering tree

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:2797-3047
```

- Full-screen `<Canvas>` background.
- Overlays: `<PopulationDots />`, `<MapParticles />`, `<MapPainter />`.
- UI layer with pointer-events none:
  - `<TopBar />`
  - `<AdminPanel />` when `activeMainTab === 'admin'`.
  - `<StationPanel />` on `home`.
  - `<RightPanel />` on `home`.
  - `<SigintDashboard />` when `activeMainTab === 'dashboard'`.
  - Secret control panel for editing `customPeaks`.
  - Disconnect overlay.
- `<AnalyticsDashboard />` when `showAnalytics`.
- `<ProfileModal />` when `showProfile`.

### 8.8 Mobile behavior

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:2788-2795
```

If `isMobile` is true, only the `SigintDashboard` is rendered, with `forceMobile`.

---

## 9. `RightPanel.jsx` — Chat & AI

`c:\projects\CGrep-v3\sdr-dashboard\src\RightPanel.jsx` implements the Discord-style collapsible chat rail.

### 9.1 Rooms

- `SYSTEM_ROOM = 'system'` — the AI bot room.
- `GLOBAL_ROOM = 'global'` — public chat.
- DM rooms: `dm_{smaller_id}_{larger_id}`.

### 9.2 State

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\RightPanel.jsx:530-570
```

- `globalMsgs`, `systemMsgs`, `dmMsgs`.
- `onlineUsers`, `allUsers`.
- `aiLoading`, `unread`.

### 9.3 Message flow

1. On mount, it fetches:
   - `GET /api/chat/history?room=global&limit=100`
   - `GET /api/chat/history?room=system_{user.id}&limit=100`
   - `GET /api/chat/users`
2. Opens `/ws/chat`.
3. On incoming `message`, calls `addMessage()` which updates the correct room state.
4. On incoming `presence`, updates online status.

### 9.4 Sending

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\RightPanel.jsx:634-694
```

- If `activeRoom === SYSTEM_ROOM` → `POST /api/chat/ai`.
- If DM → `POST /api/chat/send` with `{content, room}`.
- Else global → `POST /api/chat/send` with `{content, room: 'global'}`.
- Optimistic messages are inserted immediately and replaced with the server response.

### 9.5 File uploads

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\RightPanel.jsx:732-755
```

- `POST /api/chat/upload` as `FormData`.
- Supports images, video, generic files.
- Renders images/lightbox, video, or download cards.

### 9.6 UI

- Collapsed rail: 90 px wide, shows circular tiles.
- Expanded rail: 200 px wide, shows labels and previews.
- Chat area is 400 px wide, revealed on hover.
- Header changes based on selected room (AI bot, global chat, or DM user).
- System messages are rendered as centered amber pills.

---

## 10. `AdminPanel.jsx`

`c:\projects\CGrep-v3\sdr-dashboard\src\auth\AdminPanel.jsx` contains 9 tabs.

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\auth\AdminPanel.jsx:28-40
```

Tabs:
1. Session
2. Users
3. Role Matrix
4. Audit Logs
5. Activity
6. WhatsApp
7. Backup
8. Broadcast
9. Map Dots

### 10.1 Session tab

- Fetches `/api/admin/session-info`.
- Shows current user, session start, expiry, TTL, last global logout.
- "Force logout everyone" calls `POST /api/admin/logout-all` and logs out locally.

### 10.2 Users tab

- Fetches `/api/admin/users` and `/api/admin/roles`.
- Lists users with roles and active status.
- Clicking a user expands details: browser sessions, login history, role assignment, role removal.
- Calls `/api/admin/roles/assign` and `/api/admin/roles/remove`.

### 10.3 Role Matrix tab

- Loads roles and permissions.
- Displays a matrix where each row is a permission and each column is a role.
- Clicking a cell toggles the permission in local state; "Save" sends `PUT /api/admin/roles/{role_id}/permissions`.
- Priority rules prevent users from editing roles above their own priority.
- Can create new roles and permissions.

### 10.4 Audit Logs tab

- Fetches `/api/admin/audit-logs` with filters: action, actor_id, start/end date.
- Renders color-coded action badges.
- "JSON" button opens `JsonModal` with the event details.

### 10.5 Activity tab

- Fetches `/api/admin/activity-logs` with filters: category, start/end date.
- Columns: timestamp, user, category badge, status, event type, IP, details.
- Category colors: `API` cyan, `UI` green, `WS` amber, `ERROR` red, `NAV` purple.

### 10.6 WhatsApp tab

- Calls `/whatsapp/status` and `/whatsapp/toggle`.
- Shows/toggles WhatsApp alert status.

### 10.7 Backup tab

- Fetches `/api/telemetry`.
- Calls `POST /api/backup/run`.

### 10.8 Broadcast tab

- Sends `POST /api/admin/broadcast` with message and target room (`global` or `system`).
- Danger zone: `DELETE /api/chat/clear?room=global`.

### 10.9 Map Dots tab

- Toggles `showMapPainter` in `App.jsx`, revealing the map painter toolbar.

---

## 11. `SigintDashboard.jsx`

`c:\projects\CGrep-v3\sdr-dashboard\src\SigintDashboard.jsx` is the SIGINT panel: a tactical LED bar chart for the 18 monitored stations.

### 11.1 Design tokens

- `DBFS_MIN = -70`, `DBFS_MAX = -15`.
- Color interpolation from orange (weak) → purple → cyan (strong).
- Special `pctDAB()` for DAB+ 12A.

### 11.2 Stations

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\SigintDashboard.jsx:134-153
```

18 stations: DAB+ 12A at 88.7, then FM 90.0 through 107.7.

### 11.3 LED columns

`LEDColumn` uses `requestAnimationFrame` for smooth bar/peak animation without React re-renders. It builds a CSS gradient of segments and clips the lit portion.

### 11.4 Mobile history tab

`MobileHistoryTab` polls `/api/history` every 4 seconds, highlights new rows, and provides a CSV export modal that calls `/api/export`.

### 11.5 Real vs simulated data

- If `peaks` (from `/ws/sdr`) are present, the bars reflect real data.
- Otherwise, a 500 ms simulation loop drifts random values.
- Fake alarm button triggers `POST /fakealarm` to test alert UI.

---

## 12. `AnalyticsDashboard.jsx`

`c:\projects\CGrep-v3\sdr-dashboard\src\AnalyticsDashboard.jsx` contains three analytics views.

### 12.1 Outage Dashboard

- Fetches `/api/analytics/outages?mode=...&threshold=-65`.
- Caches results client-side for 5 minutes.
- Shows horizontal bar charts per station with time buckets.

### 12.2 Anomaly Dashboard

- Fetches `/api/analytics/anomalies` every 30 seconds.
- Fetches `/api/analytics/waterfall` every 5 seconds.
- Renders KPI cards for licensed, interference, and jamming classifications.
- Uses `useSpectrumWS()` to poll `/api/spectrum/latest` every second.

### 12.3 Spectrum canvases

- `NeonSpectrumCanvas` — 30 fps WebGL-style canvas line chart with glow layers.
- `WaterfallCanvas` — pixel-scroll waterfall using an offscreen canvas.

---

## 13. Auth Pages

### 13.1 `LoginPage.jsx`

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\auth\LoginPage.jsx:74-134
```

- Username/password form.
- Detects mobile device by user agent / touch points.
- Calls `POST /api/auth/verify-password`.
- On desktop, calls `POST /api/qr/generate` and shows `QRScreen`.
- On mobile, transitions to `MobilePasskeyPrompt`.

### 13.2 `QRScreen.jsx`

- Displays the QR code with the session URL `/verify-qr/{sessionId}`.
- Polls `/api/qr/status/{sessionId}`.
- Shows a 6-digit PIN and a countdown bar.

### 13.3 `MobilePasskeyPrompt.jsx`

- Displays the PIN from the server.
- After PIN verification, runs WebAuthn registration or authentication via `@simplewebauthn/browser`.
- Calls `POST /api/qr/mobile-verify`.

### 13.4 `MobileVerifyPage.jsx`

- Used when a mobile browser opens `/verify-qr/{sessionId}`.
- Shows PIN entry, then the WebAuthn prompt, then calls `/api/qr/mobile-verify`.

### 13.5 `ProfileModal.jsx`

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\auth\ProfileModal.jsx:19-111
```

- Resizes and compresses avatars to 1024×1024 JPEG, base64 payload.
- Calls `POST /api/user/profile`.
- Updates `AuthContext.user`.

---

## 14. Activity Tracking & Audit

### 14.1 Backend

- `ActivityMiddleware` in `server.py` logs every request to `ActivityLog`.
- `POST /api/activity` accepts batch UI events.
- `GET /api/admin/activity-logs` requires `view:audit_logs`.

### 14.2 Frontend

- `ActivityTracker.js` is imported in `App.jsx` and started at module load.
- Events instrumented:
  - `EXPORT_OPEN`, `EXPORT_DOWNLOAD` in `SigintDashboard.jsx`.
  - `BACKUP_CLICK`.
  - `ADMIN_PANEL_OPEN`, `ADMIN_PANEL_CLOSE`.
  - `ADMIN_TAB_SWITCH` in `AdminPanel.jsx`.

### 14.3 Audit

- `auth_router.py` writes `AuditLog` rows for logins, role changes, backups, logout-all.
- Exposed via `GET /api/admin/audit-logs`.

---

## 15. Map Annotations Flow

Map annotations are the newest feature. Admin users place glowing dots on the 3D map; they are persisted in `MapAnnotation` and visible to all users.

### 15.1 Backend

```python
@c:\projects\CGrep-v3\server.py:2056-2119
```

- `GET /api/map-annotations` — returns all dots.
- `POST /api/map-annotations` — admin-only; deletes existing rows and inserts the new list.
- `DELETE /api/map-annotations` — admin-only; clears table.

### 15.2 Frontend

`MapPainter` is defined inside `c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:795-944`.

- On mount, fetches `/api/map-annotations` and calls `onReady()`.
- Maintains `saved` (from DB) and `pending` (new unsaved dots).
- `screenToLonLat()` converts a click to map coordinates using the same camera math as the 3D scene.
- `projectToScreen()` converts DB coordinates back to screen pixels for erase hit-testing.
- Toolbar: Place Dots, Erase, Scale slider, Save, Clear All.
- `AnnotationSprites` renders the dots as Three.js sprites inside the `<Canvas>`.

```javascript
@c:\projects\CGrep-v3\sdr-dashboard\src\App.jsx:795-944
```

---

## 16. Complete Endpoint Map

### Auth

- `POST /api/auth/verify-password`
- `GET /api/auth/me`
- `POST /api/auth/logout`
- `POST /api/admin/logout-all`
- `POST /api/webauthn/register/generate`
- `POST /api/webauthn/register/verify`
- `POST /api/webauthn/authenticate/generate`
- `POST /api/webauthn/authenticate/verify`
- `POST /api/qr/generate`
- `GET /api/qr/status/{session_id}`
- `GET /api/qr/poll/{session_id}`
- `POST /api/qr/mobile-verify`

### RBAC / Admin

- `GET /api/admin/permissions`
- `POST /api/admin/permissions`
- `GET /api/admin/roles`
- `POST /api/admin/roles`
- `PUT /api/admin/roles/{role_id}/permissions`
- `GET /api/admin/users`
- `GET /api/admin/users/{user_id}/logins`
- `GET /api/admin/users/{user_id}/sessions`
- `POST /api/admin/roles/assign`
- `POST /api/admin/roles/remove`
- `GET /api/admin/audit-logs`
- `GET /api/admin/activity-logs`
- `GET /api/admin/session-info`

### Chat / AI

- `GET /api/chat/history`
- `POST /api/chat/send`
- `POST /api/chat/ai`
- `GET /api/chat/users`
- `POST /api/chat/upload`
- `DELETE /api/chat/clear`
- `POST /api/admin/broadcast`

### SDR / Data

- `GET /api/history`
- `GET /api/export`
- `GET /api/telemetry`
- `GET /api/spectrum/latest`
- `POST /api/backup/run`
- `WS /ws/sdr`
- `WS /ws/chat`
- `WS /ws/control`

### Analytics

- `GET /api/analytics/outages`
- `GET /api/analytics/anomalies`
- `GET /api/analytics/waterfall`

### Map Annotations

- `GET /api/map-annotations`
- `POST /api/map-annotations`
- `DELETE /api/map-annotations`

### User

- `POST /api/user/profile`

### Static

- `/assets/*`
- `/population/*`
- `/static/*`
- SPA fallback: `index.html`

---

## 17. How to Run / Build

### Backend

```bash
# Install Python deps
pip install fastapi uvicorn sqlalchemy psycopg2-binary bcrypt pyjwt python-multipart webauthn google-generativeai aiofiles

# Set required env vars
$env:DATABASE_URL="postgresql://user:pass@localhost/sdr_db"
$env:JWT_SECRET="your-secret"
$env:GEMINI_API_KEY="..."  # optional, for AI chat

# Run
python server.py
```

### Frontend

```bash
cd sdr-dashboard
npm install
npm run dev        # Vite dev server on :5173
npm run build      # production build to dist/
```

### SDR Collector

```bash
# On the machine with the RTL-SDR
pip install pyrtlsdr numpy scipy sqlalchemy psycopg2-binary pyyaml
python sdr_collector.py
```

---

## Summary

- **Hardware → DB:** `sdr_collector.py` sweeps FM/DAB and writes `SweepData` + `SignalEvent`.
- **DB → Backend:** `memory_buffer.py` keeps the latest spectrum in memory.
- **Backend → Frontend:** `server.py` serves REST and WebSocket (`/ws/sdr`) data.
- **Frontend:** React app with 3D map (`App.jsx`), SIGINT bars (`SigintDashboard.jsx`), analytics (`AnalyticsDashboard.jsx`), chat (`RightPanel.jsx`), and admin (`AdminPanel.jsx`).
- **Security:** JWT HttpOnly cookies, WebAuthn passkeys, QR/PIN mobile flow, RBAC, audit logging, and request activity tracking.
- **Map annotations:** Admin-only click-to-place dots stored in `MapAnnotation`, rendered as Three.js sprites, accessible via `GET /api/map-annotations`.

Every major flow, file, and endpoint is now documented above.
