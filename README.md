<div align="center">

<img src="logo.png" alt="CGrep logo" width="240">

# CGrep

### Real-Time SDR Radio Monitoring & SIGINT-Style Analysis Suite

*Capture. Demodulate. Visualize. Understand the spectrum.*

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Three.js](https://img.shields.io/badge/Three.js-000000?style=for-the-badge&logo=threedotjs&logoColor=white)](https://threejs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![RTL-SDR](https://img.shields.io/badge/RTL--SDR-Hardware-red?style=for-the-badge)](https://osmocom.org/projects/rtl-sdr/wiki)
[![nRSP-ST](https://img.shields.io/badge/SDRplay-nRSP--ST-blueviolet?style=for-the-badge)](https://www.sdrplay.com)
[![RSP1B](https://img.shields.io/badge/SDRplay-RSP1B-blueviolet?style=for-the-badge)](https://www.sdrplay.com)
[![MSI.SDR Pro](https://img.shields.io/badge/MSI.SDR-Pro-blueviolet?style=for-the-badge)]()
[![License](https://img.shields.io/badge/License-View--Only-orange?style=for-the-badge)](LICENSE)

</div>

---

## What is CGrep?

**CGrep** is a full-stack Software-Defined Radio monitoring platform. It interfaces with **RTL-SDR** hardware to sweep FM & DAB+ bands, demodulate broadcast audio in real time, detect and log signals, and presents everything through a **3D tactical web dashboard** — complete with geographic situational awareness, passkey-protected multi-user access, an AI operator assistant, and automated WhatsApp alerts.

It is not a toy waterfall viewer — it's a distributed RF monitoring network with collectors, a central PostgreSQL backend, cloud backups, and a command-center UI.

---

##  Feature Highlights

| | |
|:-:|---|
| 🎛️ | **SDR#-grade desktop GUI** — FFT spectrum, waterfall, click-to-tune, WFM demod, audio out (PyQt6 + pyqtgraph) |
| 📶 | **FM + DAB+ monitoring** — ensemble scanning (11B/12A), RDS decoding, multi-receiver support |
| 🗼 | **Remote collector network** — headless `sdr_collector.py` agents stream sweeps to a central server |
| 🌐 | **3D tactical dashboard** — React Three Fiber scene, glassmorphism HUD, live spectrum at 30 FPS over WebSocket |
| 🗺️ | **Geographic awareness** — region map overlay, population/coverage masks, peak-intensity terrain shader |
| 🤖 | **AI operator assistant** — local Ollama-powered chat embedded in the dashboard (fully self-hosted) |
| 🧠 | **ML threshold calibration** — unsupervised KDE + valley detection auto-tunes noise floor & signal thresholds per site |
| 🔐 | **Passwordless auth** — WebAuthn/passkeys, JWT sessions, RBAC roles & permissions |
| 📲 | **WhatsApp alerts** — self-hosted OpenWA / Green API notifications for signal events |
| ☁️ | **Cloud backups** — scheduled database exports to S3-compatible storage |
| 📄 | **Report generator** — one-click PDF reports of captured signal activity |
| 🌍 | **i18n** — full English / Azerbaijani UI translations |

---

## 🏗️ Architecture

```
        ┌──────────────┐   IQ samples   ┌───────────────────┐
        │   RTL-SDR    │ ─────────────► │   DSP pipeline    │
        │   Dongles    │                │  FFT · WFM · RDS  │
        └──────────────┘                └─────────┬─────────┘
                                                  │
              ┌───────────────────────────────────┼────────────────────┐
              │                                   │                    │
              ▼                                   ▼                    ▼
     ┌────────────────┐                 ┌──────────────────┐  ┌──────────────┐
     │  Desktop GUIs  │                 │  FastAPI server  │  │ Collectors   │
     │ PyQt6 + audio  │                 │  REST + WS :8000 │  │ (remote SDR) │
     └────────────────┘                 └────────┬─────────┘  └──────┬───────┘
                                                 │                   │
                        ┌────────────┬───────────┼───────────────────┘
                        ▼            ▼           ▼
                 ┌───────────┐ ┌───────────┐ ┌────────────────────┐
                 │ PostgreSQL│ │  Ollama   │ │ React dashboard    │
                 │  signals  │ │  AI chat  │ │ 3D scene · ECharts │
                 └───────────┘ └───────────┘ └────────────────────┘
```

---

## 🧰 Tech Stack

**Backend**

![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-013243?logo=numpy&logoColor=white)
![SciPy](https://img.shields.io/badge/SciPy-8CAAE6?logo=scipy&logoColor=white)
![PyQt6](https://img.shields.io/badge/PyQt6-41CD52?logo=qt&logoColor=white)

- `server.py` — FastAPI app: REST API, WebSocket streaming, WebAuthn auth, RBAC, chat, backups
- `sdr_collector.py`, `dual_site_monitor.py`, `regional_monitor.py` — headless collector agents
- `fm_dab_sweep.py`, `dab11b_monitor.py`, `dab12a_monitor.py` — FM + DAB+ band monitors
- `spectrum_core.py`, `monitor.py`, `lookforwaterfall.py`, `dashboard.py` — PyQt6 desktop apps

**Frontend** (`sdr-dashboard/`)

![React](https://img.shields.io/badge/React_19-61DAFB?logo=react&logoColor=black)
![Vite](https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white)
![Three.js](https://img.shields.io/badge/Three.js-black?logo=threedotjs&logoColor=white)
![Tailwind](https://img.shields.io/badge/Tailwind_4-38B2AC?logo=tailwindcss&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)

- React Three Fiber + drei + postprocessing — 3D tactical scene, bloom, custom GLSL terrain shader
- ECharts / Recharts — analytics panels & signal statistics
- Tiptap — rich chat input; tsParticles, GSAP, Framer Motion — HUD effects
- SimpleWebAuthn — passkey login; i18n EN/AZ

---

## 🚀 Quick Start

### 1. Backend

```bash
pip install numpy scipy pyqtgraph PyQt6 sounddevice pyrtlsdr \
            fastapi uvicorn psycopg2-binary

python server.py          # API + WebSocket  →  http://localhost:8000
python spectrum_core.py   # desktop spectrum GUI (optional)
```

### 2. Dashboard

```bash
cd sdr-dashboard
npm install
npm run dev               # → http://localhost:5173
```

### 3. Collectors (optional, on remote SDR machines)

```bash
python sdr_collector.py   # streams sweeps to the central server
```

> ⚙️ All secrets/hosts are configured via environment variables or a local
> `config.env` (intentionally not committed — fill in your own keys).

---

## 📁 Project Structure

```
CGrep-v3/
├── server.py               # FastAPI backend — API, WS, auth, chat, backups
├── spectrum_core.py        # PyQt6 spectrum + waterfall + audio GUI
├── dashboard.py            # radio network demo (rtl_fm pipe audio)
├── sdr_collector.py        # headless remote collector agent
├── fm_dab_sweep.py         # FM + DAB+ band sweeper
├── dab11b_monitor.py       # DAB ensemble monitor (11B)
├── dab12a_monitor.py       # DAB ensemble monitor (12A)
├── dual_site_monitor.py    # two-site FM/DAB monitor
├── regional_monitor.py     # single-site regional monitor
├── waterfall.py            # waterfall analysis
├── generate_pdf.py         # signal report generator
├── auth_router.py          # WebAuthn/JWT auth routes
├── models.py               # DB models
├── simulation.py           # simulated RF data for dev/demo
├── sdr-dashboard/          # React 19 + Vite tactical dashboard
├── essentials/             # launcher scripts & thresholds
└── rpfft/                  # rtl-power-fftw (vendored, fast sweeps)
```

---

## Demo

<!-- TO ADD THE MOBILE VIDEO: click the Edit button on this file on GitHub, place your cursor
     inside the src="" below (deleting the placeholder text), then drag & drop the mobile .mp4
     into the editor - GitHub inserts the video URL right where your cursor is. -->

<div align="center">
  <table>
    <tr>
      <td align="center"><b>Dashboard</b></td>
      <td align="center"><b>Mobile</b></td>
    </tr>
    <tr>
      <td><video src="https://github.com/user-attachments/assets/69ecd77a-acfd-4d07-9f7e-7b84c19701d5" controls muted width="440"></video></td>
      <td><video src="DROP_mobile.mp4_HERE" controls muted width="220"></video></td>
    </tr>
  </table>
</div>

---

## 🗺️ Roadmap

- [ ] Multi-band presets (airband, ADS-B, POCSAG)
- [ ] Per-signal recording & IQ capture on trigger
- [ ] Grafana-style alerting rules
- [ ] Docker compose deployment

---

## 📜 License

**Copyright © 2026 Elcan İsmayılov. All rights reserved.**

This repository is provided for **view-only and educational purposes**.
Use, reproduction, modification, distribution, or commercialization is
prohibited without explicit written permission — see [LICENSE](LICENSE).

*RX-only project — be aware of your local regulations.*

<div align="center">

**Built for the airwaves** 📡

</div>
