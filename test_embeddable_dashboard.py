"""
test_embeddable_dashboard.py

Standalone test server for EmbeddableSDRDashboard / SpectrumVisualization / WaterfallVisualization.
Pulls REAL data from sdr_db.rf_sweep_data (PostgreSQL).

Runs a FastAPI server that serves:
  GET  /api/spectrum   — HTTP polling endpoint (returns FFT array + peaks from DB)
  WS   /ws/test        — WebSocket endpoint (streams DB data every 500 ms)
  GET  /               — Self-contained test page (canvas, no React needed)

Usage:
    python test_embeddable_dashboard.py

Env vars (same as sdr_collector.py):
    DB_HOST, DB_PORT, DB_NAME, DB_USER, POSTGRES_PASSWORD

Then open: http://localhost:8765
"""

import asyncio
import os
import statistics
import time
from datetime import datetime, timedelta

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

app = FastAPI(title="Embeddable SDR Dashboard Test Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# DB connection — mirrors sdr_collector.py env vars
# ---------------------------------------------------------------------------
_DB_USER = os.getenv("DB_USER", "postgres")
_DB_PASS = os.getenv("POSTGRES_PASSWORD", "changeme")
_DB_HOST = os.getenv("DB_HOST", "localhost")
_DB_PORT = os.getenv("DB_PORT", "5432")
_DB_NAME = os.getenv("DB_NAME", "sdr_db")

_ENGINE = create_engine(
    f"postgresql://{_DB_USER}:{_DB_PASS}@{_DB_HOST}:{_DB_PORT}/{_DB_NAME}",
    pool_pre_ping=True,
)
_Session = sessionmaker(bind=_ENGINE)


# ---------------------------------------------------------------------------
# Real data reader — queries rf_sweep_data
# ---------------------------------------------------------------------------
# frequency_mhz is stored as kHz integer (e.g. 88700 = 88.7 MHz)
# FM band only — excludes DAB+ at 223936 kHz
FREQ_START_KHZ = 88_000
FREQ_END_KHZ   = 108_000
FREQ_START     = FREQ_START_KHZ / 1000.0
FREQ_END       = FREQ_END_KHZ   / 1000.0
FFT_BINS       = 256
NOISE_FLOOR    = -100.0


def _query_spectrum() -> dict:
    """
    Reads the most recent sweep batch from rf_sweep_data.
    Finds the newest timestamp, then pulls all rows within 30 seconds of it
    so we always show the last full sweep even if the collector is paused.
    """
    db = _Session()
    try:
        newest = db.execute(
            text("SELECT MAX(timestamp) FROM rf_sweep_data")
        ).scalar()
        if newest is None:
            return {"fft": [NOISE_FLOOR] * FFT_BINS, "peaks": [], "ts": time.time(), "rows": 0, "newest": None}
        since = newest - timedelta(seconds=60)
        rows = db.execute(
            text(
                "SELECT frequency_mhz, power_dbfs FROM rf_sweep_data "
                "WHERE timestamp >= :since AND frequency_mhz BETWEEN :lo AND :hi "
                "ORDER BY frequency_mhz"
            ),
            {"since": since, "lo": FREQ_START_KHZ, "hi": FREQ_END_KHZ},
        ).fetchall()
    finally:
        db.close()

    # One entry per unique frequency (100 kHz steps = ~200 points across FM band)
    # Use the max power seen at each frequency in the time window
    freq_map: dict = {}
    for freq_khz, power_dbfs in rows:
        if power_dbfs is None:
            continue
        khz = int(freq_khz)
        if khz not in freq_map or power_dbfs > freq_map[khz]:
            freq_map[khz] = round(float(power_dbfs), 2)

    # Build sorted fft array (one value per 100 kHz step)
    sorted_freqs = sorted(freq_map.keys())
    fft = [freq_map[k] for k in sorted_freqs]
    freq_labels = [round(k / 1000.0, 1) for k in sorted_freqs]

    # Peaks = frequencies significantly above the median
    if fft:
        med = statistics.median(fft)
        peaks = [
            {"freq": freq_labels[i], "dbfs": fft[i], "label": f"{freq_labels[i]:.1f} MHz"}
            for i in range(len(fft)) if fft[i] > med + 10
        ]
    else:
        fft = [NOISE_FLOOR] * FFT_BINS
        peaks = []

    return {"fft": fft, "peaks": peaks, "ts": time.time(), "rows": len(rows), "newest": str(newest)}


# ---------------------------------------------------------------------------
# HTTP polling endpoint
# ---------------------------------------------------------------------------
@app.get("/api/spectrum")
async def get_spectrum():
    try:
        data = await asyncio.get_event_loop().run_in_executor(None, _query_spectrum)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e), "fft": [], "peaks": []}, status_code=500)


# ---------------------------------------------------------------------------
# WebSocket streaming endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/test")
async def ws_test(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            try:
                data = await asyncio.get_event_loop().run_in_executor(None, _query_spectrum)
            except Exception as e:
                data = {"error": str(e), "fft": [], "peaks": []}
            await websocket.send_json(data)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# Test HTML page — self-contained canvas visualization (no React/Vite needed)
# ---------------------------------------------------------------------------
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>SDR Spectrum — Live</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    html,body{width:100%;height:100%}
    body{background:#08090f;color:#fff;font-family:'Segoe UI',sans-serif;padding:20px}
    .page{max-width:1300px;margin:0 auto;display:flex;flex-direction:column;gap:20px}
    .panel{background:rgba(10,14,23,0.8);border-radius:14px;padding:18px}
    .panel-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
    .panel-title{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:rgba(0,229,255,0.7)}
    .badge{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:6px;font-size:10px;font-family:monospace;font-weight:600;transition:all .3s}
    .badge.ok{background:rgba(0,229,255,0.08);border:1px solid rgba(0,229,255,0.25);color:#00e5ff}
    .badge.err{background:rgba(255,87,34,0.08);border:1px solid rgba(255,87,34,0.25);color:#ff5722}
    .dot{width:7px;height:7px;border-radius:50%;transition:all .3s}
    .dot.ok{background:#00e5ff;box-shadow:0 0 8px #00e5ff}
    .dot.err{background:#ff5722;box-shadow:0 0 8px #ff5722}
    .stats{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
    .stat{flex:1;min-width:90px;padding:10px;background:rgba(0,229,255,0.04);border:1px solid rgba(0,229,255,0.1);border-radius:8px;text-align:center}
    .stat-val{font-family:monospace;font-size:17px;font-weight:700;color:#00e5ff;transition:color .3s}
    .stat-lbl{font-size:9px;color:rgba(255,255,255,0.3);text-transform:uppercase;margin-top:2px;letter-spacing:.06em}
    canvas{display:block;border-radius:10px}
    .freq-axis{display:flex;justify-content:space-between;padding:5px 2px 0;font-size:9px;font-family:monospace;color:rgba(0,229,255,0.35)}
  </style>
</head>
<body>
<div class="page">

  <!-- SPECTRUM -->
  <div class="panel">
    <canvas id="spec-cv"></canvas>
  </div>

  <!-- WATERFALL -->
  <div class="panel">
    <canvas id="wf-cv"></canvas>
  </div>
</div>

<script>
const DBFS_MIN   = -100, DBFS_MAX = -20;
const LERP       = 0.12;
const FLOOR_LIFT = 8;     // visual-only dBFS lift (makes noise floor visible, no data change)

// ── shared state ─────────────────────────────────────────────────────────────
let targetFft  = [];   // raw values from server
let smoothFft  = [];   // lerped display values

// ── helpers ──────────────────────────────────────────────────────────────────
function lerp(a, b, t) { return a + (b - a) * t; }

function norm(v) { return Math.max(0, Math.min(1, (v - DBFS_MIN) / (DBFS_MAX - DBFS_MIN))); }
// normV: visual-only norm with lifted floor so noise floor renders above baseline
function normV(v) { return norm(v + FLOOR_LIFT); }

// ── Spectrum canvas ───────────────────────────────────────────────────────────
const specCV  = document.getElementById('spec-cv');
const specCTX = specCV.getContext('2d');

function resizeCanvas(cv) {
  const W = cv.parentElement.clientWidth - 36;
  cv.width  = W * devicePixelRatio;
  cv.style.width = W + 'px';
}

// Draw one bezier-smoothed curve — uses normV (visual floor lift) + optional shimmer array
function strokeCurve(ctx, fft, W, H, scale, shimmer) {
  const step = W / (fft.length - 1);
  ctx.beginPath();
  for (let i = 0; i < fft.length; i++) {
    const x  = i * step;
    const sv = shimmer ? shimmer[i] : 0;
    const y  = H - normV(fft[i] + sv) * (H - 8) * scale;
    if (i === 0) { ctx.moveTo(x, y); continue; }
    const px = (i - 1) * step;
    const pv = shimmer ? shimmer[i-1] : 0;
    const py = H - normV(fft[i-1] + pv) * (H - 8) * scale;
    const cx = (px + x) / 2;
    ctx.bezierCurveTo(cx, py, cx, y, x, y);
  }
  ctx.stroke();
}

function drawSpec(fft, shimmer) {
  if (!fft || !fft.length) return;

  const W = specCV.width / devicePixelRatio;
  const H = specCV.height / devicePixelRatio;
  const ctx = specCTX;
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);

  // margins for axes
  const ML = 48, MR = 12, MT = 14, MB = 28;
  const PW = W - ML - MR;   // plot width
  const PH = H - MT - MB;   // plot height

  // background
  ctx.fillStyle = '#08090f';
  ctx.fillRect(0, 0, W, H);

  // ── grid + Y-axis labels ──────────────────────────────────────────────────
  const DB_STEPS = [-100, -90, -80, -70, -60, -50, -40, -30, -20];
  ctx.font = '10px "Segoe UI Mono", monospace';
  ctx.textAlign = 'right';

  for (const db of DB_STEPS) {
    const yn = (db - DBFS_MIN) / (DBFS_MAX - DBFS_MIN);
    const y  = MT + PH - yn * PH;

    // grid line
    const highlight = (db === -60 || db === -40);
    ctx.strokeStyle = highlight ? 'rgba(0,229,255,0.22)' : 'rgba(0,229,255,0.10)';
    ctx.lineWidth   = highlight ? 1.0 : 0.5;
    ctx.setLineDash(highlight ? [5,4] : []);
    ctx.beginPath(); ctx.moveTo(ML, y); ctx.lineTo(ML + PW, y); ctx.stroke();
    ctx.setLineDash([]);

    // Y label — uniform brightness
    ctx.fillStyle = 'rgba(0,229,255,0.85)';
    ctx.fillText(db + ' dB', ML - 6, y + 3.5);
  }

  // ── X-axis freq labels ────────────────────────────────────────────────────
  const freqTicks = [88,90,92,94,96,98,100,102,104,106,108];
  ctx.textAlign = 'center';
  ctx.font = '10px "Segoe UI Mono", monospace';

  for (const f of freqTicks) {
    const xn = (f - 88) / (108 - 88);
    const x  = ML + xn * PW;
    const isMajor = f % 4 === 0 || f === 88 || f === 108;

    // tick mark
    ctx.strokeStyle = isMajor ? 'rgba(0,229,255,0.5)' : 'rgba(0,229,255,0.2)';
    ctx.lineWidth = isMajor ? 1 : 0.5;
    ctx.beginPath(); ctx.moveTo(x, MT + PH); ctx.lineTo(x, MT + PH + 4); ctx.stroke();

    // vertical grid line
    ctx.strokeStyle = 'rgba(0,229,255,0.09)';
    ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(x, MT); ctx.lineTo(x, MT + PH); ctx.stroke();

    // label — uniform brightness
    ctx.fillStyle = 'rgba(0,229,255,0.85)';
    ctx.fillText(f + ' MHz', x, MT + PH + MB - 4);
  }

  // ── axis border lines ─────────────────────────────────────────────────────
  // left axis glow
  ctx.shadowColor = 'rgba(0,229,255,0.4)';
  ctx.shadowBlur  = 6;
  ctx.strokeStyle = 'rgba(0,229,255,0.35)';
  ctx.lineWidth   = 1;
  ctx.beginPath(); ctx.moveTo(ML, MT); ctx.lineTo(ML, MT + PH); ctx.stroke();
  // bottom axis
  ctx.beginPath(); ctx.moveTo(ML, MT + PH); ctx.lineTo(ML + PW, MT + PH); ctx.stroke();
  ctx.shadowBlur  = 0;

  // ── parallel flowing lines (clipped to plot area) ─────────────────────────
  ctx.save();
  ctx.beginPath();
  ctx.rect(ML, MT, PW, PH);
  ctx.clip();

  // strokeCurve uses full W/H — wrap it to use plot coords
  function strokeCurvePlot(scale) {
    const step = PW / (fft.length - 1);
    ctx.beginPath();
    for (let i = 0; i < fft.length; i++) {
      const sv = shimmer ? shimmer[i] : 0;
      const yn = normV(fft[i] + sv) * scale;
      const x  = ML + i * step;
      const y  = MT + PH - yn * (PH - 4);
      if (i === 0) { ctx.moveTo(x, y); continue; }
      const px = ML + (i-1) * step;
      const pv = shimmer ? shimmer[i-1] : 0;
      const py = MT + PH - normV(fft[i-1] + pv) * scale * (PH - 4);
      ctx.bezierCurveTo((px+x)/2, py, (px+x)/2, y, x, y);
    }
    ctx.stroke();
  }

  const LINES = 20;
  ctx.lineJoin = 'round';
  for (let li = 0; li < LINES; li++) {
    const t = li / (LINES - 1);
    let r, g, b, a;
    if (t < 0.5) {
      const tt = t / 0.5;
      r = Math.round(15 + tt*40); g = Math.round(10 + tt*40);
      b = Math.round(100 + tt*155); a = 0.12 + tt*0.25;
    } else {
      const tt = (t-0.5)/0.5;
      r = Math.round(55+tt*145); g = Math.round(50+tt*200);
      b = 255; a = 0.37+tt*0.55;
    }
    ctx.strokeStyle = `rgba(${r},${g},${b},${a})`;
    ctx.lineWidth   = t > 0.88 ? 1.0 : 0.7;
    strokeCurvePlot(t);
  }

  // top glow outline — 3 passes
  ctx.shadowColor = 'rgba(100,210,255,0.5)';
  ctx.shadowBlur  = 8;
  ctx.strokeStyle = 'rgba(80,190,255,0.15)';
  ctx.lineWidth   = 10; strokeCurvePlot(1);
  ctx.shadowBlur  = 0;
  ctx.strokeStyle = 'rgba(140,220,255,0.30)';
  ctx.lineWidth   = 3.5; strokeCurvePlot(1);
  ctx.strokeStyle = '#d8f4ff';
  ctx.lineWidth   = 1.0; strokeCurvePlot(1);

  ctx.restore();
}

// ── Waterfall canvas ──────────────────────────────────────────────────────────
const wfCV  = document.getElementById('wf-cv');
const wfCTX = wfCV.getContext('2d');

let wfOff    = null;   // offscreen pixel buffer
let wfOffCTX = null;
let wfSubY   = 0;      // sub-pixel scroll accumulator
let _lastRow = null;   // newest row from WS
const WF_PX_PER_SEC = 30; // scroll speed in physical pixels/sec

// Neon colormap matching spectrum: black → deep indigo → blue → cyan → white
function wfColor(v) {
  // 4-stop ramp: 0=black, 0.3=indigo, 0.6=blue, 0.85=cyan, 1=white
  let r, g, b;
  if (v < 0.3) {
    const t = v / 0.3;
    r = Math.round(t * 30);   g = Math.round(t * 10);   b = Math.round(t * 120);
  } else if (v < 0.6) {
    const t = (v - 0.3) / 0.3;
    r = Math.round(30  + t * 25);  g = Math.round(10  + t * 50);  b = Math.round(120 + t * 135);
  } else if (v < 0.85) {
    const t = (v - 0.6) / 0.25;
    r = Math.round(55  + t * 0);   g = Math.round(60  + t * 169); b = Math.round(255);
  } else {
    const t = (v - 0.85) / 0.15;
    r = Math.round(55  + t * 200); g = Math.round(229 + t * 26);  b = 255;
  }
  return `rgb(${r},${g},${b})`;
}

// waterfall margins — same as spectrum
const WF_ML = 48, WF_MR = 12, WF_MT = 14, WF_MB = 28;

function wfEnsurePlot() {
  const W = wfCV.width, H = wfCV.height;
  const PW = Math.round((W / devicePixelRatio - WF_ML - WF_MR) * devicePixelRatio);
  const PH = Math.round((H / devicePixelRatio - WF_MT - WF_MB) * devicePixelRatio);
  if (!wfOff || wfOff.width !== PW || wfOff.height !== PH) {
    wfOff = document.createElement('canvas');
    wfOff.width = PW; wfOff.height = PH;
    wfOffCTX = wfOff.getContext('2d');
    wfOffCTX.fillStyle = '#08090f';
    wfOffCTX.fillRect(0, 0, PW, PH);
    wfSubY = 0;
  }
}

function drawWF(dt) {
  wfEnsurePlot();
  const cW = wfCV.width, cH = wfCV.height;
  const W  = cW / devicePixelRatio, H = cH / devicePixelRatio;
  const PW = wfOff.width, PH = wfOff.height;
  const ctx = wfCTX;

  // scroll pixel buffer
  wfSubY += WF_PX_PER_SEC * dt * devicePixelRatio;
  if (wfSubY >= 1 && _lastRow) {
    const shift = Math.floor(wfSubY);
    const img = wfOffCTX.getImageData(0, 0, PW, PH - shift);
    wfOffCTX.fillStyle = '#08090f';
    wfOffCTX.fillRect(0, 0, PW, PH);
    wfOffCTX.putImageData(img, 0, shift);
    const binW = PW / _lastRow.length;
    for (let bi = 0; bi < _lastRow.length; bi++) {
      wfOffCTX.fillStyle = wfColor(_lastRow[bi]);
      wfOffCTX.fillRect(bi * binW, 0, binW + 0.5, shift + 1);
    }
    wfSubY -= shift;
  }

  // clear full canvas
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  ctx.fillStyle = '#08090f';
  ctx.fillRect(0, 0, W, H);

  // blit pixel buffer into plot area
  const plotW = W - WF_ML - WF_MR, plotH = H - WF_MT - WF_MB;
  ctx.drawImage(wfOff, 0, 0, PW, PH, WF_ML, WF_MT, plotW, plotH);

  // ── Y-axis color legend (left of plot) ───────────────────────────────────
  // Draw a thin gradient bar showing the color scale, with dBFS labels
  const legendW = 7;
  const legendX = WF_ML - legendW - 14;  // sits between left edge and axis
  const grad = ctx.createLinearGradient(0, WF_MT, 0, WF_MT + plotH);
  // top = strong signal (high v=1), bottom = noise (v=0)
  grad.addColorStop(0,    '#fff');
  grad.addColorStop(0.15, '#00e5ff');
  grad.addColorStop(0.4,  `rgb(55,60,255)`);
  grad.addColorStop(0.7,  `rgb(30,10,120)`);
  grad.addColorStop(1,    '#08090f');
  ctx.fillStyle = grad;
  ctx.fillRect(legendX, WF_MT, legendW, plotH);
  ctx.strokeStyle = 'rgba(0,229,255,0.25)';
  ctx.lineWidth = 0.5;
  ctx.strokeRect(legendX, WF_MT, legendW, plotH);

  // dBFS labels alongside legend
  const legendLabels = [
    { db: -20,  label: '−20' },
    { db: -40,  label: '−40' },
    { db: -60,  label: '−60' },
    { db: -80,  label: '−80' },
    { db: -100, label: '−100' },
  ];
  ctx.font = '9px "Segoe UI Mono", monospace';
  ctx.textAlign = 'right';
  for (const { db, label } of legendLabels) {
    const yn = (db - DBFS_MIN) / (DBFS_MAX - DBFS_MIN);
    const y  = WF_MT + plotH - yn * plotH;
    ctx.fillStyle = 'rgba(0,229,255,0.85)';
    ctx.fillText(label, legendX - 3, y + 3.5);
    // tiny tick into legend bar
    ctx.strokeStyle = 'rgba(0,229,255,0.5)';
    ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(legendX, y); ctx.lineTo(legendX + legendW, y); ctx.stroke();
  }

  // ── X-axis ──────────────────────────────────────────────────────────────
  const freqTicks = [88,90,92,94,96,98,100,102,104,106,108];
  ctx.font = '10px "Segoe UI Mono", monospace';
  ctx.textAlign = 'center';
  for (const f of freqTicks) {
    const xn = (f - 88) / 20;
    const x  = WF_ML + xn * plotW;
    const isMajor = f % 4 === 0 || f === 88 || f === 108;
    ctx.strokeStyle = isMajor ? 'rgba(0,229,255,0.5)' : 'rgba(0,229,255,0.2)';
    ctx.lineWidth = isMajor ? 1 : 0.5;
    ctx.beginPath(); ctx.moveTo(x, WF_MT + plotH); ctx.lineTo(x, WF_MT + plotH + 4); ctx.stroke();
    ctx.fillStyle = 'rgba(0,229,255,0.85)';
    ctx.fillText(f + ' MHz', x, WF_MT + plotH + WF_MB - 4);
  }

  // ── axis border ──────────────────────────────────────────────────────────
  ctx.shadowColor = 'rgba(0,229,255,0.4)';
  ctx.shadowBlur  = 6;
  ctx.strokeStyle = 'rgba(0,229,255,0.35)';
  ctx.lineWidth   = 1;
  ctx.beginPath(); ctx.moveTo(WF_ML, WF_MT); ctx.lineTo(WF_ML, WF_MT + plotH); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(WF_ML, WF_MT + plotH); ctx.lineTo(WF_ML + plotW, WF_MT + plotH); ctx.stroke();
  ctx.shadowBlur = 0;
}

// ── animation loop ────────────────────────────────────────────────────────────
let _t = 0;
let _shimmer = [];
let _lastAnimTs = null;
function animate(ts) {
  const dt = _lastAnimTs ? Math.min((ts - _lastAnimTs) / 1000, 0.1) : 0.016;
  _lastAnimTs = ts;
  _t += dt;

  // Lerp each bin toward its target value
  if (targetFft.length) {
    if (smoothFft.length !== targetFft.length) {
      smoothFft  = [...targetFft];
      _shimmer   = new Array(targetFft.length).fill(0);
    } else {
      for (let i = 0; i < smoothFft.length; i++) {
        smoothFft[i] = lerp(smoothFft[i], targetFft[i], LERP);
      }
    }
  }

  // Visual-only shimmer per bin:
  //   v > -65 dBFS  → gentle smooth wave (±1.2 dB)
  //   v <= -65 dBFS → chaotic jitter (±2.5 dB, multiple overlapping frequencies)
  if (_shimmer.length === smoothFft.length) {
    for (let i = 0; i < _shimmer.length; i++) {
      const v = smoothFft[i];
      if (v > -65) {
        // strong signal: single slow sine, small amplitude
        _shimmer[i] = Math.sin(_t * 1.4 + i * 0.3) * 1.2;
      } else {
        // weak signal: slow chaotic jitter
        const chaos =
          Math.sin(_t * 2.1  + i * 1.1) * 0.9 +
          Math.sin(_t * 3.7  + i * 2.3) * 0.7 +
          Math.sin(_t * 5.3  + i * 0.7) * 0.6 +
          Math.sin(_t * 1.9  + i * 3.7) * 0.5;
        _shimmer[i] = chaos * 0.55;
      }
    }
  }

  drawSpec(smoothFft, _shimmer);
  drawWF(dt);
  requestAnimationFrame(animate);
}

// ── resize handler ────────────────────────────────────────────────────────────
function resize() {
  const H_spec = Math.round(window.innerHeight * 0.35);
  const H_wf   = Math.round(window.innerHeight * 0.32);

  resizeCanvas(specCV);
  specCV.height = H_spec * devicePixelRatio;
  specCV.style.height = H_spec + 'px';

  resizeCanvas(wfCV);
  wfCV.height = H_wf * devicePixelRatio;
  wfCV.style.height = H_wf + 'px';
}
window.addEventListener('resize', resize);
resize();

// ── data fetch (polling) ──────────────────────────────────────────────────────
async function fetchSpectrum() {
  try {
    const res  = await fetch('/api/spectrum');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    targetFft = data.fft || [];
  } catch(e) {}
}
fetchSpectrum();
setInterval(fetchSpectrum, 500);

// ── WebSocket waterfall ───────────────────────────────────────────────────────
function connectWS() {
  const ws = new WebSocket('ws://localhost:8765/ws/test');
  ws.onopen  = () => {};
  ws.onerror = () => {};
  ws.onclose = () => { setTimeout(connectWS, 3000); };
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      const fft  = data.fft || data;
      if (!Array.isArray(fft) || !fft.length) return;
      _lastRow = new Float32Array(fft.map(v => norm(v)));
    } catch(e) {}
  };
}
connectWS();

requestAnimationFrame(animate);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def serve_test_page():
    return HTML


# ---------------------------------------------------------------------------
# Standalone JSON demo endpoint — useful for quick curl / Postman checks
# ---------------------------------------------------------------------------
@app.get("/demo")
async def demo():
    """Returns a sample of the live DB payload."""
    try:
        spec = await asyncio.get_event_loop().run_in_executor(None, _query_spectrum)
        return {
            "db_rows_found":  spec["rows"],
            "fft_bins":       len(spec["fft"]),
            "fft_sample":     spec["fft"][:16],
            "peaks_sample":   spec["peaks"][:8],
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    print("=" * 60)
    print("  EmbeddableSDRDashboard Test Server")
    print("  http://localhost:8765")
    print("  Polling:   GET  http://localhost:8765/api/spectrum")
    print("  WebSocket: WS   ws://localhost:8765/ws/test")
    print("  Demo:      GET  http://localhost:8765/demo")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
