"""
dab_scan.py — Scan all DAB+ Band III channels
==============================================
Scans all standard DAB+ multiplex frequencies to find active channels.
"""

import os
import numpy as np
from rtlsdr import RtlSdr

# Full DAB+ Band III channel list (EU/Asia standard)
DAB_CHANNELS = {
    "5A": 174.928, "5B": 176.640, "5C": 178.352, "5D": 180.064,
    "6A": 181.936, "6B": 183.648, "6C": 185.360, "6D": 187.072,
    "7A": 188.928, "7B": 190.640, "7C": 192.352, "7D": 194.064,
    "8A": 195.936, "8B": 197.648, "8C": 199.360, "8D": 201.072,
    "9A": 202.928, "9B": 204.640, "9C": 206.352, "9D": 208.064,
    "10A": 209.936, "10B": 211.648, "10C": 213.360, "10D": 215.072,
    "11A": 216.928, "11B": 218.640, "11C": 220.352, "11D": 222.064,
    "12A": 223.936, "12B": 225.648, "12C": 227.360, "12D": 229.072,
    "13A": 230.784, "13B": 232.496, "13C": 234.208, "13D": 235.920,
    "13E": 237.488, "13F": 239.200
}

SDR_GAIN = float(os.getenv("SDR_GAIN", "49.6"))
SNR_THRESHOLD = float(os.getenv("SNR_THRESHOLD", "6.0"))

sdr = RtlSdr()
sdr.sample_rate = 2.4e6
sdr.err_ppm = 0
sdr.gain = SDR_GAIN

offset_hz = 333e3

print(f"Scanning {len(DAB_CHANNELS)} DAB+ channels...")
print(f"Gain: {sdr.gain} dB | SNR threshold: {SNR_THRESHOLD} dB")
print()
print(f"{'Block':<5} | {'Freq (MHz)':<10} | {'Power':<8} | {'Floor':<8} | {'SNR':<6} | {'Status':<6}")
print("-" * 60)

found = []

try:
    for block, freq in sorted(DAB_CHANNELS.items()):
        target_hz = freq * 1e6
        sdr.center_freq = target_hz - offset_hz
        
        # Flush and stabilize
        for _ in range(3):
            _ = sdr.read_samples(256 * 1024)
        
        samples = sdr.read_samples(8192)
        samples = samples - np.mean(samples)
        
        window = np.blackman(len(samples))
        fft = np.fft.fftshift(np.fft.fft(samples * window))
        mag = np.abs(fft) / len(samples)
        spectrum = 20 * np.log10(mag + 1e-12) + 41.0
        
        # Smooth
        kernel = np.ones(50) / 50
        smoothed = np.convolve(spectrum, kernel, mode='same')
        
        freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/sdr.sample_rate))
        actual_freqs = sdr.center_freq + freqs
        
        # DAB+ 1.536 MHz bandwidth analysis
        half_bw = 768e3
        signal_mask = (actual_freqs >= target_hz - half_bw) & (actual_freqs <= target_hz + half_bw)
        noise_mask = ~signal_mask
        
        power = np.mean(smoothed[signal_mask])
        floor = np.median(smoothed[noise_mask])
        snr = power - floor
        status = "ACTIVE" if snr > SNR_THRESHOLD else "-"
        
        print(f"{block:<5} | {freq:<10.3f} | {power:<8.1f} | {floor:<8.1f} | {snr:<6.1f} | {status:<6}")
        
        if snr > SNR_THRESHOLD:
            found.append((block, freq, snr))

finally:
    sdr.close()

print()
if found:
    print(f"Found {len(found)} active DAB+ multiplex(es):")
    for block, freq, snr in found:
        print(f"  {block}: {freq} MHz (SNR: {snr:.1f} dB)")
else:
    print("No active DAB+ signals found.")
    print("\nPossible reasons:")
    print("  - No DAB+ coverage in your area")
    print("  - Antenna not suitable for VHF Band III (174-240 MHz)")
    print("  - Try adjusting antenna position or gain setting")
