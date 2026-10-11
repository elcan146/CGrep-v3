# pysdrplay

A Python wrapper for SDRplay software-defined radios.

## What you need

- An SDRplay radio (RSP1, RSP1A, RSP1B, RSP2, RSPduo, RSPdx, or RSPdxR2)
- An antenna connected to it
- SDRplay API 3.15 installed
- Python 3.10 or newer

## Install the SDRplay API first

The wrapper talks to the official SDRplay API, which talks to your hardware.
Without it, nothing works.

Download it here: https://www.sdrplay.com/api/

Pick your operating system and install version 3.15. Run the installer, then
come back.

## Install pysdrplay

```bash
pip install .
```

## Quick Start

pysdrplay gives you raw IQ samples from your SDRplay receiver. IQ samples are
complex numbers representing the radio signal—I (in-phase) and Q (quadrature)
are the real and imaginary parts.

Tune to a frequency, start streaming, and the radio calls your callback with
chunks of samples.

```python
import queue
import numpy as np
from pysdrplay import SDRPlayRadio, Bw

samples = queue.Queue()

def on_samples(xi, xq, params, num_samples, reset, ctx):
    # xi, xq are ctypes pointers to 16-bit signed integers
    # .copy() is required—the buffer gets reused after the callback returns
    i = np.ctypeslib.as_array(xi, shape=(num_samples,)).copy()
    q = np.ctypeslib.as_array(xq, shape=(num_samples,)).copy()
    samples.put(i + 1j * q)

with SDRPlayRadio() as radio:
    if not radio.devices:
        print("No SDRplay device found")
        exit(1)

    radio.select_device(radio.devices[0])
    radio.set_frequency(100_000_000)   # 100 MHz
    radio.set_sample_rate(2_000_000)   # 2 million samples per second
    radio.set_bandwidth(Bw.BW_1_536)   # filter bandwidth (less than sample rate)

    radio.start(on_samples)
    iq_data = [samples.get() for _ in range(100)]  # grab 100 chunks
    radio.stop()

all_samples = np.concatenate(iq_data)
print(f"Captured {len(all_samples)} samples")
```

The callback receives `xi` and `xq` (sample pointers), `num_samples` (chunk
size), `reset` (true if the stream restarted), and `ctx` (user context, always
None). The `params` argument has stream metadata and can usually be ignored.

One thing to watch: the callback runs on a background thread owned by the
SDRplay API. Keep your callback fast—copy the data into a queue and process it
elsewhere, or you'll drop samples.

## FM radio demo

The included `fm_radio.py` is a working stereo FM receiver with a terminal
interface. It shows how to use the wrapper for a real application.

```bash
pip install ".[demo]"
python fm_radio.py
```

Or if you installed the package:
```bash
fm-radio
```

Wait 2-3 seconds for audio to buffer, then you should hear your local FM
stations (88-108 MHz band).

### Controls

| Key | What it does |
|-----|--------------|
| UP/DOWN or +/- | Fine tune (0.1 MHz) |
| PAGE UP/PAGE DOWN | Coarse tune (1 MHz) |
| G | Cycle gain modes (AGC/Low/Med/High) |
| S | Toggle stereo/mono |
| V/v | Volume up/down |
| M | Mute |
| F | FM broadcast notch filter |
| D | DAB notch filter |
| A/B | Switch tuners (RSPduo only) |
| X | Save screenshot |
| Q | Quit |

## Troubleshooting

**No SDRplay device found**

Is your device plugged in? Is the API service running?

On Windows, open Services and look for "SDRplay API Service".

On Linux:
```bash
systemctl status sdrplay
sudo systemctl start sdrplay   # if it's not running
```

**API version mismatch**

Your installed API version doesn't match what this wrapper expects (3.15).
Download the correct version from the SDRplay website.

**Device in use**

Close SDRuno, CubicSDR, or any other program using the radio.

## What's in here

- `pysdrplay.py` - The wrapper. Handles all the low-level details so you
  don't have to.
- `fm_radio.py` - Stereo FM demodulator with a terminal UI. Shows how to use
  the wrapper for a real application.

## License

MIT
