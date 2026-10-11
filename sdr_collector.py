"""
sdr_collector.py — RTL-SDR → PostgreSQL collector with rtl-sdr-analyzer features
==================================================================================
Run this on the laptop with HTOOL SDR 6 plugged in.
Sweeps specific FM frequencies and pushes detected peaks to PostgreSQL via Tailscale IP.

Based on: https://github.com/msalexms/rtl-sdr-analyzer

Dependencies:
    pip install pyrtlsdr numpy scipy sqlalchemy psycopg2-binary pyyaml

Configuration (config.env or environment variables):
    DB_HOST             PostgreSQL server address               default: localhost
    DB_PORT             PostgreSQL port                         default: 5432
    DB_NAME             Database name                           default: sdr_db
    DB_USER             PostgreSQL user                         default: postgres
    POSTGRES_PASSWORD   PostgreSQL password                     default: root
    SDR_GAIN            RTL-SDR gain in dB                      default: 28.0
    SWEEP_INTERVAL      Seconds between sweeps                  default: 2.0
    TAILSCALE_IP        Tailscale IP for remote connection      default: (uses DB_HOST)

    # rtl-sdr-analyzer style detection settings
    POWER_THRESHOLD     Power threshold for detection           default: -70.0
    Z_SCORE_THRESHOLD   Z-score threshold for anomaly detection   default: 1.5

Quick start (same machine):
    python sdr_collector.py

With Tailscale (remote laptop to PC):
    $env:TAILSCALE_IP = "100.x.x.x"  # Your PC's Tailscale IP
    $env:POSTGRES_PASSWORD = "changeme"  # TODO: set your own password
    python sdr_collector.py
"""

import os
import sys
import time
import signal
import datetime
import logging
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass

import numpy as np
import yaml

# ---------------------------------------------------------------------------
# Configuration loading (rtl-sdr-analyzer style: env > YAML > defaults)
# ---------------------------------------------------------------------------
def _load_env_file():
    """Load config.env from same directory as the exe/script."""
    if getattr(sys, 'frozen', False):
        _base = os.path.dirname(sys.executable)
    else:
        _base = os.path.dirname(os.path.abspath(__file__))

    env_path = os.path.join(_base, "config.env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _load_yaml_config() -> Dict[str, Any]:
    """Load YAML configuration (rtl-sdr-analyzer style)."""
    if getattr(sys, 'frozen', False):
        _base = os.path.dirname(sys.executable)
    else:
        _base = os.path.dirname(os.path.abspath(__file__))
    
    config_paths = [
        os.path.join(_base, "sdr_config.yml"),
        os.path.join(_base, "config.yml"),
    ]
    
    for path in config_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                print(f"[collector] Warning: Could not load {path}: {e}")
    
    return {}


# Load configurations
_load_env_file()
YAML_CONFIG = _load_yaml_config()

# Debug: Show what SDR_GAIN env var is set to
_raw_gain = os.getenv("SDR_GAIN")
print(f"[config] SDR_GAIN env var: {_raw_gain}")
print(f"[config] SDR_GAIN will use: {_raw_gain if _raw_gain else '28.0 (default)'}")


def _get_config(key: str, default: str, yaml_path: Optional[str] = None) -> str:
    """Get config with priority: env var > YAML > default."""
    env_val = os.getenv(key)
    if env_val is not None:
        return env_val
    
    if yaml_path:
        parts = yaml_path.split(".")
        val = YAML_CONFIG
        for part in parts:
            if isinstance(val, dict) and part in val:
                val = val[part]
            else:
                val = None
                break
        if val is not None:
            return str(val)
    
    return default

# ---------------------------------------------------------------------------
# DB configuration with Tailscale support (rtl-sdr-analyzer style)
# ---------------------------------------------------------------------------
DB_USER        = _get_config("DB_USER", "postgres", "database.user")
DB_PASSWORD    = _get_config("POSTGRES_PASSWORD", "changeme", "database.password")
DB_HOST        = _get_config("DB_HOST", "localhost", "database.host")
DB_PORT        = int(_get_config("DB_PORT", "5432", "database.port"))
DB_NAME        = _get_config("DB_NAME", "sdr_db", "database.name")

# Tailscale Configuration (for remote laptop → PC connection)
TAILSCALE_IP   = os.getenv("TAILSCALE_IP", YAML_CONFIG.get("tailscale", {}).get("ip", ""))
USE_TAILSCALE  = TAILSCALE_IP and TAILSCALE_IP.strip()

# Antenna detection test mode (set to 1 to see measured values)
ANTENNA_TEST_MODE = os.getenv("ANTENNA_TEST_MODE", "0") == "1"

# SDR Configuration
SDR_GAIN       = float(_get_config("SDR_GAIN", "28.0", "sdr.gain"))
SWEEP_INTERVAL = float(_get_config("SWEEP_INTERVAL", "2.0", "sdr.interval"))

# rtl-sdr-analyzer Detection Configuration
POWER_THRESHOLD   = float(_get_config("POWER_THRESHOLD", "-70.0", "detector.power_threshold"))
Z_SCORE_THRESHOLD = float(_get_config("Z_SCORE_THRESHOLD", "1.5", "detector.z_score_threshold"))
DETECTION_WINDOW  = int(_get_config("DETECTION_WINDOW", "5", "detector.detection_window"))
MIN_DURATION      = float(_get_config("MIN_DURATION", "0.1", "detector.min_duration"))

# Use Tailscale IP if provided, otherwise use DB_HOST
effective_host = TAILSCALE_IP if USE_TAILSCALE else DB_HOST
if USE_TAILSCALE:
    print(f"[collector] Using Tailscale IP: {TAILSCALE_IP}")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{effective_host}:{DB_PORT}/{DB_NAME}"

# ---------------------------------------------------------------------------
# SDR sweep settings — matches sweep20.py and rtl-sdr-analyzer
# ---------------------------------------------------------------------------
SWEEP_START_HZ  = 87.5e6
SWEEP_STOP_HZ   = 108.0e6
SWEEP_STEP_HZ   = 2.4e6
SWEEP_FS        = 2.4e6
SWEEP_FFT_SIZE  = 2048
SWEEP_CROP_BINS = 32          # bins to drop each edge
DC_NOTCH_HW     = 4           # DC spike half-width in bins
CALIBRATION_DB  = 41.0        # calibration offset to match SDR# visual scale

# Known FM stations (must match dashboard STATIONS array)
KNOWN_STATIONS_MHZ = [
    88.7, 90.0, 90.7, 92.5, 93.0, 93.3, 97.7,
    100.0, 100.5, 101.1,
    103.3, 104.0, 105.0, 105.5, 106.0, 106.3, 107.0, 107.7,
]

# ---------------------------------------------------------------------------
# Logging setup (rtl-sdr-analyzer style)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB schema (mirrors models.py — no dependency on that file needed)
# ---------------------------------------------------------------------------
def _setup_db():
    from sqlalchemy import create_engine, Column, Integer, Float, DateTime, String, text
    from sqlalchemy.orm import declarative_base, sessionmaker

    # Auto-create sdr_db if missing (use effective_host for Tailscale)
    admin_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{effective_host}:{DB_PORT}/postgres"
    try:
        admin_engine = create_engine(admin_url)
        with admin_engine.connect() as conn:
            conn.execute(text("commit"))
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": DB_NAME}
            ).fetchone()
            if not exists:
                conn.execute(text(f"CREATE DATABASE {DB_NAME}"))
                logger.info(f"Created database '{DB_NAME}'")
        admin_engine.dispose()
    except Exception as e:
        logger.warning(f"Could not check/create DB: {e}")

    Base = declarative_base()

    class SweepData(Base):
        __tablename__ = "rf_sweep_data"
        id            = Column(Integer, primary_key=True, index=True)
        timestamp     = Column(DateTime, default=datetime.datetime.utcnow)
        frequency_mhz = Column(Float, index=True)
        power_dbfs    = Column(Float)
        snr_db        = Column(Float)

    class SignalEvent(Base):
        """rtl-sdr-analyzer style event logging."""
        __tablename__ = "signal_events"
        id            = Column(Integer, primary_key=True, index=True)
        timestamp     = Column(DateTime, default=datetime.datetime.utcnow)
        frequency_mhz = Column(Float, index=True)
        power_dbfs    = Column(Float)
        bandwidth_hz  = Column(Float, nullable=True)
        duration_sec  = Column(Float, nullable=True)
        confidence    = Column(Float, nullable=True)
        event_type    = Column(String(50), default="detection")

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    logger.info(f"DB ready → {effective_host}:{DB_PORT}/{DB_NAME}")
    return Session, SweepData, SignalEvent


# ---------------------------------------------------------------------------
# RTL-SDR sweep with rtl-sdr-analyzer features
# ---------------------------------------------------------------------------
@dataclass
class DetectionResult:
    """Signal detection result (rtl-sdr-analyzer style)."""
    frequency_mhz: float
    power_dbfs: float
    snr_db: float
    bandwidth_hz: Optional[float] = None
    confidence: Optional[float] = None
    is_interference: bool = False


def _blackmanharris(n):
    """Blackman-Harris window — same as sweep20.py via calculate.py."""
    a = [0.35875, 0.48829, 0.14128, 0.01168]
    t = 2 * np.pi * np.arange(n) / (n - 1)
    w = (a[0] - a[1]*np.cos(t) + a[2]*np.cos(2*t) - a[3]*np.cos(3*t))
    return w.astype(np.float32)


def _open_sdr():
    """Initialize RTL-SDR with HTOOL SDR 6 optimized settings."""
    try:
        from rtlsdr import RtlSdr
    except ImportError:
        logger.error("pyrtlsdr not installed. Run: pip install pyrtlsdr")
        sys.exit(1)

    sdr = RtlSdr()
    sdr.sample_rate = SWEEP_FS

    # HTOOL SDR 6 optimized settings
    sdr.set_direct_sampling(False)  # Quadrature mode
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    
    logger.debug(f"Setting gain to SDR_GAIN={SDR_GAIN}")
    sdr.gain = SDR_GAIN
    actual_gain = sdr.gain  # Read back actual gain

    logger.info(f"RTL-SDR opened (HTOOL SDR 6) fs={SWEEP_FS/1e6:.3f} MHz gain={actual_gain} dB (requested {SDR_GAIN})")
    return sdr


# Pre-compute window and its coherent gain once
_WIN     = _blackmanharris(SWEEP_FFT_SIZE)
_WIN_NORM = float(np.sum(_WIN))


def _check_antenna_connected(samples: np.ndarray, spectrum_db: np.ndarray, 
                               debug: bool = False) -> bool:
    """Detect if antenna is connected by analyzing signal characteristics.
    
    When antenna is disconnected:
    - Very low signal variance (flat noise)
    - No strong peaks above noise floor
    - Consistent low power across all frequencies
    """
    # Check 1: Signal variance (I/Q samples magnitude)
    iq_variance = np.var(np.abs(samples))
    
    # Check 2: Spectrum dynamic range
    spec_range = np.max(spectrum_db) - np.min(spectrum_db)
    
    # Check 3: Peak signal strength
    peak_power = np.max(spectrum_db)
    
    # Check 4: Mean power (disconnected has very low mean)
    mean_power = np.mean(spectrum_db)
    
    if debug:
        logger.debug(f"Antenna check: var={iq_variance:.6f}, range={spec_range:.1f}dB, "
                    f"peak={peak_power:.1f}dB, mean={mean_power:.1f}dB")
    
    # RELAXED THRESHOLDS for HTOOL SDR 6
    # Disconnected antenna typically has:
    # - variance < 0.0001 (extremely flat)
    # - range < 5 dB (almost flat spectrum)
    # - peak < -50 dBFS (very weak)
    # - mean around -60 dBFS (noise floor)
    is_connected = (
        iq_variance > 0.0005 and     # Very low threshold for variance
        spec_range > 5.0 and          # Some variation in spectrum
        peak_power > -55.0           # Weak signals are OK (was -45)
    )
    
    return is_connected


def _read_chunk(sdr, center_hz):
    """Tune, flush stale USB frames, process one FFT chunk — matches sweep20.py _read_chunk.
    Returns (spectrum_db, freqs_mhz, antenna_connected)
    """
    sdr.center_freq = int(center_hz)
    # Wait for physical PLL to lock (~60ms)
    time.sleep(0.06)
    # Flush stale USB buffer data accumulated during tuning
    sdr.read_samples(512 * 1024)
    # Now read clean settled samples
    raw     = sdr.read_samples(SWEEP_FFT_SIZE * 8)
    samples = np.asarray(raw, dtype=np.complex64)[-SWEEP_FFT_SIZE:]

    # FFT with Blackman-Harris window, coherent gain normalization
    spec = np.fft.fftshift(np.fft.fft(samples * _WIN))
    mag  = np.abs(spec) / _WIN_NORM + 1e-12
    dbfs = 20.0 * np.log10(mag)

    # +24 dB calibration offset — matches sweep20.py CALIBRATION_OFFSET
    db = (dbfs + CALIBRATION_DB).astype(np.float32)

    # DC notch — interpolate across spike (matches sweep20.py)
    ctr  = SWEEP_FFT_SIZE // 2
    lo_n = max(0, ctr - DC_NOTCH_HW)
    hi_n = min(SWEEP_FFT_SIZE - 1, ctr + DC_NOTCH_HW)
    db[lo_n:hi_n] = np.interp(
        np.arange(lo_n, hi_n),
        [lo_n - 1, hi_n],
        [db[lo_n - 1], db[hi_n]]
    )

    # Crop edges
    c = SWEEP_CROP_BINS
    db = db[c: SWEEP_FFT_SIZE - c]

    # Frequency axis
    freqs_hz  = center_hz + np.linspace(-SWEEP_FS / 2, SWEEP_FS / 2, SWEEP_FFT_SIZE)[c: SWEEP_FFT_SIZE - c]
    freqs_mhz = (freqs_hz / 1e6).astype(np.float32)

    # Check antenna status
    antenna_ok = _check_antenna_connected(samples, db, debug=ANTENNA_TEST_MODE)

    return db, freqs_mhz, antenna_ok


def _sweep_once(sdr):
    """Full 87.5–108 MHz sweep. Returns (spectrum_dBFS, freq_axis_MHz, antenna_ok)."""
    spectra, freqs = [], []
    antenna_status = []
    center = SWEEP_START_HZ
    
    while center <= SWEEP_STOP_HZ + SWEEP_STEP_HZ * 0.5:
        try:
            pwr, frq, ant_ok = _read_chunk(sdr, center)
            spectra.append(pwr)
            freqs.append(frq)
            antenna_status.append(ant_ok)
        except Exception as e:
            logger.warning(f"Chunk error at {center/1e6:.1f} MHz: {e}")
        center += SWEEP_STEP_HZ

    if not spectra:
        return None, None, False

    # Antenna is considered connected if majority of chunks show valid signals
    antenna_connected = sum(antenna_status) > len(antenna_status) * 0.5
    
    return (
        np.concatenate(spectra).astype(np.float32), 
        np.concatenate(freqs).astype(np.float32),
        antenna_connected
    )



def _detect_signal_advanced(spectrum: np.ndarray, freqs_mhz: np.ndarray,
                            target_freq: float, noise_floor: float) -> DetectionResult:
    """Advanced signal detection with rtl-sdr-analyzer style features."""
    idx = int(np.abs(freqs_mhz - target_freq).argmin())
    
    # Find peak in wider window (±10 bins)
    lo = max(0, idx - 10)
    hi = min(len(spectrum), idx + 11)
    window = spectrum[lo:hi]
    window_freqs = freqs_mhz[lo:hi]
    
    peak_idx = int(np.argmax(window))
    peak_pwr = float(window[peak_idx])
    peak_freq = float(window_freqs[peak_idx])
    
    snr = peak_pwr - noise_floor
    
    # Estimate bandwidth using -3dB points (rtl-sdr-analyzer style)
    bandwidth_hz = None
    if snr > 3.0:
        threshold = peak_pwr - 3.0
        above_threshold = window > threshold
        if np.any(above_threshold):
            bw_bins = np.sum(above_threshold)
            bin_width_hz = SWEEP_FS / SWEEP_FFT_SIZE
            bandwidth_hz = float(bw_bins * bin_width_hz)
    
    # Confidence score (z-score based, from rtl-sdr-analyzer)
    mean_pwr = float(np.mean(spectrum))
    std_pwr = float(np.std(spectrum))
    confidence = (peak_pwr - mean_pwr) / (std_pwr + 1e-6) if std_pwr > 0 else 0.0
    
    # Interference detection (very high power or abnormal SNR)
    # Threshold: > -20 dBFS (extremely strong) or SNR > 30 dB (unusually clean)
    is_interference = snr > 30.0 or peak_pwr > -20.0
    
    return DetectionResult(
        frequency_mhz=round(peak_freq, 2),
        power_dbfs=round(peak_pwr, 2),
        snr_db=round(snr, 2),
        bandwidth_hz=round(bandwidth_hz, 0) if bandwidth_hz else None,
        confidence=round(confidence, 2),
        is_interference=is_interference
    )


def _find_peaks_advanced(spectrum, freqs_mhz, noise_floor):
    """rtl-sdr-analyzer style peak detection for known stations."""
    results = []
    for station in KNOWN_STATIONS_MHZ:
        detection = _detect_signal_advanced(spectrum, freqs_mhz, station, noise_floor)
        # Only include if above threshold
        if detection.snr_db > 3.0 or detection.power_dbfs > POWER_THRESHOLD:
            results.append(detection)
    
    return results


# ---------------------------------------------------------------------------
# Main collector loop
# ---------------------------------------------------------------------------
def run_collector():
    Session, SweepData, SignalEvent = _setup_db()
    db   = Session()
    sdr  = _open_sdr()

    sweep_num = 0
    running   = True
    antenna_was_connected = True  # Track antenna state changes

    def _stop(sig, frame):
        nonlocal running
        logger.info("Stopping...")
        running = False

    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    logger.info(f"Sweep loop started (interval={SWEEP_INTERVAL}s, threshold={POWER_THRESHOLD} dB)")
    if USE_TAILSCALE:
        logger.info(f"Sending data via Tailscale to {TAILSCALE_IP}")

    try:
        while running:
            t0 = time.monotonic()

            spectrum, freqs_mhz, antenna_connected = _sweep_once(sdr)
            
            # Check antenna status (can be skipped with env var)
            if not os.getenv("SKIP_ANTENNA_CHECK"):
                if not antenna_connected:
                    if antenna_was_connected:
                        logger.error("ANTENNA DISCONNECTED! Check antenna connection.")
                        antenna_was_connected = False
                    logger.warning("Skipping sweep - no antenna detected")
                    time.sleep(1.0)
                    continue
                else:
                    if not antenna_was_connected:
                        logger.info("Antenna reconnected - resuming normal operation")
                        antenna_was_connected = True
            
            if spectrum is None:
                logger.warning("Empty sweep — retrying")
                time.sleep(2.0)
                continue

            noise_floor = float(np.percentile(spectrum, 10))
            p10 = float(np.percentile(spectrum, 10))
            p50 = float(np.percentile(spectrum, 50))
            p90 = float(np.percentile(spectrum, 90))
            
            logger.debug(f"spectrum p10={p10:.1f} p50={p50:.1f} p90={p90:.1f} min={spectrum.min():.1f} max={spectrum.max():.1f}")
            
            # Use rtl-sdr-analyzer style advanced detection
            detections = _find_peaks_advanced(spectrum, freqs_mhz, noise_floor)

            if detections:
                sweep_num += 1
                sweep_ts = datetime.datetime.now()

                # Save sweep data
                sweep_records = [
                    SweepData(
                        timestamp=sweep_ts,
                        frequency_mhz=d.frequency_mhz,
                        power_dbfs=d.power_dbfs,
                        snr_db=d.snr_db,
                    )
                    for d in detections
                ]
                db.bulk_save_objects(sweep_records)

                # Save signal events (rtl-sdr-analyzer style)
                event_records = [
                    SignalEvent(
                        timestamp=sweep_ts,
                        frequency_mhz=d.frequency_mhz,
                        power_dbfs=d.power_dbfs,
                        bandwidth_hz=d.bandwidth_hz,
                        confidence=d.confidence,
                        event_type="interference" if d.is_interference else "detection",
                    )
                    for d in detections if d.confidence and d.confidence > Z_SCORE_THRESHOLD
                ]
                if event_records:
                    db.bulk_save_objects(event_records)

                db.commit()

                freqs = [d.frequency_mhz for d in detections]
                logger.info(f"[sweep #{sweep_num}] {len(detections)} stations @ {sweep_ts.strftime('%H:%M:%S')} "
                           f"| {min(freqs):.1f}–{max(freqs):.1f} MHz "
                           f"| noise={noise_floor:.1f} dBFS")
                
                # Log interference warnings
                for d in detections:
                    if d.is_interference:
                        logger.warning(f"Interference detected: {d.frequency_mhz} MHz @ {d.power_dbfs:.1f} dBFS")
            else:
                logger.debug("No stations detected above threshold")

            elapsed = time.monotonic() - t0
            wait = max(0.0, SWEEP_INTERVAL - elapsed)
            if wait > 0:
                time.sleep(wait)

    finally:
        try:
            sdr.close()
        except Exception:
            pass
        db.close()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    run_collector()
