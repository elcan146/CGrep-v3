"""
simulation.py — Replay captured CSV sweeps into PostgreSQL as live SDR feeds
================================================================================

Scans the `simulation/` folder for CSV files produced by the CGrep collector
(df_<location>_final.csv), and lets you start/stop replaying each one into
`rf_sweep_data` with the same column layout as fm_dab_sweep.py.

Each CSV gets a deterministic SDR ID derived from its filename:
    df_peak_final.csv     -> site_1
    df_Site-3_final.csv   -> site_3
    df_Site-8_final.csv       -> site_8

If simulation/SDR-RTYS.xlsx is present, every emitted row is looked up in
the RTYS workbook.  When the frequency matches a known station, the row
is labelled with station_name and rtys_name; otherwise those fields are
NULL.  The original 7 region SDR feeds (Site-1, Site-3, ...) are kept
intact.

Rows are sent in the original collection order (CSV id ascending) so the
dashboard sees the same sweep progression the real SDR produced.  Timestamps are
rewritten to the current wall clock so the data looks live, not historical.

Environment (same as fm_dab_sweep.py):
    DB_HOST, DB_PORT, DB_NAME, DB_USER, POSTGRES_PASSWORD
    TAILSCALE_IP  — remote Tailscale IP to hit instead of DB_HOST (for over-the-internet testing)
    SDR_REPLICAS  — total SDRs to run (0 = one per CSV; set to 8 for load/bandwidth testing)
    SDR_GAIN (ignored here, only for real hardware)

Dependencies:
    PyQt6, sqlalchemy, psycopg2, pandas, openpyxl

Run:
    python simulation.py
"""

import os
import re
import sys
import csv
import time
import datetime
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

# PyQt6
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# SQLAlchemy
from sqlalchemy import create_engine, Column, Integer, Float, DateTime, String, text
from sqlalchemy.orm import declarative_base, sessionmaker

# pandas is used to parse the RTYS workbook (optional, but required for per-transmitter mode)
try:
    import pandas as pd
except ImportError:
    pd = None

# ---------------------------------------------------------------------------
# Local config.env loader (mirrors sdr_collector.py)
# ---------------------------------------------------------------------------
def _load_env_file():
    """Load config.env from same directory as the script/exe."""
    if getattr(sys, "frozen", False):
        _base = Path(sys.executable).parent
    else:
        _base = Path(__file__).resolve().parent
    env_path = _base / "config.env"
    if not env_path.is_file():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


_load_env_file()


# ---------------------------------------------------------------------------
# DB configuration (mirrors fm_dab_sweep.py + Tailscale support)
# ---------------------------------------------------------------------------
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme")
TAILSCALE_IP = os.getenv("TAILSCALE_IP", "").strip()
DB_HOST = os.getenv("DB_HOST", TAILSCALE_IP if TAILSCALE_IP else "::1")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sdr_db")

# Total number of SDRs to run, distributed across the CSVs in simulation/
# (0 or unset = one SDR per CSV file)
SDR_REPLICAS = max(0, int(os.getenv("SDR_REPLICAS", "0")))


# ---------------------------------------------------------------------------
# RTYS workbook mapping (simulation/SDR-RTYS.xlsx)
# ---------------------------------------------------------------------------
RTYS_XLSX_PATH = Path(__file__).resolve().parent / "simulation" / "SDR-RTYS.xlsx"

# CSV filename (location) -> Excel sheet name
CSV_TO_SHEET = {
    "Site-1": "Günəşli SDR",
    "Site-3": "Beyləqan SDR",
    "Site-4": "Gəncə SDR",
    "Site-5": "Masallı SDR",
    "Site-6": "Qəbələ SDR",
    "Site-7": "Site-7 SDR",
    "Site-8": "Şuşa SDR",
}

# Channels not present in the Excel but known to exist in a region
EXTRA_REGION_CHANNELS = {
    "Beyləqan SDR": {
        223936: ("Site-3 DAB+", "DAB+"),
    },
}


def _hz_to_khz(hz):
    if hz is None or pd.isna(hz):
        return None
    try:
        return int(round(float(hz) / 1000))
    except Exception:
        return None


def _parse_freq_hz(value):
    if value is None or pd.isna(value):
        return None
    s = str(value).strip()
    m = re.search(r"\d+", s.replace(",", ""))
    if m:
        return int(m.group(0))
    return None


def _parse_channel(text):
    if not text or pd.isna(text):
        return None, None
    text = str(text).strip()
    m = re.search(
        r"(-?\s*(\d+(?:\.\d+)?)\s*(?:MHs|MHS|MHz|MHZ)?\s*)", text, re.IGNORECASE
    )
    if not m:
        return text, None
    try:
        freq_mhz = float(m.group(2))
    except Exception:
        return text, None
    freq_khz = int(round(freq_mhz * 1000))
    station_part = text[: m.start()].strip()
    station_part = re.sub(r"[-\s]+$", "", station_part)
    station_part = station_part or "Unknown"
    return station_part, freq_khz


def _rtys_slug(name: str) -> str:
    safe = re.sub(r"[^\w]+", "_", name, flags=re.UNICODE).strip("_").lower()
    return safe or "unknown"


# Regioni / Cyrillic -> Latin transliteration table
_AZ_TABLE = str.maketrans(
    "əƏçÇşŞıİğĞöÖüÜ",
    "eEcCsSiIgGoOuU",
)

# Additional word-level replacements applied after character transliteration
_AZ_WORD_MAP = {
    "kenar yayim": "kenar yayim",
    "Kenar yayim": "kenar yayim",
    "İctimai": "Ictimai",
    "İran": "Iran",
    "Şuşa": "Site-8",
    "Şuşa RTYS": "Site-8 RTYS",
    "Şahdag": "Sahdag",
    "Şahyeri": "Sahyeri",
    "Daşkəsən": "Site-4b",
    "Daşkəsən RTYS": "Site-4b RTYS",
    "Türkmənistan": "Turkmenistan",
    "Gürcüstan": "Gurcustan",
    "Ermənistan": "Ermenistan",
    "Turşsu": "Turssu",
    "Gilgilçay": "Gilgilcay",
    "Lənkəran": "Lenkeran",
    "Beyləqan": "Site-3",
    "Gülüstan": "Gulustan",
    "Təpəcənnət": "Tepecennet",
    "İvanovka": "Ivanovka",
    "Yergüc": "Yerguc",
    "Şahdağ": "Sahdag",
    "Xızı": "Xizi",
    "Sirvan": "Sirvan",
    "Şirvan": "Sirvan",
    "Cibir": "Cibir",
    "Astara": "Astara",
    "Lerik": "Lerik",
    "Poylu": "Poylu",
    "A.Azərbaycan": "A.Azerbaycan",
    "Azərbaycan": "Azerbaycan",
    "Şirvan": "Sirvan",
    "Xəzər": "Xezer",
    "İctimai": "Ictimai",
    "Enerji": "Enerji",
    "Sağlam": "Saglam",
    "Dalğa": "Dalga",
    "İmedi": "Imedi",
    "Yerəvan": "Yeravan",
    "Dövlət": "Dovlet",
    "Bəynəlxalq": "Beynelxalq",
    "Vəstiyi": "Vesti",
    "Priboy": "Priboy",
    "Ulduz": "Ulduz",
    "İctimai": "Ictimai",
    "Javan": "Javan",
    "Kəpəz": "Kepez",
    "Quran": "Quran",
    "Maaref": "Maaref",
    "Fərhang": "Farhang",
    "Varzesh": "Varzesh",
    "Eqtesad": "Egtesad",
    "Payam": "Payam",
}


def _latinize(text: str) -> str:
    """Transliterate Regioni special characters to plain Latin."""
    if not text:
        return text
    result = text.translate(_AZ_TABLE)
    return result


def load_rtys_map(
    xlsx_path: Optional[Path] = None,
) -> Dict[str, Dict[str, Dict[int, str]]]:
    """
    Parse SDR-RTYS.xlsx.

    Returns: {sheet_name: {rtys_name: {frequency_khz: station_name}}}
    """
    if pd is None:
        return {}
    path = xlsx_path or RTYS_XLSX_PATH
    if not path.exists():
        return {}
    try:
        xl = pd.ExcelFile(path)
    except Exception as e:
        print(f"[RTYS] Could not load {path}: {e}")
        return {}

    region_map = {}
    for sheet in xl.sheet_names:
        try:
            df = xl.parse(sheet)
        except Exception as e:
            print(f"[RTYS] Could not parse sheet {sheet}: {e}")
            continue
        if "RTYS" not in df.columns:
            continue
        df["RTYS"] = df["RTYS"].ffill()
        df = df.dropna(subset=["RTYS"])
        rtys_map = defaultdict(dict)
        for _, row in df.iterrows():
            rtys = _latinize(str(row["RTYS"]).strip())
            if not rtys:
                continue
            ch = row.get("Tezlik, kanal")
            tezlik = row.get("Tezlik")
            freq_khz = _hz_to_khz(_parse_freq_hz(tezlik))
            station_name, parsed_freq = _parse_channel(ch)
            if station_name:
                station_name = _latinize(station_name)
            if freq_khz is None:
                freq_khz = parsed_freq
            if freq_khz is None:
                continue
            rtys_map[rtys][freq_khz] = station_name
        region_map[sheet] = dict(rtys_map)
    return region_map


def build_region_lookup(
    sheet_name: str, rtys_map: Dict[str, Dict[int, str]]
) -> Dict[int, Tuple[str, str]]:
    """
    Merge all RTYS channel maps for a region into one lookup:

        {frequency_khz: (rtys_name, station_name)}
    """
    lookup: Dict[int, Tuple[str, str]] = {}
    for rtys_name, channels in rtys_map.items():
        for freq, station in channels.items():
            if freq in lookup:
                existing_rtys, _ = lookup[freq]
                print(
                    f"[RTYS] {sheet_name}: {freq} kHz appears in both "
                    f"{existing_rtys} and {rtys_name}; keeping {existing_rtys}"
                )
                continue
            lookup[freq] = (rtys_name, station)

    # Add any manual overrides for this region
    for freq, label in EXTRA_REGION_CHANNELS.get(sheet_name, {}).items():
        if freq not in lookup:
            lookup[freq] = label

    return lookup


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------
Base = declarative_base()


class SweepData(Base):
    __tablename__ = "rf_sweep_data"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    frequency_mhz = Column(Integer, index=True)  # stored as kHz integer
    power_dbfs = Column(Float)
    floor_dbfs = Column(Float)
    snr_db = Column(Float)
    sdr_id = Column(String, index=True)
    rtys_name = Column(String, index=True)
    station_name = Column(String, index=True)


def setup_db():
    """Create the engine and ensure the table + columns exist."""
    host = f"[{DB_HOST}]" if ":" in DB_HOST else DB_HOST
    db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{host}:{DB_PORT}/{DB_NAME}"
    pool_size = max(SDR_REPLICAS * 2, 8)
    max_overflow = max(SDR_REPLICAS * 2, 10)
    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=300,
        connect_args={"connect_timeout": 5},
    )

    # Create table if missing
    Base.metadata.create_all(bind=engine)

    # Make sure columns added by the real collector exist
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE rf_sweep_data ADD COLUMN IF NOT EXISTS sdr_id VARCHAR"
        ))
        conn.execute(text(
            "ALTER TABLE rf_sweep_data ADD COLUMN IF NOT EXISTS floor_dbfs FLOAT"
        ))
        conn.execute(text(
            "ALTER TABLE rf_sweep_data ADD COLUMN IF NOT EXISTS rtys_name VARCHAR"
        ))
        conn.execute(text(
            "ALTER TABLE rf_sweep_data ADD COLUMN IF NOT EXISTS station_name VARCHAR"
        ))
        conn.commit()

    return engine


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------
def discover_csvs(folder: Path) -> List[Path]:
    """Return the sweep CSV files in the simulation folder, sorted by name."""
    if not folder.is_dir():
        return []
    return sorted(
        [
            p
            for p in folder.iterdir()
            if p.suffix.lower() == ".csv"
            and not p.name.lower().startswith("all_at_once")
        ]
    )


def sdr_id_from_filename(path: Path) -> str:
    """df_peak_final.csv -> site_1, df_Site-3_final.csv -> site_3."""
    name = path.stem
    m = re.search(r"df_([a-zA-Z0-9_]+)_final", name, re.IGNORECASE)
    if m:
        location = m.group(1).lower()
        return f"{location}_1"
    # Fallback: just append _1 to the stem
    return f"{name}_1"


def load_csv(path: Path) -> List[Dict[str, Any]]:
    """
    Load a CSV and sort rows into the original collection order.

    Handles two CSV layouts seen in the simulation folder:
      - Site-1: has 'id', 'timestamp', 'sdr_id'
      - others: has 'timestamp_local', no 'id', no 'sdr_id'

    The original id column is dropped so PostgreSQL assigns a new auto-increment id.
    """
    try:
        import pandas as pd
    except ImportError:
        pd = None

    rows = []
    if pd is not None:
        df = pd.read_csv(path)
        cols = [c.lower() for c in df.columns]

        # Find the timestamp column (some files use 'timestamp', some 'timestamp_local')
        ts_col = next(
            (c for c in df.columns if c.lower() in ("timestamp", "timestamp_local")),
            None,
        )
        if ts_col:
            df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")

        # Sort by original id if available; otherwise by timestamp; otherwise keep file order
        if "id" in df.columns:
            df = df.sort_values(by="id", ascending=True)
        elif ts_col:
            df = df.sort_values(by=ts_col, ascending=True)

        # Drop columns we will rewrite (id, sdr_id)
        df = df.drop(columns=["id", "sdr_id"], errors="ignore")
        df = df.reset_index(drop=True)
        rows = df.to_dict("records")
    else:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            return rows

        cols = [c.lower() for c in rows[0].keys()]
        ts_col = next(
            (c for c in rows[0].keys() if c.lower() in ("timestamp", "timestamp_local")),
            None,
        )

        if "id" in rows[0]:
            rows.sort(key=lambda r: int(r.get("id", 0)))
        elif ts_col:
            rows.sort(key=lambda r: str(r.get(ts_col, "")))

        for r in rows:
            if "id" in r:
                del r["id"]
            if "sdr_id" in r:
                del r["sdr_id"]

    return rows


# ---------------------------------------------------------------------------
# Worker thread — one per CSV
# ---------------------------------------------------------------------------
# How many rows to batch before committing
_BATCH_SIZE = 30

# Rough bytes per row sent over the wire (used for bandwidth estimation)
_ROW_BYTES_EST = 140


class CsvSimulator(QThread):
    # count, total, interval_ms
    row_inserted = pyqtSignal(int, int, int)
    # error message
    error = pyqtSignal(str)
    # status string: "idle" | "running" | "stopping"
    status_changed = pyqtSignal(str)

    def __init__(
        self,
        csv_path: Path,
        engine,
        sdr_id: str,
        interval_ms: int = 100,
        loop_enabled: bool = True,
        rows: Optional[List[Dict[str, Any]]] = None,
        channel_map: Optional[Dict[int, Tuple[str, str]]] = None,
        rtys_name: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.csv_path = csv_path
        self.engine = engine
        self.sdr_id = sdr_id
        self._interval_ms = max(1, interval_ms)
        self._loop_enabled = bool(loop_enabled)
        self._rows_source: Optional[List[Dict[str, Any]]] = rows
        self.channel_map: Optional[Dict[int, str]] = channel_map
        self.rtys_name: Optional[str] = rtys_name
        self._rows: List[Dict[str, Any]] = []
        self._stop_requested = False
        self._count = 0
        self._bytes_estimate = 0
        self._started_at: Optional[float] = None
        self._lock = threading.Lock()

    def set_interval_ms(self, ms: int):
        with self._lock:
            self._interval_ms = max(1, int(ms))

    def interval_ms(self) -> int:
        with self._lock:
            return self._interval_ms

    def set_loop_enabled(self, enabled: bool):
        with self._lock:
            self._loop_enabled = bool(enabled)

    def loop_enabled(self) -> bool:
        with self._lock:
            return self._loop_enabled

    def stop(self):
        self._stop_requested = True
        self.status_changed.emit("stopping")
        self.wait(500)

    def run(self):
        self._stop_requested = False
        self._started_at = time.time()
        self.status_changed.emit("running")

        if self._rows_source is not None:
            self._rows = self._rows_source
        else:
            try:
                self._rows = load_csv(self.csv_path)
            except Exception as e:
                self.error.emit(f"Failed to load {self.csv_path.name}: {e}")
                self.status_changed.emit("idle")
                return

        if not self._rows:
            self.error.emit(f"No rows found in {self.csv_path.name}")
            self.status_changed.emit("idle")
            return

        total = len(self._rows)
        self._count = 0

        try:
            while not self._stop_requested:
                batch = []
                for row in self._rows:
                    if self._stop_requested:
                        break

                    ts = datetime.datetime.now()
                    frequency = int(row.get("frequency_mhz", 0))
                    rtys_name = None
                    station_name = None
                    if self.channel_map:
                        mapping = self.channel_map.get(frequency)
                        if mapping:
                            rtys_name, station_name = mapping

                    batch.append({
                        "timestamp": ts,
                        "frequency_mhz": frequency,
                        "power_dbfs": float(row.get("power_dbfs", 0)),
                        "floor_dbfs": float(row.get("floor_dbfs", 0)),
                        "snr_db": float(row.get("snr_db", 0)),
                        "sdr_id": self.sdr_id,
                        "rtys_name": rtys_name,
                        "station_name": station_name,
                    })

                    if len(batch) >= _BATCH_SIZE:
                        self._flush_batch(batch)
                        batch = []
                        # Sleep between batches — responsive to stop
                        self._interruptible_sleep()

                # Flush remaining rows in last partial batch
                if batch and not self._stop_requested:
                    self._flush_batch(batch)
                    batch = []

                if self._stop_requested:
                    break
                if not self.loop_enabled():
                    break
                # Loop back to beginning for continuous simulation
        finally:
            self.status_changed.emit("idle")

    def _flush_batch(self, batch: list):
        """Insert a batch of rows in a single transaction using raw SQL."""
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO rf_sweep_data
                            (timestamp, frequency_mhz, power_dbfs, floor_dbfs, snr_db, sdr_id, rtys_name, station_name)
                        VALUES
                            (:timestamp, :frequency_mhz, :power_dbfs, :floor_dbfs, :snr_db, :sdr_id, :rtys_name, :station_name)
                    """),
                    batch,
                )
            self._count += len(batch)
            self._bytes_estimate += len(batch) * _ROW_BYTES_EST
            self.row_inserted.emit(self._count, len(self._rows), self.interval_ms())
        except Exception as e:
            self.error.emit(f"DB batch insert error: {e}")
            time.sleep(0.5)

    def _interruptible_sleep(self):
        """Sleep for the configured interval, checking stop flag every 100ms."""
        interval = self.interval_ms() / 1000.0
        slept = 0.0
        while slept < interval and not self._stop_requested:
            chunk = min(0.1, interval - slept)
            time.sleep(chunk)
            slept += chunk


# ---------------------------------------------------------------------------
# UI widgets
# ---------------------------------------------------------------------------
class StatusBadge(QtWidgets.QLabel):
    """Small pill label that shows Idle / Running / Stopping."""

    def __init__(self, parent=None):
        super().__init__("Idle", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_color("#6b7280")

    def set_status(self, status: str):
        text = status.capitalize()
        color = {
            "idle": "#6b7280",
            "running": "#2563eb",
            "stopping": "#d97706",
        }.get(status, "#6b7280")
        self.setText(text)
        self._set_color(color)

    def _set_color(self, color: str):
        self.setStyleSheet(
            f"background-color: {color}; color: #ffffff; font-weight: 600; "
            f"padding: 3px 10px; border-radius: 10px; font-size: 11px;"
        )


class SimulatorCard(QtWidgets.QFrame):
    started = pyqtSignal()
    stopped = pyqtSignal()
    rows_changed = pyqtSignal(int)

    def __init__(
        self,
        csv_path: Path,
        engine,
        sdr_id: Optional[str] = None,
        rows: Optional[List[Dict[str, Any]]] = None,
        channel_map: Optional[Dict[int, Tuple[str, str]]] = None,
        rtys_name: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.csv_path = csv_path
        self.engine = engine
        self.sdr_id = sdr_id or sdr_id_from_filename(csv_path)
        self.preloaded_rows = rows
        self.channel_map = channel_map
        self.rtys_name = rtys_name
        self.worker: Optional[CsvSimulator] = None

        self.setObjectName("card")
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)

        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 14, 16, 14)

        # Header row: location name + status badge
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)

        location = self.sdr_id.rsplit("_", 1)[0].capitalize()
        title_text = location
        if self.channel_map:
            sdr_text = f"SDR ID: {self.sdr_id}  •  {len(self.channel_map)} known channels"
        else:
            sdr_text = f"SDR ID: {self.sdr_id}"
        self.title_label = QtWidgets.QLabel(title_text)
        self.title_label.setObjectName("cardTitle")

        self.status_badge = StatusBadge(self)

        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.status_badge)
        layout.addLayout(header)

        # SDR ID
        self.sdr_label = QtWidgets.QLabel(sdr_text)
        self.sdr_label.setObjectName("meta")
        layout.addWidget(self.sdr_label)

        # Progress and stats
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")

        self.stats_label = QtWidgets.QLabel("Rows sent: 0")
        self.stats_label.setObjectName("meta")

        layout.addWidget(self.progress)
        layout.addWidget(self.stats_label)

        # Controls row
        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(16)

        interval_layout = QtWidgets.QHBoxLayout()
        interval_layout.setSpacing(6)
        interval_lbl = QtWidgets.QLabel("Send interval:")
        interval_lbl.setObjectName("meta")
        self.interval_spin = QtWidgets.QSpinBox()
        self.interval_spin.setRange(1, 10000)
        self.interval_spin.setValue(1000)
        self.interval_spin.setSuffix(" ms")
        self.interval_spin.setToolTip("Delay between inserting each row")
        self.interval_spin.valueChanged.connect(self._on_interval_changed)
        interval_layout.addWidget(interval_lbl)
        interval_layout.addWidget(self.interval_spin)

        self.loop_checkbox = QtWidgets.QCheckBox("Loop playback")
        self.loop_checkbox.setChecked(True)
        self.loop_checkbox.setToolTip("Restart the CSV when it finishes")
        self.loop_checkbox.setObjectName("meta")
        self.loop_checkbox.stateChanged.connect(self._on_loop_changed)

        controls.addLayout(interval_layout)
        controls.addWidget(self.loop_checkbox)
        controls.addStretch()
        layout.addLayout(controls)

        # Buttons row
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.start_btn = QtWidgets.QPushButton("Start")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        self.start_btn.clicked.connect(self._start)

        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        layout.addLayout(btn_layout)

    def _on_interval_changed(self, value: int):
        if self.worker is not None:
            self.worker.set_interval_ms(value)

    def _on_loop_changed(self, state: int):
        if self.worker is not None:
            self.worker.set_loop_enabled(state == Qt.CheckState.Checked.value)

    def _start(self):
        if self.worker is not None and self.worker.isRunning():
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.interval_spin.setEnabled(False)
        self.loop_checkbox.setEnabled(False)
        self.status_badge.set_status("running")

        self.worker = CsvSimulator(
            csv_path=self.csv_path,
            engine=self.engine,
            sdr_id=self.sdr_id,
            interval_ms=self.interval_spin.value(),
            loop_enabled=self.loop_checkbox.isChecked(),
            rows=self.preloaded_rows,
            channel_map=self.channel_map,
            rtys_name=self.rtys_name,
            parent=self,
        )
        self.worker.row_inserted.connect(self._on_row)
        self.worker.error.connect(self._on_error)
        self.worker.status_changed.connect(self._on_status)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()
        self.started.emit()

    def _stop(self):
        self.stop_btn.setEnabled(False)
        if self.worker is not None:
            self.worker.stop()

    def _on_status(self, status: str):
        self.status_badge.set_status(status)

    def _on_row(self, count: int, total: int, interval_ms: int):
        pct = int((count % total) / total * 100) if total else 0
        self.progress.setValue(pct)
        self.stats_label.setText(
            f"Rows sent: {count:,}  •  {interval_ms} ms/row"
        )
        self.rows_changed.emit(count)

    def _on_error(self, msg: str):
        QtWidgets.QMessageBox.critical(self, f"Error — {self.csv_path.name}", msg)
        self._stop()

    def _on_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.interval_spin.setEnabled(True)
        self.loop_checkbox.setEnabled(True)
        self.status_badge.set_status("idle")
        self.stopped.emit()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.cards: List[SimulatorCard] = []
        self._sim_start: Optional[float] = None
        self.setWindowTitle("SDR Data Simulator")
        self.setMinimumSize(920, 620)
        self._build_ui()
        self._apply_styles()
        self._update_status_bar()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(24, 20, 24, 20)

        # Title + DB target
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(16)

        title = QtWidgets.QLabel("SDR Data Simulator")
        title.setObjectName("appTitle")
        subtitle = QtWidgets.QLabel(
            "Replay captured CSV sweeps into PostgreSQL as live SDR feeds"
        )
        subtitle.setObjectName("subtitle")

        title_col = QtWidgets.QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()

        display_host = f"[{DB_HOST}]" if ":" in DB_HOST else DB_HOST
        db_label = QtWidgets.QLabel(f"{display_host}:{DB_PORT}/{DB_NAME}")
        db_label.setObjectName("meta")
        db_label.setToolTip("Database target")
        header.addWidget(db_label)

        main_layout.addLayout(header)

        # Toolbar
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(12)

        global_interval_lbl = QtWidgets.QLabel("All intervals:")
        global_interval_lbl.setObjectName("meta")
        self.global_interval_spin = QtWidgets.QSpinBox()
        self.global_interval_spin.setRange(1, 10000)
        self.global_interval_spin.setValue(1000)
        self.global_interval_spin.setSuffix(" ms")
        self.global_interval_spin.setToolTip("Set interval for every CSV at once")
        self.global_interval_spin.valueChanged.connect(
            self._on_global_interval_changed
        )

        toolbar.addWidget(global_interval_lbl)
        toolbar.addWidget(self.global_interval_spin)
        toolbar.addStretch()

        self.start_all_btn = QtWidgets.QPushButton("Run All")
        self.start_all_btn.setObjectName("startBtn")
        self.start_all_btn.setCursor(
            QtGui.QCursor(Qt.CursorShape.PointingHandCursor)
        )
        self.start_all_btn.clicked.connect(self._start_all)

        self.stop_all_btn = QtWidgets.QPushButton("Stop All")
        self.stop_all_btn.setObjectName("stopBtn")
        self.stop_all_btn.setCursor(
            QtGui.QCursor(Qt.CursorShape.PointingHandCursor)
        )
        self.stop_all_btn.clicked.connect(self._stop_all)

        toolbar.addWidget(self.start_all_btn)
        toolbar.addWidget(self.stop_all_btn)

        main_layout.addLayout(toolbar)

        # Divider
        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        divider.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        divider.setObjectName("divider")
        main_layout.addWidget(divider)

        # Cards scroll area
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setObjectName("scrollArea")

        container = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(container)
        self.cards_layout.setSpacing(14)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)

        folder = Path(__file__).resolve().parent / "simulation"
        csvs = discover_csvs(folder)

        if not csvs:
            empty = QtWidgets.QLabel(
                f"No CSV files found in {folder}.\n"
                "Add files like df_peak_final.csv and restart."
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setObjectName("empty")
            self.cards_layout.addWidget(empty)
        else:
            rtys_workbook = load_rtys_map()
            rows_cache = {path: load_csv(path) for path in csvs}
            region_lookups: Dict[Path, Dict[int, Tuple[str, str]]] = {}
            for path in csvs:
                location = sdr_id_from_filename(path).rsplit("_", 1)[0]
                sheet_name = CSV_TO_SHEET.get(location)
                sheet_rtys = rtys_workbook.get(sheet_name) if sheet_name else None
                region_lookup: Dict[int, Tuple[str, str]] = {}
                if sheet_rtys:
                    region_lookup = build_region_lookup(sheet_name, sheet_rtys)
                region_lookups[path] = region_lookup

            total_sdrs = SDR_REPLICAS if SDR_REPLICAS > 0 else len(csvs)
            used_sdr_ids = set()
            for idx in range(total_sdrs):
                path = csvs[idx % len(csvs)]
                location = sdr_id_from_filename(path).rsplit("_", 1)[0]
                replica = (idx // len(csvs)) + 1
                sdr_id = f"{location}_{replica}"
                used_sdr_ids.add(sdr_id)
                card = SimulatorCard(
                    path,
                    self.engine,
                    sdr_id=sdr_id,
                    rows=rows_cache[path],
                    channel_map=region_lookups[path],
                )
                card.started.connect(self._update_status_bar)
                card.stopped.connect(self._update_status_bar)
                card.rows_changed.connect(self._update_status_bar)
                self.cards.append(card)
                self.cards_layout.addWidget(card)

            # Always add a site_2 replica using the Site-1 CSV if not already present.
            peak_path = next(
                (p for p in csvs if sdr_id_from_filename(p).rsplit("_", 1)[0] == "Site-1"), None
            )
            if peak_path and "site_2" not in used_sdr_ids:
                site_2_card = SimulatorCard(
                    peak_path,
                    self.engine,
                    sdr_id="site_2",
                    rows=rows_cache[peak_path],
                    channel_map=region_lookups[peak_path],
                )
                site_2_card.started.connect(self._update_status_bar)
                site_2_card.stopped.connect(self._update_status_bar)
                site_2_card.rows_changed.connect(self._update_status_bar)
                self.cards.append(site_2_card)
                self.cards_layout.addWidget(site_2_card)

        self.cards_layout.addStretch()
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # Status bar
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().showMessage("Ready")

    def _start_all(self):
        for card in self.cards:
            if card.worker is None or not card.worker.isRunning():
                card._start()

    def _on_global_interval_changed(self, value: int):
        for card in self.cards:
            card.interval_spin.setValue(value)

    def _stop_all(self):
        for card in self.cards:
            card._stop()

    def _update_status_bar(self):
        running = sum(
            1 for c in self.cards if c.worker is not None and c.worker.isRunning()
        )
        total_rows = sum(c.worker._count if c.worker else 0 for c in self.cards)
        total_bytes = sum(
            c.worker._bytes_estimate if c.worker else 0 for c in self.cards
        )

        if total_rows > 0 and self._sim_start is None:
            self._sim_start = time.time()

        if running and self._sim_start is not None:
            elapsed = time.time() - self._sim_start
            rows_per_sec = total_rows / elapsed if elapsed > 0 else 0.0
            kb_per_sec = (total_bytes / elapsed / 1024) if elapsed > 0 else 0.0
            self.statusBar().showMessage(
                f"{running} simulation{'s' if running > 1 else ''} running  •  "
                f"{total_rows:,} rows  •  {rows_per_sec:.1f} rows/s  •  {kb_per_sec:.1f} KB/s"
            )
        else:
            self.statusBar().showMessage(f"Ready  •  {total_rows:,} rows sent")

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Segoe UI", "Roboto", "Helvetica Neue", sans-serif;
                font-size: 13px;
                color: #111827;
            }
            QMainWindow {
                background-color: #f3f4f6;
            }
            QStatusBar {
                background-color: #ffffff;
                color: #6b7280;
                border-top: 1px solid #e5e7eb;
            }
            QStatusBar::item {
                border: none;
            }
            #appTitle {
                font-size: 24px;
                font-weight: 700;
                color: #111827;
            }
            #subtitle {
                font-size: 13px;
                color: #4b5563;
            }
            #footer {
                font-size: 12px;
                color: #6b7280;
            }
            #empty {
                font-size: 15px;
                color: #6b7280;
                padding: 40px;
            }
            #scrollArea {
                border: none;
                background-color: transparent;
            }
            #divider {
                background-color: #e5e7eb;
                max-height: 1px;
            }
            #card {
                background-color: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                padding: 0px;
            }
            #cardTitle {
                font-size: 16px;
                font-weight: 600;
                color: #111827;
            }
            #meta {
                font-size: 12px;
                color: #6b7280;
            }
            QPushButton {
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
                color: #111827;
                background-color: #e5e7eb;
            }
            QPushButton:hover {
                background-color: #d1d5db;
            }
            QPushButton:pressed {
                background-color: #9ca3af;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #9ca3af;
            }
            #startBtn {
                background-color: #2563eb;
                color: #ffffff;
            }
            #startBtn:hover {
                background-color: #1d4ed8;
            }
            #startBtn:pressed {
                background-color: #1e40af;
            }
            #stopBtn {
                background-color: #dc2626;
                color: #ffffff;
            }
            #stopBtn:hover {
                background-color: #b91c1c;
            }
            #stopBtn:pressed {
                background-color: #991b1b;
            }
            QProgressBar {
                border: none;
                border-radius: 4px;
                background-color: #e5e7eb;
                text-align: center;
                color: #111827;
                font-size: 11px;
                height: 16px;
            }
            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 4px;
            }
            QSpinBox {
                background-color: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 4px 6px;
                color: #111827;
                min-width: 70px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: #f3f4f6;
                border: none;
                margin: 1px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #e5e7eb;
            }
            QCheckBox {
                color: #111827;
            }
            QScrollBar:vertical {
                background-color: #f3f4f6;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background-color: #d1d5db;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #9ca3af;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                border: none;
            }
            """
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _apply_light_palette(app: QtWidgets.QApplication):
    """Set a standard bright palette for Fusion."""
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#f3f4f6"))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#111827"))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#ffffff"))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#f9fafb"))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#111827"))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor("#e5e7eb"))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#111827"))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#2563eb"))
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#ffffff"))
    app.setPalette(palette)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    _apply_light_palette(app)

    try:
        engine = setup_db()
    except Exception as e:
        QtWidgets.QMessageBox.critical(
            None,
            "Database connection failed",
            f"Could not connect to {DB_HOST}:{DB_PORT}/{DB_NAME}.\n\n{e}",
        )
        sys.exit(1)

    window = MainWindow(engine)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
