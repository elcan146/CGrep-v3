import numpy as np
from rtlsdr import RtlSdr

frequencies_mhz = np.round(np.arange(88.7, 108.1, 0.1), 1)

sdr = RtlSdr()

sdr.sample_rate = 2.4e6    
sdr.err_ppm = 0            
sdr.gain = 49.6            

# 1. Use an asymmetric offset to prevent IQ mirror aliasing
offset_hz = 333e3 

try:
    print(f"{'FM':<7} | {'Peak':<6} | {'Floor':<6} | {'SNR':<4}")
    print("-" * 35)
    
    for freq in frequencies_mhz:
        target_hz = freq * 1e6
        sdr.center_freq = target_hz - offset_hz
        
        # 2. The Deep Flush: Read and discard 3 chunks to guarantee fresh hardware data
        # 1. Deep Flush remains the same
        for _ in range(3):
            _ = sdr.read_samples(256 * 1024)
            
        # 2. Grab FEWER samples to match SDR# visual processing gain
        samples = sdr.read_samples(32768) 
        
        samples = samples - np.mean(samples)
        
        window = np.blackman(len(samples))
        windowed_samples = samples * window
        
        fft_data = np.fft.fftshift(np.fft.fft(windowed_samples))
        fft_mag = np.abs(fft_data) / len(samples)
        dbfs_spectrum = 20 * np.log10(fft_mag + 1e-12)
        
        # 3. Apply a moving average filter to smooth the static
        # This flattens the noise floor so np.max() doesn't grab a false peak
        kernel_size = 50 
        kernel = np.ones(kernel_size) / kernel_size
        smoothed_spectrum = np.convolve(dbfs_spectrum, kernel, mode='same')
        
        freqs = np.fft.fftshift(np.fft.fftfreq(len(samples), 1/sdr.sample_rate))
        actual_freqs = sdr.center_freq + freqs
        
        target_mask = (actual_freqs >= target_hz - 110e3) & (actual_freqs <= target_hz + 110e3)
        
        # 4. Measure from the SMOOTHED spectrum
        peak_dbfs = np.max(smoothed_spectrum[target_mask])
        floor_dbfs = np.median(smoothed_spectrum) 
        
        snr = peak_dbfs - floor_dbfs
            
        print(f"{freq:<7.1f} | {peak_dbfs:<6.0f} | {floor_dbfs:<6.0f} | {snr:<4.0f}")

finally:
    sdr.close()