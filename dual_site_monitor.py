"""
dual_site_monitor.py — FM sweep + DAB+ 12A monitor with PostgreSQL logging
============================================================================
Two SDRs, one script:
    site_1 = RTL-SDR (HTOOL SDR 6)          -> SDR_TYPE=rtl     (default)
    site_2 = nRSP-ST through SDRconnect     -> SDR_TYPE=sdrconnect

Measurement chain is identical to site_4.py, so the numbers logged by every
site are directly comparable:

  * The LO is parked LO_OFFSET_HZ (default 300 kHz) ABOVE the wanted channel,
    so the channel is read at baseband -LO_OFFSET_HZ and never sits on the
    DC / LO-leakage spike. This is what SDRuno calls "Auto LO offset".
  * Every capture is turned into a Parseval-normalised power spectrum
    (Hanning window, 8192-point FFT, power averaged over all frames), so a
    sum over bins is a true channel power in dBFS and one bin is a true RBW
    power in dBFS.
  * Channel power = the per-bin powers integrated over FILTER_BW_HZ
    (120 kHz, SDRuno's FM filter).
  * Noise floor = iteratively clipped mean of the surrounding off-channel
    bins (signal-carrying bins are rejected), scaled up to the same channel
    bandwidth. Reported floor and SNR are therefore independent of the FFT
    size, the sample rate and the dwell time.
  * Both numbers are gain-compensated and then shifted by one calibration
    constant, so what lands in the database is absolute dBm.

Only the analysable span is used (ANALYSIS_BW_HZ, default 1.536 MHz), which
makes an RTL-SDR at 2.4 MSps and an nRSP-ST at 2.0 MSps report the same floor
for the same antenna signal.

Database: sdr_db
Table: rf_sweep_data (id, timestamp, frequency_mhz, power_dbfs, floor_dbfs,
                      snr_db, sdr_id)
Frequency is stored as an integer kHz value (86.0 MHz -> 86000,
DAB+ 12A -> 223936). power_dbfs/floor_dbfs hold calibrated dBm.

Usage:
    python dual_site_monitor.py

    On startup you are prompted (auto-selects Q after 10 s):
        Q = Quick mode (per-channel, QUICK_MODE_FRAMES FFT frames per step)
        S = Slow  mode (per-channel, SLOW_MODE_STEP_DELAY_MS dwell per step)

Calibration (this is what makes the readings absolute):
    CAL_FREQ_MHZ  strong, stable station to calibrate on (default 90.0)
    CAL_DBM       the level that station really has, e.g. from SDRuno /
                  SDRconnect / a signal generator (default -36.5)
    CAL_OFFSET_DB skip the runtime calibration and use this fixed dB offset
                  (use the value the script printed on an earlier run)
    SKIP_CAL=1    log raw gain-compensated dBFS instead of dBm
    Never calibrate on noise or on a fading station - the constant would be
    tied to the receiver noise figure instead of to a real signal level.

Environment:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, POSTGRES_PASSWORD
    SDR_ID (leave unset to auto-derive site_1 / site_2 from the backend)
    SDR_GAIN (default: 20.2 (KEEEP IT LIKE THIS DONT CHANGE))
    SDR_TYPE (auto|rtl|rtlsdr|soapy|sdrplay|sdrconnect; default: auto)
    SDR_INDEX (device index for SoapySDR; default: 0)
    SDRPLAY_ANTENNA (SoapySDR/SDRplay antenna name; default: Antenna A)
    SDRCONNECT_HOST / SDRCONNECT_PORT (for nRSP-ST via SDRconnect WebSocket; default 127.0.0.1:5454)
    SDRCONNECT_DEVICE (primary|secondary for SDRconnect; default: primary)
    SDRCONNECT_DEVICE_INDEX (device index for selected_device event; default: 0)
    SDRCONNECT_DEVICE_NAME / SDRCONNECT_DEVICE_SERIAL (optional nRSP-ST name or serial)
    SDRCONNECT_DEVICE_MODE (nRSP-ST stream mode: Full IQ|IQ Lite|Compact; default: Full IQ)
    SDRCONNECT_PROFILE (optional SDRconnect profile to apply on connect)
    SDRCONNECT_USER / SDRCONNECT_PASS (optional WebSocket Basic auth)
    SDRCONNECT_SELECT_DEVICE (send device selection events; default: true)
    SDRCONNECT_DEBUG (print all WebSocket traffic; default: false)
    SDRCONNECT_SAMPLE_RATE (FFT sample rate in Hz; default: 2.0e6)
    SDRCONNECT_RETUNE_SETTLE_MS (extra settle time after retune before trusting IQ; default: 500)
    LO_OFFSET_HZ (LO offset that keeps the channel off DC; default: 300000)
    ANALYSIS_BW_HZ (analysed span, shared by both SDRs; default: 1536000)
    FILTER_BW_HZ (FM channel filter width; default: 120000)
    DC_GUARD_HZ (baseband bins around DC excluded from the floor; default: 60000)
    NOISE_GUARD_HZ (guard band around channel/image excluded from the floor; default: 100000)
    NOISE_CLIP (clipping factor of the noise-floor estimator; default: 3.0)
    SETTLE_MS (IQ discarded after every retune; default: 250)
    SLOW_MODE_STEP_DELAY_MS (dwell per channel in Slow mode; default: 500)
    QUICK_MODE_FRAMES (FFT frames per channel in Quick mode; default: 32)
    QUICK_MODE_DWELL_MS (overrides QUICK_MODE_FRAMES when set)
    DAB_DWELL_MS (how long to collect IQ per DAB+ 12A reading; default: 1000)
    DAB_NOISE_GUARD_HZ / DAB_DC_NOTCH_HZ (DAB floor / DC-spike handling)
    DEBUG_SDR=1 (print raw dBFS, gain compensation and mask sizes)
"""

import base64
import contextlib
import json
import os
import queue
import signal
import struct
import sys
import threading
import time
import datetime

# Add the script's own folder to the Windows DLL search path so that
# librtlsdr.dll and its dependencies are found without setting PATH.
if os.name == 'nt':
    try:
        os.add_dll_directory(os.path.dirname(os.path.abspath(__file__)))
    except (AttributeError, OSError):
        # Fallback for Python <3.8 or frozen/embedded environments
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        os.environ['PATH'] = _script_dir + os.pathsep + os.environ.get('PATH', '')

import numpy as np

# RTL-SDR is imported lazily by _open_rtl_sdr() so SDRconnect/SDRplay users
# don't need a working librtlsdr at import time.
_RTL_SDR_TYPE = None

# ---------------------------------------------------------------------------
# SDR Identity — change this to whatever you want
# ---------------------------------------------------------------------------
# Leave SDR_ID unset and it is derived from whichever backend opens:
#   RTL-SDR (HTOOL SDR 6) -> site_1,  nRSP-ST via SDRconnect -> site_2.
SDR_ID = os.getenv("SDR_ID", "").strip()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SDR_GAIN = float(os.getenv("SDR_GAIN", "20.2"))  # KEEEP IT LIKE THIS DONT CHANGE

# SDR Backend selection
SDR_TYPE = os.getenv("SDR_TYPE", "auto").strip().lower()
SDR_INDEX = int(os.getenv("SDR_INDEX", "0"))
SDRPLAY_ANTENNA = os.getenv("SDRPLAY_ANTENNA", "Antenna A")

SDRCONNECT_HOST = os.getenv("SDRCONNECT_HOST", "127.0.0.1")
SDRCONNECT_PORT = int(os.getenv("SDRCONNECT_PORT", "5454"))
SDRCONNECT_DEVICE = os.getenv("SDRCONNECT_DEVICE", "primary").strip().lower()
SDRCONNECT_DEVICE_INDEX = int(os.getenv("SDRCONNECT_DEVICE_INDEX", "0"))
SDRCONNECT_DEVICE_NAME = os.getenv("SDRCONNECT_DEVICE_NAME", "")
SDRCONNECT_DEVICE_SERIAL = os.getenv("SDRCONNECT_DEVICE_SERIAL", "")
SDRCONNECT_PROFILE = os.getenv("SDRCONNECT_PROFILE", "")
SDRCONNECT_USER = os.getenv("SDRCONNECT_USER", "")
SDRCONNECT_PASS = os.getenv("SDRCONNECT_PASS", "")
SDRCONNECT_SELECT_DEVICE = os.getenv(
    "SDRCONNECT_SELECT_DEVICE", "true"
).strip().lower() in ("1", "true", "yes")
SDRCONNECT_DEVICE_MODE = os.getenv("SDRCONNECT_DEVICE_MODE", "Full IQ").strip()
SDRCONNECT_DEBUG = os.getenv(
    "SDRCONNECT_DEBUG", "false"
).strip().lower() in ("1", "true", "yes")
SDRCONNECT_SAMPLE_RATE = float(os.getenv("SDRCONNECT_SAMPLE_RATE", "2.0e6"))
# Extra settle time after a retune command before trusting incoming IQ.
# The nRSP-ST's PLL lock + WebSocket round-trip can exceed the sweep's
# fixed discard-read margin (which was tuned for RTL-SDR's synchronous
# USB retune), letting stale IQ from the previous frequency bleed into
# the next reading (e.g. a false "signal" detected on the wrong channel).
# Default increased to 500 ms because nRSP-ST/SDRconnect retunes can take
# noticeably longer than RTL-SDR's near-instant USB tune.
SDRCONNECT_RETUNE_SETTLE_S = float(os.getenv("SDRCONNECT_RETUNE_SETTLE_MS", "500")) / 1000.0
# Extra delay between each channel step in Slow mode (per-channel sweep).
SLOW_MODE_STEP_DELAY_S = float(os.getenv("SLOW_MODE_STEP_DELAY_MS", "500")) / 1000.0
# Quick mode is controlled by the number of FFT frames per FM step, exactly
# like site_4.py. Default 32 frames at 2.0 MSps gives ~131 ms per channel.
# An explicit QUICK_MODE_DWELL_MS always wins, so existing launch scripts that
# ask for a longer dwell keep working.
QUICK_MODE_FRAMES = int(os.getenv("QUICK_MODE_FRAMES", "32"))
_QUICK_DWELL_MS = os.getenv("QUICK_MODE_DWELL_MS", "").strip()
QUICK_MODE_DWELL_S = float(_QUICK_DWELL_MS) / 1000.0 if _QUICK_DWELL_MS else None
if QUICK_MODE_FRAMES <= 0 and QUICK_MODE_DWELL_S is None:
    QUICK_MODE_DWELL_S = 0.131
# Dwell time per DAB+ 12A reading.
DAB_DWELL_S = float(os.getenv("DAB_DWELL_MS", "1000")) / 1000.0

# DB Configuration (from sdr_collector.py pattern)
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")  # Tailscale IP
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sdr_db")

# SDR Settings
DAB_12A_FREQ_HZ = 223.936e6
DAB_FREQ_KHZ = 223936  # For SQL storage
DAB_BANDWIDTH_HZ = 1.536e6
FFT_SIZE = 8192
CALIBRATION_DB = 0.0  # in-band flatness calibration (kept at 0 here)
DB_OFFSET_DB = 0.0  # auto-computed at runtime from CAL_DBM below

# --- site_4-style measurement settings ------------------------------------
# The LO is parked LO_OFFSET_HZ ABOVE the wanted channel, so the channel is
# read at baseband -LO_OFFSET_HZ and the DC / LO-leakage spike (RTL-SDR) or
# the ZIF DC spike (nRSP-ST) never lands inside the measured channel. This is
# exactly what site_4.py and SDRuno's "Auto LO offset" do.
LO_OFFSET = int(float(os.getenv("LO_OFFSET_HZ", "300000")))
# Span actually analysed, capped at 90% of the sample rate. Keeping this the
# same for every backend makes an RTL-SDR at 2.4 MSps and an nRSP-ST at
# 2.0 MSps report the same floor for the same antenna signal, and keeps the
# measurement away from the anti-aliasing roll-off at the band edges.
ANALYSIS_BW = float(os.getenv("ANALYSIS_BW_HZ", "1536000"))
# FM channel filter (SDRuno's 120 kHz FM filter) and noise-floor masks.
FILTER_BW = int(float(os.getenv("FILTER_BW_HZ", "120000")))
DC_GUARD = int(float(os.getenv("DC_GUARD_HZ", "60000")))
NOISE_GUARD = float(os.getenv("NOISE_GUARD_HZ", "100000"))
NOISE_CLIP = float(os.getenv("NOISE_CLIP", "3.0"))
# A floor estimate needs enough bins to be meaningful; below this the mask is
# widened step by step instead of returning a nonsense -300 dBFS floor.
MIN_NOISE_BINS = int(os.getenv("MIN_NOISE_BINS", "64"))
# DAB+ 12A fills 1.536 MHz, so its floor has to come from the narrow gaps
# between the blocks: a smaller guard band than FM's, and the DC spike is
# notched out of the channel sum instead of being excluded from the floor.
DAB_NOISE_GUARD = float(os.getenv("DAB_NOISE_GUARD_HZ", "30000"))
DAB_DC_NOTCH = float(os.getenv("DAB_DC_NOTCH_HZ", "5000"))
# IQ discarded after every retune (LO relock + stale USB / WebSocket buffers).
SETTLE_S = float(os.getenv("SETTLE_MS", "250")) / 1000.0
# Calibration reference: tune to CAL_FREQ and assume the channel power there
# equals CAL_DBM (read both from SDRuno/SDRconnect). Always calibrate on a
# STRONG, stable station - calibrating on noise ties the constant to the
# receiver's own noise figure instead of to a real signal level.
# CAL_OFFSET_DB skips the runtime calibration and applies a fixed constant.
CAL_FREQ = int(float(os.getenv("CAL_FREQ_MHZ", "90.0")) * 1e6)
CAL_DBM = float(os.getenv("CAL_DBM", "-36.5"))
CAL_DWELL_S = float(os.getenv("CAL_DWELL_MS", "1000")) / 1000.0
_CAL_OFFSET_ENV = os.getenv("CAL_OFFSET_DB", "").strip()
CAL_OFFSET_DB = float(_CAL_OFFSET_ENV) if _CAL_OFFSET_ENV else None
# Warn when the calibration station is too weak to tie an absolute level to.
CAL_MIN_SNR_DB = float(os.getenv("CAL_MIN_SNR_DB", "10"))
SKIP_CAL = os.getenv("SKIP_CAL", "0") == "1"

# Set DEBUG_SDR=1 to print SDR state and raw dBFS values for each step.
DEBUG_SDR = os.getenv("DEBUG_SDR", "0") == "1"

running = True
SDR_RECONNECT_DELAY = 5   # seconds to wait before re-opening the SDR after a USB error


class _SoapySdrDevice:
    """Minimal RTL-SDR-like wrapper around a SoapySDR device (e.g. SDRplay RSP)."""

    def __init__(self, device_index=0, antenna=None):
        import SoapySDR

        self.SoapySDR = SoapySDR
        results = SoapySDR.Device.enumerate()
        if not results:
            raise RuntimeError("SoapySDR did not find any devices")
        if device_index >= len(results):
            raise RuntimeError(
                f"SDR_INDEX {device_index} out of range; SoapySDR found {len(results)} device(s)"
            )
        args = results[device_index]
        self._dev = SoapySDR.Device(args)
        self._ch = 0
        self._stream = None
        self._antenna = antenna or SDRPLAY_ANTENNA

        # Initialise to the same defaults the RTL-SDR path uses.
        self._sample_rate = 2.4e6
        self._center_freq = 100e6
        self._gain = SDR_GAIN
        self._setup()

    def _setup(self):
        dev = self._dev
        ch = self._ch
        SOAPY_RX = self.SoapySDR.SOAPY_SDR_RX

        dev.setSampleRate(SOAPY_RX, ch, self._sample_rate)
        dev.setFrequency(SOAPY_RX, ch, self._center_freq)
        dev.setGainMode(SOAPY_RX, ch, False)
        dev.setGain(SOAPY_RX, ch, self._gain)

        try:
            antennas = dev.listAntennas(SOAPY_RX, ch)
            if antennas and self._antenna in antennas:
                dev.setAntenna(SOAPY_RX, ch, self._antenna)
        except Exception:
            pass

        try:
            dev.setBandwidth(SOAPY_RX, ch, self._sample_rate)
        except Exception:
            pass

        self._sample_rate = dev.getSampleRate(SOAPY_RX, ch)
        self._center_freq = dev.getFrequency(SOAPY_RX, ch)
        self._stream = dev.setupStream(SOAPY_RX, self.SoapySDR.SOAPY_SDR_CF32, [ch])
        dev.activateStream(self._stream)

    @property
    def sample_rate(self):
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value):
        self._sample_rate = float(value)
        self._dev.setSampleRate(self.SoapySDR.SOAPY_SDR_RX, self._ch, self._sample_rate)
        self._sample_rate = self._dev.getSampleRate(self.SoapySDR.SOAPY_SDR_RX, self._ch)

    @property
    def center_freq(self):
        return self._center_freq

    @center_freq.setter
    def center_freq(self, value):
        self._center_freq = int(value)
        self._dev.setFrequency(self.SoapySDR.SOAPY_SDR_RX, self._ch, self._center_freq)
        self._center_freq = int(self._dev.getFrequency(self.SoapySDR.SOAPY_SDR_RX, self._ch))

    @property
    def gain(self):
        try:
            return self._dev.getGain(self.SoapySDR.SOAPY_SDR_RX, self._ch)
        except Exception:
            return self._gain

    @gain.setter
    def gain(self, value):
        self._gain = float(value)
        self._dev.setGain(self.SoapySDR.SOAPY_SDR_RX, self._ch, self._gain)

    def set_direct_sampling(self, enabled):
        # Not applicable to SDRplay; no-op to keep the RTL-SDR API shape.
        pass

    def set_manual_gain_enabled(self, enabled):
        # True => fixed manual gain; False => AGC.
        self._dev.setGainMode(self.SoapySDR.SOAPY_SDR_RX, self._ch, not enabled)

    def set_agc_mode(self, enabled):
        self._dev.setGainMode(self.SoapySDR.SOAPY_SDR_RX, self._ch, enabled)

    def read_samples(self, n):
        if self._stream is None:
            raise RuntimeError("SoapySDR stream is not active")

        out = np.empty(n, dtype=np.complex64)
        idx = 0
        mtu = self._dev.getStreamMTU(self._stream) or 1024
        # Allow several seconds total, scaled by requested samples.
        deadline = time.time() + max(5.0, 4.0 * n / self.sample_rate)
        per_read_timeout_us = 500_000
        SOAPY_TIMEOUT = self.SoapySDR.SOAPY_SDR_TIMEOUT

        while idx < n:
            if time.time() > deadline:
                raise RuntimeError(f"SoapySDR read timeout waiting for {n} samples")
            chunk = min(n - idx, max(mtu, 1024))
            buf = np.empty(chunk, dtype=np.complex64)
            sr = self._dev.readStream(
                self._stream, [buf], chunk, timeoutUs=per_read_timeout_us
            )
            if sr.ret > 0:
                out[idx:idx + sr.ret] = buf[:sr.ret]
                idx += sr.ret
            elif sr.ret == SOAPY_TIMEOUT:
                continue
            else:
                raise RuntimeError(f"SoapySDR readStream error {sr.ret}")

        return out

    def close(self):
        try:
            if self._stream is not None:
                self._dev.deactivateStream(self._stream)
                self._dev.closeStream(self._stream)
                self._stream = None
        except Exception:
            pass
        try:
            self._dev = None
        except Exception:
            pass


def _open_rtl_sdr():
    """Open and configure the RTL-SDR."""
    global _RTL_SDR_TYPE
    from rtlsdr import RtlSdr
    _RTL_SDR_TYPE = RtlSdr
    sdr = RtlSdr()
    sdr.sample_rate = 2.4e6
    sdr.err_ppm = 0
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    sdr.gain = SDR_GAIN
    print(f"[SDR] Opened RTL-SDR: gain={sdr.gain} dB")
    _debug_sdr(sdr, "Opened RTL-SDR")
    return sdr


def _open_soapy_sdr():
    """Open an SDR via SoapySDR (used here for SDRplay RSP devices)."""
    sdr = _SoapySdrDevice(device_index=SDR_INDEX, antenna=SDRPLAY_ANTENNA)
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    sdr.gain = SDR_GAIN
    print(
        f"[SDR] Opened SoapySDR/SDRplay device {SDR_INDEX}: "
        f"fs={sdr.sample_rate / 1e6:.3f} MHz, gain={sdr.gain} dB"
    )
    return sdr


class _SDRconnectWebSocketDevice:
    """RTL-SDR-like wrapper around SDRconnect's WebSocket API (nRSP-ST, etc.)."""

    def __init__(self, host=SDRCONNECT_HOST, port=SDRCONNECT_PORT, device=SDRCONNECT_DEVICE):
        import websockets.sync.client

        self._websockets = websockets
        self._uri = f"ws://{host}:{port}/"
        self._auth_headers = None
        if SDRCONNECT_USER and SDRCONNECT_PASS:
            token = base64.b64encode(
                f"{SDRCONNECT_USER}:{SDRCONNECT_PASS}".encode()
            ).decode()
            self._auth_headers = {"Authorization": f"Basic {token}"}
        self._device = device if device in ("primary", "secondary") else "primary"
        self._sample_rate = SDRCONNECT_SAMPLE_RATE
        self._center_freq = 100e6
        self._gain = SDR_GAIN
        self._cmd_queue = queue.Queue()
        self._sample_queue = queue.Queue()
        self._running = True
        self._connected = threading.Event()
        self._stream_mtu = 4096
        self._conn = None
        self._thread = None
        self._connect_and_start()

    def _connect_and_start(self):
        import websockets.sync.client

        try:
            self._conn = websockets.sync.client.connect(
                self._uri,
                open_timeout=10,
                additional_headers=self._auth_headers or {},
            )
        except Exception as e:
            raise RuntimeError(f"Could not connect to SDRconnect at {self._uri}: {e}") from e

        self._thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._thread.start()
        if not self._connected.wait(timeout=10):
            raise RuntimeError("SDRconnect WebSocket did not become ready")

        # Select the device when talking to a multi-device SDRconnect server.
        # For a direct nRSP-ST connection, disable this with
        # SDRCONNECT_SELECT_DEVICE=false.
        # For nRSP-ST, append the stream mode (Full IQ / IQ Lite / Compact).
        if SDRCONNECT_SELECT_DEVICE:
            mode = SDRCONNECT_DEVICE_MODE
            if SDRCONNECT_PROFILE:
                self._send_event("apply_device_profile", SDRCONNECT_PROFILE)
            elif SDRCONNECT_DEVICE_SERIAL:
                value = f"{SDRCONNECT_DEVICE_SERIAL}:{mode}"
                self._send_event("selected_device_serial", value)
            elif SDRCONNECT_DEVICE_NAME:
                value = f"{SDRCONNECT_DEVICE_NAME}:{mode}"
                self._send_event("selected_device_name", value)
            else:
                value = f"{SDRCONNECT_DEVICE_INDEX}:{mode}"
                self._send_event("selected_device", value)
            time.sleep(0.5)

        # Enable IQ streaming from the chosen device.
        if SDRCONNECT_SELECT_DEVICE:
            enable_event = (
                "set_primary_device_enable" if self._device == "primary"
                else "set_secondary_device_enable"
            )
            self._send_event(enable_event, "true")
        self._send_event("device_stream_enable", "true")
        self._send_event("iq_stream_enable", "true")
        # Allow the server a moment to start the data stream.
        time.sleep(0.5)

        # Tune the hardware LO.
        self._send_property("device_center_frequency", str(int(self._center_freq)))

    def _send(self, obj):
        raw = json.dumps(obj)
        if SDRCONNECT_DEBUG:
            print(f"[SDRCONNECT TX] {raw}")
        self._cmd_queue.put(raw)

    def _send_property(self, prop, value):
        self._send({
            "event_type": "set_property",
            "property": prop,
            "device": self._device,
            "value": str(value),
        })

    def _send_event(self, event_type, value=""):
        self._send({
            "event_type": event_type,
            "property": "",
            "device": self._device,
            "value": str(value),
        })

    def _handle_text(self, text):
        try:
            msg = json.loads(text)
        except Exception:
            if SDRCONNECT_DEBUG:
                print(f"[SDRCONNECT RX (non-JSON)] {text!r}")
            return
        if SDRCONNECT_DEBUG:
            print(f"[SDRCONNECT RX] {msg}")
        event = msg.get("event_type")
        prop = msg.get("property")
        value = msg.get("value")
        if event in ("property_changed", "get_property_response"):
            if prop == "device_sample_rate" and value not in (None, ""):
                try:
                    self._sample_rate = int(value)
                except Exception:
                    pass
            elif prop == "device_center_frequency" and value not in (None, ""):
                try:
                    self._center_freq = int(value)
                except Exception:
                    pass

    def _handle_binary(self, data):
        if len(data) < 2:
            return
        payload_type = struct.unpack("<H", data[:2])[0]
        if SDRCONNECT_DEBUG:
            print(f"[SDRCONNECT RX (binary)] type={payload_type}, bytes={len(data)-2}")
        # Type 2 = primary signed 16-bit interleaved IQ
        # Type 5 = secondary signed 16-bit interleaved IQ
        if payload_type not in (2, 5):
            return
        payload = data[2:]
        n = len(payload) // 4
        if n == 0:
            return
        ints = np.frombuffer(payload[: n * 4], dtype=np.int16).astype(np.float32)
        ints /= 32768.0
        iq = ints[0::2] + 1j * ints[1::2]
        self._sample_queue.put(iq.astype(np.complex64))

    def _drain_samples(self):
        while not self._sample_queue.empty():
            try:
                self._sample_queue.get_nowait()
            except queue.Empty:
                break

    def _ws_loop(self):
        self._connected.set()
        while self._running:
            try:
                # Flush pending commands first.
                while True:
                    try:
                        cmd = self._cmd_queue.get_nowait()
                        self._conn.send(cmd)
                    except queue.Empty:
                        break

                msg = self._conn.recv(timeout=0.05)
                if isinstance(msg, bytes):
                    self._handle_binary(msg)
                else:
                    self._handle_text(msg)
            except self._websockets.exceptions.ConnectionClosed as exc:
                if SDRCONNECT_DEBUG:
                    print(f"[SDRCONNECT] connection closed: {exc}")
                break
            except TimeoutError:
                continue
            except Exception as exc:
                if SDRCONNECT_DEBUG:
                    print(f"[SDRCONNECT] receiver error: {exc}")
                continue

    @property
    def sample_rate(self):
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value):
        self._sample_rate = float(value)
        self._send_property("device_sample_rate", str(int(self._sample_rate)))

    @property
    def center_freq(self):
        return self._center_freq

    @center_freq.setter
    def center_freq(self, value):
        self._center_freq = int(value)
        # Avoid measuring stale IQ from the previous centre frequency.
        self._drain_samples()
        self._send_property("device_center_frequency", str(self._center_freq))
        # Unlike RTL-SDR's synchronous USB tune, this command is only queued
        # here; the WebSocket round-trip + nRSP-ST PLL lock time can exceed
        # the sweep's fixed discard-read margin, letting stale IQ from the
        # previous frequency bleed into the next reading. Give it time to
        # settle, then drop anything captured mid-retune.
        time.sleep(SDRCONNECT_RETUNE_SETTLE_S)
        self._drain_samples()

    @property
    def gain(self):
        return self._gain

    @gain.setter
    def gain(self, value):
        self._gain = float(value)
        self._send_property("device_gain", str(self._gain))

    def set_direct_sampling(self, enabled):
        pass

    def set_manual_gain_enabled(self, enabled):
        # SDRconnect has no direct RTL-style manual-gain switch.
        pass

    def set_agc_mode(self, enabled):
        # Try the most likely AGC property names; ignore failures.
        with contextlib.suppress(Exception):
            self._send_property("device_agc_enabled", "true" if enabled else "false")

    def read_samples(self, n):
        chunks = []
        total = 0
        deadline = time.time() + max(10.0, 4.0 * n / self.sample_rate)
        while total < n and time.time() < deadline:
            if not self._running or not running:
                raise KeyboardInterrupt
            try:
                chunk = self._sample_queue.get(timeout=0.05)
                chunks.append(chunk)
                total += len(chunk)
            except queue.Empty:
                continue
        if total == 0:
            raise RuntimeError("SDRconnect WebSocket did not deliver IQ samples")
        arr = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        return arr[:n].astype(np.complex64)

    def close(self):
        self._running = False
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2)


def _open_sdrconnect():
    """Open SDRconnect via its WebSocket API (used for networked nRSP-ST)."""
    sdr = _SDRconnectWebSocketDevice(
        host=SDRCONNECT_HOST,
        port=SDRCONNECT_PORT,
        device=SDRCONNECT_DEVICE,
    )
    print(
        f"[SDR] Opened SDRconnect WebSocket device ({SDRCONNECT_DEVICE}) "
        f"at {SDRCONNECT_HOST}:{SDRCONNECT_PORT}: "
        f"fs={sdr.sample_rate / 1e6:.3f} MHz"
    )
    return sdr


def _open_sdr():
    """Open the configured SDR backend."""
    errors = []

    if SDR_TYPE in ("auto", "rtl", "rtlsdr"):
        try:
            return _open_rtl_sdr()
        except Exception as e:
            if SDR_TYPE in ("rtl", "rtlsdr"):
                raise RuntimeError(f"Could not open RTL-SDR: {e}") from e
            errors.append(f"RTL-SDR: {e}")
            print(f"[SDR] RTL-SDR not available ({e}); trying SoapySDR/SDRplay...")

    if SDR_TYPE in ("auto", "soapy", "sdrplay"):
        try:
            return _open_soapy_sdr()
        except ImportError as e:
            if SDR_TYPE in ("soapy", "sdrplay"):
                raise RuntimeError(
                    "SoapySDR is not installed. Build/install SoapySDR and the "
                    "SoapySDRPlay support module, or use SDR_TYPE=sdrconnect."
                ) from e
            errors.append(f"SoapySDR not installed")
            print(f"[SDR] SoapySDR not installed; trying SDRconnect...")
        except Exception as e:
            if SDR_TYPE in ("soapy", "sdrplay"):
                raise RuntimeError(f"Could not open SoapySDR/SDRplay device: {e}") from e
            errors.append(f"SoapySDR/SDRplay: {e}")
            print(f"[SDR] SoapySDR/SDRplay not available ({e}); trying SDRconnect...")

    if SDR_TYPE in ("auto", "sdrconnect"):
        try:
            return _open_sdrconnect()
        except ImportError as e:
            raise RuntimeError(
                "websockets package is required for SDRconnect. Install it with:\n"
                "  pip install websockets"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Could not open SDRconnect WebSocket device: {e}") from e

    raise RuntimeError(
        f"No usable SDR backend for SDR_TYPE={SDR_TYPE!r}. Tried: {'; '.join(errors)}"
    )


def _debug_sdr(sdr, label=""):
    """Print SDR parameters when DEBUG_SDR=1."""
    if not DEBUG_SDR:
        return
    try:
        print(f"[DEBUG] {label} {type(sdr).__name__}")
        print(f"[DEBUG]   center_freq={getattr(sdr, 'center_freq', None)}")
        print(f"[DEBUG]   sample_rate={getattr(sdr, 'sample_rate', None)}")
        print(f"[DEBUG]   gain={getattr(sdr, 'gain', None)}")
        print(f"[DEBUG]   freq_correction={getattr(sdr, 'freq_correction', None)}")
        print(f"[DEBUG]   direct_sampling={getattr(sdr, 'direct_sampling', None)}")
    except Exception as e:
        print(f"[DEBUG] error reading attrs: {e}")


def _set_freq(sdr, freq_hz, retries=3):
    """Set center_freq with retries on transient errors.
    Raises on fatal USB errors (pipe, access violation) to trigger reconnect."""
    _FATAL_PATTERNS = ("pipe", "access violation", "LIBUSB_ERROR_PIPE", "LIBUSB_ERROR_NO_DEVICE")
    for attempt in range(retries):
        try:
            sdr.center_freq = int(freq_hz)
            _debug_sdr(sdr, f"Tuned to {freq_hz/1e6:.3f} MHz")
            return True
        except Exception as e:
            msg = str(e).lower()
            print(f"[SDR] center_freq set failed (attempt {attempt+1}/{retries}): {e}")
            if any(p.lower() in msg for p in _FATAL_PATTERNS):
                raise RuntimeError(f"Fatal USB error setting freq {freq_hz}: {e}") from e
            if attempt < retries - 1:
                time.sleep(0.5)
    return False


def _read_samples(sdr, n, retries=3):
    """Read samples with retries on transient errors.
    Raises on fatal USB errors (pipe, access violation) to trigger reconnect."""
    _FATAL_PATTERNS = ("pipe", "access violation", "LIBUSB_ERROR_PIPE", "LIBUSB_ERROR_NO_DEVICE")
    for attempt in range(retries):
        if not running:
            raise KeyboardInterrupt
        try:
            return sdr.read_samples(n)
        except Exception as e:
            if not running:
                raise KeyboardInterrupt
            msg = str(e).lower()
            print(f"[SDR] read_samples failed (attempt {attempt+1}/{retries}): {e}")
            if any(p.lower() in msg for p in _FATAL_PATTERNS):
                raise RuntimeError(f"Fatal USB error reading samples: {e}") from e
            if attempt < retries - 1:
                # Short sleep slices so Ctrl-C is responsive.
                for _ in range(5):
                    if not running:
                        raise KeyboardInterrupt
                    time.sleep(0.1)
    return None


def _settle(sdr, seconds=None):
    """Discard `seconds` of IQ so no sample from before a retune is measured.

    RTL-SDR's synchronous USB pipeline and SDRconnect's WebSocket queue both
    hold well over 100 ms of samples captured at the OLD frequency; measuring
    them would put a neighbouring station's power on this channel.
    """
    seconds = SETTLE_S if seconds is None else seconds
    if seconds <= 0:
        return
    # librtlsdr wants the read length to be a multiple of 512 bytes; whole FFT
    # frames satisfy that for every backend.
    n_frames = max(1, int(round(seconds * sdr.sample_rate / FFT_SIZE)))
    _read_samples(sdr, n_frames * FFT_SIZE)


def _tune_channel(sdr, target_hz, settle_s=None):
    """Park the LO LO_OFFSET Hz above target_hz, then let the front end settle.

    The wanted channel then sits at baseband -LO_OFFSET instead of on top of
    the DC / LO-leakage spike, exactly like site_4.py (and SDRuno's "Auto LO
    offset"). Returns the signed baseband offset of the channel in Hz, derived
    from the frequency the hardware actually reports, or None if the retune
    failed.
    """
    target_hz = float(target_hz)
    if not _set_freq(sdr, int(round(target_hz + LO_OFFSET))):
        return None
    _settle(sdr, settle_s)
    return target_hz - float(sdr.center_freq)


def _gain_comp_db(sdr):
    """dB to ADD to a dBFS reading to make it antenna-referred.

    The ADC sees (input power + receiver gain), so the gain has to be
    SUBTRACTED from dBFS:  dBm = dBFS - gain + calibration constant.
    RTL-SDR and SoapySDR run at a fixed manual gain that they can report, so
    the compensation is -gain and the reported level does not move if the gain
    is changed. SDRconnect keeps the nRSP-ST gain in the SDRconnect UI (this
    script never touches it), so there is nothing to compensate - that constant
    is absorbed by the CAL_DBM calibration instead. Keep the nRSP-ST gain and
    its AGC fixed in SDRconnect, otherwise the calibration stops being valid.
    """
    if isinstance(sdr, _SDRconnectWebSocketDevice):
        return 0.0
    try:
        return -float(sdr.gain)
    except Exception:
        return 0.0


_last_clip_warning = 0.0


def _warn_if_clipping(samples, threshold=0.98, every_s=30.0):
    """Throttled warning when the ADC is at (or near) full scale.

    A compressing front end reads LOW, which would quietly corrupt both the
    calibration and every level logged afterwards.
    """
    global _last_clip_warning
    chunk = samples[:FFT_SIZE]
    if chunk.size == 0:
        return
    peak = float(max(np.max(np.abs(chunk.real)), np.max(np.abs(chunk.imag))))
    if peak >= threshold and (time.time() - _last_clip_warning) > every_s:
        _last_clip_warning = time.time()
        print(f"[WARN] ADC at {peak:.3f} of full scale — the front end is "
              f"compressing; lower SDR_GAIN or add attenuation")


def _capture_spectrum(sdr, dwell_s=None, n_frames=None, fft_size=FFT_SIZE):
    """Capture IQ and return (freqs, power): linear power per FFT bin, dBFS ref.

    Give either dwell_s (seconds of IQ) or n_frames (FFT frames per step, like
    site_4.py's QUICK_MODE_FRAMES). Every captured frame is averaged into the
    power spectrum - nothing is discarded and no time is spent idling.
    Returns (None, None) if the read failed or produced no complete frame.
    """
    if n_frames is None:
        n_frames = round(float(dwell_s or 0.0) * sdr.sample_rate / fft_size)
    n_frames = max(1, int(n_frames))

    all_samples = _read_samples(sdr, n_frames * fft_size)
    if all_samples is None or len(all_samples) == 0:
        return None, None
    all_samples = np.asarray(all_samples, dtype=np.complex64)
    _warn_if_clipping(all_samples)

    # If the backend returned fewer samples than requested, average whatever
    # complete frames we got instead of crashing on a short buffer.
    got = len(all_samples) // fft_size
    if got == 0:
        return None, None
    return _spectrum(all_samples[:got * fft_size], sdr.sample_rate, fft_size)


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
    power = np.mean(np.abs(spec) ** 2, axis=0) / norm

    freqs = np.fft.fftshift(np.fft.fftfreq(fft_size, 1.0 / sample_rate))
    return freqs, power


def _channel_masks(freqs, sample_rate, filter_bw=FILTER_BW, lo_offset=0.0,
                   dc_guard=DC_GUARD, analysis_bw=None,
                   noise_guard=NOISE_GUARD):
    """Masks for (wanted channel, noise-estimation region).

    The wanted channel sits at baseband `lo_offset` (Hz). The noise region is
    everything inside the analysed span except: the channel itself plus a
    guard band, the DC / LO-leakage region, and the mirror of the channel
    (residual IQ image). The span is capped at 90% of the sample rate so the
    anti-aliasing roll-off at the band edges can never drag the floor down and
    inflate the SNR.

    When that leaves too few bins to average over - DAB+ fills nearly the
    whole span - the mask is relaxed one step at a time instead of coming back
    empty (an empty mask used to produce a -300 dBFS floor and a 260 dB SNR).
    """
    usable = min(float(analysis_bw or sample_rate * 0.9), sample_rate * 0.9)
    in_band = np.abs(freqs) <= usable / 2.0
    chan = in_band & (np.abs(freqs - lo_offset) <= filter_bw / 2.0)

    guard = filter_bw / 2.0 + noise_guard
    off_channel = (
        (np.abs(freqs - lo_offset) > guard)
        & (np.abs(freqs + lo_offset) > guard)
    )
    candidates = (
        in_band & off_channel & (np.abs(freqs) > dc_guard),
        in_band & off_channel,
        in_band & ~chan & (np.abs(freqs) > dc_guard),
        in_band & ~chan,
    )
    noise = candidates[0]
    for candidate in candidates:
        if np.count_nonzero(candidate) >= MIN_NOISE_BINS:
            noise = candidate
            break
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


def _measure(freqs, power, gain_comp_db, offset_db, sample_rate,
             lo_offset=0.0, filter_bw=FILTER_BW, analysis_bw=ANALYSIS_BW,
             dc_guard=DC_GUARD, noise_guard=NOISE_GUARD, dc_notch=0.0,
             label=""):
    """SDRuno-style channel measurement, identical to site_4.py.

    Returns (signal_dbm, floor_dbm, snr_db):
      signal = per-bin powers integrated over the channel filter bandwidth
               (an FM carrier spreads its energy over ~150 kHz, so a single
               peak bin is not the channel power)
      floor  = iteratively clipped mean per-bin noise level of the surrounding
               spectrum, scaled up to that same channel bandwidth
    Both are gain-compensated and calibrated (dBFS + gain compensation +
    calibration offset), so the numbers stay put when the gain moves, and both
    are independent of FFT size, sample rate and dwell time.
    Returns (None, None, None) if the channel is outside the analysed span.
    """
    chan, noise = _channel_masks(
        freqs, sample_rate, filter_bw=filter_bw, lo_offset=lo_offset,
        dc_guard=dc_guard, analysis_bw=analysis_bw, noise_guard=noise_guard,
    )
    n_chan = int(np.count_nonzero(chan))
    if n_chan == 0:
        return None, None, None

    # When the channel unavoidably straddles DC (DAB+ is 1.536 MHz wide), notch
    # the LO-leakage bins out of the channel sum and extrapolate the missing
    # bins from the mean of the ones that are left.
    used = chan
    if dc_notch > 0:
        notched = chan & (np.abs(freqs) > dc_notch)
        if np.count_nonzero(notched) > 0:
            used = notched
    sig_dbfs = 10.0 * np.log10(float(np.mean(power[used])) * n_chan + 1e-30)

    n_noise = int(np.count_nonzero(noise))
    if n_noise > 0:
        noise_bin = _noise_bin_power(power[noise])
    else:
        # Nothing clean left anywhere in the span: fall back to the quietest
        # tenth of the spectrum so the floor stays a real number.
        noise_bin = float(np.percentile(power, 10.0))
    noise_dbfs = 10.0 * np.log10(noise_bin + 1e-30) + 10.0 * np.log10(n_chan)

    if DEBUG_SDR:
        print(f"[DEBUG] {label or 'measure'}: chan_bins={n_chan} "
              f"used_bins={int(np.count_nonzero(used))} noise_bins={n_noise} "
              f"raw_sig={sig_dbfs:.2f} dBFS raw_floor={noise_dbfs:.2f} dBFS "
              f"gain_comp={gain_comp_db:.1f} dB offset={offset_db:.2f} dB")

    cal = gain_comp_db + offset_db + CALIBRATION_DB
    signal_dbm = sig_dbfs + cal
    floor_dbm = noise_dbfs + cal
    return signal_dbm, floor_dbm, signal_dbm - floor_dbm

# ---------------------------------------------------------------------------
# Database Setup
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
        timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
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

# ---------------------------------------------------------------------------
# Signal Handlers
# ---------------------------------------------------------------------------
def stop(sig, frame):
    global running
    running = False

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

# ---------------------------------------------------------------------------
# Single FM step
# ---------------------------------------------------------------------------
def _measure_fm_channel(sdr, target_hz, dwell_s=None, n_frames=None,
                        settle_s=None, offset_db=None, label=""):
    """Tune, dwell and measure one FM channel.

    Returns (power_dbm, floor_dbm, snr_db) or (None, None, None) if the retune
    or the read failed. The LO is parked LO_OFFSET above the channel and the
    baseband offset actually achieved by the hardware is what gets measured,
    so a backend that silently refuses a retune is caught instead of being
    logged as a reading for the wrong frequency.

    offset_db defaults to the calibration constant in force; calibrate() passes
    0.0 so that re-calibrating never compounds the offset already applied.
    """
    if offset_db is None:
        offset_db = DB_OFFSET_DB
    lo_offset = _tune_channel(sdr, target_hz, settle_s=settle_s)
    if lo_offset is None:
        print(f"[SDR] Skipping {target_hz/1e6:.1f} MHz — could not tune")
        return None, None, None

    freqs, power = _capture_spectrum(sdr, dwell_s=dwell_s, n_frames=n_frames)
    if freqs is None:
        print(f"[SDR] Skipping {target_hz/1e6:.1f} MHz — read failed")
        return None, None, None

    usable = min(ANALYSIS_BW, sdr.sample_rate * 0.9)
    if abs(lo_offset) + FILTER_BW / 2.0 > usable / 2.0:
        print(f"[SDR] Skipping {target_hz/1e6:.1f} MHz — channel sits "
              f"{lo_offset/1e3:.0f} kHz off centre, outside the "
              f"{usable/1e6:.3f} MHz analysed span (retune ignored?)")
        return None, None, None

    return _measure(
        freqs, power,
        _gain_comp_db(sdr),
        offset_db,
        sample_rate=sdr.sample_rate,
        lo_offset=lo_offset,
        filter_bw=FILTER_BW,
        analysis_bw=ANALYSIS_BW,
        label=label or f"FM {target_hz/1e6:.1f} MHz",
    )


def fm_single_step(sdr, db, SweepData, freq_mhz, dwell_s=None, n_frames=None,
                   settle_s=None) -> bool:
    """
    Scan a single FM channel and save the calibrated dBm reading to the DB.

    Args:
        dwell_s:  seconds of IQ to average (defaults to SLOW_MODE_STEP_DELAY_S).
        n_frames: FFT frames to average instead of a dwell time (Quick mode).
        settle_s: IQ discarded after the retune (defaults to SETTLE_S).

    Returns True when a reading was stored.
    """
    if not running:
        return False

    if dwell_s is None and n_frames is None:
        dwell_s = SLOW_MODE_STEP_DELAY_S

    active_dbm, noise_dbm, snr_db = _measure_fm_channel(
        sdr, freq_mhz * 1e6, dwell_s=dwell_s, n_frames=n_frames,
        settle_s=settle_s,
    )
    if active_dbm is None:
        return False

    freq_khz = int(round(freq_mhz * 1000))

    try:
        reading = SweepData(
            timestamp=datetime.datetime.now(),
            frequency_mhz=freq_khz,
            power_dbfs=round(float(active_dbm), 2),
            floor_dbfs=round(float(noise_dbm), 2),
            snr_db=round(float(snr_db), 2),
            sdr_id=SDR_ID
        )
        db.add(reading)
        db.commit()
        print(f"  FM {freq_mhz:6.1f} MHz | Power: {active_dbm:7.1f} dBm | "
              f"Floor: {noise_dbm:7.1f} dBm | SNR: {snr_db:5.1f} dB")
        return True
    except Exception as e:
        print(f"[DB ERROR] FM save failed at {freq_mhz} MHz: {e}")
        db.rollback()
        return False


# ---------------------------------------------------------------------------
# Single DAB+ reading
# ---------------------------------------------------------------------------
def dab_single_reading(sdr, db, SweepData, reading_num: int) -> bool:
    """
    Take a single DAB+ 12A reading and save to DB.

    Instead of a single FFT snapshot, it collects DAB_DWELL_S seconds of IQ
    and averages multiple FFT frames, giving weak or fading DAB signals time
    to be detected properly.

    Returns True if successful, False if stopped.
    """
    if not running:
        return False

    # DAB+ 12A occupies 1.536 MHz, i.e. practically the whole 2.0-2.4 MHz span,
    # so there is no room for an LO offset here: the block is measured centred
    # and the DC / LO-leakage bins are notched out of the channel sum instead.
    if not _set_freq(sdr, int(DAB_12A_FREQ_HZ)):
        print(f"[SDR] DAB reading #{reading_num} skipped — could not tune")
        return False

    # Discard everything captured before/during the retune, then dwell on 12A
    # and average the resulting spectra.
    _settle(sdr, max(SETTLE_S, 0.25))

    freqs, power = _capture_spectrum(sdr, dwell_s=DAB_DWELL_S)
    if freqs is None:
        print(f"[SDR] DAB reading #{reading_num} skipped — read failed")
        return False

    active_dbm, noise_dbm, snr_db = _measure(
        freqs, power,
        _gain_comp_db(sdr),
        DB_OFFSET_DB,
        sample_rate=sdr.sample_rate,
        lo_offset=DAB_12A_FREQ_HZ - float(sdr.center_freq),
        filter_bw=DAB_BANDWIDTH_HZ,
        # Use the whole usable span (0.9 x Fs): the floor has to come from the
        # narrow gaps between the DAB blocks, so a 1.536 MHz analysis window
        # would leave no off-channel bins at all.
        analysis_bw=None,
        dc_guard=0.0,
        noise_guard=DAB_NOISE_GUARD,
        dc_notch=DAB_DC_NOTCH,
        label="DAB 12A",
    )
    if active_dbm is None:
        print(f"[SDR] DAB reading #{reading_num} skipped — 12A is outside the "
              f"analysed span at {sdr.sample_rate/1e6:.3f} MSps")
        return False

    ts = datetime.datetime.now()

    try:
        reading = SweepData(
            timestamp=ts,
            frequency_mhz=DAB_FREQ_KHZ,
            power_dbfs=round(float(active_dbm), 2),
            floor_dbfs=round(float(noise_dbm), 2),
            snr_db=round(float(snr_db), 2),
            sdr_id=SDR_ID
        )
        db.add(reading)
        db.commit()
        print(
            f"  DAB #{reading_num} | Power: {active_dbm:.1f} | Floor: {noise_dbm:.1f} | "
            f"SNR: {snr_db:.1f} | dwell={DAB_DWELL_S:.2f}s"
        )
        return True
    except Exception as e:
        print(f"[DB ERROR] DAB save failed: {e}")
        db.rollback()
        return False


# ---------------------------------------------------------------------------
# Interleaved FM + DAB+ sweep
# ---------------------------------------------------------------------------
def _fm_channel_list():
    """86.0 - 109.0 MHz in 100 kHz steps — the grid site_4.py sweeps."""
    return np.round(np.arange(86.0, 109.1, 0.1), 1)


def _quick_step_args(sdr):
    """Return (dwell_s, n_frames) for one Quick-mode channel.

    QUICK_MODE_FRAMES behaves exactly like site_4.py (32 frames = ~131 ms at
    2.0 MSps); an explicit QUICK_MODE_DWELL_MS overrides it.
    """
    if QUICK_MODE_DWELL_S is not None:
        return QUICK_MODE_DWELL_S, None
    return None, QUICK_MODE_FRAMES


def _sweep(sdr, db, SweepData, fm_batch_size=10, dwell_s=None, n_frames=None,
           title="Sweep") -> tuple:
    """Per-channel FM sweep, with a DAB+ 12A reading every fm_batch_size steps.

    Quick and Slow mode run the identical measurement; only the dwell per
    channel differs, exactly like site_4.py's Quick/Slow split.
    Returns (fm_count, dab_count) of saved records.
    """
    frequencies_mhz = _fm_channel_list()
    fm_saved = 0
    dab_saved = 0
    dab_reading_num = 0

    if n_frames is not None:
        per_step = (f"{n_frames} FFT frames "
                    f"(~{n_frames * FFT_SIZE / sdr.sample_rate * 1e3:.0f} ms)")
    else:
        per_step = f"{dwell_s * 1e3:.0f} ms"
    print(f"\n[{title}] {len(frequencies_mhz)} FM channels, {per_step} per "
          f"channel, DAB+ 12A every {fm_batch_size} channels")
    print("-" * 74)

    for i, freq in enumerate(frequencies_mhz):
        if not running:
            break

        if fm_single_step(sdr, db, SweepData, freq,
                          dwell_s=dwell_s, n_frames=n_frames):
            fm_saved += 1

        if (i + 1) % fm_batch_size == 0:
            if not running:
                break
            dab_reading_num += 1
            if dab_single_reading(sdr, db, SweepData, dab_reading_num):
                dab_saved += 1

    return fm_saved, dab_saved


def interleaved_sweep(sdr, db, SweepData, fm_batch_size: int = 10) -> tuple:
    """Slow mode: SLOW_MODE_STEP_DELAY_S of IQ averaged per channel."""
    return _sweep(sdr, db, SweepData, fm_batch_size=fm_batch_size,
                  dwell_s=SLOW_MODE_STEP_DELAY_S, title="Slow Sweep")


def quick_interleaved_sweep(sdr, db, SweepData, fm_batch_size: int = 10) -> tuple:
    """Quick mode: QUICK_MODE_FRAMES FFT frames per channel (site_4 default)."""
    dwell_s, n_frames = _quick_step_args(sdr)
    return _sweep(sdr, db, SweepData, fm_batch_size=fm_batch_size,
                  dwell_s=dwell_s, n_frames=n_frames, title="Quick Sweep")


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------
def ask_mode() -> str:
    """
    Auto-selects Q (Quick) mode after a 10-second countdown.
    Press S + Enter during the wait to override to Slow mode.
    Returns 'Q' or 'S'.
    """
    import threading
    TIMEOUT = 10
    result = []

    def _read():
        try:
            answer = input("").strip().upper()
            if answer in ("Q", "S"):
                result.append(answer)
        except (EOFError, KeyboardInterrupt):
            pass

    if QUICK_MODE_DWELL_S is not None:
        quick_label = f"Quick (per-channel, {QUICK_MODE_DWELL_S*1000:.0f} ms)"
    else:
        approx_ms = QUICK_MODE_FRAMES * FFT_SIZE / 2.0e6 * 1000
        quick_label = (f"Quick (per-channel, {QUICK_MODE_FRAMES} frames, "
                       f"~{approx_ms:.0f} ms)")
    slow_label = f"Slow (per-channel, {SLOW_MODE_STEP_DELAY_S*1000:.0f} ms)"
    print(f"\nSelect sweep mode — [Q] {quick_label} / [S] {slow_label}")
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


# ---------------------------------------------------------------------------
# Calibration / identity
# ---------------------------------------------------------------------------
def _default_sdr_id(sdr):
    """site_1 for the RTL-SDR (HTOOL SDR 6), site_2 for the nRSP-ST."""
    if _RTL_SDR_TYPE is not None and isinstance(sdr, _RTL_SDR_TYPE):
        return "site_1"
    return "site_2"


def calibrate(sdr):
    """Return the dB offset that turns gain-compensated dBFS into dBm.

    Measures the channel power at CAL_FREQ with the normal measurement chain
    and returns CAL_DBM minus that reading, so every later reading is absolute.
    CAL_OFFSET_DB short-circuits this with a constant from an earlier run, and
    SKIP_CAL=1 disables it entirely. On any failure it returns 0.0 rather than
    aborting the sweep - the readings are then relative, not absolute.
    """
    if CAL_OFFSET_DB is not None:
        print(f"[CAL] Using fixed CAL_OFFSET_DB={CAL_OFFSET_DB:.2f} dB "
              f"(no runtime calibration)")
        return CAL_OFFSET_DB
    if SKIP_CAL:
        print("[CAL] SKIP_CAL=1 — no calibration, logging gain-compensated dBFS")
        return 0.0

    print(f"[CAL] Calibrating on {CAL_FREQ/1e6:.3f} MHz, which should read "
          f"{CAL_DBM:.1f} dBm ({CAL_DWELL_S:.2f}s dwell)...")
    raw_dbm, raw_floor, raw_snr = _measure_fm_channel(
        sdr, CAL_FREQ, dwell_s=CAL_DWELL_S, offset_db=0.0, label="CAL",
    )
    if raw_dbm is None:
        print("[CAL] Calibration measurement failed — continuing with 0.00 dB "
              "offset (levels are relative, not absolute)")
        return 0.0

    offset = CAL_DBM - raw_dbm
    print(f"[CAL] Reference reads {raw_dbm:.2f} dB (floor {raw_floor:.2f} dB, "
          f"SNR {raw_snr:.1f} dB) before calibration")
    if raw_snr < CAL_MIN_SNR_DB:
        print(f"[CAL] WARNING: only {raw_snr:.1f} dB SNR on the calibration "
              f"station (< {CAL_MIN_SNR_DB:.0f} dB). Calibrating on a weak or "
              f"absent signal ties the constant to the receiver noise floor — "
              f"pick a strong, stable station with CAL_FREQ_MHZ/CAL_DBM.")
    print(f"[CAL] dBm offset set to {offset:.2f} dB "
          f"(pin it with CAL_OFFSET_DB={offset:.2f} to skip this next time)")
    return offset


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global running, DB_OFFSET_DB, SDR_ID

    print("=" * 74)
    print("FM + DAB+ per-channel sweep (site_4 measurement) with PostgreSQL logging")
    print("=" * 74)
    print(f"DB      : {DB_HOST}:{DB_PORT}/{DB_NAME}  table=rf_sweep_data")
    print(f"FM      : 86-109 MHz, 100 kHz steps, DAB+ 12A every 10 channels")
    print(f"DAB     : 223.936 MHz (stored as {DAB_FREQ_KHZ} kHz), "
          f"{DAB_BANDWIDTH_HZ/1e6:.3f} MHz block")
    print(f"Measure : channel power over {FILTER_BW/1e3:.0f} kHz, floor from the "
          f"clipped mean of the surrounding bins")
    print(f"          LO offset {LO_OFFSET/1e3:.0f} kHz, analysed span "
          f"{ANALYSIS_BW/1e6:.3f} MHz, {FFT_SIZE}-point FFT")
    print(f"Stored  : power_dbfs/floor_dbfs are calibrated dBm, snr_db is their "
          f"difference")
    print("=" * 74)

    mode = ask_mode()
    if mode == "Q":
        if QUICK_MODE_DWELL_S is not None:
            print(f"\n[Mode] QUICK — per-channel, {QUICK_MODE_DWELL_S*1000:.0f} ms per step")
        else:
            print(f"\n[Mode] QUICK — per-channel, {QUICK_MODE_FRAMES} FFT frames per step")
    else:
        print(f"\n[Mode] SLOW  — per-channel, {SLOW_MODE_STEP_DELAY_S*1000:.0f} ms per step")

    # Setup database
    Session, SweepData = setup_db()
    db = Session()

    # Open SDR
    sdr = _open_sdr()
    _debug_sdr(sdr, "After open")

    if not SDR_ID:
        SDR_ID = _default_sdr_id(sdr)
        print(f"[SDR] sdr_id not set, using '{SDR_ID}' for this backend")
    print(f"[SDR] Logging as sdr_id='{SDR_ID}', "
          f"fs={sdr.sample_rate/1e6:.3f} MSps, "
          f"RBW={sdr.sample_rate/FFT_SIZE:.0f} Hz, "
          f"gain compensation {_gain_comp_db(sdr):+.1f} dB")
    if isinstance(sdr, _SDRconnectWebSocketDevice):
        print("[SDR] nRSP-ST: keep the gain and the AGC fixed in SDRconnect — "
              "this script cannot read them back, so a gain change there "
              "invalidates the calibration.")

    # Calibrate dBm offset against a strong known station
    DB_OFFSET_DB = calibrate(sdr)

    loop_count = 0
    total_fm_records = 0
    total_dab_records = 0

    try:
        while running:
            loop_count += 1
            print(f"\n{'='*60}")
            print(f"SWEEP CYCLE #{loop_count}  [{('QUICK' if mode == 'Q' else 'SLOW')}]")
            print(f"{'='*60}")

            try:
                if mode == "Q":
                    fm_saved, dab_saved = quick_interleaved_sweep(sdr, db, SweepData, fm_batch_size=10)
                else:
                    fm_saved, dab_saved = interleaved_sweep(sdr, db, SweepData, fm_batch_size=10)
            except Exception as sweep_err:
                print(f"\n[SDR ERROR] Sweep failed: {sweep_err}")
                print(f"[SDR] Attempting reconnect in {SDR_RECONNECT_DELAY}s...")
                try:
                    sdr.close()
                except Exception:
                    pass
                for _ in range(SDR_RECONNECT_DELAY):
                    if not running:
                        break
                    time.sleep(1)
                if not running:
                    break
                try:
                    sdr = _open_sdr()
                    print("[SDR] Reconnected successfully.")
                    # The gain (and therefore the calibration constant) is
                    # re-applied from scratch by _open_sdr(), so re-measure it.
                    DB_OFFSET_DB = calibrate(sdr)
                except Exception as reopen_err:
                    print(f"[SDR] Reconnect failed: {reopen_err}, retrying in {SDR_RECONNECT_DELAY}s...")
                    time.sleep(SDR_RECONNECT_DELAY)
                continue

            print(f"\n[Cycle complete] FM: {fm_saved}, DAB+: {dab_saved}")
            total_fm_records += fm_saved
            total_dab_records += dab_saved

            if not running:
                break

            print(f"\n[Total] FM: {total_fm_records}, DAB+: {total_dab_records}")
            print("Sleeping 2 seconds before next cycle...")
            time.sleep(2)

    finally:
        try:
            sdr.close()
        except Exception:
            pass
        db.close()
        print(f"\n[Done] Cycles: {loop_count}, FM: {total_fm_records}, DAB+: {total_dab_records}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Interrupted] Exiting.")
