"""
dab12a_monitor.py — DAB+ 12A Channel Monitor for Site-1
======================================================
Monitors the DAB+ 12A multiplex (223.936 MHz) - the active DAB+ frequency in Site-1.

Site-1 DAB+ Info:
    Frequency: 223.936 MHz (Channel 12A)
    Location: Site-1 TV Tower Complex
    Power: 3000W (upgraded March 2025)
    Coverage: Site-1, Absheron Peninsula, Central Aran region
    Services: 12 radio programs

Usage:
    python dab12a_monitor.py
    
Environment variables:
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

# Configuration
SDR_GAIN = float(os.getenv("SDR_GAIN", "49.6"))
MONITOR_INTERVAL = float(os.getenv("MONITOR_INTERVAL", "1.0"))
SNR_THRESHOLD = float(os.getenv("SNR_THRESHOLD", "6.0"))

# DAB+ 12A Channel - Site-1
DAB_12A_FREQ_MHZ = 223.936
DAB_12A_FREQ_HZ = 223.936e6
DAB_BANDWIDTH_HZ = 1.536e6

# SDR Settings (matches SDR++)
SDR_SAMPLE_RATE = 2.4e6
FFT_SIZE = 8192
CALIBRATION_DB = 41.0


@dataclass
class DAB12AReading:
    timestamp: datetime.datetime
    center_freq_mhz: float
    power_dbfs: float
    noise_floor_dbfs: float
    snr_db: float
    signal_variance: float
    is_active: bool


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


def measure_dab12a(sdr):
    """Measure DAB+ 12A channel signal quality."""
    # Tune directly to center frequency (matches SDR++ settings)
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
    dbfs_spectrum = 20 * np.log10(fft_mag + 1e-12) + CALIBRATION_DB
    
    kernel_size = 50
    kernel = np.ones(kernel_size) / kernel_size
    smoothed = np.convolve(dbfs_spectrum, kernel, mode='same')
    
    freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/sdr.sample_rate))
    actual_freqs = sdr.center_freq + freqs
    
    half_bw = DAB_BANDWIDTH_HZ / 2
    
    # Signal mask: DAB+ 1.536 MHz bandwidth
    signal_mask = (actual_freqs >= DAB_12A_FREQ_HZ - half_bw) & \
                  (actual_freqs <= DAB_12A_FREQ_HZ + half_bw)
    
    # Noise mask: guard bands outside DAB+ bandwidth
    noise_mask = ((actual_freqs >= DAB_12A_FREQ_HZ - half_bw - 400e3) & \
                  (actual_freqs < DAB_12A_FREQ_HZ - half_bw)) | \
                 ((actual_freqs > DAB_12A_FREQ_HZ + half_bw) & \
                  (actual_freqs <= DAB_12A_FREQ_HZ + half_bw + 400e3))
    
    if not np.any(signal_mask) or not np.any(noise_mask):
        print("[WARN] Invalid frequency masks - check SDR tuning")
        return None
    
    signal_power = float(np.mean(smoothed[signal_mask]))
    noise_floor = float(np.median(smoothed[noise_mask]))
    snr = signal_power - noise_floor
    signal_variance = float(np.var(smoothed[signal_mask]))
    is_active = snr > SNR_THRESHOLD
    
    return DAB12AReading(
        timestamp=datetime.datetime.now(),
        center_freq_mhz=DAB_12A_FREQ_MHZ,
        power_dbfs=round(signal_power, 2),
        noise_floor_dbfs=round(noise_floor, 2),
        snr_db=round(snr, 2),
        signal_variance=round(signal_variance, 6),
        is_active=is_active
    )


def run_monitor():
    """Monitor DAB+ 12A (Site-1)."""
    sdr = open_sdr()
    
    reading_count = 0
    running = True
    last_state = False  # For hysteresis
    
    def stop_handler(sig, frame):
        nonlocal running
        running = False
    
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    
    # Hysteresis thresholds
    ON_THRESHOLD = SNR_THRESHOLD
    OFF_THRESHOLD = SNR_THRESHOLD - 1.5  # Must drop 1.5 dB below to go OFF
    
    print(f"\n[DAB+ 12A Monitor - Site-1]")
    print(f"  Frequency: {DAB_12A_FREQ_MHZ} MHz (Channel 12A)")
    print(f"  Station: Site-1 TV Tower (3000W)")
    print(f"  Services: 12 radio programs")
    print(f"  Interval: {MONITOR_INTERVAL}s")
    print(f"  SNR ON: >{ON_THRESHOLD} dB | SNR OFF: <{OFF_THRESHOLD} dB")
    print(f"\n{'#':>4} | {'Time':>8} | {'Status':>4} | {'Power':>8} | {'Floor':>8} | {'SNR':>6}")
    print("-" * 55)
    
    try:
        while running:
            t0 = time.monotonic()
            
            try:
                reading = measure_dab12a(sdr)
                
                if reading is None:
                    time.sleep(0.5)
                    continue
                
                # Hysteresis logic
                if reading.snr_db >= ON_THRESHOLD:
                    last_state = True
                elif reading.snr_db < OFF_THRESHOLD:
                    last_state = False
                # else: keep last_state (hysteresis zone)
                
                reading_count += 1
                status = "ON " if last_state else "OFF"
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
        print(f"\nMonitor stopped. Total readings: {reading_count}")


if __name__ == "__main__":
    run_monitor()
