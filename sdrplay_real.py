#!/usr/bin/env python3
"""
sdrplay_real.py
SDRplay real-time spectrum / power viewer with PostgreSQL logging.

Modes (same as fm_dab_sweep.py):
  Q = Quick mode (wideband 1.5 MHz chunks, ~1s dwell per chunk)
  S = Slow mode  (original per-channel sweep, 100 kHz steps)

Database: sdr_db
Table: rf_sweep_data (id, timestamp, frequency_mhz, power_dbfs, floor_dbfs, snr_db, sdr_id)

Settings mirror what you sent:
  - Sample rate: 2.0 MSps, 1.536 MHz IF bandwidth, decimation 1
  - Zero-IF with an LO offset (SDRuno's "Auto LO offset"), so the measured
    channel never sits on the DC/LO-leakage spike
  - Tuner IF AGC enabled at -30 dBFS; every level is gain-compensated
    (dBm = dBFS + gRdB + lnaGRdB + calibration) so readings are absolute
  - Channel power integrated over the 120 kHz FM filter; noise floor taken
    from the surrounding spectrum and scaled to the same bandwidth
  - Main FFT: 8192 points over a 2 MHz span

Environment:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, POSTGRES_PASSWORD, SDR_ID
    QUICK_MODE_FRAMES  FFT frames per step in Quick mode (default 32)
    CAL_FREQ_MHZ/CAL_DBM  reference station and its SDRuno level (default
                       90.0 MHz / -36.5 dBm); SKIP_CAL=1 logs raw dBFS+gain
    SDR_AGC=0          disable the tuner IF AGC and use fixed gain
    AGC_SETPOINT_DBFS  IF AGC setpoint (default -30, same as SDRuno)
    LNA_STATE, RF_GAIN starting front-end gain (LNA backs off on overload)
    LO_OFFSET_HZ       ZIF LO offset (default 300 kHz)
    FILTER_BW_HZ       channel filter width (default 120 kHz, SDRuno's FM BW)
    SETTLE_MS          settling time discarded after each retune (default 120)
    NOISE_CLIP         clipping factor of the noise-floor estimator
"""
import csv
import os
import threading
from datetime import datetime
from pathlib import Path
import sys, time, signal, queue
import numpy as np

# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------
SAMPLE_RATE     = 2_000_000    # Hz  (2.0 MSps)
IF_BANDWIDTH    = 1.536e6      # Hz
RF_GAIN         = float(os.getenv("RF_GAIN", "39"))    # dB IF gain (59 - gr_db)
LNA_STATE       = int(os.getenv("LNA_STATE", "0"))     # 0 = most LNA gain (best NF)
CENTER_FREQ     = 108_000_000  # Hz (108.0 MHz)
FILTER_BW       = int(float(os.getenv("FILTER_BW_HZ", "120000")))  # SDRuno FM filter
FFT_SIZE        = 8192         # Main FFT points
N_FFT_FRAMES    = 4            # FFT frames averaged per display line
DWELL_SECONDS   = 2.0          # seconds to listen/analyze before each update
SCAN_START      = 86_000_000   # Hz (86.0 MHz)
SCAN_STOP       = 109_000_000  # Hz (109.0 MHz, same coverage as fm_dab_sweep)
SCAN_STEP       = 100_000      # Hz (100 kHz steps)
CALIBRATION_DB  = 0        # in-band flatness calibration (kept at 0 here)
DB_OFFSET_DB    = 0        # auto-computed at runtime from CAL_DBM below

# --- ZIF LO offset -------------------------------------------------------
# The RSP runs Zero-IF at 2 MSps (Low-IF is not a legal Fs/IF/BW combination
# here), so the LO leakage / DC spike sits exactly on the tuned frequency.
# SDRuno solves this with its "Auto LO offset (ZIF mode)" - the hardware LO is
# parked away from the wanted channel and the channel is read at a baseband
# offset. Without this, every measurement is dominated by the DC spike.
LO_OFFSET       = int(float(os.getenv("LO_OFFSET_HZ", "300000")))
DC_GUARD        = int(float(os.getenv("DC_GUARD_HZ", "60000")))   # DC bins to ignore
USABLE_BW       = min(IF_BANDWIDTH, SAMPLE_RATE * 0.9)            # analysable span

# --- Tuner IF AGC (SDRuno: IF AGC enabled, setpoint -30 dBFS) ------------
# All power readings are gain-compensated (dBm = dBFS + gRdB + lnaGRdB + K),
# so the reported level does not move when the AGC changes the gain.
USE_AGC         = os.getenv("SDR_AGC", "1") == "1"
AGC_SETPOINT    = float(os.getenv("AGC_SETPOINT_DBFS", "-30"))
SETTLE_SECONDS  = float(os.getenv("SETTLE_MS", "120")) / 1000.0   # after retune
# Noise floor estimator: iteratively clipped mean of the off-channel bins.
# Bins more than NOISE_CLIP times the running mean are treated as signal.
NOISE_CLIP      = float(os.getenv("NOISE_CLIP", "3.0"))
# Calibration reference: tune to CAL_FREQ and assume the channel power there
# equals CAL_DBM (read both from SDRuno). Always calibrate on a STRONG, stable
# station - calibrating on noise ties the constant to the receiver's own noise
# figure instead of to a real signal level.
CAL_FREQ        = int(float(os.getenv("CAL_FREQ_MHZ", "90.0")) * 1e6)
CAL_DBM         = float(os.getenv("CAL_DBM", "-36.5"))
SPECTRUM_MIN    = -110.0       # dBm floor for the ASCII spectrum
SPECTRUM_MAX    = -40.0        # dBm ceiling for the ASCII spectrum

# ---------------------------------------------------------------------------
# Quick-mode / DB settings (same as fm_dab_sweep.py)
# ---------------------------------------------------------------------------
SDR_ID          = os.getenv("SDR_ID", "site_1")

DB_USER         = os.getenv("DB_USER", "postgres")
DB_PASSWORD     = os.getenv("POSTGRES_PASSWORD", "changeme")
DB_HOST         = os.getenv("DB_HOST", "127.0.0.1")  # Tailscale IP
DB_PORT         = int(os.getenv("DB_PORT", "5432"))
DB_NAME         = os.getenv("DB_NAME", "sdr_db")

# Quick mode = the SAME per-channel measurement as Slow mode, just with a
# short dwell: N_FFT_FRAMES frames per step instead of DWELL_SECONDS.
# Override frames-per-step with QUICK_MODE_FRAMES if needed.
QUICK_MODE_FRAMES = int(os.getenv("QUICK_MODE_FRAMES", "32"))  # ~131 ms dwell per step

# Set SKIP_CAL=1 to bypass the CAL_DBM calibration and log raw dBFS values.
SKIP_CAL = os.getenv("SKIP_CAL", "0") == "1"

# Set DEBUG_SDR=1 to print what the SDRplay device/API itself reports
# (device info, stream callback params, retune events, queue stats).
DEBUG_SDR = os.getenv("DEBUG_SDR", "0") == "1"
# ---------------------------------------------------------------------------

running = True


def _stop(sig, frame):
    global running
    running = False


signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


class SDRplayDevice:
    """Minimal SDRplay wrapper around pysdrplay."""

    def __init__(self, sample_rate=SAMPLE_RATE, center_freq=CENTER_FREQ, gain=RF_GAIN):
        from pysdrplay import SDRPlayRadio, Bw, If, TunerSelect

        self._q = queue.Queue(maxsize=256)
        self._remainder = np.array([], dtype=np.complex64)
        self._running = False
        self._lna_state = LNA_STATE
        self._if_gr_db = None      # reported by the API (gain change events)
        self._lna_gr_db = None
        self._overloaded = False
        self._overload_count = 0

        self.radio = SDRPlayRadio()
        devices = self.radio.devices
        if not devices:
            self.radio.close()
            raise RuntimeError("No SDRplay devices found")

        if DEBUG_SDR:
            print(f"[SDR DEBUG] {len(devices)} device(s) found:")
            for idx, dev in enumerate(devices):
                attrs = {a: getattr(dev, a) for a in dir(dev)
                         if not a.startswith('_') and not callable(getattr(dev, a))}
                print(f"[SDR DEBUG]   dev[{idx}]: {attrs}")

        self.radio.select_device(devices[0], tuner=TunerSelect.A)
        if DEBUG_SDR:
            print(f"[SDR DEBUG] Selected device 0, tuner A")

        # Zero-IF: at Fs = 2 MSps with a 1.536 MHz IF filter this is the only
        # legal combination the RSP hardware accepts (Low-IF needs Fs = 6/8
        # MSps). The DC spike that comes with ZIF is avoided by tuning the LO
        # LO_OFFSET Hz away from the wanted channel (see set_center_freq),
        # which is what SDRuno's "Auto LO offset (ZIF mode)" does.
        self.radio.set_if_mode(If.ZERO)
        if DEBUG_SDR:
            print(f"[SDR DEBUG] IF mode set to: {If.ZERO} (ZIF + {LO_OFFSET/1e3:.0f} kHz LO offset)")

        self._sample_rate = float(sample_rate)
        self.radio.set_sample_rate(int(self._sample_rate))
        if DEBUG_SDR:
            print(f"[SDR DEBUG] Sample rate requested: {int(self._sample_rate)} Hz")

        # IF bandwidth: pick a 1.536 MHz-ish enum if the API exposes one.
        bw_choices = [getattr(Bw, a) for a in dir(Bw)
                      if not a.startswith('_')
                      and ('1_536' in a or '1536' in a or 'BW_1_536' in a)]
        if bw_choices:
            self.radio.set_bandwidth(bw_choices[0])
            if DEBUG_SDR:
                print(f"[SDR DEBUG] IF bandwidth set to: {bw_choices[0]}")
        else:
            try:
                self.radio.set_bandwidth(Bw.BW_1_536)
            except (AttributeError, ValueError):
                self.radio.set_bandwidth(self._nearest_bw(self._sample_rate))
                print("[WARN] Using nearest available IF bandwidth instead of 1.536 MHz")

        # Gain / AGC / decimation
        # RF_GAIN is the desired IF gain in dB. SDRplay's gr_db is gain
        # reduction (IFGR), valid roughly 20-59, where lower numbers = more gain.
        self._gain = float(gain)
        self._set_gr = int(round(max(20.0, min(59.0, 59.0 - self._gain))))
        try:
            self.radio.set_gain(lna_state=self._lna_state, gr_db=self._set_gr)
        except Exception as e:
            print(f"[GAIN] lna_state={self._lna_state} rejected ({e}); falling back to 0")
            self._lna_state = 0
            self.radio.set_gain(lna_state=0, gr_db=self._set_gr)
        print(f"[GAIN] IF gain reduction gr_db={self._set_gr} dB, lna_state={self._lna_state}")

        # SDRuno runs the tuner IF AGC (setpoint -30 dBFS) even with the RX
        # AGC switched off. Enabling it keeps the ADC out of compression on
        # strong FM signals; the absolute dBm reading stays correct because we
        # add the reported gain reduction back in (see total_gr_db).
        if USE_AGC:
            self.radio.set_agc(True, setpoint_dbfs=int(AGC_SETPOINT))
            print(f"[AGC] Tuner IF AGC ON, setpoint {AGC_SETPOINT:.0f} dBFS")
        else:
            self.radio.set_agc(False)
            print("[AGC] Tuner IF AGC OFF (fixed gain)")

        # Hardware DC-offset and IQ-imbalance correction: mandatory in ZIF,
        # otherwise LO leakage and the image of the wanted channel corrupt
        # every reading.
        try:
            self.radio.set_dc_offset(True, True)
        except AttributeError:
            print("[WARN] pysdrplay has no set_dc_offset(); DC/IQ correction left at default")

        try:
            self.radio.set_decimation(False, 1)
        except (AttributeError, TypeError):
            pass

        self._center_freq = int(center_freq)
        self._lo_freq = int(center_freq) + LO_OFFSET
        self._running = True
        self._cb_count = 0
        self._dropped = 0
        self.radio.start(self._on_samples, on_event=self._on_event)
        self.set_center_freq(self._center_freq)
        if DEBUG_SDR:
            print(f"[SDR DEBUG] Stream started, initial freq={self._center_freq} Hz")

        import atexit
        atexit.register(self.close)

    @staticmethod
    def _nearest_bw(sample_rate):
        from pysdrplay import Bw
        sr = float(sample_rate)
        if sr <= 0.3e6:
            return Bw.BW_0_300
        if sr <= 0.6e6:
            return Bw.BW_0_600
        if sr <= 1.6e6:
            return Bw.BW_1_536
        if sr <= 5.5e6:
            return Bw.BW_5_000
        if sr <= 6.5e6:
            return Bw.BW_6_000
        if sr <= 7.5e6:
            return Bw.BW_7_000
        return Bw.BW_8_000

    def _on_event(self, event_id, tuner, params, ctx):
        """Track the gain the API is actually using and handle ADC overload."""
        try:
            from pysdrplay import Event, PowerOverloadCbEventId
        except ImportError:
            return
        if event_id == Event.GAIN_CHANGE:
            gain = params.contents.gainParams
            self._if_gr_db = float(gain.gRdB)
            self._lna_gr_db = float(gain.lnaGRdB)
            if DEBUG_SDR:
                print(f"[SDR DEBUG] gain change: gRdB={self._if_gr_db} "
                      f"lnaGRdB={self._lna_gr_db} currGain={gain.currGain:.1f}")
        elif event_id == Event.POWER_OVERLOAD_CHANGE:
            kind = params.contents.powerOverloadParams.powerOverloadChangeType
            self._overloaded = (kind == PowerOverloadCbEventId.OVERLOAD_DETECTED)
            try:
                self.radio.acknowledge_power_overload()
            except Exception:
                pass
            if self._overloaded:
                self._overload_count += 1
                # Back the front end off one LNA step (more attenuation).
                # The reading stays absolute because lnaGRdB is added back in.
                if self._lna_state < 3:
                    self._lna_state += 1
                    try:
                        self.radio.set_gain(lna_state=self._lna_state,
                                            gr_db=self._set_gr)
                        print(f"\n[OVERLOAD] ADC overload -> lna_state={self._lna_state}")
                    except Exception:
                        pass

    @property
    def total_gr_db(self):
        """Total gain reduction (IF + LNA) currently applied, in dB.

        Adding this to a dBFS measurement removes all receiver gain changes,
        so the result tracks the true antenna-referred level exactly the way
        SDRuno's S-meter does.
        """
        if_gr = self._if_gr_db
        if if_gr is None:
            # No gain-change event seen yet: read the value the API keeps in
            # the device parameters (it is updated by the AGC too).
            try:
                if_gr = float(self.radio.get_current_gain()["gr_db"])
            except Exception:
                if_gr = float(self._set_gr)
        lna_gr = self._lna_gr_db if self._lna_gr_db is not None else 0.0
        return if_gr + lna_gr

    def _on_samples(self, xi, xq, params, num_samples, reset, ctx):
        if not self._running:
            return
        if DEBUG_SDR:
            self._cb_count += 1
            # Print the first 3 callbacks, then every 500th, plus any event.
            if self._cb_count <= 3 or self._cb_count % 500 == 0 or reset:
                info = {}
                for a in ('gRdB', 'lnaGRdB', 'numSamples', 'fsChanged',
                          'rfChanged', 'reset', 'gainChanged'):
                    v = getattr(params, a, None)
                    if v is not None:
                        info[a] = v
                print(f"[SDR DEBUG] cb#{self._cb_count} n={num_samples} "
                      f"reset={reset} params={info} "
                      f"qsize={self._q.qsize()} dropped={self._dropped}")
        i = np.ctypeslib.as_array(xi, shape=(num_samples,)).copy()
        q = np.ctypeslib.as_array(xq, shape=(num_samples,)).copy()
        try:
            self._q.put((i + 1j * q).astype(np.complex64), block=False)
        except queue.Full:
            self._dropped += 1

    def read_samples(self, n):
        if len(self._remainder) >= n:
            out = self._remainder[:n]
            self._remainder = self._remainder[n:]
            return out

        needed = n - len(self._remainder)
        chunks = [self._remainder]
        while needed > 0:
            try:
                chunk = self._q.get(timeout=2.0)
            except queue.Empty:
                if not self._running:
                    raise RuntimeError("SDRplay stream stopped")
                continue
            chunks.append(chunk)
            needed -= len(chunk)

        combined = np.concatenate(chunks)
        self._remainder = combined[n:]
        return combined[:n]

    def set_center_freq(self, hz, settle=SETTLE_SECONDS):
        """Tune so that `hz` lands at baseband -LO_OFFSET (away from DC)."""
        self._center_freq = int(hz)
        self._lo_freq = int(hz) + LO_OFFSET
        self.radio.set_frequency(self._lo_freq)
        # Flush buffered IQ captured at the OLD frequency — the queue is
        # FIFO and can hold ~130 ms of stale samples, which would corrupt
        # the next measurement.
        self._remainder = np.array([], dtype=np.complex64)
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        # Let the synthesiser relock and the IF AGC settle before measuring.
        if settle > 0:
            n_settle = int(self._sample_rate * settle)
            if n_settle > 0:
                self.read_samples(n_settle)
                self._remainder = np.array([], dtype=np.complex64)
        if DEBUG_SDR:
            print(f"[SDR DEBUG] Retuned: channel={self._center_freq} Hz, "
                  f"LO={self._lo_freq} Hz (queue flushed, {settle*1e3:.0f} ms settle)")

    @property
    def center_freq(self):
        return self._center_freq

    @property
    def lo_freq(self):
        return self._lo_freq

    @property
    def sample_rate(self):
        return self._sample_rate

    def close(self):
        if self._running:
            try:
                self.radio.stop()
            except Exception:
                pass
            self._running = False
        try:
            self.radio.close()
        except Exception:
            pass


def _channel_power_db(bin_powers):
    """Integrated channel power from linear per-bin powers, in dB.

    Summing the per-bin powers is the correct way to get the power inside a
    filter bandwidth — an FM carrier's energy is spread over ~150 kHz, not
    concentrated in one bin. This is the quantity SDRuno's S-meter shows.
    """
    if bin_powers is None or len(bin_powers) == 0:
        return None
    return float(10.0 * np.log10(np.sum(bin_powers) + 1e-30))


def _spectrum(samples, sample_rate, fft_size=FFT_SIZE):
    """Return (freqs, bin_power) where bin_power is linear power per FFT bin,
    referenced to ADC full scale.

    Normalisation: for x scaled to +/-1 full scale and window w,
        P_k = |FFT(x*w)_k|^2 / (N * sum(w^2))
    so that sum_k P_k equals the mean power of x (Parseval). That makes an
    integration over a set of bins a true channel power, and a single bin a
    true per-bin (RBW) power — both in dBFS.
    """
    n_frames = int(len(samples) // fft_size)
    frames = samples[:n_frames * fft_size].reshape(n_frames, fft_size)
    window = np.hanning(fft_size)
    norm = fft_size * float(np.sum(window ** 2))

    spec = np.fft.fftshift(np.fft.fft(frames * window, axis=1), axes=1)
    # 32768.0 converts the 16-bit integer I/Q to full scale (+/-1.0).
    power = np.mean(np.abs(spec / 32768.0) ** 2, axis=0) / norm

    freqs = np.fft.fftshift(np.fft.fftfreq(fft_size, 1.0 / sample_rate))
    return freqs, power


def _channel_masks(freqs, filter_bw=FILTER_BW, lo_offset=LO_OFFSET):
    """Masks for (wanted channel, noise-estimation region).

    The wanted channel sits at baseband -lo_offset because the LO is parked
    lo_offset Hz above it. The noise region is everything inside the IF
    passband except: the channel itself (plus a guard band), the DC/LO-leakage
    region, and the mirror of the channel (residual IQ image).
    """
    chan = np.abs(freqs + lo_offset) <= filter_bw / 2.0

    guard = filter_bw / 2.0 + 100e3
    in_if = np.abs(freqs) <= USABLE_BW / 2.0
    noise = (
        in_if
        & (np.abs(freqs) > DC_GUARD)
        & (np.abs(freqs + lo_offset) > guard)
        & (np.abs(freqs - lo_offset) > guard)
    )
    if not np.any(noise):
        noise = in_if & ~chan
    return chan, noise


def _noise_bin_power(bin_powers, clip=NOISE_CLIP, n_iter=8):
    """Mean noise power per FFT bin, with signal-carrying bins clipped away.

    Starting from the median (robust even when half the band is occupied),
    bins above `clip` x the running mean are dropped and the mean recomputed.
    Unlike a fixed percentile this converges on the true mean noise power, so
    the reported floor does not shift when the dwell/averaging changes.
    """
    vals = np.asarray(bin_powers, dtype=np.float64)
    if vals.size == 0:
        return 0.0
    ref = float(np.median(vals))
    for _ in range(n_iter):
        keep = vals <= clip * ref
        if not np.any(keep):
            break
        kept = vals[keep]
        new_ref = float(np.mean(kept))
        if kept.size == vals.size and abs(new_ref - ref) < 1e-12:
            ref = new_ref
            break
        vals = kept
        ref = new_ref
    return ref


def _measure(freqs, power, gr_db, offset_db):
    """SDRuno-style channel measurement.

    Returns (signal_dbm, noise_dbm, snr_db):
      signal = integrated power in the demod filter bandwidth
      noise  = clipped-mean per-bin noise level of the surrounding spectrum,
               scaled up to the same filter bandwidth
    Both are gain-compensated (dBFS + total gain reduction + calibration),
    so the numbers stay put when the IF AGC moves the gain.
    """
    chan, noise = _channel_masks(freqs)
    n_chan = int(np.count_nonzero(chan))

    sig_dbfs = _channel_power_db(power[chan])
    noise_bin_dbfs = 10.0 * np.log10(_noise_bin_power(power[noise]) + 1e-30)
    noise_dbfs = noise_bin_dbfs + 10.0 * np.log10(max(n_chan, 1))

    cal = gr_db + offset_db + CALIBRATION_DB
    signal_dbm = sig_dbfs + cal
    floor_dbm = noise_dbfs + cal
    return signal_dbm, floor_dbm, signal_dbm - floor_dbm


# ---------------------------------------------------------------------------
# Database Setup (same as fm_dab_sweep.py)
# ---------------------------------------------------------------------------
def setup_db():
    """Setup database connection and ensure table exists."""
    from sqlalchemy import create_engine, Column, Integer, Float, DateTime, String, text
    from sqlalchemy.orm import declarative_base, sessionmaker

    db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    # Auto-create database if missing
    admin_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/postgres"
    try:
        admin_engine = create_engine(admin_url)
        with admin_engine.connect() as conn:
            conn.execute(text("commit"))
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": DB_NAME}
            ).fetchone()
            if not exists:
                conn.execute(text(f"CREATE DATABASE {DB_NAME}"))
                print(f"[DB] Created database '{DB_NAME}'")
        admin_engine.dispose()
    except Exception as e:
        print(f"[DB] Warning: Could not check/create DB: {e}")

    Base = declarative_base()

    class SweepData(Base):
        __tablename__ = "rf_sweep_data"
        id = Column(Integer, primary_key=True, index=True)
        timestamp = Column(DateTime, default=datetime.utcnow, index=True)
        frequency_mhz = Column(Integer, index=True)  # Stored as kHz integer
        power_dbfs = Column(Float)
        floor_dbfs = Column(Float)
        snr_db = Column(Float)
        sdr_id = Column(String, index=True)

    engine = create_engine(db_url, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)

    # Add sdr_id column if it doesn't exist yet (safe migration for existing data)
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "ALTER TABLE rf_sweep_data ADD COLUMN IF NOT EXISTS sdr_id VARCHAR"
            ))
            conn.commit()
        except Exception:
            conn.rollback()
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    print(f"[DB] Connected to {DB_HOST}:{DB_PORT}/{DB_NAME}")
    return Session, SweepData



def ask_mode() -> str:
    """
    Auto-selects Q (Quick) mode after a 10-second countdown.
    Press S + Enter during the wait to override to Slow mode.
    Returns 'Q' or 'S'.
    """
    TIMEOUT = 10
    result = []

    def _read():
        try:
            answer = input("").strip().upper()
            if answer in ("Q", "S"):
                result.append(answer)
        except (EOFError, KeyboardInterrupt):
            pass

    print("\nSelect sweep mode — [Q] Quick (short dwell per channel) / [S] Slow (2s dwell)")
    print(f"Auto-selecting Q (Quick) in {TIMEOUT} seconds... (type S + Enter to override)")

    t = threading.Thread(target=_read, daemon=True)
    t.start()

    for remaining in range(TIMEOUT, 0, -1):
        if result:
            break
        print(f"\r  Auto-selecting Q in {remaining:2d}s... ", end="", flush=True)
        time.sleep(1)

    print()
    if result:
        return result[0]
    print("[Mode] Timeout reached — defaulting to Q (Quick).")
    return "Q"


def _bar(level, chars=" .:-=+*#%@"):
    i = int(np.clip(level, 0, len(chars) - 1))
    return chars[i]


def main():
    print("=" * 70)
    print("SDRplay real-data viewer + PostgreSQL logging")
    print(f"  Sample rate: {SAMPLE_RATE/1e6:.3f} MHz")
    print(f"  IF mode: ZIF + {LO_OFFSET/1e3:.0f} kHz LO offset, "
          f"IF bandwidth: {IF_BANDWIDTH/1e6:.3f} MHz")
    print(f"  IF AGC: {'ON @ %.0f dBFS' % AGC_SETPOINT if USE_AGC else 'OFF'}, "
          f"lna_state={LNA_STATE}, decimation: 1 (levels are gain-compensated)")
    print(f"  Channel filter: {FILTER_BW/1e3:.0f} kHz  (noise floor from the "
          f"clipped mean of the surrounding spectrum)")
    print(f"  Main FFT: {FFT_SIZE} points  (RBW ~ {SAMPLE_RATE/FFT_SIZE:.1f} Hz)")
    print(f"  DB: {DB_HOST}:{DB_PORT}/{DB_NAME}  table=rf_sweep_data  sdr_id={SDR_ID}")
    print("=" * 70)

    mode = ask_mode()
    if mode == "Q":
        print(f"\n[Mode] QUICK — same per-channel sweep, {QUICK_MODE_FRAMES} FFT frames "
              f"(~{QUICK_MODE_FRAMES * FFT_SIZE / SAMPLE_RATE * 1000:.0f} ms) per step")
    else:
        print(f"\n[Mode] SLOW  — original per-channel sweep, {DWELL_SECONDS}s per step")

    # Setup database (same as fm_dab_sweep.py)
    Session, SweepData = setup_db()
    db = Session()

    try:
        sdr = SDRplayDevice(
            sample_rate=SAMPLE_RATE,
            center_freq=CENTER_FREQ,
            gain=RF_GAIN,
        )
    except ImportError:
        print("[ERROR] pysdrplay is not installed.")
        print("Install it with:  pip install pysdrplay")
        return 1
    except Exception as e:
        print(f"[ERROR] {e}")
        return 1

    # CSV setup: log to the same folder as this script
    here = Path(__file__).resolve().parent
    start_dt = datetime.now()
    start_str = start_dt.strftime("%Y%m%d_%H%M%S")
    tmp_path = here / f"sdrplay_real_{start_str}.csv.tmp"
    fieldnames = ["timestamp", "center_freq_hz", "peak_dbm", "floor_dbm", "snr_db"]
    csvfile = open(tmp_path, "w", newline="")
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()
    print(f"[CSV] Logging to: {tmp_path}")

    # Calibrate dBm offset against the SDRuno reference at CENTER_FREQ
    def _calibrate_offset(sdr, target_freq, target_dbm, dwell):
        sdr.set_center_freq(target_freq)
        n_frames = max(1, int(SAMPLE_RATE * dwell / FFT_SIZE))
        samples = sdr.read_samples(FFT_SIZE * n_frames)
        freqs, power = _spectrum(samples, sdr.sample_rate)
        # Calibrate with the SAME quantity we report: gain-compensated
        # integrated channel power — that's what SDRuno's dBm reading is.
        raw_dbm, _, _ = _measure(freqs, power, sdr.total_gr_db, 0.0)
        return target_dbm - raw_dbm

    global DB_OFFSET_DB
    if SKIP_CAL:
        DB_OFFSET_DB = 0.0
        print("[CAL] SKIP_CAL=1 — no calibration, logging raw dBFS")
    else:
        print(f"[CAL] Calibrating at {CAL_FREQ/1e6:.3f} MHz, expecting {CAL_DBM} dBm...")
        DB_OFFSET_DB = _calibrate_offset(sdr, CAL_FREQ, CAL_DBM, DWELL_SECONDS)
        print(f"[CAL] dBm offset set to {DB_OFFSET_DB:.2f} dB")

    span = sdr.sample_rate
    n_bins = 60
    step = FFT_SIZE // n_bins

    scan_freqs = np.arange(SCAN_START, SCAN_STOP + SCAN_STEP / 2.0, SCAN_STEP, dtype=np.int64)
    scan_idx = 0
    print(f"[SCAN] {len(scan_freqs)} step(s) from {SCAN_START/1e6:.3f} to {SCAN_STOP/1e6:.3f} MHz")
    print("\nPress Ctrl+C to stop.\n")

    loop_count = 0
    total_records = 0

    try:
        while running:
            loop_count += 1
            print(f"\n{'='*60}")
            print(f"SWEEP CYCLE #{loop_count}  [{('QUICK' if mode == 'Q' else 'SLOW')}]")
            print(f"{'='*60}")

            # Same per-channel sweep in both modes — only the dwell differs.
            cycle_saved = 0
            while running and scan_idx < len(scan_freqs):
                current_freq = int(scan_freqs[scan_idx])
                sdr.set_center_freq(current_freq)
                scan_idx += 1

                # Listen for the dwell time, then analyze one batch.
                # Quick mode: N_FFT_FRAMES frames (~16 ms). Slow: full 2 s.
                if mode == "Q":
                    n_frames = QUICK_MODE_FRAMES
                else:
                    n_frames = max(1, int(SAMPLE_RATE * DWELL_SECONDS / FFT_SIZE))
                samples = sdr.read_samples(FFT_SIZE * n_frames)
                freqs, power = _spectrum(samples, sdr.sample_rate)

                # Channel power over the demod filter bandwidth, plus the
                # surrounding-spectrum noise floor scaled to that same
                # bandwidth — the two numbers SDRuno logs as Signal / SNR.
                active_dbm, noise_dbm, snr_db = _measure(
                    freqs, power, sdr.total_gr_db, DB_OFFSET_DB
                )

                writer.writerow({
                    "timestamp": datetime.now().isoformat(),
                    "center_freq_hz": sdr.center_freq,
                    "peak_dbm": active_dbm,
                    "floor_dbm": noise_dbm,
                    "snr_db": snr_db,
                })

                # Save to DB (same as fm_dab_sweep.py: frequency stored as kHz int)
                try:
                    reading = SweepData(
                        timestamp=datetime.now(),
                        frequency_mhz=int(round(sdr.center_freq / 1000.0)),
                        power_dbfs=round(float(active_dbm), 2),
                        floor_dbfs=round(float(noise_dbm), 2),
                        snr_db=round(float(snr_db), 2),
                        sdr_id=SDR_ID,
                    )
                    db.add(reading)
                    db.commit()
                    cycle_saved += 1
                except Exception as e:
                    print(f"\n[DB ERROR] save failed at {sdr.center_freq/1e6:.3f} MHz: {e}")
                    db.rollback()

                # ASCII spectrum across the full 2 MHz span (per-bin dBm)
                dbm = (10.0 * np.log10(power + 1e-30)
                       + sdr.total_gr_db + DB_OFFSET_DB + CALIBRATION_DB)
                bins = [np.max(dbm[i * step:(i + 1) * step]) for i in range(n_bins)]
                levels = [
                    int(9 * (b - SPECTRUM_MIN) / (SPECTRUM_MAX - SPECTRUM_MIN))
                    for b in bins
                ]
                bar = "".join(_bar(v) for v in levels)

                print(
                    f"\r  {sdr.center_freq/1e6:7.3f} MHz | "
                    f"Pk:{active_dbm:6.1f} dBm | "
                    f"F:{noise_dbm:6.1f} dBm | "
                    f"SNR:{snr_db:5.1f} dB | {bar}",
                    end="",
                    flush=True,
                )

                # 2-second dwell is already provided by the read_samples call

            total_records += cycle_saved
            print(f"\n[Cycle complete] {cycle_saved} records saved "
                  f"(total: {total_records})")
            scan_idx = 0  # restart the sweep for the next cycle
            if not running:
                break
            print("Sleeping 2 seconds before next cycle...")
            time.sleep(2)
    finally:
        print("\n\n[Stopped] closing SDRplay...")
        sdr.close()
        db.close()
        csvfile.close()
        end_dt = datetime.now()
        end_str = end_dt.strftime("%Y%m%d_%H%M%S")
        final_path = here / f"sdrplay_real_{start_str}_{end_str}.csv"
        try:
            tmp_path.rename(final_path)
            print(f"[CSV] Saved: {final_path.name}")
        except Exception as e:
            print(f"[WARN] Raw CSV still at {tmp_path}; rename failed: {e}")
        print(f"[Done] Cycles: {loop_count}, records saved: {total_records}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
