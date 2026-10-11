"""
combined_monitor.py — FM Sweep + DAB+ 12A Alternating Monitor
===============================================================
Alternates between:
  1. Full FM sweep (88.7-108 MHz) - single pass
  2. DAB+ 12A monitoring (223.936 MHz) - 10 readings
  3. Repeat

Usage:
    python combined_monitor.py
"""

import os
import sys
import time
import signal
import datetime
from dataclasses import dataclass

import numpy as np
from rtlsdr import RtlSdr

# Configuration
SDR_GAIN = float(os.getenv("SDR_GAIN", "49.6"))
FM_SNR_THRESHOLD = float(os.getenv("FM_SNR_THRESHOLD", "10.0"))
DAB_SNR_THRESHOLD = float(os.getenv("DAB_SNR_THRESHOLD", "6.0"))

# DAB+ 12A Channel - Site-1
DAB_12A_FREQ_MHZ = 223.936
DAB_12A_FREQ_HZ = 223.936e6
DAB_BANDWIDTH_HZ = 1.536e6

# SDR Settings
SDR_SAMPLE_RATE = 2.4e6
FM_OFFSET_HZ = 333e3  # Offset for FM to avoid DC spike
FFT_SIZE = 8192
CALIBRATION_DB = 41.0

# FM Frequency range
FM_START_MHZ = 88.7
FM_STOP_MHZ = 108.0
FM_STEP_MHZ = 0.1

running = True


def stop_handler(sig, frame):
    global running
    running = False
    print("\n[Monitor] Stopping...")


signal.signal(signal.SIGINT, stop_handler)
signal.signal(signal.SIGTERM, stop_handler)


def open_sdr():
    """Initialize RTL-SDR."""
    sdr = RtlSdr()
    sdr.sample_rate = SDR_SAMPLE_RATE
    sdr.err_ppm = 0
    sdr.set_direct_sampling(False)
    sdr.set_manual_gain_enabled(True)
    sdr.set_agc_mode(False)
    sdr.gain = SDR_GAIN
    return sdr


def fm_sweep(sdr):
    """
    Sweep FM band 88.7-108 MHz and show strong stations.
    Returns list of (freq_mhz, snr) tuples for stations above threshold.
    """
    frequencies_mhz = np.round(np.arange(FM_START_MHZ, FM_STOP_MHZ + 0.05, FM_STEP_MHZ), 1)
    stations_found = []
    
    print(f"\n[FM Sweep] Scanning {FM_START_MHZ}-{FM_STOP_MHZ} MHz...")
    print(f"{'Freq':<7} | {'Peak':<6} | {'Floor':<6} | {'SNR':<5} | {'Status':<6}")
    print("-" * 40)
    
    for freq in frequencies_mhz:
        if not running:
            break
            
        target_hz = freq * 1e6
        sdr.center_freq = int(target_hz - FM_OFFSET_HZ)
        
        time.sleep(0.03)  # PLL lock
        for _ in range(3):
            _ = sdr.read_samples(256 * 1024)
        
        samples = sdr.read_samples(32768)
        samples = samples - np.mean(samples)
        
        window = np.blackman(len(samples))
        windowed = samples * window
        
        fft_data = np.fft.fftshift(np.fft.fft(windowed))
        fft_mag = np.abs(fft_data) / len(samples)
        dbfs_spectrum = 20 * np.log10(fft_mag + 1e-12)
        
        kernel = np.ones(50) / 50
        smoothed = np.convolve(dbfs_spectrum, kernel, mode='same')
        
        freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/sdr.sample_rate))
        actual_freqs = sdr.center_freq + freqs
        
        target_mask = (actual_freqs >= target_hz - 110e3) & (actual_freqs <= target_hz + 110e3)
        
        peak_dbfs = np.max(smoothed[target_mask])
        floor_dbfs = np.median(smoothed)
        snr = peak_dbfs - floor_dbfs
        
        is_strong = snr > FM_SNR_THRESHOLD
        status = "STRONG" if is_strong else "-"
        
        # Only print strong stations or every 10th frequency to reduce output
        if is_strong or int(freq * 10) % 50 == 0:
            print(f"{freq:<7.1f} | {peak_dbfs:<6.0f} | {floor_dbfs:<6.0f} | {snr:<5.1f} | {status:<6}")
        
        if is_strong:
            stations_found.append((freq, snr, peak_dbfs))
    
    # Summary
    if stations_found:
        print(f"\n[FM Sweep] Found {len(stations_found)} strong stations:")
        for freq, snr, power in sorted(stations_found, key=lambda x: x[1], reverse=True):
            print(f"  {freq:.1f} MHz: SNR={snr:.1f} dB, Power={power:.1f} dBFS")
    else:
        print(f"\n[FM Sweep] No strong stations found (threshold: {FM_SNR_THRESHOLD} dB)")
    
    return stations_found


def measure_dab12a(sdr):
    """Measure DAB+ 12A channel signal quality."""
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
    
    # Signal mask: DAB+ 1.536 MHz bandwidth
    signal_mask = (actual_freqs >= DAB_12A_FREQ_HZ - half_bw) & \
                  (actual_freqs <= DAB_12A_FREQ_HZ + half_bw)
    
    # Noise mask: guard bands
    noise_mask = ((actual_freqs >= DAB_12A_FREQ_HZ - half_bw - 400e3) & \
                  (actual_freqs < DAB_12A_FREQ_HZ - half_bw)) | \
                 ((actual_freqs > DAB_12A_FREQ_HZ + half_bw) & \
                  (actual_freqs <= DAB_12A_FREQ_HZ + half_bw + 400e3))
    
    if not np.any(signal_mask) or not np.any(noise_mask):
        return None
    
    signal_power = float(np.mean(smoothed[signal_mask]))
    noise_floor = float(np.median(smoothed[noise_mask]))
    snr = signal_power - noise_floor
    
    return {
        'timestamp': datetime.datetime.now(),
        'power_dbfs': round(signal_power, 2),
        'noise_floor_dbfs': round(noise_floor, 2),
        'snr_db': round(snr, 2),
        'is_active': snr > DAB_SNR_THRESHOLD
    }


def dab12a_monitor(sdr, num_readings=10):
    """Monitor DAB+ 12A for specified number of readings."""
    print(f"\n[DAB+ 12A Monitor] Scanning {num_readings} readings at {DAB_12A_FREQ_MHZ} MHz...")
    print(f"{'#':>4} | {'Time':>8} | {'Status':>4} | {'Power':>8} | {'Floor':>8} | {'SNR':>6}")
    print("-" * 55)
    
    last_state = False
    on_threshold = DAB_SNR_THRESHOLD
    off_threshold = DAB_SNR_THRESHOLD - 1.5
    
    for i in range(num_readings):
        if not running:
            break
            
        try:
            reading = measure_dab12a(sdr)
            if reading is None:
                continue
            
            # Hysteresis
            if reading['snr_db'] >= on_threshold:
                last_state = True
            elif reading['snr_db'] < off_threshold:
                last_state = False
            
            status = "ON " if last_state else "OFF"
            time_str = reading['timestamp'].strftime("%H:%M:%S")
            
            print(f"{i+1:>4} | {time_str:>8} | {status:>4} | "
                  f"{reading['power_dbfs']:>8.1f} | {reading['noise_floor_dbfs']:>8.1f} | "
                  f"{reading['snr_db']:>6.1f}")
            
            time.sleep(1.0)  # 1 second between readings
            
        except Exception as e:
            print(f"[ERROR] DAB+ reading failed: {e}")
    
    if last_state:
        print(f"\n[DAB+ 12A Monitor] Signal ACTIVE (avg SNR ~{reading['snr_db']:.1f} dB)")
    else:
        print(f"\n[DAB+ 12A Monitor] Signal INACTIVE or weak")


def main():
    """Main loop: FM sweep -> DAB+ monitor -> repeat."""
    global running
    
    print("=" * 60)
    print("Combined FM + DAB+ Monitor")
    print("=" * 60)
    print(f"FM Range: {FM_START_MHZ}-{FM_STOP_MHZ} MHz")
    print(f"DAB+ Channel: {DAB_12A_FREQ_MHZ} MHz (12A - Site-1)")
    print(f"Gain: {SDR_GAIN} dB")
    print(f"\nPress Ctrl+C to stop")
    print("=" * 60)
    
    sdr = open_sdr()
    print(f"[SDR] Initialized: RTL-SDR @ {SDR_SAMPLE_RATE/1e6:.3f} MHz sample rate")
    
    loop_count = 0
    
    try:
        while running:
            loop_count += 1
            print(f"\n{'='*60}")
            print(f"LOOP #{loop_count} - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}")
            
            # Phase 1: FM Sweep
            fm_sweep(sdr)
            
            if not running:
                break
            
            # Phase 2: DAB+ 12A Monitor (10 readings)
            dab12a_monitor(sdr, num_readings=10)
            
            if not running:
                break
            
            # Brief pause before next loop
            print(f"\n[Loop {loop_count} complete] Restarting in 3 seconds...")
            time.sleep(3)
            
    except Exception as e:
        print(f"\n[ERROR] Main loop error: {e}")
        
    finally:
        try:
            sdr.close()
        except:
            pass
        print(f"\n[Monitor] Total loops: {loop_count}")
        print("[Monitor] Shutdown complete.")


if __name__ == "__main__":
    main()
