/**
 * ActivityTracker — lightweight frontend activity tracking singleton.
 *
 * Usage:
 *   import tracker from './ActivityTracker';
 *   tracker.track('CLICK_EXPORT', 'UI', { format: 'csv' });
 *
 * The tracker batches events and flushes to POST /api/activity every 30s
 * or when the queue reaches 100 events. On page unload it fires synchronously.
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? '';
const FLUSH_INTERVAL_MS = 30000;
const BATCH_MAX = 100;
const MIN_FLUSH_GAP_MS = 5000;

class ActivityTracker {
  constructor() {
    this._queue = [];
    this._sessionId = this._getOrCreateSession();
    this._timer = null;
    this._started = false;
    this._lastFlush = 0;
  }

  _getOrCreateSession() {
    let sid = sessionStorage.getItem('_act_sid');
    if (!sid) {
      sid = Math.random().toString(36).slice(2) + Date.now().toString(36);
      sessionStorage.setItem('_act_sid', sid);
    }
    return sid;
  }

  start() {
    if (this._started) return;
    this._started = true;
    this._timer = setInterval(() => this.flush(), FLUSH_INTERVAL_MS);

    window.addEventListener('beforeunload', () => this._flushSync());

    window.addEventListener('error', (e) => {
      this.track('JS_ERROR', 'ERROR', {
        message: e.message,
        source: e.filename,
        line: e.lineno,
        col: e.colno,
      });
      this._flushSync();
    });

    window.addEventListener('unhandledrejection', (e) => {
      this.track('UNHANDLED_PROMISE', 'ERROR', {
        reason: String(e.reason).slice(0, 200),
      });
    });

    this.track('PAGE_LOAD', 'NAV', {
      url: window.location.pathname,
      referrer: document.referrer || null,
    });
  }

  track(event_type, category = 'UI', payload = {}) {
    this._queue.push({
      event_type,
      category,
      payload,
      timestamp: new Date().toISOString(),
    });
    if (this._queue.length >= BATCH_MAX) {
      this.flush();
    }
  }

  async flush() {
    if (this._queue.length === 0) return;
    const now = Date.now();
    // If we haven't hit the batch max, throttle flushes so rapid small
    // event bursts don't spam the server with back-to-back requests.
    if (this._queue.length < BATCH_MAX && now - this._lastFlush < MIN_FLUSH_GAP_MS) {
      return;
    }
    this._lastFlush = now;
    const batch = this._queue.splice(0, BATCH_MAX);
    try {
      await fetch(`${API_BASE}/api/activity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ events: batch, session_id: this._sessionId }),
      });
    } catch {
      // Re-queue on network error (up to 50 events to avoid unbounded growth)
      if (this._queue.length < 50) {
        this._queue.unshift(...batch);
      }
    }
  }

  _flushSync() {
    if (this._queue.length === 0) return;
    const batch = this._queue.splice(0);
    const body = JSON.stringify({ events: batch, session_id: this._sessionId });
    // sendBeacon is fire-and-forget, perfect for unload
    if (navigator.sendBeacon) {
      const blob = new Blob([body], { type: 'application/json' });
      navigator.sendBeacon(`${API_BASE}/api/activity`, blob);
    }
  }

  stop() {
    if (this._timer) clearInterval(this._timer);
    this._started = false;
  }
}

const tracker = new ActivityTracker();
export default tracker;
