#!/usr/bin/env python3
"""Simple PyQt6 editor for sdr_config.json.

Install PyQt6 first:
    python -m pip install PyQt6
Then run:
    python sdr_config_editor.py
"""
import json
import sys
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

CONFIG_PATH = Path(__file__).parent / "sdr_config.json"
THRESHOLDS_PATH = Path(__file__).parent / 'sdr_thresholds.json'


class SDRConfigEditor(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SDR Config Editor")
        self.resize(1200, 800)

        self.data: dict = {"sdrs": []}
        self.current_sdr: dict | None = None
        self.current_rtys: str | None = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)

        # ---- Left: SDR / RTYS selectors ----
        left_pane = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_pane)

        # SDRs
        sdr_box = QtWidgets.QGroupBox("Regions (SDRs)")
        sdr_layout = QtWidgets.QVBoxLayout(sdr_box)
        self.sdr_list = QtWidgets.QListWidget()
        self.sdr_list.currentRowChanged.connect(self.on_sdr_changed)
        sdr_layout.addWidget(self.sdr_list)
        sdr_btn = QtWidgets.QHBoxLayout()
        self.add_sdr_btn = QtWidgets.QPushButton("Add")
        self.del_sdr_btn = QtWidgets.QPushButton("Delete")
        self.ren_sdr_btn = QtWidgets.QPushButton("Rename")
        self.add_sdr_btn.clicked.connect(self.add_sdr)
        self.del_sdr_btn.clicked.connect(self.delete_sdr)
        self.ren_sdr_btn.clicked.connect(self.rename_sdr)
        sdr_btn.addWidget(self.add_sdr_btn)
        sdr_btn.addWidget(self.del_sdr_btn)
        sdr_btn.addWidget(self.ren_sdr_btn)
        sdr_layout.addLayout(sdr_btn)
        left_layout.addWidget(sdr_box)

        # RTYS
        rtys_box = QtWidgets.QGroupBox("RTYS")
        rtys_layout = QtWidgets.QVBoxLayout(rtys_box)
        self.rtys_list = QtWidgets.QListWidget()
        self.rtys_list.currentRowChanged.connect(self.on_rtys_changed)
        rtys_layout.addWidget(self.rtys_list)
        rtys_btn = QtWidgets.QHBoxLayout()
        self.add_rtys_btn = QtWidgets.QPushButton("Add")
        self.del_rtys_btn = QtWidgets.QPushButton("Delete")
        self.ren_rtys_btn = QtWidgets.QPushButton("Rename")
        self.add_rtys_btn.clicked.connect(self.add_rtys)
        self.del_rtys_btn.clicked.connect(self.delete_rtys)
        self.ren_rtys_btn.clicked.connect(self.rename_rtys)
        rtys_btn.addWidget(self.add_rtys_btn)
        rtys_btn.addWidget(self.del_rtys_btn)
        rtys_btn.addWidget(self.ren_rtys_btn)
        rtys_layout.addLayout(rtys_btn)
        left_layout.addWidget(rtys_box)

        # ---- Right: stations + form ----
        right_pane = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_pane)

        # Stations table
        station_box = QtWidgets.QGroupBox("Stations")
        station_layout = QtWidgets.QVBoxLayout(station_box)
        self.station_table = QtWidgets.QTableWidget()
        self.station_table.setColumnCount(5)
        self.station_table.setHorizontalHeaderLabels(
            ["Frequency (MHz)", "Name", "RTYS", "DAB", "DAB Freq (kHz)"]
        )
        self.station_table.horizontalHeader().setStretchLastSection(True)
        self.station_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.station_table.itemSelectionChanged.connect(self.on_station_selected)
        station_layout.addWidget(self.station_table)
        station_btn = QtWidgets.QHBoxLayout()
        self.add_station_btn = QtWidgets.QPushButton("Add station")
        self.del_station_btn = QtWidgets.QPushButton("Delete station")
        self.add_station_btn.clicked.connect(self.add_station)
        self.del_station_btn.clicked.connect(self.delete_station)
        station_btn.addWidget(self.add_station_btn)
        station_btn.addWidget(self.del_station_btn)
        station_btn.addStretch()
        station_layout.addLayout(station_btn)
        right_layout.addWidget(station_box, stretch=2)

        # Edit form
        form_box = QtWidgets.QGroupBox("Edit selected station")
        form_layout = QtWidgets.QFormLayout(form_box)
        self.freq_spin = QtWidgets.QDoubleSpinBox()
        self.freq_spin.setRange(76.0, 108.0)
        self.freq_spin.setDecimals(1)
        self.freq_spin.setSingleStep(0.1)
        self.name_edit = QtWidgets.QLineEdit()
        self.rtys_edit = QtWidgets.QLineEdit()
        self.dab_check = QtWidgets.QCheckBox()
        self.dab_check.stateChanged.connect(self.toggle_dab)
        self.dab_freq_spin = QtWidgets.QSpinBox()
        self.dab_freq_spin.setRange(170000, 240000)
        self.dab_freq_spin.setEnabled(False)
        self.update_station_btn = QtWidgets.QPushButton("Update station")
        self.update_station_btn.clicked.connect(self.update_station)

        form_layout.addRow("Frequency (MHz):", self.freq_spin)
        form_layout.addRow("Name:", self.name_edit)
        form_layout.addRow("RTYS:", self.rtys_edit)
        form_layout.addRow("DAB:", self.dab_check)
        form_layout.addRow("DAB freq (kHz):", self.dab_freq_spin)
        form_layout.addRow(self.update_station_btn)
        right_layout.addWidget(form_box, stretch=1)

        # ---- Toolbar ----
        toolbar = QtWidgets.QToolBar()
        self.addToolBar(toolbar)
        save_action = QtGui.QAction("Save to file", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_config)
        reload_action = QtGui.QAction("Reload file", self)
        reload_action.triggered.connect(self.load_config)
        toolbar.addAction(save_action)
        toolbar.addAction(reload_action)
        thresholds_action = QtGui.QAction('Edit thresholds', self)
        thresholds_action.triggered.connect(self.open_thresholds_editor)
        toolbar.addAction(thresholds_action)

        # ---- Splitter ----
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(left_pane)
        splitter.addWidget(right_pane)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter)

        self.load_config()

    def load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {"sdrs": []}
        except json.JSONDecodeError as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Invalid JSON: {e}")
            self.data = {"sdrs": []}
        self.populate_sdr_list()

    def save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            QtWidgets.QMessageBox.information(
                self, "Saved", f"Saved to {CONFIG_PATH.name}"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Could not save: {e}")

    # ------------------------------------------------------------------
    # SDR list
    # ------------------------------------------------------------------
    def populate_sdr_list(self):
        self.sdr_list.clear()
        for sdr in self.data["sdrs"]:
            item = QtWidgets.QListWidgetItem(f"{sdr['id']} ({sdr.get('label', '')})")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, sdr)
            self.sdr_list.addItem(item)
        if self.sdr_list.count():
            self.sdr_list.setCurrentRow(0)

    def on_sdr_changed(self):
        self.current_sdr = None
        self.rtys_list.clear()
        self.station_table.setRowCount(0)
        item = self.sdr_list.currentItem()
        if not item:
            return
        self.current_sdr = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self.populate_rtys_list()

    def add_sdr(self):
        text, ok = QtWidgets.QInputDialog.getText(self, "Add SDR", "SDR ID (e.g. site_2):")
        if not ok or not text.strip():
            return
        new_sdr = {"id": text.strip(), "label": text.strip(), "stations": []}
        self.data["sdrs"].append(new_sdr)
        self.populate_sdr_list()
        # select it
        for i in range(self.sdr_list.count()):
            if self.sdr_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) == new_sdr:
                self.sdr_list.setCurrentRow(i)
                break

    def delete_sdr(self):
        if not self.current_sdr:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Delete SDR", f"Delete '{self.current_sdr['id']}'?"
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.data["sdrs"].remove(self.current_sdr)
            self.populate_sdr_list()

    def rename_sdr(self):
        if not self.current_sdr:
            return
        label, ok = QtWidgets.QInputDialog.getText(
            self, "Rename SDR", "Label:", text=self.current_sdr.get("label", "")
        )
        if ok:
            self.current_sdr["label"] = label.strip()
            self.populate_sdr_list()

    # ------------------------------------------------------------------
    # RTYS list
    # ------------------------------------------------------------------
    def rtys_for_current_sdr(self) -> list[str]:
        if not self.current_sdr:
            return []
        seen = set()
        ordered = []
        for st in self.current_sdr.get("stations", []):
            r = st.get("rtys", "")
            if r not in seen:
                seen.add(r)
                ordered.append(r)
        return ordered

    def populate_rtys_list(self):
        self.rtys_list.clear()
        for rtys in self.rtys_for_current_sdr():
            item = QtWidgets.QListWidgetItem(rtys)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, rtys)
            self.rtys_list.addItem(item)
        if self.rtys_list.count():
            self.rtys_list.setCurrentRow(0)

    def on_rtys_changed(self):
        self.current_rtys = None
        item = self.rtys_list.currentItem()
        if item:
            self.current_rtys = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self.populate_station_table()

    def add_rtys(self):
        if not self.current_sdr:
            QtWidgets.QMessageBox.warning(self, "No SDR", "Select a region first.")
            return
        text, ok = QtWidgets.QInputDialog.getText(self, "Add RTYS", "RTYS name:")
        if not ok or not text.strip():
            return
        # create one placeholder station to make the group visible
        self.current_sdr.setdefault("stations", []).append(
            {"freq": 88.0, "name": "New station", "rtys": text.strip()}
        )
        self.populate_rtys_list()
        # select the new rtys
        for i in range(self.rtys_list.count()):
            if self.rtys_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) == text.strip():
                self.rtys_list.setCurrentRow(i)
                break

    def delete_rtys(self):
        if not self.current_sdr or not self.current_rtys:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Delete RTYS",
            f"Delete all stations in RTYS '{self.current_rtys}'?"
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            stations = self.current_sdr.get("stations", [])
            self.current_sdr["stations"] = [
                s for s in stations if s.get("rtys") != self.current_rtys
            ]
            self.populate_rtys_list()

    def rename_rtys(self):
        if not self.current_sdr or not self.current_rtys:
            return
        new_name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename RTYS", "New RTYS name:", text=self.current_rtys
        )
        if not ok or not new_name.strip() or new_name.strip() == self.current_rtys:
            return
        for s in self.current_sdr.get("stations", []):
            if s.get("rtys") == self.current_rtys:
                s["rtys"] = new_name.strip()
        self.current_rtys = new_name.strip()
        self.populate_rtys_list()
        self.populate_station_table()

    # ------------------------------------------------------------------
    # Stations
    # ------------------------------------------------------------------
    def stations_for_current_rtys(self) -> list[dict]:
        if not self.current_sdr or not self.current_rtys:
            return []
        return [
            s for s in self.current_sdr.get("stations", [])
            if s.get("rtys") == self.current_rtys
        ]

    def populate_station_table(self):
        self.station_table.setRowCount(0)
        self.clear_form()
        if not self.current_sdr or not self.current_rtys:
            return
        stations = self.stations_for_current_rtys()
        self.station_table.setRowCount(len(stations))
        for row, st in enumerate(stations):
            self.station_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(st.get("freq", ""))))
            self.station_table.setItem(row, 1, QtWidgets.QTableWidgetItem(st.get("name", "")))
            self.station_table.setItem(row, 2, QtWidgets.QTableWidgetItem(st.get("rtys", "")))
            dab = QtWidgets.QTableWidgetItem("Yes" if st.get("dab") else "No")
            self.station_table.setItem(row, 3, dab)
            dab_freq = str(st.get("dab_freq_khz", "")) if st.get("dab") else ""
            self.station_table.setItem(row, 4, QtWidgets.QTableWidgetItem(dab_freq))
            # keep station dict on first column item
            self.station_table.item(row, 0).setData(QtCore.Qt.ItemDataRole.UserRole, st)

    def on_station_selected(self):
        row = self.station_table.currentRow()
        if row < 0:
            self.clear_form()
            return
        st = self.station_table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)
        self.freq_spin.setValue(float(st.get("freq", 88.0)))
        self.name_edit.setText(st.get("name", ""))
        self.rtys_edit.setText(st.get("rtys", ""))
        dab = st.get("dab", False)
        self.dab_check.setChecked(dab)
        self.dab_freq_spin.setValue(st.get("dab_freq_khz", 223936))
        self.dab_freq_spin.setEnabled(dab)

    def clear_form(self):
        self.freq_spin.setValue(88.0)
        self.name_edit.clear()
        self.rtys_edit.clear()
        self.dab_check.setChecked(False)
        self.dab_freq_spin.setValue(223936)
        self.dab_freq_spin.setEnabled(False)

    def toggle_dab(self, state):
        self.dab_freq_spin.setEnabled(state == QtCore.Qt.CheckState.Checked.value)

    def update_station(self):
        row = self.station_table.currentRow()
        if row < 0:
            return
        st = self.station_table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)
        old_rtys = st.get("rtys", "")

        st["freq"] = round(self.freq_spin.value(), 1)
        st["name"] = self.name_edit.text().strip()
        st["rtys"] = self.rtys_edit.text().strip() or old_rtys
        st["dab"] = self.dab_check.isChecked()
        if st["dab"]:
            st["dab_freq_khz"] = self.dab_freq_spin.value()
        else:
            st.pop("dab_freq_khz", None)

        # if rtys changed, the station may need to move groups in the UI
        if st.get("rtys") != old_rtys:
            self.populate_rtys_list()
            # try to keep selection on the new rtys
            for i in range(self.rtys_list.count()):
                if self.rtys_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) == st.get("rtys"):
                    self.rtys_list.setCurrentRow(i)
                    break
        else:
            self.populate_station_table()
            self.station_table.setCurrentRow(row)

    def add_station(self):
        if not self.current_sdr:
            QtWidgets.QMessageBox.warning(self, "No SDR", "Select a region first.")
            return
        if not self.current_rtys:
            QtWidgets.QMessageBox.warning(self, "No RTYS", "Select or add an RTYS first.")
            return
        new_station = {
            "freq": 88.0,
            "name": "New station",
            "rtys": self.current_rtys,
        }
        self.current_sdr.setdefault("stations", []).append(new_station)
        self.populate_station_table()
        # select the new row
        for r in range(self.station_table.rowCount()):
            if self.station_table.item(r, 0).data(QtCore.Qt.ItemDataRole.UserRole) == new_station:
                self.station_table.setCurrentCell(r, 0)
                break

    def delete_station(self):
        row = self.station_table.currentRow()
        if row < 0:
            return
        st = self.station_table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)
        reply = QtWidgets.QMessageBox.question(
            self, "Delete station", f"Delete station '{st.get('name')}'?"
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.current_sdr["stations"].remove(st)
            self.populate_rtys_list()
            self.populate_station_table()

    def open_thresholds_editor(self):
        dlg = ThresholdsEditorDialog(self)
        dlg.exec()


class ThresholdsEditorDialog(QtWidgets.QDialog):
    FIELDS = [
        ('db_min', 'Display min (dB)'),
        ('db_max', 'Display max (dB)'),
        ('noise_floor', 'Noise floor (dB)'),
        ('signal_threshold', 'Signal threshold (dB)'),
        ('snr_threshold', 'SNR threshold (dB)'),
        ('strong_signal', 'Strong signal (dB)'),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Edit SDR Thresholds')
        self.resize(700, 500)
        self.thresholds = {}
        self.cfg = {'sdrs': []}
        self.sdr_ids = []
        self.current_sdr = None
        self.current_rtys = None
        self._block_update = False
        self.build_ui()
        self.load_thresholds()

    def build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()

        self.sdr_list = QtWidgets.QListWidget()
        self.sdr_list.currentRowChanged.connect(self.on_sdr_changed)
        left.addWidget(QtWidgets.QLabel('Region:'))
        left.addWidget(self.sdr_list)

        self.rtys_list = QtWidgets.QListWidget()
        self.rtys_list.currentRowChanged.connect(self.on_rtys_changed)
        left.addWidget(QtWidgets.QLabel('RTYS:'))
        left.addWidget(self.rtys_list)

        save_btn = QtWidgets.QPushButton('Save thresholds')
        save_btn.clicked.connect(self.save_thresholds)
        left.addWidget(save_btn)

        range_box = QtWidgets.QGroupBox('Set display range for all')
        range_layout = QtWidgets.QFormLayout(range_box)
        self.global_db_min = QtWidgets.QDoubleSpinBox()
        self.global_db_min.setRange(-150.0, 0.0)
        self.global_db_min.setDecimals(1)
        self.global_db_min.setValue(-90.0)
        self.global_db_max = QtWidgets.QDoubleSpinBox()
        self.global_db_max.setRange(-150.0, 0.0)
        self.global_db_max.setDecimals(1)
        self.global_db_max.setValue(-35.0)
        range_layout.addRow('Display min (dB):', self.global_db_min)
        range_layout.addRow('Display max (dB):', self.global_db_max)
        apply_range_btn = QtWidgets.QPushButton('Apply to all SDRs & RTYS')
        apply_range_btn.clicked.connect(self.apply_global_display_range)
        range_layout.addRow(apply_range_btn)
        left.addWidget(range_box)
        layout.addLayout(left, 1)

        form_widget = QtWidgets.QWidget()
        form_layout = QtWidgets.QFormLayout(form_widget)
        self.spin_boxes = {}
        for key, label in self.FIELDS:
            spin = QtWidgets.QDoubleSpinBox()
            if key == 'snr_threshold':
                spin.setRange(0.0, 60.0)
            else:
                spin.setRange(-150.0, 0.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.1)
            spin.valueChanged.connect(self.on_value_changed)
            self.spin_boxes[key] = spin
            form_layout.addRow(label, spin)
        layout.addWidget(form_widget, 2)

    def load_thresholds(self):
        try:
            with open(THRESHOLDS_PATH, 'r', encoding='utf-8') as f:
                self.thresholds = json.load(f)
        except FileNotFoundError:
            self.thresholds = {}
        except json.JSONDecodeError as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Invalid thresholds JSON: {e}')
            self.thresholds = {}

        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                self.cfg = json.load(f)
        except Exception:
            self.cfg = {'sdrs': []}

        self.sdr_ids = [s['id'] for s in self.cfg.get('sdrs', [])]
        if not self.sdr_ids:
            self.sdr_ids = list(self.thresholds.keys())

        self.sdr_list.clear()
        for sid in self.sdr_ids:
            item = QtWidgets.QListWidgetItem(sid)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, sid)
            self.sdr_list.addItem(item)
        if self.sdr_list.count():
            self.sdr_list.setCurrentRow(0)

    def on_sdr_changed(self):
        item = self.sdr_list.currentItem()
        if not item:
            self.current_sdr = None
            return
        self.current_sdr = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self.populate_rtys_list()

    def populate_rtys_list(self):
        self.rtys_list.clear()
        if not self.current_sdr:
            return
        rtys = set()
        for sdr in self.cfg.get('sdrs', []):
            if sdr.get('id') == self.current_sdr:
                for st in sdr.get('stations', []):
                    r = st.get('rtys', '')
                    if r:
                        rtys.add(r)
        for r in self.thresholds.get(self.current_sdr, {}).get('rtys', {}).keys():
            rtys.add(r)
        ordered = ['default'] + sorted(rtys)
        for r in ordered:
            item = QtWidgets.QListWidgetItem(r)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, r)
            self.rtys_list.addItem(item)
        if self.rtys_list.count():
            self.rtys_list.setCurrentRow(0)

    def on_rtys_changed(self):
        item = self.rtys_list.currentItem()
        if not item:
            self.current_rtys = None
            return
        self.current_rtys = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self.populate_fields()

    def populate_fields(self):
        self._block_update = True
        if not self.current_sdr:
            t = {}
        elif not self.current_rtys or self.current_rtys == 'default':
            t = self.thresholds.get(self.current_sdr, {})
        else:
            t = self.thresholds.get(self.current_sdr, {}).get('rtys', {}).get(self.current_rtys, {})
        defaults = {
            'db_min': -90.0,
            'db_max': -35.0,
            'noise_floor': -90.0,
            'signal_threshold': -80.0,
            'snr_threshold': 10.0,
            'strong_signal': -70.0,
        }
        for key, spin in self.spin_boxes.items():
            spin.setValue(float(t.get(key, defaults.get(key, 0.0))))
        self._block_update = False

    def on_value_changed(self):
        if self._block_update or not self.current_sdr:
            return
        sdr_th = self.thresholds.setdefault(self.current_sdr, {})
        if self.current_rtys and self.current_rtys != 'default':
            target = sdr_th.setdefault('rtys', {}).setdefault(self.current_rtys, {})
        else:
            target = sdr_th
        for key, spin in self.spin_boxes.items():
            target[key] = round(spin.value(), 1)

    def apply_global_display_range(self):
        db_min = round(self.global_db_min.value(), 1)
        db_max = round(self.global_db_max.value(), 1)
        rtys_by_sdr = {}
        for sdr in self.cfg.get('sdrs', []):
            sid = sdr['id']
            for st in sdr.get('stations', []):
                r = st.get('rtys', '')
                if r:
                    rtys_by_sdr.setdefault(sid, set()).add(r)
        for sid in self.sdr_ids:
            sdr_th = self.thresholds.setdefault(sid, {})
            sdr_th['db_min'] = db_min
            sdr_th['db_max'] = db_max
            rtys_set = set(rtys_by_sdr.get(sid, []))
            rtys_set.update(self.thresholds.get(sid, {}).get('rtys', {}).keys())
            rtys_ths = sdr_th.setdefault('rtys', {})
            for r in rtys_set:
                th = rtys_ths.setdefault(r, {})
                th['db_min'] = db_min
                th['db_max'] = db_max
        self.save_thresholds()
        if self.current_sdr:
            self.populate_fields()

    def save_thresholds(self):
        try:
            with open(THRESHOLDS_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.thresholds, f, ensure_ascii=False, indent=2)
            QtWidgets.QMessageBox.information(self, 'Saved', f'Saved to {THRESHOLDS_PATH.name}')
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Error', f'Could not save thresholds: {e}')


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = SDRConfigEditor()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
