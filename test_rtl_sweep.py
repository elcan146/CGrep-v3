"""
test_rtl_sweep.py — Standalone raw diagnostic sweep for the RTL-SDR (HTOOL SDR 6)
===================================================================================
Isolated from fm_dab_sweep.py and the DB pipeline. Sweeps 88.7-108.0 MHz in
100 kHz steps, prints RAW (uncalibrated) peak dBFS, floor dBFS, and SNR to the
console only. Nothing is written to the database.

Purpose: verify what the RTL-SDR + pyrtlsdr is actually receiving, independent
of fm_dab_sweep.py's DB writes, calibration constants, or SDRconnect code path.

Usage:
    python test_rtl_sweep.py

Environment:
    SDR_GAIN   RTL-SDR gain in dB (default: 28.0)
    SDR_INDEX  RTL-SDR device index (default: 0)
"""

import os
import sys
import time

if os.name == 'nt':
    try:
        os.add_dll_directory(os.path.dirname(os.path.abspath(__file__)))
    except (AttributeError, OSError):
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        os.environ['PATH'] = _script_dir + os.pathsep + os.environ.get('PATH', '')

import numpy as np

try:
    from rtlsdr import RtlSdr
except ImportError:
    print("ERROR: pyrtlsdr not installed. Run: pip install pyrtlsdr")
    sys.exit(1)

SDR_GAIN = float(os.getenv("SDR_GAIN", "28.0"))
SDR_INDEX = int(os.getenv("SDR_INDEX", "0"))

SAMPLE_RATE = 2.4e6
FM_OFFSET_HZ = 333e3   # tune LO away from target so the DC spike doesn't land on it
FFT_READ_SAMPLES = 32768
DISCARD_READS = 3
DISCARD_SIZE = 256 * 1024

FREQ_START_MHZ = 88.7
FREQ_STOP_MHZ = 108.0
FREQ_STEP_MHZ = 0.1

running = True


def open_sdr():
    sdr = RtlSdr(device_index=SDR_INDEX)
    sdr.sample_rate = SAMPLE_RATE
    sdr.err_ppm = 0
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    sdr.gain = SDR_GAIN
    print(f"[SDR] Opened RTL-SDR #{SDR_INDEX}: fs={sdr.sample_rate/1e6:.3f} MHz, "
          f"gain={sdr.gain} dB (requested {SDR_GAIN})")
    return sdr


def measure(sdr, target_hz):
    sdr.center_freq = int(target_hz - FM_OFFSET_HZ)

    for _ in range(DISCARD_READS):
        sdr.read_samples(DISCARD_SIZE)

    samples = sdr.read_samples(FFT_READ_SAMPLES)
    samples = np.asarray(samples, dtype=np.complex64)
    samples = samples - np.mean(samples)

    window = np.blackman(len(samples))
    windowed = samples * window

    fft_data = np.fft.fftshift(np.fft.fft(windowed))
    fft_mag = np.abs(fft_data) / len(samples)
    dbfs = 20 * np.log10(fft_mag + 1e-12)   # RAW, no calibration offset

    kernel = np.ones(50) / 50
    smoothed = np.convolve(dbfs, kernel, mode='same')

    freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1 / sdr.sample_rate))
    actual_freqs = sdr.center_freq + freqs

    target_mask = (actual_freqs >= target_hz - 110e3) & (actual_freqs <= target_hz + 110e3)
    signal_exclusion = (actual_freqs >= target_hz - 250e3) & (actual_freqs <= target_hz + 250e3)
    dc_mask = (actual_freqs >= sdr.center_freq - 20e3) & (actual_freqs <= sdr.center_freq + 20e3)
    noise_mask = ~signal_exclusion & ~dc_mask

    peak_dbfs = float(np.max(smoothed[target_mask]))
    floor_dbfs = float(np.median(smoothed[noise_mask]))
    snr = peak_dbfs - floor_dbfs

    return peak_dbfs, floor_dbfs, snr


def main():
    sdr = open_sdr()
    frequencies_mhz = np.round(np.arange(FREQ_START_MHZ, FREQ_STOP_MHZ + FREQ_STEP_MHZ, FREQ_STEP_MHZ), 1)

    print(f"\n[Sweep] {FREQ_START_MHZ}-{FREQ_STOP_MHZ} MHz, {FREQ_STEP_MHZ*1000:.0f} kHz steps, RAW dBFS (no calibration)")
    print(f"{'Freq':<8} | {'Peak':<8} | {'Floor':<8} | {'SNR':<6}")
    print("-" * 40)

    try:
        while running:
            for freq_mhz in frequencies_mhz:
                target_hz = freq_mhz * 1e6
                try:
                    peak, floor, snr = measure(sdr, target_hz)
                except Exception as e:
                    print(f"{freq_mhz:<8.1f} | ERROR: {e}")
                    continue
                flag = "  <-- SIGNAL" if snr > 10 else ""
                print(f"{freq_mhz:<8.1f} | {peak:<8.2f} | {floor:<8.2f} | {snr:<6.2f}{flag}")
            print("-" * 40 + " (cycle complete) " + "-" * 40)
    except KeyboardInterrupt:
        print("\n[Interrupted] Exiting.")
    finally:
        sdr.close()


if __name__ == "__main__":
    main()
