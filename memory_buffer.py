"""
High-performance in-memory ring buffer for real-time SDR data.
Replaces 4 Hz database polling with memory-based access.
"""

import asyncio
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
# Using pure Python instead of numpy for compatibility
import threading

from models import SessionLocal, SweepData

class SDRMemoryBuffer:
    """Thread-safe ring buffer for last 60 seconds of SDR data."""
    
    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self.buffer = deque(maxlen=window_seconds * 4)  # 4 Hz sampling
        self.lock = threading.RLock()
        self.latest_spectrum = None
        self._running = False
        self._task = None
        
    def start_background_sync(self):
        """Start background thread to sync data from DB to memory."""
        if self._running:
            return
            
        self._running = True
        def sync_loop():
            while self._running:
                try:
                    self._sync_from_db()
                    time.sleep(5.0)  # Sync every 5 seconds
                except Exception as e:
                    print(f"[memory_buffer] sync error: {e}")
                    time.sleep(10.0)  # Back off on error
                    
        thread = threading.Thread(target=sync_loop, daemon=True)
        thread.start()
        
    def stop(self):
        """Stop background sync."""
        self._running = False
        
    # Known FM stations in kHz (must match _KNOWN_STATIONS_MHZ in server.py, skip index 0 DAB+)
    _KNOWN_FM_KHZ = [90000, 90700, 92500, 93000, 93300, 97700, 100000, 100500,
                     101100, 103300, 104000, 105000, 105500, 106000, 106300, 107000, 107700]
    _DAB_12A_KHZ = 223936

    def _sync_from_db(self):
        """Load latest per-station row from database into memory buffer."""
        from sqlalchemy import text
        db = SessionLocal()
        try:
            # One latest row per 100-kHz bucket — same recursive loose-index-scan CTE as _fetch_latest_spectrum
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

            dab_row = (
                db.query(SweepData)
                .filter(SweepData.frequency_mhz == self._DAB_12A_KHZ)
                .order_by(SweepData.id.desc())
                .first()
            )

            if not rows:
                return

            self._process_sweep_data(rows, dab_row)

        finally:
            db.close()

    def _process_sweep_data(self, rows: List[SweepData], dab_row=None):
        """Process sweep data into spectrum format — mirrors _fetch_latest_spectrum logic."""
        if not rows:
            return

        freqs_khz = [r.frequency_mhz for r in rows]
        powers    = [r.power_dbfs    for r in rows]
        ids       = [r.id            for r in rows]

        sorted_powers = sorted(powers)
        noise_floor = sorted_powers[len(sorted_powers) // 4] if sorted_powers else -90.0
        peak_power  = max(powers) if powers else -90.0
        snr_peak    = peak_power - noise_floor
        is_interference = bool(snr_peak > 20.0 or peak_power > -60.0)
        peak_intensity  = max(0.0, min(1.0, (peak_power + 100.0) / 60.0))

        # DAB+ 12A at position 0
        panoramic_peaks = []
        if dab_row:
            panoramic_peaks.append({"freq": 88.7, "dbfs": float(dab_row.power_dbfs),
                                     "ts": dab_row.timestamp.timestamp()})
        else:
            panoramic_peaks.append({"freq": 88.7, "dbfs": -95.0, "ts": None})

        station_scan = {}
        if dab_row:
            dab_snr = float(dab_row.power_dbfs) - noise_floor
            station_scan["0"] = {"pwr": float(dab_row.power_dbfs), "snr": round(dab_snr, 1),
                                  "ts": dab_row.timestamp.timestamp()}
        else:
            station_scan["0"] = {"pwr": -95.0, "snr": 0.0, "ts": 0.0}

        # For each known FM station pick the row with the highest id (most recent)
        for sidx, known_khz in enumerate(self._KNOWN_FM_KHZ, start=1):
            best_id  = -1
            best_pwr = -95.0
            best_ts  = 0.0
            for fkhz, pwr, rid, row in zip(freqs_khz, powers, ids, rows):
                if abs(fkhz - known_khz) < 150 and rid > best_id:
                    best_id  = rid
                    best_pwr = pwr
                    best_ts  = row.timestamp.timestamp()

            panoramic_peaks.append({"freq": known_khz / 1000.0, "dbfs": float(best_pwr), "ts": best_ts})
            station_scan[str(sidx)] = {
                "pwr": float(best_pwr),
                "snr": round(float(best_pwr) - noise_floor, 1),
                "ts":  best_ts,
            }

        self.latest_spectrum = {
            "peak_intensity":  peak_intensity,
            "panoramic_peaks": panoramic_peaks,
            "station_scan":    station_scan,
            "is_interference": is_interference,
            "playing_idx":     None,
            "log_running":     False,
            "log_path":        "",
        }
        
        # Add to buffer
        with self.lock:
            self.buffer.append({
                "timestamp": time.time(),
                "spectrum": self.latest_spectrum
            })
            
    def get_latest_spectrum(self) -> Optional[Dict]:
        """Get the latest spectrum data from memory."""
        with self.lock:
            if self.latest_spectrum:
                return self.latest_spectrum.copy()
            return None
            
    def get_buffer_stats(self) -> Dict:
        """Get buffer statistics for monitoring."""
        with self.lock:
            return {
                "buffer_size": len(self.buffer),
                "max_size": self.buffer.maxlen,
                "latest_timestamp": self.buffer[-1]["timestamp"] if self.buffer else None,
                "age_seconds": time.time() - self.buffer[-1]["timestamp"] if self.buffer else None
            }

# Global instance
_sdr_buffer = SDRMemoryBuffer()

def get_sdr_buffer() -> SDRMemoryBuffer:
    """Get the global SDR buffer instance."""
    return _sdr_buffer

def start_sdr_buffer():
    """Initialize and start the SDR buffer."""
    _sdr_buffer.start_background_sync()
    return _sdr_buffer
