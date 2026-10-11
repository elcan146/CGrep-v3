"""
calculate.py – Pure SDR / DSP calculation utilities.
========================================================
No Qt, no hardware, no audio devices.  Every function is stateless and
testable with plain numpy (and optionally scipy).  Import this wherever
you need the math and nothing else.

Typical usage:
    import calculate as calc
    spec_db = calc.compute_fft_db(iq_samples, fft_size=4096)
    audio   = calc.wfm_demod_block(iq_samples, fs=2.4e6, bw=200e3)
"""

from __future__ import annotations

import math
from typing import Tuple

try:
    from scipy import signal
    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUDIO_RATE = 48_000          # Hz
FM_DEVIATION = 75_000        # Hz (FM peak deviation)
DEEMPH_TAU_US = 75.0         # 75 µs (EU/Asia; use 50 µs for Americas)
LO_OFFSET_HZ = 250_000       # Hz (hardware offset-tuning shift)

# ---- Hardware / runtime defaults (RTL-SDR setup) ----
DEFAULT_FS = 2_400_000       # Sample rate (Hz)
FM_BAND_START_HZ = 87.5e6    # FM band lower edge
FM_BAND_STOP_HZ  = 108.0e6   # FM band upper edge
DEFAULT_GAIN_DB = 28.0        # Initial RF gain
DEFAULT_FFT_SIZE = 8192       # Primary FFT size (also try 16384)
DEFAULT_WINDOW = "blackmanharris"
TARGET_FPS = 60.0             # Spectrum refresh rate target
DEFAULT_WFM_BW_HZ = 200e3     # FM channel bandwidth (180–220 kHz)
NOISE_FLOOR_CLAMP = -95.0     # dBm — below this is clamped
STRONG_SIGNAL_CLAMP = -15.0   # dBm — above this is clamped
REF_DBM_0DBFS = -30.0         # rough rtl-sdr calibration

# ---------------------------------------------------------------------------
# 1. FFT / Spectrum
# ---------------------------------------------------------------------------

def blackmanharris_window(n: int) -> np.ndarray:
    """Return a Blackman-Harris window of length n as float32."""
    if HAS_SCIPY:
        return signal.windows.blackmanharris(n).astype(np.float32)
    # Minimal fallback (not as good, but works for tests)
    return np.blackman(n).astype(np.float32)


def compute_fft_db(iq: np.ndarray, fft_size: int | None = None,
                   window: np.ndarray | None = None) -> np.ndarray:
    """
    Windowed FFT of complex IQ samples, returned as dBFS float32.

    Parameters
    ----------
    iq        : complex64 samples
    fft_size  : None → len(iq); otherwise zero-pad / truncate
    window    : None → Blackman-Harris(fft_size)

    Returns
    -------
    spec_db   : float32 array, fftshifted, length = fft_size
    """
    if fft_size is None:
        fft_size = len(iq)
    if window is None:
        window = blackmanharris_window(fft_size)
    win_norm = float(np.sum(window))

    seg = iq[-fft_size:].copy()
    if seg.size < fft_size:
        seg = np.pad(seg, (0, fft_size - seg.size))

    spec = np.fft.fftshift(np.fft.fft(seg * window))
    mag = np.abs(spec) / win_norm + 1e-12
    return (20.0 * np.log10(mag) + 29.0).astype(np.float32)


def apply_dc_notch(spec_db: np.ndarray, half_width: int = 4) -> np.ndarray:
    """
    Replace the centre bins with linear interpolation to hide the DC spike.
    Works on an already-fftshifted spectrum.
    """
    n = len(spec_db)
    ctr = n // 2
    lo = max(0, ctr - half_width)
    hi = min(n - 1, ctr + half_width)
    if lo < hi:
        spec_db[lo:hi] = np.interp(
            np.arange(lo, hi),
            [lo - 1, hi],
            [spec_db[lo - 1], spec_db[hi]])
    return spec_db


def freq_axis_hz(fc: float, fs: float, n_bins: int) -> np.ndarray:
    """Absolute frequency axis (Hz) for an fftshifted spectrum."""
    return fc + (np.arange(n_bins) - n_bins / 2) * (fs / n_bins)


def freq_axis_mhz(fc: float, fs: float, n_bins: int) -> np.ndarray:
    """Absolute frequency axis (MHz) for an fftshifted spectrum."""
    return freq_axis_hz(fc, fs, n_bins) / 1e6


def stitch_sweep_chunks(chunks: list[np.ndarray]) -> np.ndarray:
    """Concatenate a list of per-chunk spectra into one panoramic array."""
    return np.concatenate(chunks)


def build_panoramic_freq_axis(start_hz: float, stop_hz: float,
                            step_hz: float, n_fft: int, crop: int) -> np.ndarray:
    """Build the full frequency axis for a stitched panoramic sweep."""
    bins_per_chunk = n_fft - 2 * crop
    n_steps = int(round((stop_hz - start_hz) / step_hz)) + 1
    total_bins = bins_per_chunk * n_steps
    return np.linspace(start_hz / 1e6, stop_hz / 1e6, total_bins)


def peak_detect(spectrum_db: np.ndarray, freq_axis_mhz: np.ndarray,
                height_db: float = -65.0, min_dist_mhz: float = 0.2,
                prominence: float = 6.0) -> tuple[np.ndarray, np.ndarray]:
    """
    Find local maxima in a stitched panoramic spectrum.
    Returns (peak_freqs_mhz, peak_powers_db).
    Falls back to a simple local-max finder if scipy is unavailable.
    """
    try:
        from scipy.signal import find_peaks
        bin_dist = max(1, int(round(min_dist_mhz / (freq_axis_mhz[1] - freq_axis_mhz[0]))))
        idxs, _ = find_peaks(spectrum_db, height=height_db, distance=bin_dist,
                             prominence=prominence)
        if idxs.size == 0:
            return np.array([]), np.array([])
        return freq_axis_mhz[idxs], spectrum_db[idxs]
    except Exception:
        # Simple fallback
        loc = (spectrum_db[1:-1] > spectrum_db[:-2]) & (spectrum_db[1:-1] > spectrum_db[2:])
        idxs = np.where(loc & (spectrum_db[1:-1] > height_db))[0] + 1
        if idxs.size == 0:
            return np.array([]), np.array([])
        # Enforce minimum distance
        keep = []
        last = -99999
        step = max(1, int(round(min_dist_mhz / abs(freq_axis_mhz[1] - freq_axis_mhz[0]))))
        for i in idxs:
            if i - last >= step:
                keep.append(i)
                last = i
        keep = np.array(keep)
        return freq_axis_mhz[keep], spectrum_db[keep]


def clamp_dbm(val: float) -> float:
    """Clamp a dBm value between NOISE_FLOOR_CLAMP and STRONG_SIGNAL_CLAMP."""
    return max(NOISE_FLOOR_CLAMP, min(STRONG_SIGNAL_CLAMP, val))


def clamp_spec_db(spec_db: np.ndarray) -> np.ndarray:
    """Clamp a dBFS spectrum to the calibrated hardware range and return in-place."""
    lo = dbm_to_dbfs(NOISE_FLOOR_CLAMP)
    hi = dbm_to_dbfs(STRONG_SIGNAL_CLAMP)
    return np.clip(spec_db, lo, hi)


def fft_interval_for_fps(fs: float, fps: float = TARGET_FPS) -> float:
    """Minimum seconds between FFT updates to hit a target FPS."""
    return 1.0 / fps


def freq_resolution_hz(fs: float, n_fft: int) -> float:
    """FFT bin spacing in Hz."""
    return fs / n_fft


# ---------------------------------------------------------------------------
# 2. Digital LO / Mixer
# ---------------------------------------------------------------------------

def apply_lo_shift(iq: np.ndarray, fs: float, offset_hz: float,
                   start_phase: float = 0.0) -> Tuple[np.ndarray, float]:
    """
    Digitally mix IQ by offset_hz to undo hardware offset tuning.

    Returns
    -------
    mixed    : complex64 array
    next_phase : phase accumulator to pass to the next block call
    """
    n = iq.size
    step = -2.0 * math.pi * offset_hz / fs
    ph = start_phase + step * np.arange(n, dtype=np.float64)
    mix = np.exp(1j * ph).astype(np.complex64)
    out = iq * mix
    next_phase = float((start_phase + step * n) % (2.0 * math.pi))
    return out, next_phase


# ---------------------------------------------------------------------------
# 3. FIR / IIR filter helpers
# ---------------------------------------------------------------------------

def firwin_lpf(cutoff_hz: float, fs: float, numtaps: int = 129) -> np.ndarray:
    """Design a real FIR low-pass filter; returns float32 taps."""
    if HAS_SCIPY:
        return signal.firwin(numtaps, cutoff_hz, fs=fs).astype(np.float32)
    # Minimal scipy-less fallback
    fc_norm = cutoff_hz / (fs / 2.0)
    n = np.arange(numtaps)
    h = np.sinc(fc_norm * (n - (numtaps - 1) / 2))
    h *= np.hamming(numtaps)
    h /= np.sum(h)
    return h.astype(np.float32)


def apply_fir(iq: np.ndarray, taps: np.ndarray,
              zi: np.ndarray | None = None) -> Tuple[np.ndarray, np.ndarray]:
    """Stateful FIR filtering using scipy.lfilter if available."""
    if HAS_SCIPY:
        out, zf = signal.lfilter(taps, [1.0], iq, zi=zi if zi is not None else np.zeros(len(taps) - 1, dtype=iq.dtype))
        return out, zf
    # Stateless fallback (first call only, no state carry)
    padded = np.concatenate([zi if zi is not None else np.zeros(len(taps) - 1, dtype=iq.dtype), iq])
    out = np.convolve(padded, taps, mode='valid')
    new_zi = padded[-(len(taps) - 1):].copy()
    return out, new_zi


def butter_lpf(cutoff_hz: float, fs: float, order: int = 4) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Design Butterworth LPF. Returns (b, a, zi_initial)."""
    if not HAS_SCIPY:
        raise RuntimeError("scipy is required for butter_lpf")
    nyq = fs / 2.0
    b, a = signal.butter(order, cutoff_hz / nyq, btype='low')
    zi = signal.lfilter_zi(b, a)
    return b.astype(np.float32), a.astype(np.float32), zi.astype(np.float32)


def apply_butter(x: np.ndarray, b: np.ndarray, a: np.ndarray,
                 zi: np.ndarray | None = None) -> Tuple[np.ndarray, np.ndarray]:
    """Stateful Butterworth IIR filter."""
    if not HAS_SCIPY:
        raise RuntimeError("scipy is required for apply_butter")
    if zi is None:
        zi = np.zeros(max(len(a), len(b)) - 1, dtype=np.float32)
    out, zf = signal.lfilter(b, a, x, zi=zi)
    return out.astype(np.float32, copy=False), zf.astype(np.float32, copy=False)


# ---------------------------------------------------------------------------
# 4. WFM demodulation chain
# ---------------------------------------------------------------------------

def configure_dsp(fs: float, bw: float, audio_rate: int = AUDIO_RATE) -> dict:
    """
    Build all filter coefficients and resampling ratios for WFM demod.

    Returns a dict you can keep as DSP state:
      - 'lpf_taps'   : IF anti-alias FIR
      - 'lpf_zi'     : FIR delay initial state
      - 'dec1'       : integer decimation factor
      - 'fs_if'      : IF sample rate after dec1
      - 'resamp_up'  : rational resample up-factor
      - 'resamp_dn'  : rational resample down-factor
      - 'deemph_b'   : de-emphasis IIR numerator
      - 'deemph_a'   : de-emphasis IIR denominator
      - 'deemph_zi'  : de-emphasis delay initial state
      - 'aud_taps'   : 15 kHz audio LPF FIR
      - 'aud_zi'     : audio LPF delay initial state
    """
    from math import gcd
    target_if = max(bw * 1.2, 240e3)
    dec1 = max(int(round(fs / target_if)), 1)
    fs_if = fs / dec1

    cutoff = min(max(bw * 0.5, 100e3), fs_if * 0.45)
    lpf_taps = firwin_lpf(cutoff, fs, numtaps=129)

    fs_if_int = int(round(fs_if))
    g = gcd(fs_if_int, audio_rate)
    up = audio_rate // g
    dn = fs_if_int // g

    # 75 µs de-emphasis IIR
    tau = DEEMPH_TAU_US * 1e-6
    dt = 1.0 / audio_rate
    alpha = dt / (tau + dt)
    b = np.array([alpha], dtype=np.float32)
    a = np.array([1.0, -(1.0 - alpha)], dtype=np.float32)

    # 15 kHz audio LPF
    aud_taps = firwin_lpf(15000.0, audio_rate, numtaps=127)

    return {
        "lpf_taps": lpf_taps,
        "lpf_zi": np.zeros(len(lpf_taps) - 1, dtype=np.complex64),
        "dec1": dec1,
        "fs_if": fs_if,
        "resamp_up": up,
        "resamp_dn": dn,
        "deemph_b": b,
        "deemph_a": a,
        "deemph_zi": np.zeros(max(len(a), len(b)) - 1, dtype=np.float32),
        "aud_taps": aud_taps,
        "aud_zi": np.zeros(len(aud_taps) - 1, dtype=np.float32),
        "prev_iq": np.complex64(1.0 + 0.0j),
        "resamp_tail": np.zeros(0, dtype=np.float32),
    }


def wfm_demod_block(iq: np.ndarray, fs: float, bw: float,
                    state: dict | None = None,
                    audio_rate: int = AUDIO_RATE) -> Tuple[np.ndarray, dict]:
    """
    Demodulate one block of complex IQ into mono WFM audio.

    Parameters
    ----------
    iq    : complex64 IQ samples at rate fs
    fs    : sample rate of iq (Hz)
    bw    : WFM channel bandwidth (Hz)
    state : dict from configure_dsp() or previous call; None → auto-init

    Returns
    -------
    audio : float32 mono audio at AUDIO_RATE
    state : updated state dict (pass back to next call)
    """
    if state is None:
        state = configure_dsp(fs, bw, audio_rate)
    elif state.get("_dsp_cfg_fs") != fs or state.get("_dsp_cfg_bw") != bw:
        state = configure_dsp(fs, bw, audio_rate)

    # Stage 1: LPF + decimate
    filtered, state["lpf_zi"] = apply_fir(
        iq, state["lpf_taps"], state["lpf_zi"])
    iq_if = filtered[::state["dec1"]].astype(np.complex64, copy=False)
    fs_if = state["fs_if"]
    if iq_if.size < 2:
        return np.zeros(0, np.float32), state

    # Stage 2: FM discriminator (phase difference)
    x = np.empty(iq_if.size, dtype=np.complex64)
    x[0] = iq_if[0] * np.conj(state["prev_iq"])
    x[1:] = iq_if[1:] * np.conj(iq_if[:-1])
    state["prev_iq"] = iq_if[-1]
    demod = np.angle(x).astype(np.float32)
    demod *= fs_if / (2.0 * math.pi * FM_DEVIATION)

    # Stage 3: rational resample to audio_rate
    tail = state["resamp_tail"]
    TAIL_LEN = 64
    if tail.size:
        x_in = np.concatenate((tail, demod))
        consumed_offset = tail.size
    else:
        x_in = demod
        consumed_offset = 0
    state["resamp_tail"] = demod[-TAIL_LEN:].copy() if demod.size >= TAIL_LEN else demod.copy()

    if HAS_SCIPY:
        audio = signal.resample_poly(
            x_in, state["resamp_up"], state["resamp_dn"]).astype(np.float32, copy=False)
    else:
        # linear-interp fallback
        num = int(len(x_in) * state["resamp_up"] / state["resamp_dn"])
        audio = np.interp(np.linspace(0, len(x_in) - 1, num),
                          np.arange(len(x_in)), x_in).astype(np.float32)

    skip_out = int(round(consumed_offset * state["resamp_up"] / state["resamp_dn"]))
    if skip_out > 0 and skip_out < audio.size:
        audio = audio[skip_out:]

    # Stage 4: de-emphasis (IIR)
    if HAS_SCIPY:
        audio, state["deemph_zi"] = signal.lfilter(
            state["deemph_b"], state["deemph_a"], audio, zi=state["deemph_zi"])
    else:
        audio = audio  # no de-emphasis without scipy

    # Stage 5: 15 kHz audio LPF (FIR)
    audio, state["aud_zi"] = apply_fir(
        audio, state["aud_taps"], state["aud_zi"])
    audio = audio.astype(np.float32, copy=False)

    # Soft clip
    np.clip(audio, -1.0, 1.0, out=audio)

    state["_dsp_cfg_fs"] = fs
    state["_dsp_cfg_bw"] = bw
    return audio, state


# ---------------------------------------------------------------------------
# 5. FM demod from rtl_sdr uint8 IQ (dashboard.py style)
# ---------------------------------------------------------------------------

def fm_demod_uint8(iq_bytes: bytes, iq_rate: float,
                   last_iq: complex | None = None) -> Tuple[np.ndarray, complex]:
    """
    Demodulate raw uint8 IQ bytes (from rtl_sdr) to MPX float @ iq_rate.

    Returns
    -------
    mpx   : float32 FM-demodulated multiplex signal
    last  : last complex sample for continuity in next block
    """
    raw = np.frombuffer(iq_bytes, dtype=np.uint8).astype(np.float32)
    raw = (raw - 127.5) / 127.5
    iq = raw[0::2] + 1j * raw[1::2]
    if last_iq is not None:
        iq = np.concatenate([[last_iq], iq])
    phase = np.angle(iq)
    dphase = np.diff(phase)
    dphase = np.mod(dphase + np.pi, 2 * np.pi) - np.pi
    last = iq[-1]
    mpx = dphase * (iq_rate / (2 * math.pi * FM_DEVIATION))
    return mpx.astype(np.float32), last


def resample_mpx_to_audio(mpx: np.ndarray, iq_rate: float,
                          mpx_rate: float = 228_000,
                          audio_rate: int = AUDIO_RATE) -> np.ndarray:
    """
    Resample MPX (from rtl_sdr IQ rate) → MPX rate → audio_rate.
    Returns float32 mono audio.
    """
    # Step 1: iq_rate → mpx_rate
    if HAS_SCIPY:
        mpx_228 = signal.resample_poly(mpx, int(mpx_rate / 1000), int(iq_rate / 1000))
    else:
        num = int(len(mpx) * mpx_rate / iq_rate)
        mpx_228 = np.interp(np.linspace(0, len(mpx) - 1, num),
                            np.arange(len(mpx)), mpx).astype(np.float32)

    # Step 2: low-pass filter mpx_228 at 15 kHz (mono audio)
    mono = apply_fir_lpf(mpx_228, cutoff_hz=15000.0, fs=mpx_rate, numtaps=51)

    # Step 3: mpx_rate → audio_rate
    if HAS_SCIPY:
        audio = signal.resample_poly(mono, int(audio_rate / 1000), int(mpx_rate / 1000))
    else:
        num = int(len(mono) * audio_rate / mpx_rate)
        audio = np.interp(np.linspace(0, len(mono) - 1, num),
                          np.arange(len(mono)), mono).astype(np.float32)
    return audio.astype(np.float32, copy=False)


def apply_fir_lpf(x: np.ndarray, cutoff_hz: float, fs: float,
                  numtaps: int = 51, state: np.ndarray | None = None) -> Tuple[np.ndarray, np.ndarray]:
    """Convenience: design + apply a FIR low-pass in one call."""
    taps = firwin_lpf(cutoff_hz, fs, numtaps=numtaps)
    if state is None:
        state = np.zeros(len(taps) - 1, dtype=np.float32)
    padded = np.concatenate([state, x])
    out = np.convolve(padded, taps, mode='valid')
    new_state = padded[-(len(taps) - 1):].copy()
    return out.astype(np.float32, copy=False), new_state


# ---------------------------------------------------------------------------
# 6. Audio quality / signal metrics
# ---------------------------------------------------------------------------

def rms_db(x: np.ndarray) -> float:
    """RMS level in dBFS (full-scale = 1.0)."""
    rms = float(np.sqrt(np.mean(x ** 2)))
    return 20.0 * math.log10(rms + 1e-9)


def rms_linear(x: np.ndarray) -> float:
    """RMS level linear (0..1)."""
    return float(np.sqrt(np.mean(x ** 2)))


def assess_audio_quality(chunk: np.ndarray, fs: int = AUDIO_RATE) -> dict:
    """
    Estimate FM audio quality from a demodulated chunk.

    Returns a dict with keys:
      - 'rms'        : linear RMS
      - 'rms_db'     : dBFS
      - 'energy_ok'  : bool (low-freq energy dominates)
      - 'flat_ok'    : bool (speech band is tonal, not noise)
      - 'pilot_ok'   : bool (19 kHz pilot detected)
      - 'indicators' : int (how many of the 3 passed)
      - 'quality'    : float 0..1
      - 'state'      : 'silence' | 'noise' | 'signal'
    """
    rms = rms_linear(chunk)
    if rms < 0.002:
        return {"rms": rms, "rms_db": -60.0, "energy_ok": False,
                "flat_ok": False, "pilot_ok": False, "indicators": 0,
                "quality": 0.0, "state": "silence"}

    fft_mag = np.abs(np.fft.rfft(chunk)).astype(np.float64)
    n_fft = len(fft_mag)
    freqs = np.linspace(0, fs / 2, n_fft)

    lo_mask = freqs < 8000
    hi_mask = freqs >= 8000
    e_lo = float(np.mean(fft_mag[lo_mask] ** 2)) if lo_mask.sum() > 0 else 0
    e_hi = float(np.mean(fft_mag[hi_mask] ** 2)) if hi_mask.sum() > 0 else 1
    lo_ratio = e_lo / (e_hi + 1e-10)
    energy_ok = lo_ratio > 6.0

    speech = fft_mag[lo_mask]
    speech = np.maximum(speech, 1e-10)
    geo = float(np.exp(np.mean(np.log(speech))))
    arith = float(np.mean(speech))
    flatness = geo / arith
    flat_ok = flatness < 0.30

    pilot_mask = (freqs >= 18500) & (freqs <= 19500)
    other_mask = (freqs >= 15000) & (freqs <= 23000) & ~pilot_mask
    e_pilot = float(np.mean(fft_mag[pilot_mask] ** 2)) if pilot_mask.sum() > 0 else 0
    e_other = float(np.mean(fft_mag[other_mask] ** 2)) if other_mask.sum() > 0 else 1
    pilot_ok = (e_pilot / (e_other + 1e-10)) > 2.5

    indicators = sum([energy_ok, flat_ok, pilot_ok])
    if indicators >= 2:
        score = min(1.0, (lo_ratio / 20.0) * 0.5 + (1.0 - flatness) * 0.5)
        quality = round(score, 3)
        state = "signal"
    elif rms > 0.008 and indicators == 0:
        quality = round(min(0.35, rms * 5), 3)
        state = "noise"
    else:
        quality = round(min(0.5, rms * 8), 3)
        state = "noise"

    return {
        "rms": rms, "rms_db": rms_db(chunk),
        "energy_ok": energy_ok, "flat_ok": flat_ok, "pilot_ok": pilot_ok,
        "indicators": indicators, "quality": quality, "state": state,
    }


# ---------------------------------------------------------------------------
# 7. Signal strength / SNR measurement
# ---------------------------------------------------------------------------

def measure_band_power(spec_db: np.ndarray, fc: float, fs: float,
                       target_hz: float, bw_hz: float = 100e3) -> Tuple[float, float] | None:
    """
    Measure average power (dBFS) and SNR (dB) for a frequency band
    inside a given spectrum.

    Returns (power_db, snr_db) or None if target is out of range.
    """
    n = len(spec_db)
    freqs = freq_axis_hz(fc, fs, n)
    if target_hz < freqs[0] or target_hz > freqs[-1]:
        return None
    mask = (freqs >= target_hz - bw_hz / 2) & (freqs <= target_hz + bw_hz / 2)
    if mask.sum() < 4:
        return None
    noise_floor = float(np.percentile(spec_db, 10))
    power_db = float(spec_db[mask].mean())
    snr_db = round(power_db - noise_floor, 1)
    return round(power_db, 1), snr_db


def signal_bars(rms_db: float, max_bars: int = 10, db_range: Tuple[float, float] = (-60, 0)) -> int:
    """Map RMS dBFS to a 0..max_bars signal strength indicator."""
    lo, hi = db_range
    return int(np.clip((rms_db - lo) / (hi - lo) * max_bars, 0, max_bars))


# ---------------------------------------------------------------------------
# 8. Simulated spectrum / audio (for testing without hardware)
# ---------------------------------------------------------------------------

def simulated_spectrum(center_hz: float = 99.5e6, fs: float = 1.2e6,
                       n_bins: int = 4096, stations: list[tuple[float, float]] | None = None,
                       noise_floor_db: float = -78.0, t: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generate a fake RF spectrum with Gaussian station peaks.

    Parameters
    ----------
    center_hz      : spectrum centre frequency (Hz)
    fs             : sample rate (Hz)
    n_bins         : FFT size
    stations       : list of (freq_MHz, power_dB) tuples
    noise_floor_db : baseline noise level
    t              : time variable for drift animation

    Returns
    -------
    freqs_MHz, db  : frequency axis and spectrum in dB
    """
    freqs = np.fft.fftshift(np.fft.fftfreq(n_bins, 1 / fs))
    freqs = (freqs + center_hz) / 1e6
    bin_width = abs(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0003
    half_bw_mhz = 0.10

    db = np.random.normal(noise_floor_db, 2.0, n_bins)
    if stations:
        for f_mhz, power in stations:
            idx = np.argmin(np.abs(freqs - f_mhz))
            half_bins = int(half_bw_mhz / bin_width)
            start = max(0, idx - half_bins)
            end = min(len(db), idx + half_bins)
            if end <= start:
                continue
            n = end - start
            x = np.linspace(-2.5, 2.5, n)
            shape = np.exp(-x * x)
            drift = 4 * math.sin(t * 0.7)
            db[start:end] += shape * (power + drift)
    return freqs, db.astype(np.float32)


def simulated_fm_audio(n_samples: int, fs: int = AUDIO_RATE,
                       base_tone: float = 440.0, vol: float = 0.8) -> Tuple[np.ndarray, float]:
    """
    Generate synthetic FM-like audio (beat tones + occasional noise burst).

    Returns
    -------
    audio : float32
    rms_db : float
    """
    t = np.arange(n_samples) / fs
    phase1 = 2 * math.pi * base_tone * t
    phase2 = 2 * math.pi * base_tone * 1.5 * t
    beat = 0.5 + 0.5 * np.sin(2 * math.pi * 2 * t)
    sig = 0.4 * np.sin(phase1) + 0.25 * np.sin(phase2)
    sig *= beat
    if np.random.random() < 0.02:
        burst = np.random.normal(0, 0.08, n_samples) * np.hanning(n_samples)
        sig += burst
    rms = float(np.sqrt(np.mean(sig ** 2)))
    sig *= vol
    sig = np.clip(sig, -1.0, 1.0)
    return sig.astype(np.float32), 20.0 * math.log10(rms + 1e-9)


def noise_audio(n_samples: int, vol: float = 0.8) -> np.ndarray:
    """Generate plain Gaussian noise audio."""
    sig = np.random.normal(0, 0.18, n_samples)
    sig *= vol
    return np.clip(sig, -1.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
# 9. Colormap / LUT
# ---------------------------------------------------------------------------

def build_waterfall_lut() -> np.ndarray:
    """
    Build a 256x3 uint8 lookup table for waterfall display.
    Black → deep purple → magenta → pink → white.
    """
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


# ---------------------------------------------------------------------------
# 10. Coordinate / map helpers
# ---------------------------------------------------------------------------

def geojson_bounds(features: list[dict]) -> Tuple[float, float, float, float]:
    """
    Compute (min_lon, max_lon, min_lat, max_lat) from GeoJSON features.
    """
    min_lon, max_lon = 180.0, -180.0
    min_lat, max_lat = 90.0, -90.0
    for f in features:
        geom = f.get("geometry", {})
        coords = geom.get("coordinates", [])
        gtype = geom.get("type", "")
        rings = []
        if gtype == "Polygon":
            rings = coords
        elif gtype == "MultiPolygon":
            rings = [r for poly in coords for r in poly]
        for ring in rings:
            for lon, lat in ring:
                min_lon = min(min_lon, lon)
                max_lon = max(max_lon, lon)
                min_lat = min(min_lat, lat)
                max_lat = max(max_lat, lat)
    return min_lon, max_lon, min_lat, max_lat


def lonlat_to_pixel(lon: float, lat: float,
                    min_lon: float, max_lon: float,
                    min_lat: float, max_lat: float,
                    width: int, height: int,
                    padding: float = 0.05) -> Tuple[int, int]:
    """
    Map a longitude/latitude to pixel coordinates with optional padding.
    Returns (px, py).
    """
    lon_span = max_lon - min_lon
    lat_span = max_lat - min_lat
    min_lon -= lon_span * padding
    max_lon += lon_span * padding
    min_lat -= lat_span * padding
    max_lat += lat_span * padding
    scale_x = width / (max_lon - min_lon)
    scale_y = height / (max_lat - min_lat)
    px = int((lon - min_lon) * scale_x)
    py = int((lat - min_lat) * scale_y)
    return px, py


# ---------------------------------------------------------------------------
# 11. Generic helpers
# ---------------------------------------------------------------------------

def hz_to_mhz(hz: float) -> float:
    return hz / 1e6


def mhz_to_hz(mhz: float) -> float:
    return mhz * 1e6


def dbm_to_dbfs(dbm: float, ref_dbm: float = -30.0) -> float:
    """Rough mapping: rtl-sdr ~ -30 dBm ≈ 0 dBFS."""
    return dbm - ref_dbm


def dbfs_to_dbm(dbfs: float, ref_dbm: float = -30.0) -> float:
    return dbfs + ref_dbm


def ema_smooth(current: np.ndarray, previous: np.ndarray | None,
               alpha: float = 0.35) -> np.ndarray:
    """Exponential moving average for spectrum display smoothing."""
    if previous is None or previous.size != current.size:
        return current.copy()
    return (1 - alpha) * previous + alpha * current


# ---------------------------------------------------------------------------
# 12. Self-test / sanity checks
# ---------------------------------------------------------------------------

def _self_test():
    """Run quick sanity checks.  Call with `python calculate.py`"""
    print("Running calculate.py self-tests...")

    # 1. FFT
    iq = np.random.normal(0, 1, DEFAULT_FFT_SIZE) + 1j * np.random.normal(0, 1, DEFAULT_FFT_SIZE)
    iq = iq.astype(np.complex64)
    spec = compute_fft_db(iq, fft_size=DEFAULT_FFT_SIZE)
    assert spec.dtype == np.float32
    assert len(spec) == DEFAULT_FFT_SIZE
    res = freq_resolution_hz(DEFAULT_FS, DEFAULT_FFT_SIZE)
    print(f"  [OK] FFT  →  len={len(spec)}  max={spec.max():.2f} dB  res={res:.1f} Hz")

    # 2. Mixer
    mixed, ph = apply_lo_shift(iq[:1024], fs=2.4e6, offset_hz=250e3)
    assert mixed.dtype == np.complex64
    assert 0 <= ph < 2 * math.pi
    print(f"  [OK] LO shift  →  phase={ph:.4f} rad")

    # 3. WFM demod (stateful)
    state = configure_dsp(fs=DEFAULT_FS, bw=DEFAULT_WFM_BW_HZ)
    audio, state = wfm_demod_block(iq, fs=DEFAULT_FS, bw=DEFAULT_WFM_BW_HZ, state=state)
    assert audio.dtype == np.float32
    assert audio.size > 0
    print(f"  [OK] WFM demod  →  {audio.size} samples  rms={rms_linear(audio):.4f}")

    # 4. Audio quality
    qual = assess_audio_quality(audio[:AUDIO_RATE])
    print(f"  [OK] Quality  →  {qual}")

    # 5. Signal strength
    pwr, snr = measure_band_power(spec, fc=99.5e6, fs=DEFAULT_FS,
                                  target_hz=99.5e6, bw_hz=DEFAULT_WFM_BW_HZ)
    print(f"  [OK] Band power  →  pwr={pwr}  snr={snr}")

    # 5b. Clamp
    clamped = clamp_spec_db(spec)
    print(f"  [OK] Clamp  →  min={clamped.min():.1f}  max={clamped.max():.1f} dBFS")

    # 6. Simulated spectrum
    freqs, db = simulated_spectrum(stations=[(99.5, 40), (101.0, 35)])
    assert len(freqs) == len(db) == 4096
    print(f"  [OK] Sim spectrum  →  max={db.max():.1f} dB")

    # 7. Simulated audio
    aud, rms = simulated_fm_audio(4800)
    assert aud.dtype == np.float32
    print(f"  [OK] Sim audio  →  rms_db={rms:.1f}")

    # 8. LUT
    lut = build_waterfall_lut()
    assert lut.shape == (256, 3)
    print(f"  [OK] LUT  →  shape={lut.shape}")

    # 9. FIR
    taps = firwin_lpf(100e3, 2.4e6, numtaps=65)
    assert taps.dtype == np.float32
    assert len(taps) == 65
    print(f"  [OK] FIR  →  {len(taps)} taps")

    # 10. Geo bounds
    fake_features = [{
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[44.0, 38.0], [51.0, 38.0], [51.0, 42.0], [44.0, 42.0], [44.0, 38.0]]]
        }
    }]
    bounds = geojson_bounds(fake_features)
    assert bounds == (44.0, 51.0, 38.0, 42.0)
    print(f"  [OK] Geo bounds  →  {bounds}")

    print("\nAll tests passed.")


if __name__ == "__main__":
    _self_test()
