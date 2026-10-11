"""
FM Radio TUI - pysdrplay Demo
=============================
A stereo FM radio receiver showcasing pysdrplay.

Controls:
  UP/DOWN or +/-     : Fine tune (0.1 MHz)
  PAGE UP/PAGE DOWN  : Coarse tune (1 MHz)
  A/B                : Select tuner (RSPduo only)
  G                  : Cycle gain (Low/Med/High/AGC)
  S                  : Toggle stereo/mono
  F                  : Toggle FM notch filter
  D                  : Toggle DAB notch filter
  V/v                : Volume up/down
  M                  : Mute/unmute
  R                  : Refresh display
  Q                  : Quit
"""

import sys
import time
import threading
from collections import deque

import numpy as np
import scipy.signal as sig
import sounddevice as sd
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pysdrplay import SDRPlayRadio, TunerSelect, Bw

# Device type ID for RSPduo (has dual tuners)
RSPDUO_ID = 3

# FM Broadcast Band Constants
FM_BAND_LOW = 87.5  # MHz - US/Europe FM band lower limit
FM_BAND_HIGH = 108.0  # MHz - US/Europe FM band upper limit
FM_DEVIATION = 75000  # Hz - Maximum frequency deviation for FM broadcast
DEEMPHASIS_TAU = 75e-6  # seconds - North America de-emphasis (50e-6 for Europe)


# ============================================================================
# STEREO FM DEMODULATOR
# ============================================================================


class StereoFMDemodulator:
    """
    FM Broadcast stereo demodulator following the CCIR/FCC multiplex standard.

    Signal Chain:
        RF IQ (2 MHz) -> Decimate (250 kHz) -> FM Demod -> MPX Signal

    MPX (Multiplex) Signal contains:
        - L+R mono audio: 0-15 kHz baseband
        - 19 kHz pilot tone: stereo broadcast indicator
        - L-R stereo diff: 23-53 kHz (DSB-SC modulated on 38 kHz subcarrier)

    Stereo Recovery:
        1. Detect 19 kHz pilot (indicates stereo broadcast)
        2. Create square wave from pilot zero-crossings for mixing
        3. Multiply L-R band by carrier to demodulate
        4. Matrix decode: L = (L+R) + (L-R), R = (L+R) - (L-R)
        5. Apply 75us de-emphasis (compensates FM pre-emphasis)
        6. Resample to audio output rate

    For mono-only operation, set stereo_enabled=False.
    """

    def __init__(self, sample_rate=2_000_000, audio_rate=48000):
        self.sample_rate = sample_rate
        self.audio_rate = audio_rate
        self.stereo_enabled = True

        # Accumulate IQ samples before processing
        self.iq_buffer = []
        self.iq_buffer_samples = 0
        self.process_threshold = 100000  # ~50ms of IQ data at 2 MHz

        # Intermediate rate for FM demod (decimate 8x: 2 MHz -> 250 kHz)
        self.fs1 = 250000
        self.dec1 = sample_rate // self.fs1

        # Signal strength tracking
        self.signal_level = 0.0

        # FIR filters for signal processing
        # lpf1: Anti-alias before decimation (120 kHz cutoff)
        self.lpf1 = sig.firwin(63, 120000, fs=sample_rate)
        # lpf_mono: Extract L+R mono signal (0-15 kHz)
        self.lpf_mono = sig.firwin(63, 15000, fs=self.fs1)
        # bpf_pilot: Isolate 19 kHz pilot tone (18-20 kHz narrow band)
        self.bpf_pilot = sig.firwin(63, [18000, 20000], pass_zero=False, fs=self.fs1)
        # bpf_stereo: Extract L-R stereo difference (23-53 kHz DSB-SC band)
        self.bpf_stereo = sig.firwin(63, [23000, 53000], pass_zero=False, fs=self.fs1)
        # lpf_audio: Final audio lowpass after stereo demod
        self.lpf_audio = sig.firwin(31, 15000, fs=self.fs1)

        # De-emphasis
        self.deemph_alpha = 1.0 / (1.0 + self.fs1 * DEEMPHASIS_TAU)

        # State for continuity
        self.prev_sample = 1 + 0j
        self.deemph_state_l = 0.0
        self.deemph_state_r = 0.0
        self.pilot_detected = False

        # Filter states
        self.lpf1_state = sig.lfilter_zi(self.lpf1, 1).astype(np.complex128) * 0
        self.lpf_mono_state = sig.lfilter_zi(self.lpf_mono, 1) * 0
        self.bpf_pilot_state = sig.lfilter_zi(self.bpf_pilot, 1) * 0
        self.bpf_stereo_state = sig.lfilter_zi(self.bpf_stereo, 1) * 0

    def add_samples(self, xi, xq, num_samples):
        """Add samples to buffer and calculate signal strength."""
        if num_samples == 0:
            return

        i_samples = np.ctypeslib.as_array(xi, shape=(num_samples,))
        q_samples = np.ctypeslib.as_array(xq, shape=(num_samples,))
        iq = i_samples.astype(np.float32) + 1j * q_samples.astype(np.float32)

        # Update signal level (EMA with faster response for UI)
        self.signal_level = 0.7 * self.signal_level + 0.3 * np.mean(np.abs(iq))

        self.iq_buffer.append(iq.copy())
        self.iq_buffer_samples += num_samples

    def process_if_ready(self):
        """Process buffer if ready. Returns (left, right) stereo audio or None."""
        if self.iq_buffer_samples < self.process_threshold:
            return None

        # Combine buffer
        iq = np.concatenate(self.iq_buffer)
        self.iq_buffer = []
        self.iq_buffer_samples = 0

        # Remove DC and convert to float64 for processing
        iq = (iq - np.mean(iq)).astype(np.complex128)

        # First decimation
        iq_filt, self.lpf1_state = sig.lfilter(self.lpf1, 1, iq, zi=self.lpf1_state)
        iq_dec = iq_filt[:: self.dec1]

        # Amplitude limiter
        iq_limited = iq_dec / (np.abs(iq_dec) + 1e-10)

        # FM demodulation
        samples = np.concatenate([[self.prev_sample], iq_limited])
        self.prev_sample = iq_limited[-1]
        baseband = np.angle(samples[1:] * np.conj(samples[:-1]))

        # Scale baseband
        baseband = baseband * (self.fs1 / (2 * np.pi * FM_DEVIATION))

        # Extract mono (L+R)
        mono, self.lpf_mono_state = sig.lfilter(
            self.lpf_mono, 1, baseband, zi=self.lpf_mono_state
        )

        if self.stereo_enabled:
            # Extract pilot tone (19 kHz)
            pilot, self.bpf_pilot_state = sig.lfilter(
                self.bpf_pilot, 1, baseband, zi=self.bpf_pilot_state
            )

            # Detect pilot presence
            pilot_power = np.mean(pilot**2)
            self.pilot_detected = pilot_power > 0.0005

            if self.pilot_detected:
                # Simple stereo decoding: square the pilot to get 38kHz
                # This is faster than Hilbert transform
                carrier_38k = np.sign(pilot)  # Square wave at 38kHz

                # Extract stereo difference signal
                stereo_diff, self.bpf_stereo_state = sig.lfilter(
                    self.bpf_stereo, 1, baseband, zi=self.bpf_stereo_state
                )

                # Demodulate stereo
                stereo_demod = stereo_diff * carrier_38k * 2

                # Lowpass filter
                diff = sig.lfilter(self.lpf_audio, 1, stereo_demod)

                # Reconstruct L and R
                left = mono + diff
                right = mono - diff
            else:
                left = mono
                right = mono
        else:
            left = mono
            right = mono
            self.pilot_detected = False

        # Vectorized de-emphasis (much faster than Python loop)
        b_deemph = np.array([self.deemph_alpha])
        a_deemph = np.array([1.0, -(1.0 - self.deemph_alpha)])
        left_out, zl = sig.lfilter(b_deemph, a_deemph, left, zi=[self.deemph_state_l])
        right_out, zr = sig.lfilter(b_deemph, a_deemph, right, zi=[self.deemph_state_r])
        self.deemph_state_l = zl[0]
        self.deemph_state_r = zr[0]

        # Resample to audio rate
        left_audio = sig.resample_poly(left_out, self.audio_rate, self.fs1)
        right_audio = sig.resample_poly(right_out, self.audio_rate, self.fs1)

        # Normalize
        peak = max(np.max(np.abs(left_audio)), np.max(np.abs(right_audio)), 0.01)
        left_audio = (left_audio / peak * 0.8).astype(np.float32)
        right_audio = (right_audio / peak * 0.8).astype(np.float32)

        return left_audio, right_audio


# ============================================================================
# FM RADIO APPLICATION
# ============================================================================


class FMRadio:
    """FM Radio with enhanced TUI interface."""

    GAIN_MODES = ["AGC", "Low", "Med", "High"]
    GAIN_SETTINGS = {
        "Low": (0, 40),  # LNA state 0, GR 40
        "Med": (3, 35),  # LNA state 3, GR 35
        "High": (6, 25),  # LNA state 6, GR 25
    }

    def __init__(self):
        self.frequency = 106.5
        self.volume = 0.8
        self.muted = False
        self.running = False
        self.sample_rate = 2_000_000
        self.audio_rate = 48000

        # Device info
        self.device_type = "Unknown"
        self.device_serial = ""
        self.hw_version = 0
        self.available_tuners = ["A"]  # Default single tuner
        self.current_tuner = "A"

        # Settings
        self.gain_mode = "Med"
        self.fm_notch = False
        self.dab_notch = False
        self.stereo_enabled = True

        # Components
        self.radio = None
        self.demodulator = None

        # Use deque for O(1) append/popleft instead of array concat
        self.audio_chunks_l = deque()
        self.audio_chunks_r = deque()
        self.audio_total_samples = 0
        self.audio_lock = threading.Lock()
        self.audio_ready = False

    def _detect_device(self):
        """Detect device type and capabilities."""
        self.radio = SDRPlayRadio()
        devices = self.radio.devices

        if not devices:
            return False

        self.device_serial = devices[0]

        # Select device temporarily to get info
        # Use Tuner A first for detection
        self.radio.select_device(self.device_serial, tuner=TunerSelect.A)
        info = self.radio.get_device_info()

        self.device_type = info["type"]
        self.hw_version = info["hw_version"]

        # Determine available tuners based on device type
        if self.hw_version == RSPDUO_ID:
            self.available_tuners = ["A", "B"]
            if self.current_tuner not in self.available_tuners:
                self.current_tuner = "B"  # Default to B for RSPduo
        else:
            self.available_tuners = ["A"]
            self.current_tuner = "A"

        # Release and reselect with proper tuner
        self.radio.release_device()
        return True

    def start(self):
        """Initialize and start the radio."""
        print("\nInitializing FM Radio...")

        if not self._detect_device():
            print("ERROR: No SDRplay device found!")
            return False

        print(f"Found: {self.device_type} (Serial: {self.device_serial})")
        print(f"Available tuners: {', '.join(self.available_tuners)}")

        # Select the appropriate tuner
        tuner = TunerSelect.B if self.current_tuner == "B" else TunerSelect.A
        self.radio.select_device(self.device_serial, tuner=tuner)

        # Configure device
        self.radio.set_sample_rate(self.sample_rate)
        self.radio.set_bandwidth(Bw.BW_1_536)
        self.radio.set_frequency(int(self.frequency * 1_000_000))

        # Apply initial settings
        self._apply_gain_mode()
        self._apply_notch_filters()

        # Create demodulator
        self.demodulator = StereoFMDemodulator(self.sample_rate, self.audio_rate)
        self.demodulator.stereo_enabled = self.stereo_enabled

        # Initialize audio buffers (using deque of chunks)
        self.audio_chunks_l = deque()
        self.audio_chunks_r = deque()
        self.audio_total_samples = 0
        self.audio_ready = False
        self.audio_playback_buffer_l = np.array([], dtype=np.float32)
        self.audio_playback_buffer_r = np.array([], dtype=np.float32)

        self.running = True

        # Start stereo audio output
        self.audio_stream = sd.OutputStream(
            samplerate=self.audio_rate,
            channels=2,
            dtype=np.float32,
            blocksize=2048,
            callback=self._audio_callback,
            latency="high",
        )
        self.audio_stream.start()

        self.radio.start(self._sdr_callback)

        print("Radio started!")
        return True

    def _apply_gain_mode(self):
        """Apply the current gain mode."""
        if self.gain_mode == "AGC":
            self.radio.set_agc(True, setpoint_dbfs=-30)
        else:
            self.radio.set_agc(False)
            lna, gr = self.GAIN_SETTINGS[self.gain_mode]
            self.radio.set_gain(lna, gr)

    def _apply_notch_filters(self):
        """Apply notch filter settings."""
        try:
            self.radio.set_notch_filter(self.fm_notch)
        except Exception:
            pass  # Not all devices support this
        try:
            self.radio.set_dab_notch(self.dab_notch)
        except Exception:
            pass  # Not all devices support this

    def _sdr_callback(self, xi, xq, params, num_samples, reset, ctx):
        """Callback for SDR samples - process directly."""
        if not self.running or num_samples == 0:
            return

        try:
            # Add samples to demodulator
            self.demodulator.add_samples(xi, xq, num_samples)

            # Process if ready
            result = self.demodulator.process_if_ready()

            if result is not None:
                left, right = result

                if self.muted:
                    left = np.zeros_like(left)
                    right = np.zeros_like(right)
                else:
                    left = left * self.volume
                    right = right * self.volume

                with self.audio_lock:
                    # Append chunks to deque (O(1) operation)
                    self.audio_chunks_l.append(left)
                    self.audio_chunks_r.append(right)
                    self.audio_total_samples += len(left)

                    # Limit total buffered samples
                    max_buffer = self.audio_rate * 2
                    while self.audio_total_samples > max_buffer and self.audio_chunks_l:
                        removed_l = self.audio_chunks_l.popleft()
                        self.audio_chunks_r.popleft()  # Keep deques synchronized
                        self.audio_total_samples -= len(removed_l)

                    # Wait for 0.5 second of audio buffered before starting playback
                    if (
                        not self.audio_ready
                        and self.audio_total_samples > self.audio_rate // 2
                    ):
                        self.audio_ready = True

        except Exception:
            pass

    def _audio_callback(self, outdata, frames, time_info, status):
        """Callback for stereo audio output."""
        try:
            with self.audio_lock:
                if not self.audio_ready:
                    outdata.fill(0)
                    return

                # Collect enough samples from chunks
                while (
                    len(self.audio_playback_buffer_l) < frames and self.audio_chunks_l
                ):
                    chunk_l = self.audio_chunks_l.popleft()
                    chunk_r = self.audio_chunks_r.popleft()
                    self.audio_total_samples -= len(chunk_l)
                    self.audio_playback_buffer_l = np.concatenate(
                        [self.audio_playback_buffer_l, chunk_l]
                    )
                    self.audio_playback_buffer_r = np.concatenate(
                        [self.audio_playback_buffer_r, chunk_r]
                    )

                if len(self.audio_playback_buffer_l) >= frames:
                    outdata[:, 0] = self.audio_playback_buffer_l[:frames]
                    outdata[:, 1] = self.audio_playback_buffer_r[:frames]
                    self.audio_playback_buffer_l = self.audio_playback_buffer_l[frames:]
                    self.audio_playback_buffer_r = self.audio_playback_buffer_r[frames:]
                elif len(self.audio_playback_buffer_l) > 0:
                    n = len(self.audio_playback_buffer_l)
                    outdata[:n, 0] = self.audio_playback_buffer_l
                    outdata[:n, 1] = self.audio_playback_buffer_r
                    outdata[n:, :] = 0
                    self.audio_playback_buffer_l = np.array([], dtype=np.float32)
                    self.audio_playback_buffer_r = np.array([], dtype=np.float32)
                else:
                    outdata.fill(0)
        except Exception:
            outdata.fill(0)

    def set_frequency(self, freq_mhz):
        """Set tuner frequency."""
        freq_mhz = max(FM_BAND_LOW, min(FM_BAND_HIGH, freq_mhz))
        self.frequency = freq_mhz
        if self.radio:
            self.radio.set_frequency(int(freq_mhz * 1_000_000))

    def tune_up(self, step=0.1):
        self.set_frequency(round(self.frequency + step, 1))

    def tune_down(self, step=0.1):
        self.set_frequency(round(self.frequency - step, 1))

    def set_volume(self, vol):
        self.volume = max(0.0, min(1.0, vol))

    def toggle_mute(self):
        self.muted = not self.muted

    def cycle_gain(self):
        """Cycle through gain modes."""
        idx = self.GAIN_MODES.index(self.gain_mode)
        self.gain_mode = self.GAIN_MODES[(idx + 1) % len(self.GAIN_MODES)]
        if self.radio:
            self._apply_gain_mode()

    def toggle_stereo(self):
        """Toggle stereo/mono mode."""
        self.stereo_enabled = not self.stereo_enabled
        if self.demodulator:
            self.demodulator.stereo_enabled = self.stereo_enabled

    def toggle_fm_notch(self):
        """Toggle FM notch filter."""
        self.fm_notch = not self.fm_notch
        if self.radio:
            try:
                self.radio.set_notch_filter(self.fm_notch)
            except Exception:
                pass

    def toggle_dab_notch(self):
        """Toggle DAB notch filter."""
        self.dab_notch = not self.dab_notch
        if self.radio:
            try:
                self.radio.set_dab_notch(self.dab_notch)
            except Exception:
                pass

    def select_tuner(self, tuner):
        """Select tuner (A or B) - requires restart."""
        if tuner not in self.available_tuners:
            return False
        if tuner == self.current_tuner:
            return True

        self.current_tuner = tuner

        # Need to restart with new tuner
        if self.running:
            was_running = True
            self.stop()
        else:
            was_running = False

        if was_running:
            time.sleep(0.5)
            self.start()

        return True

    def get_signal_strength(self):
        """Get signal strength as percentage (0-100) using dB scaling."""
        if self.demodulator:
            level = self.demodulator.signal_level
            if level < 1:
                return 0
            # Logarithmic (dB) scaling: 20*log10(level/100) maps to ~0-50dB range
            # Noise floor assumed ~100, strong signal ~30000+
            db = 20 * np.log10(level / 100)
            return min(100, max(0, int(db * 2)))  # 0-50dB -> 0-100%
        return 0

    def is_stereo(self):
        """Check if stereo signal is detected."""
        if self.demodulator and self.stereo_enabled:
            return self.demodulator.pilot_detected
        return False

    def stop(self):
        self.running = False

        if self.radio:
            try:
                self.radio.stop()
                self.radio.release_device()
                self.radio.close()
            except Exception:
                pass
            self.radio = None

        if hasattr(self, "audio_stream"):
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass


# ============================================================================
# TUI INTERFACE (Rich-based with screenshot export)
# ============================================================================

# Console with recording enabled for screenshot export
_console = None


def get_console():
    """Lazy initialization of Rich console."""
    global _console
    if _console is None:
        _console = Console(record=True)
    return _console


def make_signal_bar(value: int, width: int = 20) -> Text:
    """Create a colored signal strength bar."""
    filled = int(value / 100 * width)
    text = Text()
    color = "green" if value >= 60 else "yellow" if value >= 30 else "red"
    text.append("█" * filled, style=color)
    text.append("░" * (width - filled), style="dim")
    return text


def make_volume_bar(value: int, width: int = 20) -> Text:
    """Create a volume bar."""
    filled = int(value / 100 * width)
    text = Text()
    text.append("█" * filled, style="blue")
    text.append("░" * (width - filled), style="dim")
    return text


def build_display(radio) -> Panel:
    """Build the radio display as a Rich Panel."""
    freq = radio.frequency
    vol = int(radio.volume * 100)
    signal = radio.get_signal_strength()

    # Signal description
    if signal < 20:
        sig_desc = "Weak"
    elif signal < 50:
        sig_desc = "Fair"
    elif signal < 75:
        sig_desc = "Good"
    else:
        sig_desc = "Strong"

    # Build main table
    table = Table.grid(padding=(0, 2))
    table.add_column("Label", style="bold", width=12)
    table.add_column("Value")

    # Device info
    table.add_row("Device:", f"{radio.device_type} ({radio.device_serial})")

    if len(radio.available_tuners) > 1:
        tuner_text = Text()
        for t in radio.available_tuners:
            if t == radio.current_tuner:
                tuner_text.append(f"[{t}]", style="bold green")
            else:
                tuner_text.append(f" {t} ", style="dim")
            tuner_text.append("  ")
        table.add_row("Tuner:", tuner_text)

    table.add_row("", "")  # Spacer

    # Frequency with dial
    freq_text = Text(f"{freq:6.1f} MHz", style="bold cyan")
    table.add_row("Frequency:", freq_text)

    # Frequency dial (width=46 to match original)
    dial_width = 46
    pos = int((freq - FM_BAND_LOW) / (FM_BAND_HIGH - FM_BAND_LOW) * dial_width)
    dial = Text()
    dial.append("│" + "─" * pos + "▼" + "─" * (dial_width - pos) + "│", style="dim")
    table.add_row("", dial)
    # Dial labels with proper spacing
    dial_label = Text()
    dial_label.append(f"{FM_BAND_LOW}", style="dim")
    dial_label.append(" " * (dial_width - 4), style="dim")  # Spacing between labels
    dial_label.append(f"{FM_BAND_HIGH}", style="dim")
    table.add_row("", dial_label)

    table.add_row("", "")  # Spacer

    # Signal meter
    sig_bar = make_signal_bar(signal)
    sig_bar.append(f"  ({sig_desc})")
    table.add_row("Signal:", sig_bar)

    # Stereo indicator
    stereo_text = Text()
    if radio.is_stereo():
        stereo_text.append("STEREO", style="bold green")
    else:
        stereo_text.append("MONO", style="dim")
    stereo_text.append(f"  (Mode: {'Stereo' if radio.stereo_enabled else 'Mono'})")
    table.add_row("Reception:", stereo_text)

    table.add_row("", "")  # Spacer

    # Volume
    vol_bar = make_volume_bar(vol)
    vol_bar.append(f"  {vol}%")
    if radio.muted:
        vol_bar.append(" [MUTED]", style="bold red")
    table.add_row("Volume:", vol_bar)

    # Gain mode
    gain_text = Text()
    for g in radio.GAIN_MODES:
        if g == radio.gain_mode:
            gain_text.append(f"[{g}]", style="bold green")
        else:
            gain_text.append(f" {g} ", style="dim")
        gain_text.append("  ")
    table.add_row("Gain:", gain_text)

    # Filters
    filter_row = Text()
    filter_row.append("FM Notch: ")
    filter_row.append(
        "ON" if radio.fm_notch else "OFF", style="green" if radio.fm_notch else "dim"
    )
    filter_row.append("    DAB Notch: ")
    filter_row.append(
        "ON" if radio.dab_notch else "OFF", style="green" if radio.dab_notch else "dim"
    )
    table.add_row("Filters:", filter_row)

    table.add_row("", "")  # Spacer

    # Controls
    controls = Text()
    controls.append("↑/↓ +/-  Fine tune    PgUp/PgDn  Coarse tune\n")
    controls.append("G        Cycle gain\n")
    if len(radio.available_tuners) > 1:
        controls.append("A/B      Select tuner S          Stereo/Mono\n")
    else:
        controls.append("S        Stereo/Mono\n")
    controls.append("F        FM notch     D          DAB notch\n")
    controls.append("V/v      Volume       M          Mute\n")
    controls.append("X        Screenshot   R          Refresh      Q  Quit")
    table.add_row("Controls:", controls)

    return Panel(table, title="FM Radio - pysdrplay Demo", border_style="blue")


def export_screenshot(filename: str = None):
    """Export current console display as SVG for verification."""
    if filename is None:
        filename = f"fm_radio_{time.strftime('%Y%m%d_%H%M%S')}.svg"
    get_console().save_svg(filename, title="FM Radio")
    return filename


def run_tui(radio):
    """Run the TUI main loop with Rich Live display."""
    import msvcrt  # Windows keyboard input

    console = get_console()

    # Dispatch table for special keys (after \xe0 prefix)
    SPECIAL_KEYS = {
        b"H": lambda: radio.tune_up(0.1),  # Up arrow
        b"P": lambda: radio.tune_down(0.1),  # Down arrow
        b"I": lambda: radio.tune_up(1.0),  # Page Up
        b"Q": lambda: radio.tune_down(1.0),  # Page Down
    }

    # Dispatch table for regular keys
    KEY_ACTIONS = {
        b"+": lambda: radio.tune_up(0.1),
        b"=": lambda: radio.tune_up(0.1),
        b"-": lambda: radio.tune_down(0.1),
        b"_": lambda: radio.tune_down(0.1),
        b"V": lambda: radio.set_volume(radio.volume + 0.1),
        b"v": lambda: radio.set_volume(radio.volume - 0.1),
        b"m": radio.toggle_mute,
        b"M": radio.toggle_mute,
        b"g": radio.cycle_gain,
        b"G": radio.cycle_gain,
        b"s": radio.toggle_stereo,
        b"S": radio.toggle_stereo,
        b"f": radio.toggle_fm_notch,
        b"F": radio.toggle_fm_notch,
        b"d": radio.toggle_dab_notch,
        b"D": radio.toggle_dab_notch,
        b"r": lambda: None,  # Refresh only
        b"R": lambda: None,
        b"x": export_screenshot,  # Screenshot export
        b"X": export_screenshot,
        b"q": lambda: setattr(radio, "running", False),
        b"Q": lambda: setattr(radio, "running", False),
    }

    # Add tuner selection keys if available
    if "A" in radio.available_tuners:
        KEY_ACTIONS[b"a"] = KEY_ACTIONS[b"A"] = lambda: radio.select_tuner("A")
    if "B" in radio.available_tuners:
        KEY_ACTIONS[b"b"] = KEY_ACTIONS[b"B"] = lambda: radio.select_tuner("B")

    with Live(
        build_display(radio), console=console, refresh_per_second=4, screen=True
    ) as live:
        while radio.running:
            if msvcrt.kbhit():
                key = msvcrt.getch()
                action = None

                if key == b"\xe0":  # Special key prefix
                    action = SPECIAL_KEYS.get(msvcrt.getch())
                else:
                    action = KEY_ACTIONS.get(key)

                if action:
                    action()
                    live.update(build_display(radio))

            time.sleep(0.05)


def main():
    print(__doc__)
    radio = FMRadio()
    try:
        if radio.start():
            run_tui(radio)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down...")
        radio.stop()
        print("Goodbye!")


if __name__ == "__main__":
    main()
