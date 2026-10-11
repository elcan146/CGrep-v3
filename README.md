# CGrep — SDR Radio Monitoring Suite

A collection of Python and React applications for real-time FM radio monitoring, spectrum analysis, and tactical visualization using RTL-SDR hardware.

---

## Overview

This repository contains a full-stack SDR (Software Defined Radio) monitoring system:

- **Python backends** that interface with RTL-SDR dongles to stream IQ samples, perform DSP (FFT, WFM demodulation), and output audio.
- **React frontend** that provides a 3D tactical dashboard with real-time spectrum visualization, geographic situational awareness, and SIGINT-style analytics.

---

## Repository Structure

| File / Directory | Description |
|------------------|-------------|
| `spectrum_core.py` | Full-featured FM Radio Monitoring GUI (SDR# style). PyQt6 + pyqtgraph. Streams IQ, computes FFT spectrum & waterfall, performs WFM demodulation, and outputs audio via `sounddevice`. |
| `monitor.py` | Standalone spectrum monitor with click-to-tune, frequency entry, peak-hold, and mouse-wheel zoom. Auto-detects RTL-SDR or falls back to simulated RF. |
| `dashboard.py` | Region SDR Radio Network demo. Uses `rtl_fm` via pipe for zero-latency audio and simulates known FM stations with metadata. |
| `server.py` | FastAPI WebSocket server that streams simulated (or real) FFT data to the React dashboard at 30 FPS. Optional desktop window via `webview`. |
| `calculate.py` | Shared DSP utilities (FFT, demodulation helpers) used by the GUI apps. |
| `sweep20.py` | Frequency sweep utility for RTL-SDR. |
| `wp.py` | Waypoint / signal logging utility. |
| `generate_pdf.py` | Report generator for captured signals. |
| `generate_mask.py` | Utility for generating geographic masks. |
| `audiotest.py` | Audio pipeline test script. |
| `test.py` | General test / validation script. |
| `sdr-dashboard/` | React + Vite + Three.js tactical dashboard. |

---

## Python Applications

### Requirements

- Python 3.10+
- RTL-SDR dongle & drivers ([osmocom RTL-SDR](https://osmocom.org/projects/rtl-sdr/wiki))

### Install Dependencies

```bash
pip install numpy scipy pyqtgraph PyQt6 sounddevice pyrtlsdr
# Optional: for the web server
pip install fastapi uvicorn
```

**Platform-specific RTL-SDR drivers:**

- **Linux:** `sudo apt install rtl-sdr`
- **macOS:** `brew install librtlsdr`
- **Windows:** Download from [osmocom.org](https://osmocom.org/projects/rtl-sdr/wiki) and add to `PATH`.

### Running the Apps

```bash
# Full GUI with spectrum + waterfall + audio
python spectrum_core.py

# Lightweight spectrum monitor
python monitor.py

# Region radio network demo
python dashboard.py

# WebSocket server for the React dashboard
python server.py
```

---

## React Dashboard

A 3D tactical situational-awareness interface built with **React**, **Vite**, **Three.js**, and **React Three Fiber**. Features a glassmorphism UI, real-time spectrum panels, and geographic visualization centered on Region.

### Tech Stack

- React 19 + Vite
- `@react-three/fiber` & `@react-three/drei` — 3D scene management
- `@react-three/postprocessing` — Bloom, Vignette effects
- Custom tactical design tokens (glassmorphism, glitch effects, mono/digit fonts)

### Setup

```bash
cd sdr-dashboard
npm install
npm run dev
```

The dev server starts on `http://localhost:5173` by default. Ensure `server.py` is running to feed live spectrum data over the `/ws/sdr` WebSocket.

---

## Architecture

```
┌─────────────┐     IQ samples      ┌─────────────┐
│  RTL-SDR    │ ──────────────────> │  SdrWorker  │
│   Dongle    │                     │  (QThread)  │
└─────────────┘                     └──────┬──────┘
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    │                      │                      │
                    ▼                      ▼                      ▼
              ┌─────────┐           ┌────────────┐          ┌───────────┐
              │   FFT   │           │ WFM Demod  │          │  Waterfall│
              │ (numpy) │           │ + Decimate │          │  (pyqtgr) │
              └────┬────┘           └─────┬──────┘          └───────────┘
                   │                        │
                   ▼                        ▼
            ┌──────────┐           ┌─────────────┐
            │  GUI Plot  │           │ AudioWorker │ ──> sounddevice
            │(pyqtgraph)│           │  (callback) │      OutputStream
            └──────────┘           └─────────────┘
                   │
                   ▼ WebSocket
            ┌──────────────┐
            │ React Dashboard│
            │  (3D / SIGINT) │
            └──────────────┘
```

---

## Key Features

- **Real-time spectrum & waterfall** with Blackman-Harris windowed FFT
- **Wide-FM demodulation** with resampling and audio playback
- **Click-to-tune** on the spectrum display
- **Peak-hold trace** and mouse-wheel zoom
- **Auto RTL-SDR detection** with graceful fallback to simulated signals
- **FastAPI WebSocket bridge** for browser-based dashboards
- **3D tactical React UI** with bloom post-processing and glassmorphism

---

## License

This project is provided as-is for educational and experimental radio monitoring purposes.
