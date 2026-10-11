"""
spectrum_monitor_db.py - Database-driven Spectrum Monitor
===========================================================
Uses EXACT same pyqtgraph waterfall from lookforwaterfall.py,
but with pyqtgraph spectrum (more reliable than HTML canvas).
Reads from sdr_db rf_sweep_data table.

Usage:
    python spectrum_monitor_db.py
"""

import sys
import json
import time
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

# Database config
DB_HOST = "127.0.0.1"
DB_PORT = 5432
DB_NAME = "sdr_db"
DB_USER = "postgres"
DB_PASSWORD = "changeme"  # TODO: set your own password

pg.setConfigOption('useOpenGL', False)
pg.setConfigOption('enableExperimental', False)
pg.setConfigOption('background', '#03000a')
pg.setConfigOption('foreground', '#dddddd')


def get_db_session():
    dsn = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    engine = create_engine(dsn, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    return Session(), engine


def fetch_latest_sweep(db):
    """Fetch the most recent sweep from database."""
    query = text("""
        SELECT timestamp, frequency_mhz, power_dbfs, floor_dbfs, snr_db
        FROM rf_sweep_data
        WHERE timestamp >= NOW() - INTERVAL '5 minutes'
          AND frequency_mhz >= 88000 AND frequency_mhz <= 108000
        ORDER BY timestamp DESC, frequency_mhz ASC
        LIMIT 200
    """)
    return db.execute(query).fetchall()


# ---------------------------------------------------------------------------
# Database Worker
# ---------------------------------------------------------------------------
class DbWorker(QtCore.QThread):
    """Emits fft_ready signal just like SdrWorker."""
    
    fft_ready = QtCore.pyqtSignal(np.ndarray, float, float, float)
    error = QtCore.pyqtSignal(str)

    def __init__(self, db, value_column='snr_db', parent=None):
        super().__init__(parent)
        self.db = db
        self.value_column = value_column
        self._running = False
        self._refresh_interval = 2.0
        self._fc = 98e6
        self._fs = 20e6

    def set_value_column(self, col):
        self.value_column = col

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        print(f"[DbWorker] Started, refreshing every {self._refresh_interval}s")
        while self._running:
            try:
                sweep_data = fetch_latest_sweep(self.db)
                print(f"[DbWorker] Fetched {len(sweep_data)} rows from DB")
                if sweep_data:
                    spec_db = self._process_to_spectrum(sweep_data)
                    print(f"[DbWorker] Processed spectrum: {spec_db.shape if spec_db is not None else 'None'}, range [{spec_db.min():.1f}, {spec_db.max():.1f}]" if spec_db is not None else "[DbWorker] No spectrum data")
                    if spec_db is not None:
                        self.fft_ready.emit(spec_db, self._fc, self._fs, self._fc)
                        print("[DbWorker] Emitted fft_ready signal")
            except Exception as e:
                print(f"[DbWorker] Error: {e}")
                self.error.emit(str(e))
            time.sleep(self._refresh_interval)

    def _process_to_spectrum(self, sweep_data):
        freqs_mhz = []
        values = []
        for ts, freq_khz, power, floor, snr in sweep_data:
            freqs_mhz.append(freq_khz / 1000.0)
            if self.value_column == 'power_dbfs':
                values.append(power if power is not None else -120.0)
            elif self.value_column == 'floor_dbfs':
                values.append(floor if floor is not None else -120.0)
            else:
                values.append(snr if snr is not None else 0.0)
        
        if len(freqs_mhz) < 2:
            return None
        
        values = np.array(values, dtype=np.float32)
        values = np.nan_to_num(values, nan=-120.0, posinf=-10.0, neginf=-140.0)
        
        # Interpolate to 200 bins for 88-108 MHz
        target_freqs = np.linspace(88.0, 108.0, 200)
        return np.interp(target_freqs, freqs_mhz, values, left=values[0], right=values[-1])


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    """Spectrum monitor - exact same UI/UX as lookforwaterfall.py."""
    
    WATERFALL_ROWS = 256

    def __init__(self, db=None):
        super().__init__()
        self.setWindowTitle("Spectrum Monitor - Database")
        self.resize(1400, 800)

        # Worker
        self.db = db
        self.worker = DbWorker(db, value_column='snr_db')
        self.worker.fft_ready.connect(self._on_fft, QtCore.Qt.ConnectionType.QueuedConnection)
        self.worker.error.connect(self._on_error, QtCore.Qt.ConnectionType.QueuedConnection)

        # State (exact from original)
        self.spec_smoothed = None
        self.spec_alpha = 0.35
        self.waterfall_buf = np.full((self.WATERFALL_ROWS, 200), -120.0, dtype=np.float32)
        self._wf_row = 0
        self._last_wf_draw = 0.0
        self._fc = 98e6
        self._fs = 20e6
        self._tuned_fc = 98e6
        self._spec_db_lo = 0   # SNR min (0 dB)
        self._spec_db_hi = 50  # SNR max (50 dB)

        self._build_ui()
        self._apply_initial_settings()
        self.worker.start()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Stylesheet (exact from lookforwaterfall.py)
        central.setStyleSheet("""
            QWidget { background:#111318; }
            QLabel { color:rgba(255,255,255,0.45); font-family:'SF Pro Text','Helvetica Neue',Arial; font-size:11px; }
            QGroupBox {
                color:rgba(255,255,255,0.55); font-family:'SF Pro Display','Helvetica Neue',Arial,sans-serif;
                font-size:10px; font-weight:600; letter-spacing:0.8px;
                border:1px solid rgba(255,255,255,0.08); border-radius:10px; margin-top:16px;
                background:rgba(28,30,36,0.85);
            }
            QGroupBox::title { subcontrol-origin:margin; left:12px; top:3px; color:rgba(255,255,255,0.38); letter-spacing:1px; font-size:9px; }
            QComboBox {
                background:rgba(255,255,255,0.07); color:rgba(255,255,255,0.88);
                border:1px solid rgba(255,255,255,0.12); border-radius:7px; padding:4px 8px;
                font-family:'SF Pro Text','Helvetica Neue',Arial; font-size:12px;
            }
            QSlider::groove:horizontal { height:3px; background:rgba(255,255,255,0.10); border-radius:2px; }
            QSlider::handle:horizontal { width:14px; height:14px; margin:-6px 0; background:white; border-radius:7px; }
            QSlider::sub-page:horizontal { background:rgba(10,132,255,0.80); border-radius:2px; }
        """)

        # Top bar
        top_bar = QtWidgets.QWidget()
        top_bar.setFixedHeight(52)
        top_bar.setStyleSheet("background:rgba(20,22,28,0.97); border-bottom:1px solid rgba(255,255,255,0.07);")
        top = QtWidgets.QHBoxLayout(top_bar)
        top.setContentsMargins(16, 0, 16, 0)
        
        title = QtWidgets.QLabel("Spectrum Monitor")
        title.setStyleSheet("color:rgba(255,255,255,0.55); font-family:'SF Pro Display','Helvetica Neue',Arial; font-size:14px; font-weight:500;")
        top.addWidget(title)
        top.addStretch(1)
        
        self.status_lbl = QtWidgets.QLabel("Database Mode")
        self.status_lbl.setStyleSheet("color:rgba(255,255,255,0.30); font-family:'SF Pro Text','Helvetica Neue',Arial; font-size:11px;")
        top.addWidget(self.status_lbl)
        
        root.addWidget(top_bar)

        # Main area
        split = QtWidgets.QHBoxLayout()
        split.setContentsMargins(12, 12, 12, 12)
        split.setSpacing(12)

        # Center: Spectrum + Waterfall
        center = self._build_center()
        split.addWidget(center, 1)

        # Sidebar
        sidebar = self._build_sidebar()
        split.addWidget(sidebar)

        root.addLayout(split)

    def _build_center(self):
        """Build spectrum (pyqtgraph) + waterfall."""
        container = QtWidgets.QWidget()
        vlay = QtWidgets.QVBoxLayout(container)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(2)

        # Spectrum plot (pyqtgraph - more reliable than HTML)
        spec_widget = pg.GraphicsLayoutWidget()
        spec_widget.setBackground('#03000a')
        self.spec_plot = spec_widget.addPlot(row=0, col=0)
        self.spec_plot.setLabel('bottom', 'Frequency', units='MHz', color='rgba(255,255,255,0.35)')
        self.spec_plot.setLabel('left', 'dB', color='rgba(255,255,255,0.35)')
        self.spec_plot.getAxis('bottom').setTextPen(pg.mkPen((255, 255, 255, 90)))
        self.spec_plot.getAxis('left').setTextPen(pg.mkPen((255, 255, 255, 90)))
        self.spec_plot.showGrid(x=True, y=True, alpha=0.2)
        self.spec_plot.setMouseEnabled(x=True, y=False)
        
        # Spectrum curves (cyan = smoothed, yellow = raw)
        self.spec_curve = self.spec_plot.plot(pen=pg.mkPen('#00E5FF', width=2))
        self.spec_curve_ema = self.spec_plot.plot(pen=pg.mkPen('#FFC53D', width=1.5))
        
        # Tuning line
        self.tune_line_spec = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen('#ffcc33', width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.spec_plot.addItem(self.tune_line_spec)

        # Waterfall (exact from original)
        wf_widget = pg.GraphicsLayoutWidget()
        wf_widget.ci.setSpacing(0)
        wf_widget.setBackground('#03000a')
        self.wf_plot = wf_widget.addPlot(row=0, col=0)
        self.wf_plot.setLabel('bottom', 'Frequency', units='MHz', color='rgba(255,255,255,0.35)', size='9pt')
        self.wf_plot.setLabel('left', 'Time', color='rgba(255,255,255,0.35)', size='9pt')
        self.wf_plot.getAxis('bottom').setTextPen(pg.mkPen((255, 255, 255, 90)))
        self.wf_plot.getAxis('left').setTextPen(pg.mkPen((255, 255, 255, 90)))
        self.wf_plot.setMouseEnabled(x=True, y=False)
        # Fix Y axis to show time scrolling down (0 at top, 256 at bottom)
        self.wf_plot.getViewBox().invertY(False)
        
        self.wf_img = pg.ImageItem(axisOrder='row-major')
        self.wf_plot.addItem(self.wf_img)
        self.wf_lut = self._build_lut()
        self.wf_img.setLookupTable(self.wf_lut)
        self.wf_img.setLevels((0, 50))  # Match SNR range
        
        self.tune_line_wf = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen('#ffcc33', width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.wf_plot.addItem(self.tune_line_wf)

        vlay.addWidget(spec_widget, 2)
        vlay.addWidget(wf_widget, 3)
        return container

    def _build_sidebar(self):
        """Right sidebar controls."""
        box = QtWidgets.QGroupBox("DISPLAY")
        box.setMaximumWidth(210)
        lay = QtWidgets.QFormLayout(box)

        def _row(slider, lbl):
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            h.addWidget(slider, 1)
            h.addWidget(lbl)
            lbl.setFixedWidth(40)
            lbl.setStyleSheet("color:rgba(255,255,255,0.55); font-size:11px;")
            return w

        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(5, 100)
        self.zoom_slider.setValue(100)
        self.zoom_lbl = QtWidgets.QLabel("100%")
        self.zoom_slider.valueChanged.connect(self._apply_zoom)
        self.zoom_slider.valueChanged.connect(lambda v: self.zoom_lbl.setText(f"{v}%"))
        lay.addRow("Zoom:", _row(self.zoom_slider, self.zoom_lbl))

        self.range_min = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.range_min.setRange(0, 100)
        self.range_min.setValue(0)
        self.range_min_lbl = QtWidgets.QLabel("0")
        self.range_min.valueChanged.connect(self._apply_range)
        self.range_min.valueChanged.connect(lambda v: self.range_min_lbl.setText(str(v)))
        lay.addRow("dB min:", _row(self.range_min, self.range_min_lbl))

        self.range_max = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.range_max.setRange(0, 100)
        self.range_max.setValue(50)
        self.range_max_lbl = QtWidgets.QLabel("50")
        self.range_max.valueChanged.connect(self._apply_range)
        self.range_max.valueChanged.connect(lambda v: self.range_max_lbl.setText(str(v)))
        lay.addRow("dB max:", _row(self.range_max, self.range_max_lbl))

        self.offset_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.offset_slider.setRange(-60, 60)
        self.offset_slider.setValue(0)
        self.offset_lbl = QtWidgets.QLabel("0")
        self.offset_slider.valueChanged.connect(self._apply_offset)
        self.offset_slider.valueChanged.connect(lambda v: self.offset_lbl.setText(str(v)))
        lay.addRow("WF offset:", _row(self.offset_slider, self.offset_lbl))

        self.val_combo = QtWidgets.QComboBox()
        self.val_combo.addItem("SNR (dB)", "snr_db")
        self.val_combo.addItem("Power (dBFS)", "power_dbfs")
        self.val_combo.addItem("Floor (dBFS)", "floor_dbfs")
        self.val_combo.currentIndexChanged.connect(self._on_value_changed)
        lay.addRow("Plot:", self.val_combo)

        lay.addItem(QtWidgets.QSpacerItem(10, 10, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding))
        return box

    @staticmethod
    def _build_lut():
        """Cyber-dark LUT from lookforwaterfall.py."""
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

    def _on_value_changed(self, idx):
        self.worker.set_value_column(self.val_combo.currentData())

    def _apply_initial_settings(self):
        # Set spectrum ranges
        self.spec_plot.setXRange(88.0, 108.0, padding=0)
        self.spec_plot.setYRange(0, 50, padding=0)
        # Set waterfall ranges
        self.wf_plot.setXRange(88.0, 108.0, padding=0)
        self.wf_plot.setYRange(0, self.WATERFALL_ROWS, padding=0)
        # Apply user settings
        self._apply_range()
        self._apply_zoom()

    def _apply_zoom(self):
        """Apply zoom - spectrum X range and waterfall X range."""
        frac = self.zoom_slider.value() / 100.0
        half = 10.0 * frac  # 10 MHz half-span max
        center = 98.0
        x0 = max(center - half, 88.0)
        x1 = min(center + half, 108.0)
        
        # Update spectrum plot (MHz)
        self.spec_plot.setXRange(x0, x1, padding=0)
        
        # Update waterfall (Hz)
        self.wf_plot.setXRange(x0 * 1e6, x1 * 1e6, padding=0)

    def _apply_range(self):
        """Apply dB range."""
        lo = self.range_min.value()
        hi = self.range_max.value()
        if hi <= lo:
            hi = lo + 1
        self._spec_db_lo = lo
        self._spec_db_hi = hi
        
        # Update spectrum Y range
        self.spec_plot.setYRange(lo, hi, padding=0)
        
        # Update waterfall levels
        off = self.offset_slider.value()
        self.wf_img.setLevels((lo + off, hi + off))

    def _apply_offset(self):
        self._apply_range()

    @QtCore.pyqtSlot(np.ndarray, float, float, float)
    def _on_fft(self, spec_db: np.ndarray, fc: float, fs: float, tuned_fc: float):
        """Handle new spectrum data from worker."""
        print(f"[_on_fft] Received spectrum: {spec_db.shape}, range [{spec_db.min():.1f}, {spec_db.max():.1f}]")
        # EMA smoothing
        if self.spec_smoothed is None or self.spec_smoothed.size != spec_db.size:
            self.spec_smoothed = spec_db.copy()
        else:
            a = self.spec_alpha
            self.spec_smoothed = (1 - a) * self.spec_smoothed + a * spec_db

        n = spec_db.size
        freqs = fc + (np.arange(n) - n / 2) * (fs / n)
        freqs_mhz = freqs / 1e6

        # Update spectrum curves
        self.spec_curve.setData(freqs_mhz, spec_db)
        self.spec_curve_ema.setData(freqs_mhz, self.spec_smoothed)

        # Ring-buffer waterfall
        if self.waterfall_buf.shape[1] != n:
            self.waterfall_buf = np.full((self.WATERFALL_ROWS, n), -120.0, dtype=np.float32)
            self._wf_row = 0
        
        self.waterfall_buf[self._wf_row % self.WATERFALL_ROWS, :] = self.spec_smoothed
        self._wf_row += 1

        # Always update waterfall (throttle to ~20 fps)
        now_wf = time.monotonic()
        if now_wf - self._last_wf_draw >= 0.05:
            self._last_wf_draw = now_wf
            # Ring buffer display
            r = self._wf_row % self.WATERFALL_ROWS
            rows = self.WATERFALL_ROWS
            display = np.zeros_like(self.waterfall_buf)
            display[:rows-r] = self.waterfall_buf[r:]
            display[rows-r:] = self.waterfall_buf[:r]
            self.wf_img.setImage(display, autoLevels=False)
            # X range 88-108 MHz, Y range 0-256 (time)
            self.wf_img.setRect(QtCore.QRectF(88.0, 0, 20.0, rows))
            self.wf_plot.setYRange(0, rows, padding=0)

        # Update tuning markers
        self.tune_line_spec.setPos(tuned_fc / 1e6)
        self.tune_line_wf.setPos(tuned_fc)

    @QtCore.pyqtSlot(str)
    def _on_error(self, msg: str):
        self.status_lbl.setText(f"Error: {msg}")

    def closeEvent(self, e: QtGui.QCloseEvent):
        self.worker.stop()
        self.worker.wait(1500)
        super().closeEvent(e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("Spectrum Monitor - Database Mode")
    print("=" * 60)
    print(f"Database: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    print("=" * 60)
    
    db, engine = get_db_session()
    
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle('Fusion')
    
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor('#111318'))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor('#dddddd'))
    app.setPalette(palette)
    
    window = MainWindow(db=db)
    window.show()
    
    print("\n[Spectrum Monitor] Running...")
    print("Close window to stop\n")
    
    try:
        app.exec()
    except KeyboardInterrupt:
        print("\n[Interrupt] Stopping...")
    finally:
        db.close()
        engine.dispose()
        print("[Done] Spectrum Monitor closed")


if __name__ == "__main__":
    main()
