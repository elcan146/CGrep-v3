import sounddevice as sd
import numpy as np

# Generate a 440 Hz tone for 1 second
sample_rate = 48000
time = np.linspace(0, 1, sample_rate, False)
tone = np.sin(440 * time * 2 * np.pi) * 0.5  # 0.5 is half volume

print("Playing a 1-second test tone...")
sd.play(tone, sample_rate)
sd.wait()
print("Test complete.")
#c