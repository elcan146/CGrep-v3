"""
dab11b_monitor.py — DAB+ 11B Channel Monitor for HTOOL SDR 6
============================================================
Continuously monitors the DAB+ 11B multiplex (218.640 MHz) and logs
signal quality metrics to PostgreSQL.

DAB+ 11B Channel Specs:
    Center Frequency: 218.640 MHz
    Bandwidth: 1.536 MHz (DAB+ standard)
    
Dependencies:
    pip install pyrtlsdr numpy sqlalchemy psycopg2-binary pyyaml

Configuration (config.env or environment variables):
    DB_HOST             PostgreSQL server address               default: localhost
    DB_PORT             PostgreSQL port                         default: 5432
    DB_NAME             Database name                           default: sdr_db
    DB_USER             PostgreSQL user                         default: postgres
    POSTGRES_PASSWORD   PostgreSQL password                     default: root
    SDR_GAIN            RTL-SDR gain in dB                      default: 49.6
    MONITOR_INTERVAL    Seconds between measurements            default: 1.0
    TAILSCALE_IP        Tailscale IP for remote connection      default: (uses DB_HOST)
    
    # DAB+ Detection Settings
    SNR_THRESHOLD       Minimum SNR for "active" status         default: 6.0
    POWER_THRESHOLD     Minimum power for detection             default: -75.0

Quick start:
    python dab11b_monitor.py
    
With Tailscale:
    $env:TAILSCALE_IP = "100.x.x.x"
    python dab11b_monitor.py
"""

import os
import sys
import time
import signal
import datetime
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

import numpy as np
import yaml

# ---------------------------------------------------------------------------
# Configuration loading (matches sdr_collector.py style)
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
    """Load YAML configuration."""
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
                print(f"[dab11b] Warning: Could not load {path}: {e}")
    
    return {}


# Load configurations
_load_env_file()
YAML_CONFIG = _load_yaml_config()


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
# DB configuration
# ---------------------------------------------------------------------------
DB_USER        = _get_config("DB_USER", "postgres", "database.user")
DB_PASSWORD    = _get_config("POSTGRES_PASSWORD", "changeme", "database.password")
DB_HOST        = _get_config("DB_HOST", "localhost", "database.host")
DB_PORT        = int(_get_config("DB_PORT", "5432", "database.port"))
DB_NAME        = _get_config("DB_NAME", "sdr_db", "database.name")

# Tailscale Configuration
TAILSCALE_IP   = os.getenv("TAILSCALE_IP", YAML_CONFIG.get("tailscale", {}).get("ip", ""))
USE_TAILSCALE  = TAILSCALE_IP and TAILSCALE_IP.strip()

# SDR Configuration
SDR_GAIN           = float(_get_config("SDR_GAIN", "49.6", "sdr.gain"))
MONITOR_INTERVAL   = float(_get_config("MONITOR_INTERVAL", "1.0", "sdr.interval"))

# DAB+ Detection Settings
SNR_THRESHOLD      = float(_get_config("SNR_THRESHOLD", "6.0", "detector.snr_threshold"))
POWER_THRESHOLD    = float(_get_config("POWER_THRESHOLD", "-75.0", "detector.power_threshold"))

# Use Tailscale IP if provided
effective_host = TAILSCALE_IP if USE_TAILSCALE else DB_HOST
if USE_TAILSCALE:
    print(f"[dab11b] Using Tailscale IP: {TAILSCALE_IP}")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{effective_host}:{DB_PORT}/{DB_NAME}"

# ---------------------------------------------------------------------------
# DAB+ 11B Channel Constants
# ---------------------------------------------------------------------------
DAB_11B_FREQ_MHZ   = 218.640          # Center frequency in MHz
DAB_11B_FREQ_HZ    = 218.640e6        # Center frequency in Hz
DAB_BANDWIDTH_HZ   = 1.536e6          # DAB+ standard bandwidth
DAB_BANDWIDTH_KHZ  = 1536             # DAB+ bandwidth in kHz

# SDR Settings optimized for DAB+ reception
SDR_SAMPLE_RATE    = 2.4e6            # 2.4 MHz sample rate
TUNE_OFFSET_HZ     = 333e3            # Offset to push DC spike outside DAB block
FFT_SIZE           = 8192             # Higher resolution for DAB bandwidth analysis
CALIBRATION_DB     = 41.0             # Same calibration as sdr_collector

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class DAB11BReading:
    """DAB+ 11B channel measurement result."""
    timestamp: datetime.datetime
    center_freq_mhz: float
    power_dbfs: float
    noise_floor_dbfs: float
    snr_db: float
    signal_variance: float
    is_active: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'center_freq_mhz': self.center_freq_mhz,
            'power_dbfs': self.power_dbfs,
            'noise_floor_dbfs': self.noise_floor_dbfs,
            'snr_db': self.snr_db,
            'signal_variance': self.signal_variance,
            'is_active': self.is_active,
        }


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------
def _setup_db():
    """Setup database tables for DAB+ 11B monitoring."""
    from sqlalchemy import create_engine, Column, Integer, Float, DateTime, Boolean, text
    from sqlalchemy.orm import declarative_base, sessionmaker

    # Auto-create database if missing
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

    class DAB11BData(Base):
        """DAB+ 11B channel measurements."""
        __tablename__ = "dab_11b_data"
        id                = Column(Integer, primary_key=True, index=True)
        timestamp         = Column(DateTime, default=datetime.datetime.utcnow, index=True)
        center_freq_mhz   = Column(Float, default=DAB_11B_FREQ_MHZ)
        power_dbfs        = Column(Float)
        noise_floor_dbfs  = Column(Float)
        snr_db            = Column(Float)
        signal_variance   = Column(Float)
        is_active         = Column(Boolean, default=False)

    class DAB11BEvent(Base):
        """DAB+ 11B channel state change events."""
        __tablename__ = "dab_11b_events"
        id                = Column(Integer, primary_key=True, index=True)
        timestamp         = Column(DateTime, default=datetime.datetime.utcnow)
        event_type        = Column(Integer)  # 1=up, 0=down
        power_dbfs        = Column(Float)
        snr_db            = Column(Float)

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    logger.info(f"DB ready → {effective_host}:{DB_PORT}/{DB_NAME}")
    return Session, DAB11BData, DAB11BEvent


# ---------------------------------------------------------------------------
# SDR Interface
# ---------------------------------------------------------------------------
def _open_sdr():
    """Initialize RTL-SDR optimized for DAB+ 11B monitoring."""
    try:
        from rtlsdr import RtlSdr
    except ImportError:
        logger.error("pyrtlsdr not installed. Run: pip install pyrtlsdr")
        sys.exit(1)

    sdr = RtlSdr()
    sdr.sample_rate = SDR_SAMPLE_RATE
    sdr.err_ppm = 0  # Adjust if your SDR has frequency error
    
    # HTOOL SDR 6 optimized settings
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    sdr.gain = SDR_GAIN
    actual_gain = sdr.gain

    logger.info(f"RTL-SDR opened (HTOOL SDR 6) fs={SDR_SAMPLE_RATE/1e6:.3f} MHz gain={actual_gain} dB")
    return sdr


def _measure_dab11b(sdr) -> Optional[DAB11BReading]:
    """
    Measure DAB+ 11B channel signal quality.
    
    Returns DAB11BReading with signal metrics or None on error.
    """
    # Tune with offset to avoid DC spike in the DAB bandwidth
    tune_freq = DAB_11B_FREQ_HZ - TUNE_OFFSET_HZ
    sdr.center_freq = int(tune_freq)
    
    # Wait for PLL lock and flush stale samples
    time.sleep(0.06)
    for _ in range(3):
        _ = sdr.read_samples(256 * 1024)
    
    # Read samples for analysis
    samples = sdr.read_samples(FFT_SIZE * 4)
    samples = np.asarray(samples, dtype=np.complex64)[-FFT_SIZE:]
    
    # Remove DC component
    samples = samples - np.mean(samples)
    
    # Apply Blackman window for better frequency resolution
    window = np.blackman(len(samples))
    windowed = samples * window
    
    # FFT analysis
    fft_data = np.fft.fftshift(np.fft.fft(windowed))
    fft_mag = np.abs(fft_data) / len(samples)
    dbfs_spectrum = 20 * np.log10(fft_mag + 1e-12)
    
    # Apply calibration offset
    dbfs_spectrum = dbfs_spectrum + CALIBRATION_DB
    
    # Smooth spectrum for analysis
    kernel_size = 50
    kernel = np.ones(kernel_size) / kernel_size
    smoothed = np.convolve(dbfs_spectrum, kernel, mode='same')
    
    # Frequency axis
    freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/sdr.sample_rate))
    actual_freqs = sdr.center_freq + freqs
    
    # Define masks for DAB+ bandwidth analysis
    # DAB+ 11B center is at DAB_11B_FREQ_HZ, bandwidth is ±768 kHz
    half_bw = DAB_BANDWIDTH_HZ / 2
    
    # Signal mask: the actual DAB+ 1.536 MHz bandwidth
    signal_mask = (actual_freqs >= DAB_11B_FREQ_HZ - half_bw) & \
                  (actual_freqs <= DAB_11B_FREQ_HZ + half_bw)
    
    # Noise mask: regions outside the DAB+ bandwidth (guard bands)
    # Use 200 kHz guard regions on each side
    noise_mask = ((actual_freqs >= DAB_11B_FREQ_HZ - half_bw - 400e3) & \
                  (actual_freqs < DAB_11B_FREQ_HZ - half_bw)) | \
                 ((actual_freqs > DAB_11B_FREQ_HZ + half_bw) & \
                  (actual_freqs <= DAB_11B_FREQ_HZ + half_bw + 400e3))
    
    if not np.any(signal_mask) or not np.any(noise_mask):
        logger.warning("Invalid frequency masks - check SDR tuning")
        return None
    
    # Calculate metrics
    signal_power = np.mean(smoothed[signal_mask])
    noise_floor = np.median(smoothed[noise_mask])
    snr = signal_power - noise_floor
    
    # Calculate signal variance (indicates modulation activity)
    signal_variance = np.var(smoothed[signal_mask])
    
    # Determine if DAB+ is active (signal present and modulated)
    is_active = snr > SNR_THRESHOLD and signal_power > POWER_THRESHOLD
    
    return DAB11BReading(
        timestamp=datetime.datetime.now(),
        center_freq_mhz=DAB_11B_FREQ_MHZ,
        power_dbfs=round(signal_power, 2),
        noise_floor_dbfs=round(noise_floor, 2),
        snr_db=round(snr, 2),
        signal_variance=round(signal_variance, 6),
        is_active=is_active
    )


# ---------------------------------------------------------------------------
# Main monitor loop
# ---------------------------------------------------------------------------
def run_monitor():
    """Main DAB+ 11B monitoring loop."""
    Session, DAB11BData, DAB11BEvent = _setup_db()
    db = Session()
    sdr = _open_sdr()
    
    reading_count = 0
    running = True
    last_active_state = None  # Track state changes
    
    def _stop(sig, frame):
        nonlocal running
        logger.info("Stopping DAB+ 11B monitor...")
        running = False
    
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    
    logger.info(f"DAB+ 11B Monitor started")
    logger.info(f"  Frequency: {DAB_11B_FREQ_MHZ} MHz")
    logger.info(f"  Bandwidth: {DAB_BANDWIDTH_KHZ} kHz")
    logger.info(f"  Sample Rate: {SDR_SAMPLE_RATE/1e6} MHz")
    logger.info(f"  Interval: {MONITOR_INTERVAL}s")
    logger.info(f"  SNR Threshold: {SNR_THRESHOLD} dB")
    
    if USE_TAILSCALE:
        logger.info(f"  Tailscale: {TAILSCALE_IP}")
    
    try:
        while running:
            t0 = time.monotonic()
            
            try:
                reading = _measure_dab11b(sdr)
                
                if reading is None:
                    logger.warning("Failed to measure DAB+ 11B - retrying")
                    time.sleep(0.5)
                    continue
                
                # Store reading in database
                db_record = DAB11BData(
                    timestamp=reading.timestamp,
                    center_freq_mhz=reading.center_freq_mhz,
                    power_dbfs=reading.power_dbfs,
                    noise_floor_dbfs=reading.noise_floor_dbfs,
                    snr_db=reading.snr_db,
                    signal_variance=reading.signal_variance,
                    is_active=reading.is_active,
                )
                db.add(db_record)
                
                # Detect and log state changes
                if last_active_state is not None and reading.is_active != last_active_state:
                    event = DAB11BEvent(
                        timestamp=reading.timestamp,
                        event_type=1 if reading.is_active else 0,
                        power_dbfs=reading.power_dbfs,
                        snr_db=reading.snr_db,
                    )
                    db.add(event)
                    state_str = "ACTIVE" if reading.is_active else "INACTIVE"
                    logger.info(f"State change: DAB+ 11B is now {state_str}")
                
                last_active_state = reading.is_active
                db.commit()
                
                reading_count += 1
                
                # Console output every 10 readings or on state change
                if reading_count % 10 == 0 or (reading.is_active != last_active_state):
                    status = "ON " if reading.is_active else "OFF"
                    logger.info(
                        f"[{reading_count:04d}] 11B: {status} | "
                        f"Power: {reading.power_dbfs:6.1f} dBFS | "
                        f"Floor: {reading.noise_floor_dbfs:6.1f} dBFS | "
                        f"SNR: {reading.snr_db:5.1f} dB"
                    )
                else:
                    # Quiet mode - just debug
                    logger.debug(
                        f"Power: {reading.power_dbfs:.1f} dBFS, "
                        f"SNR: {reading.snr_db:.1f} dB, "
                        f"Active: {reading.is_active}"
                    )
                
            except Exception as e:
                logger.error(f"Measurement error: {e}")
                db.rollback()
            
            # Maintain consistent interval
            elapsed = time.monotonic() - t0
            wait = max(0.0, MONITOR_INTERVAL - elapsed)
            if wait > 0:
                time.sleep(wait)
    
    finally:
        try:
            sdr.close()
        except Exception:
            pass
        db.close()
        logger.info(f"Monitor stopped. Total readings: {reading_count}")


if __name__ == "__main__":
    run_monitor()
