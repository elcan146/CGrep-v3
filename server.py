import asyncio
import base64 as _b64
import glob
import io
import logging
import logging.handlers
import os
import random
import shutil
import subprocess
import sys
import threading
import traceback
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import psutil
from datetime import datetime as _dt, timedelta

# Import safe AI tools to prevent SQL injection
from safe_ai_tools import get_safe_ai_tools, execute_safe_tool

# Load .env file if present (allows setting keys like GEMINI_API_KEY without terminal)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _, _v = _line.partition('=')
                os.environ.setdefault(_k.strip(), _v.strip())

# Silence the WinError 10054 / ConnectionResetError noise that spams the console
# when a WebSocket client disconnects abruptly on Windows (asyncio ProactorEventLoop bug).
if sys.platform == 'win32':
    import asyncio.proactor_events as _pe
    _orig_conn_lost = _pe._ProactorBasePipeTransport._call_connection_lost
    def _quiet_conn_lost(self, exc):
        try:
            _orig_conn_lost(self, exc)
        except (ConnectionResetError, OSError):
            pass
    _pe._ProactorBasePipeTransport._call_connection_lost = _quiet_conn_lost

# Load config.env into os.environ (simple key=value parser, no dependency needed)
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.env')
if os.path.isfile(_CONFIG_PATH):
    with open(_CONFIG_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _, _v = _line.partition('=')
                os.environ.setdefault(_k.strip(), _v.strip())

import numpy as np
import uvicorn
try:
    import webview
    HAS_WEBVIEW = True
except Exception:
    HAS_WEBVIEW = False
    webview = None
from collections import defaultdict, deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends, BackgroundTasks, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from auth_router import router as auth_router, get_current_user, require_permission, _COOKIE_NAME, _decode_jwt, get_client_ip, log_audit_event
import uuid as _uuid

from models import SessionLocal, SweepData, SignalEvent, StationOutage, User, ActivityLog, DeviceSession, ChatMessage, MapAnnotation, engine
from sqlalchemy import desc, func, text, or_, and_
import uuid as _uuid_mod
import green_api

# ---------------------------------------------------------------------------
# Backup configuration (reads from environment / config.env)
# ---------------------------------------------------------------------------
def _env(key, default=''):
    return os.environ.get(key, default).strip()

# Dedicated thread pool for blocking DB / IO work. Uvicorn shuts down the event
# loop's *default* executor during graceful shutdown; using our own pool keeps
# in-flight requests from failing with "cannot schedule new futures after shutdown".
_THREAD_POOL = ThreadPoolExecutor(
    max_workers=int(_env('THREAD_POOL_WORKERS', '200')),
    thread_name_prefix='sdr-db-',
)

async def _run_in_thread(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_THREAD_POOL, fn, *args)

BASE_DIR             = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR           = os.path.join(BASE_DIR, 'backup')
LOGS_DIR             = os.path.join(BASE_DIR, 'Logs')
os.makedirs(LOGS_DIR, exist_ok=True)

# Rotating file loggers for request tracing and server health.
# Logs are written to C:\projects\CGrep-v3\Logs\access.log and health.log.
_ACCESS_LOG = logging.getLogger('sdr.access')
_ACCESS_LOG.setLevel(logging.INFO)
_ACCESS_LOG.propagate = False
_access_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOGS_DIR, 'access.log'),
    maxBytes=10*1024*1024,
    backupCount=5,
    encoding='utf-8',
)
_access_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
_ACCESS_LOG.addHandler(_access_handler)

_HEALTH_LOG = logging.getLogger('sdr.health')
_HEALTH_LOG.setLevel(logging.INFO)
_HEALTH_LOG.propagate = False
_health_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOGS_DIR, 'health.log'),
    maxBytes=10*1024*1024,
    backupCount=5,
    encoding='utf-8',
)
_health_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
_HEALTH_LOG.addHandler(_health_handler)

BACKUP_INTERVAL_SEC  = int(_env('BACKUP_INTERVAL_HOURS', '3')) * 3600

# ---------------------------------------------------------------------------
# SDR config — loaded from sdr_config.json
# ---------------------------------------------------------------------------
import json as _json

_SDR_CONFIG_PATH = os.path.join(BASE_DIR, 'sdr_config.json')

def _load_sdr_config():
    """Load SDR configuration from sdr_config.json."""
    try:
        with open(_SDR_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return _json.load(f)
    except Exception as e:
        print(f"[WARN] Could not load sdr_config.json: {e}")
        return {"sdrs": []}

_SDR_CONFIG = _load_sdr_config()


def _get_all_sdr_ids(db=None):
    """Return list of SDR IDs from config file."""
    return [s["id"] for s in _SDR_CONFIG.get("sdrs", [])]


def _get_sdr_config(sdr_id):
    """Return config dict for a specific SDR, or None."""
    for s in _SDR_CONFIG.get("sdrs", []):
        if s["id"] == sdr_id:
            return s
    return None


# ── Per-region calibration thresholds ─────────────────────────────────────────
_SDR_THRESHOLDS_PATH = os.path.join(BASE_DIR, 'sdr_thresholds.json')

def _load_sdr_thresholds():
    """Load per-region calibration thresholds from sdr_thresholds.json."""
    try:
        with open(_SDR_THRESHOLDS_PATH, 'r', encoding='utf-8') as f:
            return _json.load(f)
    except Exception as e:
        print(f"[WARN] Could not load sdr_thresholds.json: {e}")
        return {}

_SDR_THRESHOLDS = _load_sdr_thresholds()


def _freq_mhz(val):
    """Return frequency in MHz.

    rf_sweep_data.frequency_mhz is stored as either a kHz integer (e.g. 88700)
    by fm_dab_sweep.py/simulation.py or as a MHz float (e.g. 88.7) by
    sdr_collector.py. Normalize both to MHz so downstream logic groups the
    same physical frequency together.
    """
    if val is None:
        return 0.0
    v = float(val)
    return round(v / 1000.0, 3) if v >= 1000 else round(v, 3)


# ── Per-station RTYS and name maps (from sdr_config.json) ─────────────────────
def _build_station_rtys_map():
    m = {}
    for sdr in _SDR_CONFIG.get('sdrs', []):
        sid = sdr['id']
        for st in sdr.get('stations', []):
            rtys = st.get('rtys')
            if not rtys:
                continue
            freq = _freq_mhz(st.get('freq', 0))
            m[(sid, round(freq, 3))] = rtys
    return m


def _build_station_name_map():
    m = {}
    for sdr in _SDR_CONFIG.get('sdrs', []):
        sid = sdr['id']
        for st in sdr.get('stations', []):
            name = st.get('name')
            if not name:
                continue
            freq = _freq_mhz(st.get('freq', 0))
            m[(sid, round(freq, 3))] = name
    return m

_STATION_RTYS_MAP = _build_station_rtys_map()
_STATION_NAME_MAP = _build_station_name_map()

# Per-SDR "main" RTYS (primary transmitter for each site).
# These match the "rtys" values in sdr_config.json.
_MAIN_RTYS_BY_SDR = {
    "site_1": "BTQK",
    "site_2": "BTQK",
    "site_3": "Şuşa",
    "site_4": "Daşkəsən",
    "site_5": "Lerik",
    "site_6": "Şuşa",
    "site_7": "Yergüc",
    "site_8": "Şuşa",
}


def _get_rtys_for_freq(sdr_id, freq_mhz):
    key = (sdr_id, round(freq_mhz, 3))
    if key in _STATION_RTYS_MAP:
        return _STATION_RTYS_MAP[key]
    # tolerant lookup
    for (sid, f), rtys in _STATION_RTYS_MAP.items():
        if sid == sdr_id and abs(f - freq_mhz) < 0.15:
            return rtys
    return None


def _get_station_name_for_freq(sdr_id, freq_mhz):
    key = (sdr_id, round(freq_mhz, 3))
    if key in _STATION_NAME_MAP:
        return _STATION_NAME_MAP[key]
    for (sid, f), name in _STATION_NAME_MAP.items():
        if sid == sdr_id and abs(f - freq_mhz) < 0.15:
            return name
    return "Unknown"


def _resolve_station(sdr_id, raw_freq_mhz, tolerance=0.15):
    """Return (exact_freq, station_name, rtys) of the closest configured station.
    On a tie, prefer the SDR's main RTYS so the alert does not flip to a
    secondary/remote transmitter.
    """
    best = None
    best_diff = None
    best_is_main = False
    main_rtys = _MAIN_RTYS_BY_SDR.get(sdr_id)
    for sdr in _SDR_CONFIG.get("sdrs", []):
        if sdr.get("id") != sdr_id:
            continue
        for st in sdr.get("stations", []):
            st_freq = _freq_mhz(st.get("freq", 0))
            diff = abs(st_freq - raw_freq_mhz)
            if diff > tolerance:
                continue
            is_main = st.get("rtys") == main_rtys
            replace = False
            if best is None or diff < best_diff - 1e-6:
                replace = True
            elif abs(diff - best_diff) <= 1e-6:
                # same distance: prefer the main RTYS, then the lower exact freq
                if is_main and not best_is_main:
                    replace = True
                elif is_main == best_is_main and st_freq < best[0]:
                    replace = True
            if replace:
                best = (st_freq, st.get("name", "Unknown"), st.get("rtys", "Unknown"))
                best_diff = diff
                best_is_main = is_main
    if best is None:
        return raw_freq_mhz, "Unknown", "Unknown"
    return best


def _get_sdr_label(sdr_id):
    for sdr in _SDR_CONFIG.get('sdrs', []):
        if sdr.get('id') == sdr_id:
            return sdr.get('label') or sdr_id
    return sdr_id


def _get_calibrated_threshold(sdr_id, rtys):
    sdr_th = _SDR_THRESHOLDS.get(sdr_id)
    if not sdr_th:
        return _DROPS_DEFAULT_THRESHOLD
    if rtys:
        rtys_th = sdr_th.get('rtys', {}).get(rtys)
        if rtys_th:
            return rtys_th.get('signal_threshold', sdr_th.get('signal_threshold', _DROPS_DEFAULT_THRESHOLD))
    return sdr_th.get('signal_threshold', _DROPS_DEFAULT_THRESHOLD)


def _calibrated_max_threshold():
    max_t = float('-inf')
    for sdr_th in _SDR_THRESHOLDS.values():
        t = sdr_th.get('signal_threshold')
        if t is not None:
            max_t = max(max_t, t)
        for rtys_th in sdr_th.get('rtys', {}).values():
            t = rtys_th.get('signal_threshold')
            if t is not None:
                max_t = max(max_t, t)
    return max_t if max_t != float('-inf') else _DROPS_DEFAULT_THRESHOLD


def _get_calibrated_snr_threshold(sdr_id, rtys):
    sdr_th = _SDR_THRESHOLDS.get(sdr_id)
    if not sdr_th:
        return _DROPS_DEFAULT_SNR_THRESHOLD
    if rtys:
        rtys_th = sdr_th.get('rtys', {}).get(rtys)
        if rtys_th:
            return rtys_th.get('snr_threshold', sdr_th.get('snr_threshold', _DROPS_DEFAULT_SNR_THRESHOLD))
    return sdr_th.get('snr_threshold', _DROPS_DEFAULT_SNR_THRESHOLD)


def _calibrated_max_snr_threshold():
    max_t = float('-inf')
    for sdr_th in _SDR_THRESHOLDS.values():
        t = sdr_th.get('snr_threshold')
        if t is not None:
            max_t = max(max_t, t)
        for rtys_th in sdr_th.get('rtys', {}).values():
            t = rtys_th.get('snr_threshold')
            if t is not None:
                max_t = max(max_t, t)
    return max_t if max_t != float('-inf') else _DROPS_DEFAULT_SNR_THRESHOLD


# ---------------------------------------------------------------------------
# R2 backup configuration
# ---------------------------------------------------------------------------
R2_ACCOUNT_ID        = _env('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID     = _env('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = _env('R2_SECRET_ACCESS_KEY')
R2_BUCKET            = _env('R2_BUCKET', 'cgrep-backups')

os.makedirs(BACKUP_DIR, exist_ok=True)

_backup_state = {
    'last_local':  None,   # datetime of last successful local backup
    'last_r2':     None,   # datetime of last successful R2 upload
    'local_size':  0.0,    # total MB of all files in backup/
    'r2_size':     0.0,    # total MB in R2 bucket
    'last_error':  '',
    'running':     False,
}


def _local_backup_size_mb():
    total = sum(os.path.getsize(f) for f in glob.glob(os.path.join(BACKUP_DIR, '*.dump')))
    return round(total / (1024 * 1024), 2)


def _r2_total_size_mb():
    if not (R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY):
        return 0.0
    try:
        import boto3
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name='auto',
        )
        paginator = s3.get_paginator('list_objects_v2')
        total = 0
        for page in paginator.paginate(Bucket=R2_BUCKET):
            for obj in page.get('Contents', []):
                total += obj['Size']
        return round(total / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _run_backup():
    _backup_state['running'] = True
    try:
        db_pass = _env('POSTGRES_PASSWORD', 'changeme')
        db_user = _env('DB_USER', 'postgres')
        db_host = _env('DB_HOST', 'localhost')
        db_port = _env('DB_PORT', '5432')
        db_name = _env('DB_NAME', 'sdr_db')

        stamp    = _dt.now().strftime('%Y%m%d_%H%M%S')
        filename = f'sdr_db_{stamp}.dump'
        filepath = os.path.join(BACKUP_DIR, filename)

        pg_dump = (shutil.which('pg_dump')
                   or r'C:\Program Files\PostgreSQL\18\bin\pg_dump.exe'
                   or r'C:\Program Files\PostgreSQL\17\bin\pg_dump.exe')
        env = os.environ.copy()
        env['PGPASSWORD'] = db_pass

        result = subprocess.run(
            [pg_dump, '-h', db_host, '-p', db_port, '-U', db_user,
             '-F', 'c', '-f', filepath, db_name],
            env=env, capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            _backup_state['last_error'] = 'pg_dump failed (check server logs)'
            return

        _backup_state['last_local'] = _dt.now().isoformat()
        _backup_state['local_size'] = _local_backup_size_mb()

        # Upload to R2
        if R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY:
            try:
                import boto3
                s3 = boto3.client(
                    's3',
                    endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
                    aws_access_key_id=R2_ACCESS_KEY_ID,
                    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                    region_name='auto',
                )
                s3.upload_file(filepath, R2_BUCKET, filename)
                _backup_state['last_r2']   = _dt.now().isoformat()
                _backup_state['r2_size']   = _r2_total_size_mb()
                _backup_state['last_error'] = ''
            except Exception as e:
                _backup_state['last_error'] = f'R2: {str(e)[:150]}'
        else:
            _backup_state['last_error'] = ''

        # Keep only last 10 local dumps
        dumps = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.dump')))
        for old in dumps[:-10]:
            try: os.remove(old)
            except Exception: pass
        _backup_state['local_size'] = _local_backup_size_mb()

        # Prune R2 objects older than 14 days
        if R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY:
            try:
                import boto3
                from datetime import timezone
                s3 = boto3.client(
                    's3',
                    endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
                    aws_access_key_id=R2_ACCESS_KEY_ID,
                    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                    region_name='auto',
                )
                cutoff = _dt.now(timezone.utc) - __import__('datetime').timedelta(days=14)
                paginator = s3.get_paginator('list_objects_v2')
                to_delete = []
                for page in paginator.paginate(Bucket=R2_BUCKET):
                    for obj in page.get('Contents', []):
                        if obj['LastModified'] < cutoff:
                            to_delete.append({'Key': obj['Key']})
                if to_delete:
                    s3.delete_objects(Bucket=R2_BUCKET, Delete={'Objects': to_delete})
                _backup_state['r2_size'] = _r2_total_size_mb()
            except Exception:
                pass

    except Exception as e:
        _backup_state['last_error'] = str(e)[:200]
    finally:
        _backup_state['running'] = False


def _backup_scheduler():
    time.sleep(BACKUP_INTERVAL_SEC)  # wait full interval before first backup
    while True:
        _run_backup()
        time.sleep(BACKUP_INTERVAL_SEC)


_backup_thread = threading.Thread(target=_backup_scheduler, daemon=True)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI()

@app.on_event('startup')
async def _startup_tasks():
    """Run at server start: create indexes, start background workers, and pre-build caches."""

    # 1. Create performance indexes (non-fatal if they fail) off the main event loop.
    #    CREATE INDEX CONCURRENTLY must run outside a transaction, so use AUTOCOMMIT.
    def _create_indexes():
        for _idx_sql in [
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_signal_events_ts_freq_pwr ON signal_events (timestamp, frequency_mhz, power_dbfs)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_ts ON rf_sweep_data (timestamp DESC)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_freq_id ON rf_sweep_data (frequency_mhz, id DESC)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_sdr_id ON rf_sweep_data (sdr_id)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_sdr_freq_id ON rf_sweep_data (sdr_id, frequency_mhz, id DESC) INCLUDE (timestamp, power_dbfs, snr_db)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_snr ON rf_sweep_data (snr_db)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_bucket_id_cover ON rf_sweep_data (CAST(round(frequency_mhz / 100.0) * 100 AS INTEGER), id DESC) INCLUDE (timestamp, frequency_mhz, power_dbfs, snr_db)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_drops_ts ON rf_sweep_data (timestamp DESC) INCLUDE (id, sdr_id, frequency_mhz, power_dbfs, floor_dbfs, snr_db) WHERE power_dbfs < -65 OR (snr_db IS NOT NULL AND snr_db < 20)",
        ]:
            try:
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text(_idx_sql))
            except Exception:
                pass
    _index_thread = threading.Thread(target=_create_indexes, daemon=False, name="db-indexes")
    _index_thread.start()
    print("[startup] DB index creation started in background")

    # 2. Pre-build all 3 outage chart modes so first user request is instant
    for m in ('24h', '7d', '14d'):
        try:
            result = await _run_in_thread(_build_outage_result, m)
            with _OUTAGE_CACHE_LOCK:
                _outage_cache[m] = result
        except Exception:
            pass
    print("[startup] Outage caches primed")

    # 3. Start background threads after server is up to avoid blocking import or startup.
    _backup_thread.start()
    _spectrum_cache_thread.start()
    _peaks_cache_thread.start()
    _telemetry_cache_thread.start()
    _drops_cache_thread.start()
    _outage_refresh_thread.start()
    _sdr_public_cache_thread.start()
    _cleanup_thread.start()
    print("[startup] Background cache/cleanup threads started")

    # 4. Start batched activity-log worker on the event loop.
    global _activity_queue, _activity_worker_task, _health_worker_task
    _activity_queue = asyncio.Queue(maxsize=2000)
    _activity_worker_task = asyncio.create_task(_activity_worker())
    print("[startup] Activity batch worker started")

    # 5. Start periodic server health logger.
    _health_worker_task = asyncio.create_task(_health_logger_worker())
    print(f"[startup] Logging subsystem active: {LOGS_DIR}")
    print("[startup] Health logger started")

@app.on_event('shutdown')
async def _shutdown_tasks():
    global _activity_worker_task, _health_worker_task
    for task in (_activity_worker_task, _health_worker_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

_FRONTEND_ORIGIN = os.environ.get('FRONTEND_ORIGIN', 'http://localhost:5173').strip().rstrip('/')
_PUBLIC_BASE_URL  = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/')

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_FRONTEND_ORIGIN, 'https://example.com', 'http://localhost:8000'],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Next-Step-Token", "X-Poll-Token"],
)

app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
@app.middleware('http')
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    is_spectrum = path.startswith('/spectrum') or path.startswith('/api/spectrum-live')
    response.headers['X-Content-Type-Options']  = 'nosniff'
    response.headers['X-Frame-Options']          = 'SAMEORIGIN' if is_spectrum else 'DENY'
    response.headers['X-XSS-Protection']         = '1; mode=block'
    response.headers['Referrer-Policy']           = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']        = 'geolocation=(), microphone=(), camera=()'
    response.headers['Accept-CH']                 = 'Sec-CH-UA, Sec-CH-UA-Mobile, Sec-CH-UA-Model, Sec-CH-UA-Platform, Sec-CH-UA-Platform-Version, Sec-CH-UA-Full-Version-List'
    response.headers['Critical-CH']               = 'Sec-CH-UA-Model, Sec-CH-UA-Platform'
    script_src = "'self' 'unsafe-inline' 'wasm-unsafe-eval'" if is_spectrum else "'self' 'wasm-unsafe-eval'"
    frame_ancestors = "'self'" if is_spectrum else "'none'"
    response.headers['Content-Security-Policy']   = (
        f"default-src 'self'; "
        f"script-src {script_src}; "
        f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"font-src 'self' https://fonts.gstatic.com data:; "
        f"img-src 'self' data: blob:; "
        f"media-src 'self' blob:; "
        f"connect-src 'self' wss: ws: blob:; "
        f"worker-src blob:; "
        f"frame-ancestors {frame_ancestors};"
    )
    return response


# ---------------------------------------------------------------------------
# Activity tracking middleware — logs HTTP request batches in the background
# ---------------------------------------------------------------------------
_SKIP_ACTIVITY_PATHS = {
    '/api/activity', '/ws/sdr', '/api/spectrum-live', '/ws/spectrum', '/spectrum',
    '/ws/chat', '/api/chat/history',
}
_SKIP_ACTIVITY_PREFIXES = ('/assets/', '/DS-DIGIT', '/favicon')
_NO_ACTIVITYLOG_PATHS = {'/api/history', '/api/telemetry', '/api/spectrum/latest'}  # upsert device_session only
_SKIP_ALL_DB_PATHS = {'/api/telemetry', '/api/spectrum/latest', '/api/sdr/list'}  # skip even device_session upsert
_SKIP_ALL_DB_PREFIXES = ('/api/sdr/', '/api/history', '/api/analytics/', '/api/export', '/api/chat/')

# Real-time telemetry counters
_REQUEST_WINDOW_SEC = 5
_request_times = deque()
_request_lock = threading.Lock()
_net_io_lock = threading.Lock()
_last_net_io = None

# Async queue for batched activity writes. Created on startup to avoid
# needing an event loop during import.
_activity_queue: asyncio.Queue | None = None
_activity_worker_task = None


def _write_activity_batch(batch: list):
    """Write a batch of activity events in a single DB transaction."""
    if not batch:
        return
    pool = engine.pool
    if hasattr(pool, 'checkedout') and pool.checkedout() >= pool.size():
        return
    db = SessionLocal()
    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        now = _dt.utcnow()

        # Aggregate per-session request counts within this batch so the upsert
        # increments by the correct amount and duplicate session rows are avoided.
        session_groups = {}
        for ev in batch:
            key = (ev['user_id'], ev['session_id'], ev['ip'], ev['ua'])
            if key not in session_groups:
                session_groups[key] = {
                    'count': 0,
                    'hints': ev.get('hints'),
                }
            session_groups[key]['count'] += 1

        for (user_id, session_id, ip, ua), info in session_groups.items():
            upsert_vals = dict(
                id=_uuid_mod.uuid4(),
                user_id=user_id,
                session_id=session_id,
                ip_address=ip,
                user_agent=ua,
                first_seen=now,
                last_seen=now,
                request_count=info['count'],
                device_hints=info['hints'] or None,
            )
            stmt = pg_insert(DeviceSession).values(**upsert_vals).on_conflict_do_update(
                constraint='uq_device_session',
                set_={
                    'last_seen':     now,
                    'request_count': DeviceSession.request_count + info['count'],
                    'device_hints':  info['hints'] or None,
                },
            )
            db.execute(stmt)

        # Insert ActivityLog rows only for paths that request it.
        entries = []
        for ev in batch:
            if ev['path'] in _NO_ACTIVITYLOG_PATHS:
                continue
            entries.append(ActivityLog(
                user_id=ev['user_id'],
                session_id=ev['session_id'],
                category='API',
                event_type=f"{ev['method']} {ev['path']}",
                method=ev['method'],
                path=ev['path'],
                status_code=ev['status_code'],
                duration_ms=ev['duration_ms'],
                ip_address=ev['ip'],
                user_agent=ev['ua'],
                payload={},
            ))
        if entries:
            db.add_all(entries)

        db.commit()
    except Exception as exc:
        db.rollback()
        print(f'[activity] batch write error: {exc}')
    finally:
        db.close()


async def _activity_worker():
    """Single async worker that flushes activity batches every 2s or 100 events."""
    while True:
        item = await _activity_queue.get()
        if item is None:
            break
        batch = [item]
        deadline = asyncio.get_event_loop().time() + 2.0
        while len(batch) < 100:
            timeout = max(0.0, deadline - asyncio.get_event_loop().time())
            try:
                item = await asyncio.wait_for(_activity_queue.get(), timeout=timeout)
                if item is None:
                    break
                batch.append(item)
            except asyncio.TimeoutError:
                break
        try:
            await asyncio.get_event_loop().run_in_executor(_THREAD_POOL, _write_activity_batch, batch)
        except Exception as exc:
            print(f'[activity] worker executor error: {exc}')


# ---------------------------------------------------------------------------
# Server health / performance logger
# ---------------------------------------------------------------------------
_health_worker_task: asyncio.Task | None = None


def _ping_ms(host: str = '8.8.8.8') -> float:
    """Run one ICMP ping and return round-trip time in ms, or -1 on failure."""
    try:
        proc = subprocess.run(
            ['ping', '-n', '1', '-w', '2000', host],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode != 0:
            return -1.0
        out = proc.stdout
        # Windows ping output: "time=12ms" or "time<1ms"
        if 'time<' in out:
            return 0.5
        import re
        m = re.search(r'time[=<]([\d.]+)ms', out)
        if m:
            return float(m.group(1))
        return -1.0
    except Exception:
        return -1.0


def _db_health_stats():
    """Return (database_size_bytes, active_connections)."""
    db = SessionLocal()
    try:
        size = db.execute(text('SELECT pg_database_size(current_database())')).scalar() or 0
        conns = db.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        )).scalar() or 0
        return int(size), int(conns)
    except Exception:
        return 0, 0
    finally:
        db.close()


def _collect_health_metrics():
    """Gather CPU, memory, disk, network, ping, DB, and thread-pool health.
    Each metric is best-effort so one failing sensor doesn't blank the whole line."""
    try:
        cpu = psutil.cpu_percent(interval=0.5)
    except Exception:
        cpu = -1.0
    try:
        mem_pct = psutil.virtual_memory().percent
    except Exception:
        mem_pct = -1.0
    try:
        disk_pct = psutil.disk_usage(BASE_DIR).percent
    except Exception:
        disk_pct = -1.0
    try:
        net = psutil.net_io_stats()
        net_sent, net_recv = net.bytes_sent, net.bytes_recv
    except Exception:
        net_sent = net_recv = 0
    try:
        net_conns = len(psutil.net_connections())
    except Exception:
        net_conns = -1
    ping_ms = _ping_ms()
    try:
        db_size, db_conns = _db_health_stats()
    except Exception:
        db_size, db_conns = 0, -1
    load1, load5, load15 = os.getloadavg() if hasattr(os, 'getloadavg') else (0.0, 0.0, 0.0)
    ws_clients = len(_chat_clients)
    online_users = len(_chat_online)
    thread_pool_workers = _THREAD_POOL._max_workers
    thread_pool_pending = getattr(_THREAD_POOL, '_work_queue', None)
    thread_pool_qsize = thread_pool_pending.qsize() if thread_pool_pending else 0
    return (
        f"cpu={cpu:.1f}% mem={mem_pct:.1f}% disk={disk_pct:.1f}% "
        f"load1={load1:.2f} load5={load5:.2f} load15={load15:.2f} "
        f"net_sent={net_sent} net_recv={net_recv} net_conns={net_conns} "
        f"ping={ping_ms:.1f}ms db_size={db_size} db_conns={db_conns} "
        f"ws_clients={ws_clients} online_users={online_users} "
        f"thread_pool_workers={thread_pool_workers} thread_pool_pending={thread_pool_qsize}"
    )


_HEALTH_LOG_INTERVAL_SEC = float(_env('HEALTH_LOG_INTERVAL_SEC', '10'))


async def _health_logger_worker():
    """Write health snapshot to Logs/health.log periodically."""
    while True:
        await asyncio.sleep(_HEALTH_LOG_INTERVAL_SEC)
        try:
            snapshot = await _run_in_thread(_collect_health_metrics)
            _HEALTH_LOG.info(snapshot)
        except Exception as exc:
            _HEALTH_LOG.warning(f"health collection error: {exc}")


@app.middleware('http')
async def access_log_middleware(request: Request, call_next):
    """Log every HTTP request with timing to Logs/access.log (no DB hit)."""
    t0 = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - t0) * 1000

    ip = get_client_ip(request)
    user_id = None
    token = request.cookies.get(_COOKIE_NAME)
    if token:
        pl = _decode_jwt(token)
        if pl:
            user_id = pl.get('sub')

    query = request.url.query
    path_with_query = f"{request.url.path}?{query}" if query else request.url.path
    req_bytes = request.headers.get('content-length', '')
    resp_bytes = response.headers.get('content-length', '')
    _ACCESS_LOG.info(
        f"{request.method} {path_with_query} | status={response.status_code} | "
        f"time={duration_ms:.2f}ms | req_bytes={req_bytes} resp_bytes={resp_bytes} | "
        f"ip={ip} | user={user_id} | ua={request.headers.get('user-agent', '')[:120]}"
    )
    return response


@app.middleware('http')
async def activity_middleware(request: Request, call_next):
    path = request.url.path
    if path in _SKIP_ACTIVITY_PATHS or any(path.startswith(p) for p in _SKIP_ACTIVITY_PREFIXES):
        return await call_next(request)

    t0 = time.time()
    response = await call_next(request)
    duration_ms = int((time.time() - t0) * 1000)

    # Track request rate
    with _request_lock:
        _request_times.append(t0)
        cutoff = t0 - _REQUEST_WINDOW_SEC
        while _request_times and _request_times[0] < cutoff:
            _request_times.popleft()

    try:
        ip = get_client_ip(request)
        ua = request.headers.get('user-agent', '')[:512]
        token = request.cookies.get(_COOKIE_NAME)
        user_id = None
        session_id = None
        if token:
            pl = _decode_jwt(token)
            if pl:
                try:
                    user_id = _uuid_mod.UUID(pl.get('sub', ''))
                except (ValueError, AttributeError):
                    pass
                session_id = pl.get('jti')

        hints = {}
        ch_model    = request.headers.get('Sec-CH-UA-Model', '').strip()
        ch_platform = request.headers.get('Sec-CH-UA-Platform', '').strip('"').strip()
        ch_pv       = request.headers.get('Sec-CH-UA-Platform-Version', '').strip('"').strip()
        ch_mobile   = request.headers.get('Sec-CH-UA-Mobile', '').strip()
        ch_ua       = request.headers.get('Sec-CH-UA', '').strip()
        if ch_model:    hints['model']            = ch_model.strip('"')
        if ch_platform: hints['platform']         = ch_platform
        if ch_pv:       hints['platform_version'] = ch_pv
        if ch_mobile:   hints['mobile']           = ch_mobile == '?1'
        if ch_ua:       hints['brands']           = ch_ua

        if path not in _SKIP_ALL_DB_PATHS and not any(path.startswith(p) for p in _SKIP_ALL_DB_PREFIXES):
            if _activity_queue is not None:
                try:
                    _activity_queue.put_nowait({
                        'path': path, 'method': request.method,
                        'status_code': response.status_code, 'duration_ms': duration_ms,
                        'ip': ip, 'ua': ua, 'user_id': user_id,
                        'session_id': session_id, 'hints': hints,
                    })
                except asyncio.QueueFull:
                    pass
    except Exception as _outer_exc:
        print(f'[activity] outer error: {_outer_exc}')

    return response


# ---------------------------------------------------------------------------
# Rate limiter — max 5 password attempts per IP per 60 s
# ---------------------------------------------------------------------------
_rate_buckets: dict = defaultdict(list)


def _check_rate_limit(ip: str, limit: int = 5, window: int = 60):
    now = time.time()
    bucket = [t for t in _rate_buckets.get(ip, []) if now - t < window]
    bucket.append(now)
    if bucket:
        _rate_buckets[ip] = bucket
    else:
        _rate_buckets.pop(ip, None)
    if len(bucket) > limit:
        raise HTTPException(status_code=429, detail='Too many attempts — try again later')


# ---------------------------------------------------------------------------
# Activity — frontend event ingestion endpoint
# ---------------------------------------------------------------------------
from pydantic import BaseModel as _BM
from typing import List as _List, Optional as _Opt

class _FrontendEvent(_BM):
    event_type: str
    category:   str = 'UI'
    payload:    dict = {}
    timestamp:  _Opt[str] = None

class _ActivityBatch(_BM):
    events:     _List[_FrontendEvent]
    session_id: _Opt[str] = None


@app.post('/api/activity')
async def ingest_activity(batch: _ActivityBatch, request: Request):
    """Fire-and-forget frontend event ingestion. No auth required."""
    ip = get_client_ip(request)
    ua = request.headers.get('user-agent', '')[:512]
    token = request.cookies.get(_COOKIE_NAME)
    user_id = None
    session_id = batch.session_id
    if token:
        payload = _decode_jwt(token)
        if payload:
            try:
                user_id = _uuid_mod.UUID(payload.get('sub', ''))
            except (ValueError, AttributeError):
                pass
            if not session_id:
                session_id = payload.get('jti')

    db = SessionLocal()
    written = 0
    try:
        for ev in batch.events[:50]:  # cap at 50 per batch
            try:
                ts = None
                if ev.timestamp:
                    try:
                        from datetime import datetime as _dtt
                        ts = _dtt.fromisoformat(ev.timestamp.replace('Z', '+00:00')).replace(tzinfo=None)
                    except Exception:
                        pass
                entry = ActivityLog(
                    timestamp=ts or _dt.utcnow(),
                    user_id=user_id,
                    session_id=session_id,
                    category=(ev.category or 'UI')[:50],
                    event_type=(ev.event_type or 'UNKNOWN')[:100],
                    ip_address=ip,
                    user_agent=ua,
                    payload=ev.payload or {},
                )
                db.add(entry)
                written += 1
            except Exception as exc:
                print(f'[activity] row error: {exc}')
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            print(f'[activity] commit error: {exc}')
    finally:
        db.close()
    return {'status': 'ok', 'written': written}


# ---------------------------------------------------------------------------
# Activity — admin read endpoint
# ---------------------------------------------------------------------------
from typing import Optional as _OptStr

@app.get('/api/admin/activity-logs')
async def list_activity_logs(
    request: Request,
    _caller: User = Depends(require_permission('view:audit_logs')),
    limit:    int = 50,
    offset:   int = 0,
    category: _OptStr[str] = None,
    user_id:  _OptStr[str] = None,
    start_date: _OptStr[str] = None,
    end_date:   _OptStr[str] = None,
):
    db2 = SessionLocal()
    try:
        q = db2.query(ActivityLog).order_by(ActivityLog.timestamp.desc())
        if category:
            q = q.filter(ActivityLog.category == category)
        if user_id:
            try:
                q = q.filter(ActivityLog.user_id == _uuid_mod.UUID(user_id))
            except ValueError:
                raise HTTPException(400, 'Invalid user_id')
        if start_date:
            try:
                from datetime import datetime as _dtt
                q = q.filter(ActivityLog.timestamp >= _dtt.fromisoformat(start_date))
            except ValueError:
                raise HTTPException(400, 'Invalid start_date')
        if end_date:
            try:
                from datetime import datetime as _dtt
                q = q.filter(ActivityLog.timestamp <= _dtt.fromisoformat(end_date))
            except ValueError:
                raise HTTPException(400, 'Invalid end_date')
        total = q.count()
        rows  = q.offset(offset).limit(limit).all()
        return {
            'total': total, 'offset': offset, 'limit': limit,
            'logs': [
                {
                    'id':          str(r.id),
                    'timestamp':   r.timestamp.isoformat() + 'Z',
                    'user_id':     str(r.user_id) if r.user_id else None,
                    'username':    r.user.username if r.user else None,
                    'session_id':  r.session_id,
                    'category':    r.category,
                    'event_type':  r.event_type,
                    'method':      r.method,
                    'path':        r.path,
                    'status_code': r.status_code,
                    'duration_ms': r.duration_ms,
                    'ip_address':  r.ip_address,
                    'user_agent':  r.user_agent,
                    'payload':     r.payload,
                }
                for r in rows
            ],
        }
    finally:
        db2.close()


@app.get('/api/admin/logs')
async def tail_server_logs(
    log: str = 'access',
    lines: int = 200,
    _caller: User = Depends(require_permission('view:audit_logs')),
):
    """Tail the last N lines of Logs/access.log or Logs/health.log."""
    if log not in ('access', 'health'):
        raise HTTPException(status_code=400, detail="log must be 'access' or 'health'")
    path = os.path.join(LOGS_DIR, f'{log}.log')
    if not os.path.isfile(path):
        return {'lines': []}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            # Read last N lines efficiently.
            f.seek(0, 2)
            buf_size = 8192
            chunks = []
            remaining = lines
            pos = f.tell()
            while pos > 0 and remaining > 0:
                read_size = min(buf_size, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                chunks.insert(0, chunk)
                remaining -= chunk.count('\n')
            text = ''.join(chunks)
            all_lines = text.splitlines()
            return {'lines': all_lines[-lines:]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get('/api/admin/users/{user_id}/sessions')
async def user_sessions(
    user_id: str,
    _caller: User = Depends(require_permission('manage:users')),
):
    """Return distinct browser sessions for a user from device_sessions table."""
    import user_agents as _ua_lib
    import re as _re

    def _parse(ua_str):
        if not ua_str:
            return {'browser': '—', 'os': '—', 'device': '—', 'arch': '', 'is_mobile': False, 'is_tablet': False, 'raw': ''}
        ua = _ua_lib.parse(ua_str)
        browser = ua.browser.family
        if ua.browser.version_string:
            browser += ' ' + ua.browser.version_string.split('.')[0]
        os_str = ua.os.family
        if ua.os.version_string:
            os_str += ' ' + ua.os.version_string
        device = ua.device.family if ua.device.family != 'Other' else ''
        if ua.device.brand and ua.device.brand != 'Other':
            device = ua.device.brand
            if ua.device.model and ua.device.model != 'Other' and ua.device.model != ua.device.brand:
                device += ' ' + ua.device.model
        arch = ''
        if _re.search(r'arm64|aarch64', ua_str, _re.I):
            arch = 'ARM64'
        elif _re.search(r'Win64|x86_64|x64|WOW64', ua_str, _re.I):
            arch = 'x64'
        elif _re.search(r'i686|i386|x86', ua_str, _re.I):
            arch = 'x86'
        return {
            'browser':   browser,
            'os':        os_str,
            'device':    device or os_str,
            'arch':      arch,
            'is_mobile': ua.is_mobile,
            'is_tablet': ua.is_tablet,
            'raw':       ua_str[:120],
        }

    try:
        uid = _uuid_mod.UUID(user_id)
    except ValueError:
        raise HTTPException(400, 'Invalid user_id')

    db2 = SessionLocal()
    try:
        rows = (
            db2.query(DeviceSession)
            .filter(DeviceSession.user_id == uid)
            .order_by(DeviceSession.last_seen.desc())
            .limit(50)
            .all()
        )
        def _model_from_hints(hints):
            if not hints:
                return None
            m = hints.get('model', '').strip().strip('"')
            p = hints.get('platform', '')
            pv = hints.get('platform_version', '')
            if m:
                return f"{p} · {m}" if p else m
            if p:
                return f"{p} {pv}".strip()
            return None

        return [
            {
                'session_id':    r.session_id,
                'ip_address':    r.ip_address,
                'first_seen':    (r.first_seen.isoformat() + 'Z') if r.first_seen else None,
                'last_seen':     (r.last_seen.isoformat() + 'Z') if r.last_seen else None,
                'request_count': r.request_count,
                'model':         _model_from_hints(r.device_hints),
                'device_hints':  r.device_hints,
                **_parse(r.user_agent),
            }
            for r in rows
        ]
    finally:
        db2.close()


@app.get('/api/config')
async def get_config():
    return JSONResponse({'publicBaseUrl': _PUBLIC_BASE_URL or None})


_weather_cache = {}
_WEATHER_TTL_SEC = 3600  # cache per location for 1 hour

@app.get('/api/weather')
async def get_weather(lat: float, lon: float):
    """Proxy current weather from Open-Meteo, cached per location for 1 hour."""
    key = (round(lat, 4), round(lon, 4))
    now = time.time()
    cached = _weather_cache.get(key)
    if cached and now - cached['ts'] < _WEATHER_TTL_SEC:
        return JSONResponse(cached['data'])

    url = f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true'
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = _json.loads(resp.read().decode('utf-8'))
        _weather_cache[key] = {'ts': now, 'data': data}
        return JSONResponse(data)
    except Exception as exc:
        if cached:
            return JSONResponse(cached['data'])
        raise HTTPException(status_code=502, detail=f'Weather lookup failed: {exc}')


# ---------------------------------------------------------------------------
# Public SDR scan API — read-only, no login required (Meteo-style)
# ---------------------------------------------------------------------------
# The server builds one 30-minute snapshot on clock boundaries (:00 and :30).
# Every caller gets exactly the same data until the next window is published.
_SDR_PUBLIC_CACHE: dict | None = None
_SDR_PUBLIC_CACHE_LOCK = threading.Lock()
_SDR_PUBLIC_REFRESH_SEC = 1800.0  # 30 minutes


def _build_sdr_public_snapshot():
    """Build a snapshot covering the last completed 30-minute window."""
    now = _dt.now()
    # Floor to the most recent completed 30-minute boundary
    minute = now.minute - (now.minute % 30)
    window_end = now.replace(minute=minute, second=0, microsecond=0)
    window_start = window_end - timedelta(minutes=30)

    db = SessionLocal()
    try:
        rows = db.query(SweepData).with_entities(
            SweepData.id,
            SweepData.timestamp,
            SweepData.frequency_mhz,
            SweepData.power_dbfs,
            SweepData.floor_dbfs,
            SweepData.snr_db,
            SweepData.sdr_id,
        ).filter(SweepData.timestamp >= window_start) \
         .filter(SweepData.timestamp < window_end) \
         .order_by(desc(SweepData.timestamp), desc(SweepData.id)) \
         .limit(100000) \
         .all()
        samples = [
            {
                "id": r.id,
                "ts": r.timestamp.timestamp() if r.timestamp is not None else 0,
                "freq_mhz": _freq_mhz(r.frequency_mhz) if r.frequency_mhz is not None else 0.0,
                "power_dbfs": round(r.power_dbfs, 1) if r.power_dbfs is not None else None,
                "floor_dbfs": round(r.floor_dbfs, 1) if r.floor_dbfs is not None else None,
                "snr_db": round(r.snr_db, 1) if r.snr_db is not None else None,
                "sdr_id": r.sdr_id or "",
            }
            for r in rows
        ]
    finally:
        db.close()

    return {
        "window_start": window_start.timestamp(),
        "window_end": window_end.timestamp(),
        "generated_at": now.timestamp(),
        "samples": samples,
    }


def _sdr_public_cache_loop():
    """Refresh the public SDR snapshot on every 30-minute clock boundary."""
    global _SDR_PUBLIC_CACHE
    # Build once at startup so the first request does not have to wait.
    try:
        snapshot = _build_sdr_public_snapshot()
        with _SDR_PUBLIC_CACHE_LOCK:
            _SDR_PUBLIC_CACHE = snapshot
        print("[sdr_public] initial snapshot built")
    except Exception:
        print("[sdr_public] initial snapshot error:")
        traceback.print_exc()

    while True:
        now = _dt.now()
        # seconds until the next 30-minute boundary
        elapsed_in_period = (now.minute % 30) * 60 + now.second + now.microsecond / 1_000_000
        wait = _SDR_PUBLIC_REFRESH_SEC - elapsed_in_period
        if wait <= 0:
            wait += _SDR_PUBLIC_REFRESH_SEC
        time.sleep(wait)

        try:
            snapshot = _build_sdr_public_snapshot()
            with _SDR_PUBLIC_CACHE_LOCK:
                _SDR_PUBLIC_CACHE = snapshot
            print("[sdr_public] snapshot refreshed")
        except Exception:
            print("[sdr_public] refresh error:")
            traceback.print_exc()


_sdr_public_cache_thread = threading.Thread(target=_sdr_public_cache_loop, daemon=True, name="sdr-public-cache")


@app.get('/api/sdr')
async def get_sdr(sdr_id: str = '', limit: int = 10000):
    """Public SDR scan snapshot. Data is the same for all callers in the same 30-minute window."""
    global _SDR_PUBLIC_CACHE
    with _SDR_PUBLIC_CACHE_LOCK:
        snapshot = _SDR_PUBLIC_CACHE
    if snapshot is None:
        # Warm cache on first request so the very first caller does not get empty data.
        snapshot = _build_sdr_public_snapshot()
        with _SDR_PUBLIC_CACHE_LOCK:
            _SDR_PUBLIC_CACHE = snapshot
    samples = snapshot["samples"]
    if sdr_id:
        samples = [s for s in samples if s.get("sdr_id") == sdr_id]
    return JSONResponse({
        "generated_at": snapshot["generated_at"],
        "window_start": snapshot["window_start"],
        "window_end": snapshot["window_end"],
        "samples": samples[:limit],
    })


# Known stations — must match STATIONS in SigintDashboard.jsx
# First station is DAB+ 12A (223.936 MHz = 223936 kHz in SQL), rest are FM
_KNOWN_STATIONS_MHZ = [
    88.7,   # Placeholder for DAB+ 12A (actual data fetched separately as 223936 kHz)
    90.0, 90.0, 90.7, 92.5, 93.0, 93.3, 97.7,
    99.3, 100.5, 101.1, 101.3,
    103.0, 103.3, 104.0, 105.0, 106.0, 106.3, 107.0,
]

# DAB+ 12A frequency in kHz (as stored in SQL)
_DAB_12A_KHZ = 223936


def _fetch_latest_spectrum():
    """
    Fetch the single most-recent DB row for each known station independently.
    Uses a recursive loose-index-scan CTE to avoid a 9M-row DISTINCT ON sort.
    Requires the covering index:
        CREATE INDEX ix_rf_sweep_bucket_id_cover
        ON rf_sweep_data (CAST(round(frequency_mhz / 100.0) * 100 AS INTEGER), id DESC)
        INCLUDE (timestamp, frequency_mhz, power_dbfs, snr_db);
    """
    from sqlalchemy import text
    db = SessionLocal()
    try:
        # Walk the (bucket, id DESC) index once per bucket — one lookup per station.
        sql = text("""
            WITH RECURSIVE latest AS (
                SELECT * FROM (
                    SELECT id, timestamp, frequency_mhz, power_dbfs, snr_db,
                           CAST(round(frequency_mhz / 100.0) * 100 AS INTEGER) AS bucket
                    FROM rf_sweep_data
                    WHERE frequency_mhz >= :lo AND frequency_mhz <= :hi
                    ORDER BY CAST(round(frequency_mhz / 100.0) * 100 AS INTEGER), id DESC
                    LIMIT 1
                ) sub
                UNION ALL
                SELECT nxt.id, nxt.timestamp, nxt.frequency_mhz, nxt.power_dbfs, nxt.snr_db,
                       CAST(round(nxt.frequency_mhz / 100.0) * 100 AS INTEGER)
                FROM latest cur
                CROSS JOIN LATERAL (
                    SELECT id, timestamp, frequency_mhz, power_dbfs, snr_db
                    FROM rf_sweep_data
                    WHERE frequency_mhz >= :lo AND frequency_mhz <= :hi
                      AND CAST(round(frequency_mhz / 100.0) * 100 AS INTEGER) > cur.bucket
                    ORDER BY CAST(round(frequency_mhz / 100.0) * 100 AS INTEGER), id DESC
                    LIMIT 1
                ) nxt
            )
            SELECT id, timestamp, frequency_mhz, power_dbfs, snr_db FROM latest
        """)
        rows = db.execute(sql, {"lo": 88700, "hi": 108100}).fetchall()

        # Latest DAB+ 12A row
        dab_row = (
            db.query(SweepData)
            .filter(SweepData.frequency_mhz == _DAB_12A_KHZ)
            .order_by(desc(SweepData.id))
            .first()
        )

        if not rows:
            return None

        # Build freq_mhz -> row lookup
        row_by_freq = {r.frequency_mhz / 1000.0: r for r in rows}

        # Noise floor / peak from these latest-per-station values
        all_pwr = [r.power_dbfs for r in rows]
        sorted_pwr  = sorted(all_pwr)
        noise_floor = sorted_pwr[len(sorted_pwr) // 4]
        peak_power  = max(all_pwr)
        snr_peak    = peak_power - noise_floor
        is_interference = bool(snr_peak > 20.0 or peak_power > -60.0)
        peak_intensity  = float(np.clip((peak_power + 100.0) / 60.0, 0.0, 1.0))

        # ── Latest full sweep for SigintDashboard (200kHz window must be from one sweep)
        latest_sweep_row = (
            db.query(SweepData)
            .filter(SweepData.frequency_mhz >= 88700, SweepData.frequency_mhz <= 108100)
            .order_by(desc(SweepData.id))
            .first()
        )
        sweep_rows = []
        if latest_sweep_row and latest_sweep_row.timestamp:
            from datetime import timedelta
            sweep_ts = latest_sweep_row.timestamp
            sweep_rows = (
                db.query(SweepData)
                .filter(
                    SweepData.frequency_mhz >= 88700,
                    SweepData.frequency_mhz <= 108100,
                    SweepData.timestamp >= sweep_ts - timedelta(seconds=1),
                    SweepData.timestamp <= sweep_ts + timedelta(seconds=1),
                )
                .all()
            )

        # DAB+ at slot 0
        panoramic_peaks = []
        sigint_peaks    = []
        station_scan    = {}
        if dab_row:
            panoramic_peaks.append({"freq": 88.7, "dbfs": float(dab_row.power_dbfs),
                                     "ts": dab_row.timestamp.timestamp()})
            sigint_peaks.append({"freq": 88.7, "dbfs": float(dab_row.power_dbfs),
                                  "ts": dab_row.timestamp.timestamp()})
            station_scan["0"] = {"pwr": float(dab_row.power_dbfs),
                                  "snr": round(float(dab_row.power_dbfs) - noise_floor, 1),
                                  "ts":  dab_row.timestamp.timestamp()}
        else:
            panoramic_peaks.append({"freq": 88.7, "dbfs": -95.0, "ts": None})
            sigint_peaks.append({"freq": 88.7, "dbfs": -95.0, "ts": None})
            station_scan["0"] = {"pwr": -95.0, "snr": 0.0, "ts": 0.0}

        # FM stations
        for idx, known in enumerate(_KNOWN_STATIONS_MHZ[1:], start=1):
            best = None
            best_dist = 0.15
            for freq_mhz, r in row_by_freq.items():
                d = abs(freq_mhz - known)
                if d < best_dist:
                    best_dist = d
                    best = r

            pwr = float(best.power_dbfs) if best else -95.0
            ts  = best.timestamp.timestamp() if best else 0.0
            panoramic_peaks.append({"freq": float(known), "dbfs": pwr, "ts": ts})
            station_scan[str(idx)] = {"pwr": pwr, "snr": round(pwr - noise_floor, 1), "ts": ts}

            # SigintDashboard: highest peak within the station's 200 kHz bandwidth
            # (using the latest full sweep so the window is from one coherent sample)
            # If the latest sweep has no data for this station, omit it so the
            # frontend keeps the previous value instead of snapping to -70.
            peak = None
            window_rows = []
            for r in sweep_rows:
                freq_mhz = r.frequency_mhz / 1000.0
                if abs(freq_mhz - known) <= 0.10:
                    window_rows.append(r)
                    if peak is None or r.power_dbfs > peak.power_dbfs:
                        peak = r
            if peak:
                # Jamming detection for 101.3 MHz:
                # A CW jammer produces an extremely high SNR (sharp tone above flat
                # noise floor) whereas a real wideband FM station has moderate SNR.
                # Use the median of the last 5 readings to avoid single-sample noise.
                jammed = False
                if abs(known - 101.3) < 0.01:
                    recent = (
                        db.query(SweepData.snr_db)
                        .filter(SweepData.frequency_mhz == 101300)
                        .order_by(desc(SweepData.id))
                        .limit(5)
                        .all()
                    )
                    if recent:
                        snr_vals = sorted(r.snr_db for r in recent)
                        median_snr = snr_vals[len(snr_vals) // 2]
                        # Real FM: SNR 10-28 dB. CW jammer: SNR 30-60+ dB.
                        jammed = bool(median_snr > 30.0)
                sigint_peaks.append({
                    "freq":   float(known),
                    "dbfs":   float(peak.power_dbfs),
                    "ts":     peak.timestamp.timestamp(),
                    "jammed": jammed,
                })

        return {
            "peak_intensity":  peak_intensity,
            "panoramic_peaks": panoramic_peaks,
            "sigint_peaks":    sigint_peaks,
            "station_scan":    station_scan,
            "is_interference": is_interference,
            "playing_idx":     None,
            "log_running":     False,
            "log_path":        "",
        }

    except Exception:
        return None
    finally:
        db.close()


# ── Shared spectrum cache (one query feeds all WS clients) ────────────────────
_spectrum_cache: dict | None = None
_SPECTRUM_REFRESH_SEC = float(_env('SPECTRUM_REFRESH_SEC', '2.0'))


def _spectrum_cache_loop():
    """Background thread: refresh spectrum data every 2 seconds."""
    global _spectrum_cache
    time.sleep(3)  # Wait for DB to be ready on startup
    while True:
        try:
            result = _fetch_latest_spectrum()
            if result is not None:
                _spectrum_cache = result
        except Exception as e:
            print(f"[spectrum_cache] error: {e}")
        time.sleep(_SPECTRUM_REFRESH_SEC)


_spectrum_cache_thread = threading.Thread(target=_spectrum_cache_loop, daemon=True, name="spectrum-cache")


@app.get("/api/history")
async def get_history(hours: float = 0, _user=Depends(get_current_user)):
    return await _run_in_thread(_get_history_cached, hours)


def _history_sync(hours=0):
    db = SessionLocal()
    try:
        query = db.query(SweepData).order_by(desc(SweepData.id))
        if hours and hours > 0:
            since = _dt.utcnow() - timedelta(hours=hours)
            query = query.filter(SweepData.timestamp >= since)
        rows = query.limit(200).all()
        return [
            {
                "id": r.id,
                "ts": r.timestamp.timestamp() if r.timestamp is not None else 0,
                "freq": _freq_mhz(r.frequency_mhz) if r.frequency_mhz is not None else 0.0,
                "dbfs": round(r.power_dbfs, 1) if r.power_dbfs is not None else 0.0,
                "snr": round(r.snr_db, 1) if r.snr_db is not None else 0.0,
                "sdr_id": r.sdr_id or "",
            }
            for r in rows
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# History cache (TTL)
# ---------------------------------------------------------------------------
_HISTORY_CACHE: dict = {}
_HISTORY_TS: dict = {}
_HISTORY_LOCK = threading.Lock()
_HISTORY_TTL_SEC = float(_env('HISTORY_TTL_SEC', '5.0'))


def _get_history_cached(hours: float = 0):
    now = time.time()
    key = hours
    with _HISTORY_LOCK:
        cached = _HISTORY_CACHE.get(key)
        ts = _HISTORY_TS.get(key, 0)
        if cached is not None and (now - ts) < _HISTORY_TTL_SEC:
            return cached
    result = _history_sync(hours)
    with _HISTORY_LOCK:
        _HISTORY_CACHE[key] = result
        _HISTORY_TS[key] = time.time()
    return result


# ---------------------------------------------------------------------------
# Drops cache — refreshed by a background thread every 30-60s
# ---------------------------------------------------------------------------
_DROPS_DEFAULT_HOURS = 24.0
_DROPS_DEFAULT_THRESHOLD = -65.0
_DROPS_DEFAULT_MIN_DURATION = 180.0
_DROPS_DEFAULT_MAX_GAP = 30.0
_DROPS_DEFAULT_SNR_THRESHOLD = 10.0
_DROPS_REFRESH_SEC = float(_env('DROPS_REFRESH_SEC', '300.0'))
_drops_cache: list | None = None
_drops_calibrated_drops_cache: list | None = None
_drops_calibrated_cache: list | None = None
_drops_calibrated_60_cache: list | None = None
_DROPS_CACHE_LOCK = threading.Lock()
_DROPS_CAL_60_MIN_DURATION = 60.0
# WhatsApp drop-alert settings: min duration 10 min, random per-key cooldown,
# plus a random global cooldown so no two alerts go out back-to-back.
_DROP_ALERT_MIN_DURATION_SEC = 600.0
_DROP_ALERT_COOLDOWN_MIN_SEC = 420.0  # 7 minutes
_DROP_ALERT_COOLDOWN_MAX_SEC = 900.0  # 15 minutes
_DROP_ALERT_EXCLUDE = {
    ("site_1", 99.3),
    ("site_1", 101.3),
    ("site_2", 99.3),
    ("site_2", 101.3),
}
_last_drop_alert_ts: dict[tuple[str, float], float] = {}
_last_drop_alert_cooldown: dict[tuple[str, float], float] = {}
_last_drop_alert_any = 0.0
_last_drop_alert_any_cooldown: float | None = None
_DROP_ALERTS_ENABLED = False


def _format_hhmmss(seconds):
    seconds = int(round(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _generate_drop_plot_base64(rows, sdr_id, freq_mhz, rtys_name, station_name):
    """Build a base64 PNG of the last 10 sweep rows for a drop."""
    if not rows:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MultipleLocator
    except Exception as e:
        print(f"[drop_plot] matplotlib not available: {e}")
        return None

    try:
        rows_sorted = sorted(rows, key=lambda r: r.get("timestamp") or "")
        last_rows = rows_sorted[-10:]
        timestamps = [r.get("timestamp", "") for r in last_rows]
        power = [r.get("power_dbfs") if r.get("power_dbfs") is not None else -100.0 for r in last_rows]
        snr = [r.get("snr_db") if r.get("snr_db") is not None else 0.0 for r in last_rows]

        x = list(range(len(last_rows)))
        width = 0.5
        power_min = min(power)
        power_max = max(power)
        power_bottom = power_min - max(1.0, (power_max - power_min) * 0.05)
        power_heights = [v - power_bottom for v in power]

        fig, ax1 = plt.subplots(figsize=(max(10, len(last_rows) * 1.1), 6))

        bars_power = ax1.bar(
            x,
            power_heights,
            width,
            bottom=power_bottom,
            label="Power (dBFS)",
            color="#1f77b4",
        )
        ax1.set_ylabel("Power (dBFS)", color="#1f77b4")
        ax1.tick_params(axis="y", labelcolor="#1f77b4")

        # Fixed headroom (25% of range, min 3 units) so annotate offsets never run out of room.
        power_range = max(power_max - power_min, 1.0)
        ax1.set_ylim(power_bottom, power_max + power_range * 0.35)
        # Fixed tick interval on the power axis.
        ax1.yaxis.set_major_locator(MultipleLocator(max(1, round(power_range / 5))))

        ax2 = ax1.twinx()
        ax2.plot(x, snr, color="#ff7f0e", marker="o", linewidth=2, label="SNR (dB)")
        ax2.set_ylabel("SNR (dB)", color="#ff7f0e")
        ax2.tick_params(axis="y", labelcolor="#ff7f0e")

        snr_min, snr_max = min(snr), max(snr)
        snr_range = max(snr_max - snr_min, 1.0)
        ax2.set_ylim(snr_min - snr_range * 0.2, snr_max + snr_range * 0.35)
        ax2.yaxis.set_major_locator(MultipleLocator(max(1, round(snr_range / 5))))

        # Annotate power values: fixed pixel offset above each bar, with a readable background.
        for rect, val in zip(bars_power, power):
            ax1.annotate(
                f"{val:.1f}",
                xy=(rect.get_x() + rect.get_width() / 2, val),
                xytext=(0, 6),
                textcoords="offset points",
                ha="center", va="bottom", fontsize=8, color="#1f77b4",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75),
                clip_on=False,
            )

        # Annotate SNR values: fixed pixel offset above each point, offset further up
        # so it never lands where a power label sits.
        for xi, val in zip(x, snr):
            ax2.annotate(
                f"{val:.1f}",
                xy=(xi, val),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center", va="bottom", fontsize=8, color="#ff7f0e",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75),
                clip_on=False,
            )

        def _short_time(t):
            if isinstance(t, str) and len(t) >= 16:
                return t[11:16]
            return str(t)

        time_labels = [_short_time(t) for t in timestamps]
        ax1.set_xticks(x)
        ax1.set_xticklabels(time_labels, rotation=45, ha="right")
        ax1.set_xlabel("Vaxt (HH:MM)")
        ax1.set_xlim(-0.5, len(x) - 0.5)

        sdr_label = _get_sdr_label(sdr_id)
        title = f"{sdr_label} | {rtys_name} | {freq_mhz:g} MHz | {station_name}"
        ax1.set_title(title, fontsize=13, fontweight="bold")

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")

        ax1.grid(axis="y", linestyle="--", alpha=0.4)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        plt.close(fig)
        buf.seek(0)
        return _b64.b64encode(buf.read()).decode("ascii")
    except Exception:
        print("[drop_plot] error:")
        traceback.print_exc()
        return None


def _send_drop_alert(event):
    """Build and send a single WhatsApp drop alert with an optional plot."""
    duration = event.get("duration_sec", 0) or 0
    sdr_id = event.get("sdr_id") or ""
    raw_freq = round(float(event.get("freq", 0)), 3)
    # Resolve to the closest configured station so the message shows the exact
    # licensed frequency and correct station name, not the raw SDR reading.
    exact_freq, station_name, rtys_name = _resolve_station(sdr_id, raw_freq)
    # Hard guard: only the configured main RTYS can trigger WhatsApp alerts.
    # This also catches Unknown and any test call that bypasses _notify_main_drops.
    main_rtys = _MAIN_RTYS_BY_SDR.get(sdr_id)
    if main_rtys and rtys_name != main_rtys:
        print(f"[drop_alert] blocked non-main RTYS for {sdr_id}: {rtys_name} (freq {raw_freq})")
        return None
    end_ts = event.get("ts") or time.time()
    start_ts = end_ts - duration

    start_str = _dt.fromtimestamp(start_ts).strftime("%H:%M:%S")
    end_str = _dt.fromtimestamp(end_ts).strftime("%H:%M:%S")
    date_str = _dt.fromtimestamp(end_ts).strftime("%y/%m/%d")
    dur_str = _format_hhmmss(duration)

    msg = (
        "Siqnal səviyyəsində düşmə qeydə alındı\n"
        f"SDR: {sdr_id}\n"
        f"RTYS: {rtys_name}\n"
        f"Tezlik: {exact_freq} MHz - {station_name}\n"
        f"Müddət: {start_str} - {end_str} - {dur_str}\n"
        f"Tarix: {date_str}\n"
        "example.com"
    )

    try:
        rows = _drop_rows_sync(sdr_id, raw_freq, start_ts, end_ts)
        image_b64 = _generate_drop_plot_base64(rows, sdr_id, exact_freq, rtys_name, station_name)
    except Exception:
        print("[drop_alert] plot error:")
        traceback.print_exc()
        image_b64 = None

    def _send():
        try:
            if image_b64:
                # Image caption already contains the full drop message.
                green_api.send_image_to_all(image_b64, caption=msg)
            else:
                green_api.send_test(message=msg)
        except Exception:
            print("[drop_alert] send error:")
            traceback.print_exc()

    threading.Thread(target=_send, daemon=True, name="drop-whatsapp-alert").start()
    return msg


def _notify_main_drops(events):
    """Send WhatsApp alerts for new sustained drops."""
    global _last_drop_alert_any, _last_drop_alert_any_cooldown
    if not _DROP_ALERTS_ENABLED:
        return
    if not green_api.is_configured():
        return
    now = time.time()
    for e in events:
        duration = e.get("duration_sec", 0) or 0
        if duration < _DROP_ALERT_MIN_DURATION_SEC:
            continue
        end_ts = e.get("ts") or now
        # Only alert for recent drops (cache refreshes every ~5 min; 15 min window is safe).
        if end_ts < now - 900:
            continue
        sdr_id = e.get("sdr_id") or ""
        freq = round(float(e.get("freq", 0)), 3)
        if (sdr_id, round(freq, 1)) in _DROP_ALERT_EXCLUDE:
            continue
        # Only the SDR's "main" RTYS (primary transmitter) should trigger alerts.
        # Resolve to the closest configured station so SDR drift does not flip the
        # alert to a secondary/remote transmitter.
        _, _, rtys_name = _resolve_station(sdr_id, freq)
        main_rtys = _MAIN_RTYS_BY_SDR.get(sdr_id)
        if main_rtys and rtys_name != main_rtys:
            continue
        # Random global cooldown: never send a new alert before the random
        # cooldown from any previous one.
        if _last_drop_alert_any_cooldown is None:
            _last_drop_alert_any_cooldown = random.randint(
                int(_DROP_ALERT_COOLDOWN_MIN_SEC),
                int(_DROP_ALERT_COOLDOWN_MAX_SEC),
            ) + random.random()
        if now - _last_drop_alert_any < _last_drop_alert_any_cooldown:
            continue
        key = (sdr_id, freq)
        last_alert = _last_drop_alert_ts.get(key, 0)
        cooldown = _last_drop_alert_cooldown.get(key)
        if cooldown is None:
            cooldown = random.randint(
                int(_DROP_ALERT_COOLDOWN_MIN_SEC),
                int(_DROP_ALERT_COOLDOWN_MAX_SEC),
            ) + random.random()
            _last_drop_alert_cooldown[key] = cooldown
        if now - last_alert < cooldown:
            continue

        _send_drop_alert(e)

        _last_drop_alert_ts[key] = now
        _last_drop_alert_any = now
        _last_drop_alert_cooldown[key] = random.randint(
            int(_DROP_ALERT_COOLDOWN_MIN_SEC),
            int(_DROP_ALERT_COOLDOWN_MAX_SEC),
        ) + random.random()
        _last_drop_alert_any_cooldown = random.randint(
            int(_DROP_ALERT_COOLDOWN_MIN_SEC),
            int(_DROP_ALERT_COOLDOWN_MAX_SEC),
        ) + random.random()


def _test_random_drop_alert():
    """Pick a random sustained drop from the last week and send a test alert."""
    events = _drops_sync(
        168.0,  # 1 week
        _DROPS_DEFAULT_THRESHOLD,
        _DROPS_DEFAULT_MIN_DURATION,
        _DROPS_DEFAULT_MAX_GAP,
        False,
    )
    candidates = [e for e in events if (e.get("duration_sec", 0) or 0) >= _DROP_ALERT_MIN_DURATION_SEC]
    main_candidates = []
    for e in candidates:
        sdr_id = e.get("sdr_id") or ""
        freq = round(float(e.get("freq", 0)), 3)
        main_rtys = _MAIN_RTYS_BY_SDR.get(sdr_id)
        if not main_rtys:
            continue
        _, _, rtys_name = _resolve_station(sdr_id, freq)
        if rtys_name == main_rtys:
            main_candidates.append(e)
    if not main_candidates:
        return {"ok": False, "detail": "No main-RTYS drops found"}
    event = random.choice(main_candidates)
    _send_drop_alert(event)
    return {"ok": True, "event": event}


def _drops_cache_loop():
    global _drops_cache, _drops_calibrated_cache, _drops_calibrated_60_cache
    time.sleep(5)  # let DB warm up first
    while True:
        try:
            result = _drops_sync(
                _DROPS_DEFAULT_HOURS,
                _DROPS_DEFAULT_THRESHOLD,
                _DROPS_DEFAULT_MIN_DURATION,
                _DROPS_DEFAULT_MAX_GAP,
                False,
            )
            with _DROPS_CACHE_LOCK:
                _drops_cache = result
            _notify_main_drops(result)
        except Exception:
            print("[drops_cache] error:")
            traceback.print_exc()
        try:
            # Home ticker asks for min_duration=60, overview asks for 180. Compute the
            # 60s calibrated set once; 180s events are exactly the 60s events whose
            # duration is >= 180s, so derive that cache instead of running twice.
            result_cal_60 = _drops_sync(
                _DROPS_DEFAULT_HOURS,
                _DROPS_DEFAULT_THRESHOLD,
                _DROPS_CAL_60_MIN_DURATION,
                _DROPS_DEFAULT_MAX_GAP,
                True,
            )
            result_cal_180 = [e for e in result_cal_60 if e.get("duration_sec", 0) >= _DROPS_DEFAULT_MIN_DURATION]
            with _DROPS_CACHE_LOCK:
                _drops_calibrated_60_cache = result_cal_60
                _drops_calibrated_cache = result_cal_180
        except Exception:
            print("[drops_calibrated_cache] error:")
            traceback.print_exc()
        time.sleep(_DROPS_REFRESH_SEC)


_drops_cache_thread = threading.Thread(target=_drops_cache_loop, daemon=True, name="drops-cache")


@app.get("/api/drops")
async def get_drops(
    hours: float = _DROPS_DEFAULT_HOURS,
    threshold: float = _DROPS_DEFAULT_THRESHOLD,
    min_duration: float = _DROPS_DEFAULT_MIN_DURATION,
    max_gap: float = _DROPS_DEFAULT_MAX_GAP,
    use_calibrated: bool = False,
    _user=Depends(get_current_user),
):
    # The two front-end consumers (home ticker and Overview tab) hit these exact
    # defaults. Serve them from the background cache; only run _drops_sync on
    # unusual combinations so the tab never waits for a multi-second scan.
    cached = None
    if (
        hours == _DROPS_DEFAULT_HOURS
        and threshold == _DROPS_DEFAULT_THRESHOLD
        and max_gap == _DROPS_DEFAULT_MAX_GAP
    ):
        with _DROPS_CACHE_LOCK:
            if use_calibrated:
                if min_duration == _DROPS_DEFAULT_MIN_DURATION:
                    cached = _drops_calibrated_cache
                elif min_duration == _DROPS_CAL_60_MIN_DURATION:
                    cached = _drops_calibrated_60_cache
            else:
                if min_duration == _DROPS_DEFAULT_MIN_DURATION:
                    cached = _drops_cache
    if cached is not None:
        return cached
    # Only fall back to on-the-fly calculation for non-default parameters.
    # The default UI parameters are always satisfied by the background cache loop.
    # If the cache is not ready yet, return empty instead of blocking a request
    # with a multi-second _drops_sync scan on 50M rows.
    if (
        hours == _DROPS_DEFAULT_HOURS
        and threshold == _DROPS_DEFAULT_THRESHOLD
        and max_gap == _DROPS_DEFAULT_MAX_GAP
        and (min_duration == _DROPS_DEFAULT_MIN_DURATION or min_duration == _DROPS_CAL_60_MIN_DURATION)
    ):
        return []
    return await _run_in_thread(_drops_sync, hours, threshold, min_duration, max_gap, use_calibrated)


def _drops_sync(hours, threshold, min_duration, max_gap, use_calibrated=False):
    db = SessionLocal()
    try:
        since = _dt.utcnow() - timedelta(hours=hours)
        power_db_threshold = _calibrated_max_threshold() if use_calibrated else threshold
        snr_db_threshold = _calibrated_max_snr_threshold() if use_calibrated else _DROPS_DEFAULT_SNR_THRESHOLD
        # Stream rows ordered by SDR/frequency/timestamp so we can process one group
        # at a time.  Previously every row for the whole window was held in a dict,
        # which could consume several GB when the DB grows.
        query = (
            db.query(SweepData)
            .with_entities(
                SweepData.id,
                SweepData.timestamp,
                SweepData.frequency_mhz,
                SweepData.power_dbfs,
                SweepData.floor_dbfs,
                SweepData.snr_db,
                SweepData.sdr_id,
            )
            .filter(SweepData.timestamp >= since)
        )
        if use_calibrated:
            query = query.filter(
                or_(
                    and_(SweepData.snr_db.isnot(None), SweepData.snr_db < snr_db_threshold),
                    and_(SweepData.snr_db.is_(None), SweepData.power_dbfs < power_db_threshold)
                )
            )
        else:
            query = query.filter(SweepData.power_dbfs < power_db_threshold)
        # Order by sdr_id then the same MHz normalisation _freq_mhz uses, so the
        # streaming grouping below never splits one logical frequency group.
        query = query.order_by(
            text(
                "sdr_id, "
                "CASE WHEN frequency_mhz >= 1000 THEN ROUND(frequency_mhz / 1000.0, 3) "
                "ELSE ROUND(frequency_mhz, 3) END, "
                "timestamp"
            )
        ).yield_per(5000)

        events = []

        def _process_group(group_rows):
            if not group_rows:
                return
            first = group_rows[0]
            sdr_id = first.sdr_id or ""
            freq = _freq_mhz(first.frequency_mhz)

            group_threshold = threshold
            snr_threshold = _DROPS_DEFAULT_SNR_THRESHOLD
            if use_calibrated:
                rtys = _get_rtys_for_freq(sdr_id, freq)
                group_threshold = _get_calibrated_threshold(sdr_id, rtys)
                snr_threshold = _get_calibrated_snr_threshold(sdr_id, rtys)

            # FM is swept across the band (one sample per freq every ~minutes),
            # DAB is monitored continuously (samples every ~second). Adapt the
            # gap/duration rules to the actual cadence for this frequency so
            # slow sweeps still form sustained runs.
            timestamps = [r.timestamp for r in group_rows if r.timestamp is not None]
            if len(timestamps) >= 2:
                gaps = sorted(
                    (timestamps[i + 1] - timestamps[i]).total_seconds()
                    for i in range(len(timestamps) - 1)
                )
                median_gap = gaps[len(gaps) // 2]
            else:
                median_gap = 0.0
            effective_max_gap = max(max_gap, median_gap * 1.5) if median_gap > max_gap else max_gap
            effective_min_duration = max(min_duration, median_gap)

            run_start = None
            run_end = None
            run_min_floor = None
            run_min_snr = None

            def finish_run():
                nonlocal run_start, run_end, run_min_floor, run_min_snr
                if run_start is None or run_end is None:
                    run_start = None
                    run_end = None
                    run_min_floor = None
                    run_min_snr = None
                    return
                dur = (run_end.timestamp - run_start.timestamp).total_seconds()
                if dur >= effective_min_duration:
                    events.append({
                        "id": run_end.id,
                        "sdr_id": sdr_id,
                        "freq": freq,
                        "ts": run_end.timestamp.timestamp(),
                        "dbfs": round(run_end.power_dbfs, 1),
                        "duration_sec": round(dur, 1),
                        "floor_dbfs": round(run_min_floor, 1) if run_min_floor is not None else None,
                        "snr_db": round(run_min_snr, 1) if run_min_snr is not None else None,
                    })
                run_start = None
                run_end = None
                run_min_floor = None
                run_min_snr = None

            for r in group_rows:
                if r.timestamp is None or r.power_dbfs is None:
                    continue
                if r.snr_db is not None:
                    is_drop = r.snr_db < snr_threshold
                else:
                    is_drop = r.power_dbfs < group_threshold
                if is_drop:
                    if run_start is None:
                        run_start = r
                        run_end = r
                        run_min_floor = r.floor_dbfs
                        run_min_snr = r.snr_db
                    elif (r.timestamp - run_end.timestamp).total_seconds() > effective_max_gap:
                        finish_run()
                        run_start = r
                        run_end = r
                        run_min_floor = r.floor_dbfs
                        run_min_snr = r.snr_db
                    else:
                        run_end = r
                        if r.floor_dbfs is not None and (run_min_floor is None or r.floor_dbfs < run_min_floor):
                            run_min_floor = r.floor_dbfs
                        if r.snr_db is not None and (run_min_snr is None or r.snr_db < run_min_snr):
                            run_min_snr = r.snr_db
                else:
                    if run_start is not None:
                        finish_run()
            if run_start is not None:
                finish_run()

        current_key = None
        group_rows = []
        for r in query:
            key = (r.sdr_id or "", _freq_mhz(r.frequency_mhz))
            if current_key is None or key == current_key:
                group_rows.append(r)
            else:
                _process_group(group_rows)
                group_rows = [r]
            current_key = key
        if group_rows:
            _process_group(group_rows)

        events.sort(key=lambda x: x["ts"], reverse=True)
        print(f"[_drops_sync] use_calibrated={use_calibrated} min_duration={min_duration} events={len(events)}")
        return events
    finally:
        db.close()


@app.get("/api/drop_rows")
async def get_drop_rows(
    sdr_id: str,
    freq: float,
    start_ts: float,
    end_ts: float,
    _user=Depends(get_current_user),
):
    return await _run_in_thread(_drop_rows_sync, sdr_id, freq, start_ts, end_ts)


def _drop_rows_sync(sdr_id, freq, start_ts, end_ts):
    db = SessionLocal()
    try:
        freq_mhz = round(float(freq), 3)
        # rf_sweep_data timestamps are stored as local naive datetimes (from datetime.now()),
        # so use fromtimestamp (local) rather than utcfromtimestamp to match the column.
        start = _dt.fromtimestamp(start_ts - 2)
        end = _dt.fromtimestamp(end_ts + 2)
        tol_mhz = 0.05
        tol_khz = tol_mhz * 1000
        low_mhz = freq_mhz - tol_mhz
        high_mhz = freq_mhz + tol_mhz
        low_khz = (freq_mhz * 1000) - tol_khz
        high_khz = (freq_mhz * 1000) + tol_khz

        def _run(select_floor):
            floor_col = "floor_dbfs," if select_floor else "NULL as floor_dbfs,"
            sql = text(f"""
                SELECT id, timestamp, frequency_mhz, power_dbfs, {floor_col} snr_db, sdr_id
                FROM rf_sweep_data
                WHERE sdr_id = :sdr_id
                  AND timestamp BETWEEN :start AND :end
                  AND (frequency_mhz BETWEEN :low_mhz AND :high_mhz OR frequency_mhz BETWEEN :low_khz AND :high_khz)
                ORDER BY timestamp
                LIMIT 2000
            """)
            return db.execute(sql, {
                'sdr_id': sdr_id,
                'start': start,
                'end': end,
                'low_mhz': low_mhz,
                'high_mhz': high_mhz,
                'low_khz': low_khz,
                'high_khz': high_khz,
            }).mappings().all()

        try:
            rows = _run(True)
        except Exception as e:
            print(f"[drop_rows] floor_dbfs select failed ({e}), retrying without it", flush=True)
            rows = _run(False)

        # Diagnostic: how many rows exist for this SDR/time window regardless of frequency?
        diag_sql = text("""
            SELECT COUNT(*) as cnt FROM rf_sweep_data
            WHERE sdr_id = :sdr_id AND timestamp BETWEEN :start AND :end
        """)
        diag = db.execute(diag_sql, {'sdr_id': sdr_id, 'start': start, 'end': end}).mappings().first()
        print(f"[drop_rows] sdr_id={sdr_id} freq={freq_mhz} start={start} end={end} rows={len(rows)} total_in_window={diag['cnt'] if diag else '?'}", flush=True)
        return [
            {
                "id": r['id'],
                "timestamp": r['timestamp'].isoformat().replace("T", " ") if r['timestamp'] else None,
                "frequency_mhz": r['frequency_mhz'],
                "power_dbfs": round(r['power_dbfs'], 2) if r['power_dbfs'] is not None else None,
                "floor_dbfs": round(r['floor_dbfs'], 2) if r['floor_dbfs'] is not None else None,
                "snr_db": round(r['snr_db'], 2) if r['snr_db'] is not None else None,
                "sdr_id": r['sdr_id'] or "",
            }
            for r in rows
        ]
    finally:
        db.close()


@app.get("/api/drop_rows_debug")
async def get_drop_rows_debug(
    sdr_id: str,
    start_ts: float,
    end_ts: float,
    limit: int = 20,
    _user=Depends(get_current_user),
):
    return await _run_in_thread(_drop_rows_debug_sync, sdr_id, start_ts, end_ts, limit)


def _drop_rows_debug_sync(sdr_id, start_ts, end_ts, limit):
    db = SessionLocal()
    try:
        start = _dt.fromtimestamp(start_ts)
        end = _dt.fromtimestamp(end_ts)
        sql = text("""
            SELECT id, timestamp, frequency_mhz, power_dbfs, snr_db, sdr_id
            FROM rf_sweep_data
            WHERE sdr_id = :sdr_id AND timestamp BETWEEN :start AND :end
            ORDER BY timestamp
            LIMIT :limit
        """)
        rows = db.execute(sql, {'sdr_id': sdr_id, 'start': start, 'end': end, 'limit': limit}).mappings().all()
        return [
            {
                "id": r['id'],
                "timestamp": r['timestamp'].isoformat().replace("T", " ") if r['timestamp'] else None,
                "frequency_mhz": r['frequency_mhz'],
                "power_dbfs": round(r['power_dbfs'], 2) if r['power_dbfs'] is not None else None,
                "snr_db": round(r['snr_db'], 2) if r['snr_db'] is not None else None,
                "sdr_id": r['sdr_id'] or "",
            }
            for r in rows
        ]
    finally:
        db.close()


@app.get("/api/sdr/list")
async def sdr_list(_user=Depends(get_current_user)):
    """Return list of SDR configs from sdr_config.json, with per-region thresholds."""
    sdrs = _SDR_CONFIG.get("sdrs", [])
    return [
        {
            "id": s["id"],
            "label": s.get("label", s["id"]),
            "stations": s.get("stations", []),
            "thresholds": _SDR_THRESHOLDS.get(s["id"], None),
        }
        for s in sdrs
    ]


@app.get("/api/sdr/{sdr_id}/telemetry")
async def sdr_telemetry(
    sdr_id: str,
    freqs: str = "",
    window: str = "24h",
    history_freq: float | None = None,
    _user=Depends(get_current_user),
):
    windows = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}
    targets = []
    for value in freqs.split(","):
        try:
            targets.append(int(round(float(value.strip()) * 1000)))
        except ValueError:
            pass
    if not targets:
        return {"sdr_id": sdr_id, "window": window, "signals": [], "history": []}

    seconds = windows.get(window, windows["24h"])
    db = SessionLocal()
    try:
        stats_rows = db.execute(text("""
            SELECT frequency_mhz, MIN(power_dbfs) AS min_power, MAX(power_dbfs) AS max_power, COUNT(*) AS samples
            FROM rf_sweep_data
            WHERE sdr_id = :sid
              AND frequency_mhz = ANY(:freqs)
              AND timestamp >= NOW() - (:seconds * INTERVAL '1 second')
            GROUP BY frequency_mhz
        """), {"sid": sdr_id, "freqs": targets, "seconds": seconds}).fetchall()
        latest_rows = db.execute(text("""
            SELECT DISTINCT ON (frequency_mhz) frequency_mhz, timestamp, power_dbfs, snr_db
            FROM rf_sweep_data
            WHERE sdr_id = :sid
              AND frequency_mhz = ANY(:freqs)
            ORDER BY frequency_mhz, timestamp DESC, id DESC
        """), {"sid": sdr_id, "freqs": targets}).fetchall()

        stats = {int(row.frequency_mhz): row for row in stats_rows}
        signals = []
        for row in latest_rows:
            freq_khz = int(row.frequency_mhz)
            aggregate = stats.get(freq_khz)
            power = float(row.power_dbfs)
            snr = float(row.snr_db) if row.snr_db is not None else None
            signals.append({
                "freq": round(freq_khz / 1000.0, 3),
                "power": round(power, 2),
                "min": round(float(aggregate.min_power), 2) if aggregate else power,
                "peak": round(float(aggregate.max_power), 2) if aggregate else power,
                "snr": round(snr, 2) if snr is not None else None,
                "noise_floor": round(power - snr, 2) if snr is not None else None,
                "samples": int(aggregate.samples) if aggregate else 1,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            })

        history = []
        if history_freq is not None:
            history_khz = int(round(history_freq * 1000))
            history_rows = db.execute(text("""
                SELECT timestamp, power_dbfs, snr_db
                FROM rf_sweep_data
                WHERE sdr_id = :sid
                  AND frequency_mhz = :freq
                  AND timestamp >= NOW() - (:seconds * INTERVAL '1 second')
                ORDER BY timestamp DESC
                LIMIT 120
            """), {"sid": sdr_id, "freq": history_khz, "seconds": seconds}).fetchall()
            history = [
                {
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                    "power": round(float(row.power_dbfs), 2),
                    "snr": round(float(row.snr_db), 2) if row.snr_db is not None else None,
                    "noise_floor": round(float(row.power_dbfs) - float(row.snr_db), 2) if row.snr_db is not None else None,
                }
                for row in reversed(history_rows)
            ]

        return {"sdr_id": sdr_id, "window": window, "signals": signals, "history": history}
    finally:
        db.close()


@app.get("/api/sdr/{sdr_id}/peaks")
async def sdr_peaks(sdr_id: str, freqs: str = "", _user=Depends(get_current_user)):
    """Return latest sigint peaks for a specific SDR.
    
    Optional ?freqs=88.7,90.0,... (MHz, comma-separated) allows the frontend
    to request any RTYS-specific frequencies that may not be in sdr_config.
    When freqs is supplied the cache is bypassed and a live query is run.
    """
    extra: list[int] = []
    if freqs:
        for f in freqs.split(","):
            try:
                extra.append(int(round(float(f.strip()) * 1000)))
            except ValueError:
                pass
    if extra:
        return await _run_in_thread(_fetch_sdr_peaks, sdr_id, extra)
    cached = _peaks_cache.get(sdr_id)
    if cached is not None:
        return cached
    # Fallback: first request before cache is warm
    return await _run_in_thread(_fetch_sdr_peaks, sdr_id)


# ── Background peaks cache ────────────────────────────────────────────────────
_peaks_cache: dict = {}  # sdr_id -> response dict
_PEAKS_REFRESH_SEC = float(_env('PEAKS_REFRESH_SEC', '5.0'))


def _fetch_sdr_peaks(sdr_id: str, extra_freqs: list = None):
    """Fetch latest peak per station frequency for a specific SDR.

    extra_freqs: optional list of kHz ints to query IN ADDITION to (or instead
    of) the sdr_config station list.  This is used by the live RTYS-scoped
    endpoint so the frontend can always get real data for every RTYS tab,
    even if sdr_config.json doesn't enumerate that RTYS.
    """
    cfg = _get_sdr_config(sdr_id)
    stations = cfg.get("stations", []) if cfg else []

    # Build the complete set of kHz targets
    freq_khz_set: set[int] = set()
    for st in stations:
        if st.get("dab") and st.get("dab_freq_khz"):
            freq_khz_set.add(int(st["dab_freq_khz"]))
        else:
            freq_khz_set.add(int(round(st["freq"] * 1000)))
    if extra_freqs:
        freq_khz_set.update(extra_freqs)

    db = SessionLocal()
    try:
        peaks = []
        if freq_khz_set:
            freq_khz_list = list(freq_khz_set)
            # Single query: latest row per exact frequency for this SDR
            # Uses the covering index (sdr_id, frequency_mhz, id DESC)
            rows = db.execute(
                text("""
                    SELECT DISTINCT ON (frequency_mhz)
                        id, timestamp, frequency_mhz, power_dbfs, snr_db
                    FROM rf_sweep_data
                    WHERE sdr_id = :sid
                      AND frequency_mhz = ANY(:freqs)
                    ORDER BY frequency_mhz, id DESC
                """),
                {"sid": sdr_id, "freqs": freq_khz_list},
            ).fetchall()

            for r in rows:
                freq_mhz = r.frequency_mhz / 1000.0
                peaks.append({
                    "freq": round(freq_mhz, 1),
                    "dbfs": round(float(r.power_dbfs), 1),
                    "ts": r.timestamp.timestamp() if r.timestamp else None,
                    "sdr_id": sdr_id,
                })
        else:
            # Fallback: no config at all — get recent distinct frequencies
            rows = db.execute(
                text("""
                    SELECT DISTINCT ON (frequency_mhz)
                        id, timestamp, frequency_mhz, power_dbfs, snr_db
                    FROM rf_sweep_data
                    WHERE sdr_id = :sid
                      AND id > (SELECT COALESCE(MAX(id) - 50000, 0) FROM rf_sweep_data WHERE sdr_id = :sid)
                    ORDER BY frequency_mhz, id DESC
                """),
                {"sid": sdr_id},
            ).fetchall()

            for r in rows:
                freq_mhz = r.frequency_mhz / 1000.0
                peaks.append({
                    "freq": round(freq_mhz, 1),
                    "dbfs": round(float(r.power_dbfs), 1),
                    "ts": r.timestamp.timestamp() if r.timestamp else None,
                    "sdr_id": sdr_id,
                })

        return {"sdr_id": sdr_id, "peaks": peaks}
    finally:
        db.close()


def _peaks_cache_loop():
    """Background thread: refresh peaks for all configured SDRs every N seconds."""
    time.sleep(3)  # Wait for DB to be ready on startup
    while True:
        try:
            # Skip if pool is under heavy pressure
            pool = engine.pool
            if hasattr(pool, 'checkedout') and pool.checkedout() >= pool.size() - 5:
                time.sleep(_PEAKS_REFRESH_SEC)
                continue
            sdrs = _SDR_CONFIG.get("sdrs", [])
            for s in sdrs:
                sid = s["id"]
                try:
                    _peaks_cache[sid] = _fetch_sdr_peaks(sid)
                except Exception as e:
                    print(f"[peaks_cache] error for {sid}: {e}")
                    time.sleep(2)  # Back off on error
                time.sleep(0.5)  # Spread load between SDRs
        except Exception as e:
            print(f"[peaks_cache] loop error: {e}")
        time.sleep(_PEAKS_REFRESH_SEC)


_peaks_cache_thread = threading.Thread(target=_peaks_cache_loop, daemon=True, name="peaks-cache")


_EXPORT_ROW_LIMIT = 50_000  # hard cap to prevent full-DB dumps

@app.get("/api/export")
async def export_csv(from_ts: float = 0.0, to_ts: float = 0.0, _user=Depends(get_current_user)):
    import csv, io, asyncio
    from datetime import datetime as dt
    from fastapi.responses import Response

    def _build_csv():
        from datetime import timezone, timedelta
        db = SessionLocal()
        try:
            q = db.query(SweepData).order_by(SweepData.timestamp)
            # rf_sweep_data timestamps are stored as local naive datetimes.
            if from_ts > 0:
                q = q.filter(SweepData.timestamp >= dt.fromtimestamp(from_ts))
            if to_ts > 0:
                q = q.filter(SweepData.timestamp <= dt.fromtimestamp(to_ts))
            rows = q.limit(_EXPORT_ROW_LIMIT).all()
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "timestamp_local", "sdr_id", "frequency_mhz", "power_dbfs", "floor_dbfs", "snr_db"])
            for r in rows:
                # The stored timestamp is already local; format it directly.
                local_ts = r.timestamp or dt.fromtimestamp(0)
                w.writerow([
                    r.id,
                    local_ts.strftime("%Y-%m-%d %H:%M:%S"),
                    r.sdr_id or '',
                    round(r.frequency_mhz / 1000.0, 3),
                    round(r.power_dbfs, 1),
                    round(r.floor_dbfs, 1) if r.floor_dbfs is not None else '',
                    round(r.snr_db, 1) if r.snr_db is not None else '',
                ])
            return buf.getvalue()
        finally:
            db.close()

    csv_data = await _run_in_thread(_build_csv)
    return Response(
        content=csv_data.encode('utf-8-sig'),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=cgrep_export.csv"},
    )


# ---------------------------------------------------------------------------
# Telemetry cache — refreshed by a background thread every 15 s
# ---------------------------------------------------------------------------
_telemetry_cache: dict | None = None
_TELEMETRY_REFRESH_SEC = float(_env('TELEMETRY_REFRESH_SEC', '15.0'))


def _telemetry_cache_loop():
    global _telemetry_cache
    time.sleep(5)  # let DB warm up first
    while True:
        try:
            _telemetry_cache = _telemetry_sync()
        except Exception as e:
            print(f"[telemetry_cache] error: {e}")
        time.sleep(_TELEMETRY_REFRESH_SEC)


_telemetry_cache_thread = threading.Thread(target=_telemetry_cache_loop, daemon=True, name="telemetry-cache")


@app.get("/api/telemetry")
async def get_telemetry(_user=Depends(get_current_user)):
    data = _telemetry_cache if _telemetry_cache is not None else await _run_in_thread(_telemetry_sync)
    return JSONResponse(
        data,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


def _telemetry_sync():
    """Run telemetry collection in a thread to avoid blocking the event loop."""
    cpu = psutil.cpu_percent(interval=None)  # non-blocking (returns cached value)
    ram = psutil.virtual_memory().percent
    db_mb = 0.0
    active_users = 0
    db = SessionLocal()
    try:
        result = db.execute(
            __import__('sqlalchemy').text("SELECT pg_database_size('sdr_db') / (1024.0 * 1024.0)")
        ).scalar()
        db_mb = round(float(result), 2)
        since = _dt.utcnow() - timedelta(minutes=5)
        active_users = db.execute(
            text("SELECT COUNT(DISTINCT user_id) FROM device_sessions WHERE last_seen >= :since"),
            {"since": since},
        ).scalar() or 0
    except Exception:
        db_mb = 0.0
    finally:
        db.close()

    # Requests per second (window average)
    now = time.time()
    with _request_lock:
        cutoff = now - _REQUEST_WINDOW_SEC
        while _request_times and _request_times[0] < cutoff:
            _request_times.popleft()
        requests_per_sec = len(_request_times) / _REQUEST_WINDOW_SEC

    # Network I/O in Mbps
    net_io_mbps = 0.0
    try:
        counters = psutil.net_io_counters()
        total_bytes = counters.bytes_sent + counters.bytes_recv
        with _net_io_lock:
            global _last_net_io
            if _last_net_io is None:
                _last_net_io = (total_bytes, now)
            else:
                last_total, last_t = _last_net_io
                delta = max(now - last_t, 0.001)
                bytes_per_sec = (total_bytes - last_total) / delta
                net_io_mbps = (bytes_per_sec * 8) / (1024.0 * 1024.0)
                _last_net_io = (total_bytes, now)
    except Exception:
        net_io_mbps = 0.0

    # SDR nodes, DB and WhatsApp status, 24h drop counter
    sdr_nodes = []
    sdr_status = False
    db_status = False
    whatsapp_status = False
    drops_24h = 0
    try:
        # WhatsApp status now reflects whether Green API credentials are configured
        whatsapp_status = green_api.is_configured()

        db2 = SessionLocal()
        try:
            now_local = _dt.now()  # timestamps stored as local time by collector
            since_60s = now_local - timedelta(seconds=60)
            since_24h = now_local - timedelta(hours=24)

            latest_row = db2.execute(
                text("SELECT MAX(timestamp) AS ts, EXTRACT(EPOCH FROM (NOW() - MAX(timestamp))) AS age_sec FROM rf_sweep_data")
            ).fetchone()
            latest_sweep = latest_row[0] if latest_row else None
            latest_age_sec = latest_row[1] if latest_row else None
            db_status = latest_sweep is not None and latest_age_sec is not None and latest_age_sec < 300

            online_threshold_sec = 120

            last_seen_rows = db2.execute(
                text("SELECT sdr_id, MAX(timestamp) AS ts, EXTRACT(EPOCH FROM (NOW() - MAX(timestamp))) AS age_sec FROM rf_sweep_data WHERE sdr_id IS NOT NULL GROUP BY sdr_id")
            ).fetchall()
            last_seen_map = {row[0]: row[1].timestamp() if row[1] else None for row in last_seen_rows}

            active_set = set()
            for sdr_id, last_ts, age_sec in last_seen_rows:
                if not last_ts:
                    continue
                if age_sec < online_threshold_sec:
                    active_set.add(sdr_id)

            all_ids = _get_all_sdr_ids(db2)
            sdr_nodes = [{"id": sdr_id, "online": sdr_id in active_set, "last_seen": last_seen_map.get(sdr_id)} for sdr_id in all_ids]
            sdr_status = bool(active_set)

            drops_24h = db2.execute(
                text("SELECT COUNT(*) FROM rf_sweep_data WHERE power_dbfs < -65 AND timestamp >= :since"),
                {"since": since_24h},
            ).scalar() or 0
        finally:
            db2.close()
    except Exception:
        pass

    return {
        "cpu": cpu,
        "ram": ram,
        "db_mb": db_mb,
        "backup_local_mb": _backup_state['local_size'],
        "backup_r2_mb":    _backup_state['r2_size'],
        "backup_last_local": _backup_state['last_local'],
        "backup_last_r2":    _backup_state['last_r2'],
        "backup_running":    _backup_state['running'],
        "backup_error":      _backup_state['last_error'],
        "r2_configured":     bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY),
        "active_users":      active_users,
        "batch_requests_per_sec": round(requests_per_sec, 1),
        "network_io_mbps":   round(net_io_mbps, 2),
        "sdr_status":        sdr_status,
        "sdr_nodes":         sdr_nodes,
        "db_status":         db_status,
        "whatsapp_status":   whatsapp_status,
        "drops_24h":         int(drops_24h),
    }


@app.post("/api/backup/run")
async def trigger_backup(
    request: Request,
    background_tasks: BackgroundTasks,
    _user: User = Depends(require_permission('trigger:backup')),
):
    if _backup_state['running']:
        return {"status": "already_running"}
    threading.Thread(target=_run_backup, daemon=True).start()
    _ip = get_client_ip(request)
    _db = SessionLocal()
    background_tasks.add_task(
        log_audit_event, _db, 'BACKUP_TRIGGERED', 'System', _ip,
        actor_id=_user.id, details={'trigger': 'manual'},
    )
    return {"status": "started"}


@app.websocket("/ws/sdr")
async def sdr_stream(websocket: WebSocket):
    # Validate Origin header to prevent CSWSH
    origin = websocket.headers.get('origin', '')
    if origin not in [_FRONTEND_ORIGIN, 'https://example.com', 'http://localhost:8000']:
        await websocket.close(code=4003, reason='Invalid Origin')
        return
    
    if not os.environ.get('DRI_TESTMODE'):
        token = websocket.cookies.get(_COOKIE_NAME)
        if not token or not _decode_jwt(token):
            await websocket.close(code=4001)
            return
    await websocket.accept()

    TICK_INTERVAL = 2.0
    _silent = {
        "peak_intensity":  0.0,
        "panoramic_peaks": [],
        "sigint_peaks":    [],
        "station_scan":    {},
        "is_interference": False,
        "playing_idx":     None,
        "log_running":     False,
        "log_path":        "",
    }

    try:
        while True:
            t0 = time.time()

            # Serve from shared cache (refreshed by background thread)
            payload = dict(_spectrum_cache) if _spectrum_cache else dict(_silent)
            # Attach peaks_cache so OverviewDashboard gets real-time data without polling
            if _peaks_cache:
                payload["sdr_peaks"] = _peaks_cache

            await websocket.send_json(payload)

            elapsed = time.time() - t0
            await asyncio.sleep(max(0.0, TICK_INTERVAL - elapsed))

    except Exception:
        pass  # Connection closed silently


# ---------------------------------------------------------------------------
# Chat — WebSocket hub
# ---------------------------------------------------------------------------
_chat_clients: set = set()   # set of WebSocket objects
_chat_lock: asyncio.Lock | None = None   # initialised on first async call
_chat_online: dict = {}      # ws -> {user_id, username, display_name, avatar_url}
_chat_last_seen: dict = {}   # user_id -> datetime of last disconnect


def _get_chat_lock() -> asyncio.Lock:
    global _chat_lock
    if _chat_lock is None:
        _chat_lock = asyncio.Lock()
    return _chat_lock


async def _chat_broadcast(payload: dict, exclude: WebSocket | None = None):
    """Send JSON payload to all connected chat clients."""
    data = __import__('json').dumps(payload)
    dead = []
    lock = _get_chat_lock()
    async with lock:
        targets = list(_chat_clients)
    for ws in targets:
        if ws is exclude:
            continue
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    lock = _get_chat_lock()
    for ws in dead:
        async with lock:
            _chat_clients.discard(ws)
            _chat_online.pop(ws, None)


def _presence_payload():
    users = [
        {k: str(v) if k == 'user_id' and v else v for k, v in info.items()}
        for info in _chat_online.values()
    ]
    return {"type": "presence", "users": users}


def _msg_to_dict(msg: ChatMessage, username: str | None = None,
                 display_name: str | None = None, avatar_url: str | None = None) -> dict:
    return {
        "type":         "message",
        "id":           str(msg.id),
        "timestamp":    msg.timestamp.isoformat() + "Z",
        "user_id":      str(msg.user_id) if msg.user_id else None,
        "sender_type":  msg.sender_type,
        "username":     username,
        "display_name": display_name,
        "avatar_url":   avatar_url,
        "content":      msg.content,
        "room":         msg.room,
    }


@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    origin = websocket.headers.get('origin', '')
    if origin not in [_FRONTEND_ORIGIN, 'https://example.com', 'http://localhost:8000']:
        await websocket.close(code=4003, reason='Invalid Origin')
        return
    if os.environ.get('DRI_TESTMODE'):
        jwt_pl = {'sub': '00000000-0000-0000-0000-000000000001'}
    else:
        token = websocket.cookies.get(_COOKIE_NAME)
        jwt_pl = _decode_jwt(token) if token else None
        if not jwt_pl:
            await websocket.close(code=4001)
            return
    await websocket.accept()

    try:
        uid = _uuid_mod.UUID(jwt_pl['sub'])
    except Exception:
        await websocket.close(code=4001)
        return

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == uid).first()
        uname = u.username if u else 'unknown'
        dname = u.display_name if u else None
        avar  = u.avatar_url if u else None
    finally:
        db.close()

    async with _get_chat_lock():
        _chat_clients.add(websocket)
        _chat_online[websocket] = {
            'user_id':      uid,
            'username':     uname,
            'display_name': dname,
            'avatar_url':   avar,
        }

    await _chat_broadcast(_presence_payload())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = __import__('json').loads(raw)
            except Exception:
                continue
            if data.get('type') == 'ping':
                await websocket.send_text(__import__('json').dumps({'type': 'pong'}))
            # Messages are sent via REST POST /api/chat/send; WS is receive-only for client
    except Exception:
        pass
    finally:
        async with _get_chat_lock():
            info = _chat_online.pop(websocket, None)
            if info and info.get('user_id'):
                _chat_last_seen[str(info['user_id'])] = _dt.utcnow()
            _chat_clients.discard(websocket)
        await _chat_broadcast(_presence_payload())


# ---------------------------------------------------------------------------
# Chat — REST endpoints
# ---------------------------------------------------------------------------
class _SendMsg(_BM):
    content: str
    room:    str = 'global'


@app.get('/api/chat/history')
async def chat_history(
    room: str = 'global',
    limit: int = 100,
    _user: User = Depends(get_current_user),
):
    room = _norm_chat_room(_user, room)
    _assert_room_access(_user, room)
    return await _run_in_thread(_chat_history_sync, room, limit)


def _chat_history_sync(room, limit):
    db = SessionLocal()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.room == room)
            .order_by(ChatMessage.timestamp.desc())
            .limit(min(limit, 200))
            .all()
        )
        rows = list(reversed(rows))
        out = []
        for m in rows:
            if m.sender:
                out.append(_msg_to_dict(m, m.sender.username, m.sender.display_name, m.sender.avatar_url))
            else:
                out.append(_msg_to_dict(m))
        return out
    finally:
        db.close()


@app.get('/api/chat/users')
async def chat_users(_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        all_users = db.query(User).filter(User.is_active == True).order_by(User.username).all()
        online_ids = {str(info['user_id']) for info in _chat_online.values() if info.get('user_id')}
        return [
            {
                'user_id':      str(u.id),
                'username':     u.username,
                'display_name': u.display_name,
                'avatar_url':   u.avatar_url,
                'online':       str(u.id) in online_ids,
                'last_seen':    _chat_last_seen[str(u.id)].isoformat() if str(u.id) in _chat_last_seen else None,
            }
            for u in all_users
        ]
    finally:
        db.close()


def _is_dm_room(room: str) -> bool:
    """Check if a room name follows DM convention: dm_{uuid}_{uuid}"""
    parts = room.split('_', 2)
    return len(parts) == 3 and parts[0] == 'dm'


def _dm_room_name(uid1, uid2) -> str:
    """Generate canonical DM room name from two user IDs (sorted)."""
    a, b = sorted([str(uid1), str(uid2)])
    return f'dm_{a}_{b}'


def _dm_participants(room: str) -> set:
    """Extract the two user_id strings from a DM room name."""
    parts = room.split('_', 2)
    if len(parts) == 3:
        return {parts[1], parts[2]}
    return set()


def _is_system_room(room: str) -> bool:
    return room.startswith('system_') and len(room) > len('system_')


def _system_room_owner(room: str) -> str | None:
    if not _is_system_room(room):
        return None
    return room[len('system_'):]


def _norm_chat_room(user: User, room: str) -> str:
    """Allow users to refer to their personal AI room as just 'system'."""
    if room == 'system':
        return f'system_{user.id}'
    return room


def _assert_room_access(user: User, room: str):
    """Raise 403 if the user is not allowed to access the given chat room."""
    if room == 'global':
        return
    if _is_dm_room(room):
        if str(user.id).lower() in {p.lower() for p in _dm_participants(room)}:
            return
        raise HTTPException(403, 'Not a participant of this DM')
    if _is_system_room(room):
        if user.is_admin or str(user.id).lower() == (_system_room_owner(room) or '').lower():
            return
        raise HTTPException(403, 'Forbidden')
    # Unknown room namespace — deny by default
    raise HTTPException(403, 'Forbidden')


async def _broadcast_to_room(payload: dict, room: str, exclude_sender_id: str | None = None):
    """Send a chat payload only to users allowed to see the given room."""
    if _is_dm_room(room):
        targets = _dm_participants(room)
        if exclude_sender_id:
            targets = targets - {exclude_sender_id}
        if targets:
            await _chat_broadcast_to(payload, targets)
    elif _is_system_room(room):
        owner = _system_room_owner(room)
        if owner:
            await _chat_broadcast_to(payload, {owner})
    else:
        # global
        targets = {
            str(info.get('user_id', '')) for info in _chat_online.values()
            if str(info.get('user_id', '')) != str(exclude_sender_id or '')
        }
        if targets:
            await _chat_broadcast_to(payload, targets)


async def _chat_broadcast_to(payload: dict, target_user_ids: set):
    """Send JSON payload only to connected clients whose user_id is in target_user_ids."""
    data = __import__('json').dumps(payload)
    dead = []
    lock = _get_chat_lock()
    async with lock:
        targets = list(_chat_clients)
    for ws in targets:
        info = _chat_online.get(ws)
        if not info:
            continue
        if str(info.get('user_id', '')) not in target_user_ids:
            continue
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    if dead:
        lock = _get_chat_lock()
        for ws in dead:
            async with lock:
                _chat_clients.discard(ws)
                _chat_online.pop(ws, None)


@app.post('/api/chat/send')
async def chat_send(
    body: _SendMsg,
    _user: User = Depends(get_current_user),
):
    content = body.content.strip()[:2000]
    if not content:
        raise HTTPException(400, 'Empty message')
    room = _norm_chat_room(_user, body.room)
    _assert_room_access(_user, room)

    def _persist():
        db = SessionLocal()
        try:
            msg = ChatMessage(
                user_id=_user.id,
                sender_type='user',
                content=content,
                room=room,
            )
            db.add(msg)
            db.commit()
            db.refresh(msg)
            return _msg_to_dict(msg, _user.username, _user.display_name, _user.avatar_url)
        finally:
            db.close()

    payload = await _run_in_thread(_persist)

    # Broadcast only to users who are allowed to see this room.
    await _broadcast_to_room(payload, room, exclude_sender_id=str(_user.id))
    return payload


@app.get('/api/chat/dm/rooms')
async def chat_dm_rooms(_user: User = Depends(get_current_user)):
    """List DM rooms the current user participates in, with last message preview."""
    uid = str(_user.id)
    db = SessionLocal()
    try:
        from sqlalchemy import func, distinct
        # Find all DM rooms this user has messages in
        dm_rooms = (
            db.query(distinct(ChatMessage.room))
            .filter(ChatMessage.room.like(f'%{uid}%'))
            .filter(ChatMessage.room.like('dm_%'))
            .all()
        )
        rooms = [r[0] for r in dm_rooms if uid in _dm_participants(r[0])]
        result = []
        for room in rooms:
            participants = _dm_participants(room)
            other_id = (participants - {uid}).pop() if len(participants) == 2 else None
            other_user = None
            if other_id:
                other_user = db.query(User).filter(User.id == _uuid_mod.UUID(other_id)).first()
            last_msg = (
                db.query(ChatMessage)
                .filter(ChatMessage.room == room)
                .order_by(ChatMessage.timestamp.desc())
                .first()
            )
            result.append({
                'room': room,
                'other_user': {
                    'user_id': other_id,
                    'username': other_user.username if other_user else None,
                    'display_name': other_user.display_name if other_user else None,
                    'avatar_url': other_user.avatar_url if other_user else None,
                } if other_user else None,
                'last_message': {
                    'content': last_msg.content[:100] if last_msg else None,
                    'timestamp': last_msg.timestamp.isoformat() + 'Z' if last_msg else None,
                    'sender_type': last_msg.sender_type if last_msg else None,
                } if last_msg else None,
            })
        result.sort(key=lambda r: r['last_message']['timestamp'] if r.get('last_message') and r['last_message'].get('timestamp') else '', reverse=True)
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Chat — Clear room
# ---------------------------------------------------------------------------
@app.delete('/api/chat/clear')
async def chat_clear(room: str = 'global', _user: User = Depends(get_current_user)):
    room = _norm_chat_room(_user, room)
    _assert_room_access(_user, room)
    uid = str(_user.id)
    # Users may only clear their own system room; admins may also clear global
    own_system_room = f'system_{uid}'
    if room == 'global' and not _user.is_admin:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail='Admin only')
    if room not in ('global', own_system_room) and not _user.is_admin:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail='Forbidden')
    deleted = await _run_in_thread(_chat_clear_sync, room)
    if room == 'global':
        await _chat_broadcast({'type': 'clear_room', 'room': 'global'})
    elif _is_system_room(room):
        await _broadcast_to_room({'type': 'clear_room', 'room': room}, room)
    return {'deleted': deleted, 'room': room}


def _chat_clear_sync(room: str) -> int:
    db = SessionLocal()
    try:
        count = db.query(ChatMessage).filter(ChatMessage.room == room).delete()
        db.commit()
        return count
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Chat — File upload
# ---------------------------------------------------------------------------
CHAT_UPLOADS_DIR = os.path.join(BASE_DIR, "static", "chat_uploads")
os.makedirs(CHAT_UPLOADS_DIR, exist_ok=True)

_BLOCKED_EXTENSIONS = {'.svg'}
_ALLOWED_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp',
    '.mp4', '.mov',
    '.mp3', '.wav', '.m4a', '.ogg',
    '.txt', '.md', '.rtf',
    '.pdf',
    '.doc', '.docx', '.ppt', '.pptx',
    '.zip', '.rar',
    '.json', '.csv', '.xml',
}

_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
_VIDEO_EXTENSIONS = {'.mp4', '.mov'}


@app.post('/api/chat/upload')
async def chat_upload(
    file: UploadFile = File(...),
    room: str = Form('global'),
    _user: User = Depends(get_current_user),
):
    """Upload a file and create a chat message with file metadata."""
    room = _norm_chat_room(_user, room)
    _assert_room_access(_user, room)
    import pathlib
    ext = pathlib.Path(file.filename or '').suffix.lower()
    if ext in _BLOCKED_EXTENSIONS:
        raise HTTPException(400, 'SVG files are not allowed for security reasons')
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(400, f'File type {ext} not supported')

    # Read file (limit 50MB)
    content_bytes = await file.read()
    if len(content_bytes) > 50 * 1024 * 1024:
        raise HTTPException(400, 'File too large (max 50MB)')

    # Save to disk
    file_id = str(_uuid_mod.uuid4())
    safe_name = f"{file_id}{ext}"
    file_path = os.path.join(CHAT_UPLOADS_DIR, safe_name)
    with open(file_path, 'wb') as f:
        f.write(content_bytes)

    file_url = f"/static/chat_uploads/{safe_name}"
    file_size = len(content_bytes)
    original_name = file.filename or f"file{ext}"

    # Determine file category
    if ext in _IMAGE_EXTENSIONS:
        file_type = 'image'
    elif ext in _VIDEO_EXTENSIONS:
        file_type = 'video'
    else:
        file_type = 'file'

    # Store as a chat message with JSON content
    import json as _json_upload
    msg_content = _json_upload.dumps({
        'type': 'file',
        'file_type': file_type,
        'file_url': file_url,
        'file_name': original_name,
        'file_size': file_size,
        'file_ext': ext,
    })

    db = SessionLocal()
    try:
        msg = ChatMessage(
            user_id=_user.id,
            sender_type='user',
            content=msg_content,
            room=room,
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        payload = _msg_to_dict(msg, _user.username, _user.display_name, _user.avatar_url)
    finally:
        db.close()

    # Broadcast (excl sender — they get the response directly)
    sender_id = str(_user.id)
    if _is_dm_room(room):
        targets = _dm_participants(room) - {sender_id}
        if targets:
            await _chat_broadcast_to(payload, targets)
    else:
        await _chat_broadcast_to(payload, {
            str(info.get('user_id', '')) for info in _chat_online.values()
            if str(info.get('user_id', '')) != sender_id
        })
    return payload


# ---------------------------------------------------------------------------
# AI chat endpoint — Gemini gemini-2.5-flash with function calling
# ---------------------------------------------------------------------------
def _load_apis_txt(path: str = 'apis.txt') -> dict:
    apis = {}
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        if not os.path.isfile(p):
            return apis
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if ' - ' in line:
                    k, _, v = line.partition(' - ')
                    if k not in apis:
                        apis[k.strip()] = v.strip()
    except Exception:
        pass
    return apis


_APIS = _load_apis_txt()
_LIBRETRANSLATE_URL = _env('LIBRETRANSLATE_URL', 'https://libretranslate.de').rstrip('/')
_LIBRETRANSLATE_API_KEY = _env('LIBRETRANSLATE_API_KEY', '')


_RF_SWEEP_SCHEMA = """
Table: rf_sweep_data
Columns:
  id            INTEGER PRIMARY KEY
  timestamp     TIMESTAMP WITHOUT TIME ZONE  (indexed; use ISO range filters)
  frequency_mhz FLOAT   (stored in kHz — e.g. 106300 = 106.3 MHz; indexed)
  power_dbfs    FLOAT   (signal power in dBFS; typical range -90 to -20)
  snr_db        FLOAT   (signal-to-noise ratio in dB)
  sdr_id        TEXT    (SDR device identifier, nullable)
Notes:
  - Table has 90M+ rows. ALWAYS aggregate (COUNT, AVG, MAX, MIN) or use LIMIT ≤ 50.
  - Timestamp index: ix_rf_sweep_ts on (timestamp DESC) — use WHERE timestamp >= ... for speed.
  - frequency_mhz index: ix_sweep_ts_freq on (timestamp, frequency_mhz).
  - Frequency is stored in kHz. To query 106.3 MHz use frequency_mhz BETWEEN 106200 AND 106400.
"""

# Use safe AI tools instead of vulnerable SQL execution
_GEMINI_TOOLS = [{"function_declarations": get_safe_ai_tools()}]

# ---------------------------------------------------------------------------
# Ollama local AI (replaces Gemini when Ollama is available)
# ---------------------------------------------------------------------------
_OLLAMA_URL = _env('OLLAMA_URL', 'http://localhost:11434')
_OLLAMA_MODEL = _env('OLLAMA_MODEL', 'mistral:7b-instruct-v0.3-q4_K_M')
_OLLAMA_TIMEOUT = int(_env('OLLAMA_TIMEOUT_SEC', '300'))
_OLLAMA_NUM_CTX = int(_env('OLLAMA_NUM_CTX', '4096'))
_CHAT_AI_STORE_HISTORY = _env('CHAT_AI_STORE_HISTORY', 'true').lower() in ('1', 'true', 'yes')

_OLLAMA_SYSTEM = """You are GrepBot, the AI assistant embedded in the cgrep SDR (Software-Defined Radio) dashboard, serving RF operators and analysts in Region.

## CORE IDENTITY & TONE
- You are a confident, senior RF/spectrum analysis expert — not a generic chatbot.
- Give direct, decisive answers. Do not hedge with "it depends" without immediately following up with the most likely answer based on available data.
- Do not ask clarifying questions unless truly necessary (e.g., missing a required frequency band or station ID that cannot be inferred). If ambiguous, state your assumption explicitly and proceed with the best answer.
- No filler, no apologies, no "I'm just an AI" disclaimers. Get straight to the point.
- Be proactive: if you notice something relevant (anomaly, out-of-band signal, health issue) while answering, mention it even if not explicitly asked.

## LANGUAGE RULES (STRICT)
- ALWAYS reply ONLY in clear, plain English or plain Regioni depends on user prompt.
- The user may write in Turkish, Russian, Arabic, or English — their message is pre-translated to English for you.
- NEVER use Chinese characters, Cyrillic, Arabic script, or any non-English alphabet in your response, under any circumstance, even if quoting or referencing terms.


## CALCULATIONS
- When a question involves numbers (frequency conversion, wavelength, dBm/dBW/watts conversion, path loss, duty cycle, bandwidth-time products, SNR estimates, etc.), calculate it yourself and show the result clearly, with the formula used if it adds clarity.
- Common conversions to handle instantly: dBm ↔ mW/W, frequency ↔ wavelength (λ = c/f), dB ↔ linear ratio, EIRP calculations, Nyquist/sampling rate requirements.
- Round sensibly and state units explicitly. If precision matters (e.g., regulatory compliance), say so.

## RESPONSE STYLE
- DONT text something more than 7 sentences
- be simple and text just info statistics. dont make a word mess. stop using ##text and **text thingies.
- Default to concise, scannable answers: short paragraphs, bullet points or tables for data, bolded key numbers/conclusions.
- Only go long-form (detailed report style) when the user explicitly asks for a report, summary document, or detailed writeup.
- When giving a report, structure it clearly: Summary → Findings → Data/Numbers → Recommendation.
- Always end an analysis with a clear takeaway or recommended action — don't just dump data and stop.

## BEHAVIOR ON UNCERTAINTY
- If data is genuinely missing and no tool can retrieve it, state your best expert estimate and clearly label it as an estimate/assumption — do not refuse to answer.
- Never respond with "I don't know" alone. Always pair uncertainty with the most probable answer, reasoning, or next diagnostic step.

## OUT OF SCOPE
- You do not provide legal/regulatory rulings as binding fact — frame regulatory statements as general guidance, not legal advice.
- You do not assist with actively jamming, spoofing, or illegally transmitting on licensed/restricted frequencies. Politely decline such requests and redirect to legitimate spectrum monitoring use."""

# OpenAI-style tool format is what Ollama /api/chat expects
_OLLAMA_TOOLS = [{'type': 'function', 'function': t} for t in get_safe_ai_tools()]


def _call_ollama_chat_sync(messages, tools=None):
    """Synchronous HTTP call to Ollama /api/chat."""
    import json as _json
    payload = {
        'model': _OLLAMA_MODEL,
        'messages': messages,
        'stream': False,
        'options': {
            'temperature': 0.3,
            'num_ctx': _OLLAMA_NUM_CTX,
        },
    }
    if tools:
        payload['tools'] = tools
    data = _json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        f'{_OLLAMA_URL.rstrip("/")}/api/chat',
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=_OLLAMA_TIMEOUT) as resp:
            return _json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {'error': f'Ollama request failed: {e}'}


def _looks_regioni(text: str) -> bool:
    low = text.lower()
    if 'ə' in low:
        return True
    az_words = {'mən', 'sən', 'biz', 'nə', 'bu', 'yox', 'bəli', 'salam', 'sağ', 'təşəkkür', 'necə', 'harda', 'niyə', 'çox', 'az', 'gün'}
    return any(w in low for w in az_words)


async def _translate_with_libretranslate(text: str, source_lang: str, target_lang: str) -> str:
    if not _LIBRETRANSLATE_URL or source_lang == target_lang or not text.strip():
        return text
    payload = {
        'q': text,
        'source': source_lang,
        'target': target_lang,
        'format': 'text',
    }
    if _LIBRETRANSLATE_API_KEY:
        payload['api_key'] = _LIBRETRANSLATE_API_KEY

    def _call():
        import json as _json
        data = _json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            f'{_LIBRETRANSLATE_URL}/translate',
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read().decode('utf-8'))
                return result.get('translatedText', text)
        except Exception:
            return text

    return await _run_in_thread(_call)


async def _final_reply(reply: str, source_lang: str) -> str:
    if source_lang == 'az':
        return await _translate_with_libretranslate(reply, 'en', 'az')
    return reply


async def _ollama_chat_reply(body):
    """Build messages, run tool-call loop, and return the final text."""
    import json as _json
    source_lang = 'az' if _looks_regioni(body.message) else 'en'
    user_text = body.message
    if source_lang == 'az':
        user_text = await _translate_with_libretranslate(user_text, 'az', 'en')
        if not user_text.strip():
            user_text = body.message
    _ENGLISH_ONLY = (
        "\n\nCRITICAL: You must ALWAYS reply ONLY in clear English. "
        "Do not use Chinese, Turkish, Russian, Arabic, or any other language. "
        "Do not use Chinese characters, Cyrillic, Arabic script, or any non-English alphabet. "
        "Use plain English words and sentences only."
    )
    messages = [{'role': 'system', 'content': _OLLAMA_SYSTEM}]
    for h in body.history[-6:]:
        role = 'user' if h.get('sender_type') == 'user' else 'assistant'
        messages.append({'role': role, 'content': h.get('content', '')})
    messages.append({'role': 'user', 'content': user_text + _ENGLISH_ONLY})

    tools = _OLLAMA_TOOLS
    for _ in range(8):
        resp = await _run_in_thread(_call_ollama_chat_sync, messages, tools)
        if 'error' in resp:
            return f'[AI error: {resp["error"]}]'
        msg = resp.get('message', {})
        messages.append(msg)
        tool_calls = msg.get('tool_calls')
        if not tool_calls:
            return await _final_reply(msg.get('content', '').strip() or '[No response]', source_lang)
        # Execute every tool the model asked for
        for tc in tool_calls:
            fn = tc.get('function', {})
            name = fn.get('name')
            args = fn.get('arguments', {})
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except Exception:
                    args = {}
            result = await _run_in_thread(execute_safe_tool, name, args)
            messages.append({'role': 'tool', 'content': str(result)})

    # Safety final call without tools if we hit the loop limit
    resp = await _run_in_thread(_call_ollama_chat_sync, messages, None)
    if 'error' in resp:
        return f'[AI error: {resp["error"]}]'
    return await _final_reply(resp.get('message', {}).get('content', '').strip() or '[No response]', source_lang)


def _safe_sql_execute(sql: str) -> str:
    """Validate and execute AI-generated SQL. Returns JSON string of results."""
    import re, json
    sql = sql.strip().rstrip(';')
    # Only allow SELECT
    if not re.match(r'^\s*SELECT\b', sql, re.IGNORECASE):
        return json.dumps({"error": "Only SELECT queries are allowed."})
    # Block dangerous keywords
    blocked = re.compile(r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|EXECUTE|EXEC|COPY)\b', re.IGNORECASE)
    if blocked.search(sql):
        return json.dumps({"error": "Blocked keyword detected in SQL."})
    # Enforce LIMIT ≤ 50 (add if missing)
    if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
        sql = sql + ' LIMIT 50'
    else:
        # Replace any existing LIMIT > 50
        def _cap_limit(m):
            try:
                n = int(m.group(1))
                return f'LIMIT {min(n, 50)}'
            except Exception:
                return m.group(0)
        sql = re.sub(r'LIMIT\s+(\d+)', _cap_limit, sql, flags=re.IGNORECASE)
    db = SessionLocal()
    try:
        result = db.execute(text(sql)).fetchall()
        cols = db.execute(text(sql)).keys() if hasattr(db.execute(text(sql)), 'keys') else []
        rows_out = [dict(zip(cols, row)) for row in result]
        return json.dumps(rows_out, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)[:300]})
    finally:
        db.close()


def _safe_sql_execute_v2(sql: str) -> str:
    """V2: properly captures column names."""
    import re, json
    sql = sql.strip().rstrip(';')
    if not re.match(r'^\s*SELECT\b', sql, re.IGNORECASE):
        return json.dumps({"error": "Only SELECT queries are allowed."})
    blocked = re.compile(r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|EXECUTE|EXEC|COPY)\b', re.IGNORECASE)
    if blocked.search(sql):
        return json.dumps({"error": "Blocked keyword detected in SQL."})
    if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
        sql = sql + ' LIMIT 50'
    else:
        def _cap(m):
            try: return f'LIMIT {min(int(m.group(1)), 50)}'
            except: return m.group(0)
        sql = re.sub(r'LIMIT\s+(\d+)', _cap, sql, flags=re.IGNORECASE)
    db = SessionLocal()
    try:
        result = db.execute(text(sql))
        cols = list(result.keys())
        rows_out = [dict(zip(cols, row)) for row in result.fetchall()]
        return json.dumps(rows_out[:20], default=str)
    except Exception as e:
        return json.dumps({"error": str(e)[:300]})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# AI request queue (serializes Ollama calls and reports position/ETA)
# ---------------------------------------------------------------------------
_AI_QUEUE   = asyncio.Queue()
_AI_ORDER   = deque()
_AI_PENDING = {}
_AI_PROCESSING = None
_AI_LAST_TIMES = deque(maxlen=10)
_AI_WORKER  = None


def _compute_eta(position: int) -> int:
    if not _AI_LAST_TIMES:
        return 10
    avg = sum(_AI_LAST_TIMES) / len(_AI_LAST_TIMES)
    return max(1, int((position + 1) * avg))


async def _broadcast_queue_status():
    for pos, it in enumerate(_AI_ORDER):
        payload = {
            'type': 'queue_update',
            'request_id': it['request_id'],
            'position': pos + 1,
            'eta_seconds': _compute_eta(pos + 1),
            'status': 'queued',
        }
        await _chat_broadcast_to(payload, {it['user_id']})
    if _AI_PROCESSING:
        elapsed = time.time() - _AI_PROCESSING.get('started_at', time.time())
        remaining = max(0, _compute_eta(0) - int(elapsed))
        payload = {
            'type': 'queue_update',
            'request_id': _AI_PROCESSING['request_id'],
            'position': 0,
            'eta_seconds': remaining,
            'status': 'processing',
        }
        await _chat_broadcast_to(payload, {_AI_PROCESSING['user_id']})


async def _ai_queue_worker():
    global _AI_PROCESSING
    while True:
        item = await _AI_QUEUE.get()
        _AI_PENDING.pop(item['request_id'], None)
        if _AI_ORDER:
            _AI_ORDER.popleft()
        _AI_PROCESSING = item
        item['started_at'] = time.time()
        await _broadcast_queue_status()
        try:
            reply = await _ollama_chat_reply(item['body'])
            item['reply'] = reply
            _AI_LAST_TIMES.append(time.time() - item['started_at'])
        except Exception as exc:
            item['reply'] = f'[AI error: {exc}]'
            _AI_LAST_TIMES.append(time.time() - item['started_at'])
        _AI_PROCESSING = None
        if not item['future'].done():
            item['future'].set_result(item['reply'])
        await _broadcast_queue_status()


class _AIChat(_BM):
    message:     str
    history:     list = []
    room:        str = 'system'
    request_id:  _Opt[str] = None


@app.post('/api/chat/ai')
async def chat_ai(
    body: _AIChat,
    _user: User = Depends(get_current_user),
):
    body.room = _norm_chat_room(_user, body.room)
    _assert_room_access(_user, body.room)
    # Save and broadcast the user's own message first so it appears in chat
    _store_ai_chat = _CHAT_AI_STORE_HISTORY or not _is_system_room(body.room)
    if _store_ai_chat:
        db = SessionLocal()
        try:
            user_msg = ChatMessage(
                user_id=_user.id, sender_type='user', content=body.message, room=body.room,
            )
            db.add(user_msg); db.commit(); db.refresh(user_msg)
            user_payload = _msg_to_dict(user_msg, _user.username, _user.display_name, _user.avatar_url)
        finally:
            db.close()
    else:
        user_msg = ChatMessage(
            user_id=_user.id, sender_type='user', content=body.message, room=body.room,
        )
        user_payload = _msg_to_dict(user_msg, _user.username, _user.display_name, _user.avatar_url)
    await _broadcast_to_room(user_payload, body.room)

    request_id = body.request_id or _uuid_mod.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    item = {
        'request_id': request_id,
        'user_id': str(_user.id),
        'room': body.room,
        'body': body,
        'future': future,
    }
    _AI_ORDER.append(item)
    _AI_PENDING[request_id] = item
    await _AI_QUEUE.put(item)
    global _AI_WORKER
    if _AI_WORKER is None:
        _AI_WORKER = asyncio.create_task(_ai_queue_worker())
    await _broadcast_queue_status()
    reply_text = await future

    if _store_ai_chat:
        db = SessionLocal()
        try:
            msg = ChatMessage(user_id=None, sender_type='ai', content=reply_text, room=body.room)
            db.add(msg); db.commit(); db.refresh(msg)
            payload = _msg_to_dict(msg)
        finally:
            db.close()
    else:
        msg = ChatMessage(
            user_id=None, sender_type='ai', content=reply_text, room=body.room,
        )
        payload = _msg_to_dict(msg)
    await _broadcast_to_room(payload, body.room)
    return payload


# ---------------------------------------------------------------------------
# Admin broadcast — send a system message to all users without AI
# ---------------------------------------------------------------------------
class _BroadcastMsg(_BM):
    message: str
    room:    str = 'global'


@app.post('/api/admin/broadcast')
async def admin_broadcast(
    body: _BroadcastMsg,
    _caller: User = Depends(get_current_user),
):
    if not _caller.is_admin:
        raise HTTPException(403, 'Admin only')
    content = body.message.strip()[:2000]
    if not content:
        raise HTTPException(400, 'Empty message')

    if body.room == 'system':
        # Fan out a copy to every active user's personal system room
        db = SessionLocal()
        try:
            all_users = db.query(User).filter(User.is_active == True).all()
            payloads = []
            for u in all_users:
                room_name = f'system_{u.id}'
                msg = ChatMessage(user_id=None, sender_type='ai', content=content, room=room_name)
                db.add(msg)
            db.commit()
            # Re-query to get committed rows with proper ids/timestamps
            for u in all_users:
                room_name = f'system_{u.id}'
                last = (
                    db.query(ChatMessage)
                    .filter(ChatMessage.room == room_name, ChatMessage.sender_type == 'ai')
                    .order_by(ChatMessage.timestamp.desc())
                    .first()
                )
                if last:
                    payloads.append(_msg_to_dict(last))
        finally:
            db.close()
        for p in payloads:
            owner = _system_room_owner(p.get('room', ''))
            if owner:
                await _chat_broadcast_to(p, {owner})
        return {'broadcast': 'system', 'count': len(payloads)}
    else:
        # Global room — save once as ai sender and broadcast to all online users
        db = SessionLocal()
        try:
            msg = ChatMessage(user_id=None, sender_type='ai', content=content, room=body.room)
            db.add(msg); db.commit(); db.refresh(msg)
            payload = _msg_to_dict(msg)
        finally:
            db.close()
        await _chat_broadcast_to(payload, {
            str(info.get('user_id', '')) for info in _chat_online.values()
        })
        return payload


# ---------------------------------------------------------------------------
# User profile endpoints
# ---------------------------------------------------------------------------
import base64 as _b64
import re as _re_profile

AVATAR_DIR = os.path.join(BASE_DIR, "static", "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)


class _ProfileUpdate(_BM):
    display_name: _Opt[str] = None
    avatar_b64:   _Opt[str] = None   # base64 JPEG data-URI or raw base64


@app.get('/api/user/profile')
async def get_profile(_user: User = Depends(get_current_user)):
    return {
        'username':     _user.username,
        'display_name': _user.display_name,
        'avatar_url':   _user.avatar_url,
    }


@app.post('/api/user/profile')
async def update_profile(
    body: _ProfileUpdate,
    _user: User = Depends(get_current_user),
):
    def _save_profile():
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == _user.id).first()
            if not u:
                raise HTTPException(404, 'User not found')

            if body.display_name is not None:
                name = body.display_name.strip()[:100]
                u.display_name = name or None

            if body.avatar_b64:
                raw = body.avatar_b64
                # Reject SVG in data URI header or payload
                raw_lower = raw.lower()
                if 'data:image/svg+xml' in raw_lower or '<svg' in raw_lower:
                    raise HTTPException(400, 'SVG avatars are not allowed for security reasons')
                if ',' in raw:
                    raw = raw.split(',', 1)[1]
                try:
                    img_bytes = _b64.b64decode(raw)
                    if len(img_bytes) > 4 * 1024 * 1024:
                        raise HTTPException(400, 'Avatar too large (max 4 MB after decode)')
                    # Reject SVG content decoded as base64 (XML/SVG tags)
                    if b'<svg' in img_bytes[:2048].lower():
                        raise HTTPException(400, 'SVG avatars are not allowed for security reasons')
                    # Only accept common raster image magic bytes
                    _magic = img_bytes[:12]
                    if not (
                        _magic.startswith(b'\xff\xd8\xff') or              # JPEG
                        _magic.startswith(b'\x89PNG\r\n\x1a\n') or         # PNG
                        _magic.startswith(b'GIF87a') or                    # GIF
                        _magic.startswith(b'GIF89a') or                      # GIF
                        (_magic.startswith(b'RIFF') and _magic[8:12] == b'WEBP') or  # WebP
                        _magic.startswith(b'BM')                           # BMP
                    ):
                        raise HTTPException(400, 'Only JPEG/PNG/GIF/WebP/BMP avatar images are allowed')
                    avatar_path = os.path.join(AVATAR_DIR, f"{_user.id}.jpg")
                    with open(avatar_path, 'wb') as f:
                        f.write(img_bytes)
                    u.avatar_url = f"/static/avatars/{_user.id}.jpg"
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(400, 'Invalid avatar data')

            db.commit()
            return {'username': u.username, 'display_name': u.display_name, 'avatar_url': u.avatar_url}
        finally:
            db.close()

    result = await _run_in_thread(_save_profile)

    # Update WS presence info if user is connected
    async with _get_chat_lock():
        for ws, info in list(_chat_online.items()):
            if info.get('user_id') == _user.id:
                info['display_name'] = result.get('display_name')
                info['avatar_url']   = result.get('avatar_url')
    return result


# ---------------------------------------------------------------------------
# WhatsApp / Green API admin endpoints
# ---------------------------------------------------------------------------
@app.get('/whatsapp/status')
async def whatsapp_status(_caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    global _DROP_ALERTS_ENABLED
    return {
        'configured': green_api.is_configured(),
        'numbers': green_api.get_numbers(),
        'groups': green_api.get_groups(),
        'drop_alerts_enabled': _DROP_ALERTS_ENABLED,
    }


@app.get('/whatsapp/numbers')
async def whatsapp_numbers_get(_caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    return {'numbers': green_api.get_numbers()}


@app.post('/whatsapp/numbers')
async def whatsapp_numbers_save(body: dict, _caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    numbers = body.get('numbers', [])
    if not isinstance(numbers, list):
        raise HTTPException(status_code=400, detail='numbers must be a list')
    try:
        saved = await _run_in_thread(green_api.save_numbers, numbers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {'numbers': saved}


@app.get('/whatsapp/groups')
async def whatsapp_groups_get(_caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    return {'groups': green_api.get_groups()}


@app.post('/whatsapp/groups')
async def whatsapp_groups_save(body: dict, _caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    groups = body.get('groups', [])
    if not isinstance(groups, list):
        raise HTTPException(status_code=400, detail='groups must be a list')
    try:
        saved = await _run_in_thread(green_api.save_groups, groups)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {'groups': saved}


@app.post('/whatsapp/test')
async def whatsapp_test(body: dict, _caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    if not green_api.is_configured():
        raise HTTPException(status_code=503, detail='Green API is not configured')
    phones = body.get('phones') or body.get('numbers') or None
    groups = body.get('groups') or None
    message = body.get('message')
    try:
        result = await _run_in_thread(green_api.send_test, phones, groups, message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@app.get('/whatsapp/drop_alerts')
async def whatsapp_drop_alerts_get(_caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    global _DROP_ALERTS_ENABLED
    return {'enabled': _DROP_ALERTS_ENABLED}


@app.post('/whatsapp/drop_alerts')
async def whatsapp_drop_alerts_set(body: dict, _caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    global _DROP_ALERTS_ENABLED
    _DROP_ALERTS_ENABLED = bool(body.get('enabled', False))
    print(f"[whatsapp] drop alerts enabled={_DROP_ALERTS_ENABLED}")
    return {'enabled': _DROP_ALERTS_ENABLED}


@app.post('/whatsapp/test_drop_alert')
async def whatsapp_test_drop_alert(_caller: User = Depends(get_current_user)):
    if not _caller.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    if not green_api.is_configured():
        raise HTTPException(status_code=503, detail='WhatsApp is not configured')
    return await _run_in_thread(_test_random_drop_alert)


# ---------------------------------------------------------------------------
# Static frontend (React build output)
# ---------------------------------------------------------------------------
DIST_DIR = os.path.join(BASE_DIR, "sdr-dashboard", "dist")
ASSETS_DIR = os.path.join(DIST_DIR, "assets")

@app.middleware("http")
async def _no_cache_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/assets/") and request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

if os.path.isdir(ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

_STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(_STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# Serve public root files from dist (fonts, images, etc.) — registered BEFORE mounts
_PUBLIC_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.ttf', '.woff', '.woff2', '.mp3'}

@app.get("/")
async def serve_root():
    index_path = os.path.join(DIST_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path, headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})
    return {"error": "React build not found. Run `npm run build` inside sdr-dashboard."}


# ---------------------------------------------------------------------------
# Background server + PyWebView desktop window
# ---------------------------------------------------------------------------
_uvicorn_server = None

def run_server():
    # Fix Windows WinError 10054: proactor event loop throws ConnectionResetError
    # on every client disconnect via Cloudflare tunnel. Selector loop avoids this,
    # but it is capped at ~64 concurrent sockets. Set DRI_PROACTOR=1 for load tests.
    if sys.platform == 'win32' and not os.environ.get('DRI_PROACTOR'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _host = os.environ.get('SERVER_HOST', '0.0.0.0')
    _port = int(os.environ.get('SERVER_PORT', '8000'))
    config = uvicorn.Config(
        app,
        host=_host,
        port=_port,
        log_level="info",
        limit_max_requests=None,
        h11_max_incomplete_event_size=10*1024*1024,
        timeout_keep_alive=120,
        timeout_graceful_shutdown=10,
    )
    global _uvicorn_server
    _uvicorn_server = uvicorn.Server(config=config)
    _uvicorn_server.run()


# ---------------------------------------------------------------------------
# Analytics — known stations list (mirrors _KNOWN_STATIONS_MHZ but with names)
# ---------------------------------------------------------------------------
_STATION_NAMES = {
    88.7:  'DAB+ 12A', 90.0: 'Ictimai R.', 90.7: 'ANS FM',
    92.5:  'Space FM',  93.0: 'Retro FM',   93.3: 'Azad AZ',
    97.7:  'Radio Cay', 100.0: 'FM 100',    100.5: 'Dünya R.',
    101.1: 'Lider FM',  103.3: 'Xəzər FM',  104.0: 'Meydan FM',
    105.0: 'Idman AZ',  105.5: 'Radio Günəş', 106.0: 'Asan R.',
    106.3: 'Azercell',  107.0: 'ITV R.',    107.7: 'Natavan',
}


# Central outage cache — built by background thread every 5 min, shared by all users
_outage_cache: dict = {}        # key -> result_dict  (no TTL needed, thread refreshes)
_OUTAGE_CACHE_LOCK = threading.Lock()
_OUTAGE_REFRESH_SEC = float(_env('OUTAGE_REFRESH_SEC', '300.0'))  # default: 5 minutes

# Station list for analytics (matches user request)
_ANALYTICS_STATIONS = [
    (90.0, 'Ictimai R.'), (90.7, 'ANS FM'), (92.5, 'Space FM'),
    (93.0, 'Retro FM'), (93.3, 'Azad AZ'), (97.7, 'Radio Cay'),
    (100.0, 'FM 100'), (100.5, 'Dünya R.'), (101.1, 'Lider FM'),
    (103.3, 'Xəzər FM'), (104.0, 'Meydan FM'), (105.0, 'Idman AZ'),
    (105.5, 'Radio Günəş'), (106.0, 'Asan R.'), (106.3, 'Azercell'),
    (107.0, 'ITV R.'), (107.7, 'Natavan'),
]


def _build_outage_result(mode: str, threshold: float = -65.0, sdr_id: str = '') -> dict:
    """Build outage data from rf_sweep_data. Called by background thread.
    If sdr_id is provided, filters by that SDR and uses its station list from config.
    """
    from collections import defaultdict
    db = SessionLocal()
    try:
        now = _dt.utcnow()
        if mode == '24h':
            since = now - timedelta(hours=24)
            bucket_count = 24
            bucket_type = 'hour'
        elif mode == '7d':
            since = now - timedelta(days=7)
            bucket_count = 7
            bucket_type = 'day'
        else:  # 14d
            since = now - timedelta(days=14)
            bucket_count = 14
            bucket_type = 'day'

        if bucket_type == 'hour':
            labels = [(now - timedelta(hours=i)).hour for i in range(bucket_count - 1, -1, -1)]
            label_names = [f"{h:02d}:00" for h in labels]
        else:
            labels = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(bucket_count - 1, -1, -1)]
            weekday_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            label_names = [weekday_names[_dt.strptime(d, '%Y-%m-%d').weekday()] if bucket_count == 7 else d[5:] for d in labels]

        # Build station list: from sdr_config if sdr_id given, else default
        if sdr_id:
            cfg = _get_sdr_config(sdr_id)
            stations_list = []
            seen_freqs = set()
            if cfg:
                for st in cfg.get("stations", []):
                    f = st["freq"]
                    if f not in seen_freqs:
                        seen_freqs.add(f)
                        stations_list.append((f, st.get("name", "")))
            if not stations_list:
                stations_list = _ANALYTICS_STATIONS
        else:
            stations_list = _ANALYTICS_STATIONS

        # frequency_mhz column actually stores kHz (e.g. 90000 = 90 MHz)
        freq_lo_khz = min(f for f, _ in stations_list) * 1000 - 500
        freq_hi_khz = max(f for f, _ in stations_list) * 1000 + 500
        trunc_expr = "DATE_TRUNC('hour', timestamp)" if bucket_type == 'hour' else "DATE_TRUNC('day', timestamp)"

        # Build SQL with optional sdr_id filter
        sdr_filter = "AND sdr_id = :sid" if sdr_id else ""
        params = {"since": since, "thr": threshold, "lo": freq_lo_khz, "hi": freq_hi_khz}
        if sdr_id:
            params["sid"] = sdr_id

        bulk = db.execute(text(f"""
            SELECT
                ROUND(frequency_mhz / 100.0) * 100 AS freq_bin_khz,
                {trunc_expr}                         AS bucket,
                COUNT(*)                             AS cnt
            FROM rf_sweep_data
            WHERE timestamp >= :since
              AND power_dbfs < :thr
              AND frequency_mhz BETWEEN :lo AND :hi
              {sdr_filter}
            GROUP BY freq_bin_khz, bucket
            ORDER BY freq_bin_khz, bucket
        """), params).fetchall()

        drop_index = defaultdict(list)
        for freq_bin_khz, bucket_ts, cnt in bulk:
            drop_index[float(freq_bin_khz)].append((bucket_ts, int(cnt)))

        results = []
        total_drops = 0
        for freq_mhz, name in stations_list:
            freq_khz = freq_mhz * 1000
            station_buckets: dict = {}
            for fb_khz, rows in drop_index.items():
                if abs(fb_khz - freq_khz) <= 100:
                    for bucket_ts, cnt in rows:
                        station_buckets[bucket_ts] = station_buckets.get(bucket_ts, 0) + cnt

            buckets = [0] * bucket_count
            station_total = 0
            for bucket_ts, cnt in station_buckets.items():
                if bucket_type == 'hour':
                    hours_ago = int((now - bucket_ts).total_seconds() / 3600)
                    if 0 <= hours_ago < bucket_count:
                        buckets[bucket_count - 1 - hours_ago] += cnt
                        station_total += cnt
                else:
                    d = bucket_ts.strftime('%Y-%m-%d')
                    if d in labels:
                        buckets[labels.index(d)] += cnt
                        station_total += cnt

            total_drops += station_total
            results.append({'freq': freq_mhz, 'name': name, 'buckets': buckets, 'total': station_total})

        return {
            'stations': results, 'labels': label_names,
            'bucket_type': bucket_type, 'threshold_db': threshold,
            'mode': mode, 'total_drops': total_drops,
            'sdr_id': sdr_id or 'all',
        }
    finally:
        db.close()


def _outage_refresh_loop():
    """Background thread: rebuild all outage cache modes every 5 minutes."""
    while True:
        for m in ('24h', '7d', '14d'):
            try:
                result = _build_outage_result(m)
                with _OUTAGE_CACHE_LOCK:
                    _outage_cache[m] = result
            except Exception:
                pass
        time.sleep(_OUTAGE_REFRESH_SEC)


_outage_refresh_thread = threading.Thread(target=_outage_refresh_loop, daemon=True)


# ---------------------------------------------------------------------------
# Periodic cleanup — keeps small tables fast
# ---------------------------------------------------------------------------
def _cleanup_old_records():
    """Prune old device_sessions, activity_logs, QR sessions and auth challenges."""
    db = SessionLocal()
    try:
        now = _dt.utcnow()
        # inactive device sessions older than 7 days
        db.execute(
            text("DELETE FROM device_sessions WHERE last_seen < :since"),
            {"since": now - timedelta(days=7)},
        )
        # activity logs older than 30 days
        db.execute(
            text("DELETE FROM activity_logs WHERE timestamp < :since"),
            {"since": now - timedelta(days=30)},
        )
        # expired/stale QR sessions older than 1 day
        db.execute(
            text("DELETE FROM pending_qr_sessions WHERE expires_at < :since"),
            {"since": now - timedelta(days=1)},
        )
        # expired auth challenges older than 1 hour
        db.execute(
            text("DELETE FROM auth_challenges WHERE expires_at < :since"),
            {"since": now - timedelta(hours=1)},
        )
        db.commit()
        print(f"[cleanup] pruned old records at {now.isoformat()}")
    except Exception as exc:
        db.rollback()
        print(f'[cleanup] error: {exc}')
    finally:
        db.close()


def _cleanup_scheduler():
    """Run cleanup every 10 minutes."""
    time.sleep(60)
    while True:
        _cleanup_old_records()
        time.sleep(600)


_cleanup_thread = threading.Thread(target=_cleanup_scheduler, daemon=True, name="cleanup")


@app.get('/api/analytics/outages')
async def get_outages(
    _user=Depends(get_current_user),
    mode: str = '7d',
    threshold: float = -65.0,
    sdr_id: str = '',
):
    """Return pre-built outage cache. Supports per-SDR filtering via sdr_id param."""
    cache_key = f"{mode}:{sdr_id}" if sdr_id else mode
    with _OUTAGE_CACHE_LOCK:
        cached = _outage_cache.get(cache_key)
    if cached:
        return cached
    # Build on demand for specific SDR or use default
    result = await _run_in_thread(_build_outage_result, mode, threshold, sdr_id)
    with _OUTAGE_CACHE_LOCK:
        _outage_cache[cache_key] = result
    return result

@app.get('/api/analytics/outages-debug')
async def get_outages_debug(
    mode: str = '7d',
    threshold: float = -65.0,
):
    """Debug endpoint - no authentication required."""
    try:
        with _OUTAGE_CACHE_LOCK:
            cached = _outage_cache.get(mode)
        if cached:
            return cached
        # First boot: build synchronously
        result = _build_outage_result(mode, threshold)
        with _OUTAGE_CACHE_LOCK:
            _outage_cache[mode] = result
        return result
    except Exception as e:
        return {"error": str(e), "debug": True}

@app.get('/api/test-simple')
async def test_simple():
    """Simple test endpoint to verify routing works."""
    return {"message": "API routing works!", "timestamp": time.time()}


# ---------------------------------------------------------------------------
# Map Annotations — admin-placed glowing clusters
# ---------------------------------------------------------------------------

class _AnnotationBody(_BM):
    lon:    float
    lat:    float
    label:  str  = ''
    color:  str  = '#00e5ff'
    radius: float = 8.0

@app.get('/api/map-annotations')
async def get_map_annotations(_user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        rows = db.query(MapAnnotation).order_by(MapAnnotation.created_at).all()
        return [{
            'id':         str(r.id),
            'lon':        r.lon,
            'lat':        r.lat,
            'label':      r.label or '',
            'color':      r.color,
            'radius':     r.radius,
            'created_at': r.created_at.isoformat(),
        } for r in rows]
    finally:
        db.close()

@app.post('/api/map-annotations')
async def save_map_annotations(
    body: list[_AnnotationBody],
    _user: User = Depends(get_current_user),
):
    if not _user.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    db = SessionLocal()
    try:
        # Replace all annotations with the new set
        db.query(MapAnnotation).delete()
        for a in body:
            db.add(MapAnnotation(
                created_by=_user.id,
                lon=a.lon, lat=a.lat,
                label=a.label, color=a.color, radius=a.radius,
            ))
        db.commit()
        return {'saved': len(body)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.delete('/api/map-annotations')
async def clear_map_annotations(_user: User = Depends(get_current_user)):
    if not _user.is_admin:
        raise HTTPException(status_code=403, detail='Admin only')
    db = SessionLocal()
    try:
        db.query(MapAnnotation).delete()
        db.commit()
        return {'cleared': True}
    finally:
        db.close()


@app.get('/api/analytics/anomalies')
async def get_anomalies(_user=Depends(get_current_user)):
    """Smart RF Intent Classifier.

    Reads the last 3 minutes of rf_sweep_data, groups by frequency, computes
    mean/std of SNR and uses a behavioural rules engine to classify each active
    frequency.  Also returns the most recent signal_events rows for the Raw
    Events sub-tab.
    """
    import pandas as pd
    from datetime import timedelta, datetime as _dtt

    db = SessionLocal()
    try:
        # ── 1. Smart classification from rf_sweep_data ────────────────────
        since = _dtt.utcnow() - timedelta(minutes=3)
        sweep_rows = (
            db.query(
                SweepData.timestamp,
                SweepData.frequency_mhz,
                SweepData.power_dbfs,
                SweepData.snr_db,
            )
            .filter(SweepData.timestamp >= since)
            .all()
        )

        classifications = []
        if sweep_rows:
            df = pd.DataFrame(sweep_rows, columns=['timestamp', 'frequency_mhz', 'power_dbfs', 'snr_db'])

            # SweepData.frequency_mhz is stored in kHz — convert to MHz
            df['freq_mhz'] = df['frequency_mhz'] / 1000.0

            # Noise floor estimate: power - snr  (≈ floor_dbfs)
            df['floor_dbfs'] = df['power_dbfs'] - df['snr_db']

            stats = df.groupby('freq_mhz').agg(
                readings_count=('snr_db', 'count'),
                snr_mean=('snr_db', 'mean'),
                snr_std=('snr_db', 'std'),
                floor_mean=('floor_dbfs', 'mean'),
                power_max=('power_dbfs', 'max'),
            ).reset_index()

            active = stats[stats['snr_mean'] > 10]

            for _, row in active.iterrows():
                freq   = float(row['freq_mhz'])
                snr    = float(row['snr_mean'])
                vol    = float(row['snr_std']) if not pd.isna(row['snr_std']) else 0.0
                floor  = float(row['floor_mean'])
                p_max  = float(row['power_max'])
                count  = int(row['readings_count'])

                # ── Behavioural Rules Engine ──────────────────────────────
                if floor > -80:
                    classification = 'WIDEBAND_JAMMING'
                    label          = 'Wideband Jamming / Elevated Noise'
                    severity       = 'critical'
                elif snr > 20 and vol < 4.0:
                    classification = 'LICENSED_BROADCAST'
                    label          = 'Licensed Broadcast'
                    severity       = 'ok'
                elif snr > 12 and vol >= 4.0:
                    classification = 'INTERFERENCE'
                    label          = 'Interference / Unstable Source'
                    severity       = 'warning'
                else:
                    classification = 'BACKGROUND_NOISE'
                    label          = 'Background Noise / Transient'
                    severity       = 'info'

                # Resolve station name if known (±0.15 MHz)
                station_name = next(
                    (v for k, v in _STATION_NAMES.items() if abs(freq - k) < 0.15),
                    None,
                )

                classifications.append({
                    'frequency_mhz':   round(freq, 1),
                    'snr_mean':        round(snr, 1),
                    'snr_std':         round(vol, 2),
                    'floor_mean':      round(floor, 1),
                    'power_max':       round(p_max, 1),
                    'readings_count':  count,
                    'classification':  classification,
                    'label':           label,
                    'severity':        severity,
                    'station_name':    station_name,
                })

        # ── 2. Raw signal_events (most recent 200) ────────────────────────
        raw_rows = (
            db.query(SignalEvent)
            .order_by(desc(SignalEvent.timestamp))
            .limit(200)
            .all()
        )

        known_freqs = list(_STATION_NAMES.keys())
        def _is_unknown(freq):
            return not any(abs(freq - k) < 0.15 for k in known_freqs)

        raw_events = [
            {
                'id':               r.id,
                'timestamp':        r.timestamp.isoformat(),
                'frequency_mhz':    round(r.frequency_mhz, 3),
                'power_dbfs':       round(r.power_dbfs, 1),
                'bandwidth_hz':     r.bandwidth_hz,
                'duration_sec':     r.duration_sec,
                'confidence':       r.confidence,
                'event_type':       r.event_type,
                'is_unknown_carrier': _is_unknown(r.frequency_mhz),
            }
            for r in raw_rows
        ]

        return {
            'classifications': classifications,
            'raw_events':      raw_events,
            'analyzed_at':     _dtt.utcnow().isoformat(),
            'window_minutes':  3,
        }
    finally:
        db.close()


@app.get('/api/analytics/waterfall')
async def get_waterfall(_user=Depends(get_current_user)):
    """Return the last 90 seconds of sweep data as waterfall rows.

    Each row is a dict {ts, freqs: [MHz,...], powers: [dBFS,...]}.
    Frequencies are binned to at most 200 bins across 88–108 MHz.
    Rows are ordered oldest→newest so the canvas can scroll them upward.
    Also returns classification overlay: list of {freq_mhz, severity} for
    any active classified signal so the frontend can draw alert dots.
    """
    import math
    from datetime import timedelta, datetime as _dtt

    MAX_FREQ_BINS = 200
    FREQ_LO_KHZ  = 88_000
    FREQ_HI_KHZ  = 108_100

    db = SessionLocal()
    try:
        since = _dtt.utcnow() - timedelta(seconds=30)

        rows = (
            db.query(SweepData.timestamp, SweepData.frequency_mhz, SweepData.power_dbfs)
            .filter(SweepData.timestamp >= since)
            .filter(SweepData.frequency_mhz >= FREQ_LO_KHZ)
            .filter(SweepData.frequency_mhz <= FREQ_HI_KHZ)
            .order_by(SweepData.timestamp, SweepData.frequency_mhz)
            .all()
        )

        if not rows:
            return {'freqs': [], 'rows': [], 'min_power': -90, 'max_power': -20, 'classifications': []}

        # Build freq axis (bin to MAX_FREQ_BINS)
        all_freqs_khz = sorted(set(int(r.frequency_mhz) for r in rows))
        all_freqs_mhz = sorted(set(round(f / 1000.0, 2) for f in all_freqs_khz))

        if len(all_freqs_mhz) > MAX_FREQ_BINS:
            step = math.ceil(len(all_freqs_mhz) / MAX_FREQ_BINS)
            chunks = [all_freqs_mhz[i:i+step] for i in range(0, len(all_freqs_mhz), step)]
            binned_freqs = [round(sum(c) / len(c), 2) for c in chunks]
        else:
            chunks = [[f] for f in all_freqs_mhz]
            binned_freqs = all_freqs_mhz

        # Group by exact timestamp — each timestamp is one sweep step (~1.5 MHz chunk)
        # Consecutive timestamps that are close together form one full sweep
        from collections import defaultdict
        freq_to_bin = {}
        for ci, chunk in enumerate(chunks):
            for f in chunk:
                freq_to_bin[f] = ci

        ts_map = defaultdict(lambda: [None] * len(binned_freqs))
        for r in rows:
            ts_key = r.timestamp.strftime('%H:%M:%S.%f')
            freq_mhz = round(r.frequency_mhz / 1000.0, 2)
            bi = freq_to_bin.get(freq_mhz)
            if bi is not None and r.power_dbfs is not None:
                ts_map[ts_key][bi] = round(float(r.power_dbfs), 1)

        waterfall_rows = [
            {'ts': ts, 'powers': powers}
            for ts, powers in sorted(ts_map.items())
        ]

        all_vals = [p for row in waterfall_rows for p in row['powers'] if p is not None]
        min_p = round(min(all_vals), 1) if all_vals else -90
        max_p = round(max(all_vals), 1) if all_vals else -20

        # Include classification overlay from the anomalies engine (reuse logic)
        import pandas as pd
        since3 = _dtt.utcnow() - timedelta(minutes=3)
        sweep3 = (
            db.query(SweepData.frequency_mhz, SweepData.power_dbfs, SweepData.snr_db)
            .filter(SweepData.timestamp >= since3)
            .all()
        )
        class_overlay = []
        if sweep3:
            df = pd.DataFrame(sweep3, columns=['frequency_mhz', 'power_dbfs', 'snr_db'])
            df['freq_mhz']   = df['frequency_mhz'] / 1000.0
            df['floor_dbfs'] = df['power_dbfs'] - df['snr_db']
            stats = df.groupby('freq_mhz').agg(
                snr_mean=('snr_db', 'mean'),
                snr_std=('snr_db', 'std'),
                floor_mean=('floor_dbfs', 'mean'),
            ).reset_index()
            for _, row in stats[stats['snr_mean'] > 10].iterrows():
                freq  = float(row['freq_mhz'])
                snr   = float(row['snr_mean'])
                vol   = float(row['snr_std']) if not pd.isna(row['snr_std']) else 0.0
                floor = float(row['floor_mean'])
                if floor > -80:
                    sev = 'critical'
                elif snr > 20 and vol < 4.0:
                    sev = 'ok'
                elif snr > 12 and vol >= 4.0:
                    sev = 'warning'
                else:
                    continue  # skip background
                class_overlay.append({'freq_mhz': round(freq, 1), 'severity': sev})

        return {
            'freqs':           binned_freqs,
            'rows':            waterfall_rows,
            'min_power':       min_p,
            'max_power':       max_p,
            'classifications': class_overlay,
        }
    finally:
        db.close()


def _query_latest_sweep(sdr_id: str = '') -> dict:
    """Return the most recent full FM sweep (88-108 MHz).
    Grabs last 8 seconds (one full sweep cycle = ~7s) and takes max power per frequency.
    If sdr_id is provided, only returns data from that SDR.
    """
    from datetime import timedelta, datetime as _dtt
    FREQ_LO = 88_000
    FREQ_HI = 108_100
    db = SessionLocal()
    try:
        since = _dtt.utcnow() - timedelta(seconds=8)
        sdr_filter = "AND sdr_id = :sid" if sdr_id else ""
        params = {"since": since, "lo": FREQ_LO, "hi": FREQ_HI}
        if sdr_id:
            params["sid"] = sdr_id
        rows = db.execute(
            text(
                "SELECT frequency_mhz, MAX(power_dbfs) "
                "FROM rf_sweep_data "
                "WHERE timestamp >= :since "
                "  AND frequency_mhz BETWEEN :lo AND :hi "
                "  AND power_dbfs IS NOT NULL "
                f"  {sdr_filter} "
                "GROUP BY frequency_mhz "
                "ORDER BY frequency_mhz"
            ),
            params,
        ).fetchall()
    finally:
        db.close()
    if not rows:
        return {"fft": [], "freqs": []}
    fft   = [round(float(p), 2) for _, p in rows]
    freqs = [round(int(f) / 1000.0, 3) for f, _ in rows]
    return {"fft": fft, "freqs": freqs}


# Spectrum latest cache (TTL) — one DB query serves many clients
_SPECTRUM_LATEST_CACHE: dict = {}
_SPECTRUM_LATEST_TS: dict = {}
_SPECTRUM_LATEST_LOCK = threading.Lock()
_SPECTRUM_LATEST_TTL = float(_env('SPECTRUM_LATEST_TTL_SEC', '2.0'))


def _get_spectrum_latest_cached(sdr_id: str = '') -> dict:
    now = time.time()
    key = sdr_id or '__all__'
    with _SPECTRUM_LATEST_LOCK:
        cached = _SPECTRUM_LATEST_CACHE.get(key)
        ts = _SPECTRUM_LATEST_TS.get(key, 0)
        if cached is not None and (now - ts) < _SPECTRUM_LATEST_TTL:
            return cached
    result = _query_latest_sweep(sdr_id)
    with _SPECTRUM_LATEST_LOCK:
        _SPECTRUM_LATEST_CACHE[key] = result
        _SPECTRUM_LATEST_TS[key] = time.time()
    return result


@app.get('/api/spectrum/latest')
async def get_spectrum_latest(_user=Depends(get_current_user), sdr_id: str = ''):
    """Return the latest full FM sweep (88-108 MHz) — cached server-side so many clients share one query.
    If sdr_id is specified, only returns data from that SDR.
    """
    return await _run_in_thread(_get_spectrum_latest_cached, sdr_id)


@app.get('/api/analytics/occupancy')
async def get_occupancy(_user=Depends(get_current_user)):
    """Downsample rf_sweep_data into 15-min x freq buckets for the heatmap.
    Returns at most MAX_FREQ_BINS × MAX_TIME_SLOTS cells to keep JSON small.
    """
    from datetime import timedelta, datetime as _dtt, timezone as _tz
    import math

    MAX_FREQ_BINS  = 80
    MAX_TIME_SLOTS = 96   # 24h ÷ 15min = 96 max anyway

    db = SessionLocal()
    try:
        since = _dtt.utcnow() - timedelta(hours=24)

        # GROUP BY on the server — only ~96 × N_FREQ rows come back, not 187K
        rows = (
            db.query(
                func.floor(func.extract('epoch', SweepData.timestamp) / 900).label('slot'),
                SweepData.frequency_mhz,
                func.avg(SweepData.power_dbfs).label('avg_power'),
            )
            .filter(SweepData.timestamp >= since)
            .filter(SweepData.frequency_mhz >= 88700)
            .filter(SweepData.frequency_mhz <= 108100)
            .group_by('slot', SweepData.frequency_mhz)
            .order_by('slot', SweepData.frequency_mhz)
            .all()
        )

        if not rows:
            return {'freqs': [], 'slots': [], 'matrix': [], 'min_power': -90, 'max_power': -20}

        # Convert kHz storage to MHz, build lookup
        # freq stored as kHz (e.g. 91000.0 = 91.0 MHz)
        lookup = {}
        for r in rows:
            freq_mhz = round(float(r.frequency_mhz) / 1000.0, 2)
            slot = int(r.slot)
            lookup[(slot, freq_mhz)] = round(float(r.avg_power), 1)

        all_slots = sorted(set(k[0] for k in lookup))
        all_freqs = sorted(set(k[1] for k in lookup))

        # Bin frequencies if too many (merge adjacent bins)
        if len(all_freqs) > MAX_FREQ_BINS:
            step = math.ceil(len(all_freqs) / MAX_FREQ_BINS)
            binned_freqs = []
            for i in range(0, len(all_freqs), step):
                chunk = all_freqs[i:i+step]
                binned_freqs.append(round(sum(chunk) / len(chunk), 2))
            freq_groups = [all_freqs[i:i+step] for i in range(0, len(all_freqs), step)]
        else:
            binned_freqs = all_freqs
            freq_groups = [[f] for f in all_freqs]

        # Slot labels
        def slot_label(s):
            dt = _dtt.fromtimestamp(s * 900, tz=_tz.utc)
            return dt.strftime('%H:%M')

        # Build compact matrix: list of {"s": slot_label, "v": [power, ...]}
        # None encoded as null-friendly sentinel (-999) to keep JSON minimal
        matrix_rows = []
        for s in all_slots:
            vals = []
            for fg in freq_groups:
                powers = [lookup.get((s, f)) for f in fg if lookup.get((s, f)) is not None]
                vals.append(round(sum(powers) / len(powers), 1) if powers else None)
            matrix_rows.append(vals)

        # Power range for colour scale
        all_vals = [v for row in matrix_rows for v in row if v is not None]
        min_p = round(min(all_vals), 1) if all_vals else -90
        max_p = round(max(all_vals), 1) if all_vals else -20

        return {
            'freqs':     binned_freqs,
            'slots':     [slot_label(s) for s in all_slots],
            'matrix':    matrix_rows,
            'min_power': min_p,
            'max_power': max_p,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# SPA fallback route - MUST be last to avoid intercepting API calls
# ---------------------------------------------------------------------------
@app.get("/{filename:path}")
async def serve_public_file(filename: str):
    # Skip API paths - let them be handled by API routes
    if filename.startswith("api/"):
        return JSONResponse({"error": "API endpoint not found"}, status_code=404)
    
    import pathlib as _pl
    p = _pl.Path(DIST_DIR) / filename
    ext = p.suffix.lower()
    # Only serve known static file types directly; everything else → index.html
    if ext in _PUBLIC_EXTS and p.is_file():
        return FileResponse(str(p))
    # SPA fallback
    index_path = _pl.Path(DIST_DIR) / "index.html"
    if index_path.is_file():
        return FileResponse(str(index_path), headers={"Cache-Control": "no-store"})
    return JSONResponse({"error": "not found"}, status_code=404)


if __name__ == "__main__":
    if 'testmode' in sys.argv:
        sys.argv = [a for a in sys.argv if a != 'testmode']
        os.environ['DRI_TESTMODE'] = '1'
        print("[server] TESTMODE active — login/auth disabled")

    import signal

    _shutdown_state = [False]

    def _graceful_shutdown(*_):
        if _shutdown_state[0]:
            print("\n[server] Force exit")
            os._exit(0)
        _shutdown_state[0] = True
        print("\n[server] Shutting down gracefully...")
        if _uvicorn_server is not None:
            _uvicorn_server.should_exit = True
            _uvicorn_server.force_exit = True
        else:
            os._exit(0)

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    server_thread = threading.Thread(target=run_server, daemon=False, name="uvicorn-server")
    server_thread.start()

    time.sleep(1.0)

    _webview_url = os.environ.get('PUBLIC_BASE_URL', '').strip().rstrip('/') or 'http://localhost:8000'
    import webbrowser
    webbrowser.open(_webview_url)
    print(f"[server] Opening browser: {_webview_url}")

    # Poll the thread so Windows has a chance to run the SIGINT handler.
    while server_thread.is_alive():
        server_thread.join(timeout=0.5)