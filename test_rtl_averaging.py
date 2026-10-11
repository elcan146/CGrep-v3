"""
test_rtl_averaging.py — Does averaging multiple FFT frames reveal the weak signal?
====================================================================================
fm_dab_sweep.py takes ONE ~13.6ms snapshot (32768 samples @ 2.4 MSPS) per
frequency per sweep pass. SDR#'s waterfall integrates visually over several
seconds. This script captures N consecutive frames at the same offset-tuned
frequency, averages their power spectra (Welch-style), and compares the
single-frame vs averaged SNR to see if longer integration reveals the signal.

Usage:
    python test_rtl_averaging.py 96.0
    python test_rtl_averaging.py 94.5
"""

import os
import sys

if os.name == 'nt':
    try:
        os.add_dll_directory(os.path.dirname(os.path.abspath(__file__)))
    except (AttributeError, OSError):
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        os.environ['PATH'] = _script_dir + os.pathsep + os.environ.get('PATH', '')

import numpy as np
from rtlsdr import RtlSdr

target_mhz = float(sys.argv[1]) if len(sys.argv) > 1 else 96.0
gain = float(os.getenv("SDR_GAIN", "28.0"))
N_FRAMES = int(os.getenv("N_FRAMES", "40"))
OFFSET_HZ = 333e3

target_hz = target_mhz * 1e6
lo_hz = target_hz - OFFSET_HZ

sdr = RtlSdr()
sdr.sample_rate = 2.4e6
sdr.err_ppm = 0
sdr.set_direct_sampling(False)
sdr.set_manual_gain_enabled(True)
sdr.set_agc_mode(False)
sdr.center_freq = int(lo_hz)
sdr.gain = gain

print(f"Target {target_mhz} MHz | LO {sdr.center_freq/1e6:.4f} MHz | gain={sdr.gain} dB | frames={N_FRAMES}")

for _ in range(3):
    sdr.read_samples(256 * 1024)

window = np.blackman(32768)
power_sum = None

for i in range(N_FRAMES):
    samples = sdr.read_samples(32768)
    samples = np.asarray(samples, dtype=np.complex64)
    samples = samples - np.mean(samples)
    windowed = samples * window
    fft_data = np.fft.fftshift(np.fft.fft(windowed))
    fft_mag = np.abs(fft_data) / len(samples)
    power = fft_mag ** 2  # linear power for correct averaging
    power_sum = power if power_sum is None else power_sum + power

avg_power = power_sum / N_FRAMES
avg_dbfs = 10 * np.log10(avg_power + 1e-24)

kernel = np.ones(50) / 50
smoothed = np.convolve(avg_dbfs, kernel, mode='same')

bin_freqs = np.fft.fftshift(np.fft.fftfreq(32768, 1 / sdr.sample_rate))
actual_freqs = sdr.center_freq + bin_freqs

target_mask = (actual_freqs >= target_hz - 110e3) & (actual_freqs <= target_hz + 110e3)
signal_exclusion = (actual_freqs >= target_hz - 250e3) & (actual_freqs <= target_hz + 250e3)
dc_mask = (actual_freqs >= sdr.center_freq - 20e3) & (actual_freqs <= sdr.center_freq + 20e3)
noise_mask = ~signal_exclusion & ~dc_mask

peak_dbfs = float(np.max(smoothed[target_mask]))
floor_dbfs = float(np.median(smoothed[noise_mask]))
snr = peak_dbfs - floor_dbfs

print(f"\nAveraged over {N_FRAMES} frames (~{N_FRAMES*32768/2.4e6*1000:.0f} ms total):")
print(f"  Peak={peak_dbfs:.2f} dBFS  Floor={floor_dbfs:.2f} dBFS  SNR={snr:.2f} dB")

sdr.close()
