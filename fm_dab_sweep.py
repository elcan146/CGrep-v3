"""
quick_fm_dab_sweep.py — FM Sweep + DAB+ 12A Monitor with PostgreSQL logging
============================================================================
Alternates between:
  1. FM sweep (86-109 MHz, 0.1 MHz steps) - logs all readings
  2. DAB+ 12A monitor (10 readings) - logs all readings

Database: sdr_db
Table: rf_sweep_data (id, timestamp, frequency_mhz, power_dbfs, floor_dbfs, snr_db)

For DAB: frequency stored as 223936 (kHz integer)

Usage:
    python quick_fm_dab_sweep.py

    On startup you will be prompted:
        Q = Quick mode (wideband 1.5 MHz chunks, ~1s dwell per chunk)
        S = Slow mode  (original per-channel sweep)

Environment:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, POSTGRES_PASSWORD
    SDR_GAIN (default: 20.2 (KEEEP IT LIKE THIS DONT CHANGE))
    SDR_TYPE (auto|rtl|rtlsdr|pop|msi|rsp1|soapy|sdrplay|sdruno|sdrconnect; default: auto)
    SDR_INDEX (device index for RTL-SDR or SoapySDR; default: 0)
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
    SDRCONNECT_RETUNE_SETTLE_MS (extra settle time after retune before trusting IQ; default: 200)
    SLOW_MODE_STEP_DELAY_MS (delay between each channel step in Slow mode; default: 500)
    QUICK_MODE_DWELL_MS (how long to collect IQ per 1.5 MHz wideband chunk in Quick mode; default: 1000)
    DAB_DWELL_MS (how long to collect IQ per DAB+ 12A reading; default: 1000)
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

# ---------------------------------------------------------------------------
# SDR Identity — change this to whatever you want
# ---------------------------------------------------------------------------
SDR_ID = os.getenv("SDR_ID", "site_1")

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
# Dwell time per 1.5 MHz wideband chunk in Quick mode.
# At least 1 second is recommended so the FFT can average enough frames to
# see weak/fading signals reliably.
QUICK_MODE_DWELL_S = float(os.getenv("QUICK_MODE_DWELL_MS", "1000")) / 1000.0
# Dwell time per DAB+ 12A reading.
DAB_DWELL_S = float(os.getenv("DAB_DWELL_MS", "1000")) / 1000.0

# DB Configuration (from sdr_collector.py pattern)
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")  # Tailscale IP
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sdr_db")

# SDR Settings
FM_OFFSET_HZ = 333e3
DAB_12A_FREQ_HZ = 223.936e6
DAB_FREQ_KHZ = 223936  # For SQL storage
DAB_BANDWIDTH_HZ = 1.536e6
FFT_SIZE = 8192
CALIBRATION_DB = 43.0  # dBm calibration (was 0.0 for raw dBFS recalibration)
DB_OFFSET_DB = 107.0   # dBm output offset (was 0.0 for raw dBFS recalibration)

running = True
SDR_RECONNECT_DELAY = 5   # seconds to wait before re-opening the SDR after a USB error

FM_CHANNEL_HALF_BW_HZ = 75e3  # integrate FM channel power over +/- 75 kHz
NOISE_FLOOR_EMA_ALPHA = 0.25   # slow-decay rolling noise-floor weight (0..1)
_noise_floor_ema = None


class _SoapySdrDevice:
    """Minimal RTL-SDR-like wrapper around a SoapySDR device (e.g. SDRplay RSP)."""

    def __init__(self, device_index=0, antenna=None, device_args=None):
        import SoapySDR

        self.SoapySDR = SoapySDR
        if device_args is not None:
            args = device_args
        else:
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

        # Miri / SDRplay clones do not always expose a gain-mode API; ignore
        # failures so the device still opens with a sensible default.
        try:
            dev.setGainMode(SOAPY_RX, ch, False)
        except Exception:
            pass
        try:
            dev.setGain(SOAPY_RX, ch, self._gain)
        except Exception:
            pass

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
    from rtlsdr import RtlSdr

    sdr = RtlSdr(device_index=SDR_INDEX)
    sdr.sample_rate = 2.4e6
    sdr.err_ppm = 0
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    sdr.gain = SDR_GAIN
    print(f"[SDR] Opened RTL-SDR (index {SDR_INDEX}): gain={sdr.gain} dB")
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


def _kwargs_get(kwargs, key, default=None):
    """Safely get a value from a SoapySDRKwargs result (SWIG map-like object)."""
    try:
        return kwargs.get(key, default)
    except AttributeError:
        pass
    try:
        return dict(kwargs).get(key, default)
    except (TypeError, ValueError):
        pass
    try:
        return kwargs[key]
    except KeyError:
        return default


class _SDRplayApiDevice:
    """RTL-SDR-like wrapper around the official SDRplay API (pysdrplay)."""

    def __init__(self, device_index=0, sample_rate=2.0e6, center_freq=100e6, gain=None):
        from pysdrplay import SDRPlayRadio, Bw, If, TunerSelect

        self._q = queue.Queue(maxsize=256)
        self._remainder = np.array([], dtype=np.complex64)
        self._running = False

        radio = SDRPlayRadio()
        devices = radio.devices
        if not devices:
            radio.close()
            raise RuntimeError("pysdrplay did not find any SDRplay devices")
        if device_index >= len(devices):
            radio.close()
            raise RuntimeError(
                f"SDR_INDEX {device_index} out of range; "
                f"found {len(devices)} SDRplay device(s)"
            )

        radio.select_device(devices[device_index], tuner=TunerSelect.A)

        # Use zero-IF and match the bandwidth to the sample rate.
        radio.set_if_mode(If.ZERO)
        self._sample_rate = float(sample_rate)
        radio.set_sample_rate(int(self._sample_rate))
        radio.set_bandwidth(self._nearest_bw(self._sample_rate))

        self._gain = float(gain if gain is not None else SDR_GAIN)
        radio.set_gain(lna_state=0, gr_db=int(max(0, min(59, self._gain))))
        radio.set_agc(False)

        self._radio = radio
        self._center_freq = int(center_freq)
        self._start_stream()
        self.set_center_freq(self._center_freq)

        import atexit
        atexit.register(self.close)

    @staticmethod
    def _nearest_bw(sample_rate):
        from pysdrplay import Bw
        sample_rate = float(sample_rate)
        if sample_rate <= 0.3e6:
            return Bw.BW_0_300
        if sample_rate <= 0.6e6:
            return Bw.BW_0_600
        if sample_rate <= 1.6e6:
            return Bw.BW_1_536
        if sample_rate <= 5.5e6:
            return Bw.BW_5_000
        if sample_rate <= 6.5e6:
            return Bw.BW_6_000
        if sample_rate <= 7.5e6:
            return Bw.BW_7_000
        return Bw.BW_8_000

    def _on_samples(self, xi, xq, params, num_samples, reset, ctx):
        if not self._running:
            return
        i = np.ctypeslib.as_array(xi, shape=(num_samples,)).copy()
        q = np.ctypeslib.as_array(xq, shape=(num_samples,)).copy()
        try:
            self._q.put((i + 1j * q).astype(np.complex64), block=False)
        except queue.Full:
            pass

    def _start_stream(self):
        self._running = True
        self._radio.start(self._on_samples)

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

    def set_center_freq(self, hz):
        self._radio.set_frequency(int(hz))
        self._center_freq = int(hz)

    @property
    def center_freq(self):
        return self._center_freq

    @center_freq.setter
    def center_freq(self, hz):
        self.set_center_freq(hz)

    @property
    def sample_rate(self):
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value):
        from pysdrplay import Bw
        value = float(value)
        self._radio.set_sample_rate(int(value))
        self._radio.set_bandwidth(self._nearest_bw(value))
        self._sample_rate = value

    @property
    def gain(self):
        return self._gain

    @gain.setter
    def gain(self, value):
        from pysdrplay import Bw
        self._gain = float(value)
        self._radio.set_gain(lna_state=0, gr_db=int(max(0, min(59, self._gain))))

    def set_direct_sampling(self, value):
        # Direct sampling is not applicable to the SDRplay API backend.
        pass

    def set_manual_gain_enabled(self, value):
        self._radio.set_agc(not bool(value))

    def set_agc_mode(self, value):
        self._radio.set_agc(bool(value))

    def close(self):
        if self._running:
            try:
                self._radio.stop()
            except Exception:
                pass
            self._running = False
        try:
            self._radio.close()
        except Exception:
            pass


def _open_sdrplay_api():
    """Open an SDRplay RSP1/clone via the official SDRplay API (pysdrplay)."""
    sdr = _SDRplayApiDevice(device_index=SDR_INDEX)
    print(
        f"[SDR] Opened SDRplay API device {SDR_INDEX}: "
        f"fs={sdr.sample_rate / 1e6:.3f} MHz, gain={sdr.gain} dB"
    )
    return sdr


def _open_sdruno():
    """Open an RSP1/SDRuno SDR via SoapySDR, selecting the SDRplay/SDRuno driver."""
    import SoapySDR

    results = list(SoapySDR.Device.enumerate())
    if not results:
        raise RuntimeError("SoapySDR did not find any devices")

    filtered = [
        r for r in results
        if _kwargs_get(r, "driver", "").lower() in ("sdruno", "sdrplay", "rsp1")
    ]
    if not filtered:
        raise RuntimeError(
            "SoapySDR did not find any SDRplay/SDRuno "
            "(driver=sdruno/sdrplay/rsp1) devices"
        )
    if SDR_INDEX >= len(filtered):
        raise RuntimeError(
            f"SDR_INDEX {SDR_INDEX} out of range; "
            f"found {len(filtered)} SDRplay/SDRuno device(s)"
        )

    sdr = _SoapySdrDevice(device_args=filtered[SDR_INDEX], antenna=SDRPLAY_ANTENNA)
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    sdr.gain = SDR_GAIN
    print(
        f"[SDR] Opened SDRuno/SDRplay RSP1 device {SDR_INDEX}: "
        f"fs={sdr.sample_rate / 1e6:.3f} MHz, gain={sdr.gain} dB"
    )
    return sdr


def _open_pop_msi_sdr():
    """Open a POP MSI SDR Pro / MSI.SDR / RSP1 clone via SoapySDR.

    These devices are Mirics MSi2500/MSi001 clones of the SDRplay RSP1 and
    usually appear through the SoapySDR Miri driver (driver=miri/mirisdr)
    or the SDRplay/SDRuno driver.  We try the Miri-specific names first, then
    fall back to the same filter used for SDRuno/SDRplay.
    """
    import SoapySDR

    results = list(SoapySDR.Device.enumerate())
    if not results:
        raise RuntimeError("SoapySDR did not find any devices")

    pop_msi_drivers = {"miri", "mirisdr", "soapyMiri"}
    sdrplay_drivers = {"sdruno", "sdrplay", "rsp1"}

    # Prefer the Miri driver when available (POP MSI / MSI.SDR clones).
    filtered = [
        r for r in results
        if _kwargs_get(r, "driver", "").lower() in pop_msi_drivers
    ]
    # Fall back to SDRplay/SDRuno/RSP1-labelled devices.
    if not filtered:
        filtered = [
            r for r in results
            if _kwargs_get(r, "driver", "").lower() in sdrplay_drivers
            or "rsp1" in _kwargs_get(r, "label", "").lower()
            or "msi" in _kwargs_get(r, "label", "").lower()
            or "pop" in _kwargs_get(r, "label", "").lower()
        ]

    if not filtered:
        print(f"[SDR] SoapySDR discovered: {results}")
        raise RuntimeError(
            "SoapySDR did not find any POP MSI / MSI.SDR / RSP1-compatible "
            "devices (looked for drivers: miri, mirisdr, sdruno, sdrplay, rsp1)"
        )
    if SDR_INDEX >= len(filtered):
        raise RuntimeError(
            f"SDR_INDEX {SDR_INDEX} out of range; "
            f"found {len(filtered)} POP MSI / RSP1-compatible device(s)"
        )

    sdr = _SoapySdrDevice(device_args=filtered[SDR_INDEX], antenna=SDRPLAY_ANTENNA)
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    try:
        sdr.gain = SDR_GAIN
    except Exception:
        pass

    drv = _kwargs_get(filtered[SDR_INDEX], "driver", "unknown")
    label = _kwargs_get(filtered[SDR_INDEX], "label", "")
    print(
        f"[SDR] Opened POP MSI / RSP1 clone (driver={drv!r}, label={label!r}) "
        f"index {SDR_INDEX}: fs={sdr.sample_rate / 1e6:.3f} MHz"
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
            print(f"[SDR] RTL-SDR not available ({e}); trying SDRuno/SDRplay...")

    if SDR_TYPE in ("auto", "sdruno"):
        try:
            return _open_sdruno()
        except ImportError as e:
            if SDR_TYPE == "sdruno":
                raise RuntimeError(
                    "SoapySDR is not installed. Build/install SoapySDR and the "
                    "SoapySDRPlay support module for SDRuno/RSP1."
                ) from e
            errors.append(f"SDRuno SoapySDR not installed")
            print(f"[SDR] SDRuno SoapySDR not installed; trying POP MSI/SDRplay...")
        except Exception as e:
            if SDR_TYPE == "sdruno":
                raise RuntimeError(f"Could not open SDRuno/RSP1 device: {e}") from e
            errors.append(f"SDRuno: {e}")
            print(f"[SDR] SDRuno not available ({e}); trying POP MSI/SDRplay...")

    if SDR_TYPE in ("auto", "pop", "msi", "rsp1"):
        try:
            return _open_pop_msi_sdr()
        except ImportError as e:
            if SDR_TYPE in ("pop", "msi", "rsp1"):
                raise RuntimeError(
                    "SoapySDR is required for POP MSI SDR Pro / RSP1 clones. "
                    "Install SoapySDR with the Miri or SDRplay module."
                ) from e
            errors.append("POP MSI SoapySDR not installed")
            print(f"[SDR] POP MSI SoapySDR not installed; trying SDRplay API/SoapySDR...")
        except Exception as e:
            if SDR_TYPE in ("pop", "msi", "rsp1"):
                # RSP1 clones can also be driven through the official SDRplay API.
                try:
                    return _open_sdrplay_api()
                except ImportError:
                    pass
                except Exception as e2:
                    raise RuntimeError(
                        f"Could not open POP MSI SDR Pro / RSP1 device: {e} "
                        f"(SDRplay API fallback also failed: {e2})"
                    ) from e
                raise RuntimeError(f"Could not open POP MSI SDR Pro / RSP1 device: {e}") from e
            errors.append(f"POP MSI: {e}")
            print(f"[SDR] POP MSI not available ({e}); trying SDRplay API/SoapySDR...")

    if SDR_TYPE in ("auto", "soapy", "sdrplay"):
        try:
            return _open_sdrplay_api()
        except ImportError as e:
            install_msg = (
                "pysdrplay is not installed. Install it with:\n"
                "  conda run -n sdr python -m pip install git+https://git.sr.ht/~cg/pysdrplay"
            )
            if SDR_TYPE in ("soapy", "sdrplay"):
                raise RuntimeError(install_msg) from e
            errors.append("pysdrplay not installed")
            print(f"[SDR] pysdrplay not installed; trying SoapySDR/SDRplay...")
        except Exception as e:
            if SDR_TYPE in ("soapy", "sdrplay"):
                raise RuntimeError(f"Could not open SDRplay API device: {e}") from e
            errors.append(f"pysdrplay: {e}")
            print(f"[SDR] pysdrplay not available ({e}); trying SoapySDR/SDRplay...")
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


def _set_freq(sdr, freq_hz, retries=3):
    """Set center_freq with retries on transient errors.
    Raises on fatal USB errors (pipe, access violation) to trigger reconnect."""
    _FATAL_PATTERNS = ("pipe", "access violation", "LIBUSB_ERROR_PIPE", "LIBUSB_ERROR_NO_DEVICE")
    for attempt in range(retries):
        try:
            sdr.center_freq = int(freq_hz)
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


def _get_fm_offset_hz(sdr):
    """
    Return the LO offset to use for FM sweeps with the given backend.

    RTL-SDR has a strong DC spike at the tuned centre, so we tune the LO
    ~333 kHz below the target and account for it in software.

    SDRplay (SoapySDR) and SDRconnect (nRSP-ST) do not need this, and using
    the same offset pushes the right-hand edge of each wideband chunk close
    to (or past) the Nyquist limit at 2.0 MSPS, causing spurious detections
    (e.g. a "strong" station at a frequency that is clean in SDR#/SDRconnect).
    """
    if isinstance(sdr, (_SoapySdrDevice, _SDRconnectWebSocketDevice, _SDRplayApiDevice)):
        return 0.0
    return FM_OFFSET_HZ


def _capture_averaged_dbfs(sdr, dwell_s, frame_size=32768):
    """
    Capture back-to-back FFT frames for at least ~dwell_s seconds and average
    their power spectra (Welch-style). A single 32768-sample snapshot is only
    ~13.6ms of dwell at 2.4 MSPS -- too short to reliably confirm a real,
    fading/marginal signal. Averaging multiple consecutive frames over the
    full step time gives the receiver time to actually "see" the channel
    before moving on, and reduces noise variance by ~sqrt(N).

    The function enforces a minimum wall-clock dwell time so the SDR stays
    on the target frequency for the requested duration even if the backend
    returns buffered IQ quickly.

    Returns (smoothed_dbfs, bin_freqs) or (None, None) on read failure.
    """
    n_frames_target = max(1, round(dwell_s * sdr.sample_rate / frame_size))
    window = np.blackman(frame_size)

    # One single read for all frames instead of n_frames separate calls.
    # RTL-SDR's direct USB reads have negligible per-call overhead, but
    # SDRconnect's WebSocket-queue read_samples() has real per-call latency
    # that multiplies badly across dozens of small calls -- a single big
    # read achieves the same total dwell/averaging without that penalty.
    start = time.time()
    all_samples = _read_samples(sdr, n_frames_target * frame_size)
    elapsed = time.time() - start

    # Make sure we actually sit on this frequency for the requested time.
    if elapsed < dwell_s:
        time.sleep(dwell_s - elapsed)

    if all_samples is None or len(all_samples) == 0:
        return None, None
    all_samples = np.asarray(all_samples, dtype=np.complex64)

    # If the backend returned fewer samples than requested, average whatever
    # valid complete frames we got instead of crashing on a short buffer.
    n_frames = len(all_samples) // frame_size
    if n_frames == 0:
        return None, None

    power_sum = None
    for i in range(n_frames):
        frame = all_samples[i * frame_size:(i + 1) * frame_size]
        frame = frame - np.mean(frame)
        windowed = frame * window
        fft_data = np.fft.fftshift(np.fft.fft(windowed))
        fft_mag = np.abs(fft_data) / frame_size
        power = fft_mag ** 2
        power_sum = power if power_sum is None else power_sum + power

    avg_power = power_sum / n_frames
    dbfs = 10 * np.log10(avg_power + 1e-24) + CALIBRATION_DB

    kernel = np.ones(50) / 50
    smoothed = np.convolve(dbfs, kernel, mode='same')

    bin_freqs = np.fft.fftshift(np.fft.fftfreq(frame_size, 1 / sdr.sample_rate))
    return smoothed, bin_freqs

# ---------------------------------------------------------------------------
# Channel power / rolling noise-floor helpers
# ---------------------------------------------------------------------------
def _linear_avg_dbfs(dbfs_values):
    """Average the linear power of dBFS/dBm bins and return dB."""
    if dbfs_values is None or len(dbfs_values) == 0:
        return None
    linear = 10 ** (dbfs_values / 10.0)
    return float(10 * np.log10(np.mean(linear) + 1e-24))


def _reset_noise_floor_ema():
    global _noise_floor_ema
    _noise_floor_ema = None


def _rolling_floor_dbfs(measured_floor):
    """Slow-decay exponential moving average of the measured noise floor."""
    global _noise_floor_ema
    if measured_floor is None:
        return _noise_floor_ema
    if _noise_floor_ema is None:
        _noise_floor_ema = float(measured_floor)
    else:
        _noise_floor_ema = (
            NOISE_FLOOR_EMA_ALPHA * measured_floor
            + (1.0 - NOISE_FLOOR_EMA_ALPHA) * _noise_floor_ema
        )
    return _noise_floor_ema


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
# FM Sweep (Slow — original per-channel)
# ---------------------------------------------------------------------------
def fm_sweep(sdr, db, SweepData) -> int:
    """
    FM sweep 86-109 MHz, 0.1 MHz steps.
    Saves each reading to DB in real-time.
    Returns count of readings saved.
    """
    frequencies_mhz = np.round(np.arange(86.0, 109.1, 0.1), 1)
    saved_count = 0
    
    print(f"\n[FM Sweep] 86-109 MHz, {len(frequencies_mhz)} steps...")
    print(f"{'Freq':<7} | {'Power':<6} | {'Floor':<6} | {'SNR':<4}")
    print("-" * 35)

    _reset_noise_floor_ema()
    
    for freq in frequencies_mhz:
        if not running:
            break
        
        target_hz = freq * 1e6
        if not _set_freq(sdr, target_hz - _get_fm_offset_hz(sdr)):
            print(f"[SDR] Skipping {freq} MHz — could not tune")
            continue

        for _ in range(3):
            _read_samples(sdr, 256 * 1024)

        # Spend the full step dwell time actually sampling & averaging this
        # channel (not idling) so a weak/fading signal has time to show up.
        smoothed, freqs = _capture_averaged_dbfs(sdr, SLOW_MODE_STEP_DELAY_S)
        if smoothed is None:
            print(f"[SDR] Skipping {freq} MHz — read failed")
            continue

        actual_freqs = sdr.center_freq + freqs
        
        half_bw = FM_CHANNEL_HALF_BW_HZ
        target_mask = (actual_freqs >= target_hz - half_bw) & (actual_freqs <= target_hz + half_bw)
        
        # Measure floor far from the WFM signal, adjacent stations and the DC spike
        # so it matches the flat per-spectrum noise floor shown in SDR#.
        signal_exclusion = (actual_freqs >= target_hz - 250e3) & (actual_freqs <= target_hz + 250e3)
        dc_mask = (actual_freqs >= sdr.center_freq - 20e3) & (actual_freqs <= sdr.center_freq + 20e3)
        # Robust noise reference: 25th percentile of quietest non-DC bins.
        # Blank any sideband bin more than 6 dB above that reference.
        base_noise = float(np.percentile(smoothed[~dc_mask], 25))
        noise_mask = ~signal_exclusion & ~dc_mask & (smoothed <= base_noise + 6.0)
        noise_values = smoothed[noise_mask]
        
        power_dbfs = _linear_avg_dbfs(smoothed[target_mask])
        if power_dbfs is None:
            print(f"[SDR] Skipping {freq} MHz - no bins in target mask")
            continue
        measured_floor = _linear_avg_dbfs(noise_values)
        if measured_floor is None:
            measured_floor = base_noise
        floor_dbfs = _rolling_floor_dbfs(measured_floor)
        snr = power_dbfs - floor_dbfs
        
        # Store frequency as integer kHz (e.g., 86000 for 86.0 MHz)
        freq_khz = int(freq * 1000)
        
        # Save to DB immediately (real-time)
        try:
            reading = SweepData(
                timestamp=datetime.datetime.now(),
                frequency_mhz=freq_khz,
                power_dbfs=round(power_dbfs + DB_OFFSET_DB, 2),
                floor_dbfs=round(floor_dbfs + DB_OFFSET_DB, 2),
                snr_db=round(snr, 2),
                sdr_id=SDR_ID
            )
            db.add(reading)
            db.commit()
            saved_count += 1
        except Exception as e:
            print(f"[DB ERROR] FM save failed at {freq} MHz: {e}")
            db.rollback()
        
        print(f"{freq:<7.1f} | {power_dbfs:<6.0f} | {floor_dbfs:<6.0f} | {snr:<4.0f}")
    
    return saved_count

# ---------------------------------------------------------------------------
# DAB+ 12A Monitor
# ---------------------------------------------------------------------------
def dab12a_monitor(sdr, db, SweepData) -> int:
    """
    DAB+ 12A monitor, 10 readings.
    Saves each reading to DB in real-time.
    Returns count of readings saved.
    """
    saved_count = 0
    
    print(f"\n[DAB+ 12A Monitor] 223.936 MHz, 10 readings...")
    print(f"{'#':>4} | {'Time':>8} | {'Status':>4} | {'Power':>8} | {'Floor':>8} | {'SNR':>6}")
    print("-" * 55)
    
    last_state = False
    on_threshold = 6.0
    off_threshold = 4.5
    
    for i in range(10):
        if not running:
            break
        
        sdr.center_freq = int(DAB_12A_FREQ_HZ)
        
        time.sleep(0.06)
        for _ in range(3):
            _ = sdr.read_samples(256 * 1024)
        
        samples = sdr.read_samples(FFT_SIZE * 4)
        samples = np.asarray(samples, dtype=np.complex64)[-FFT_SIZE:]
        
        window = np.blackman(len(samples))
        windowed = samples * window
        
        fft_data = np.fft.fftshift(np.fft.fft(windowed))
        fft_mag = np.abs(fft_data) / len(samples)
        dbfs = 20 * np.log10(fft_mag + 1e-12) + CALIBRATION_DB
        
        kernel = np.ones(50) / 50
        smoothed = np.convolve(dbfs, kernel, mode='same')
        
        freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/sdr.sample_rate))
        actual_freqs = sdr.center_freq + freqs
        
        half_bw = DAB_BANDWIDTH_HZ / 2
        signal_mask = (actual_freqs >= DAB_12A_FREQ_HZ - half_bw) & \
                      (actual_freqs <= DAB_12A_FREQ_HZ + half_bw)
        
        noise_mask = ((actual_freqs >= DAB_12A_FREQ_HZ - half_bw - 400e3) & \
                      (actual_freqs < DAB_12A_FREQ_HZ - half_bw)) | \
                     ((actual_freqs > DAB_12A_FREQ_HZ + half_bw) & \
                      (actual_freqs <= DAB_12A_FREQ_HZ + half_bw + 400e3))
        
        power = float(np.mean(smoothed[signal_mask]))
        floor = float(np.median(smoothed[noise_mask]))
        snr = power - floor
        
        # Hysteresis
        if snr >= on_threshold:
            last_state = True
        elif snr < off_threshold:
            last_state = False
        
        status = "ON " if last_state else "OFF"
        ts = datetime.datetime.now()
        time_str = ts.strftime("%H:%M:%S")
        
        print(f"{i+1:>4} | {time_str:>8} | {status:>4} | {power:>8.1f} | {floor:>8.1f} | {snr:>6.1f}")
        
        # Save to DB immediately (real-time)
        try:
            reading = SweepData(
                timestamp=ts,
                frequency_mhz=DAB_FREQ_KHZ,  # 223936
                power_dbfs=round(power + DB_OFFSET_DB, 2),
                floor_dbfs=round(floor + DB_OFFSET_DB, 2),
                snr_db=round(snr, 2),
                sdr_id=SDR_ID
            )
            db.add(reading)
            db.commit()
            saved_count += 1
        except Exception as e:
            print(f"[DB ERROR] DAB save failed: {e}")
            db.rollback()
        
        time.sleep(1.0)
    
    return saved_count

# ---------------------------------------------------------------------------
# Single FM step
# ---------------------------------------------------------------------------
def fm_single_step(sdr, db, SweepData, freq_mhz) -> bool:
    """
    Scan a single FM frequency and save to DB.
    Returns True if successful, False if stopped.
    """
    if not running:
        return False
    
    target_hz = freq_mhz * 1e6
    if not _set_freq(sdr, target_hz - _get_fm_offset_hz(sdr)):
        print(f"[SDR] Skipping {freq_mhz} MHz — could not tune")
        return False

    for _ in range(3):
        _read_samples(sdr, 256 * 1024)

    # Spend the full step dwell time actually sampling & averaging this
    # channel (not idling) so a weak/fading signal has time to show up.
    smoothed, freqs = _capture_averaged_dbfs(sdr, SLOW_MODE_STEP_DELAY_S)
    if smoothed is None:
        print(f"[SDR] Skipping {freq_mhz} MHz — read failed")
        return False

    actual_freqs = sdr.center_freq + freqs
    
    half_bw = FM_CHANNEL_HALF_BW_HZ
    target_mask = (actual_freqs >= target_hz - half_bw) & (actual_freqs <= target_hz + half_bw)
    
    # Measure floor far from the WFM signal, adjacent stations and the DC spike
    # so it matches the flat per-spectrum noise floor shown in SDR#.
    signal_exclusion = (actual_freqs >= target_hz - 250e3) & (actual_freqs <= target_hz + 250e3)
    dc_mask = (actual_freqs >= sdr.center_freq - 20e3) & (actual_freqs <= sdr.center_freq + 20e3)
    noise_mask = ~signal_exclusion & ~dc_mask
    
    power_dbfs = _linear_avg_dbfs(smoothed[target_mask])
    if power_dbfs is None:
        print(f"[SDR] Skipping {freq_mhz} MHz - no bins in target mask")
        return False
    measured_floor = _linear_avg_dbfs(smoothed[noise_mask])
    if measured_floor is None:
        measured_floor = float(np.percentile(smoothed[~dc_mask], 25))
    floor_dbfs = _rolling_floor_dbfs(measured_floor)
    snr = power_dbfs - floor_dbfs
    
    freq_khz = int(freq_mhz * 1000)
    
    try:
        reading = SweepData(
            timestamp=datetime.datetime.now(),
            frequency_mhz=freq_khz,
            power_dbfs=round(power_dbfs + DB_OFFSET_DB, 2),
            floor_dbfs=round(floor_dbfs + DB_OFFSET_DB, 2),
            snr_db=round(snr, 2),
            sdr_id=SDR_ID
        )
        db.add(reading)
        db.commit()
        print(f"  FM {freq_mhz:.1f} MHz | Power: {power_dbfs:.0f} | Floor: {floor_dbfs:.0f} | SNR: {snr:.0f}")
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
    
    if not _set_freq(sdr, DAB_12A_FREQ_HZ):
        print(f"[SDR] DAB reading #{reading_num} skipped — could not tune")
        return False

    # Flush stale samples from the previous chunk, then dwell on 12A for the
    # configured amount of time and average the resulting spectra.
    # 512k samples gives nRSP-ST/SDRconnect extra time to finish retuning.
    _read_samples(sdr, 512 * 1024)

    smoothed, freqs = _capture_averaged_dbfs(sdr, DAB_DWELL_S)
    if smoothed is None:
        print(f"[SDR] DAB reading #{reading_num} skipped — read failed")
        return False

    actual_freqs = sdr.center_freq + freqs

    half_bw = DAB_BANDWIDTH_HZ / 2
    signal_mask = (actual_freqs >= DAB_12A_FREQ_HZ - half_bw) & \
                  (actual_freqs <= DAB_12A_FREQ_HZ + half_bw)

    noise_mask = ((actual_freqs >= DAB_12A_FREQ_HZ - half_bw - 400e3) & \
                  (actual_freqs < DAB_12A_FREQ_HZ - half_bw)) | \
                 ((actual_freqs > DAB_12A_FREQ_HZ + half_bw) & \
                  (actual_freqs <= DAB_12A_FREQ_HZ + half_bw + 400e3))

    power = float(np.mean(smoothed[signal_mask]))
    floor = float(np.median(smoothed[noise_mask]))
    snr = power - floor

    ts = datetime.datetime.now()

    try:
        reading = SweepData(
            timestamp=ts,
            frequency_mhz=DAB_FREQ_KHZ,
            power_dbfs=round(power + DB_OFFSET_DB, 2),
            floor_dbfs=round(floor + DB_OFFSET_DB, 2),
            snr_db=round(snr, 2),
            sdr_id=SDR_ID
        )
        db.add(reading)
        db.commit()
        print(
            f"  DAB #{reading_num} | Power: {power:.1f} | Floor: {floor:.1f} | "
            f"SNR: {snr:.1f} | dwell={DAB_DWELL_S:.2f}s"
        )
        return True
    except Exception as e:
        print(f"[DB ERROR] DAB save failed: {e}")
        db.rollback()
        return False


# ---------------------------------------------------------------------------
# Interleaved FM + DAB+ sweep (Slow mode)
# ---------------------------------------------------------------------------
def interleaved_sweep(sdr, db, SweepData, fm_batch_size: int = 10) -> tuple:
    """
    Interleaved sweep: every N FM steps, do 1 DAB+ reading.
    Returns (fm_count, dab_count) of saved records.
    """
    frequencies_mhz = np.round(np.arange(86.0, 109.1, 0.1), 1)
    fm_saved = 0
    dab_saved = 0
    dab_reading_num = 0
    
    print(f"\n[Interleaved Sweep] {len(frequencies_mhz)} FM steps, DAB+ every {fm_batch_size} steps")
    print(f"{'Step':>6} | {'Freq':>7} | {'Type':>6} | {'Result'}")
    print("-" * 50)

    _reset_noise_floor_ema()
    
    for i, freq in enumerate(frequencies_mhz):
        if not running:
            break
        
        # FM step (dwells for SLOW_MODE_STEP_DELAY_S actually sampling/averaging)
        if fm_single_step(sdr, db, SweepData, freq):
            fm_saved += 1
        
        # Every N FM steps, do DAB+ reading
        if (i + 1) % fm_batch_size == 0:
            if not running:
                break
            dab_reading_num += 1
            print(f"  >> DAB+ 12A reading #{dab_reading_num}")
            if dab_single_reading(sdr, db, SweepData, dab_reading_num):
                dab_saved += 1
    
    return fm_saved, dab_saved


# ---------------------------------------------------------------------------
# FM Wideband Step (Quick mode — 1.5 MHz chunk, 15 channels per call)
# ---------------------------------------------------------------------------
def fm_wideband_step(sdr, db, SweepData, center_mhz) -> int:
    """
    Scans a 1.5 MHz chunk of spectrum at once using the SDR's sample rate.
    Slices the FFT into fifteen 100 kHz channels and saves them to the DB.

    In Quick mode the chunk is sampled for QUICK_MODE_DWELL_S seconds and the
    power spectrum is averaged across many consecutive FFT frames. This makes
    the measurement much less noisy and gives weak/fading signals time to show
    up, instead of relying on a single 13 ms snapshot.

    Why 1.5 MHz and not 2.0 MHz:
        For RTL-SDR the DC-offset trick tunes the hardware 333 kHz BELOW the
        requested centre, so the usable right edge is only centre + 867 kHz
        (1200 - 333).  A 2 MHz window would place its outermost right-hand
        channels beyond that edge, into the anti-aliasing roll-off, producing
        a spuriously low noise floor and a falsely elevated SNR.  Limiting
        the window to ±750 kHz keeps every channel safely inside the flat
        passband.  SDRplay / SDRconnect backends use no LO offset, so ±750 kHz
        is even further from the Nyquist edge.

    Returns the number of channels successfully saved.
    """
    if not running:
        return 0

    target_hz = center_mhz * 1e6
    if not _set_freq(sdr, target_hz - _get_fm_offset_hz(sdr)):
        print(f"[SDR] Wideband chunk {center_mhz} MHz skipped — could not tune")
        return 0

    # Flush any stale samples from the previous chunk, then collect the full
    # configured dwell time so the FFT average is meaningful.
    # 512k samples gives nRSP-ST/SDRconnect extra time to finish retuning.
    _read_samples(sdr, 512 * 1024)

    smoothed, freqs = _capture_averaged_dbfs(sdr, QUICK_MODE_DWELL_S)
    if smoothed is None:
        print(f"[SDR] Wideband chunk {center_mhz} MHz skipped — read failed")
        return 0

    actual_freqs = sdr.center_freq + freqs
    
    saved_count = 0
    
    # DC spike is at the SDR LO; exclude it from any floor calculation.
    dc_mask = (actual_freqs >= sdr.center_freq - 20e3) & (actual_freqs <= sdr.center_freq + 20e3)
    # Robust noise reference: use the guard-band bins outside the 1.5 MHz
    # chunk (left-over sample-rate bandwidth) instead of the chunk itself,
    # so active stations inside the chunk don't pull the floor up or down.
    chunk_low = target_hz - 0.75e6
    chunk_high = target_hz + 0.75e6
    guard_mask = ((actual_freqs < chunk_low) | (actual_freqs > chunk_high)) & ~dc_mask
    if np.any(guard_mask):
        base_noise = float(np.percentile(smoothed[guard_mask], 25))
    else:
        base_noise = float(np.percentile(smoothed[~dc_mask], 25))
    
    # Generate the 15 channels (100 kHz steps) that fit inside this 1.5 MHz chunk
    # e.g., if center is 86.75, this generates 86.0, 86.1 ... 87.5
    channel_freqs_mhz = np.round(np.arange(center_mhz - 0.75, center_mhz + 0.75, 0.1), 1)
    
    half_bw = FM_CHANNEL_HALF_BW_HZ
    for channel_mhz in channel_freqs_mhz:
        channel_hz = channel_mhz * 1e6
        
        # Integrate this FM channel over +/- 75 kHz
        target_mask = (actual_freqs >= channel_hz - half_bw) & (actual_freqs <= channel_hz + half_bw)
        
        # Calculate integrated channel power and rolling floor for this bin
        power_dbfs = _linear_avg_dbfs(smoothed[target_mask])
        if power_dbfs is None:
            print(f"[SDR] Skipping {channel_mhz} MHz - no bins in target mask")
            continue
        
        # Wide guard band ±250 kHz and DC, plus a sideband blanker:
        # drop any bin more than 6 dB above the chunk's quiet noise reference.
        signal_exclusion = (actual_freqs >= channel_hz - 250e3) & (actual_freqs <= channel_hz + 250e3)
        noise_mask = ~signal_exclusion & ~dc_mask & (smoothed <= base_noise + 6.0)
        noise_values = smoothed[noise_mask]
        measured_floor = _linear_avg_dbfs(noise_values)
        if measured_floor is None:
            measured_floor = base_noise
        floor_dbfs = _rolling_floor_dbfs(measured_floor)

        snr = power_dbfs - floor_dbfs
        freq_khz = int(channel_mhz * 1000)
        
        try:
            reading = SweepData(
                timestamp=datetime.datetime.now(),
                frequency_mhz=freq_khz,
                power_dbfs=round(power_dbfs + DB_OFFSET_DB, 2),
                floor_dbfs=round(floor_dbfs + DB_OFFSET_DB, 2),
                snr_db=round(snr, 2),
                sdr_id=SDR_ID
            )
            db.add(reading)
            saved_count += 1
        except Exception as e:
            print(f"[DB ERROR] FM save failed at {channel_mhz} MHz: {e}")
            db.rollback()

    # Commit all 15 channels in one fast database transaction
    db.commit()
    print(
        f"  Wideband Chunk {center_mhz} MHz processed | {saved_count} channels logged | "
        f"dwell={QUICK_MODE_DWELL_S:.2f}s"
    )
    
    return saved_count


# ---------------------------------------------------------------------------
# Quick interleaved sweep (uses fm_wideband_step)
# ---------------------------------------------------------------------------
def quick_interleaved_sweep(sdr, db, SweepData, fm_batch_size: int = 2) -> tuple:
    """
    Quick interleaved sweep: iterates over 1.5 MHz wideband chunks covering
    86-109 MHz, inserting a DAB+ reading every fm_batch_size chunks.
    Returns (fm_count, dab_count) of saved records.
    """
    # Center frequencies for 1.5 MHz chunks covering 86-109 MHz
    # Centers: 86.75, 88.25, 89.75 ... 108.25  (each covers center±0.75 MHz)
    chunk_centers = np.round(np.arange(86.75, 108.3, 1.5), 2)
    fm_saved = 0
    dab_saved = 0
    dab_reading_num = 0

    print(f"\n[Quick Sweep] {len(chunk_centers)} x 1.5 MHz chunks, DAB+ every {fm_batch_size} chunks")
    print("-" * 50)

    _reset_noise_floor_ema()

    for i, center in enumerate(chunk_centers):
        if not running:
            break

        fm_saved += fm_wideband_step(sdr, db, SweepData, center)

        if (i + 1) % fm_batch_size == 0:
            if not running:
                break
            dab_reading_num += 1
            print(f"  >> DAB+ 12A reading #{dab_reading_num}")
            if dab_single_reading(sdr, db, SweepData, dab_reading_num):
                dab_saved += 1

    return fm_saved, dab_saved


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

    print(f"\nSelect sweep mode — [Q] Quick (wideband chunks) / [S] Slow (per-channel)")
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
# Main
# ---------------------------------------------------------------------------
def main():
    global running
    
    print("=" * 60)
    print("FM + DAB+ Interleaved Sweep with PostgreSQL logging")
    print("=" * 60)
    print(f"DB: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    print(f"FM: 86-109 MHz (100 kHz steps, DAB+ every 10 steps)")
    print(f"DAB: 223.936 MHz (223936 in DB)")
    print(f"Table: rf_sweep_data (id, timestamp, frequency_mhz, power_dbfs, floor_dbfs, snr_db)")
    print("=" * 60)

    mode = ask_mode()
    if mode == "Q":
        print(f"\n[Mode] QUICK — wideband 1.5 MHz chunks, dwell={QUICK_MODE_DWELL_S:.2f}s per chunk")
    else:
        print("\n[Mode] SLOW  — original per-channel sweep")
    
    # Setup database
    Session, SweepData = setup_db()
    db = Session()

    # Open SDR
    sdr = _open_sdr()

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
                    fm_saved, dab_saved = quick_interleaved_sweep(sdr, db, SweepData, fm_batch_size=2)
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
