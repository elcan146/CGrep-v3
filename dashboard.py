#!/usr/bin/env python3
"""
Region SDR Radio Network
─────────────────────────────
Audio pipeline (how every real radio app works):

  rtl_fm (C program) ──► pipe ──► ring buffer ──► sounddevice callback ──► DAC
                  ↑
      handles all USB, FM demod, de-emphasis, resampling in C
      Python never touches the hot path → zero glitches

Install dependencies:
  pip install PyQt6 pyqtgraph sounddevice numpy
  sudo apt install rtl-sdr        # Linux
  brew install librtlsdr          # macOS
  (Windows: download rtl-sdr from osmocom.org and add to PATH)
"""

import json, os, sys, time, shutil, threading, subprocess, random
import numpy as np
try:
    from scipy import signal
    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False
import pyqtgraph as pg
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget,
                              QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QSlider, QPushButton)
from PyQt6.QtCore  import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui   import QFont
import sounddevice as sd

FS = 48_000          # audio sample rate that rtl_fm will output

# Known / simulated FM stations
STATIONS = {
    99.5:  {"name": "Radio Space",     "base_tone": 440.0,
            "songs": ["Dua Lipa - Levitating", "The Weeknd - Blinding Lights",
                      "Lady Gaga - Poker Face", "Ed Sheeran - Shape of You"]},
    101.0: {"name": "Azadlıq FM",      "base_tone": 330.0,
            "songs": ["Sezen Aksu - Gülümse", "Müslüm Gürses - Nilüfer",
                      "Ajda Pekkan - Pet'r Oil"]},
    103.5: {"name": "Araz FM",         "base_tone": 528.0,
            "songs": ["Tarkan - Şımarık", "Sertab Erener - Everyway That I Can",
                      "Hadise - Dum Tek Tek"]},
    95.5:  {"name": "Retro FM",        "base_tone": 262.0,
            "songs": ["ABBA - Dancing Queen", "Queen - Bohemian Rhapsody",
                      "Michael Jackson - Billie Jean"]},
    97.2:  {"name": "Klassik Radio",   "base_tone": 392.0,
            "songs": ["Mozart - Symphony No. 40", "Vivaldi - Four Seasons",
                      "Beethoven - Moonlight Sonata"]},
    99.1:  {"name": "Lənkəran FM",     "base_tone": 294.0,
            "songs": ["Local Folk - Yasamal", "Jazz Trio - Evening Breeze",
                      "Caspian Waves - Fisherman's Song"]},
    99.9:  {"name": "Zaqatala Radio",  "base_tone": 349.0,
            "songs": ["Highland Tune - Mountain Echo", "Accordion Mix - Georgian Border",
                      "Forest Rain - Morning Mist"]},
    100.3: {"name": "Şuşa Radio",      "base_tone": 370.0,
            "songs": ["Mugham - Bayati Shiraz", "Tar Solo - Karabakh Nights",
                      "Ashiq Song - Road to Shusha"]},
}


# ──────────────────────────────────────────────────────────────────────────────
# Ring buffer — lock-protected, numpy-backed, never blocks
# ──────────────────────────────────────────────────────────────────────────────
class RingBuffer:
    def __init__(self, seconds=3.0):
        n          = int(FS * seconds)
        self._b    = np.zeros(n, np.float32)
        self._n    = n
        self._w    = self._r = 0
        self._lk   = threading.Lock()

    def _avail(self):
        return (self._w - self._r) % self._n

    def flush(self):
        with self._lk:
            self._w = self._r = 0
            self._b[:] = 0.0

    def push(self, x: np.ndarray):
        x = np.asarray(x, np.float32)
        L = len(x)
        with self._lk:
            space = self._n - 1 - self._avail()
            if L > space:                               # drop oldest, stay live
                self._r = (self._r + L - space) % self._n
            e = self._w + L
            if e <= self._n:
                self._b[self._w:e] = x
            else:
                s = self._n - self._w
                self._b[self._w:] = x[:s]
                self._b[:L - s]   = x[s:]
            self._w = e % self._n

    def pull(self, n: int) -> np.ndarray:
        """Called from sounddevice audio thread — must be fast and non-blocking."""
        with self._lk:
            k = min(self._avail(), n)
            e = self._r + k
            if e <= self._n:
                out = self._b[self._r:e].copy()
            else:
                s   = self._n - self._r
                out = np.concatenate([self._b[self._r:], self._b[:k - s]])
            self._r = (self._r + k) % self._n
        if k < n:
            out = np.pad(out, (0, n - k))              # silence on underrun
        return out

    def peek(self, n: int) -> np.ndarray:
        """Newest n samples without consuming — for FFT display."""
        with self._lk:
            k = min(self._avail(), n)
            if k == 0:
                return np.zeros(0, np.float32)
            r = (self._w - k) % self._n
            e = r + k
            if e <= self._n:
                return self._b[r:e].copy()
            s = self._n - r
            return np.concatenate([self._b[r:], self._b[:k - s]])


# ──────────────────────────────────────────────────────────────────────────────
# FM Player Thread — runs rtl_fm as a subprocess, pipes PCM into ring buffer
# ──────────────────────────────────────────────────────────────────────────────
class FMPlayerThread(QThread):
    sig_rms = pyqtSignal(float)     # RMS dBFS for signal strength meter

    def __init__(self, ring: RingBuffer, freq_mhz=99.5):
        super().__init__()
        self._ring    = ring
        self._freq    = freq_mhz
        self._vol     = 0.8
        self._running = True
        self._ev      = threading.Event()   # set to trigger frequency change

    @staticmethod
    def available() -> bool:
        if shutil.which('rtl_fm') is not None:
            return True
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rtl_fm.exe')
        return os.path.exists(local)

    def set_frequency(self, mhz: float):
        self._freq = mhz
        self._ring.flush()          # clear old-station audio immediately
        self._ev.set()              # wake the run loop to restart rtl_fm

    def set_volume(self, vol: float):
        self._vol = max(0.0, min(1.0, vol))

    def _spawn(self):
        """Spawn rtl_fm tuned to self._freq."""
        cmd = 'rtl_fm'
        if shutil.which('rtl_fm') is None:
            local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rtl_fm.exe')
            if os.path.exists(local):
                cmd = local
        return subprocess.Popen(
            [cmd,
             '-f', str(int(self._freq * 1e6)),  # center frequency
             '-M', 'fm',                         # wideband FM
             '-s', '200000',                     # IF sample rate (200 kHz)
             '-r', str(FS),                      # output audio rate (48 kHz)
             '-A', 'fast',                       # fast atan FM demodulator
             '-l', '0',                          # squelch off
             '-E', 'deemp',                      # 50 µs de-emphasis (EU/Asia)
             '-'],                               # write int16 PCM to stdout
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,                           # unbuffered pipe — low latency
        )

    def _read_rtl_stderr(self, proc):
        try:
            for line in iter(proc.stderr.readline, b''):
                if not self._running:
                    break
                text = line.decode('utf-8', errors='ignore').strip()
                if text:
                    print(f"[FM RTL] {text}", flush=True)
        except Exception:
            pass

    def run(self):
        CHUNK = 8192    # bytes = 4096 int16 samples ≈ 85 ms of audio per read
        proc  = self._spawn()
        err_thread = threading.Thread(target=self._read_rtl_stderr, args=(proc,))
        err_thread.daemon = True
        err_thread.start()
        try:
            while self._running:

                # Frequency change requested by UI slider
                if self._ev.is_set():
                    self._ev.clear()
                    proc.terminate()
                    try:    proc.wait(timeout=2)
                    except: proc.kill()
                    proc = self._spawn()

                # rtl_fm died unexpectedly (device unplugged, etc.) — retry
                if proc.poll() is not None:
                    time.sleep(1.0)
                    proc = self._spawn()
                    continue

                # Read a chunk of raw int16 PCM from rtl_fm stdout
                # This blocks until CHUNK bytes arrive (~85 ms) — that's fine;
                # the ring buffer is 3 s deep so the audio callback never starves.
                try:
                    raw = proc.stdout.read(CHUNK)
                except OSError:
                    time.sleep(0.02)
                    continue

                if not raw:
                    time.sleep(0.02)
                    continue

                # Convert to float32, apply volume, push to ring buffer
                pcm     = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
                pcm    *= self._vol
                rms_db  = 20 * np.log10(np.sqrt(np.mean(pcm ** 2)) + 1e-9)
                self.sig_rms.emit(float(np.clip(rms_db, -60, 0)))
                self._ring.push(pcm)

        finally:
            proc.terminate()
            try:    proc.wait(timeout=2)
            except: proc.kill()
            err_thread.join(timeout=1)

    def stop(self):
        self._running = False
        self._ev.set()
        self.quit()
        self.wait(3000)


RDS_PTY_NAMES = {
    0: "None", 1: "News", 2: "Current Affairs", 3: "Information",
    4: "Sport", 5: "Education", 6: "Drama", 7: "Culture",
    8: "Science", 9: "Varied", 10: "Pop Music", 11: "Rock Music",
    12: "Easy Listening", 13: "Light Classical", 14: "Serious Classical",
    15: "Other Music", 16: "Weather", 17: "Finance", 18: "Children",
    19: "Social Affairs", 20: "Religion", 21: "Phone-in", 22: "Travel",
    23: "Leisure", 24: "Jazz", 25: "Country", 26: "National Music",
    27: "Oldies", 28: "Folk Music", 29: "Documentary", 30: "Alarm Test",
    31: "Alarm",
}


# ──────────────────────────────────────────────────────────────────────────────
# RDS FM Player Thread — rtl_sdr IQ → Python FM demod → redsea --feed-through
# redsea stderr = JSON (RDS), we extract audio ourselves from MPX
# ──────────────────────────────────────────────────────────────────────────────
class RDSFMPlayerThread(QThread):
    sig_rms = pyqtSignal(float)
    sig_rds_ps = pyqtSignal(str)
    sig_rds_rt = pyqtSignal(str)
    sig_rds_ct = pyqtSignal(str)
    sig_rds_pty = pyqtSignal(str)
    sig_fallback = pyqtSignal()

    IQ_RATE = 1_024_000   # rtl_sdr IQ sample rate (Hz)
    MPX_RATE = 228_000    # MPX rate for redsea (standard RDS rate)
    MONO_CUT = 15_000     # L+R audio lives below 15 kHz

    def __init__(self, ring: RingBuffer, freq_mhz=99.5):
        super().__init__()
        self._ring = ring
        self._freq = freq_mhz
        self._vol = 0.8
        self._running = True
        self._ev = threading.Event()
        self._lpf_state = None
        self._fir_taps = None
        self._fir_state = None
        self._last_iq = None
        self._total_pumped = 0
        self._last_pump_time = 0.0
        self._log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rds_debug.log')

    def _log(self, msg: str):
        ts = time.strftime('%H:%M:%S')
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        try:
            with open(self._log_path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
                f.flush()
        except Exception:
            pass

    @staticmethod
    def available() -> bool:
        rtl = shutil.which('rtl_sdr') is not None
        if not rtl:
            local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rtl_sdr.exe')
            rtl = os.path.exists(local)
        if not rtl:
            return False
        red = shutil.which('redsea') is not None
        if not red:
            local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'redsea.exe')
            red = os.path.exists(local)
        return red

    @staticmethod
    def _rtl_cmd() -> str:
        if shutil.which('rtl_sdr') is not None:
            return 'rtl_sdr'
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rtl_sdr.exe')
        return local if os.path.exists(local) else 'rtl_sdr'

    @staticmethod
    def _redsea_cmd() -> str:
        if shutil.which('redsea') is not None:
            return 'redsea'
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'redsea.exe')
        return local if os.path.exists(local) else 'redsea'

    @staticmethod
    def _msys2_bin() -> str | None:
        candidates = [
            r'C:\msys64\mingw64\bin',
            r'C:\msys2\mingw64\bin',
            os.path.expandvars(r'%USERPROFILE%\msys64\mingw64\bin'),
            os.path.expandvars(r'%LOCALAPPDATA%\msys64\mingw64\bin'),
        ]
        for d in candidates:
            if os.path.isfile(os.path.join(d, 'libgcc_s_seh-1.dll')):
                return d
        return None

    def set_frequency(self, mhz: float):
        self._freq = mhz
        self._ring.flush()
        self._ev.set()
        self._lpf_state = None
        self._fir_state = None
        self._last_iq = None

    def set_volume(self, vol: float):
        self._vol = max(0.0, min(1.0, vol))

    def _spawn_rtl(self):
        cmd = self._rtl_cmd()
        return subprocess.Popen(
            [cmd,
             '-f', str(int(self._freq * 1e6)),
             '-s', str(self.IQ_RATE),
             '-'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def _spawn_redsea(self):
        cmd = self._redsea_cmd()
        env = None
        msys2 = self._msys2_bin()
        if msys2:
            env = os.environ.copy()
            env['PATH'] = msys2 + os.pathsep + env.get('PATH', '')
        return subprocess.Popen(
            [cmd, '--feed-through', '-r', str(self.MPX_RATE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=env,
        )

    @staticmethod
    def _preflight() -> list:
        problems = []
        env = None
        msys2 = RDSFMPlayerThread._msys2_bin()
        if msys2:
            env = os.environ.copy()
            env['PATH'] = msys2 + os.pathsep + env.get('PATH', '')
        try:
            p = subprocess.run(
                [RDSFMPlayerThread._redsea_cmd(), '--help'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=3, check=False, env=env)
            if p.returncode != 0 and b'Usage' not in p.stdout and b'usage' not in p.stdout:
                err = p.stdout.decode('utf-8', errors='ignore').strip()[:200]
                problems.append(f"redsea won't start: {err}")
        except Exception as e:
            problems.append(f"redsea preflight failed: {e}")
        try:
            p = subprocess.Popen(
                [RDSFMPlayerThread._rtl_cmd(), '-f', '99500000',
                 '-s', '1024000', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0,
            )
            try:
                _, errs = p.communicate(timeout=2)
                err = errs.decode('utf-8', errors='ignore').strip()
                bad_keywords = ('error', 'failed', 'usb_open', 'permissions',
                              'not found', 'cannot', 'unable', 'refusing')
                if any(k in err.lower() for k in bad_keywords):
                    for line in err.splitlines():
                        l = line.strip()
                        if l and any(k in l.lower() for k in bad_keywords):
                            problems.append(f"rtl_sdr: {l}")
                            break
                else:
                    problems.append(f"rtl_sdr exited with code {p.returncode}")
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except Exception:
                    p.kill()
        except Exception as e:
            problems.append(f"rtl_sdr preflight failed: {e}")
        return problems

    def _kill_proc(self, proc, name):
        if proc is None or proc.poll() is not None:
            return
        if sys.platform == 'win32':
            subprocess.call(
                ['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    def _init_filters(self):
        if self._fir_taps is not None:
            return
        nyq = self.MPX_RATE / 2.0
        if HAS_SCIPY:
            from scipy import signal as sp_signal
            self._lpf_b, self._lpf_a = sp_signal.butter(
                4, self.MONO_CUT / nyq, btype='low')
            self._lpf_zi = sp_signal.lfilter_zi(self._lpf_b, self._lpf_a)
        else:
            taps = 51
            fc = self.MONO_CUT / nyq
            n = np.arange(taps)
            h = np.sinc(fc * (n - (taps - 1) / 2))
            h *= np.hamming(taps)
            h /= np.sum(h)
            self._fir_taps = h.astype(np.float32)
            self._fir_state = np.zeros(taps - 1, np.float32)

    def _filter_mpx(self, mpx: np.ndarray) -> np.ndarray:
        self._init_filters()
        if HAS_SCIPY:
            from scipy import signal as sp_signal
            if self._lpf_state is None:
                self._lpf_state = self._lpf_zi * mpx[0]
            mono, self._lpf_state = sp_signal.lfilter(
                self._lpf_b, self._lpf_a, mpx, zi=self._lpf_state)
            return mono
        padded = np.concatenate([self._fir_state, mpx])
        mono = np.convolve(padded, self._fir_taps, mode='valid')
        self._fir_state = padded[-(len(self._fir_taps) - 1):].copy()
        return mono

    def _fm_demod(self, iq_bytes: bytes) -> np.ndarray:
        """FM demodulate raw uint8 IQ to MPX float @ IQ_RATE."""
        raw = np.frombuffer(iq_bytes, dtype=np.uint8).astype(np.float32)
        raw = (raw - 127.5) / 127.5
        iq = raw[0::2] + 1j * raw[1::2]
        if self._last_iq is not None:
            iq = np.concatenate([[self._last_iq], iq])
        phase = np.angle(iq)
        dphase = np.diff(phase)
        dphase = np.mod(dphase + np.pi, 2 * np.pi) - np.pi
        self._last_iq = iq[-1]
        mpx = dphase * (self.IQ_RATE / (2 * np.pi * 75_000))
        return mpx.astype(np.float32)

    def _resample_mpx(self, mpx: np.ndarray) -> np.ndarray:
        """Resample MPX from IQ_RATE to MPX_RATE (1024k → 228k)."""
        if HAS_SCIPY:
            from scipy import signal as sp_signal
            return sp_signal.resample_poly(mpx, 57, 256)
        num = int(len(mpx) * self.MPX_RATE / self.IQ_RATE)
        return np.interp(np.linspace(0, len(mpx) - 1, num),
                         np.arange(len(mpx)), mpx)

    def _resample_audio(self, x: np.ndarray) -> np.ndarray:
        """Resample audio from MPX_RATE to FS (228k → 48k)."""
        if HAS_SCIPY:
            from scipy import signal as sp_signal
            return sp_signal.resample_poly(x, 4, 19)
        num = int(len(x) * FS / self.MPX_RATE)
        return np.interp(np.linspace(0, len(x) - 1, num),
                         np.arange(len(x)), x)

    def _read_json(self, proc):
        try:
            for line in iter(proc.stderr.readline, b''):
                if not self._running:
                    break
                try:
                    text = line.decode('utf-8', errors='ignore').strip()
                    if not text:
                        continue
                    data = json.loads(text)
                    if 'ps' in data and data['ps']:
                        self.sig_rds_ps.emit(str(data['ps']))
                    if 'rt' in data and data['rt']:
                        self.sig_rds_rt.emit(str(data['rt']))
                    if 'clock' in data and data['clock']:
                        self.sig_rds_ct.emit(str(data['clock']))
                    if 'ct' in data and data['ct']:
                        self.sig_rds_ct.emit(str(data['ct']))
                    if 'pty' in data:
                        pty_name = RDS_PTY_NAMES.get(int(data['pty']), f"PTY {data['pty']}")
                        self.sig_rds_pty.emit(pty_name)
                except Exception:
                    if text:
                        self._log(f"[redsea stderr] {text}")
        except Exception:
            pass

    def _read_rtl_stderr(self, proc):
        try:
            for line in iter(proc.stderr.readline, b''):
                if not self._running:
                    break
                text = line.decode('utf-8', errors='ignore').strip()
                if text:
                    self._log(f"[RTL] {text}")
        except Exception:
            pass

    def _pump_and_audio(self, rtl_proc, redsea_proc):
        BATCH_MS = 100
        BATCH_BYTES = self.IQ_RATE * BATCH_MS // 1000 * 2
        iq_buf = bytearray()
        total_pumped = 0
        first = True
        last_log = time.time()
        last_mpx_rms = 0.0
        try:
            while self._running:
                chunk = rtl_proc.stdout.read(65536)
                if not chunk:
                    self._log("[RDS] rtl_sdr stdout EOF")
                    break
                iq_buf.extend(chunk)
                self._last_pump_time = time.time()

                while len(iq_buf) >= BATCH_BYTES:
                    batch = bytes(iq_buf[:BATCH_BYTES])
                    del iq_buf[:BATCH_BYTES]

                    # FM demodulate
                    mpx = self._fm_demod(batch)

                    # Signal-level diagnostic (first batch only)
                    if first:
                        raw = np.frombuffer(batch, dtype=np.uint8).astype(np.float32)
                        raw = (raw - 127.5) / 127.5
                        iq = raw[0::2] + 1j * raw[1::2]
                        amp_rms = float(np.sqrt(np.mean(np.abs(iq) ** 2)))
                        self._log(f"[RDS] IQ amplitude RMS = {amp_rms:.3f} (strong >0.3, weak <0.1)")

                    # Resample to MPX_RATE
                    mpx_228 = self._resample_mpx(mpx)
                    if len(mpx_228) > 0:
                        last_mpx_rms = float(np.sqrt(np.mean(mpx_228 ** 2)))

                    # Send to redsea
                    mpx_int16 = np.clip(mpx_228 * 32767, -32768, 32767).astype(np.int16)
                    try:
                        redsea_proc.stdin.write(mpx_int16.tobytes())
                        redsea_proc.stdin.flush()
                    except (BrokenPipeError, OSError):
                        self._log("[RDS] redsea stdin broken pipe")
                        return

                    total_pumped += len(mpx_int16.tobytes())
                    self._total_pumped = total_pumped
                    if first and total_pumped > 0:
                        self._log(f"[RDS] first audio data: {total_pumped} bytes pumped")
                        first = False

                    # Extract audio
                    mono = self._filter_mpx(mpx_228)
                    audio = self._resample_audio(mono)
                    audio *= self._vol
                    rms_db = 20 * np.log10(np.sqrt(np.mean(audio ** 2)) + 1e-9)
                    self.sig_rms.emit(float(np.clip(rms_db, -60, 0)))
                    self._ring.push(audio.astype(np.float32))

                if time.time() - last_log >= 5:
                    self._log(f"[RDS] pumped {total_pumped} bytes to redsea, buffer {len(iq_buf)} bytes")
                    last_log = time.time()
                    # Periodically log audio RMS to verify demodulation
                    if last_mpx_rms > 0:
                        self._log(f"[RDS] MPX audio RMS = {last_mpx_rms:.3f}")
        except Exception as e:
            self._log(f"[RDS] pump error: {e}")

    def run(self):
        self._log("[RDS] run() started")
        self._log("[RDS] running preflight...")
        preflight = self._preflight()
        self._log("[RDS] preflight done")
        if preflight:
            for msg in preflight:
                self._log(f"[RDS DIAG] {msg}")
            self._log("[RDS DIAG] Fix the above issues; app will keep trying...")
        cycle = 0
        while self._running:
            cycle += 1
            self._ev.clear()
            try:
                rtl = self._spawn_rtl()
                redsea = self._spawn_redsea()
            except Exception as e:
                self._log(f"[RDS] spawn error: {e}")
                time.sleep(2)
                continue
            self._log(f"[RDS] cycle {cycle}: rtl_sdr PID {rtl.pid}, redsea PID {redsea.pid}")

            json_thread = threading.Thread(target=self._read_json, args=(redsea,))
            json_thread.daemon = True
            json_thread.start()

            rtl_err_thread = threading.Thread(target=self._read_rtl_stderr, args=(rtl,))
            rtl_err_thread.daemon = True
            rtl_err_thread.start()

            self._total_pumped = 0
            self._last_pump_time = time.time()

            pump_thread = threading.Thread(target=self._pump_and_audio, args=(rtl, redsea))
            pump_thread.daemon = True
            pump_thread.start()

            # Monitor processes and data flow
            while self._running and pump_thread.is_alive():
                time.sleep(0.5)

                if rtl.poll() is not None:
                    self._log(f"[RDS] rtl_sdr exited code {rtl.returncode}")
                    break
                if redsea.poll() is not None:
                    self._log(f"[RDS] redsea exited code {redsea.returncode}")
                    break

                if time.time() - self._last_pump_time > 5:
                    self._log("[RDS] no data from rtl_sdr for 5s, declaring stall")
                    break

            self._ev.set()
            self._kill_proc(rtl, 'rtl_sdr')
            self._kill_proc(redsea, 'redsea')
            json_thread.join(timeout=1)
            rtl_err_thread.join(timeout=1)
            pump_thread.join(timeout=1)
            self._log(f"[RDS] cycle {cycle} ended (pumped {self._total_pumped} bytes)")
            if cycle >= 3 and self._total_pumped == 0:
                self._log("[RDS] too many failed cycles, emitting fallback signal")
                self.sig_fallback.emit()
                break
            time.sleep(0.5)

    def stop(self):
        self._running = False
        self._ev.set()
        self.quit()
        self.wait(3000)


# ──────────────────────────────────────────────────────────────────────────────
# Simulated FM Player — generates synthetic audio when no RTL-SDR is present
# ──────────────────────────────────────────────────────────────────────────────
class SimulatedFMPlayerThread(QThread):
    sig_rms = pyqtSignal(float)

    def __init__(self, ring: RingBuffer, freq_mhz=99.5):
        super().__init__()
        self._ring    = ring
        self._freq    = freq_mhz
        self._vol     = 0.8
        self._running = True
        self._ev      = threading.Event()
        self._chunk   = 2048
        self._t       = 0.0

    def set_frequency(self, mhz: float):
        self._freq = mhz
        self._ring.flush()
        self._ev.set()

    def set_volume(self, vol: float):
        self._vol = max(0.0, min(1.0, vol))

    def _nearest_station(self):
        best = None
        best_diff = 999.0
        for f, info in STATIONS.items():
            diff = abs(f - self._freq)
            if diff < best_diff:
                best_diff = diff
                best = (f, info)
        return best, best_diff

    def _gen_audio(self, n: int) -> np.ndarray:
        nearest, diff = self._nearest_station()
        if nearest is not None and diff < 0.15:
            base = nearest[1]['base_tone']
            t = np.arange(n) / FS
            phase1 = 2 * np.pi * base * (t + self._t)
            phase2 = 2 * np.pi * base * 1.5 * (t + self._t)
            beat = 0.5 + 0.5 * np.sin(2 * np.pi * 2 * (t + self._t))
            sig = 0.4 * np.sin(phase1) + 0.25 * np.sin(phase2)
            sig *= beat
            self._t += n / FS
            if np.random.random() < 0.02:
                burst = np.random.normal(0, 0.08, n) * np.hanning(n)
                sig += burst
            rms = np.sqrt(np.mean(sig ** 2))
        else:
            sig = np.random.normal(0, 0.18, n)
            rms = np.sqrt(np.mean(sig ** 2))
        sig *= self._vol
        sig = np.clip(sig, -1.0, 1.0)
        return sig.astype(np.float32), 20 * np.log10(rms + 1e-9)

    def run(self):
        try:
            while self._running:
                if self._ev.is_set():
                    self._ev.clear()
                pcm, rms_db = self._gen_audio(self._chunk)
                self.sig_rms.emit(float(np.clip(rms_db, -60, 0)))
                self._ring.push(pcm)
                time.sleep(self._chunk / FS)
        finally:
            pass

    def stop(self):
        self._running = False
        self._ev.set()
        self.quit()
        self.wait(3000)


# ──────────────────────────────────────────────────────────────────────────────
# Simulated Spectrum Thread — generates realistic RF spectrum for other cities
# ──────────────────────────────────────────────────────────────────────────────
class SimulatedSDRThread(QThread):
    new_data = pyqtSignal(np.ndarray, np.ndarray)

    def __init__(self, city_name, stations, noise=-78):
        super().__init__()
        self._running = True
        self._city    = city_name
        self._stations = stations  # list of (freq_mhz, power_db)
        self._noise   = noise
        self._t       = 0.0

    def run(self):
        center = 99.5e6
        freqs = np.fft.fftshift(np.fft.fftfreq(4096, 1 / 1.2e6))
        freqs = (freqs + center) / 1e6
        bin_width = abs(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0003
        half_bw_mhz = 0.10  # 200 kHz total bandwidth

        while self._running:
            db = np.random.normal(self._noise, 2.0, 4096)

            for f_mhz, power in self._stations:
                idx = np.argmin(np.abs(freqs - f_mhz))
                half_bins = int(half_bw_mhz / bin_width)
                start = max(0, idx - half_bins)
                end = min(len(db), idx + half_bins)
                if end <= start:
                    continue
                n = end - start
                x = np.linspace(-2.5, 2.5, n)
                shape = np.exp(-x * x)
                drift = 4 * np.sin(self._t * 0.7 + hash(self._city) % 7)
                db[start:end] += shape * (power + drift)

            self._t += 0.08
            self.new_data.emit(freqs, db)
            time.sleep(0.08)

    def stop(self):
        self._running = False
        self.wait()


# ──────────────────────────────────────────────────────────────────────────────
# Main Window
# ──────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    PREBUF_MS = 500     # fill ring for 500 ms before opening audio stream

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Region SDR Radio Network")
        self.resize(1280, 820)
        pg.setConfigOptions(antialias=True)
        pg.setConfigOption('background', '#05070b')
        pg.setConfigOption('foreground', '#8fa0bc')

        self._ring    = RingBuffer(3.0)
        self._stream  = None
        self._threads = []
        self._fm      = None

        self._build_ui()
        self._init_peak()
        self._init_simulated()

        # Audio FFT display — reads from ring buffer, no extra thread needed
        self._fft_timer = QTimer(self)
        self._fft_timer.timeout.connect(self._refresh_audio_fft)
        self._fft_timer.start(50)   # 20 fps

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setSpacing(6)
        vbox.setContentsMargins(8, 8, 8, 8)

        # ── Site-1 (live audio) panel ──
        Site-1 = QWidget()
        Site-1.setStyleSheet("background:#0a0e17;border:1px solid rgba(0,229,255,0.15);border-radius:10px;")
        bv = QVBoxLayout(Site-1)
        bv.setContentsMargins(14, 10, 14, 12)

        hdr = QHBoxLayout()
        city_lbl = QLabel("🔴  Site-1  —  Live FM Radio")
        city_lbl.setFont(QFont("monospace", 11, QFont.Weight.Bold))
        city_lbl.setStyleSheet("color:#ff5722;letter-spacing:1px;")
        self._freq_lbl = QLabel("99.5 MHz")
        self._freq_lbl.setFont(QFont("JetBrains Mono", 34, QFont.Weight.Bold))
        self._freq_lbl.setStyleSheet("color:#00e5ff;")
        self._sig_lbl = QLabel("starting…")
        self._sig_lbl.setStyleSheet(
            "color:#444;font-family:monospace;font-size:11px;")
        hdr.addWidget(city_lbl)
        hdr.addStretch()
        hdr.addWidget(self._sig_lbl)
        hdr.addWidget(self._freq_lbl)
        bv.addLayout(hdr)

        self._apw = pg.PlotWidget()
        self._apw.setYRange(-90, 0)
        self._apw.setXRange(0, 20)
        self._apw.setLabel('bottom', 'kHz')
        self._apw.setLabel('left',   'dB')
        self._apw.showGrid(x=True, y=True, alpha=0.15)
        self._apw.getAxis('bottom').setPen(pg.mkPen('#1a2333', width=1))
        self._apw.getAxis('left').setPen(pg.mkPen('#1a2333', width=1))
        self._apw.setFixedHeight(210)
        self._acurve = self._apw.plot(
            pen=pg.mkPen('#00e5ff', width=1.5),
            fillLevel=-90,
            brush=pg.mkBrush(0, 229, 255, 30),
        )
        bv.addWidget(self._apw)

        ctrl = QHBoxLayout()
        ctrl.addWidget(self._lbl("Freq"))
        self._fsl = QSlider(Qt.Orientation.Horizontal)
        self._fsl.setRange(880, 1080)
        self._fsl.setValue(995)
        self._fsl.setStyleSheet(
            "QSlider::handle:horizontal{background:#00e5ff;"
            "width:14px;margin:-5px 0;border-radius:0px;}"
            "QSlider::groove:horizontal{background:#0d131f;border:1px solid #1a2333;height:4px;}"
            "QSlider::sub-page:horizontal{background:#00e5ff;height:4px;}")
        self._fsl.valueChanged.connect(self._on_freq)
        ctrl.addWidget(self._fsl, 5)
        ctrl.addSpacing(20)
        ctrl.addWidget(self._lbl("Vol"))
        self._vsl = QSlider(Qt.Orientation.Horizontal)
        self._vsl.setRange(0, 100)
        self._vsl.setValue(80)
        self._vsl.setStyleSheet(
            "QSlider::handle:horizontal{background:#ffaf00;"
            "width:14px;margin:-5px 0;border-radius:0px;}"
            "QSlider::groove:horizontal{background:#0d131f;border:1px solid #1a2333;height:4px;}"
            "QSlider::sub-page:horizontal{background:#ffaf00;height:4px;}")
        self._vsl.valueChanged.connect(self._on_vol)
        ctrl.addWidget(self._vsl, 2)
        bv.addLayout(ctrl)

        self._station_lbl = QLabel("📻 —")
        self._station_lbl.setFont(QFont("JetBrains Mono", 12, QFont.Weight.Bold))
        self._station_lbl.setStyleSheet("color:#8fa0bc;")
        bv.addWidget(self._station_lbl)

        self._song_lbl = QLabel("🎵 —")
        self._song_lbl.setFont(QFont("JetBrains Mono", 10))
        self._song_lbl.setStyleSheet("color:#5a6a85;")
        bv.addWidget(self._song_lbl)

        rds_grid = QGridLayout()
        rds_grid.setSpacing(4)
        self._rds_ps_lbl = self._rds_lbl("PS", "--------")
        self._rds_pty_lbl = self._rds_lbl("PTY", "---")
        self._rds_rt_lbl = self._rds_lbl("RT", "")
        self._rds_ct_lbl = self._rds_lbl("CT", "--:--")
        rds_grid.addWidget(QLabel("RDS"), 0, 0)
        rds_grid.addWidget(self._rds_ps_lbl, 0, 1)
        rds_grid.addWidget(self._rds_pty_lbl, 0, 2)
        rds_grid.addWidget(self._rds_rt_lbl, 1, 1, 1, 2)
        rds_grid.addWidget(self._rds_ct_lbl, 2, 1)
        bv.addLayout(rds_grid)

        vbox.addWidget(Site-1, 3)

        # ── Simulated city panels (bottom row) ──
        sim_row = QWidget()
        gr = QGridLayout(sim_row)
        gr.setSpacing(6)
        self._sim_curves = {}
        self._sim_btns = {}
        for i, (city, spike, col) in enumerate([
            ("Lenkeran", 99.1, '#00e5ff'),
            ("Zaqatala", 99.9, '#ffaf00'),
            ("Site-8",    100.3, '#ff5722'),
        ]):
            w = QWidget()
            w.setStyleSheet("background:#0a0e17;border:1px solid rgba(0,229,255,0.15);border-radius:8px;")
            wv = QVBoxLayout(w)
            wv.setContentsMargins(8, 6, 8, 6)
            top = QHBoxLayout()
            top.addWidget(QLabel(
                f"<b style='color:{col}'>{city}</b>"
                f"<span style='color:#444;font-size:10px'> · simulated · {spike} MHz</span>"))
            top.addStretch()
            btn = QPushButton("▶")
            btn.setCheckable(True)
            btn.setFixedSize(28, 22)
            btn.setStyleSheet(
                f"QPushButton{{background:#222;color:{col};border-radius:4px;font:bold 10px monospace;}}"
                f"QPushButton:checked{{background:{col};color:#111;}}"
            )
            btn.clicked.connect(lambda checked, c=city, s=spike: self._on_sim_play(c, s, checked))
            self._sim_btns[city] = btn
            top.addWidget(btn)
            wv.addLayout(top)
            pw = pg.PlotWidget()
            pw.setYRange(-80, 0)
            pw.showGrid(x=True, y=True, alpha=0.15)
            pw.setFixedHeight(175)
            curve = pw.plot(pen=pg.mkPen(col, width=1.2))
            self._sim_curves[city] = curve
            wv.addWidget(pw)
            gr.addWidget(w, 0, i)
        vbox.addWidget(sim_row, 2)

    @staticmethod
    def _lbl(text):
        l = QLabel(text)
        l.setStyleSheet("color:#8fa0bc;font-family:'JetBrains Mono';font-size:10px;")
        return l

    def _rds_lbl(self, key, default):
        l = QLabel(f"<b>{key}</b>: <span style='color:#ffaf00'>{default}</span>")
        l.setFont(QFont("JetBrains Mono", 9))
        l.setStyleSheet("color:#8fa0bc;")
        return l

    # ── Backend init ──────────────────────────────────────────────────────────
    def _init_peak(self):
        if RDSFMPlayerThread.available():
            self._default_player_cls = RDSFMPlayerThread
            self._fm = RDSFMPlayerThread(self._ring, freq_mhz=99.5)
            self._connect_rds_signals()
            self._sig_lbl.setText("RDS ▶ " + "░" * 8)
        elif FMPlayerThread.available():
            self._default_player_cls = FMPlayerThread
            self._fm = FMPlayerThread(self._ring, freq_mhz=99.5)
            self._sig_lbl.setText("▶ " + "░" * 10)
        else:
            self._default_player_cls = SimulatedFMPlayerThread
            self._fm = SimulatedFMPlayerThread(self._ring, freq_mhz=99.5)
            self._sig_lbl.setText("SIM " + "░" * 8)
            self._freq_lbl.setStyleSheet("color:#ffaa44;font-size:34px;")
        self._fm.sig_rms.connect(self._on_rms)
        self._fm.start()
        self._threads.append(self._fm)

        # Wait PREBUF_MS before opening the audio stream so the ring
        # is never empty on the very first callback → zero startup glitch.
        QTimer.singleShot(self.PREBUF_MS, self._open_audio_stream)
        self._update_station_text(99.5)

    def _connect_rds_signals(self):
        if isinstance(self._fm, RDSFMPlayerThread):
            self._fm.sig_rds_ps.connect(self._on_rds_ps)
            self._fm.sig_rds_rt.connect(self._on_rds_rt)
            self._fm.sig_rds_ct.connect(self._on_rds_ct)
            self._fm.sig_rds_pty.connect(self._on_rds_pty)
            self._fm.sig_fallback.connect(self._on_rds_fallback)

    def _disconnect_rds_signals(self, player):
        if isinstance(player, RDSFMPlayerThread):
            for sig, slot in [
                (player.sig_rds_ps, self._on_rds_ps),
                (player.sig_rds_rt, self._on_rds_rt),
                (player.sig_rds_ct, self._on_rds_ct),
                (player.sig_rds_pty, self._on_rds_pty),
                (player.sig_fallback, self._on_rds_fallback),
            ]:
                try:
                    sig.disconnect(slot)
                except Exception:
                    pass
            try:
                player.sig_rms.disconnect(self._on_rms)
            except Exception:
                pass

    def _on_rds_fallback(self):
        print("[MAIN] RDS failed, falling back to plain FM audio")
        if self._fm:
            self._disconnect_rds_signals(self._fm)
            self._fm.stop()
            if self._fm in self._threads:
                self._threads.remove(self._fm)
            self._fm = None
        self._default_player_cls = FMPlayerThread
        self._fm = FMPlayerThread(self._ring, freq_mhz=self._fsl.value() / 10.0)
        self._fm.sig_rms.connect(self._on_rms)
        self._fm.start()
        self._threads.append(self._fm)
        self._sig_lbl.setText("▶ " + "░" * 10)

    def _open_audio_stream(self):
        def audio_callback(outdata, frames, time_info, status):
            # sounddevice calls this on its own real-time thread.
            # All we do is pull from the ring buffer — trivially fast.
            outdata[:, 0] = self._ring.pull(frames)

        self._stream = sd.OutputStream(
            samplerate=FS,
            channels=1,
            dtype='float32',
            latency='low',
            callback=audio_callback,
        )
        self._stream.start()

    def _init_simulated(self):
        city_stations = {
            "Lenkeran": [(95.5, 38), (99.1, 45), (103.5, 32)],
            "Zaqatala": [(97.2, 40), (99.9, 48), (101.0, 35)],
            "Site-8":     [(99.5, 36), (100.3, 50), (103.5, 30)],
        }
        for city, spike, _ in [("Lenkeran", 99.1, None),
                                ("Zaqatala", 99.9, None),
                                ("Site-8",    100.3, None)]:
            t = SimulatedSDRThread(city, city_stations[city])
            t.new_data.connect(
                lambda f, d, c=city: self._sim_curves[c].setData(f, d))
            t.start()
            self._threads.append(t)

    # ── Slots ─────────────────────────────────────────────────────────────────
    def _on_freq(self, v):
        mhz = v / 10.0
        self._freq_lbl.setText(f"{mhz:.1f} MHz")
        if self._fm:
            self._fm.set_frequency(mhz)
        self._update_station_text(mhz)

    def _on_vol(self, v):
        if self._fm:
            self._fm.set_volume(v / 100.0)

    def _on_rms(self, rms_db):
        bars = int((rms_db + 60) / 6)   # map -60…0 dBFS → 0…10 blocks
        if isinstance(self._fm, SimulatedFMPlayerThread):
            prefix = "SIM"
        elif isinstance(self._fm, RDSFMPlayerThread):
            prefix = "RDS"
        else:
            prefix = "▶"
        self._sig_lbl.setText(f"{prefix} " + "█" * bars + "░" * (10 - bars))

    def _update_station_text(self, mhz):
        best = None
        best_diff = 999.0
        for f, info in STATIONS.items():
            diff = abs(f - mhz)
            if diff < best_diff:
                best_diff = diff
                best = info
        if best and best_diff < 0.15:
            self._station_lbl.setText(f"📻 {best['name']}")
            self._song_lbl.setText(f"🎵 {random.choice(best['songs'])}")
            self._station_lbl.setStyleSheet("color:#ffaa44;")
            self._song_lbl.setStyleSheet("color:#888;")
        else:
            self._station_lbl.setText("📻 No signal")
            self._song_lbl.setText("🎵 —")
            self._station_lbl.setStyleSheet("color:#444;")
            self._song_lbl.setStyleSheet("color:#333;")

    def _on_sim_play(self, city, freq_mhz, checked):
        for c, btn in self._sim_btns.items():
            if c != city:
                btn.setChecked(False)
        if not checked:
            if self._fm:
                self._disconnect_rds_signals(self._fm)
                self._fm.stop()
                if self._fm in self._threads:
                    self._threads.remove(self._fm)
                self._fm = None
            # restore default Site-1 player type
            cls = getattr(self, '_default_player_cls', SimulatedFMPlayerThread)
            self._fm = cls(self._ring, freq_mhz=self._fsl.value() / 10.0)
            self._fm.sig_rms.connect(self._on_rms)
            if isinstance(self._fm, RDSFMPlayerThread):
                self._connect_rds_signals()
            self._fm.start()
            self._threads.append(self._fm)
            self._update_station_text(self._fsl.value() / 10.0)
            return
        self._fsl.setValue(int(freq_mhz * 10))
        # _on_freq is triggered by the slider automatically

    def _on_rds_ps(self, text):
        self._rds_ps_lbl.setText(f"<b>PS</b>: <span style='color:#ffcc66'>{text}</span>")

    def _on_rds_rt(self, text):
        self._rds_rt_lbl.setText(f"<b>RT</b>: <span style='color:#aaddff'>{text}</span>")

    def _on_rds_ct(self, text):
        self._rds_ct_lbl.setText(f"<b>CT</b>: <span style='color:#88ffaa'>{text}</span>")

    def _on_rds_pty(self, text):
        self._rds_pty_lbl.setText(f"<b>PTY</b>: <span style='color:#ff88cc'>{text}</span>")

    def _refresh_audio_fft(self):
        N     = 8192
        audio = self._ring.peek(N)
        if len(audio) < N // 2:
            return
        if len(audio) < N:
            audio = np.pad(audio, (0, N - len(audio)))
        pwr = 20 * np.log10(np.abs(np.fft.rfft(audio * np.hanning(N))) / N + 1e-9)
        khz = np.fft.rfftfreq(N, 1 / FS) / 1000
        self._acurve.setData(khz, pwr)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    def closeEvent(self, ev):
        self._fft_timer.stop()
        if self._stream:
            self._stream.stop()
            self._stream.close()
        for t in self._threads:
            t.stop()
        ev.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QMainWindow { background-color: #05070b; }
        QWidget { color: #8fa0bc; font-family: 'Space Grotesk', 'Segoe UI', sans-serif; }
        QLabel { font-family: 'JetBrains Mono', 'Consolas', monospace; }
        QPushButton {
            background: rgba(0, 229, 255, 0.05);
            border: 1px solid rgba(0, 229, 255, 0.3);
            color: #8fa0bc;
            border-radius: 4px;
            padding: 6px 12px;
            font-family: 'JetBrains Mono', monospace;
        }
        QPushButton:hover {
            border: 1px solid #00e5ff;
            background: rgba(0, 229, 255, 0.15);
        }
        QPushButton:pressed {
            background: #00e5ff;
            color: #000;
        }
        QGroupBox {
            border: 1px solid rgba(0, 229, 255, 0.15);
            background: rgba(10, 14, 23, 0.75);
            margin-top: 8px;
            padding-top: 8px;
        }
        QFrame {
            border: 1px solid rgba(0, 229, 255, 0.15);
            background: rgba(10, 14, 23, 0.75);
        }
        QComboBox, QSpinBox {
            background: #0a0e17;
            border: 1px solid rgba(0, 229, 255, 0.3);
            color: #8fa0bc;
            font-family: 'JetBrains Mono', monospace;
            padding: 3px 6px;
        }
    """)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())