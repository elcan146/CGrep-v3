"""
spectrum_core.py - FM Radio Monitoring GUI (SDR# style)
================================================
A single-file PyQt6 application that uses an RTL-SDR (via pyrtlsdr) to:
  * Stream raw IQ samples in a background QThread
  * Compute an FFT spectrum (Blackman-Harris windowed, dBFS) for display
  * Perform Wide-FM (WFM) demodulation -> resample -> push to sounddevice
  * Render a real-time spectrum analyzer + waterfall (pyqtgraph + OpenGL)

Architecture
------------
  [SDR HW]  --IQ-->  SdrWorker (QThread)
                       |--> FFT (numpy)            --signal--> GUI plot
                       |--> WFM demod + decimate   --queue---> AudioWorker
                                                                    |
                                                                    v
                                                            sounddevice OutputStream

The GUI thread never touches the SDR or heavy DSP; the worker emits compact
arrays via Qt signals.  Audio runs in its own callback thread (sounddevice).

Author: generated prototype
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import sys
import json
import queue
import threading
import time
import numpy as np
from scipy import signal

from PyQt6 import QtCore, QtGui, QtWidgets
# QtWebEngineWidgets MUST be imported before QApplication is constructed.
# (On Qt 6 the import alone is sufficient; do NOT also set
# AA_ShareOpenGLContexts -- that forces OpenGL composition and clashes
# with WebEngine's default D3D11/RHI backend, producing a black screen.)
from PyQt6 import QtWebEngineWidgets  # noqa: F401  (side-effects only)
import pyqtgraph as pg

# NOTE: pyqtgraph's OpenGL acceleration is intentionally OFF.  When enabled
# it inserts QOpenGLWidget instances into the window, which forces the
# top-level QWindow onto an OpenGL compositor and then clashes with
# QtWebEngine's QQuickWidget (D3D11/RHI), producing a black screen plus
# "no QRhi" warnings.  Software-rendered line plots are still smooth at the
# data rates spectrum_core runs at.
try:
    pg.setConfigOption('useOpenGL', False)
    pg.setConfigOption('enableExperimental', False)
except Exception:
    pass
pg.setConfigOption('background', '#06020d')
pg.setConfigOption('foreground', '#dddddd')

# Hardware + audio (import lazily-ish so the GUI still starts even if missing)
try:
    from rtlsdr import RtlSdr
except Exception as e:
    RtlSdr = None
    _RTL_IMPORT_ERR = e

try:
    import sounddevice as sd
except Exception as e:
    sd = None
    _SD_IMPORT_ERR = e

import csv
import os
import datetime
from scipy.signal import find_peaks

from sweep20 import SweepEngine, SweepWorker, KNOWN_STATIONS_MHZ, SpectrumPeakLogger


# ---------------------------------------------------------------------------
# Audio worker - pulls demodulated mono float32 audio from a queue
# and feeds a sounddevice OutputStream callback.
# ---------------------------------------------------------------------------
class AudioPlayer:
    """Thread-safe audio sink.

    Uses a dedicated daemon thread that blocks on a queue and writes to
    sounddevice synchronously (sd.RawOutputStream.write).  This keeps audio
    completely independent of the Qt event loop so GUI activity never causes
    glitches or stutter.
    """

    # Chunk size fed to sd.write per loop iteration (≈85 ms @ 48 kHz)
    WRITE_CHUNK = 4096

    def __init__(self, samplerate=48000):
        self.samplerate = samplerate
        # Queue holds numpy float32 arrays from the DSP thread.
        # maxsize=0 means unbounded — we rely on _drain logic instead.
        self.q = queue.Queue(maxsize=0)
        self.volume = 0.5
        self._stream  = None
        self._thread  = None
        self._stop_evt = threading.Event()
        self._ring = np.zeros(0, dtype=np.float32)
        # Audio quality estimation (updated every chunk)
        self.quality = 0.0          # 0.0 (noise/silence) .. 1.0 (clean signal)
        self.state   = "silence"    # "silence" | "noise" | "signal"

    # ------------------------------------------------------------------
    def start(self):
        if sd is None:
            print("sounddevice not available:", _SD_IMPORT_ERR)
            return
        self._stop_evt.clear()
        self._ring = np.zeros(0, dtype=np.float32)
        # Open a blocking output stream.  extra_settings / latency chosen for
        # maximum stability on Windows WASAPI / MME.
        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype='float32',
            blocksize=self.WRITE_CHUNK,
            latency=0.5,          # explicit 500 ms device buffer
        )
        self._stream.start()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_evt.set()
        # Unblock the waiting thread
        try:
            self.q.put_nowait(None)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        # Drain queue
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except Exception:
                break
        self._ring = np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------------
    def drain(self):
        """Flush stale audio after a retune."""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self._ring = np.zeros(0, dtype=np.float32)
        self.quality = 0.0
        self.state   = "silence"

    # ------------------------------------------------------------------
    def _assess_quality(self, chunk: np.ndarray):
        """Estimate audio quality from a demodulated chunk.
        Updates self.quality (0-1) and self.state.

        Three-factor test — all must pass to be called a signal:
          1. RMS level (silence gating)
          2. Low-frequency energy dominance (speech/music vs flat noise)
          3. Spectral flatness of the speech band only (0-8 kHz)
        """
        fs = self.samplerate  # typically 48000
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        if rms < 0.002:
            self.quality = 0.0
            self.state   = "silence"
            return

        fft_mag = np.abs(np.fft.rfft(chunk)).astype(np.float64)
        n_fft   = len(fft_mag)
        freqs   = np.linspace(0, fs / 2, n_fft)

        # ── Factor 1: speech/music energy ratio (below 8 kHz vs full band) ──
        lo_mask  = freqs <  8000
        hi_mask  = freqs >= 8000
        e_lo = float(np.mean(fft_mag[lo_mask] ** 2)) if lo_mask.sum() > 0 else 0
        e_hi = float(np.mean(fft_mag[hi_mask] ** 2)) if hi_mask.sum() > 0 else 1
        lo_ratio = e_lo / (e_hi + 1e-10)
        # Real audio: lo_ratio >> 1  |  FM noise: lo_ratio ≈ 1-3
        energy_ok = lo_ratio > 6.0

        # ── Factor 2: spectral flatness of speech band only ──
        speech = fft_mag[lo_mask]
        speech = np.maximum(speech, 1e-10)
        geo    = float(np.exp(np.mean(np.log(speech))))
        arith  = float(np.mean(speech))
        flatness = geo / arith           # 0=tonal, 1=noise
        flat_ok  = flatness < 0.30       # strict: FM noise is ~0.35-0.60

        # ── Factor 3: 19 kHz pilot tone check (FM stereo marker) ──
        pilot_mask = (freqs >= 18500) & (freqs <= 19500)
        other_mask = (freqs >= 15000) & (freqs <= 23000) & ~pilot_mask
        e_pilot = float(np.mean(fft_mag[pilot_mask] ** 2)) if pilot_mask.sum() > 0 else 0
        e_other = float(np.mean(fft_mag[other_mask] ** 2)) if other_mask.sum() > 0 else 1
        pilot_ok = (e_pilot / (e_other + 1e-10)) > 2.5   # pilot clearly above surrounding

        # ── Scoring ──
        indicators = sum([energy_ok, flat_ok, pilot_ok])
        if indicators >= 2:
            # Good signal: score from lo_ratio and flatness
            score = min(1.0, (lo_ratio / 20.0) * 0.5 + (1.0 - flatness) * 0.5)
            self.quality = round(score, 3)
            self.state   = "signal"
        elif rms > 0.008 and indicators == 0:
            # High RMS but no signal characteristics → noise
            self.quality = round(min(0.35, rms * 5), 3)
            self.state   = "noise"
        else:
            # Weak or ambiguous
            self.quality = round(min(0.5, rms * 8), 3)
            self.state   = "noise"

    # ------------------------------------------------------------------
    def push(self, audio: np.ndarray):
        """Called from the DSP (SdrWorker) thread."""
        a = audio.astype(np.float32, copy=False)
        # If queue is growing too large (>2 s) drop the oldest chunk
        if self.q.qsize() > int(2.0 * self.samplerate / self.WRITE_CHUNK):
            try:
                self.q.get_nowait()
            except Exception:
                pass
        self.q.put(a)

    # ------------------------------------------------------------------
    def _loop(self):
        """Runs on a dedicated daemon thread — completely Qt-independent."""
        CHUNK = self.WRITE_CHUNK
        silence = np.zeros(CHUNK, dtype=np.float32)

        # Pre-buffer ~300 ms before starting playback
        prebuf = int(0.3 * self.samplerate)
        collected = 0
        stash: list[np.ndarray] = []
        while collected < prebuf and not self._stop_evt.is_set():
            try:
                chunk = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            if chunk is None:
                return
            stash.append(chunk)
            collected += chunk.size
        # Re-insert stash so the main loop sees them
        for s in stash:
            self.q.put(s)

        while not self._stop_evt.is_set():
            # Drain queue into ring buffer
            while not self.q.empty():
                try:
                    item = self.q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    return
                self._ring = np.concatenate((self._ring, item))

            if self._ring.size >= CHUNK:
                raw = self._ring[:CHUNK]
                self._ring = self._ring[CHUNK:]
                self._assess_quality(raw)
                out = raw * self.volume
                try:
                    self._stream.write(out.reshape(-1, 1))
                except Exception:
                    pass
            else:
                # Underrun — write silence and wait for more data
                try:
                    self._stream.write(silence.reshape(-1, 1))
                except Exception:
                    pass
                # Block until at least one chunk arrives
                try:
                    item = self.q.get(timeout=0.2)
                    if item is None:
                        return
                    self._ring = np.concatenate((self._ring, item))
                except queue.Empty:
                    pass


# ---------------------------------------------------------------------------
# SDR worker thread - reads IQ, computes FFT, demodulates WFM, emits results
# ---------------------------------------------------------------------------
class SdrWorker(QtCore.QThread):
    """Background thread.

    Reads ``read_size`` IQ samples per cycle from the RTL-SDR, computes a
    Blackman-Harris-windowed FFT (in dBFS), and performs WFM demodulation
    (low-pass -> phase discriminator -> decimation -> de-emphasis) into the
    audio queue belonging to ``audio``.
    """

    # spectrum_db, lo_center_hz (physical), fs, tuned_fc_hz (user target)
    fft_ready = QtCore.pyqtSignal(np.ndarray, float, float, float)
    error = QtCore.pyqtSignal(str)

    AUDIO_RATE = 48000  # output audio sample rate

    def __init__(self, audio: AudioPlayer, parent=None):
        super().__init__(parent)
        self.audio = audio
        # Default radio params
        self._fc = 97.7e6           # center frequency (Hz)
        self._fs = 2.400e6          # sample rate
        self._gain = 'auto'         # RF gain (dB) or 'auto'
        self._bw = 200e3            # WFM bandwidth (Hz)
        # Smaller blocks => smoother audio flow (~16 ms @ 2.048 MSPS).
        # Must be a multiple of 16384 for librtlsdr.
        self._read_size = 32 * 1024
        self._fft_size = 4096

        self._running = False
        self._lock = QtCore.QMutex()
        self._dirty = True          # Need to (re)apply hw settings

        # ---- Streaming DSP state (persistent across blocks) ----
        self._prev_iq = np.complex64(1.0 + 0.0j)   # last IQ sample of previous block
        self._lpf_taps = None       # FIR taps for IF low-pass
        self._lpf_zi = None         # FIR delay line state
        self._dec1 = 1              # integer decimation factor stage 1
        self._fs_if = self._fs      # IF sample rate after stage 1
        self._resamp_up = 1         # rational resampler up
        self._resamp_dn = 1         # rational resampler down
        self._resamp_tail = np.zeros(0, dtype=np.float32)  # overlap for stitching
        self._deemph_b = None       # de-emphasis IIR coeffs
        self._deemph_a = None
        self._deemph_zi = np.zeros(1, dtype=np.float32)
        self._dsp_cfg_fs = None     # last fs we configured DSP for
        self._dsp_cfg_bw = None
        # Offset tuning: tune the SDR LO_OFFSET Hz BELOW the desired centre,
        # then digitally mix the signal back to baseband.  Pushes the RTL-SDR
        # DC spike well outside the FM channel for much cleaner audio.
        self.LO_OFFSET = 250e3
        self._mix_phase = 0.0       # NCO phase accumulator
        # Audio post-filter (15 kHz LPF) - removes 19 kHz pilot + HF noise
        self._aud_taps = None
        self._aud_zi = None

        # Panoramic sweep mode
        self._sweep_mode = False
        self.SWEEP_START  = 88e6
        self.SWEEP_STOP   = 108e6
        self.SWEEP_STEP   = 2e6
        self.SWEEP_FS     = 2.048e6
        self.SWEEP_FFT    = 2048   # per-chunk FFT size
        self.SWEEP_CROP   = 64    # bins to crop each edge + DC notch width

    # ------------------------------------------------------------------
    # Setters (thread-safe via QMutex; actual hw update happens in run())
    # ------------------------------------------------------------------
    def set_sweep_mode(self, enabled: bool):
        with QtCore.QMutexLocker(self._lock):
            self._sweep_mode = bool(enabled)
            self._dirty = True

    def set_freq(self, hz: float):
        with QtCore.QMutexLocker(self._lock):
            self._fc = float(hz)
            self._dirty = True

    def set_sample_rate(self, hz: float):
        with QtCore.QMutexLocker(self._lock):
            self._fs = float(hz)
            self._dirty = True

    def set_gain(self, gain):
        with QtCore.QMutexLocker(self._lock):
            self._gain = gain
            self._dirty = True

    def set_bandwidth(self, hz: float):
        with QtCore.QMutexLocker(self._lock):
            self._bw = float(hz)

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Main loop - uses librtlsdr's ASYNC reader so the USB pipeline is
    # continuously drained.  IQ blocks are pushed into a queue and a DSP
    # loop in this QThread pops them off.  This eliminates the per-block
    # USB starvation gap that the synchronous read_samples() version had.
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Panoramic sweep: synchronous read, retune per step, stitch & emit
    # ------------------------------------------------------------------
    def _run_sweep(self, sdr):
        """Blocking sweep loop — called from run() when sweep mode is on."""
        NFFT   = self.SWEEP_FFT
        CROP   = self.SWEEP_CROP
        FS     = self.SWEEP_FS
        START  = self.SWEEP_START
        STOP   = self.SWEEP_STOP
        STEP   = self.SWEEP_STEP
        DC_W   = 4   # half-width of DC notch in bins

        win      = signal.windows.blackmanharris(NFFT).astype(np.float32)
        win_norm = np.sum(win)

        freqs = np.arange(START, STOP + STEP * 0.5, STEP)

        # ---- Frequency axis for the full stitched array ----
        # Each chunk contributes (NFFT - 2*CROP) bins spanning STEP Hz.
        bins_per_chunk = NFFT - 2 * CROP
        total_bins     = bins_per_chunk * len(freqs)
        freq_axis_mhz  = np.linspace(START / 1e6, STOP / 1e6, total_bins)

        # ---- Peak detection config ----
        PEAK_HEIGHT   = -65.0   # dBFS threshold — only real stations pass
        # Minimum separation: ~200 kHz FM channel = bins_per_chunk * (200kHz/STEP)
        PEAK_DISTANCE = max(1, int(bins_per_chunk * 200e3 / STEP))

        # ---- Logger (one instance per sweep session) ----
        _logger = SpectrumPeakLogger()
        LOG_INTERVAL = 2.0        # seconds between CSV writes
        last_log_time = 0.0

        # Configure SDR for sweep
        try:
            sdr.sample_rate = FS
            sdr.gain = self._gain
        except Exception as e:
            self.error.emit(f"Sweep SDR config: {e}")
            _logger.close()
            return

        while self._running:
            # Check if sweep mode was disabled
            with QtCore.QMutexLocker(self._lock):
                if not self._sweep_mode:
                    return
                gain = self._gain

            chunks = []
            sweep_ok = True

            for fc_step in freqs:
                if not self._running:
                    return
                try:
                    # Retune
                    sdr.center_freq = int(fc_step)
                    # PLL settle: discard dummy block
                    sdr.read_samples(2048)
                    # Real measurement
                    samples = np.asarray(
                        sdr.read_samples(NFFT * 4), dtype=np.complex64)
                except Exception as e:
                    self.error.emit(f"Sweep read error @ {fc_step/1e6:.0f} MHz: {e}")
                    sweep_ok = False
                    break

                # ---- FFT on last NFFT samples ----
                seg = samples[-NFFT:]
                if seg.size < NFFT:
                    seg = np.pad(seg, (0, NFFT - seg.size))

                spec = np.fft.fftshift(np.fft.fft(seg * win))
                mag  = np.abs(spec) / win_norm + 1e-12
                db   = (20.0 * np.log10(mag) + 29.0).astype(np.float32)

                # DC notch: replace centre bins with interpolated neighbours
                ctr = NFFT // 2
                lo_n, hi_n = max(0, ctr - DC_W), min(NFFT - 1, ctr + DC_W)
                db[lo_n:hi_n] = np.interp(
                    np.arange(lo_n, hi_n),
                    [lo_n - 1, hi_n],
                    [db[lo_n - 1], db[hi_n]])

                # Crop outer edges to hide filter roll-off
                db_crop = db[CROP: NFFT - CROP]
                chunks.append(db_crop)

            if not sweep_ok or not chunks:
                time.sleep(0.05)
                continue

            # Stitch all chunks into the full 88-108 MHz array
            stitched = np.concatenate(chunks)

            # ---- Peak detection + throttled CSV logging ----
            now = time.monotonic()
            if now - last_log_time >= LOG_INTERVAL and _scan_state.get("panoramic_log", False):
                try:
                    peak_idxs, peak_props = find_peaks(
                        stitched,
                        height=PEAK_HEIGHT,
                        distance=PEAK_DISTANCE,
                        prominence=6.0)   # must stand at least 6 dB above neighbours
                    if peak_idxs.size > 0 and peak_idxs.max() < len(freq_axis_mhz):
                        peak_freqs = np.round(freq_axis_mhz[peak_idxs], 1)
                        peak_powers = stitched[peak_idxs]
                        _logger.write_peaks(peak_freqs, peak_powers)
                        print(f"[sweep_logger] {len(peak_idxs)} peaks logged "
                              f"({', '.join(f'{f:.1f}' for f in peak_freqs)} MHz)")
                except Exception as exc:
                    print(f"[sweep_logger] Peak detect error: {exc}")
                last_log_time = now

            # Emit full stitched array to UI — fc=98 MHz (band centre), fs=20 MHz
            self.fft_ready.emit(stitched, 98e6, 20e6, 98e6)
            # ~4 sweeps/sec is plenty for a panoramic view
            time.sleep(0.05)

        _logger.close()

    def run(self):
        if RtlSdr is None:
            self.error.emit(f"pyrtlsdr not available: {_RTL_IMPORT_ERR}")
            return
        try:
            sdr = RtlSdr()
        except Exception as e:
            self.error.emit(f"Could not open RTL-SDR: {e}")
            return

        self._running = True

        # ---- Branch: panoramic sweep mode vs. single-frequency mode ----
        with QtCore.QMutexLocker(self._lock):
            sweep_on = self._sweep_mode
        if sweep_on:
            self._run_sweep(sdr)
            try: sdr.close()
            except Exception: pass
            return

        # Force DSP reconfigure on first block (clears any stale filter state)
        self._dsp_cfg_fs = None
        self._dsp_cfg_bw = None
        self._resamp_tail = np.zeros(0, dtype=np.float32)
        # Pre-build FFT window
        win = signal.windows.blackmanharris(self._fft_size).astype(np.float32)
        win_norm = np.sum(win)

        # IQ queue between the librtlsdr callback thread and this DSP thread.
        # Audio absolutely must NOT stall, so on overflow we drop the OLDEST
        # block (low latency is more important than completeness).
        iq_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=8)

        # Apply initial hardware settings before starting the async reader
        with QtCore.QMutexLocker(self._lock):
            fc = self._fc
            fs = self._fs
            gain = self._gain
            bw = self._bw
            self._dirty = False
        try:
            sdr.sample_rate = fs
            sdr.center_freq = fc - self.LO_OFFSET
            sdr.gain = gain
        except Exception as e:
            self.error.emit(f"Initial SDR config error: {e}")
            try: sdr.close()
            except Exception: pass
            return

        # ---- Async reader thread ----
        reader_done = threading.Event()

        def _iq_callback(samples, ctx):
            if not self._running:
                try: sdr.cancel_read_async()
                except Exception: pass
                return
            arr = np.asarray(samples, dtype=np.complex64)
            try:
                iq_q.put_nowait(arr)
            except queue.Full:
                # Drop the oldest block to keep latency bounded
                try:
                    iq_q.get_nowait()
                    iq_q.put_nowait(arr)
                except Exception:
                    pass

        def _reader():
            try:
                sdr.read_samples_async(_iq_callback,
                                       num_samples=self._read_size)
            except Exception as e:
                if self._running:
                    self.error.emit(f"SDR async read error: {e}")
            finally:
                reader_done.set()

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        # Throttle FFT updates to ~20 fps so the GUI thread isn't flooded
        # at high IQ rates - this keeps the audio path's CPU headroom large.
        last_fft = 0.0
        fft_interval = 1.0 / 20.0

        try:
            while self._running:
                # ---- Apply pending HW changes (retune / gain) ----
                with QtCore.QMutexLocker(self._lock):
                    new_fc, new_fs, new_gain, new_bw, dirty = (
                        self._fc, self._fs, self._gain, self._bw, self._dirty)
                    self._dirty = False
                if dirty:
                    try:
                        if new_fs != fs:
                            # Sample-rate change requires async restart
                            try: sdr.cancel_read_async()
                            except Exception: pass
                            reader_done.wait(2.0)
                            sdr.sample_rate = new_fs
                            sdr.center_freq = new_fc - self.LO_OFFSET
                            sdr.gain = new_gain
                            # Drain any stale IQ
                            with iq_q.mutex:
                                iq_q.queue.clear()
                            reader_done.clear()
                            reader_thread = threading.Thread(
                                target=_reader, daemon=True)
                            reader_thread.start()
                        else:
                            sdr.center_freq = new_fc - self.LO_OFFSET
                            sdr.gain = new_gain
                    except Exception as e:
                        self.error.emit(f"SDR config error: {e}")
                    fc, fs, gain, bw = new_fc, new_fs, new_gain, new_bw
                    self._mix_phase = 0.0

                # ---- Pull next IQ block ----
                try:
                    iq = iq_q.get(timeout=0.2)
                except queue.Empty:
                    continue

                # ---- Apply digital LO shift ONCE (used by both FFT + demod)
                # After this, baseband 0 Hz == tuned frequency, and the
                # hardware DC spike has been pushed to -LO_OFFSET.
                if (self._dsp_cfg_fs != fs):
                    # Force DSP reconfigure so filter state is fresh
                    self._configure_dsp(fs, bw)
                iq = self._apply_mixer(iq, fs)

                # ---- WFM demod (priority: feed audio first) ----
                audio = self._wfm_demod(iq, fs, bw)
                if audio is not None and audio.size:
                    self.audio.push(audio)

                # ---- FFT (rate-limited) ----
                now = time.monotonic()
                if now - last_fft >= fft_interval:
                    last_fft = now
                    seg = iq[-self._fft_size:]
                    if seg.size < self._fft_size:
                        seg = np.pad(seg, (0, self._fft_size - seg.size))
                    spec = np.fft.fftshift(np.fft.fft(seg * win))
                    mag = np.abs(spec) / win_norm + 1e-12
                    spec_db = (20.0 * np.log10(mag) + 29.0).astype(np.float32)
                    # IQ is now centred on the tuned frequency, so display
                    # centre == marker == tuned fc.
                    self.fft_ready.emit(spec_db, fc, fs, fc)
        finally:
            self._running = False
            try: sdr.cancel_read_async()
            except Exception: pass
            reader_done.wait(2.0)
            try: sdr.close()
            except Exception: pass

    # ------------------------------------------------------------------
    # Digital LO shift that undoes the hardware offset tuning.  Applied
    # ONCE per IQ block - both the FFT (display) and the demod use the
    # mixed signal, so the spectrum is centred on the tuned frequency
    # and the carrier sits exactly under the tuning marker.
    # ------------------------------------------------------------------
    def _apply_mixer(self, iq: np.ndarray, fs: float) -> np.ndarray:
        n = iq.size
        step = -2.0 * np.pi * self.LO_OFFSET / fs
        ph = self._mix_phase + step * np.arange(n, dtype=np.float64)
        mix = np.exp(1j * ph).astype(np.complex64)
        out = iq * mix
        self._mix_phase = float((self._mix_phase + step * n) % (2.0 * np.pi))
        return out

    # ------------------------------------------------------------------
    # Configure the DSP chain for a given input sample rate / bandwidth.
    # Builds filter taps and computes resampling ratios.  Persistent state
    # (filter delay lines) is preserved across blocks for click-free output.
    # ------------------------------------------------------------------
    def _configure_dsp(self, fs: float, bw: float):
        from math import gcd
        # Stage 1: integer decimation to an IF rate near 240 kHz that fully
        # contains the FM signal (~200 kHz channel).
        target_if = max(bw * 1.2, 240e3)
        dec1 = max(int(round(fs / target_if)), 1)
        fs_if = fs / dec1

        # Anti-alias / channel filter.  Cutoff = max(bw/2, 100 kHz),
        # transition into Nyquist of the IF.
        cutoff = min(max(bw * 0.5, 100e3), fs_if * 0.45)
        # ~129-tap FIR is a good balance of selectivity and CPU.
        numtaps = 129
        taps = signal.firwin(numtaps, cutoff, fs=fs).astype(np.float32)

        # Rational resample from fs_if -> AUDIO_RATE
        fs_if_int = int(round(fs_if))
        g = gcd(fs_if_int, self.AUDIO_RATE)
        up = self.AUDIO_RATE // g
        dn = fs_if_int // g

        # 75 us de-emphasis as a 1-pole IIR at AUDIO_RATE
        tau = 75e-6
        dt = 1.0 / self.AUDIO_RATE
        alpha = dt / (tau + dt)
        b = np.array([alpha], dtype=np.float32)
        a = np.array([1.0, -(1.0 - alpha)], dtype=np.float32)

        # 15 kHz audio low-pass FIR @ AUDIO_RATE - removes the 19 kHz stereo
        # pilot tone and high-frequency hiss, the single biggest source of
        # "harshness" vs SDR#'s output.
        aud_taps = signal.firwin(
            127, 15000.0, fs=self.AUDIO_RATE).astype(np.float32)

        # Commit
        self._lpf_taps = taps
        self._lpf_zi = np.zeros(numtaps - 1, dtype=np.complex64)
        self._dec1 = dec1
        self._fs_if = fs_if
        self._resamp_up = up
        self._resamp_dn = dn
        self._deemph_b = b
        self._deemph_a = a
        self._deemph_zi = np.zeros(max(len(a), len(b)) - 1, dtype=np.float32)
        self._aud_taps = aud_taps
        self._aud_zi = np.zeros(len(aud_taps) - 1, dtype=np.float32)
        self._prev_iq = np.complex64(1.0 + 0.0j)
        self._mix_phase = 0.0
        self._dsp_cfg_fs = fs
        self._dsp_cfg_bw = bw

    # ------------------------------------------------------------------
    # WFM demod: stateful LPF -> decimate -> phase discriminator
    #            -> rational resample -> de-emphasis (75 us)
    # All filters keep state across calls so block boundaries are seamless.
    # ------------------------------------------------------------------
    def _wfm_demod(self, iq: np.ndarray, fs: float, bw: float):
        if (self._lpf_taps is None
                or self._dsp_cfg_fs != fs
                or self._dsp_cfg_bw != bw):
            self._configure_dsp(fs, bw)

        # Stage 1: stateful low-pass + integer decimate.
        # lfilter on complex IQ with real taps applies the same FIR to I and Q.
        filtered, self._lpf_zi = signal.lfilter(
            self._lpf_taps, [1.0], iq, zi=self._lpf_zi)
        iq_if = filtered[::self._dec1].astype(np.complex64, copy=False)
        fs_if = self._fs_if
        if iq_if.size < 2:
            return None

        # Stage 2: FM discriminator using x[n] * conj(x[n-1]).
        # Stitch with the actual last IQ sample of the previous block (not
        # just its phase) so the very first output sample is continuous.
        x = np.empty(iq_if.size, dtype=np.complex64)
        x[0] = iq_if[0] * np.conj(self._prev_iq)
        x[1:] = iq_if[1:] * np.conj(iq_if[:-1])
        self._prev_iq = iq_if[-1]
        demod = np.angle(x).astype(np.float32)
        # Scale: peak deviation 75 kHz -> ~ +/-1.0
        demod *= fs_if / (2.0 * np.pi * 75e3)

        # Stage 3: rational resample to AUDIO_RATE (polyphase FIR).
        # resample_poly is not stateful; we mitigate boundary artifacts by
        # carrying a short tail from the previous block and overlapping.
        TAIL = 64  # samples kept at high rate for overlap
        if self._resamp_tail.size:
            x_in = np.concatenate((self._resamp_tail, demod))
            consumed_offset = self._resamp_tail.size
        else:
            x_in = demod
            consumed_offset = 0
        # Save new tail for next call
        self._resamp_tail = demod[-TAIL:].copy() if demod.size >= TAIL else demod.copy()

        audio = signal.resample_poly(
            x_in, self._resamp_up, self._resamp_dn).astype(np.float32, copy=False)
        # Trim the part of the output corresponding to the old tail to avoid
        # re-emitting samples already produced last call.
        skip_out = int(round(consumed_offset * self._resamp_up / self._resamp_dn))
        if skip_out > 0 and skip_out < audio.size:
            audio = audio[skip_out:]

        # Stage 4: 75 us de-emphasis (vectorised, stateful).
        audio, self._deemph_zi = signal.lfilter(
            self._deemph_b, self._deemph_a, audio, zi=self._deemph_zi)

        # Stage 5: 15 kHz audio low-pass.
        audio, self._aud_zi = signal.lfilter(
            self._aud_taps, [1.0], audio, zi=self._aud_zi)
        audio = audio.astype(np.float32, copy=False)

        # Soft clip
        np.clip(audio, -1.0, 1.0, out=audio)
        return audio


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    WATERFALL_ROWS = 256
    FFT_BINS = 4096

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CGrep - FM Radio Monitor")
        self.resize(1400, 800)

        # ---- DSP / audio backends ----
        self.audio = AudioPlayer(samplerate=SdrWorker.AUDIO_RATE)
        self.worker = SdrWorker(self.audio)
        self.worker.fft_ready.connect(self._on_fft, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.error.connect(self._on_error, QtCore.Qt.ConnectionType.QueuedConnection)

        self.sweep_worker = SweepWorker(
            use_simulated=RtlSdr is None,
        )
        self.sweep_worker.fft_ready.connect(self._on_fft, QtCore.Qt.ConnectionType.QueuedConnection)
        self.sweep_worker.error.connect(self._on_error, QtCore.Qt.ConnectionType.QueuedConnection)

        # ---- Display state ----
        self.spec_smoothed = None      # EMA-smoothed dB spectrum for display
        self.spec_alpha = 0.35
        self.waterfall_buf = np.full(
            (self.WATERFALL_ROWS, self.FFT_BINS), -120.0, dtype=np.float32)
        self._wf_row = 0          # ring-buffer write pointer
        self._last_wf_draw = 0.0  # throttle waterfall redraws
        self._fc = 97.7e6
        self._fs = 2.400e6

        # ---- Build UI ----
        self._build_ui()
        self._apply_initial_settings()

    # ------------------------------------------------------------------
    # Seamless in-window page swap from the 3D landing map (page 0) to the
    # FM monitor UI (page 1).  Triggered by the React app POSTing
    # /switch/spectrum_core (which fires _UiBridge.switch_requested on the GUI thread).
    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def switch_to_monitor(self):
        # ── Halt ALL background SDR activity so the USB device is free ──
        # 1) Stop the panoramic sweep logger (it opens/closes RTL-SDR in a thread)
        # 2) Stop the 2 MHz station-sweep logger
        _scan_state["log_stop"]      = True
        _scan_state["log_active"]    = False
        _scan_state["log_running"]   = False
        _scan_state["log_scan_idx"]  = 0
        try:
            if _scan_state.get("log_file"):
                _scan_state["log_file"].close()
                _scan_state["log_file"]   = None
                _scan_state["log_writer"] = None
        except Exception:
            pass
        # 3) Pause the FM band scanner loop
        _scan_state["active"] = False
        # 4) Stop any active sweep worker
        self.sweep_worker.stop()
        self.sweep_worker.wait(1500)
        # 5) Cancel any in-progress background panoramic sweep
        engine = _scan_state.get("pano_engine")
        if engine:
            engine.cancel()
        # 6) Switch to monitor page FIRST so panoramic logger sees monitor_open
        if hasattr(self, "stack"):
            self.stack.setCurrentIndex(1)
        # 7) Wait for the panoramic logger loop to notice monitor_open=True,
        #    finish its sweep, and release the SDR (~1 sweep + margin).
        import time as _t; _t.sleep(3.0)
        self.tune_to_freq(97.7e6)   # instant tune to default station
        if hasattr(self, "stack"):
            # Load the spectrum HTML only after the page is visible so
            # QWebEngine's JS engine initialises properly.
            self.spec_web.setHtml(self._SPECTRUM_HTML)
            self._spec_web_ready = False  # wait for loadFinished
            # Fade-in the monitor page
            monitor = self.stack.widget(1)
            effect = QtWidgets.QGraphicsOpacityEffect(monitor)
            monitor.setGraphicsEffect(effect)
            self._fade_anim = QtCore.QPropertyAnimation(effect, b"opacity")
            self._fade_anim.setDuration(600)
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
            self._fade_anim.finished.connect(lambda: monitor.setGraphicsEffect(None))
            self._fade_anim.start()
        # Free the embedded Chromium tab so it stops eating CPU/RAM in
        # the background once we've switched away from the landing map.
        if hasattr(self, "web_view") and self.web_view is not None:
            try:
                self.web_view.stop()
                self.web_view.setUrl(QtCore.QUrl("about:blank"))
            except Exception:
                pass

    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def switch_to_map(self):
        # Re-silence audio, resume scanner, restore logging mode
        self.audio.volume = 0.0
        _scan_state["active"]        = True
        _scan_state["log_stop"]      = False
        _scan_state["panoramic_log"] = True   # default mode — background pano logger resumes
        # Don't restart the 2 MHz file logger automatically — pano mode is default
        if hasattr(self, "stack"):
            self.stack.setCurrentIndex(0)
        # Reload the map when switching back
        if hasattr(self, "web_view") and self.web_view is not None:
            try:
                self.web_view.setUrl(QtCore.QUrl("http://127.0.0.1:8000"))
            except Exception:
                pass
        # Stop monitoring when switching back to map
        if self.btn_power.isChecked():
            self.btn_power.setChecked(False)

    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def mute_audio(self):
        if hasattr(self, "audio") and self.audio is not None:
            self.audio.volume = 0.0

    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def unmute_audio(self):
        if hasattr(self, "audio") and self.audio is not None:
            self.audio.volume = 0.5

    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def start_radio(self):
        if not self.btn_power.isChecked():
            self.btn_power.setChecked(True)

    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def stop_radio(self):
        if self.btn_power.isChecked():
            self.btn_power.setChecked(False)

    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def start_scan_worker(self):
        """Start the SDR worker for background scanning — audio silenced."""
        if not self.worker._running:
            self.audio.volume = 0.0   # silent scan
            self.audio.start()
            self.worker.start()

    @QtCore.pyqtSlot(float)
    def tune_to_freq(self, hz: float):
        """Tune SDR to hz: mute, flush stale audio, retune, then unmute."""
        self.audio.volume = 0.0
        self.audio.drain()
        mhz = hz / 1e6
        self.freq_spin.setValue(mhz)  # dirty flag → worker retuning hardware
        # If in narrow 2 MHz mode and running, just retune the FM worker
        if self.btn_power.isChecked():
            span = 20 if self.btn_span.isChecked() else 2
            if span < 18:
                self.worker.set_freq(hz)
                self.worker._dirty = True
        # Unmute after SDR settles (~800 ms) to avoid transition noise
        QtCore.QTimer.singleShot(800, lambda: setattr(self.audio, 'volume', 0.5))

    @QtCore.pyqtSlot()
    def stop_and_scan(self):
        """Called by /radio/untune — re-silence audio and resume scanner."""
        self.audio.volume = 0.0       # back to silent scan mode

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self):
        # The window hosts a QStackedWidget so we can flip between two pages
        # inside the SAME desktop window (no PyWebView, no separate process):
        #   page 0 = QWebEngineView showing the React 3D wave map
        #   page 1 = the FM monitor UI (top bar + sidebars + spectrum + waterfall)
        # Clicking the Site-1 peak triggers a switch from page 0 -> page 1.
        self.stack = QtWidgets.QStackedWidget()
        self.setCentralWidget(self.stack)

        # ---- Page 0: 3D landing map ----
        self.web_view = QtWebEngineWidgets.QWebEngineView()
        self.web_view.setUrl(QtCore.QUrl("http://127.0.0.1:8000"))
        self.stack.addWidget(self.web_view)

        # ---- Page 1: FM monitor ----
        monitor_page = QtWidgets.QWidget()
        monitor_page.setObjectName("monitorPage")
        self.stack.addWidget(monitor_page)
        root = QtWidgets.QVBoxLayout(monitor_page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Global dark stylesheet — macOS-style frosted glass
        monitor_page.setStyleSheet("""
            QWidget#monitorPage { background:#111318; }
            QGroupBox {
                color:rgba(255,255,255,0.55);
                font-family:'SF Pro Display','Helvetica Neue',Arial,sans-serif;
                font-size:10px; font-weight:600; letter-spacing:0.8px;
                border:1px solid rgba(255,255,255,0.08);
                border-radius:10px; margin-top:16px;
                background:rgba(28,30,36,0.85);
            }
            QGroupBox::title {
                subcontrol-origin:margin; left:12px; top:3px;
                color:rgba(255,255,255,0.38); letter-spacing:1px; font-size:9px;
            }
            QLabel {
                color:rgba(255,255,255,0.45);
                font-family:'SF Pro Text','Helvetica Neue',Arial,sans-serif;
                font-size:11px;
            }
            QDoubleSpinBox, QComboBox {
                background:rgba(255,255,255,0.07);
                color:rgba(255,255,255,0.88);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:7px; padding:4px 8px;
                font-family:'SF Pro Text','Helvetica Neue',Arial,sans-serif;
                font-size:12px;
                selection-background-color:rgba(10,132,255,0.40);
            }
            QDoubleSpinBox:focus, QComboBox:focus {
                border:1px solid rgba(10,132,255,0.70);
            }
            QComboBox QAbstractItemView {
                background:#1c1e24; color:rgba(255,255,255,0.85);
                border:1px solid rgba(255,255,255,0.10);
                selection-background-color:rgba(10,132,255,0.35);
                border-radius:6px;
            }
            QComboBox::drop-down { border:none; }
            QComboBox::down-arrow {
                image:none; width:0; height:0;
                border-left:4px solid transparent;
                border-right:4px solid transparent;
                border-top:5px solid rgba(255,255,255,0.40);
            }
            QSlider::groove:horizontal {
                height:3px; background:rgba(255,255,255,0.10);
                border-radius:2px;
            }
            QSlider::handle:horizontal {
                width:14px; height:14px; margin:-6px 0;
                background:white; border-radius:7px;
                border:none;
            }
            QSlider::handle:horizontal:hover { background:#e0e0e0; }
            QSlider::sub-page:horizontal {
                background:rgba(10,132,255,0.80);
                border-radius:2px;
            }
            QPushButton {
                background:rgba(255,255,255,0.08);
                color:rgba(255,255,255,0.80);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:8px; padding:5px 16px;
                font-family:'SF Pro Text','Helvetica Neue',Arial,sans-serif;
                font-size:12px; font-weight:500;
            }
            QPushButton:hover { background:rgba(255,255,255,0.14); }
            QPushButton:pressed { background:rgba(255,255,255,0.06); }
        """)

        # Show the landing page first; switch_to_monitor() flips to page 1.
        self.stack.setCurrentIndex(0)

        # ---- Top bar ----
        top_bar = QtWidgets.QWidget()
        top_bar.setFixedHeight(52)
        top_bar.setStyleSheet(
            "background:rgba(20,22,28,0.97);"
            "border-bottom:1px solid rgba(255,255,255,0.07);")
        top = QtWidgets.QHBoxLayout(top_bar)
        top.setContentsMargins(16, 0, 16, 0)
        top.setSpacing(10)

        # Power button — green/red macOS traffic-light style
        self.btn_power = QtWidgets.QPushButton("  Start")
        self.btn_power.setCheckable(True)
        self.btn_power.setFixedSize(90, 32)
        self.btn_power.setStyleSheet(
            "QPushButton { background:rgba(48,209,88,0.18); color:#30d158;"
            "  border:1px solid rgba(48,209,88,0.35); border-radius:8px;"
            "  font-family:'SF Pro Text','Helvetica Neue',Arial; font-size:12px; font-weight:500; }"
            "QPushButton:hover { background:rgba(48,209,88,0.28); }"
            "QPushButton:checked { background:rgba(255,69,58,0.18); color:#ff453a;"
            "  border:1px solid rgba(255,69,58,0.40); }"
            "QPushButton:checked:hover { background:rgba(255,69,58,0.28); }")
        self.btn_power.toggled.connect(self._on_power_toggled)
        self.btn_power.toggled.connect(
            lambda on: self.btn_power.setText("  Stop" if on else "  Start"))

        # Freq spinbox
        freq_lbl = QtWidgets.QLabel("Frequency")
        freq_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.30);"
            "font-family:'SF Pro Text','Helvetica Neue',Arial; font-size:11px;")
        self.freq_spin = QtWidgets.QDoubleSpinBox()
        self.freq_spin.setSuffix(" MHz")
        self.freq_spin.setDecimals(3)
        self.freq_spin.setRange(24.0, 1766.0)
        self.freq_spin.setSingleStep(0.1)
        self.freq_spin.setValue(97.700)
        self.freq_spin.setFixedWidth(155)
        self.freq_spin.valueChanged.connect(
            lambda mhz: self.worker.set_freq(mhz * 1e6))

        # Status pill
        self.status_lbl = QtWidgets.QLabel("Idle")
        self.status_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.30);"
            "font-family:'SF Pro Text','Helvetica Neue',Arial;"
            "font-size:11px;")

        top.addWidget(self.btn_power)
        top.addSpacing(12)
        top.addWidget(freq_lbl)
        top.addWidget(self.freq_spin)
        top.addStretch(1)

        # Title
        title = QtWidgets.QLabel("Spectrum Monitor")
        title.setStyleSheet(
            "color:rgba(255,255,255,0.55);"
            "font-family:'SF Pro Display','Helvetica Neue',Arial;"
            "font-size:13px; font-weight:600;")
        top.addWidget(title)
        top.addStretch(1)

        top.addWidget(self.status_lbl)
        top.addSpacing(8)

        # Span toggle — switches between 20 MHz (full FM band) and 2 MHz (narrow)
        self.btn_span = QtWidgets.QPushButton("2 MHz")
        self.btn_span.setCheckable(True)
        self.btn_span.setChecked(False)
        self.btn_span.setFixedSize(72, 32)
        self.btn_span.setStyleSheet(
            "QPushButton { background:rgba(255,159,10,0.12); color:#ff9f0a;"
            "  border:1px solid rgba(255,159,10,0.35); border-radius:8px;"
            "  font-family:'SF Pro Text','Helvetica Neue',Arial; font-size:12px; font-weight:500; }"
            "QPushButton:hover { background:rgba(255,159,10,0.22); }"
            "QPushButton:checked { background:rgba(255,159,10,0.30); color:#ffcc00;"
            "  border:1px solid rgba(255,204,0,0.55); }"
            "QPushButton:checked:hover { background:rgba(255,159,10,0.40); }")
        self.btn_span.toggled.connect(self._on_span_toggled)
        top.addWidget(self.btn_span)
        top.addSpacing(12)

        self.btn_back = QtWidgets.QPushButton("← Map")
        self.btn_back.setFixedSize(75, 32)
        self.btn_back.setStyleSheet(
            "QPushButton { background:rgba(255,255,255,0.07); color:rgba(255,255,255,0.55);"
            "  border:1px solid rgba(255,255,255,0.10); border-radius:8px;"
            "  font-family:'SF Pro Text','Helvetica Neue',Arial; font-size:12px; font-weight:500; }"
            "QPushButton:hover { background:rgba(255,255,255,0.13); }")
        self.btn_back.clicked.connect(self.switch_to_map)
        top.addWidget(self.btn_back)

        root.addWidget(top_bar)

        # ---- Body: left sidebar | center | right sidebar ----
        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(6, 6, 6, 6)
        body.setSpacing(6)
        root.addLayout(body, 1)

        body.addWidget(self._build_left_sidebar())
        body.addWidget(self._build_center(), 1)
        body.addWidget(self._build_right_sidebar())

    # ------------------------------------------------------------------
    def _build_left_sidebar(self):
        box = QtWidgets.QGroupBox("HARDWARE")
        box.setMaximumWidth(220)
        lay = QtWidgets.QFormLayout(box)

        self.sr_combo = QtWidgets.QComboBox()
        for label, val in [("1.024 MSPS", 1.024e6),
                           ("2.048 MSPS", 2.048e6),
                           ("2.4 MSPS", 2.4e6)]:
            self.sr_combo.addItem(label, val)
        self.sr_combo.setCurrentIndex(2)
        self.sr_combo.currentIndexChanged.connect(
            lambda _i: self.worker.set_sample_rate(self.sr_combo.currentData()))
        lay.addRow("Sample rate:", self.sr_combo)

        def _row(slider, lbl):
            w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            h.addWidget(slider, 1); h.addWidget(lbl)
            lbl.setFixedWidth(46)
            lbl.setStyleSheet("color:rgba(255,255,255,0.55); font-size:11px; qproperty-alignment:AlignRight;")
            return w

        self.bw_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.bw_slider.setRange(50, 250)
        self.bw_slider.setValue(200)
        self.bw_lbl = QtWidgets.QLabel("200 kHz")
        self.bw_slider.valueChanged.connect(self._on_bw_changed)
        lay.addRow("WFM BW:", _row(self.bw_slider, self.bw_lbl))

        self.gain_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.gain_slider.setRange(-1, 50)
        self.gain_slider.setValue(40)
        self.gain_lbl = QtWidgets.QLabel("40 dB")
        self.gain_slider.valueChanged.connect(self._on_gain_changed)
        lay.addRow("RF gain:", _row(self.gain_slider, self.gain_lbl))

        self.vol_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(50)
        self.vol_lbl = QtWidgets.QLabel("50%")
        self.vol_slider.valueChanged.connect(self._on_vol_changed)
        lay.addRow("Volume:", _row(self.vol_slider, self.vol_lbl))

        lay.addItem(QtWidgets.QSpacerItem(
            10, 10, QtWidgets.QSizePolicy.Policy.Minimum,
            QtWidgets.QSizePolicy.Policy.Expanding))
        return box

    # ------------------------------------------------------------------
    _SPECTRUM_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#03000a; overflow:hidden; }
canvas { position:absolute; top:0; left:0; }
#info { position:fixed; top:6px; left:10px;
  font:10px/1 'Courier New',monospace; color:rgba(0,220,255,0.75);
  letter-spacing:.14em; pointer-events:none; }
</style></head><body>
<canvas id="s"></canvas>
<div id="info">SCANNING&hellip;</div>
<script>
const cvs = document.getElementById('s');
const ctx = cvs.getContext('2d');
const info = document.getElementById('info');

const PAD = {l:52, r:8, t:10, b:28};
const LERP = 0.32;
let dbMin=-68, dbMax=-28;
let freqLo=0, freqHi=0, tuned=0;
let N=0, target=null, current=null, peaks=null;
let fc=0, fs=0;

function resize(){
  cvs.width  = window.innerWidth  * devicePixelRatio;
  cvs.height = window.innerHeight * devicePixelRatio;
  cvs.style.width  = window.innerWidth  + 'px';
  cvs.style.height = window.innerHeight + 'px';
}
window.addEventListener('resize', resize); resize();

function setRange(lo, hi){ dbMin=lo; dbMax=hi; }
function setFreqRange(lo, hi, _fc){ freqLo=lo; freqHi=hi; tuned=_fc; }

function updateSpectrum(data, _fc, _fs){
  fc=_fc; fs=_fs;
  if(!freqHi){ freqLo=_fc-_fs/2; freqHi=_fc+_fs/2; tuned=_fc; }
  if(!target || target.length!==data.length){
    N=data.length;
    target=new Float32Array(data);
    current=new Float32Array(data);
    peaks=new Float32Array(data);
  } else { target.set(data); }
  info.textContent = (fc/1e6).toFixed(4)+' MHz   ►  '+
    (fs/1e6).toFixed(3)+' MSPS   dB ['+dbMin+' / '+dbMax+']';
}

function dbToY(v, top, h){
  return top + h*(1-(Math.max(dbMin,Math.min(dbMax,v))-dbMin)/(dbMax-dbMin));
}
function freqToX(f, left, w){
  if(freqHi===freqLo) return left;
  return left + w*(f-freqLo)/(freqHi-freqLo);
}

function drawGrid(left, top, w, h){
  ctx.save();
  ctx.shadowBlur=0;
  /* dB horizontal lines */
  const step = (dbMax-dbMin)<=60 ? 10 : 20;
  ctx.font = Math.round(10*devicePixelRatio)+'px Courier New';
  ctx.textAlign='right'; ctx.textBaseline='middle';
  for(let db=Math.ceil(dbMin/step)*step; db<=dbMax; db+=step){
    const y = dbToY(db, top, h);
    ctx.strokeStyle = 'rgba(0,200,255,0.10)';
    ctx.lineWidth = devicePixelRatio;
    ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(left+w,y); ctx.stroke();
    ctx.fillStyle='rgba(0,200,255,0.50)';
    ctx.fillText(db+' dB', left-4, y);
  }
  /* freq vertical lines ~5 divisions */
  const span = freqHi - freqLo;
  const fMag = Math.pow(10, Math.floor(Math.log10(span/4)));
  const fStep = Math.round(span/(5*fMag))*fMag;
  ctx.textAlign='center'; ctx.textBaseline='top';
  for(let f=Math.ceil(freqLo/fStep)*fStep; f<=freqHi; f+=fStep){
    const x = freqToX(f, left, w);
    ctx.strokeStyle = 'rgba(0,200,255,0.10)';
    ctx.lineWidth = devicePixelRatio;
    ctx.beginPath(); ctx.moveTo(x,top); ctx.lineTo(x,top+h); ctx.stroke();
    ctx.fillStyle='rgba(0,200,255,0.45)';
    ctx.fillText((f/1e6).toFixed(2), x, top+h+4);
  }
  /* tuned frequency marker */
  if(tuned){ const x=freqToX(tuned,left,w);
    ctx.strokeStyle='rgba(255,200,0,0.70)';
    ctx.lineWidth=1.5*devicePixelRatio;
    ctx.setLineDash([6*devicePixelRatio,4*devicePixelRatio]);
    ctx.beginPath(); ctx.moveTo(x,top); ctx.lineTo(x,top+h); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.restore();
}

function buildPath(arr, left, top, w, h){
  const n=arr.length, xs=w/(n-1);
  ctx.beginPath();
  ctx.moveTo(left, dbToY(arr[0],top,h));
  for(let i=1;i<n-1;i++){
    const mx=left+(i-0.5)*xs;
    const my=(dbToY(arr[i-1],top,h)+dbToY(arr[i],top,h))/2;
    ctx.quadraticCurveTo(left+(i-1)*xs, dbToY(arr[i-1],top,h), mx, my);
  }
  ctx.lineTo(left+(n-1)*xs, dbToY(arr[n-1],top,h));
}

function loop(){
  requestAnimationFrame(loop);
  if(!current) return;

  const W=cvs.width, H=cvs.height;
  const left=PAD.l*devicePixelRatio, top=PAD.t*devicePixelRatio;
  const w=W-left-PAD.r*devicePixelRatio, h=H-top-PAD.b*devicePixelRatio;

  /* lerp + peak hold */
  for(let i=0;i<N;i++){
    current[i]+=(target[i]-current[i])*LERP;
    if(current[i]>peaks[i]) peaks[i]=current[i];
    else peaks[i]+=(current[i]-peaks[i])*0.035;
  }

  /* light ghost trail */
  ctx.fillStyle='rgba(3,0,10,0.72)';
  ctx.fillRect(0,0,W,H);

  drawGrid(left, top, w, h);

  /* border rect */
  ctx.strokeStyle='rgba(0,180,255,0.18)';
  ctx.lineWidth=devicePixelRatio;
  ctx.shadowBlur=0;
  ctx.strokeRect(left,top,w,h);

  /* clip to plot area */
  ctx.save();
  ctx.beginPath(); ctx.rect(left,top,w,h); ctx.clip();

  /* FILL */
  buildPath(current,left,top,w,h);
  ctx.lineTo(left+w,top+h); ctx.lineTo(left,top+h); ctx.closePath();
  const grd=ctx.createLinearGradient(0,top,0,top+h);
  grd.addColorStop(0.00,'rgba(255,240,60,0.88)');
  grd.addColorStop(0.10,'rgba(255,20,120,0.78)');
  grd.addColorStop(0.40,'rgba(140,0,255,0.40)');
  grd.addColorStop(0.80,'rgba(0,180,255,0.12)');
  grd.addColorStop(1.00,'rgba(0,0,20,0.00)');
  ctx.fillStyle=grd; ctx.shadowBlur=0; ctx.fill();

  /* UNDERGLOW */
  buildPath(current,left,top,w,h);
  ctx.strokeStyle='rgba(255,0,160,0.22)';
  ctx.lineWidth=9*devicePixelRatio;
  ctx.shadowBlur=22*devicePixelRatio;
  ctx.shadowColor='rgba(255,0,180,1.0)';
  ctx.stroke();

  /* MID GLOW */
  buildPath(current,left,top,w,h);
  ctx.strokeStyle='rgba(200,60,255,0.60)';
  ctx.lineWidth=2.5*devicePixelRatio;
  ctx.shadowBlur=10*devicePixelRatio;
  ctx.shadowColor='rgba(180,0,255,0.9)';
  ctx.stroke();

  /* CORE */
  buildPath(current,left,top,w,h);
  ctx.strokeStyle='rgba(255,255,255,1.0)';
  ctx.lineWidth=1.2*devicePixelRatio;
  ctx.shadowBlur=7*devicePixelRatio;
  ctx.shadowColor='rgba(255,255,255,1.0)';
  ctx.stroke();

  /* PEAK HOLD */
  buildPath(peaks,left,top,w,h);
  ctx.strokeStyle='rgba(0,255,210,0.60)';
  ctx.lineWidth=devicePixelRatio;
  ctx.shadowBlur=5*devicePixelRatio;
  ctx.shadowColor='rgba(0,255,200,0.8)';
  ctx.stroke();

  ctx.restore();
  ctx.shadowBlur=0;
}
requestAnimationFrame(loop);
</script></body></html>
"""

    def _build_center(self):
        container = QtWidgets.QWidget()
        vlay = QtWidgets.QVBoxLayout(container)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(2)

        # ---- HTML5 Canvas spectrum ----
        self.spec_web = QtWebEngineWidgets.QWebEngineView()
        self._spec_web_ready = False
        self.spec_web.loadFinished.connect(lambda ok: setattr(self, '_spec_web_ready', ok))
        # NOTE: setHtml is deferred to switch_to_monitor() so the JS engine
        # starts while the widget is actually visible (page 1 of stack).
        self.spec_web.setMinimumHeight(200)
        self.tune_line_spec = None

        # ---- Waterfall (pyqtgraph) ----
        wf_widget = pg.GraphicsLayoutWidget()
        wf_widget.ci.setSpacing(0)
        wf_widget.setBackground('#03000a')
        self.wf_plot = wf_widget.addPlot(row=0, col=0)
        self.wf_plot.setLabel('bottom', 'Frequency', units='Hz',
                              color='rgba(255,255,255,0.35)', size='9pt')
        self.wf_plot.setLabel('left', 'Time',
                              color='rgba(255,255,255,0.35)', size='9pt')
        self.wf_plot.getAxis('bottom').setTextPen(pg.mkPen((255, 255, 255, 90)))
        self.wf_plot.getAxis('left').setTextPen(pg.mkPen((255, 255, 255, 90)))
        self.wf_plot.getAxis('bottom').setPen(pg.mkPen((255, 255, 255, 20)))
        self.wf_plot.getAxis('left').setPen(pg.mkPen((255, 255, 255, 20)))
        self.wf_plot.setMouseEnabled(x=True, y=False)
        self.wf_img = pg.ImageItem(axisOrder='row-major')
        self.wf_plot.addItem(self.wf_img)
        self.wf_lut = self._build_lut()
        self.wf_img.setLookupTable(self.wf_lut)
        self.wf_img.setLevels((-68, -28))
        self.tune_line_wf = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen('#ffcc33', width=1.5,
                         style=QtCore.Qt.PenStyle.DashLine))
        self.wf_plot.addItem(self.tune_line_wf)

        vlay.addWidget(self.spec_web, 2)
        vlay.addWidget(wf_widget, 3)
        return container

    # ------------------------------------------------------------------
    def _build_right_sidebar(self):
        box = QtWidgets.QGroupBox("DISPLAY")
        box.setMaximumWidth(210)
        lay = QtWidgets.QFormLayout(box)

        def _row(slider, lbl):
            w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            h.addWidget(slider, 1); h.addWidget(lbl)
            lbl.setFixedWidth(40)
            lbl.setStyleSheet("color:rgba(255,255,255,0.55); font-size:11px; qproperty-alignment:AlignRight;")
            return w

        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(5, 100)
        self.zoom_slider.setValue(100)
        self.zoom_lbl = QtWidgets.QLabel("100%")
        self.zoom_slider.valueChanged.connect(self._apply_zoom)
        self.zoom_slider.valueChanged.connect(lambda v: self.zoom_lbl.setText(f"{v}%"))
        lay.addRow("Zoom:", _row(self.zoom_slider, self.zoom_lbl))

        self.range_min = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.range_min.setRange(-140, 0); self.range_min.setValue(-100)
        self.range_min_lbl = QtWidgets.QLabel("-100")
        self.range_min.valueChanged.connect(self._apply_range)
        self.range_min.valueChanged.connect(lambda v: self.range_min_lbl.setText(str(v)))

        self.range_max = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.range_max.setRange(-140, 0); self.range_max.setValue(-10)
        self.range_max_lbl = QtWidgets.QLabel("-10")
        self.range_max.valueChanged.connect(self._apply_range)
        self.range_max.valueChanged.connect(lambda v: self.range_max_lbl.setText(str(v)))

        lay.addRow("dB min:", _row(self.range_min, self.range_min_lbl))
        lay.addRow("dB max:", _row(self.range_max, self.range_max_lbl))

        self.offset_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.offset_slider.setRange(-60, 60)
        self.offset_slider.setValue(7)
        self.offset_lbl = QtWidgets.QLabel("7")
        self.offset_slider.valueChanged.connect(self._apply_offset)
        self.offset_slider.valueChanged.connect(lambda v: self.offset_lbl.setText(str(v)))
        lay.addRow("WF offset:", _row(self.offset_slider, self.offset_lbl))

        lay.addItem(QtWidgets.QSpacerItem(
            10, 10, QtWidgets.QSizePolicy.Policy.Minimum,
            QtWidgets.QSizePolicy.Policy.Expanding))
        return box

    # ------------------------------------------------------------------
    @staticmethod
    def _build_lut():
        # Match spectrum graph palette: black -> deep purple -> magenta -> pink -> white
        stops = np.array([
            [0,   0,   0],
            [20,   0,  40],
            [80,   0, 120],
            [160,  0, 180],
            [220, 80, 200],
            [255, 180, 255],
        ], dtype=np.float32)
        xs = np.linspace(0, 1, len(stops))
        idx = np.linspace(0, 1, 256)
        lut = np.empty((256, 3), dtype=np.uint8)
        for c in range(3):
            lut[:, c] = np.interp(idx, xs, stops[:, c]).astype(np.uint8)
        return lut

    # ==================================================================
    # Settings + slot handlers
    # ==================================================================
    def _apply_initial_settings(self):
        self.worker.set_freq(self.freq_spin.value() * 1e6)
        self.worker.set_sample_rate(self.sr_combo.currentData())
        self.worker.set_bandwidth(self.bw_slider.value() * 1e3)
        self._on_gain_changed(self.gain_slider.value())
        self._on_vol_changed(self.vol_slider.value())
        self._apply_range()

    def _on_power_toggled(self, on: bool):
        if on:
            self.btn_power.setText("  Stop")
            span = 20 if self.btn_span.isChecked() else 2
            if span >= 18:
                # ---- 20 MHz panoramic: SweepWorker, no audio ----
                self.status_lbl.setText("Panoramic sweep 88–108 MHz")
                self.freq_spin.setEnabled(False)
                self.audio.stop()
                if self.worker.isRunning():
                    self.worker.stop()
                    self.worker.wait(3000)
                # Small USB settle delay before new device open
                import time as _t; _t.sleep(0.5)
                self.sweep_worker.stop()
                if self.sweep_worker.isRunning():
                    self.sweep_worker.wait(3000)
                self.sweep_worker = self._create_sweep_worker(span)
                try:
                    self.worker.fft_ready.disconnect(self._on_fft)
                except Exception:
                    pass
                try:
                    self.sweep_worker.fft_ready.disconnect(self._on_fft)
                except Exception:
                    pass
                self.sweep_worker.fft_ready.connect(self._on_fft, QtCore.Qt.ConnectionType.QueuedConnection)
                print(f"[power] Starting sweep_worker  span={span}MHz")
                self.sweep_worker.start()
            else:
                # ---- 2 MHz narrow: SdrWorker for audio + FFT ----
                self.status_lbl.setText(f"Span {span} MHz")
                self.freq_spin.setEnabled(True)
                self.sweep_worker.stop()
                if self.sweep_worker.isRunning():
                    self.sweep_worker.wait(3000)
                try:
                    self.sweep_worker.fft_ready.disconnect(self._on_fft)
                except Exception:
                    pass
                try:
                    self.worker.fft_ready.disconnect(self._on_fft)
                except Exception:
                    pass
                self.worker.fft_ready.connect(self._on_fft, QtCore.Qt.ConnectionType.QueuedConnection)
                if self.worker.isRunning():
                    # Already running from dashboard — just retune
                    self.worker.set_freq(self.freq_spin.value() * 1e6)
                    self.worker.set_sample_rate(self.sr_combo.currentData())
                    self.worker.set_bandwidth(self.bw_slider.value() * 1e3)
                    self.worker._dirty = True
                    print(f"[power] Retuning existing SdrWorker  fc={self.freq_spin.value()}MHz")
                else:
                    self.worker.set_freq(self.freq_spin.value() * 1e6)
                    self.worker.set_sample_rate(self.sr_combo.currentData())
                    self.worker.set_bandwidth(self.bw_slider.value() * 1e3)
                    print(f"[power] Starting SdrWorker  fc={self.freq_spin.value()}MHz")
                    self.worker.start()
                self.audio.volume = 0.0
                self.audio.start()
                QtCore.QTimer.singleShot(800, lambda: setattr(self.audio, 'volume', 0.5))
        else:
            self.btn_power.setText("  Start")
            self.status_lbl.setText("Stopping...")
            if self.worker.isRunning():
                self.worker.stop()
                self.worker.wait(3000)
            self.sweep_worker.stop()
            if self.sweep_worker.isRunning():
                self.sweep_worker.wait(3000)
            self.audio.stop()
            # Reconnect SdrWorker for dashboard
            try:
                self.worker.fft_ready.disconnect(self._on_fft)
            except Exception:
                pass
            try:
                self.sweep_worker.fft_ready.disconnect(self._on_fft)
            except Exception:
                pass
            self.worker.fft_ready.connect(self._on_fft, QtCore.Qt.ConnectionType.QueuedConnection)
            self.status_lbl.setText("Idle")  # noqa

    def _create_sweep_worker(self, span_mhz: int):
        """Create a SweepWorker with the desired span (2–20 MHz)."""
        gain = self.gain_slider.value()
        gain = 'auto' if gain < 0 else float(gain)
        if span_mhz >= 18:
            return SweepWorker(gain=gain, use_simulated=RtlSdr is None)
        else:
            center_mhz = self.freq_spin.value()
            start_hz = (center_mhz - span_mhz / 2) * 1e6
            stop_hz = (center_mhz + span_mhz / 2) * 1e6
            return SweepWorker(
                start_hz=start_hz,
                stop_hz=stop_hz,
                gain=gain,
                use_simulated=RtlSdr is None,
            )

    def _on_span_toggled(self, on: bool):
        """Switch between 20 MHz (checked) and 2 MHz (unchecked) sweep span."""
        span = 20 if on else 2
        self.btn_span.setText(f"{span} MHz")
        if not self.btn_power.isChecked():
            return
        if span >= 18:
            # ---- Switch to 20 MHz panoramic ----
            if self.worker.isRunning():
                self.worker.stop()
                self.worker.wait(3000)
            self.audio.stop()
            # USB settle before new device open
            import time as _t; _t.sleep(0.5)
            self.sweep_worker.stop()
            if self.sweep_worker.isRunning():
                self.sweep_worker.wait(3000)
            self.sweep_worker = self._create_sweep_worker(span)
            try:
                self.worker.fft_ready.disconnect(self._on_fft)
            except Exception:
                pass
            try:
                self.sweep_worker.fft_ready.disconnect(self._on_fft)
            except Exception:
                pass
            self.sweep_worker.fft_ready.connect(self._on_fft, QtCore.Qt.ConnectionType.QueuedConnection)
            self.status_lbl.setText("Panoramic sweep 88–108 MHz")
            print(f"[span_toggle] Starting sweep_worker  span={span}MHz")
            self.sweep_worker.start()
        else:
            # ---- Switch to 2 MHz narrow with audio ----
            self.sweep_worker.stop()
            if self.sweep_worker.isRunning():
                self.sweep_worker.wait(3000)
            try:
                self.sweep_worker.fft_ready.disconnect(self._on_fft)
            except Exception:
                pass
            try:
                self.worker.fft_ready.disconnect(self._on_fft)
            except Exception:
                pass
            self.worker.fft_ready.connect(self._on_fft, QtCore.Qt.ConnectionType.QueuedConnection)
            if self.worker.isRunning():
                self.worker.set_freq(self.freq_spin.value() * 1e6)
                self.worker.set_sample_rate(self.sr_combo.currentData())
                self.worker.set_bandwidth(self.bw_slider.value() * 1e3)
                self.worker._dirty = True
                print(f"[span_toggle] Retuning existing SdrWorker  fc={self.freq_spin.value()}MHz")
            else:
                self.worker.set_freq(self.freq_spin.value() * 1e6)
                self.worker.set_sample_rate(self.sr_combo.currentData())
                self.worker.set_bandwidth(self.bw_slider.value() * 1e3)
                print(f"[span_toggle] Starting SdrWorker  fc={self.freq_spin.value()}MHz")
                self.worker.start()
            self.audio.volume = 0.0
            self.audio.start()
            QtCore.QTimer.singleShot(800, lambda: setattr(self.audio, 'volume', 0.5))
            self.status_lbl.setText(f"Span {span} MHz")

    def _on_log_onoff_changed(self, on: bool):
        """Stub — logging UI removed; controlled from React map UI."""
        pass

    def _on_log_mode_changed(self, panoramic: bool):
        """Stub — logging UI removed; controlled from React map UI."""
        pass

    def _on_bw_changed(self, khz: int):
        self.bw_lbl.setText(f"{khz} kHz")
        self.worker.set_bandwidth(khz * 1e3)

    def _on_gain_changed(self, v: int):
        if v < 0:
            self.gain_lbl.setText("AUTO")
            self.worker.set_gain('auto')
            self.sweep_worker.set_gain('auto')
        else:
            self.gain_lbl.setText(f"{v} dB")
            self.worker.set_gain(float(v))
            self.sweep_worker.set_gain(float(v))

    def _on_vol_changed(self, v: int):
        self.vol_lbl.setText(f"{v}%")
        self.audio.volume = v / 100.0

    def _monitor_visible(self):
        return (hasattr(self, 'stack') and
                self.stack.currentIndex() == 1)

    def _apply_zoom(self):
        fc = getattr(self, '_fc', None)
        fs = getattr(self, '_fs', None)
        if not fc or not fs:
            return
        frac = self.zoom_slider.value() / 100.0
        half = (fs / 2.0) * frac
        centre = getattr(self, '_tuned_fc', None) or fc
        lo_edge = fc - fs / 2.0
        hi_edge = fc + fs / 2.0
        x0 = max(centre - half, lo_edge)
        x1 = min(centre + half, hi_edge)
        self.wf_plot.setXRange(x0, x1, padding=0)
        if self._spec_web_ready and self._monitor_visible():
            try:
                self.spec_web.page().runJavaScript(
                    f'setFreqRange({x0},{x1},{centre})')
            except Exception:
                pass

    def _apply_range(self):
        lo = self.range_min.value()
        hi = self.range_max.value()
        if hi <= lo:
            hi = lo + 1
        self._spec_db_lo = lo
        self._spec_db_hi = hi
        if self._spec_web_ready and self._monitor_visible():
            try:
                self.spec_web.page().runJavaScript(f'setRange({lo},{hi})')
            except Exception:
                pass
        off = self.offset_slider.value() if hasattr(self, 'offset_slider') else 0
        self.wf_img.setLevels((lo + off, hi + off))

    def _apply_offset(self):
        self._apply_range()

    # ==================================================================
    # Signal slots from worker
    # ==================================================================
    @QtCore.pyqtSlot(np.ndarray, float, float, float)
    def _on_fft(self, spec_db: np.ndarray, fc: float, fs: float,
                tuned_fc: float):
        # Smooth (EMA) for nicer display
        if self.spec_smoothed is None or self.spec_smoothed.size != spec_db.size:
            self.spec_smoothed = spec_db.copy()
        else:
            a = self.spec_alpha
            self.spec_smoothed = (1 - a) * self.spec_smoothed + a * spec_db

        # X axis: absolute Hz
        n = spec_db.size
        freqs = fc + (np.arange(n) - n / 2) * (fs / n)
        # Downsample to 512 bins for JS payload
        ds = self.spec_smoothed[::max(1, len(self.spec_smoothed)//512)][:512]
        # Only re-push range/zoom when fc/fs/tuned_fc actually changes
        fc_changed = (fc != self._fc or fs != self._fs
                      or tuned_fc != getattr(self, '_tuned_fc', None))
        if fc_changed:
            self._fc, self._fs = fc, fs
            self._tuned_fc = tuned_fc
            self._apply_zoom()
            print(f"[_on_fft] span_changed  n={n} fc={fc/1e6:.2f}MHz fs={fs/1e6:.1f}MHz  "
                  f"ds_len={len(ds)}  x_range={freqs[0]/1e6:.2f}-{freqs[-1]/1e6:.2f}MHz")

        if self._spec_web_ready and self._monitor_visible():
            lo = getattr(self, '_spec_db_lo', self.range_min.value())
            hi = getattr(self, '_spec_db_hi', self.range_max.value())
            _frac = self.zoom_slider.value() / 100.0
            _half = (fs / 2.0) * _frac
            _ctr  = getattr(self, '_tuned_fc', fc) or fc
            _x0   = max(_ctr - _half, fc - fs / 2.0)
            _x1   = min(_ctr + _half, fc + fs / 2.0)
            _n_ds = len(ds)
            _full_lo = fc - fs / 2.0
            _full_hi = fc + fs / 2.0
            _span = _full_hi - _full_lo
            _i0 = max(0, int((_x0 - _full_lo) / _span * _n_ds))
            _i1 = min(_n_ds, int((_x1 - _full_lo) / _span * _n_ds) + 1)
            ds_vis = ds[_i0:_i1] if _i1 > _i0 else ds
            try:
                page = self.spec_web.page()
                page.runJavaScript(f"setRange({lo},{hi})")
                page.runJavaScript(f"setFreqRange({_x0},{_x1},{_ctr})")
                page.runJavaScript(f"updateSpectrum({json.dumps(ds_vis.tolist())},{_x0 + (_x1-_x0)/2},{_x1-_x0})")
            except Exception:
                pass

        # Ring-buffer waterfall — write pointer avoids np.roll allocation
        if self.waterfall_buf.shape[1] != n:
            self.waterfall_buf = np.full((self.WATERFALL_ROWS, n), -120.0,
                                         dtype=np.float32)
            self._wf_row = 0
        self.waterfall_buf[self._wf_row % self.WATERFALL_ROWS, :] = self.spec_smoothed
        self._wf_row += 1

        # Throttle waterfall redraw to ~10 fps — most expensive GUI op
        now_wf = time.monotonic()
        if now_wf - self._last_wf_draw >= 0.10:
            self._last_wf_draw = now_wf
            r = self._wf_row % self.WATERFALL_ROWS
            display = np.concatenate((self.waterfall_buf[r:], self.waterfall_buf[:r]), axis=0)
            self.wf_img.setImage(display, autoLevels=False)
            self.wf_img.setRect(QtCore.QRectF(
                freqs[0], 0, freqs[-1] - freqs[0], self.WATERFALL_ROWS))

        # Position the tuning marker on the waterfall
        self.tune_line_wf.setPos(tuned_fc)

    @QtCore.pyqtSlot(str)
    def _on_error(self, msg: str):
        self.status_lbl.setText(f"Error: {msg}")
        # Auto-stop on hard error
        if self.btn_power.isChecked():
            self.btn_power.setChecked(False)

    # ------------------------------------------------------------------
    def closeEvent(self, e: QtGui.QCloseEvent):
        try:
            self.worker.stop()
            self.worker.wait(1500)
            self.sweep_worker.stop()
            self.sweep_worker.wait(1500)
            self.audio.stop()
        except Exception:
            pass
        super().closeEvent(e)


# ---------------------------------------------------------------------------
# Backend bridge: an in-process FastAPI server (uvicorn on a daemon thread)
# that serves the React 3D map embedded in the Qt window's QWebEngineView,
# streams peak_intensity over a WebSocket, and exposes /switch/spectrum_core to flip
# the QStackedWidget from the landing page to the FM monitor in-place.
# ---------------------------------------------------------------------------
import asyncio as _asyncio
import json as _json
import os as _os
import random as _random
import numpy as _np

_win_ref: list = [None]  # filled after MainWindow is created; read by /ws/sdr

# FM scanner state — at module level so MainWindow can pause/resume it
_scan_state: dict = {
    "results": {},
    "playing": False,
    "playing_idx": None,
    "scan_idx": 0,
    "active": True,      # False when monitor page is open
    "log_enabled": True,   # logging on by default
    "log_active":  False,  # 2 MHz sweep logger off — 20 MHz panoramic is default
    "panoramic_log": True, # default = panoramic peak logger
    "log_running": False,
    "log_path":    "",
    "log_file":    None,
    "log_writer":  None,
    "log_scan_idx": 0,
    "log_stop":    False,
    "panoramic_peaks": [],  # latest real SDR peaks for dashboard
}


class _UiBridge(QtCore.QObject):
    """Lives on the Qt GUI thread; FastAPI handlers (background threads)
    emit `switch_requested` and Qt marshals it to the main thread."""
    switch_requested = QtCore.pyqtSignal()
    map_requested = QtCore.pyqtSignal()
    audio_mute_requested = QtCore.pyqtSignal()
    audio_unmute_requested = QtCore.pyqtSignal()
    radio_start_requested = QtCore.pyqtSignal()
    radio_stop_requested = QtCore.pyqtSignal()
    tune_requested = QtCore.pyqtSignal(float)  # freq in Hz
    scan_worker_start = QtCore.pyqtSignal()    # start SDR worker for scanning
    stop_and_scan = QtCore.pyqtSignal()          # stop playback, re-silence, resume scan


def _build_landing_app(bridge: "_UiBridge"):
    from fastapi import FastAPI, WebSocket
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    base_dir = _os.path.dirname(_os.path.abspath(__file__))
    dist_dir = _os.path.join(base_dir, "sdr-dashboard", "dist")
    assets_dir = _os.path.join(dist_dir, "assets")
    index_path = _os.path.join(dist_dir, "index.html")

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

    # Full FM band in 100 kHz steps for CSV logging (matches SDR# band scanner format)
    LOG_FREQS_HZ = [87.0e6 + i * 100e3 for i in range(211)]   # 87.0–108.0 MHz

    # FM station centre frequencies in Hz (must match STATIONS in SigintDashboard.jsx)
    FM_FREQS_HZ = [
        88.7e6, 90.0e6, 90.7e6, 92.5e6, 93.0e6, 93.3e6, 97.7e6,
        100.0e6, 100.5e6, 101.0e6, 101.1e6, 102.8e6, 103.0e6, 103.3e6,
        104.0e6, 105.0e6, 106.0e6, 106.3e6, 107.0e6, 107.7e6,
    ]

    # Use the module-level _scan_state

    def _measure_raw_dbm(win, target_hz: float, bw: float = 100e3):
        """Measure real dBm and SNR for a specific frequency from the current FFT.
        Returns (power_dbm, snr_db) or None if frequency is out of window."""
        try:
            buf   = win.waterfall_buf
            fc    = win._fc
            fs    = win._fs
            n     = buf.shape[1]
            row   = buf[(win._wf_row - 1) % win.WATERFALL_ROWS]
            freqs = fc + (_np.arange(n) - n / 2) * (fs / n)
            f_lo, f_hi = freqs[0], freqs[-1]
            if target_hz < f_lo or target_hz > f_hi:
                return None
            mask = (freqs >= target_hz - bw / 2) & (freqs <= target_hz + bw / 2)
            if mask.sum() < 4:
                return None
            noise_floor = float(_np.percentile(row, 10))
            power_dbm   = float(row[mask].mean())
            snr_db      = round(power_dbm - noise_floor, 1)
            return round(power_dbm, 1), snr_db
        except Exception:
            return None

    # Build a lookup: station_hz (int) -> station_index for fast matching
    _STATION_HZ = {int(round(f)): i for i, f in enumerate(FM_FREQS_HZ)}

    def _update_station_results(win, target_hz: float) -> None:
        """If target_hz is within 50 kHz of a known station, measure real
        dBm + SNR and update ss['results'][idx] = {pwr, snr, ts}."""
        MATCH_HZ = 50e3
        for station_hz, idx in _STATION_HZ.items():
            if abs(target_hz - station_hz) <= MATCH_HZ:
                r = _measure_raw_dbm(win, station_hz)
                if r is not None:
                    pwr, snr = r
                    import time as _t2
                    _scan_state["results"][str(idx)] = {
                        "pwr": pwr, "snr": snr, "ts": _t2.time()
                    }
                break

    async def _fm_scanner():
        """Background coroutine: cycles through FM freqs every 3 s.
        When logging is active, switches to full 100 kHz grid and writes CSV.
        Pauses while a station is playing OR while the monitor page is open."""
        import time as _time
        import datetime as _dt_sc
        DWELL  = 3.0   # seconds per station (normal scan)
        DWELL_LOG = 1.2  # fast dwell when logging (RTL-SDR settles in ~400 ms)
        SETTLE = DWELL * 0.75   # settle time before measuring
        _worker_start_emitted = False
        while True:
            try:
                ss = _scan_state
                win = _win_ref[0]
                # Pause if: no window yet, user playing a station, or monitor is open
                if win is None or ss["playing"] or not ss["active"]:
                    _worker_start_emitted = False
                    await _asyncio.sleep(0.5)
                    continue

                # Ensure worker is running
                if not getattr(win.worker, '_running', False):
                    if not _worker_start_emitted:
                        bridge.scan_worker_start.emit()
                        _worker_start_emitted = True
                    await _asyncio.sleep(1.0)
                    continue

                # Scanning always uses the full log grid so the UI bars
                # are updated from real measurements, not a short station list.
                # When logging is paused/stopped, still sweep but don't write.
                logging = ss.get("log_active", False)
                freq_list = LOG_FREQS_HZ
                dwell = DWELL_LOG
                idx = ss.get("log_scan_idx", 0) % len(freq_list)

                target_hz = freq_list[idx]

                # Retune
                try:
                    win.worker.set_freq(target_hz)
                except Exception:
                    await _asyncio.sleep(1.0)
                    continue

                # Settle
                await _asyncio.sleep(dwell * 0.75)

                # --- Update station status from real dBm/SNR measurement ---
                _update_station_results(win, target_hz)

                # --- Write CSV log row ---
                if logging and ss.get("log_writer") is not None:
                    actual_hz = target_hz
                    r = _measure_raw_dbm(win, actual_hz)
                    if r is not None:
                        pwr, snr = r
                        ts_str = _dt_sc.datetime.now().strftime("%d.%m.%Y %H:%M")
                        try:
                            ss["log_writer"].writerow([ts_str, int(actual_hz), pwr, snr])
                            ss["log_file"].flush()
                        except Exception:
                            pass
                    # Advance log index; when sweep completes, start a new file
                    next_log_idx = idx + 1
                    if next_log_idx >= len(freq_list):
                        print(f"[logger] Done: {ss.get('log_path', '')}")
                        try:
                            ss["log_file"].close()
                        except Exception:
                            pass
                        ss["log_file"]   = None
                        ss["log_writer"] = None
                        if not ss.get("log_stop"):
                            _open_new_log()   # immediately start next sweep file
                        else:
                            ss["log_active"]  = False
                            ss["log_running"] = False
                    else:
                        ss["log_scan_idx"] = next_log_idx
                else:
                    # logging off but still advance position so UI keeps updating
                    ss["log_scan_idx"] = (idx + 1) % len(freq_list)

                await _asyncio.sleep(dwell * 0.25)

            except Exception as exc:
                print(f"[scanner] error: {exc}")
                await _asyncio.sleep(1.0)

    # Kick off scanner using modern lifespan pattern
    from contextlib import asynccontextmanager as _acm
    import os as _os_log, csv as _csv_boot, datetime as _dt_boot

    def _open_new_log():
        """Open a fresh log file in C:\projects\CGrep-v3\Logs and arm the scan state."""
        log_dir = r"C:\projects\CGrep-v3\Logs"
        try:
            _os_log.makedirs(log_dir, exist_ok=True)
        except Exception:
            log_dir = _os_log.path.dirname(_os_log.path.abspath(__file__))
        ts = _dt_boot.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = _os_log.path.join(log_dir, f"fm_log_{ts}.csv")
        try:
            fh = open(path, "w", newline="", encoding="utf-8")
            wr = _csv_boot.writer(fh)
            wr.writerow(["Date Stamp", " VFO Freq (Hz)", " Power (dBm)", " SNR (dB)"])
        except Exception as exc:
            print(f"[logger] Cannot open log file: {exc}")
            return
        _scan_state["log_active"]   = True
        _scan_state["log_running"]  = True
        _scan_state["log_stop"]     = False
        _scan_state["log_path"]     = path
        _scan_state["log_file"]     = fh
        _scan_state["log_writer"]   = wr
        _scan_state["log_scan_idx"] = 0
        print(f"[logger] Logging to: {path}  ({len(LOG_FREQS_HZ)} freqs)")

    # store reference so MainWindow can call it from any scope
    _scan_state["open_new_log"] = _open_new_log

    import numpy as _np
    from scipy import signal as _signal

    # ------------------------------------------------------------------
    # Background panoramic sweep logger — runs in the FastAPI event loop,
    # independent of the Qt spectrum monitor.  Uses a thread executor so
    # the blocking RTL-SDR reads don't stall the async loop.
    # ------------------------------------------------------------------
    async def _panoramic_sweep_logger():
        import concurrent.futures as _cf
        _executor = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pano_sweep")

        LOG_INTERVAL = 2.0   # seconds between CSV writes
        _pano_logger = SpectrumPeakLogger()
        _pano_logger_dash = SpectrumPeakLogger(prefix="sweepdash")
        last_log_time = 0.0

        print("[pano_logger] Background panoramic sweep logger started")

        # Re-use one engine (keeps SDR open) for much faster sweeps
        _engine = None

        def _do_sweep():
            """Run one full 88-108 MHz sweep using SweepEngine."""
            nonlocal _engine
            try:
                if _engine is None:
                    _engine = SweepEngine(
                        start_hz=88e6,
                        stop_hz=108e6,
                        gain=40,
                        use_simulated=False,
                    )
                _engine.reset_cancel()
                _scan_state["pano_engine"] = _engine
                spectrum, freqs = _engine.sweep_once()
                pf, pp = _engine.find_peaks(spectrum)
                return spectrum, freqs, pf, pp
            except Exception as exc:
                print(f"[pano_logger] Sweep error: {exc}")
                # Reset engine on error so next iteration re-opens SDR
                try:
                    if _engine:
                        _engine._close_sdr()
                except Exception:
                    pass
                _engine = None
                return None, None, None, None

        loop = _asyncio.get_event_loop()
        while True:
            try:
                if not _scan_state.get("panoramic_log", True):
                    await _asyncio.sleep(1.0)
                    continue

                if RtlSdr is None:
                    await _asyncio.sleep(5.0)
                    continue

                # Halt if the spectrum monitor page is open OR worker is running
                # to avoid any USB device conflicts.
                win_obj = _win_ref[0]
                monitor_open = (
                    win_obj is not None and
                    hasattr(win_obj, 'stack') and
                    win_obj.stack.currentIndex() == 1
                )
                worker_running = (
                    win_obj is not None and
                    hasattr(win_obj, 'worker') and
                    win_obj.worker._running
                )
                if monitor_open or worker_running:
                    # Release SDR while monitor is active
                    if _engine:
                        try:
                            _engine._close_sdr()
                        except Exception:
                            pass
                        _engine = None
                    await _asyncio.sleep(1.0)
                    continue

                spectrum, freqs, pf, pp = await loop.run_in_executor(_executor, _do_sweep)
                if spectrum is None:
                    await _asyncio.sleep(5.0)
                    continue

                # Store latest peaks for dashboard — filter to known stations only
                _scan_state["panoramic_peaks"] = [
                    {"freq": float(f), "dbfs": float(p)} for f, p in zip(pf, pp)
                    if any(abs(f - known) < 0.15 for known in KNOWN_STATIONS_MHZ)
                ] if pf.size > 0 else []

                now = _asyncio.get_event_loop().time()
                if now - last_log_time >= LOG_INTERVAL:
                    if pf.size > 0:
                        _pano_logger.write_peaks(pf, pp)
                        print(f"[pano_logger] {len(pf)} peaks → "
                              f"{', '.join(f'{f:.1f}' for f in pf)} MHz")
                    # Log dashboard-specific stations only
                    dash_peaks = _scan_state["panoramic_peaks"]
                    if dash_peaks:
                        _pano_logger_dash.write_dashboard(dash_peaks)
                        print(f"[sweepdash_logger] {len(dash_peaks)} dashboard peaks logged")
                    last_log_time = now

                # Short pause between sweeps — SDR stays open so no need for long wait
                await _asyncio.sleep(0.2)

            except Exception as exc:
                print(f"[pano_logger] Error: {exc}")
                await _asyncio.sleep(3.0)

    @_acm
    async def _lifespan(application):
        # 20 MHz panoramic mode is the default — don't start the 2 MHz logger on boot.
        # _open_new_log() is called only when the user switches to 2 MHz log mode.
        _asyncio.ensure_future(_fm_scanner())
        _asyncio.ensure_future(_panoramic_sweep_logger())
        yield

    app.router.lifespan_context = _lifespan

    @app.websocket("/ws/sdr")
    async def sdr_stream(websocket: WebSocket):
        await websocket.accept()
        print("[landing] React 3D map connected")
        current_intensity = 0.2
        try:
            while True:
                step = _random.uniform(-0.15, 0.15)
                current_intensity = max(0.0, min(1.0, current_intensity + step))
                payload: dict = {
                    "peak_intensity": current_intensity,
                    "station_scan":   _scan_state["results"],
                    "playing_idx":    _scan_state["playing_idx"],
                    "log_running":    _scan_state.get("log_running", False),
                    "log_path":       _scan_state.get("log_path", ""),
                    "panoramic_peaks": _scan_state.get("panoramic_peaks", []),
                }
                # Attach live audio quality + noise_clean state
                try:
                    if _scan_state["playing"] and _win_ref[0] is not None:
                        aud = _win_ref[0].audio
                        payload["audio_quality"] = aud.quality
                        payload["audio_state"]   = aud.state
                except Exception:
                    pass
                await websocket.send_json(payload)
                await _asyncio.sleep(0.25)
        except Exception as e:
            print(f"[landing] WebSocket closed: {e}")

    @app.websocket("/ws/control")
    async def control_stream(websocket: WebSocket):
        await websocket.accept()
        print("[landing] Control WebSocket connected")
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = _json.loads(data)
                    command = msg.get("command")
                    if command == "radio_start":
                        bridge.radio_start_requested.emit()
                    elif command == "radio_stop":
                        bridge.radio_stop_requested.emit()
                    elif command == "audio_mute":
                        bridge.audio_mute_requested.emit()
                    elif command == "audio_unmute":
                        bridge.audio_unmute_requested.emit()
                except Exception as e:
                    print(f"[landing] Control command error: {e}")
        except Exception as e:
            print(f"[landing] Control WebSocket closed: {e}")

    if _os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    async def serve_root():
        if _os.path.isfile(index_path):
            return FileResponse(index_path)
        return {"error": f"React build not found at {index_path}"}

    # Serve any file that lives in dist root (fonts, pngs, etc.)
    @app.get("/{filename:path}")
    async def serve_dist_file(filename: str):
        # Skip API-like paths
        if filename.startswith(("radio/", "dashboard/", "audio/", "switch/", "open-", "ws")):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        candidate = _os.path.join(dist_dir, filename)
        if _os.path.isfile(candidate):
            return FileResponse(candidate)
        # SPA fallback → index.html
        if _os.path.isfile(index_path):
            return FileResponse(index_path)
        return {"error": f"React build not found at {index_path}"}

    @app.post("/switch/spectrum_core")
    async def switch():
        # Emitting from this background thread is safe because the signal
        # is delivered to the Qt main thread via the default QueuedConnection.
        bridge.switch_requested.emit()
        return {"ok": True}

    @app.post("/switch/map")
    async def switch_map():
        bridge.map_requested.emit()
        return {"ok": True}

    @app.post("/audio/mute")
    async def audio_mute():
        bridge.audio_mute_requested.emit()
        return {"ok": True}

    @app.post("/audio/unmute")
    async def audio_unmute():
        bridge.audio_unmute_requested.emit()
        return {"ok": True}

    @app.post("/radio/start")
    async def radio_start():
        bridge.radio_start_requested.emit()
        return {"ok": True}

    @app.post("/radio/stop")
    async def radio_stop():
        bridge.radio_stop_requested.emit()
        return {"ok": True}

    from fastapi import Body as _Body
    @app.post("/radio/tune")
    async def radio_tune(payload: dict = _Body(...)):
        """Tune to a specific FM station by index. Suspends scanner."""
        idx  = int(payload.get("idx", 0))
        hz   = FM_FREQS_HZ[idx]
        _scan_state["playing"]     = True
        _scan_state["playing_idx"] = idx
        bridge.tune_requested.emit(hz)
        return {"ok": True, "freq_hz": hz}

    @app.post("/radio/untune")
    async def radio_untuned():
        """Stop playback, silence audio, resume scanner."""
        _scan_state["playing"]     = False
        _scan_state["playing_idx"] = None
        bridge.stop_and_scan.emit()
        return {"ok": True}

    @app.post("/radio/log")
    async def radio_log(payload: dict = _Body(...)):
        """Start or stop a full FM band CSV log sweep.
        POST {"action": "start", "path": "C:/logs/scan.csv"} to begin.
        POST {"action": "stop"} to stop."""
        import os as _os2, datetime as _dt
        action = payload.get("action", "start")
        if action == "stop":
            _scan_state["log_active"]  = False
            _scan_state["log_running"] = False
            try:
                if _scan_state.get("log_file"):
                    _scan_state["log_file"].close()
                    _scan_state["log_file"]   = None
                    _scan_state["log_writer"] = None
            except Exception:
                pass
            return {"ok": True, "status": "stopping"}

        _open_new_log()
        return {"ok": True, "status": "started", "path": _scan_state["log_path"]}

    @app.post("/radio/log-mode")
    async def radio_log_mode(payload: dict = _Body(...)):
        """Switch logging mode: POST {"mode": "2mhz"} or {"mode": "20mhz"}."""
        mode = payload.get("mode", "20mhz")
        if mode == "20mhz":
            _scan_state["panoramic_log"] = True
            _scan_state["log_active"]    = False
            _scan_state["log_running"]   = False
            _scan_state["log_stop"]      = True
            try:
                if _scan_state.get("log_file"):
                    _scan_state["log_file"].close()
                _scan_state["log_file"]   = None
                _scan_state["log_writer"] = None
            except Exception:
                pass
            return {"ok": True, "mode": "20mhz"}
        else:
            _scan_state["panoramic_log"] = False
            _scan_state["log_stop"]      = False
            _open_new_log()
            return {"ok": True, "mode": "2mhz", "path": _scan_state.get("log_path", "")}

    @app.get("/open-logs")
    async def open_logs():
        """Open F:\\SDR\\CGrep\\Logs in Windows Explorer."""
        import subprocess as _sp, os as _ose
        log_dir = r"C:\projects\CGrep-v3\Logs"
        _ose.makedirs(log_dir, exist_ok=True)
        _sp.Popen(["explorer", log_dir])
        return {"ok": True}

    @app.post("/dashboard/alert")
    async def dashboard_alert(payload: dict = _Body(...)):
        """Log a red-threshold crossing: {freq, dbfs, ts}."""
        import csv as _csv2, os as _osa, datetime as _dta
        log_dir = r"C:\projects\CGrep-v3\Logs"
        _osa.makedirs(log_dir, exist_ok=True)
        today = _dta.date.today().strftime("%Y_%m_%d")
        path  = _osa.path.join(log_dir, f"reds_{today}.csv")
        is_new = not _osa.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = _csv2.writer(fh)
            if is_new:
                w.writerow(["Timestamp", "Frequency_MHz", "Power_dBFS"])
            w.writerow([payload.get("ts",""), payload.get("freq",""), payload.get("dbfs","")])
        return {"ok": True}

    return app


def _start_backend(bridge: "_UiBridge") -> bool:
    """Start the FastAPI/uvicorn server on a daemon thread.
    Returns True if backend started, False if deps are missing."""
    try:
        import uvicorn
    except Exception as exc:
        print(f"[landing] uvicorn unavailable, landing page disabled: {exc}")
        return False

    app = _build_landing_app(bridge)

    def _run_server():
        try:
            uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
        except Exception as exc:
            print(f"[landing] uvicorn error: {exc}")

    threading.Thread(target=_run_server, daemon=True).start()
    # Give uvicorn a moment to bind before the QWebEngineView issues its GET
    time.sleep(0.6)
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    # 1. Bridge object lives on the GUI thread; FastAPI handlers emit on it.
    bridge = _UiBridge()

    # 2. Spin up the FastAPI server in the background BEFORE Qt boots, so the
    #    QWebEngineView can immediately load http://127.0.0.1:8000 on show.
    backend_ok = _start_backend(bridge)

    # 3. Boot the single Qt window.  It contains a QStackedWidget:
    #       page 0 = QWebEngineView (3D map)
    #       page 1 = FM monitor (the existing UI)
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    _win_ref[0] = win  # expose to the FastAPI /ws/sdr handler

    # Wire the Site-1-peak click (POST /switch/spectrum_core in the React app) to the
    # in-window page swap.  Qt marshals this signal to the GUI thread.
    bridge.switch_requested.connect(win.switch_to_monitor)
    bridge.map_requested.connect(win.switch_to_map)
    bridge.audio_mute_requested.connect(win.mute_audio)
    bridge.audio_unmute_requested.connect(win.unmute_audio)
    bridge.radio_start_requested.connect(win.start_radio)
    bridge.radio_stop_requested.connect(win.stop_radio)
    bridge.tune_requested.connect(win.tune_to_freq)
    bridge.scan_worker_start.connect(win.start_scan_worker)
    bridge.stop_and_scan.connect(win.stop_and_scan)

    if not backend_ok:
        # If the backend can't start, just show the FM monitor directly.
        win.switch_to_monitor()

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()