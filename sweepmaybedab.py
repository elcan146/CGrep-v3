import numpy as np
from rtlsdr import RtlSdr

# Standard VHF Band III DAB+ Multiplex Frequencies (EU/Asia)
# You can expand this from 5A (174.928) all the way to 13F (239.200)
dab_channels = {
    "11A": 216.928, "11B": 218.640, "11C": 220.352, "11D": 222.064,
    "12A": 223.936, "12B": 225.648, "12C": 227.360, "12D": 229.072,
    "13A": 230.784, "13B": 232.496, "13C": 234.208, "13D": 235.920
}

sdr = RtlSdr()
sdr.sample_rate = 2.4e6    
sdr.err_ppm = 0            
sdr.gain = 49.6            

# Offset to push the DC spike outside of the 1.536 MHz digital block
offset_hz = 333e3 

try:
    print(f"{'Block':<5} | {'Freq (MHz)':<10} | {'Power':<6} | {'Floor':<6} | {'SNR':<4}")
    print("-" * 45)
    
    for block, freq in dab_channels.items():
        target_hz = freq * 1e6
        sdr.center_freq = target_hz - offset_hz
        
        # Deep flush
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
        
        # MASSIVE MASK: Target the full 1.536 MHz DAB+ bandwidth
        # 768 kHz on either side of the center frequency
       # 1. Target the full 1.536 MHz DAB+ bandwidth
        target_mask = (actual_freqs >= target_hz - 768e3) & (actual_freqs <= target_hz + 768e3)
        
        # 2. INVERT THE MASK: Look only at the empty space outside the DAB block
        noise_mask = ~target_mask
        
        # 3. Calculate Power using ONLY the target area
        block_power_dbfs = np.mean(smoothed_spectrum[target_mask])
        
        # 4. Calculate Floor using ONLY the empty edge space
        floor_dbfs = np.median(smoothed_spectrum[noise_mask]) 
        
        snr = block_power_dbfs - floor_dbfs
            
        print(f"{block:<5} | {freq:<10.3f} | {block_power_dbfs:<6.0f} | {floor_dbfs:<6.0f} | {snr:<4.0f}")

finally:
    sdr.close()