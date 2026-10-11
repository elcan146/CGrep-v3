"""
test_rtl_simple.py — Dead-simple raw RTL-SDR check, no tricks
================================================================
Tunes DIRECTLY to the target frequency (no offset-tuning, no exclusion
masks, no smoothing). Just opens the dongle, reads samples, does a plain
FFT, and prints max/mean/min dBFS. Nothing else.

Usage:
    python test_rtl_simple.py 94.5
    python test_rtl_simple.py 96.0
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

freq_mhz = float(sys.argv[1]) if len(sys.argv) > 1 else 94.5
gain = float(os.getenv("SDR_GAIN", "28.0"))

sdr = RtlSdr()
sdr.sample_rate = 2.4e6
sdr.center_freq = freq_mhz * 1e6
sdr.gain = gain

print(f"Tuned to {freq_mhz} MHz, sample_rate={sdr.sample_rate/1e6:.3f} MHz, gain={sdr.gain} dB")

for i in range(10):
    samples = sdr.read_samples(65536)
    samples = np.asarray(samples, dtype=np.complex64)

    mag = np.abs(samples)
    print(f"[{i}] IQ magnitude: mean={np.mean(mag):.6f} max={np.max(mag):.6f} std={np.std(mag):.6f}")

    fft = np.fft.fftshift(np.fft.fft(samples))
    fft_mag = np.abs(fft) / len(samples)
    dbfs = 20 * np.log10(fft_mag + 1e-12)

    print(f"    dBFS: max={np.max(dbfs):.2f} mean={np.mean(dbfs):.2f} min={np.min(dbfs):.2f}")

sdr.close()
