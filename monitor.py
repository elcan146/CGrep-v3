"""
monitor.py – SDR#-style FM spectrum monitor
========================================================
A standalone PyQt6 app that works like SDR#:
  • Live spectrum + waterfall
  • Click on spectrum to tune
  • Frequency entry, step buttons, mouse-wheel zoom
  • Peak-hold trace
  • WFM demod → sounddevice audio
  • Auto RTL-SDR, falls back to simulated RF

Run:
    python monitor.py
"""
from __future__ import annotations

import sys
import time
import math
import queue
import threading
import numpy as np

from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
pg.setConfigOption('useOpenGL', True)
pg.setConfigOption('enableExperimental', True)

try:
    import sounddevice as sd
    HAS_AUDIO = True
except Exception:
    sd = None
    HAS_AUDIO = False

import calculate as calc

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FS = calc.DEFAULT_FS
FFT_SIZE = calc.DEFAULT_FFT_SIZE
FM_LO = calc.FM_BAND_START_HZ
FM_HI = calc.FM_BAND_STOP_HZ
GAIN_DB = calc.DEFAULT_GAIN_DB
BW_HZ = calc.DEFAULT_WFM_BW_HZ

WATERFALL_ROWS = 250
TARGET_FPS = int(calc.TARGET_FPS)

SIM_STATIONS = [
    (88.7, 42, 440.0), (90.0, 38, 330.0), (92.5, 45, 528.0),
    (95.5, 35, 262.0), (97.7, 50, 392.0), (99.5, 40, 294.0),
    (101.0, 44, 349.0), (103.5, 36, 370.0), (105.0, 33, 220.0),
    (107.0, 39, 660.0),
]

# ---------------------------------------------------------------------------
# Audio – thread-safe queue → sounddevice callback
# ---------------------------------------------------------------------------
class AudioSink:
    def __init__(self, sr: int = calc.AUDIO_RATE):
        self.sr = sr
        self.volume = 0.6
        self._q: queue.Queue = queue.Queue(maxsize=40)
        self._stream = None
        self._rem = np.zeros(0, dtype=np.float32)  # leftover from previous callback

    def start(self):
        if not HAS_AUDIO:
            return
        # drain
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._rem = np.zeros(0, dtype=np.float32)
        self._stream = sd.OutputStream(
            samplerate=self.sr, channels=1, dtype='float32',
            blocksize=2048, latency=0.3,
            callback=self._cb,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._rem = np.zeros(0, dtype=np.float32)

    def push(self, chunk: np.ndarray):
        if chunk is None or chunk.size == 0:
            return
        a = chunk.astype(np.float32, copy=False) * self.volume
        np.clip(a, -1.0, 1.0, out=a)
        # Drop oldest if backed up (> ~1 s)
        if self._q.qsize() > 20:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
        self._q.put(a)

    def _cb(self, outdata, frames, time_info, status):
        needed = frames
        pos = 0
        out = np.zeros(needed, dtype=np.float32)
        # prepend any leftover from last callback
        if self._rem.size:
            take = min(self._rem.size, needed)
            out[:take] = self._rem[:take]
            pos = take
            self._rem = self._rem[take:] if take < self._rem.size else np.zeros(0, dtype=np.float32)
        while pos < needed and not self._q.empty():
            try:
                chunk = self._q.get_nowait()
            except queue.Empty:
                break
            take = min(len(chunk), needed - pos)
            out[pos:pos + take] = chunk[:take]
            pos += take
            if take < len(chunk):
                self._rem = chunk[take:]
        outdata[:, 0] = out


# ---------------------------------------------------------------------------
# Simulated RF source (FM-modulated tones you can actually hear)
# ---------------------------------------------------------------------------
class SimulatedRF:
    def __init__(self, fs: float):
        self.fs = fs
        self._phase = 0.0
        self._t = 0.0

    def read(self, n: int, fc: float) -> np.ndarray:
        """Return complex64 IQ samples."""
        dt = 1.0 / self.fs
        t = np.arange(n) * dt + self._t
        self._t = t[-1] + dt

        # Base noise
        iq = (np.random.normal(0, 0.03, n) + 1j * np.random.normal(0, 0.03, n)).astype(np.complex64)

        # Add FM-modulated stations
        for f_mhz, power_db, tone_hz in SIM_STATIONS:
            # Station offset from current tuned center
            offset_hz = (f_mhz * 1e6) - fc
            # Only include if within Nyquist
            if abs(offset_hz) > self.fs * 0.45:
                continue
            # FM tone
            m = 0.5 * np.sin(2 * math.pi * tone_hz * t)
            # FM phase accumulation
            kf = 75000.0 / 0.5  # deviation scaling
            phase = 2 * math.pi * offset_hz * t + 2 * math.pi * kf * np.cumsum(m) * dt
            amp = 10 ** ((power_db - 80) / 20.0)  # rough scaling
            iq += amp * np.exp(1j * phase).astype(np.complex64)

        return iq


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------
class SdrWorker(QtCore.QThread):
    spectrum_ready = QtCore.pyqtSignal(np.ndarray, np.ndarray, float, float)  # freqs, db, fc, fs
    audio_ready = QtCore.pyqtSignal(np.ndarray)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._fc = 97.7e6
        self._fs = FS
        self._gain = GAIN_DB
        self._bw = BW_HZ
        self._fft_size = FFT_SIZE

        self._use_sim = True
        self._sdr = None
        self._sim = SimulatedRF(self._fs)
        self._dsp_state = None

        self._lock = threading.Lock()

        # Try real hardware once at startup
        self._try_hw()

    def set_freq(self, hz: float):
        with self._lock:
            self._fc = float(hz)
        if self._sdr is not None:
            try:
                self._sdr.center_freq = int(self._fc - calc.LO_OFFSET_HZ)
            except Exception:
                pass

    def set_gain(self, db: float):
        with self._lock:
            self._gain = float(db)
        if self._sdr is not None:
            try:
                self._sdr.gain = self._gain
            except Exception:
                pass

    def set_fft_size(self, n: int):
        with self._lock:
            self._fft_size = int(n)
            self._dsp_state = None  # force reconfig

    def stop(self):
        self._running = False
        self.wait(3000)

    def _try_hw(self):
        try:
            from rtlsdr import RtlSdr
            self._sdr = RtlSdr()
            self._sdr.sample_rate = self._fs
            self._sdr.center_freq = int(self._fc - calc.LO_OFFSET_HZ)
            self._sdr.gain = self._gain
            self._use_sim = False
            print("[SDR] RTL-SDR ready")
        except Exception as e:
            print(f"[SDR] Simulated mode ({e})")
            self._use_sim = True

    def _read_real(self) -> np.ndarray | None:
        try:
            samples = np.asarray(self._sdr.read_samples(self._fft_size * 4), dtype=np.complex64)
            samples, _ = calc.apply_lo_shift(samples, self._fs, calc.LO_OFFSET_HZ)
            return samples[-self._fft_size:]
        except Exception as e:
            print(f"[SDR] read error: {e}")
            return None

    def run(self):
        win = calc.blackmanharris_window(self._fft_size)
        win_norm = float(np.sum(win))
        interval = calc.fft_interval_for_fps(self._fs, TARGET_FPS)
        last_fft = 0.0
        mix_phase = 0.0

        while self._running:
            t0 = time.monotonic()

            with self._lock:
                fc = self._fc
                fs = self._fs
                bw = self._bw
                nfft = self._fft_size

            if self._use_sim:
                iq = self._sim.read(nfft, fc)
            else:
                iq = self._read_real()
                if iq is None:
                    time.sleep(0.05)
                    continue
                # Re-apply LO shift to keep spectrum centred on fc
                iq, mix_phase = calc.apply_lo_shift(iq, fs, calc.LO_OFFSET_HZ, mix_phase)

            # ---- Spectrum ----
            now = time.monotonic()
            if now - last_fft >= interval:
                last_fft = now
                seg = iq[-nfft:]
                if seg.size < nfft:
                    seg = np.pad(seg, (0, nfft - seg.size))
                spec = np.fft.fftshift(np.fft.fft(seg * win))
                mag = np.abs(spec) / win_norm + 1e-12
                spec_db = (20.0 * np.log10(mag) + 29.0).astype(np.float32)
                spec_db = calc.clamp_spec_db(spec_db)
                freqs = calc.freq_axis_mhz(fc, fs, nfft)
                self.spectrum_ready.emit(freqs, spec_db, fc, fs)

            # ---- Audio ----
            audio, self._dsp_state = calc.wfm_demod_block(
                iq, fs=fs, bw=bw, state=self._dsp_state)
            if audio is not None and audio.size:
                self.audio_ready.emit(audio)

            elapsed = time.monotonic() - t0
            sleep_for = max(0.0, interval - elapsed)
            if sleep_for > 0:
                time.sleep(sleep_for)

        if self._sdr is not None:
            try:
                self._sdr.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main window – SDR# layout
# ---------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SDR# FM Monitor")
        self.resize(1400, 820)

        self.worker = SdrWorker()
        self.worker.spectrum_ready.connect(self._on_spectrum, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.audio_ready.connect(self._on_audio, QtCore.Qt.ConnectionType.QueuedConnection)

        self.audio = AudioSink()

        self.wf_buf = np.full((WATERFALL_ROWS, FFT_SIZE), -120.0, dtype=np.float32)
        self._wf_display = np.full((WATERFALL_ROWS, FFT_SIZE), -120.0, dtype=np.float32)
        self._wf_row = 0
        self._peak_hold = None
        self._spec_alpha = 0.30
        self._spec_smooth = None

        self._fc = 97.7e6
        self._fs = FS
        self._zoom_frac = 1.0

        self._build_ui()
        self.worker.start()
        self.audio.start()

    # ------------------------------------------------------------------
    def _build_ui(self):
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        root = QtWidgets.QVBoxLayout(cw)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Dark theme
        self.setStyleSheet("""
            QMainWindow { background:#0b0d14; }
            QLabel { color:rgba(255,255,255,0.60); font-family:'Segoe UI',monospace; font-size:11px; }
            QLineEdit { background:rgba(255,255,255,0.06); color:#00ffcc; border:1px solid #333; border-radius:5px; padding:3px 8px; font-family:monospace; font-size:13px; }
            QSlider::groove:horizontal { height:4px; background:#222; border-radius:2px; }
            QSlider::handle:horizontal { background:#00ffcc; border-radius:6px; width:12px; margin:-4px 0; }
            QSlider::sub-page:horizontal { background:#00aa88; border-radius:2px; }
            QPushButton { background:#1a1d26; color:#00ffcc; border:1px solid #333; border-radius:5px; padding:3px 10px; font-family:monospace; }
            QPushButton:hover { background:#252a36; }
            QPushButton:checked { background:#00ffcc; color:#000; }
            QComboBox { background:#1a1d26; color:#fff; border:1px solid #333; border-radius:5px; padding:3px; }
        """)
        pg.setConfigOption('background', '#0b0d14')
        pg.setConfigOption('foreground', '#888888')

        # ---- Row 1: Frequency controls ----
        row1 = QtWidgets.QHBoxLayout()

        self.btn_play = QtWidgets.QPushButton("▶ Start")
        self.btn_play.setCheckable(True)
        self.btn_play.setChecked(True)
        self.btn_play.toggled.connect(self._on_play)
        row1.addWidget(self.btn_play)

        # Step buttons
        for label, step in [("<<", -1.0), ("<", -0.1), (">", 0.1), (">>", 1.0)]:
            b = QtWidgets.QPushButton(label)
            b.setFixedWidth(32)
            b.clicked.connect(lambda _s=step: self._step_freq(_s))
            row1.addWidget(b)

        self.freq_edit = QtWidgets.QLineEdit("97.700")
        self.freq_edit.setFixedWidth(100)
        self.freq_edit.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.freq_edit.returnPressed.connect(self._on_freq_edit)
        row1.addWidget(self.freq_edit)
        row1.addWidget(QtWidgets.QLabel("MHz"))

        # Fine slider
        self.freq_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.freq_slider.setRange(8750, 10800)  # 0.01 MHz steps
        self.freq_slider.setValue(9770)
        self.freq_slider.valueChanged.connect(self._on_slider)
        row1.addWidget(self.freq_slider, 3)

        self.lbl_mode = QtWidgets.QLabel("SIM" if self.worker._use_sim else "RTL-SDR")
        self.lbl_mode.setStyleSheet("color:#ff9f0a; font-weight:bold;")
        row1.addWidget(self.lbl_mode)

        root.addLayout(row1)

        # ---- Row 2: hardware / display controls ----
        row2 = QtWidgets.QHBoxLayout()

        row2.addWidget(QtWidgets.QLabel("Gain"))
        self.gain_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.gain_slider.setRange(-1, 50)
        self.gain_slider.setValue(int(GAIN_DB))
        self.gain_slider.valueChanged.connect(self._on_gain)
        row2.addWidget(self.gain_slider, 1)
        self.lbl_gain = QtWidgets.QLabel(f"{int(GAIN_DB)} dB")
        row2.addWidget(self.lbl_gain)

        row2.addWidget(QtWidgets.QLabel("Vol"))
        self.vol_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(60)
        self.vol_slider.valueChanged.connect(self._on_vol)
        row2.addWidget(self.vol_slider, 1)
        self.lbl_vol = QtWidgets.QLabel("60%")
        row2.addWidget(self.lbl_vol)

        row2.addWidget(QtWidgets.QLabel("FFT"))
        self.fft_combo = QtWidgets.QComboBox()
        for label, val in [("8192", 8192), ("16384", 16384)]:
            self.fft_combo.addItem(label, val)
        self.fft_combo.currentIndexChanged.connect(self._on_fft_size)
        row2.addWidget(self.fft_combo)

        row2.addWidget(QtWidgets.QLabel("dB min"))
        self.dbmin_spin = QtWidgets.QSpinBox()
        self.dbmin_spin.setRange(-140, 0)
        self.dbmin_spin.setValue(-80)
        self.dbmin_spin.valueChanged.connect(self._on_range)
        row2.addWidget(self.dbmin_spin)

        row2.addWidget(QtWidgets.QLabel("max"))
        self.dbmax_spin = QtWidgets.QSpinBox()
        self.dbmax_spin.setRange(-100, 20)
        self.dbmax_spin.setValue(-10)
        self.dbmax_spin.valueChanged.connect(self._on_range)
        row2.addWidget(self.dbmax_spin)

        self.peak_btn = QtWidgets.QPushButton("Peak")
        self.peak_btn.setCheckable(True)
        self.peak_btn.setChecked(True)
        row2.addWidget(self.peak_btn)

        root.addLayout(row2)

        # ---- Spectrum ----
        self.spec_plot = pg.PlotWidget()
        self.spec_plot.setYRange(-80, -10)
        self.spec_plot.setLabel('bottom', 'Frequency', units='MHz')
        self.spec_plot.setLabel('left', 'Power', units='dBFS')
        self.spec_plot.showGrid(x=True, y=True, alpha=0.15)
        self.spec_plot.setMouseEnabled(x=True, y=False)
        self.spec_plot.scene().sigMouseClicked.connect(self._on_click)

        self.curve_live = self.spec_plot.plot(pen=pg.mkPen('#00ffcc', width=1.3),
                                              fillLevel=-90, brush=pg.mkBrush(0, 255, 200, 18))
        self.curve_peak = self.spec_plot.plot(pen=pg.mkPen('#ff3366', width=1.0, style=QtCore.Qt.PenStyle.DotLine))
        self.tune_line = pg.InfiniteLine(angle=90, movable=False,
                                         pen=pg.mkPen('#ffcc33', width=1.8,
                                                      style=QtCore.Qt.PenStyle.DashLine))
        self.spec_plot.addItem(self.tune_line)
        root.addWidget(self.spec_plot, 2)

        # ---- Waterfall ----
        self.wf_widget = pg.GraphicsLayoutWidget()
        self.wf_plot = self.wf_widget.addPlot()
        self.wf_plot.setLabel('bottom', 'Frequency', units='MHz')
        self.wf_plot.setLabel('left', 'Time')
        self.wf_img = pg.ImageItem(axisOrder='row-major')
        self.wf_plot.addItem(self.wf_img)
        self.wf_plot.setMouseEnabled(x=True, y=False)
        lut = calc.build_waterfall_lut()
        self.wf_img.setLookupTable(lut)
        self.wf_img.setLevels((-80, -20))
        root.addWidget(self.wf_widget, 3)

    # ------------------------------------------------------------------
    def _mhz(self) -> float:
        return self._fc / 1e6

    def _set_freq(self, mhz: float):
        mhz = max(87.5, min(108.0, mhz))
        self._fc = mhz * 1e6
        self.worker.set_freq(self._fc)
        self.freq_edit.setText(f"{mhz:.3f}")
        self.freq_slider.setValue(int(round(mhz * 100)))
        self.tune_line.setValue(mhz)

    def _step_freq(self, step_mhz: float):
        self._set_freq(self._mhz() + step_mhz)

    def _on_freq_edit(self):
        try:
            self._set_freq(float(self.freq_edit.text()))
        except ValueError:
            pass

    def _on_slider(self, v: int):
        self._set_freq(v / 100.0)

    def _on_gain(self, v: int):
        if v < 0:
            self.lbl_gain.setText("AUTO")
            self.worker.set_gain('auto')
        else:
            self.lbl_gain.setText(f"{v} dB")
            self.worker.set_gain(float(v))

    def _on_vol(self, v: int):
        self.lbl_vol.setText(f"{v}%")
        self.audio.volume = v / 100.0

    def _on_fft_size(self, _idx: int):
        n = self.fft_combo.currentData()
        self.worker.set_fft_size(n)
        # Reset buffers
        self.wf_buf = np.full((WATERFALL_ROWS, n), -120.0, dtype=np.float32)
        self._wf_row = 0
        self._peak_hold = None
        self._spec_smooth = None

    def _on_range(self):
        lo = self.dbmin_spin.value()
        hi = self.dbmax_spin.value()
        if hi <= lo:
            hi = lo + 5
        self.spec_plot.setYRange(lo, hi)
        self.wf_img.setLevels((lo, hi))

    def _on_play(self, on: bool):
        if on:
            self.btn_play.setText("▶ Start")
            self.worker.start()
            self.audio.start()
        else:
            self.btn_play.setText("⏹ Stop")
            self.worker.stop()
            self.audio.stop()

    # ------------------------------------------------------------------
    def _on_click(self, ev):
        """Click on spectrum to tune."""
        vb = self.spec_plot.getViewBox()
        pos = ev.scenePos()
        if self.spec_plot.sceneBoundingRect().contains(pos):
            mp = vb.mapSceneToView(pos)
            mhz = float(mp.x())
            if 87.0 <= mhz <= 108.5:
                self._set_freq(mhz)

    def _on_wheel(self, ev):
        """Mouse wheel zooms the X axis."""
        vb = self.spec_plot.getViewBox()
        # Simple zoom by adjusting X range around center
        x0, x1 = vb.viewRange()[0]
        ctr = (x0 + x1) / 2
        span = (x1 - x0) * (0.9 if ev.delta() > 0 else 1.1)
        span = max(0.2, min(20.0, span))
        vb.setXRange(ctr - span / 2, ctr + span / 2, padding=0)

    # ------------------------------------------------------------------
    @QtCore.pyqtSlot(np.ndarray, np.ndarray, float, float)
    def _on_spectrum(self, freqs_mhz: np.ndarray, db: np.ndarray, fc: float, fs: float):
        self._fc = fc
        self._fs = fs

        # EMA smooth
        if self._spec_smooth is None or self._spec_smooth.size != db.size:
            self._spec_smooth = db.copy()
        else:
            a = self._spec_alpha
            self._spec_smooth = (1 - a) * self._spec_smooth + a * db

        # Peak hold
        if self.peak_btn.isChecked():
            if self._peak_hold is None or self._peak_hold.size != db.size:
                self._peak_hold = db.copy()
            else:
                mask = self._spec_smooth > self._peak_hold
                self._peak_hold[mask] = self._spec_smooth[mask]
                self._peak_hold += (self._spec_smooth - self._peak_hold) * 0.02  # slow decay
        else:
            self._peak_hold = None

        self.curve_live.setData(freqs_mhz, self._spec_smooth)
        if self._peak_hold is not None:
            self.curve_peak.setData(freqs_mhz, self._peak_hold)
        else:
            self.curve_peak.setData([], [])

        # Auto X range if zoom not manually set
        vb = self.spec_plot.getViewBox()
        cur_x0, cur_x1 = vb.viewRange()[0]
        auto_x0 = freqs_mhz[0]
        auto_x1 = freqs_mhz[-1]
        # Only auto-range on first frame or if nearly full span
        if abs(cur_x1 - cur_x0) > (auto_x1 - auto_x0) * 0.99:
            vb.setXRange(auto_x0, auto_x1, padding=0)

        self.tune_line.setValue(fc / 1e6)
        self.freq_edit.setText(f"{fc / 1e6:.3f}")

        # Waterfall — pre-allocated display buffer, no np.concatenate per frame
        n = len(db)
        if self.wf_buf.shape[1] != n:
            self.wf_buf = np.full((WATERFALL_ROWS, n), -120.0, dtype=np.float32)
            self._wf_display = np.full((WATERFALL_ROWS, n), -120.0, dtype=np.float32)
            self._wf_row = 0
        self.wf_buf[self._wf_row % WATERFALL_ROWS, :] = self._spec_smooth
        self._wf_row += 1
        r = self._wf_row % WATERFALL_ROWS
        rows = WATERFALL_ROWS
        self._wf_display[:rows - r] = self.wf_buf[r:]
        self._wf_display[rows - r:] = self.wf_buf[:r]
        self.wf_img.setImage(self._wf_display, autoLevels=False)
        self.wf_img.setRect(QtCore.QRectF(freqs_mhz[0], 0,
                                          freqs_mhz[-1] - freqs_mhz[0], WATERFALL_ROWS))

    @QtCore.pyqtSlot(np.ndarray)
    def _on_audio(self, audio: np.ndarray):
        self.audio.push(audio)

    # ------------------------------------------------------------------
    def closeEvent(self, ev: QtGui.QCloseEvent):
        self.worker.stop()
        self.audio.stop()
        ev.accept()


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
