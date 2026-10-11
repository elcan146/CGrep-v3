"""
waterfall.py — PyQt6 + pyqtgraph Waterfall for FM Band (88-108 MHz)
===================================================================
Based on spectrum_core.py implementation:
  * Ring buffer waterfall (no np.roll per frame)
  * Cyber-dark LUT coloring
  * Hardware-accelerated pyqtgraph rendering
  * Real-time spectrum + waterfall + stats

Usage:
    python waterfall.py                    # Real-time mode
    python waterfall.py --history 30       # Last 30 minutes history
    python waterfall.py --value power_dbfs # Plot power instead of SNR

Database: sdr_db
Table: rf_sweep_data (frequency_mhz, power_dbfs, floor_dbfs, snr_db, timestamp)
"""

import os
import sys
import time
import signal
import argparse
import numpy as np
from datetime import datetime, timedelta

# PyQt6 + pyqtgraph (same as spectrum_core.py)
from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

# Disable OpenGL (same fix as spectrum_core.py)
try:
    pg.setConfigOption('useOpenGL', False)
    pg.setConfigOption('enableExperimental', False)
except Exception:
    pass
pg.setConfigOption('background', '#06020d')
pg.setConfigOption('foreground', '#dddddd')

# Database
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sdr_db")
DB_USER = os.getenv("DB_USER", "postgres")

# FM Band (stored as kHz in DB)
FM_START_MHZ = 88.0
FM_END_MHZ = 108.0
FM_START_KHZ = int(FM_START_MHZ * 1000)
FM_END_KHZ = int(FM_END_MHZ * 1000)

# Waterfall settings (same as spectrum_core.py)
WATERFALL_ROWS = 256
FFT_BINS = 200
REFRESH_INTERVAL = 2.0

running = True

# ---------------------------------------------------------------------------
# Database Functions
# ---------------------------------------------------------------------------
def get_db_session():
    db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    engine = create_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session(), engine


def fetch_latest_sweep(db):
    """Fetch the most recent complete sweep."""
    sql = """
        WITH latest_sweep AS (
            SELECT timestamp FROM rf_sweep_data
            WHERE frequency_mhz BETWEEN :min_freq AND :max_freq
            ORDER BY timestamp DESC LIMIT 1
        )
        SELECT r.timestamp, r.frequency_mhz, r.power_dbfs, r.floor_dbfs, r.snr_db
        FROM rf_sweep_data r
        JOIN latest_sweep l ON r.timestamp = l.timestamp
        WHERE r.frequency_mhz BETWEEN :min_freq AND :max_freq
        ORDER BY r.frequency_mhz ASC
    """
    result = db.execute(text(sql), {"min_freq": FM_START_KHZ, "max_freq": FM_END_KHZ})
    return [(r[0], r[1], r[2], r[3], r[4]) for r in result.fetchall()]


def fetch_history_data(db, minutes=10):
    """Fetch historical sweep data."""
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(minutes=minutes)
    
    sql = """
        SELECT timestamp, frequency_mhz, power_dbfs, floor_dbfs, snr_db
        FROM rf_sweep_data
        WHERE frequency_mhz BETWEEN :min_freq AND :max_freq
          AND timestamp BETWEEN :start AND :end
        ORDER BY timestamp DESC, frequency_mhz ASC
        LIMIT 50000
    """
    result = db.execute(text(sql), {
        "min_freq": FM_START_KHZ, "max_freq": FM_END_KHZ,
        "start": start_time, "end": end_time
    })
    return [(r[0], r[1], r[2], r[3], r[4]) for r in result.fetchall()]


# ---------------------------------------------------------------------------
# Signal Handlers
# ---------------------------------------------------------------------------
def stop(sig, frame):
    global running
    running = False

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)


# ---------------------------------------------------------------------------
# PyQt6 Main Window (based on spectrum_core.py implementation)
# ---------------------------------------------------------------------------
class WaterfallWindow(QtWidgets.QMainWindow):
    """FM Waterfall viewer using pyqtgraph (same ring buffer approach as spectrum_core.py)."""

    WATERFALL_ROWS = 256
    FFT_BINS = 200

    def __init__(self, value_column='snr_db', db_session=None, parent=None):
        super().__init__(parent)
        self.value_column = value_column
        self.db = db_session

        self.setWindowTitle(f'FM Waterfall 88-108 MHz ({value_column})')
        self.resize(1200, 800)

        # Central widget
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        vlay = QtWidgets.QVBoxLayout(central)
        vlay.setContentsMargins(8, 8, 8, 8)
        vlay.setSpacing(8)

        # ---- Top: Spectrum Plot ----
        spec_card = QtWidgets.QWidget()
        spec_card.setStyleSheet("background:#1A1C25; border-radius:8px;")
        spec_lay = QtWidgets.QVBoxLayout(spec_card)
        spec_lay.setContentsMargins(8, 8, 8, 8)

        self.spec_widget = pg.GraphicsLayoutWidget()
        self.spec_widget.setBackground('#0B0E14')
        self.spec_plot = self.spec_widget.addPlot(row=0, col=0)
        self.spec_plot.setLabel('bottom', 'Frequency', units='MHz', color='#9CA3AF')
        self.spec_plot.setLabel('left', self.get_value_label(), color='#9CA3AF')
        self.spec_plot.setXRange(FM_START_MHZ, FM_END_MHZ)
        self.spec_plot.getAxis('bottom').setTextPen(pg.mkPen('#9CA3AF'))
        self.spec_plot.getAxis('left').setTextPen(pg.mkPen('#9CA3AF'))
        self.spec_plot.showGrid(x=True, y=True, alpha=0.2)

        # Spectrum curves
        self.spec_curve = self.spec_plot.plot(pen=pg.mkPen('#00E5FF', width=1.5))
        self.spec_curve_ema = self.spec_plot.plot(pen=pg.mkPen('#FFC53D', width=2))

        spec_lay.addWidget(self.spec_widget)
        vlay.addWidget(spec_card, 2)

        # ---- Middle: Waterfall ----
        wf_card = QtWidgets.QWidget()
        wf_card.setStyleSheet("background:#1A1C25; border-radius:8px;")
        wf_lay = QtWidgets.QVBoxLayout(wf_card)
        wf_lay.setContentsMargins(8, 8, 8, 8)

        self.wf_widget = pg.GraphicsLayoutWidget()
        self.wf_widget.ci.setSpacing(0)
        self.wf_widget.setBackground('#0B0E14')
        self.wf_plot = self.wf_widget.addPlot(row=0, col=0)
        self.wf_plot.setLabel('bottom', 'Frequency', units='MHz', color='#9CA3AF')
        self.wf_plot.setLabel('left', 'Time', color='#9CA3AF')
        self.wf_plot.setXRange(FM_START_MHZ, FM_END_MHZ)
        self.wf_plot.getAxis('bottom').setTextPen(pg.mkPen('#9CA3AF'))
        self.wf_plot.getAxis('left').setTextPen(pg.mkPen('#9CA3AF'))
        self.wf_plot.setMouseEnabled(x=True, y=False)

        # Waterfall ImageItem (same as spectrum_core.py)
        self.wf_img = pg.ImageItem(axisOrder='row-major')
        self.wf_plot.addItem(self.wf_img)
        self.wf_lut = self._build_lut()
        self.wf_img.setLookupTable(self.wf_lut)
        self.wf_img.setLevels((-65, -36))

        # Frequency marker line
        self.tune_line = pg.InfiniteLine(
            angle=90, movable=True,
            pen=pg.mkPen('#FFC53D', width=1.5, style=QtCore.Qt.PenStyle.DashLine)
        )
        self.wf_plot.addItem(self.tune_line)

        wf_lay.addWidget(self.wf_widget)
        vlay.addWidget(wf_card, 3)

        # ---- Bottom: Stats Panel ----
        stats_card = QtWidgets.QWidget()
        stats_card.setStyleSheet("background:#1A1C25; border-radius:8px;")
        stats_lay = QtWidgets.QHBoxLayout(stats_card)
        stats_lay.setContentsMargins(12, 8, 12, 8)

        self.stats_label = QtWidgets.QLabel("Waiting for data...")
        self.stats_label.setStyleSheet(
            "color:#00E5FF; font-family:'JetBrains Mono','Consolas',monospace; font-size:11px;"
        )
        stats_lay.addWidget(self.stats_label)
        stats_lay.addStretch(1)

        # Controls
        controls = QtWidgets.QHBoxLayout()

        self.min_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.min_slider.setRange(-140, 0)
        self.min_slider.setValue(-65)
        self.min_slider.valueChanged.connect(self._update_levels)
        controls.addWidget(QtWidgets.QLabel("Min:"))
        controls.addWidget(self.min_slider)

        self.max_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.max_slider.setRange(-140, 0)
        self.max_slider.setValue(-36)
        self.max_slider.valueChanged.connect(self._update_levels)
        controls.addWidget(QtWidgets.QLabel("Max:"))
        controls.addWidget(self.max_slider)

        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setStyleSheet(
            "QPushButton { background:#2A2D3A; color:#fff; padding:4px 12px; border-radius:4px; }"
            "QPushButton:checked { background:#E11D48; }"
        )
        controls.addWidget(self.pause_btn)

        stats_lay.addLayout(controls)
        vlay.addWidget(stats_card)

        # ---- Waterfall State (ring buffer approach from spectrum_core.py) ----
        self.waterfall_buf = np.full(
            (self.WATERFALL_ROWS, self.FFT_BINS), -120.0, dtype=np.float32
        )
        self._wf_display_buf = np.full(
            (self.WATERFALL_ROWS, self.FFT_BINS), -120.0, dtype=np.float32
        )
        self._wf_row = 0
        self._last_wf_draw = 0.0
        self._last_update = 0.0
        self.spec_smoothed = None
        self.spec_alpha = 0.35

        # Frequency axis cache
        self._freqs_cache = np.linspace(FM_START_MHZ, FM_END_MHZ, self.FFT_BINS)

        # Update timer
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update)
        self.timer.start(int(REFRESH_INTERVAL * 1000))

    def _build_lut(self):
        """Cyber-dark waterfall LUT (same as spectrum_core.py)."""
        stops = np.array([
            [11,  14,  20],   # #0B0E14
            [26,  28,  37],   # #1A1C25
            [60,  40, 120],   # deep indigo
            [99, 102, 241],   # #6366F1
            [0,   229, 255],  # #00E5FF
            [255, 255, 255],  # white peaks
        ], dtype=np.float32)
        xs = np.linspace(0, 1, len(stops))
        idx = np.linspace(0, 1, 256)
        lut = np.empty((256, 3), dtype=np.uint8)
        for c in range(3):
            lut[:, c] = np.interp(idx, xs, stops[:, c]).astype(np.uint8)
        return lut

    def get_value_label(self):
        labels = {'power_dbfs': 'Power (dBFS)', 'floor_dbfs': 'Noise Floor (dBFS)', 'snr_db': 'SNR (dB)'}
        return labels.get(self.value_column, 'Value')

    def _update_levels(self):
        vmin = self.min_slider.value()
        vmax = self.max_slider.value()
        self.wf_img.setLevels((vmin, vmax))

    def _update(self):
        """Main update loop - fetch data and update plots."""
        global running
        if not running or self.pause_btn.isChecked() or not self.db:
            return

        try:
            sweep_data = fetch_latest_sweep(self.db)
            if sweep_data:
                self._process_sweep(sweep_data)
        except Exception as e:
            self.stats_label.setText(f"Error: {e}")

    def _process_sweep(self, sweep_data):
        """Process sweep data and update displays (same approach as spectrum_core.py)."""
        # Extract values
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

        values = np.array(values, dtype=np.float32)
        # Replace NaN/Inf with safe defaults
        values = np.nan_to_num(values, nan=-120.0, posinf=-10.0, neginf=-140.0)

        # EMA smoothing for spectrum display
        if self.spec_smoothed is None or len(self.spec_smoothed) != len(values):
            self.spec_smoothed = values.copy()
        else:
            self.spec_smoothed = (
                self.spec_alpha * values +
                (1 - self.spec_alpha) * self.spec_smoothed
            )

        # Update spectrum curves
        self.spec_curve.setData(freqs_mhz, values)
        self.spec_curve_ema.setData(freqs_mhz, self.spec_smoothed)

        # Update Y range auto-scale (clamped to prevent overflow)
        if len(values) > 0:
            vmin, vmax = float(np.min(values)), float(np.max(values))
            # Clamp to valid dB ranges
            vmin = max(-140.0, min(100.0, vmin))
            vmax = max(-140.0, min(100.0, vmax))
            margin = max((vmax - vmin) * 0.1, 5.0)
            self.spec_plot.setYRange(vmin - margin, vmax + margin, padding=0)

        # ---- Ring buffer waterfall update (spectrum_core.py approach) ----
        # Interpolate to fixed frequency bins
        if len(freqs_mhz) >= 2:
            interp_vals = np.interp(
                self._freqs_cache, freqs_mhz, values,
                left=values[0], right=values[-1]
            )
        else:
            interp_vals = np.full(self.FFT_BINS, values[0] if values else -120.0)

        # Ring buffer write (no np.roll)
        self.waterfall_buf[self._wf_row % self.WATERFALL_ROWS, :] = interp_vals
        self._wf_row += 1

        # Throttle waterfall redraw to ~20 fps
        now = time.monotonic()
        if now - self._last_wf_draw >= 0.05:
            self._last_wf_draw = now
            r = self._wf_row % self.WATERFALL_ROWS
            rows = self.WATERFALL_ROWS

            # Copy into display buffer in visual order (recent at bottom)
            self._wf_display_buf[:rows - r] = self.waterfall_buf[r:]
            self._wf_display_buf[rows - r:] = self.waterfall_buf[:r]

            self.wf_img.setImage(self._wf_display_buf, autoLevels=False)
            self.wf_img.setRect(QtCore.QRectF(
                FM_START_MHZ, 0, FM_END_MHZ - FM_START_MHZ, self.WATERFALL_ROWS))

        # Update stats
        self._update_stats(sweep_data, values)

    def _update_stats(self, sweep_data, values):
        """Update statistics display."""
        if len(values) == 0:
            return

        # Find peaks
        threshold = np.percentile(values, 75)
        peaks = [(freq, val) for freq, val in zip(
            [r[1]/1000.0 for r in sweep_data], values) if val > threshold]

        ts = sweep_data[0][0].strftime('%H:%M:%S') if sweep_data else 'N/A'
        stats = (f"Sweep: {ts} | {len(sweep_data)} pts | "
                 f"{self.value_column}: min={np.min(values):.1f} max={np.max(values):.1f} "
                 f"avg={np.mean(values):.1f} | Peaks: {len(peaks)}")
        self.stats_label.setText(stats)

    def closeEvent(self, event):
        global running
        running = False
        self.timer.stop()
        event.accept()


# ---------------------------------------------------------------------------
# History Dialog (static waterfall view)
# ---------------------------------------------------------------------------
class HistoryDialog(QtWidgets.QDialog):
    """Static waterfall view for historical data."""

    def __init__(self, db, minutes=10, value_column='snr_db', parent=None):
        super().__init__(parent)
        self.db = db
        self.minutes = minutes
        self.value_column = value_column

        self.setWindowTitle(f'History: Last {minutes} Minutes ({value_column})')
        self.resize(1000, 600)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Waterfall widget
        self.wf_widget = pg.GraphicsLayoutWidget()
        self.wf_widget.setBackground('#0B0E14')
        self.wf_plot = self.wf_widget.addPlot()
        self.wf_plot.setLabel('bottom', 'Frequency', units='MHz')
        self.wf_plot.setLabel('left', 'Time (recent → older)')

        self.wf_img = pg.ImageItem(axisOrder='row-major')
        self.wf_plot.addItem(self.wf_img)

        # Build LUT
        lut = self._build_lut()
        self.wf_img.setLookupTable(lut)
        self.wf_img.setLevels((-65, -36))

        layout.addWidget(self.wf_widget)

        # Load data
        self._load_data()

    def _build_lut(self):
        """Cyber-dark LUT."""
        stops = np.array([
            [11,  14,  20],
            [26,  28,  37],
            [60,  40, 120],
            [99, 102, 241],
            [0,   229, 255],
            [255, 255, 255],
        ], dtype=np.float32)
        xs = np.linspace(0, 1, len(stops))
        idx = np.linspace(0, 1, 256)
        lut = np.empty((256, 3), dtype=np.uint8)
        for c in range(3):
            lut[:, c] = np.interp(idx, xs, stops[:, c]).astype(np.uint8)
        return lut

    def _load_data(self):
        """Fetch and display historical data."""
        rows = fetch_history_data(self.db, self.minutes, self.value_column)
        if not rows:
            QtWidgets.QMessageBox.warning(self, "No Data", "No data found for the specified time range.")
            return

        # Group by timestamp
        sweeps = {}
        for ts, freq_khz, power, floor, snr in rows:
            if ts not in sweeps:
                sweeps[ts] = {}
            val = {'power_dbfs': power, 'floor_dbfs': floor, 'snr_db': snr}[self.value_column]
            sweeps[ts][freq_khz / 1000.0] = val

        timestamps = sorted(sweeps.keys(), reverse=True)
        all_freqs = sorted(set().union(*[s.keys() for s in sweeps.values()]))

        if not all_freqs or not timestamps:
            return

        # Build waterfall matrix
        waterfall = np.full((len(timestamps), len(all_freqs)), -120.0, dtype=np.float32)
        for row_idx, ts in enumerate(timestamps):
            for col_idx, freq in enumerate(all_freqs):
                if freq in sweeps[ts]:
                    waterfall[row_idx, col_idx] = sweeps[ts][freq]

        # Display
        self.wf_img.setImage(waterfall, autoLevels=False)
        self.wf_img.setRect(QtCore.QRectF(
            min(all_freqs), 0, max(all_freqs) - min(all_freqs), len(timestamps)))
        self.wf_plot.setXRange(min(all_freqs), max(all_freqs))
        self.wf_plot.setYRange(0, len(timestamps))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global running

    parser = argparse.ArgumentParser(description='FM Spectrum Waterfall (PyQt6 + pyqtgraph)')
    parser.add_argument('--history', type=int, metavar='MINUTES',
                        help='Show history for last N minutes (static view)')
    parser.add_argument('--value', choices=['power_dbfs', 'floor_dbfs', 'snr_db'],
                        default='snr_db', help='Value to plot (default: snr_db)')
    parser.add_argument('--refresh', type=float, default=REFRESH_INTERVAL,
                        help=f'Refresh interval in seconds (default: {REFRESH_INTERVAL})')

    args = parser.parse_args()

    print("=" * 60)
    print("FM Waterfall 88-108 MHz (PyQt6 + pyqtgraph)")
    print("=" * 60)
    print(f"Database: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    print(f"Plotting: {args.value}")
    print("=" * 60)

    # Connect to database
    db, engine = get_db_session()

    # Create Qt Application
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle('Fusion')

    # Dark palette
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor('#0B0E14'))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor('#dddddd'))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor('#1A1C25'))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor('#2A2D3A'))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor('#dddddd'))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor('#2A2D3A'))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor('#dddddd'))
    app.setPalette(palette)

    try:
        if args.history:
            # History mode
            dialog = HistoryDialog(db, minutes=args.history, value_column=args.value)
            dialog.exec()
        else:
            # Live mode
            window = WaterfallWindow(value_column=args.value, db_session=db)
            window.show()
            print(f"\n[Live Mode] Updating every {args.refresh}s")
            print("Close window to stop\n")
            app.exec()

    except KeyboardInterrupt:
        print("\n[Interrupt] Stopping...")

    finally:
        running = False
        db.close()
        engine.dispose()
        print("[Done] Waterfall closed")


if __name__ == "__main__":
    main()
