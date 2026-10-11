"""
loop.py — FM sweep (0.1 MHz steps) then DAB+ 12A (10 readings), repeat
"""

import os
import sys
import time
import signal
import datetime

import numpy as np
from rtlsdr import RtlSdr

# Settings from original files
SDR_GAIN = 49.6
FM_OFFSET_HZ = 333e3
DAB_12A_FREQ_HZ = 223.936e6
DAB_BANDWIDTH_HZ = 1.536e6
FFT_SIZE = 8192
CALIBRATION_DB = 41.0

running = True

def stop(sig, frame):
    global running
    running = False

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

def fm_sweep(sdr):
    """Exact logic from sweepmaybe.py"""
    frequencies_mhz = np.round(np.arange(88.7, 108.1, 0.1), 1)
    
    print(f"\n{'FM':<7} | {'Peak':<6} | {'Floor':<6} | {'SNR':<4}")
    print("-" * 35)
    
    for freq in frequencies_mhz:
        if not running:
            break
            
        target_hz = freq * 1e6
        sdr.center_freq = int(target_hz - FM_OFFSET_HZ)
        
        for _ in range(3):
            _ = sdr.read_samples(256 * 1024)
            
        samples = sdr.read_samples(32768)
        samples = samples - np.mean(samples)
        
        window = np.blackman(len(samples))
        windowed_samples = samples * window
        
        fft_data = np.fft.fftshift(np.fft.fft(windowed_samples))
        fft_mag = np.abs(fft_data) / len(samples)
        dbfs_spectrum = 20 * np.log10(fft_mag + 1e-12)
        
        kernel_size = 50
        kernel = np.ones(kernel_size) / kernel_size
        smoothed_spectrum = np.convolve(dbfs_spectrum, kernel, mode='same')
        
        freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/sdr.sample_rate))
        actual_freqs = sdr.center_freq + freqs
        
        target_mask = (actual_freqs >= target_hz - 110e3) & (actual_freqs <= target_hz + 110e3)
        
        peak_dbfs = np.max(smoothed_spectrum[target_mask])
        floor_dbfs = np.median(smoothed_spectrum)
        
        snr = peak_dbfs - floor_dbfs
        
        print(f"{freq:<7.1f} | {peak_dbfs:<6.0f} | {floor_dbfs:<6.0f} | {snr:<4.0f}")

def dab12a_monitor(sdr):
    """Exact logic from dab12a_monitor.py - 10 readings"""
    print(f"\n[DAB+ 12A Monitor - Site-1]")
    print(f"  Frequency: 223.936 MHz (Channel 12A)")
    print(f"  Interval: 1.0s")
    print(f"\n{'#':>4} | {'Time':>8} | {'Status':>4} | {'Power':>8} | {'Floor':>8} | {'SNR':>6}")
    print("-" * 55)
    
    last_state = False
    on_threshold = 6.0
    off_threshold = 4.5
    
    for i in range(10):
        if not running:
            break
        
        # Tune directly to center frequency
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
        
        signal_mask = (actual_freqs >= DAB_12A_FREQ_HZ - half_bw) & \
                      (actual_freqs <= DAB_12A_FREQ_HZ + half_bw)
        
        noise_mask = ((actual_freqs >= DAB_12A_FREQ_HZ - half_bw - 400e3) & \
                      (actual_freqs < DAB_12A_FREQ_HZ - half_bw)) | \
                     ((actual_freqs > DAB_12A_FREQ_HZ + half_bw) & \
                      (actual_freqs <= DAB_12A_FREQ_HZ + half_bw + 400e3))
        
        signal_power = float(np.mean(smoothed[signal_mask]))
        noise_floor = float(np.median(smoothed[noise_mask]))
        snr = signal_power - noise_floor
        
        # Hysteresis logic
        if snr >= on_threshold:
            last_state = True
        elif snr < off_threshold:
            last_state = False
        
        status = "ON " if last_state else "OFF"
        time_str = datetime.datetime.now().strftime("%H:%M:%S")
        
        print(f"{i+1:>4} | {time_str:>8} | {status:>4} | "
              f"{signal_power:>8.1f} | {noise_floor:>8.1f} | "
              f"{snr:>6.1f}")
        
        time.sleep(1.0)

# Main loop
print("=" * 60)
print("FM Sweep (88.7-108 MHz, 0.1 MHz steps) + DAB+ 12A (10 readings)")
print("Press Ctrl+C to stop")
print("=" * 60)

sdr = RtlSdr()
sdr.sample_rate = 2.4e6
sdr.err_ppm = 0
sdr.set_direct_sampling(False)
sdr.set_manual_gain_enabled(True)
sdr.set_agc_mode(False)
sdr.gain = SDR_GAIN

loop_count = 0

try:
    while running:
        loop_count += 1
        print(f"\n{'='*60}")
        print(f"LOOP #{loop_count}")
        print(f"{'='*60}")
        
        # Phase 1: FM sweep 88.7-108 MHz
        fm_sweep(sdr)
        
        if not running:
            break
        
        # Phase 2: DAB+ 12A, 10 readings
        dab12a_monitor(sdr)
        
        if not running:
            break
        
        print("\n[Loop complete] Restarting in 2 seconds...")
        time.sleep(2)

finally:
    sdr.close()
    print(f"\nStopped after {loop_count} loops")
