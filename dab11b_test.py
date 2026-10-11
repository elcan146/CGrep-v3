"""
dab11b_test.py — Simple DAB+ 11B Channel Test (No Database)
=============================================================
Quick test script for monitoring DAB+ 11B multiplex (218.640 MHz).
Just prints signal metrics to console.

Usage:
    python dab11b_test.py
    
Configuration via environment:
    SDR_GAIN          RTL-SDR gain in dB (default: 49.6)
    MONITOR_INTERVAL  Seconds between readings (default: 1.0)
    SNR_THRESHOLD     Minimum SNR for "active" status (default: 6.0)
"""

import os
import sys
import time
import signal
import datetime
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SDR_GAIN = float(os.getenv("SDR_GAIN", "49.6"))
MONITOR_INTERVAL = float(os.getenv("MONITOR_INTERVAL", "1.0"))
SNR_THRESHOLD = float(os.getenv("SNR_THRESHOLD", "6.0"))

# DAB+ 11B Channel Constants
DAB_11B_FREQ_MHZ = 218.640
DAB_11B_FREQ_HZ = 218.640e6
DAB_BANDWIDTH_HZ = 1.536e6

# SDR Settings
SDR_SAMPLE_RATE = 2.4e6
TUNE_OFFSET_HZ = 333e3
FFT_SIZE = 8192
CALIBRATION_DB = 41.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class DAB11BReading:
    timestamp: datetime.datetime
    center_freq_mhz: float
    power_dbfs: float
    noise_floor_dbfs: float
    snr_db: float
    signal_variance: float
    is_active: bool


# ---------------------------------------------------------------------------
# SDR Interface
# ---------------------------------------------------------------------------
def open_sdr():
    """Initialize RTL-SDR."""
    try:
        from rtlsdr import RtlSdr
    except ImportError:
        print("ERROR: pyrtlsdr not installed. Run: pip install pyrtlsdr")
        sys.exit(1)

    sdr = RtlSdr()
    sdr.sample_rate = SDR_SAMPLE_RATE
    sdr.err_ppm = 0
    
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    sdr.gain = SDR_GAIN
    actual_gain = sdr.gain

    print(f"[SDR] Opened RTL-SDR: fs={SDR_SAMPLE_RATE/1e6:.3f} MHz, gain={actual_gain} dB")
    return sdr


def measure_dab11b(sdr):
    """Measure DAB+ 11B channel signal quality."""
    # Tune with offset to avoid DC spike in the DAB bandwidth
    tune_freq = DAB_11B_FREQ_HZ - TUNE_OFFSET_HZ
    sdr.center_freq = int(tune_freq)
    
    # Wait for PLL lock and flush stale samples
    time.sleep(0.06)
    for _ in range(3):
        _ = sdr.read_samples(256 * 1024)
    
    # Read samples for analysis
    samples = sdr.read_samples(FFT_SIZE * 4)
    samples = np.asarray(samples, dtype=np.complex64)[-FFT_SIZE:]
    
    # Remove DC component
    samples = samples - np.mean(samples)
    
    # Apply Blackman window
    window = np.blackman(len(samples))
    windowed = samples * window
    
    # FFT analysis
    fft_data = np.fft.fftshift(np.fft.fft(windowed))
    fft_mag = np.abs(fft_data) / len(samples)
    dbfs_spectrum = 20 * np.log10(fft_mag + 1e-12)
    
    # Apply calibration offset
    dbfs_spectrum = dbfs_spectrum + CALIBRATION_DB
    
    # Smooth spectrum
    kernel_size = 50
    kernel = np.ones(kernel_size) / kernel_size
    smoothed = np.convolve(dbfs_spectrum, kernel, mode='same')
    
    # Frequency axis
    freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/sdr.sample_rate))
    actual_freqs = sdr.center_freq + freqs
    
    # Define masks
    half_bw = DAB_BANDWIDTH_HZ / 2
    
    # Signal mask: the actual DAB+ 1.536 MHz bandwidth
    signal_mask = (actual_freqs >= DAB_11B_FREQ_HZ - half_bw) & \
                  (actual_freqs <= DAB_11B_FREQ_HZ + half_bw)
    
    # Noise mask: guard bands outside DAB+ bandwidth
    noise_mask = ((actual_freqs >= DAB_11B_FREQ_HZ - half_bw - 400e3) & \
                  (actual_freqs < DAB_11B_FREQ_HZ - half_bw)) | \
                 ((actual_freqs > DAB_11B_FREQ_HZ + half_bw) & \
                  (actual_freqs <= DAB_11B_FREQ_HZ + half_bw + 400e3))
    
    if not np.any(signal_mask) or not np.any(noise_mask):
        print("[WARN] Invalid frequency masks - check SDR tuning")
        return None
    
    # Calculate metrics
    signal_power = float(np.mean(smoothed[signal_mask]))
    noise_floor = float(np.median(smoothed[noise_mask]))
    snr = signal_power - noise_floor
    signal_variance = float(np.var(smoothed[signal_mask]))
    is_active = snr > SNR_THRESHOLD
    
    return DAB11BReading(
        timestamp=datetime.datetime.now(),
        center_freq_mhz=DAB_11B_FREQ_MHZ,
        power_dbfs=round(signal_power, 2),
        noise_floor_dbfs=round(noise_floor, 2),
        snr_db=round(snr, 2),
        signal_variance=round(signal_variance, 6),
        is_active=is_active
    )


# ---------------------------------------------------------------------------
# Main test loop
# ---------------------------------------------------------------------------
def run_test():
    """Simple test loop - just print to console."""
    sdr = open_sdr()
    
    reading_count = 0
    running = True
    
    def stop_handler(sig, frame):
        nonlocal running
        running = False
    
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    
    print(f"\n[DAB+ 11B Test Monitor]")
    print(f"  Frequency: {DAB_11B_FREQ_MHZ} MHz")
    print(f"  Bandwidth: {DAB_BANDWIDTH_HZ/1e3} kHz")
    print(f"  Interval: {MONITOR_INTERVAL}s")
    print(f"  SNR Threshold: {SNR_THRESHOLD} dB")
    print(f"\n{'#':>4} | {'Time':>8} | {'Status':>4} | {'Power':>8} | {'Floor':>8} | {'SNR':>6}")
    print("-" * 55)
    
    try:
        while running:
            t0 = time.monotonic()
            
            try:
                reading = measure_dab11b(sdr)
                
                if reading is None:
                    time.sleep(0.5)
                    continue
                
                reading_count += 1
                status = "ON" if reading.is_active else "OFF"
                time_str = reading.timestamp.strftime("%H:%M:%S")
                
                print(f"{reading_count:>4} | {time_str:>8} | {status:>4} | "
                      f"{reading.power_dbfs:>8.1f} | {reading.noise_floor_dbfs:>8.1f} | "
                      f"{reading.snr_db:>6.1f}")
                
            except Exception as e:
                print(f"[ERROR] {e}")
            
            elapsed = time.monotonic() - t0
            wait = max(0.0, MONITOR_INTERVAL - elapsed)
            if wait > 0:
                time.sleep(wait)
    
    finally:
        try:
            sdr.close()
        except Exception:
            pass
        print(f"\nTest stopped. Total readings: {reading_count}")


if __name__ == "__main__":
    run_test()
