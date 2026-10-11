"""
test_rtl_inversion.py — Check for spectral inversion (I/Q swap) on this RTL-SDR
=================================================================================
Reproduces fm_dab_sweep.py's offset-tuning trick (tune 333 kHz BELOW target to
dodge the DC spike), then checks BOTH frequency-labeling conventions to see
which one actually finds the real signal peak. This tells us whether the
script's "actual_freqs = center_freq + freqs" formula is correct for this
hardware, or needs to be "center_freq - freqs" (spectral inversion).

Usage:
    python test_rtl_inversion.py 96.0
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
OFFSET_HZ = 333e3

target_hz = target_mhz * 1e6
lo_hz = target_hz - OFFSET_HZ  # same trick fm_dab_sweep.py uses

sdr = RtlSdr()
sdr.sample_rate = 2.4e6
sdr.center_freq = int(lo_hz)
sdr.gain = gain

print(f"Target: {target_mhz} MHz | LO tuned to: {sdr.center_freq/1e6:.4f} MHz "
      f"(offset {OFFSET_HZ/1e3:.0f} kHz below target)")

for _ in range(3):
    sdr.read_samples(256 * 1024)

samples = sdr.read_samples(32768)
samples = np.asarray(samples, dtype=np.complex64)
samples = samples - np.mean(samples)

window = np.blackman(len(samples))
windowed = samples * window

fft_data = np.fft.fftshift(np.fft.fft(windowed))
fft_mag = np.abs(fft_data) / len(samples)
dbfs = 20 * np.log10(fft_mag + 1e-12)

kernel = np.ones(50) / 50
smoothed = np.convolve(dbfs, kernel, mode='same')

bin_freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1 / sdr.sample_rate))

peak_idx = int(np.argmax(smoothed))
peak_bin_freq = bin_freqs[peak_idx]  # baseband offset of the strongest bin

label_normal = sdr.center_freq + peak_bin_freq     # fm_dab_sweep.py's current formula
label_inverted = sdr.center_freq - peak_bin_freq   # spectral-inversion-corrected formula

print(f"\nStrongest FFT bin: baseband offset = {peak_bin_freq/1e3:+.2f} kHz, power = {smoothed[peak_idx]:.2f} dBFS")
print(f"  Labeled as (current formula, center+offset): {label_normal/1e6:.4f} MHz")
print(f"  Labeled as (inverted formula,  center-offset): {label_inverted/1e6:.4f} MHz")
print(f"\nTarget was: {target_mhz:.4f} MHz")

if abs(label_normal - target_hz) < abs(label_inverted - target_hz):
    print("\n=> Current '+' formula matches the target better. No inversion detected.")
else:
    print("\n=> INVERTED '-' formula matches the target better. Spectral inversion (I/Q swap) detected!")

sdr.close()
