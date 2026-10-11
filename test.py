import numpy as np
import matplotlib.pyplot as plt
from rtlsdr import RtlSdr

def main():
    sdr = RtlSdr()

    # Hardware setup
    sdr.sample_rate = 2.048e6
    sdr.center_freq = 99.5e6  # Tune to an active FM station in your area
    sdr.gain = 'auto'

    print("Reading samples from the ether...")
    # Read a good chunk of samples for a smooth graph
    samples = sdr.read_samples(256 * 1024)
    sdr.close()

    # Plotting the Power Spectral Density (PSD)
    print("Plotting spectrum...")
    plt.figure(figsize=(10, 6))
    plt.psd(samples, NFFT=1024, Fs=sdr.sample_rate / 1e6, Fc=sdr.center_freq / 1e6, color='crimson')
    
    plt.title('Live Frequency Spectrum')
    plt.xlabel('Frequency (MHz)')
    plt.ylabel('Relative Power (dB)')
    plt.grid(True)
    plt.show()

if __name__ == '__main__':
    main()