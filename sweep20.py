"""
sweep20.py – 20 MHz Panoramic Sweep Engine
============================================
Standalone 20 MHz spectrum sweeper that feeds into spectrum_core.py / monitor.py.

Can run:
  1. Imported:  from sweep20 import SweepWorker  →  drop-in replacement for SdrWorker sweep mode
  2. Standalone: python sweep20.py  →  CLI sweep with live matplotlib plot

Uses calculate.py for all DSP.  No hard-coded FFT math here.
"""
from __future__ import annotations

import os
import sys
import csv
import time
import math
import datetime
import threading
import queue
from typing import Callable, List, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------
try:
    from PyQt6 import QtCore
    HAS_QT = True
except Exception:
    HAS_QT = False

try:
    from rtlsdr import RtlSdr
    HAS_RTL = True
except Exception:
    HAS_RTL = False

import calculate as calc


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SWEEP_START_HZ = 87.5e6
SWEEP_STOP_HZ  = 108.0e6
SWEEP_STEP_HZ  = 2.0e6       # retune step
SWEEP_FS       = 2.048e6     # sample rate during sweep
SWEEP_FFT      = 2048
SWEEP_CROP     = 24          # bins to drop at each edge (keeps exactly 2.0 MHz per chunk)
DC_NOTCH_HW    = 4           # half-width of DC notch in bins

PEAK_HEIGHT_DB    = -65.0
PEAK_PROMINENCE   = 10.0      # real RF needs higher prominence
PEAK_MIN_DIST_KHZ = 300.0    # wider spacing for real FM channels

LOG_INTERVAL_SEC  = 2.0
LOG_DIR           = r"C:\projects\CGrep-v3\Logs"

# Known real FM stations — always force-include after every sweep
KNOWN_STATIONS_MHZ = (
    88.7, 90.0, 90.7, 92.5, 93.0, 93.3, 97.7,
    100.0, 100.5, 101.0, 101.1, 102.8, 103.0,
    103.3, 104.0, 105.0, 106.0, 106.3, 107.0, 107.7,
)

# The stations our React dashboard tracks — used for targeted window extraction
# to bypass flat-top issues when loud FM audio widens the signal.
# MUST match SigintDashboard.jsx STATIONS array exactly.
TARGET_FREQS = [
    88.7, 90.0, 90.7, 92.5, 93.0, 93.3, 97.7,
    100.0, 100.5, 101.0, 101.1, 102.8, 103.0,
    103.3, 104.0, 105.0, 106.0, 106.3, 107.0,
    107.7,
]


# ---------------------------------------------------------------------------
# CSV Peak Logger (thread-safe)
# ---------------------------------------------------------------------------
class SpectrumPeakLogger:
    """Daily-rolling CSV logger for panoramic sweep peaks."""

    def __init__(self, log_dir: str = LOG_DIR, prefix: str = "sweep"):
        self._lock = threading.Lock()
        self._date  = None
        self._fh    = None
        self._writer = None
        self._log_dir = log_dir
        self._prefix = prefix

    def _rotate(self):
        today = datetime.date.today()
        if today == self._date and self._fh is not None:
            return
        if self._fh:
            self._fh.close()
        os.makedirs(self._log_dir, exist_ok=True)
        path = os.path.join(
            self._log_dir,
            f"{self._prefix}_{today.strftime('%Y%m%d')}.csv")
        is_new = not os.path.exists(path)
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        if is_new:
            self._writer.writerow(["Timestamp", "Frequency_MHz", "Center_Frequency_MHz", "Power_dBFS", "Noise_Floor_dBFS", "SNR_dB", "Note"])
        self._date = today
        print(f"[sweep_logger] Writing to: {path}")

    def write_peaks(self, freq_mhz_arr, power_dbfs_arr, noise_floor_db: float = None, center_freq_mhz: float = None):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            try:
                self._rotate()
                # Calculate noise floor from all powers if not provided
                if noise_floor_db is None and len(power_dbfs_arr) > 0:
                    noise_floor_db = np.percentile(power_dbfs_arr, 10)  # 10th percentile as noise floor
                if center_freq_mhz is None:
                    center_freq_mhz = 98.0  # Default FM band center
                for f, p in zip(freq_mhz_arr, power_dbfs_arr):
                    snr = p - noise_floor_db if noise_floor_db is not None else 0.0
                    self._writer.writerow([
                        ts,
                        f"{round(float(f), 1):.1f}",
                        f"{center_freq_mhz:.1f}",
                        f"{p:.2f}",
                        f"{noise_floor_db:.2f}" if noise_floor_db is not None else "",
                        f"{snr:.2f}",
                        ""
                    ])
                self._fh.flush()
            except Exception as exc:
                print(f"[{self._prefix}_logger] Write error: {exc}")

    def write_dashboard(self, dashboard_peaks: list, noise_floor_db: float = None, center_freq_mhz: float = None):
        """Write dashboard-specific peaks (list of {freq, dbfs} dicts)."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            try:
                self._rotate()
                # Calculate noise floor from all peaks if not provided
                if noise_floor_db is None and dashboard_peaks:
                    powers = [float(item["dbfs"]) for item in dashboard_peaks]
                    noise_floor_db = np.percentile(powers, 10)
                if center_freq_mhz is None:
                    center_freq_mhz = 98.0
                for item in dashboard_peaks:
                    freq = round(float(item["freq"]), 1)
                    pwr  = float(item["dbfs"])
                    snr = pwr - noise_floor_db if noise_floor_db is not None else 0.0
                    self._writer.writerow([
                        ts,
                        f"{freq:.1f}",
                        f"{center_freq_mhz:.1f}",
                        f"{pwr:.2f}",
                        f"{noise_floor_db:.2f}" if noise_floor_db is not None else "",
                        f"{snr:.2f}",
                        ""
                    ])
                self._fh.flush()
            except Exception as exc:
                print(f"[sweep_logger] Write error: {exc}")

    def write_fake_alarm(self, freq_mhz: float, power_dbfs: float, note: str = "FAKEALARM",
                         noise_floor_db: float = None, center_freq_mhz: float = None):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            try:
                self._rotate()
                snr = power_dbfs - noise_floor_db if noise_floor_db is not None else 0.0
                if center_freq_mhz is None:
                    center_freq_mhz = 98.0
                self._writer.writerow([
                    ts,
                    f"{round(float(freq_mhz), 1):.1f}",
                    f"{center_freq_mhz:.1f}",
                    f"{power_dbfs:.2f}",
                    f"{noise_floor_db:.2f}" if noise_floor_db is not None else "",
                    f"{snr:.2f}",
                    note
                ])
                self._fh.flush()
            except Exception as exc:
                print(f"[{self._prefix}_logger] Write error: {exc}")

    def close(self):
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None


# ---------------------------------------------------------------------------
# Core sweep engine (no Qt, testable)
# ---------------------------------------------------------------------------
class SweepEngine:
    """
    Performs a 20 MHz panoramic sweep using RTL-SDR or simulated data.

    Usage:
        engine = SweepEngine()
        for spectrum, freqs_mhz in engine.run_realtime():
            # spectrum is float32 dBFS array
            # freqs_mhz is the corresponding MHz axis
            update_gui(spectrum, freqs_mhz)
    """

    def __init__(
        self,
        start_hz: float = SWEEP_START_HZ,
        stop_hz: float = SWEEP_STOP_HZ,
        step_hz: float = SWEEP_STEP_HZ,
        fs: float = SWEEP_FS,
        n_fft: int = SWEEP_FFT,
        crop: int = SWEEP_CROP,
        gain: float | str = calc.DEFAULT_GAIN_DB,
        use_simulated: bool = False,
        sim_stations: List[Tuple[float, float]] | None = None,
    ):
        self.start_hz = start_hz
        self.stop_hz  = stop_hz
        self.step_hz  = step_hz
        self.fs       = fs
        self.n_fft    = n_fft
        self.crop     = crop
        self.gain     = gain
        self.use_sim  = use_simulated
        self._running = False
        self._cancelled = False
        self._sdr     = None

        # Simulated mode setup
        self._sim_t = 0.0
        self._sim_stations = sim_stations or [
            (90.5, 45.0), (92.3, 38.0), (95.0, 50.0),
            (99.5, 55.0), (101.0, 42.0), (103.7, 48.0), (107.2, 36.0),
        ]

        # Pre-compute sweep frequencies
        self._step_freqs = np.arange(start_hz, stop_hz + step_hz * 0.5, step_hz)
        self.bins_per_chunk = n_fft - 2 * crop
        self.total_bins = self.bins_per_chunk * len(self._step_freqs)
        self.freq_axis_mhz = np.linspace(start_hz / 1e6, stop_hz / 1e6, self.total_bins)

        # Pre-build FFT window
        self._win = calc.blackmanharris_window(n_fft)
        self._win_norm = float(np.sum(self._win))

        # Peak detection spacing
        bin_width_hz = calc.freq_resolution_hz(fs, n_fft)
        step_bins = int(round(step_hz / bin_width_hz))
        self._peak_distance = max(1, int(PEAK_MIN_DIST_KHZ * 1e3 / (step_hz / self.bins_per_chunk)))

    def cancel(self):
        """Signal the current sweep to abort at the next chunk boundary."""
        self._cancelled = True

    def reset_cancel(self):
        """Clear cancellation flag before starting a new sweep."""
        self._cancelled = False

    # ------------------------------------------------------------------
    # Hardware helpers
    # ------------------------------------------------------------------
    def _open_sdr(self):
        if not HAS_RTL:
            raise RuntimeError("RTL-SDR library not available")
        self._sdr = RtlSdr()
        self._sdr.sample_rate = self.fs
        self._sdr.center_freq = int(self._step_freqs[0])
        # Disable AGC and lock to fixed manual gain for stable noise floor
        self._sdr.set_manual_gain_enabled(True)
        self._sdr.gain = 36.4
        print(f"[sweep] RTL-SDR opened  fs={self.fs/1e6:.3f} MHz  gain={self._sdr.gain}")

    def _close_sdr(self):
        if self._sdr is not None:
            try:
                self._sdr.close()
            except Exception:
                pass
            self._sdr = None

    def _read_chunk(self, fc_hz: float) -> np.ndarray:
        """Read IQ samples for one step frequency."""
        if self.use_sim:
            return self._read_simulated(fc_hz)

        if self._sdr is None:
            self._open_sdr()
        self._sdr.center_freq = int(fc_hz)
        # Flush stale / transition data: read a large block then keep only
        # the last FFT-sized chunk so the spectrum is computed from clean,
        # fully-settled samples (not buffered USB frames from the old freq).
        samples = np.asarray(self._sdr.read_samples(self.n_fft * 8), dtype=np.complex64)
        return samples[-self.n_fft:]

    def _read_simulated(self, fc_hz: float) -> np.ndarray:
        """Generate clean synthetic IQ for one step with a few station carriers."""
        n = self.n_fft * 4
        t = np.arange(n) / self.fs
        iq = np.zeros(n, dtype=np.complex64)

        # Very low noise floor
        noise = (np.random.randn(n) + 1j * np.random.randn(n)).astype(np.complex64)
        iq += noise * 0.0001

        # Add stations that fall within this step's Nyquist bandwidth
        for f_mhz, power_db in self._sim_stations:
            f_hz = f_mhz * 1e6
            # Only emit if station is within the capture bandwidth (avoid aliasing)
            if abs(f_hz - fc_hz) > self.fs * 0.5:
                continue
            # Relative frequency within this chunk
            rel_hz = f_hz - fc_hz
            amp = 0.008  # calibrated to produce ~ -10 dBFS peaks after FFT
            phase = np.random.uniform(0, 2 * math.pi)
            iq += amp * np.exp(2j * np.pi * rel_hz * t + 1j * phase).astype(np.complex64)

        self._sim_t += 0.04
        return iq[-self.n_fft:]

    # ------------------------------------------------------------------
    # FFT + processing for one chunk
    # ------------------------------------------------------------------
    def _process_chunk(self, iq: np.ndarray) -> np.ndarray:
        if iq.size < self.n_fft:
            iq = np.pad(iq, (0, self.n_fft - iq.size))

        spec = np.fft.fftshift(np.fft.fft(iq * self._win))
        # 1. Calculate linear amplitude, normalized by the window's coherent gain
        mag = np.abs(spec) / self._win_norm + 1e-12

        # 2. Calculate Decibels relative to Full Scale (dBFS)
        dbfs = 20.0 * np.log10(mag)

        # 3. Match SDR# visual scale (noise floor ~ -54, peaks ~ -30)
        CALIBRATION_OFFSET = 24.0
        db = (dbfs + CALIBRATION_OFFSET).astype(np.float32)

        # DC notch
        ctr = self.n_fft // 2
        lo_n = max(0, ctr - DC_NOTCH_HW)
        hi_n = min(self.n_fft - 1, ctr + DC_NOTCH_HW)
        db[lo_n:hi_n] = np.interp(
            np.arange(lo_n, hi_n),
            [lo_n - 1, hi_n],
            [db[lo_n - 1], db[hi_n]])

        # Crop edges
        return db[self.crop: self.n_fft - self.crop]

    # ------------------------------------------------------------------
    # One full sweep
    # ------------------------------------------------------------------
    def sweep_once(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Perform one complete sweep.  Returns (spectrum_db, freq_axis_mhz).
        spectrum_db is float32 length total_bins.
        """
        chunks = []
        freq_chunks = []
        for fc_step in self._step_freqs:
            if self._cancelled:
                self._cancelled = False
                return (
                    np.full(self.total_bins, -100.0, dtype=np.float32),
                    self.freq_axis_mhz,
                )
            try:
                iq = self._read_chunk(fc_step)
                chunks.append(self._process_chunk(iq))
                freq_chunks.append(calc.freq_axis_hz(fc_step, self.fs, self.n_fft)[self.crop:-self.crop] / 1e6)
            except Exception as e:
                print(f"[sweep] Error @ {fc_step/1e6:.1f} MHz: {e}")
                break

        if not chunks:
            # Return empty / noise floor
            return (
                np.full(self.total_bins, -100.0, dtype=np.float32),
                self.freq_axis_mhz,
            )

        stitched = np.concatenate(chunks).astype(np.float32)
        freqs_mhz = np.concatenate(freq_chunks).astype(np.float32)
        self.freq_axis_mhz = freqs_mhz
        return stitched, freqs_mhz

    # ------------------------------------------------------------------
    # Spectrum smoothing
    # ------------------------------------------------------------------
    @staticmethod
    def _smooth(spec: np.ndarray, window: int = 15) -> np.ndarray:
        """Simple box-car smoothing to reduce noise peaks."""
        if window < 3:
            return spec
        half = window // 2
        out = np.empty_like(spec)
        for i in range(len(spec)):
            lo = max(0, i - half)
            hi = min(len(spec), i + half + 1)
            out[i] = np.mean(spec[lo:hi])
        return out

    # ------------------------------------------------------------------
    # Peak detection on stitched spectrum
    # ------------------------------------------------------------------
    def find_peaks(self, spectrum_db: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Returns (peak_freqs_mhz, peak_powers_db)."""
        # Estimate noise floor from median; require strong margin for real RF
        noise_floor = float(np.percentile(spectrum_db, 50))
        margin = 8.0 if self.use_sim else 15.0
        floor = -25.0 if self.use_sim else -40.0
        height = max(noise_floor + margin, floor)
        try:
            from scipy.signal import find_peaks
            idxs, props = find_peaks(
                spectrum_db,
                height=height,
                distance=self._peak_distance,
                prominence=PEAK_PROMINENCE,
            )
            if idxs.size == 0:
                freqs = np.array([], dtype=np.float32)
                powers = np.array([], dtype=np.float32)
            else:
                freqs = self.freq_axis_mhz[idxs]
                powers = spectrum_db[idxs]
        except Exception:
            # Manual fallback
            freqs, powers = self._simple_peak_find(spectrum_db, height)

        # Merge known stations: force-include any missing known freq
        if self.freq_axis_mhz is not None and self.freq_axis_mhz.size > 0:
            known_f, known_p = self._merge_known_stations(freqs, powers, spectrum_db)
            # Re-sort by frequency
            if known_f.size > 0:
                order = np.argsort(known_f)
                return known_f[order], known_p[order]
        return freqs, powers

    def _merge_known_stations(self, freqs: np.ndarray, powers: np.ndarray,
                              spectrum_db: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Ensure every target station is present with the true maximum power
        from a window around the target, bypassing flat-top issues from
        find_peaks when loud FM audio widens the signal.
        Each detected peak can only be claimed by one target to prevent
        close stations (e.g. 101.0 and 101.1) from overwriting each other."""
        fa = self.freq_axis_mhz
        if fa is None or fa.size == 0:
            return freqs, powers

        # Start with detected peaks; track which are claimed by a target
        merged_f = freqs.tolist() if freqs.size > 0 else []
        merged_p = powers.tolist() if powers.size > 0 else []
        claimed = [False] * len(merged_f)

        for target in TARGET_FREQS:
            # Find the array index for this exact frequency
            idx = int(np.abs(fa - target).argmin())

            # Define a window of +/- 100 bins (~ +/- 100 kHz)
            lo = max(0, idx - 100)
            hi = min(len(spectrum_db), idx + 100)
            window_max_dbfs = float(np.max(spectrum_db[lo:hi]))

            # Find closest UNCLAIMED peak within 150 kHz
            best_idx = -1
            best_dist = 999.0
            for i, f in enumerate(merged_f):
                if claimed[i]:
                    continue
                d = abs(f - target)
                if d < best_dist and d < 0.15:
                    best_dist = d
                    best_idx = i

            if best_idx >= 0:
                # Claim this peak for this target — snap freq to exact target
                merged_f[best_idx] = float(target)
                if window_max_dbfs > merged_p[best_idx] + 0.5:
                    merged_p[best_idx] = window_max_dbfs
                claimed[best_idx] = True
            else:
                # No unclaimed peak nearby — force-add with window max
                merged_f.append(float(target))
                merged_p.append(window_max_dbfs)
                claimed.append(True)

        return np.array(merged_f, dtype=np.float32), np.array(merged_p, dtype=np.float32)

    def _simple_peak_find(self, spectrum_db: np.ndarray, height: float) -> Tuple[np.ndarray, np.ndarray]:
        """Simple local maxima finder (no scipy needed)."""
        loc_max = (spectrum_db[1:-1] > spectrum_db[:-2]) & (spectrum_db[1:-1] > spectrum_db[2:])
        idxs = np.where(loc_max & (spectrum_db[1:-1] > height))[0] + 1
        if idxs.size == 0:
            return np.array([]), np.array([])
        # Enforce minimum distance
        filtered = []
        last = -self._peak_distance * 2
        for i in idxs:
            if i - last >= self._peak_distance:
                filtered.append(i)
                last = i
        idxs = np.array(filtered)
        return self.freq_axis_mhz[idxs], spectrum_db[idxs]

    # ------------------------------------------------------------------
    # Realtime generator (yields spectrum continuously)
    # ------------------------------------------------------------------
    def run_realtime(
        self,
        callback: Callable[[np.ndarray, np.ndarray], None] | None = None,
        log_peaks: bool = True,
    ):
        """
        Runs sweeps in a loop until stop() is called.
        If callback is given, calls callback(spectrum_db, freq_axis_mhz) each sweep.
        Yields (spectrum_db, freq_axis_mhz) for use in 'for' loops.
        """
        self._running = True
        logger = SpectrumPeakLogger() if log_peaks else None
        last_log = 0.0

        if not self.use_sim:
            try:
                self._open_sdr()
            except Exception as e:
                print(f"[sweep] RTL-SDR failed ({e}), falling back to simulated")
                self.use_sim = True

        try:
            while self._running:
                t0 = time.monotonic()
                spectrum, freqs = self.sweep_once()

                if callback:
                    callback(spectrum, freqs)
                yield spectrum, freqs

                # Peak logging
                if logger and time.monotonic() - last_log >= LOG_INTERVAL_SEC:
                    pf, pp = self.find_peaks(spectrum)
                    if pf.size > 0:
                        logger.write_peaks(pf, pp)
                        print(f"[sweep] {len(pf)} peaks  ({', '.join(f'{f:.1f}' for f in pf)} MHz)")
                    last_log = time.monotonic()

                # Throttle to ~4 sweeps/sec
                elapsed = time.monotonic() - t0
                sleep_for = max(0.0, 0.20 - elapsed)
                if sleep_for > 0:
                    time.sleep(sleep_for)
        finally:
            self._running = False
            self._close_sdr()
            if logger:
                logger.close()

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# Qt Worker for easy GUI integration
# ---------------------------------------------------------------------------
if HAS_QT:
    class SweepWorker(QtCore.QThread):
        """
        Drop-in QThread that emits stitched 20 MHz spectra.
        Connect fft_ready to your GUI slot (same signature as spectrum_core.py SdrWorker).
        """
        fft_ready = QtCore.pyqtSignal(np.ndarray, float, float, float)
        peaks_found = QtCore.pyqtSignal(list, list)   # freq_mhz list, power_db list
        error = QtCore.pyqtSignal(str)

        def __init__(
            self,
            start_hz: float = SWEEP_START_HZ,
            stop_hz: float = SWEEP_STOP_HZ,
            gain: float | str = calc.DEFAULT_GAIN_DB,
            use_simulated: bool = False,
            parent=None,
        ):
            super().__init__(parent)
            self._engine = SweepEngine(
                start_hz=start_hz,
                stop_hz=stop_hz,
                gain=gain,
                use_simulated=use_simulated,
            )
            self._gain = gain

        def set_gain(self, db: float):
            self._engine.gain = db

        def run(self):
            try:
                for spectrum, freqs in self._engine.run_realtime():
                    if not self.isRunning():
                        break
                    center = (self._engine.start_hz + self._engine.stop_hz) / 2.0
                    span = self._engine.stop_hz - self._engine.start_hz
                    self.fft_ready.emit(spectrum, center, span, center)

                    pf, pp = self._engine.find_peaks(spectrum)
                    if pf.size > 0:
                        self.peaks_found.emit(pf.tolist(), pp.tolist())
            except Exception as e:
                self.error.emit(f"SweepWorker: {e}")
            finally:
                self._engine.stop()

        def stop(self):
            self._engine.cancel()  # abort current sweep immediately
            self._engine.stop()
            self.wait(2000)


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------
def _standalone_plot():
    """Run sweep with a live matplotlib display (no Qt needed)."""
    import matplotlib.pyplot as plt

    engine = SweepEngine(use_simulated=not HAS_RTL)
    fig, ax = plt.subplots(figsize=(14, 4))
    line, = ax.plot([], [], lw=0.8, color="#00ff88")
    ax.set_xlim(engine.start_hz / 1e6, engine.stop_hz / 1e6)
    ax.set_ylim(-100, -20)
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("dBFS")
    ax.set_title("20 MHz Panoramic Sweep  (close window to stop)")
    ax.grid(True, alpha=0.2)
    plt.tight_layout()
    plt.ion()
    plt.show()

    print("[sweep20] Starting sweep…  (close matplotlib window to stop)")
    try:
        for spectrum, freqs in engine.run_realtime():
            if not plt.fignum_exists(fig.number):
                break
            line.set_data(freqs, spectrum)
            # Peak markers
            pf, pp = engine.find_peaks(spectrum)
            ax.set_title(
                f"20 MHz Sweep  |  Peaks: {', '.join(f'{f:.1f}' for f in pf) if pf.size else 'none'}"
            )
            fig.canvas.draw_idle()
            fig.canvas.flush_events()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        plt.ioff()
        plt.close()
        print("[sweep20] Stopped.")


def _standalone_text():
    """Run sweep with text-only output."""
    engine = SweepEngine(use_simulated=not HAS_RTL)
    print("[sweep20] Text-mode sweep  (Ctrl+C to stop)")
    try:
        for spectrum, freqs in engine.run_realtime():
            pf, pp = engine.find_peaks(spectrum)
            if pf.size:
                peaks_str = ", ".join(f"{f:.1f}MHz({p:.1f}dB)" for f, p in zip(pf, pp))
                print(f"  Peaks: {peaks_str}")
            else:
                print("  (no peaks)")
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--text", "-t"):
        _standalone_text()
    else:
        try:
            import matplotlib
            _standalone_plot()
        except ImportError:
            print("matplotlib not installed, falling back to text mode")
            _standalone_text()
