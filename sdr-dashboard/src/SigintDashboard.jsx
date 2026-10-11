import React, { useState, useEffect, useRef, useCallback, useMemo, memo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useTranslation } from './i18n/LanguageContext';

// Inject shared blink keyframe once — all blinking elements share the same CSS clock
if (typeof document !== 'undefined' && !document.getElementById('sig-blink-style')) {
  const s = document.createElement('style');
  s.id = 'sig-blink-style';
  s.textContent = `
    @keyframes sigBlink {
      0%,100% { opacity:1; }
      50%      { opacity:0; }
    }
    .sig-blink { animation: sigBlink 0.65s ease-in-out infinite; }
    @keyframes sigFlash {
      0%,100% { opacity:0; }
      50%      { opacity:0.18; }
    }
    .sig-flash { animation: sigFlash 0.65s ease-in-out infinite; }
  `;
  document.head.appendChild(s);
}

// ─── Design tokens ────────────────────────────────────────────────────────────
const SYS   = `'Space Grotesk',sans-serif`;
const MONO  = `'JetBrains Mono',monospace`;
const DIGIT = `'DSDigit','JetBrains Mono',monospace`;
const CYAN  = '#00e5ff';
const AMBER = '#ffaf00';
const ORANGE = '#ff5722';
const PURPLE = '#4a00e0';
const RED   = '#ff5722';
// Fallback calibration used only until real per-SDR thresholds (computed from
// actual SQL power_dbfs history, see sdr_thresholds.json / /api/sdr/list) load.
// fm_dab_sweep.py now logs raw dBFS (DB_OFFSET_DB=0), so the default scale is
// negative. These defaults only matter while thresholds are loading.
const DBFS_MIN = -90;
const DBFS_MAX = -20;
const RED_THRESHOLD_DBFS   = DBFS_MIN;
const GREEN_THRESHOLD_DBFS = DBFS_MAX;
const BAR_HEADROOM = 0.12;          // keep 12% empty space above the max calibrated value
const BAR_SCALE    = 1 - BAR_HEADROOM; // factor applied to all bar heights / thresholds

// DAB+ 12A is stored in SQL at 223.936 MHz but displayed at 88.7 MHz.
const DAB_SQL_FREQ_MHZ = 223.936;
const DAB_PEAK_KEY = DAB_SQL_FREQ_MHZ.toFixed(1); // server rounds to 1 decimal

// iOS-style on/off toggle for dBm ↔ dBµV display units
function UnitToggle({ unit, onChange }) {
  const isDbuv = unit === 'dbuv';
  return (
    <div
      onClick={() => onChange(isDbuv ? 'dbm' : 'dbuv')}
      style={{
        display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', userSelect: 'none'
      }}
      title={isDbuv ? 'Switch to raw dBm (dBFS)' : 'Switch to dBµV (+107 dB)'}
    >
      <span style={{
        fontSize: 10, fontWeight: 600, fontFamily: SYS,
        color: isDbuv ? 'rgba(255,255,255,0.35)' : CYAN,
        transition: 'color 0.2s',
      }}>dBm</span>
      <div style={{
        width: 44, height: 24, borderRadius: 12,
        background: isDbuv ? '#34c759' : 'rgba(255,255,255,0.15)',
        position: 'relative', transition: 'background 0.2s ease',
        boxShadow: 'inset 0 1px 3px rgba(0,0,0,0.3)',
      }}>
        <div style={{
          width: 20, height: 20, borderRadius: '50%',
          background: '#fff',
          position: 'absolute', top: 2,
          left: isDbuv ? 22 : 2,
          boxShadow: '0 2px 4px rgba(0,0,0,0.25)',
          transition: 'left 0.2s ease',
        }} />
      </div>
      <span style={{
        fontSize: 10, fontWeight: 600, fontFamily: SYS,
        color: isDbuv ? CYAN : 'rgba(255,255,255,0.35)',
        transition: 'color 0.2s',
      }}>dBµV</span>
    </div>
  );
}

const unitLabel = (unit) => unit === 'dbuv' ? 'dBµV' : 'dBm';
const displayVal = (v, unit) => unit === 'dbuv' ? v + 107 : v;

const TOTAL_H      = () => Math.max(180, window.innerHeight - (window.innerWidth <= 768 ? 110 : window.innerWidth <= 1024 ? 150 : 180));
const FREQ_LABEL_H = () => Math.round(TOTAL_H() * (window.innerWidth <= 768 ? 0.28 : window.innerWidth <= 1024 ? 0.32 : 0.40));
function getBarH()  { return TOTAL_H() - FREQ_LABEL_H(); }
function getVisibleBars() { return window.innerWidth <= 768 ? 8 : window.innerWidth <= 1024 ? 14 : window.innerWidth <= 1366 ? 20 : 30; }

// Solid bar color: black (low) → dark blue → blue → cyan → white (hot tip)
const C0 = { r:0x08, g:0x09, b:0x0f };  // near black — bottom / no signal
const C1 = { r:0x1e, g:0x0a, b:0x78 };  // dark blue
const C2 = { r:0x37, g:0x3c, b:0xff }; // blue
const C3 = { r:0x00, g:0xe5, b:0xff }; // cyan
const C4 = { r:0xff, g:0xff, b:0xff }; // white — top / 0 dBFS hot

function lerpColor(a, b, t) {
  return {
    r: Math.round(a.r + (b.r - a.r) * t),
    g: Math.round(a.g + (b.g - a.g) * t),
    b: Math.round(a.b + (b.b - a.b) * t),
  };
}

// 5-stop interpolation matching the user-supplied gradient
function colorAt(p) {
  if (p <= 0.30) {
    // bottom (-60 → -42): black → dark blue
    const t = p / 0.30;
    return lerpColor(C0, C1, t);
  } else if (p <= 0.60) {
    // (-42 → -24): dark blue → blue
    const t = (p - 0.30) / 0.30;
    return lerpColor(C1, C2, t);
  } else if (p <= 0.85) {
    // (-24 → -9): blue → cyan
    const t = (p - 0.60) / 0.25;
    return lerpColor(C2, C3, t);
  } else {
    // hot tip (-9 → 0): cyan → white
    const t = (p - 0.85) / 0.15;
    return lerpColor(C3, C4, t);
  }
}

function barColor(p) {
  const { r, g, b } = colorAt(p);
  return `rgb(${r},${g},${b})`;
}

function barGlow(p) {
  const { r, g, b } = colorAt(p);
  return `0 0 16px rgba(${r},${g},${b},0.90), 0 0 32px rgba(${r},${g},${b},0.45)`;
}

function pct(dbfs, calMin = DBFS_MIN, calMax = DBFS_MAX) {
  const clamped = Math.max(calMin, Math.min(calMax, dbfs));
  return ((clamped - calMin) / (calMax - calMin)) * BAR_SCALE;
}

function isDABStation(s) { return s && (s.dab === true || s.freq === 88.7); }

// Red threshold Y position inside bar track (fraction from bottom) — same
// real per-SDR calibration is used for every station, including DAB+.
function thresholdPct(calMin = DBFS_MIN, calMax = DBFS_MAX, calRedThresh = RED_THRESHOLD_DBFS) {
  return Math.max(0, Math.min(BAR_SCALE, ((calRedThresh - calMin) / (calMax - calMin)) * BAR_SCALE));
}

// Green threshold Y position inside bar track (fraction from bottom)
function greenThresholdPct(calMin = DBFS_MIN, calMax = DBFS_MAX, calGreenThresh = GREEN_THRESHOLD_DBFS) {
  return Math.max(0, Math.min(BAR_SCALE, ((calGreenThresh - calMin) / (calMax - calMin)) * BAR_SCALE));
}

// Whitelist — 18 stations: DAB+ 12A (223.936 MHz / block 12A) + 17 FM stations
const DEFAULT_STATIONS = [
  { freq: 88.7,  name: 'DAB+ 12A',   dab: true  },  // 223.936 MHz DAB+ block 12A; keyed at 88.7 for data matching
  { freq: 90.0,  name: 'PLANET'       },
  { freq: 90.0,  name: 'ICTIMAI'      },
  { freq: 90.7,  name: 'YURD'         },
  { freq: 92.5,  name: 'DALGA'        },
  { freq: 93.0,  name: 'SAGLAM'       },
  { freq: 93.3,  name: 'RETRO'        },
  { freq: 97.7,  name: 'HIT FM'       },
  { freq: 99.3,  name: 'XY ICTIMAI'   },
  { freq: 100.5, name: 'TMB'          },
  { freq: 101.1, name: 'ENERJI'       },
  { freq: 101.3, name: 'XY'           },
  { freq: 102.0, name: 'ANS'          },
  { freq: 103.0, name: 'XEZER'        },
  { freq: 103.3, name: 'ASAN'         },
  { freq: 104.0, name: 'SPACE'        },
  { freq: 105.0, name: 'AZERBAYCAN'   },
  { freq: 106.0, name: 'A.AZERBAYCAN' },
  { freq: 106.3, name: 'A.AZERBAYCAN' },
  { freq: 107.0, name: 'REAL'         },
];

// Build station list from config (station objects with freq, name, rtys, dab)
function buildStationsFromConfig(stations) {
  if (!stations || stations.length === 0) return DEFAULT_STATIONS;
  return stations.map(s => ({
    freq: s.freq,
    name: s.name || `${s.freq.toFixed(1)} MHz`,
    rtys: s.rtys || '',
    ...(s.dab ? { dab: true } : {}),
  }));
}

function initData(stations) {
  return (stations || DEFAULT_STATIONS).map(s => ({ ...s, dbfs: DBFS_MIN + Math.random() * (DBFS_MAX - DBFS_MIN) }));
}

// ─── Stat chip ────────────────────────────────────────────────────────────────
function Stat({ label, value, accent, glow, onClick }) {
  return (
    <div onClick={onClick}
      style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:2,
        cursor: onClick ? 'pointer' : 'default' }}>
      <span style={{
        fontFamily:SYS, fontSize:22, fontWeight:700, lineHeight:'1',
        color: accent || '#fff',
        textShadow: glow ? `0 0 14px ${accent}` : 'none',
        letterSpacing:'-0.03em',
      }}>{value}</span>
      <span style={{ fontFamily:SYS, fontSize:10, letterSpacing:'0.06em', fontWeight:500,
        color:'rgba(255,255,255,0.28)', whiteSpace:'nowrap', textTransform:'uppercase' }}>{label}</span>
    </div>
  );
}

const PANEL_SPRING = { type:'spring', stiffness:340, damping:34, mass:1.0 };
const TAB_SPRING   = { type:'spring', stiffness:420, damping:40, mass:0.7 };
const BAR_SPRING   = { type:'spring', stiffness:85,  damping:17, mass:0.9 };

// ─── Shared animation loop for all LED columns ─────────────────────────────
// Instead of N independent requestAnimationFrame loops, one loop drives all bars.
// Capped at 20fps to cut CPU usage while keeping motion fluid.
const _animCallbacks = new Set();
let _animRunning = false;
let _animPrev = 0;
let _animLastFrame = 0;
const _FRAME_MS = 1000 / 20;

function _animLoop(now) {
  if (_animCallbacks.size === 0) { _animRunning = false; return; }
  if (document.hidden) {
    requestAnimationFrame(_animLoop);
    return;
  }
  if (now - _animLastFrame < _FRAME_MS) {
    requestAnimationFrame(_animLoop);
    return;
  }
  _animLastFrame = now;
  const dt = Math.min((now - _animPrev) / 1000, 0.05);
  _animPrev = now;
  _animCallbacks.forEach(fn => fn(dt));
  requestAnimationFrame(_animLoop);
}

function _registerAnim(fn) {
  _animCallbacks.add(fn);
  if (!_animRunning) {
    _animRunning = true;
    _animPrev = performance.now();
    _animLastFrame = 0;
    requestAnimationFrame(_animLoop);
  }
}

function _unregisterAnim(fn) {
  _animCallbacks.delete(fn);
}

// Build a CSS gradient that draws all LED segments in one layer, including gaps
function buildGradient(totalSeg, segH, segGap, threshSeg, greenThreshSeg, lit) {
  const colH = totalSeg * (segH + segGap);
  const stops = [];
  for (let i = 0; i < totalSeg; i++) {
    const p0 = i * (segH + segGap) / colH;
    const p1 = (i * (segH + segGap) + segH) / colH;
    const p2 = ((i + 1) * (segH + segGap)) / colH;
    let color;
    if (i === threshSeg) {
      color = lit ? 'rgba(255,59,48,0.95)' : 'rgba(255,59,48,0.20)';
    } else if (i === greenThreshSeg) {
      color = lit ? 'rgba(76,175,80,0.95)' : 'rgba(76,175,80,0.20)';
    } else if (lit) {
      const { r, g, b } = colorAt(i / totalSeg);
      color = `rgb(${r},${g},${b})`;
    } else {
      color = 'rgba(255,255,255,0.05)';
    }
    stops.push(`${color} ${p0 * 100}%`, `${color} ${p1 * 100}%`);
    stops.push(`transparent ${p1 * 100}%`, `transparent ${p2 * 100}%`);
  }
  return `linear-gradient(to top, ${stops.join(', ')})`;
}

function buildInvertedGradient(totalSeg, segH, segGap, lit) {
  const colH = totalSeg * (segH + segGap);
  const stops = [];
  for (let i = 0; i < totalSeg; i++) {
    const p0 = i * (segH + segGap) / colH;
    const p1 = (i * (segH + segGap) + segH) / colH;
    const p2 = ((i + 1) * (segH + segGap)) / colH;
    const frac = i / totalSeg;
    let color;
    if (lit) {
      const r = Math.round(255 * (0.4 + 0.6 * frac));
      const g = Math.round(59  * (1 - frac));
      color = `rgb(${r},${g},20)`;
    } else {
      color = 'rgba(255,255,255,0.05)';
    }
    stops.push(`${color} ${p0 * 100}%`, `${color} ${p1 * 100}%`);
    stops.push(`transparent ${p1 * 100}%`, `transparent ${p2 * 100}%`);
  }
  return `linear-gradient(to top, ${stops.join(', ')})`;
}

const LEDColumn = memo(function LEDColumn({ litCount, totalSeg, threshSeg, greenThreshSeg, segH, segGap, isRed, inverted }) {
  const colH    = totalSeg * (segH + segGap);
  const targetH = (litCount / totalSeg) * colH;
  const barRef  = useRef();
  const peakRef = useRef();

  const unlitBg = useMemo(() => inverted ? buildInvertedGradient(totalSeg, segH, segGap, false) : buildGradient(totalSeg, segH, segGap, threshSeg, greenThreshSeg, false), [totalSeg, segH, segGap, threshSeg, greenThreshSeg, inverted]);
  const litBg   = useMemo(() => inverted ? buildInvertedGradient(totalSeg, segH, segGap, true)  : buildGradient(totalSeg, segH, segGap, threshSeg, greenThreshSeg, true),  [totalSeg, segH, segGap, threshSeg, greenThreshSeg, inverted]);

  const state = useRef(null);
  if (!state.current) {
    state.current = { barCur: targetH, peakCur: targetH, peakVel: 0, holdLeft: 0, target: targetH };
  }

  useEffect(() => {
    state.current.target = (litCount / totalSeg) * colH;
  }, [litCount, totalSeg, colH]);

  useEffect(() => {
    // Reset to target on colH change so the bar doesn't jump with stale pixel values
    state.current.barCur  = state.current.target;
    state.current.peakCur = state.current.target;
    state.current.peakVel = 0;
    state.current.holdLeft = 0;

    const HOLD_MS  = 400;
    const GRAVITY  = colH * 1.8;
    const LERP_K   = 0.14;

    const tick = (dt) => {
      const s = state.current;
      const k = 1 - Math.pow(1 - LERP_K, dt * 60);
      s.barCur += (s.target - s.barCur) * k;

      if (s.barCur >= s.peakCur - 0.5) {
        s.peakCur  = s.barCur;
        s.peakVel  = 0;
        s.holdLeft = HOLD_MS;
      } else if (s.holdLeft > 0) {
        s.holdLeft -= dt * 1000;
      } else {
        s.peakVel += GRAVITY * dt;
        s.peakCur  = Math.max(s.barCur, s.peakCur - s.peakVel * dt);
        if (s.peakCur <= s.barCur + 0.5) s.peakVel = 0;
      }

      if (barRef.current) {
        const clip = inverted
          ? (s.barCur / colH) * 100
          : (1 - s.barCur / colH) * 100;
        barRef.current.style.clipPath = inverted
          ? `inset(0 0 ${clip}% 0)`
          : `inset(${clip}% 0 0 0)`;
      }
      if (peakRef.current) {
        peakRef.current.style.transform = `translateY(${-s.peakCur}px)`;
      }
    };

    _registerAnim(tick);
    return () => _unregisterAnim(tick);
  }, [colH]);

  return (
    <div style={{ position:'relative', width:'100%', height: colH }}>
      {/* Unlit segments — one layer */}
      <div style={{ position:'absolute', inset:0, background: unlitBg }} />

      {/* Lit segments — one layer clipped to current bar height */}
      <div ref={barRef} style={{ position:'absolute', inset:0, background: litBg, clipPath: 'inset(100% 0 0 0)', willChange:'clip-path' }} />

      {/* Peak — thin white line, composited via transform */}
      <div ref={peakRef} style={{
        position:'absolute', left:0, right:0, zIndex:20,
        height: 2, borderRadius:1, bottom:0,
        background:'rgba(255,255,255,0.85)',
        boxShadow:'0 0 6px 1px rgba(255,255,255,0.5)',
        transform:'translateY(0px)',
        willChange:'transform',
      }} />

      {/* Red alert flash overlay — CSS keyframe */}
      {isRed && (
        <div className="sig-flash" style={{
          position:'absolute', inset:0, zIndex:5, borderRadius:4,
          background:'rgba(239,68,68,1)',
          pointerEvents:'none',
        }} />
      )}
    </div>
  );
});

function MobileHistoryTab() {
  const { t } = useTranslation();
  const [rows, setRows]         = useState([]);
  const [newIds, setNewIds]     = useState(new Set());
  const knownIdsRef             = useRef(new Set());
  const [showExport, setShowExport] = useState(false);
  const [exporting,  setExporting]  = useState(false);
  const pad = n => String(n).padStart(2,'0');
  const localIso = (d) => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const [exportFrom, setExportFrom] = useState(() => { const d = new Date(); d.setHours(d.getHours()-1); return localIso(d); });
  const [exportTo,   setExportTo]   = useState(() => localIso(new Date()));

  const fmtTime = (ts) => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('az', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  };

  const fmtFreq = (freq) => {
    if (Math.abs(freq - 223.936) < 0.01) return 'DAB+';
    return freq.toFixed(3);
  };

  const dbfsColor = (dbfs) => {
    const p = Math.max(0, Math.min(1, (dbfs - DBFS_MIN) / (DBFS_MAX - DBFS_MIN)));
    const { r, g, b } = colorAt(p);
    return `rgb(${r},${g},${b})`;
  };

  const doExport = async () => {
    setExporting(true);
    try {
      const fromTs = exportFrom ? new Date(exportFrom).getTime()/1000 : 0;
      const toTs   = exportTo   ? new Date(exportTo).getTime()/1000   : 0;
      const res  = await fetch(`/api/export?from_ts=${fromTs}&to_ts=${toTs}`, { credentials: 'include' });
      if (!res.ok) throw new Error(`Export failed: ${res.status}`);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `cgrep_${exportFrom.replace(/[T:]/g,'-')}_to_${exportTo.replace(/[T:]/g,'-')}.csv`;
      a.click(); URL.revokeObjectURL(a.href);
      setShowExport(false);
    } catch (e) {
      console.error('Export error:', e);
    }
    setExporting(false);
  };

  useEffect(() => {
    let cancelled = false;
    let id = null;
    const fetchHistory = async () => {
      if (document.hidden || cancelled) return;
      try {
        const res = await fetch('/api/history');
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (cancelled) return;
        const incoming = new Set();
        const fresh = [];
        data.forEach(r => {
          incoming.add(r.id);
          if (!knownIdsRef.current.has(r.id)) fresh.push(r.id);
        });
        if (fresh.length > 0) {
          setNewIds(prev => { const next = new Set(prev); fresh.forEach(id => next.add(id)); return next; });
          setTimeout(() => {
            setNewIds(prev => { const next = new Set(prev); fresh.forEach(id => next.delete(id)); return next; });
          }, 900);
        }
        knownIdsRef.current = incoming;
        setRows(data);
      } catch (_) {}
    };
    const start = () => { fetchHistory(); id = setInterval(fetchHistory, 10000); };
    const stop  = () => { clearInterval(id); id = null; };
    const onVis = () => { if (document.hidden) { stop(); } else { stop(); start(); } };
    document.addEventListener('visibilitychange', onVis);
    start();
    return () => { cancelled = true; stop(); document.removeEventListener('visibilitychange', onVis); };
  }, []);

  return (
    <div style={{ flex:1, minHeight:0, display:'flex', flexDirection:'column' }}>
      {/* Header row with Export button */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'0 12px 8px', flexShrink:0 }}>
        <div style={{
          display:'grid', gridTemplateColumns:'62px 1fr 62px 48px',
          gap:8, flex:1,
          fontFamily:SYS, fontSize:10, fontWeight:600,
          color:'rgba(255,255,255,0.22)', letterSpacing:'0.08em', textTransform:'uppercase',
        }}>
          <span>{t('sigint.time')}</span><span>{t('sigint.freq')}</span>
          <span style={{ textAlign:'right' }}>{t('sigint.dbfs')}</span>
          <span style={{ textAlign:'right' }}>{t('sigint.snr')}</span>
        </div>
        <button onClick={() => setShowExport(true)} style={{
          marginLeft:10, flexShrink:0,
          background:'rgba(0,229,255,0.10)', border:'1px solid rgba(0,229,255,0.30)',
          borderRadius:7, color:CYAN, fontSize:10, fontWeight:600,
          padding:'5px 10px', cursor:'pointer', fontFamily:SYS, letterSpacing:'0.05em',
        }}>{t('sigint.export')}</button>
      </div>

      {/* Rows */}
      <div style={{ flex:1, minHeight:0, overflowY:'auto' }}>
        {rows.map(r => (
          <div
            key={r.id}
            style={{
              display:'grid', gridTemplateColumns:'62px 1fr 62px 48px',
              gap:8, padding:'8px 12px',
              borderRadius:8, marginBottom:3,
              border:'1px solid rgba(255,255,255,0.04)',
            }}
          >
            <span style={{ fontFamily:MONO, fontSize:10, color:'rgba(255,255,255,0.30)', whiteSpace:'nowrap' }}>
              {fmtTime(r.ts)}
            </span>
            <span style={{ fontFamily:DIGIT, fontSize:18, color:dbfsColor(r.dbfs), letterSpacing:'0.04em', textShadow:`0 0 8px ${dbfsColor(r.dbfs)}55` }}>
              {fmtFreq(r.freq)}
            </span>
            <span style={{ fontFamily:MONO, fontSize:12, fontWeight:600, color: r.dbfs < RED_THRESHOLD_DBFS ? '#ff5722' : dbfsColor(r.dbfs), textAlign:'right' }}>
              {r.dbfs.toFixed(1)}
            </span>
            <span style={{ fontFamily:MONO, fontSize:11, color:'rgba(255,255,255,0.35)', textAlign:'right' }}>
              {r.snr.toFixed(1)}
            </span>
          </div>
        ))}
      </div>

      {/* Export modal */}
      <AnimatePresence>
      {showExport && (
        <motion.div
          initial={{ opacity:0 }} animate={{ opacity:1 }} exit={{ opacity:0 }}
          transition={{ duration:0.18 }}
          style={{
            position:'fixed', inset:0, zIndex:999,
            display:'flex', alignItems:'center', justifyContent:'center',
            background:'rgba(0,0,0,0.85)',
          }} onClick={() => setShowExport(false)}>
          <motion.div
            initial={{ opacity:0, y:24, scale:0.95 }}
            animate={{ opacity:1, y:0,  scale:1 }}
            exit={{    opacity:0, y:16, scale:0.97 }}
            transition={{ duration:0.22, ease:'easeOut' }}
            onClick={e => e.stopPropagation()}
            style={{
              background:'rgba(8,12,22,0.97)',
              border:'1px solid rgba(0,229,255,0.18)',
              borderRadius:20,
              boxShadow:'0 0 0 1px rgba(0,229,255,0.06), 0 24px 64px rgba(0,0,0,0.7), 0 0 40px rgba(0,229,255,0.06)',
              padding:'28px 24px 24px',
              width:'min(340px, calc(100vw - 32px))',
              display:'flex', flexDirection:'column', gap:20, fontFamily:SYS,
            }}>
            {/* Header */}
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
              <div>
                <div style={{ fontSize:16, fontWeight:700, color:'#fff', letterSpacing:'-0.01em', marginBottom:3 }}>{t('sigint.exportCsv')}</div>
                <div style={{ fontSize:11, color:'rgba(255,255,255,0.30)', fontWeight:300 }}>{t('sigint.selectRange')}</div>
              </div>
              <button onClick={() => setShowExport(false)} style={{
                background:'rgba(255,255,255,0.06)', border:'1px solid rgba(255,255,255,0.10)',
                borderRadius:8, color:'rgba(255,255,255,0.50)', fontSize:14, cursor:'pointer',
                width:28, height:28, display:'flex', alignItems:'center', justifyContent:'center',
                lineHeight:1, padding:0,
              }}>✕</button>
            </div>
            {/* Quick presets */}
            <div style={{ display:'flex', gap:6 }}>
              {[['1h',1],['6h',6],['24h',24],['7d',168]].map(([lbl, hrs]) => (
                <button key={lbl} onClick={() => {
                  const now = new Date(); const f = new Date(now - hrs*3600000);
                  setExportFrom(localIso(f)); setExportTo(localIso(now));
                }} style={{
                  flex:1, background:'rgba(0,229,255,0.07)', border:'1px solid rgba(0,229,255,0.18)',
                  borderRadius:8, color:'rgba(0,229,255,0.80)', fontSize:11, fontWeight:600,
                  padding:'7px 0', cursor:'pointer', fontFamily:SYS, letterSpacing:'0.04em',
                }}>{lbl}</button>
              ))}
            </div>
            {/* Date inputs */}
            {[[t('common.from'), exportFrom, setExportFrom],[t('common.to'), exportTo, setExportTo]].map(([label, val, setter]) => (
              <div key={label} style={{ display:'flex', flexDirection:'column', gap:6 }}>
                <span style={{ fontSize:10, fontWeight:700, letterSpacing:'0.10em', textTransform:'uppercase', color:'rgba(255,255,255,0.28)' }}>{label}</span>
                <input type="datetime-local" value={val} onChange={e => setter(e.target.value)}
                  style={{
                    width:'100%', boxSizing:'border-box',
                    background:'rgba(255,255,255,0.05)',
                    border:'1px solid rgba(0,229,255,0.15)',
                    borderRadius:10, color:'#fff', padding:'10px 14px',
                    fontSize:13, fontFamily:MONO, outline:'none', colorScheme:'dark',
                  }}
                  onFocus={e => e.target.style.borderColor='rgba(0,229,255,0.45)'}
                  onBlur={e  => e.target.style.borderColor='rgba(0,229,255,0.15)'}
                />
              </div>
            ))}
            <div style={{ height:1, background:'rgba(255,255,255,0.06)' }} />
            <button
              disabled={exporting}
              onClick={doExport}
              style={{
                background: exporting ? 'rgba(0,229,255,0.05)' : 'linear-gradient(135deg,rgba(0,229,255,0.20),rgba(74,0,224,0.20))',
                border:`1px solid ${exporting ? 'rgba(0,229,255,0.12)' : 'rgba(0,229,255,0.35)'}`,
                borderRadius:12, color: exporting ? 'rgba(0,229,255,0.35)' : CYAN,
                fontSize:13, fontWeight:700, padding:'13px', cursor: exporting ? 'default' : 'pointer',
                fontFamily:SYS, letterSpacing:'0.06em', textTransform:'uppercase',
                boxShadow: exporting ? 'none' : '0 0 20px rgba(0,229,255,0.10)',
                transition:'all 0.2s',
              }}
            >{exporting ? t('sigint.preparing') : t('sigint.downloadCsv')}</button>
          </motion.div>
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  );
}

const MobileListRow = memo(function MobileListRow({ s, p, isRed, lastUpdated, unit }) {
  const barW = `${Math.round(p * 100)}%`;
  const { r, g, b } = colorAt(p);
  const barCol = `rgb(${r},${g},${b})`;
  const ageSec = lastUpdated ? Math.floor((Date.now() - lastUpdated) / 1000) : null;
  const ageLabel = ageSec === null ? '—' : ageSec < 60 ? `${ageSec}s` : `${Math.floor(ageSec/60)}m`;
  const ageColor = ageSec === null ? 'rgba(255,255,255,0.18)'
    : ageSec < 5  ? 'rgba(0,229,255,0.70)'
    : ageSec < 15 ? 'rgba(255,175,0,0.65)'
    : 'rgba(255,255,255,0.25)';
  return (
    <div
      className={isRed ? 'sig-blink' : ''}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 44px 120px 52px',
        alignItems: 'center',
        gap: 10,
        padding: '10px 12px',
        borderRadius: 10,
        background: isRed ? 'rgba(255,87,34,0.08)' : 'rgba(255,255,255,0.03)',
        border: `1px solid ${isRed ? 'rgba(255,87,34,0.25)' : 'rgba(255,255,255,0.06)'}`,
        marginBottom: 6,
      }}
    >
      <span style={{
        fontFamily: DIGIT,
        fontSize: 22,
        fontWeight: 400,
        color: '#ff5722',
        letterSpacing: s.freq === 88.7 ? '0.01em' : '0.04em',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        textShadow: isRed ? '0 0 12px rgba(255,87,34,0.9)' : '0 0 8px rgba(255,87,34,0.45)',
      }}>
        {s.freq === 88.7 ? 'DAB+ 12A' : s.freq.toFixed(1)}
      </span>
      <span style={{
        fontFamily: MONO, fontSize: 10, fontWeight: 500,
        color: ageColor, textAlign: 'right',
      }}>
        {ageLabel}
      </span>
      <div style={{ position: 'relative', height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.07)' }}>
        <div style={{
          position: 'absolute', left: 0, top: 0, bottom: 0,
          width: barW, borderRadius: 3,
          background: barCol,
          boxShadow: `0 0 8px rgba(${r},${g},${b},0.55)`,
          transition: 'width 0.35s ease',
        }} />
      </div>
      <span style={{
        fontFamily: MONO, fontSize: 12, fontWeight: 600,
        color: isRed ? '#ff5722' : `rgb(${r},${g},${b})`,
        textAlign: 'right',
      }}>
        {displayVal(s.dbfs, unit).toFixed(0)}<span style={{ fontSize: 9, opacity: 0.55, marginLeft: 1 }}>{unitLabel(unit)}</span>
      </span>
    </div>
  );
});

// ── Desktop Bar Chart with sliding viewport ──────────────────────────────────
const SCROLL_SPEED = 3; // bars per second when hovering edge

const DesktopBarChart = memo(function DesktopBarChart({ data, barH, freqH, jitterRef, cal, currentSdrId, unit }) {
  const scrollRef = useRef(null);
  const containerRef = useRef(null);
  const [scrollDir, setScrollDir] = useState(0); // -1 left, 0 none, 1 right
  const [containerW, setContainerW] = useState(900);

  // Measure container width
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      for (const e of entries) setContainerW(e.contentRect.width);
    });
    ro.observe(el);
    setContainerW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // Auto-scroll effect when hovering edges
  useEffect(() => {
    if (scrollDir === 0) return;
    const el = scrollRef.current;
    if (!el) return;
    let raf;
    let last = performance.now();
    let lastFrame = 0;
    const FRAME_MS = 1000 / 30;
    const step = (now) => {
      raf = requestAnimationFrame(step);
      if (document.hidden) return;
      if (now - lastFrame < FRAME_MS) return;
      lastFrame = now;
      const dt = (now - last) / 1000;
      last = now;
      const barWidth = containerW / getVisibleBars();
      el.scrollLeft += scrollDir * SCROLL_SPEED * barWidth * dt;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [scrollDir, containerW]);

  const VISIBLE_BARS = getVisibleBars();
  const GAP = containerW <= 600 ? 3 : 6;
  const barW = Math.max(16, Math.floor((containerW - 24) / VISIBLE_BARS - GAP)); // 24 = padding
  const totalW = data.length * (barW + GAP) + 24; // total scrollable width

  return (
    <div ref={containerRef} style={{ flex:1, minHeight:0, position:'relative',
      background:'rgba(5,7,11,0.45)', borderRadius:20,
      border:'1px solid rgba(0,229,255,0.10)' }}>
      {/* Scroll container */}
      <div
        ref={scrollRef}
        style={{
          position:'absolute', inset:0, overflowX:'hidden', overflowY:'visible',
          borderRadius:20, paddingTop:32,
        }}
      >
        {/* ── dBFS numbers banner — fixed row above all bars ── */}
        <div style={{
          position:'absolute', top:0, left:0, right:0, height:32,
          display:'flex', alignItems:'center',
          background:'rgba(5,7,11,0.80)',
          borderBottom:'1px solid rgba(0,229,255,0.12)',
          borderRadius:'20px 20px 0 0',
          padding:'0 12px', gap: GAP,
          justifyContent: totalW <= containerW ? 'center' : 'flex-start',
          width: totalW > containerW ? totalW : '100%',
          zIndex:40, pointerEvents:'none',
        }}>
          {data.map(s => {
            const f2 = s.freq.toFixed(1);
            const jitter2 = jitterRef.current[f2] || 0;
            const vDbfs2 = Math.max(cal.min, Math.min(cal.max, s.dbfs + jitter2));
            const isJammed2   = currentSdrId === 'site_1' && s.jammed === true;
            const isRed2 = isJammed2 ? true : s.dbfs < cal.red;
            return (
              <div key={`lbl-${f2}`} style={{
                flex:`0 0 ${barW}px`, minWidth:0,
                textAlign:'center',
                fontFamily:MONO, fontSize:13, fontWeight:700, lineHeight:'1',
                color: isRed2 ? '#ff5722' : CYAN,
                textShadow: isRed2 ? '0 0 10px rgba(255,87,34,1)' : `0 0 10px ${CYAN}`,
                letterSpacing:'0.02em',
              }}>{displayVal(s.dbfs, unit).toFixed(0)}</div>
            );
          })}
        </div>

        <div style={{
          display:'flex', alignItems:'flex-end', height:'100%',
          width: totalW > containerW ? totalW : '100%',
          padding:'0 12px',
          gap: GAP,
          justifyContent: totalW <= containerW ? 'center' : 'flex-start',
        }}>
          {data.map(s => {
            const f = s.freq.toFixed(1);
            const jitter = jitterRef.current[f] || 0;
            const visualDbfs = Math.max(cal.min, Math.min(cal.max, s.dbfs + jitter));
            const p = pct(visualDbfs, cal.min, cal.max);
            const isJammed   = currentSdrId === 'site_1' && s.jammed === true;
            const isRed = isJammed ? true : s.dbfs < cal.red;
            const SEG_GAP = 3;
            const SEG_H   = 6;
            const TOTAL_SEG = Math.floor(barH / (SEG_H + SEG_GAP));
            const litCount  = Math.round(p * TOTAL_SEG);
            const threshSeg = Math.round(thresholdPct(cal.min, cal.max, cal.red) * TOTAL_SEG);
            const greenThreshSeg = Math.round(greenThresholdPct(cal.min, cal.max, cal.green) * TOTAL_SEG);
            return (
              <div key={`${s.name}-${f}`} style={{ display:'flex', flexDirection:'column',
                alignItems:'center', flex:`0 0 ${barW}px`, minWidth:0, gap:0 }}>
                <div style={{ width:'100%', height:barH, flexShrink:0, position:'relative',
                  display:'flex', flexDirection:'column-reverse', gap: SEG_GAP,
                  alignItems:'stretch', justifyContent:'flex-start' }}>
                  <LEDColumn
                    litCount={litCount}
                    totalSeg={TOTAL_SEG}
                    threshSeg={threshSeg}
                    greenThreshSeg={greenThreshSeg}
                    segH={SEG_H}
                    segGap={SEG_GAP}
                    isRed={isRed}
                    inverted={false}
                  />
                  {isJammed && (
                    <div className="sig-blink" style={{
                      position:'absolute', inset:0, zIndex:10,
                      background:'repeating-linear-gradient(45deg,rgba(255,0,0,0.18) 0px,rgba(255,0,0,0.18) 6px,transparent 6px,transparent 12px)',
                      borderRadius:4, pointerEvents:'none',
                      boxShadow:'inset 0 0 20px rgba(255,0,0,0.35)',
                    }} />
                  )}
                </div>
                {(() => {
                  const glowStr  = 4  + p * 20;
                  const glowStr2 = 10 + p * 40;
                  const labelShadow = isRed
                    ? `0 0 8px rgba(255,87,34,1), 0 0 20px rgba(255,87,34,0.8), 0 0 40px rgba(255,87,34,0.5)`
                    : `0 0 ${glowStr}px rgba(255,87,34,0.9), 0 0 ${glowStr2}px rgba(255,87,34,${(0.2 + p * 0.4).toFixed(2)})`;
                  const dimShadow = isRed
                    ? `0 0 6px rgba(255,87,34,0.7)`
                    : `0 0 6px rgba(255,87,34,${(0.15 + p * 0.3).toFixed(2)})`;
                  return (
                    <div
                      className={isRed ? 'sig-blink' : ''}
                      style={{
                        flexShrink:0, width:'100%',
                        height: freqH,
                        display:'flex', flexDirection:'row',
                        alignItems:'stretch', justifyContent:'flex-start',
                        overflow:'hidden',
                        opacity: isRed ? undefined : 0.5 + p * 0.5,
                      }}>
                      <div style={{
                        fontFamily: DIGIT, fontSize: Math.min(34, barW * 0.75), fontWeight:400,
                        color: isJammed ? '#ff0000' : '#ff5722',
                        letterSpacing:'0.04em',
                        whiteSpace:'nowrap', lineHeight:'1',
                        writingMode:'vertical-rl', transform:'rotate(180deg)',
                        textShadow: isJammed ? '0 0 12px rgba(255,0,0,1),0 0 30px rgba(255,0,0,0.7)' : labelShadow,
                        display:'flex', alignItems:'center', justifyContent:'center',
                        flex:1, minWidth:0, overflow:'hidden',
                      }}>
                        {isJammed ? 'JAMMED' : s.name}
                      </div>
                      {!s.dab && (
                        <div style={{
                          fontFamily: DIGIT, fontSize: Math.min(21, barW * 0.48), fontWeight:400,
                          color: isRed ? 'rgba(255,87,34,0.95)' : 'rgba(255,87,34,0.60)',
                          letterSpacing:'0.03em',
                          whiteSpace:'nowrap', lineHeight:'1',
                          writingMode:'vertical-rl', transform:'rotate(180deg)',
                          textShadow: dimShadow,
                          display:'flex', alignItems:'center', justifyContent:'center',
                          flexShrink:0,
                        }}>
                          {s.freq.toFixed(1)}
                        </div>
                      )}
                    </div>
                  );
                })()}
              </div>
            );
          })}
        </div>
      </div>

      {/* Left hover zone — scroll left */}
      <div
        onMouseEnter={() => setScrollDir(-1)}
        onMouseLeave={() => setScrollDir(0)}
        style={{
          position:'absolute', left:0, top:0, bottom:0, width:60,
          zIndex:5, cursor:'w-resize',
          background:'linear-gradient(to right, rgba(0,229,255,0.06), transparent)',
          borderRadius:'20px 0 0 20px',
          display:'flex', alignItems:'center', justifyContent:'center',
          opacity: scrollDir === -1 ? 1 : 0.4,
          transition:'opacity 0.2s',
        }}
      >
        <span style={{ fontFamily:SYS, fontSize:20, color:'rgba(0,229,255,0.5)', pointerEvents:'none' }}>◀</span>
      </div>

      {/* Right hover zone — scroll right */}
      <div
        onMouseEnter={() => setScrollDir(1)}
        onMouseLeave={() => setScrollDir(0)}
        style={{
          position:'absolute', right:0, top:0, bottom:0, width:60,
          zIndex:5, cursor:'e-resize',
          background:'linear-gradient(to left, rgba(0,229,255,0.06), transparent)',
          borderRadius:'0 20px 20px 0',
          display:'flex', alignItems:'center', justifyContent:'center',
          opacity: scrollDir === 1 ? 1 : 0.4,
          transition:'opacity 0.2s',
        }}
      >
        <span style={{ fontFamily:SYS, fontSize:20, color:'rgba(0,229,255,0.5)', pointerEvents:'none' }}>▶</span>
      </div>

      {/* Scroll position indicator */}
      {data.length > VISIBLE_BARS && (
        <div style={{
          position:'absolute', bottom:6, left:'10%', right:'10%', height:6,
          borderRadius:3, zIndex:6,
        }}>
          <ScrollIndicator scrollRef={scrollRef} totalItems={data.length} visibleItems={VISIBLE_BARS} />
        </div>
      )}
    </div>
  );
});

// Interactive scroll indicator — click track or drag thumb to scroll bars
function ScrollIndicator({ scrollRef, totalItems, visibleItems }) {
  const [pos, setPos] = useState(0);
  const trackRef = useRef(null);
  const dragging = useRef(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const update = () => {
      const max = el.scrollWidth - el.clientWidth;
      setPos(max > 0 ? el.scrollLeft / max : 0);
    };
    el.addEventListener('scroll', update);
    update();
    return () => el.removeEventListener('scroll', update);
  }, [scrollRef, totalItems]);

  const thumbW = Math.max(10, (visibleItems / totalItems) * 100);

  const scrollToFraction = useCallback((frac) => {
    const el = scrollRef.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    el.scrollLeft = Math.max(0, Math.min(max, frac * max));
  }, [scrollRef]);

  const getFractionFromEvent = useCallback((e) => {
    const track = trackRef.current;
    if (!track) return 0;
    const rect = track.getBoundingClientRect();
    const x = (e.clientX || e.touches?.[0]?.clientX || 0) - rect.left;
    const thumbPx = (thumbW / 100) * rect.width;
    const usable = rect.width - thumbPx;
    return usable > 0 ? Math.max(0, Math.min(1, (x - thumbPx / 2) / usable)) : 0;
  }, [thumbW]);

  const onPointerDown = useCallback((e) => {
    e.preventDefault();
    dragging.current = true;
    const frac = getFractionFromEvent(e);
    scrollToFraction(frac);

    const onMove = (ev) => {
      if (!dragging.current) return;
      ev.preventDefault();
      scrollToFraction(getFractionFromEvent(ev));
    };
    const onUp = () => {
      dragging.current = false;
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
  }, [getFractionFromEvent, scrollToFraction]);

  return (
    <div
      ref={trackRef}
      onPointerDown={onPointerDown}
      style={{
        position:'absolute', top:-4, bottom:-4, left:0, right:0,
        cursor:'pointer', touchAction:'none',
      }}
    >
      <div style={{
        position:'absolute', top:4, bottom:4, left:0, right:0,
        background:'rgba(255,255,255,0.05)', borderRadius:2,
      }} />
      <div style={{
        position:'absolute', top:2, bottom:2,
        left:`${pos * (100 - thumbW)}%`,
        width:`${thumbW}%`,
        background: dragging.current ? 'rgba(0,229,255,0.55)' : 'rgba(0,229,255,0.35)',
        borderRadius:3,
        transition: dragging.current ? 'none' : 'left 0.1s ease-out',
        cursor:'grab',
      }} />
    </div>
  );
}

export default memo(function SigintDashboard({ hidden, peaks = [], forceMobile = false, open: openProp, onOpenChange, menuAction, onMenuAction, selectedSdrId, selectedRtys, sdrRtysMap, topOffset = 52, sdrNodes }) {
  const { t } = useTranslation();
  const [internalOpen, setInternalOpen] = useState(forceMobile);
  const open = openProp !== undefined ? openProp : internalOpen;
  const setOpen = useCallback((v) => {
    if (openProp === undefined) setInternalOpen(v);
    onOpenChange?.(v);
  }, [openProp, onOpenChange]);
  const [mobileTab, setMobileTab] = useState('dashboard');
  const [data,     setData]     = useState(() => initData(DEFAULT_STATIONS));
  const [barH,     setBarH]     = useState(getBarH);
  const [freqH,    setFreqH]    = useState(() => FREQ_LABEL_H());
  const [redCount, setRedCount] = useState(0);
  const [contentReady, setContentReady] = useState(false);
  const [tick, setTick] = useState(0); // Force re-render for jitter animation
  const [unit, setUnit] = useState('dbm'); // 'dbm' (raw dBFS) | 'dbuv' (+107 dB)
  // Track which stations are currently below threshold to avoid duplicate logs
  const belowRef = useRef(new Set());
  const fakeAlarmRef = useRef(null); // { freq, expiry }
  
  // Track last update time and visual jitter for stale stations
  const lastUpdateRef = useRef({}); // { freq: timestamp }
  const jitterRef = useRef({}); // { freq: jitterOffset }
  // Persistent power cache — survives RTYS tab switches; keyed "sdrId:freq"
  const freqPowerCache = useRef({}); // { "site_1:87.7": dbfs }
  // Track last SQL timestamp per freq to detect truly new data
  const freqTsCache = useRef({}); // { "sdrId:freq": ts (epoch seconds) }

  // ── SDR navigation — driven by props from TopBar ─────────────────────────
  const [sdrPeaks,   setSdrPeaks]   = useState(null);
  const [sdrApiInfo, setSdrApiInfo] = useState(null); // thresholds + config for current SDR

  const currentSdrId    = selectedSdrId || 'site_1';
  const currentSdrLabel = (sdrRtysMap?.[currentSdrId]?.label) || currentSdrId;
  // Offline detection from telemetry: undefined means not yet loaded, assume online
  const sdrOnlineMap = Object.fromEntries((sdrNodes || []).map(n => [n.id, n.online]));
  const isSdrOffline = sdrOnlineMap[currentSdrId] === false;

  // Fetch SDR config/thresholds whenever SDR changes
  useEffect(() => {
    let cancelled = false;
    const fetchInfo = async () => {
      try {
        const res = await fetch('/api/sdr/list');
        if (!res.ok || cancelled) return;
        const list = await res.json();
        if (!cancelled) setSdrApiInfo(list.find(s => s.id === currentSdrId) || null);
      } catch { /* ignore */ }
    };
    fetchInfo();
  }, [currentSdrId]);

  const currentSdrApi = sdrApiInfo;

  // Stations for current SDR + RTYS filter
  const STATIONS = useMemo(() => {
    const rtysData = sdrRtysMap?.[currentSdrId];
    if (rtysData && selectedRtys && rtysData.stations?.[selectedRtys]) {
      const entries = rtysData.stations[selectedRtys]; // [[freq, name], ...]
      return entries.map(([f, stName]) => ({
        freq: f,
        name: stName || (f === 223.936 ? 'DAB+' : `${f.toFixed(1)}`),
        ...(f === 223.936 ? { dab: true } : {}),
      }));
    }
    if (currentSdrApi?.stations?.length) return buildStationsFromConfig(currentSdrApi.stations);
    return DEFAULT_STATIONS;
  }, [currentSdrId, selectedRtys, sdrRtysMap, currentSdrApi]);

  // Real calibration derived from actual SQL power_dbfs history for the current
  // SDR (sdrApiInfo.thresholds, computed by analyze_thresholds.py / served by
  // /api/sdr/list). Falls back to a generic range only while thresholds are
  // still loading or missing for this SDR.
  const cal = useMemo(() => {
    const th = currentSdrApi?.thresholds;
    if (th) {
      const base = th.rtys?.[selectedRtys] || th;
      return {
        min:   base.db_min,
        max:   base.db_max,
        red:   base.signal_threshold,
        green: base.strong_signal,
      };
    }
    return {
      min: DBFS_MIN,
      max: DBFS_MAX,
      red: RED_THRESHOLD_DBFS,
      green: GREEN_THRESHOLD_DBFS,
    };
  }, [currentSdrApi, selectedRtys]);

  const REFRESH_SEC = 10;
  const [countdown, setCountdown] = useState(REFRESH_SEC);

  // Fetch peaks every 10s; countdown ticks every 1s
  useEffect(() => {
    if (!open) { setSdrPeaks(null); return; }
    if (!currentSdrId) { setSdrPeaks(null); return; }
    let cancelled = false;
    const doFetch = async () => {
      try {
        const rtysFreqs = STATIONS.map(s => s.dab === true ? DAB_SQL_FREQ_MHZ.toFixed(3) : s.freq.toFixed(1)).join(',');
        const base = `/api/sdr/${encodeURIComponent(currentSdrId)}/peaks`;
        const url = rtysFreqs ? `${base}?freqs=${rtysFreqs}` : base;
        const res = await fetch(url);
        if (!res.ok || cancelled) return;
        const json = await res.json();
        if (!cancelled) { setSdrPeaks(json.peaks || []); setCountdown(REFRESH_SEC); }
      } catch { /* ignore */ }
    };
    doFetch();
    const fetchId = setInterval(doFetch, REFRESH_SEC * 1000);
    const tickId  = setInterval(() => setCountdown(c => Math.max(0, c - 1)), 1000);
    return () => { cancelled = true; clearInterval(fetchId); clearInterval(tickId); };
  }, [currentSdrId, STATIONS, open]);

  // Use only REST peaks — WebSocket peaks (props.peaks) only contain BTQK freqs
  const effectivePeaks = sdrPeaks;

  function applyFakeAlarm(arr) {
    if (!fakeAlarmRef.current) return arr;
    if (Date.now() > fakeAlarmRef.current.expiry) {
      fakeAlarmRef.current = null;
      return arr;
    }
    return arr.map(s => {
      if (Math.abs(s.freq - fakeAlarmRef.current.freq) < 0.05) {
        return { ...s, dbfs: -90 };
      }
      return s;
    });
  }

  useEffect(() => { if (hidden && !forceMobile) setOpen(false); }, [hidden, forceMobile]);
  useEffect(() => { if (forceMobile) setOpen(true); }, [forceMobile]);

  // Defer heavy chart mount until first open — keep mounted forever after
  useEffect(() => {
    if (open && !contentReady) {
      const t = setTimeout(() => setContentReady(true), 300);
      return () => clearTimeout(t);
    }
  }, [open]);

  useEffect(() => {
    const onResize = () => { setBarH(getBarH()); setFreqH(FREQ_LABEL_H()); };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Build a lookup from freq -> station name
  const stationNames = useMemo(() => {
    const map = {};
    STATIONS.forEach(s => { map[s.freq.toFixed(1)] = s.name; });
    return map;
  }, [STATIONS]);


  // Update data from real SDR peaks — always show exactly the known stations for current SDR
  useEffect(() => {
    // Build a lookup: freq (1 decimal) -> real power from SDR
    const peakMap = {};
    const peakTsMap = {};
    const jammedMap = {};
    // freshMap: only freqs with a NEWER sql timestamp than last poll
    const freshMap = {};
    if (effectivePeaks && effectivePeaks.length > 0) {
      effectivePeaks.forEach(p => {
        const f = parseFloat(p.freq).toFixed(1);
        const raw = parseFloat(p.dbfs);
        const clamped = Math.max(cal.min, Math.min(cal.max, raw));
        const tsKey = `${currentSdrId}:${f}`;
        const prevTs = freqTsCache.current[tsKey];
        const newTs = p.ts ?? null;
        // Only treat as fresh if timestamp is new (or first time)
        const isFresh = newTs !== null && (prevTs === undefined || newTs > prevTs);
        if (isFresh) {
          freshMap[f] = clamped;
          freqPowerCache.current[tsKey] = raw;
          freqTsCache.current[tsKey] = newTs;
          peakMap[f] = clamped;
        }
        if (p.ts != null) peakTsMap[f] = p.ts * 1000;
        if (p.jammed && currentSdrId === 'site_1') jammedMap[f] = true;
      });
    }

    setData(prevData => {
      const prevMap = {};
      prevData.forEach(s => { prevMap[s.freq.toFixed(1)] = s.dbfs; });

      let realData = STATIONS.map(s => {
        const f = s.freq.toFixed(1);
        const peakKey = s.dab ? DAB_PEAK_KEY : f;
        const cacheKey = `${currentSdrId}:${f}`;
        const prev = prevMap[f];
        const coldDefault = (cal.min + cal.max) / 2;
        const startVal = prev !== undefined ? prev : (freqPowerCache.current[cacheKey] ?? coldDefault);

        // Show the latest SQL value directly; only freeze when no fresh data yet
        const dbfs = freshMap[peakKey] !== undefined
          ? freshMap[peakKey]
          : startVal; // no new data → freeze at current value

        if (freshMap[peakKey] !== undefined) {
          if (peakTsMap[peakKey] !== undefined) lastUpdateRef.current[f] = peakTsMap[peakKey];
          jitterRef.current[f] = 0;
        }

        return { ...s, dbfs, jammed: jammedMap[peakKey] || false };
      });
      realData = applyFakeAlarm(realData);

      const below = realData.filter(s => s.dbfs < cal.red).length;
      setRedCount(below);

      return realData;
    });
  }, [effectivePeaks, STATIONS, cal, currentSdrId]);

  useEffect(() => {
    // Interval for stale station jitter — updates refs only, no re-render needed
    // The per-bar rAF loop reads jitterRef so visual updates happen automatically
    const jitterId = setInterval(() => {
      if (!effectivePeaks || effectivePeaks.length === 0) return;
      const now = Date.now();
      const STALE_MS = 1500;
      STATIONS.forEach(s => {
        const f = s.freq.toFixed(1);
        const lastUpdate = lastUpdateRef.current[f] || 0;
        if (now - lastUpdate > STALE_MS) {
          jitterRef.current[f] = (Math.random() * 0.2 + 0.1) * (Math.random() > 0.5 ? 1 : -1);
        } else {
          jitterRef.current[f] = 0;
        }
      });
    }, 800);
    return () => clearInterval(jitterId);
  }, [effectivePeaks, STATIONS]);

  useEffect(() => {
    // 1-second tick to keep mobile age counter updating second-by-second
    if (!forceMobile) return;
    const ageId = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(ageId);
  }, [forceMobile]);

  const driftSignRef = useRef({}); // alternates +1/-1 per freq to prevent accumulation
  useEffect(() => {
    // Cosmetic oscillation only — alternates sign each tick so bars cannot drift up or down
    const id = setInterval(() => {
      setData(prev => prev.map(s => {
        const f = s.freq.toFixed(1);
        const sign = driftSignRef.current[f] = -(driftSignRef.current[f] || 1);
        const drift = sign * (0.1 + Math.random() * 0.2); // 0.1–0.3 dBFS, alternating
        return { ...s, dbfs: Math.max(cal.min, Math.min(cal.max, s.dbfs + drift)) };
      }));
    }, 500);
    return () => clearInterval(id);
  }, [cal]);

  const avg       = (data.reduce((a, s) => a + s.dbfs, 0) / data.length).toFixed(1);
  const hotCount  = data.filter(s => s.dbfs > cal.green).length;
  const thY       = (1 - thresholdPct(cal.min, cal.max, cal.red)) * barH; // px from top of track

  const openLogs = useCallback(() => {
    fetch('http://127.0.0.1:8000/open-logs').catch(()=>{});
  }, []);

  const triggerFakeAlarm = useCallback(() => {
    const s = STATIONS[Math.floor(Math.random() * STATIONS.length)];
    fakeAlarmRef.current = { freq: s.freq, expiry: Date.now() + 3000 };
    setData(prev => applyFakeAlarm(prev));
    fetch('http://127.0.0.1:8000/fakealarm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ freq: s.freq, name: s.name }),
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!menuAction) return;
    if (menuAction.type === 'logs') openLogs();
    else if (menuAction.type === 'alarm') triggerFakeAlarm();
    onMenuAction?.();
  }, [menuAction]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <AnimatePresence>
        {open && (
          <motion.div
            key="dash-panel"
            initial={{ x:'-100%' }}
            animate={{ x:0 }}
            exit={{ x:'-100%' }}
            transition={PANEL_SPRING}
            style={{
              position:'fixed', top:topOffset, left:0, right:0, bottom:0,
              zIndex:60, pointerEvents:'auto',
              background: forceMobile ? '#0a0e17' : 'rgba(10,14,23,0.92)',
              borderRight:'1px solid rgba(0,229,255,0.15)',
              boxShadow:'0 8px 32px 0 rgba(0,0,0,0.5)',
              display:'flex', flexDirection:'column',
              padding: forceMobile ? '16px 10px 10px' : '14px clamp(12px, 3vw, 44px) 14px',
              gap: forceMobile ? 18 : 'clamp(8px, 1.5vh, 18px)',
              willChange:'transform',
              filter: isSdrOffline ? 'grayscale(1) brightness(0.55)' : 'none',
              transition: 'filter 0.4s ease',
            }}
          >
            {/* Offline warning banner */}
            {isSdrOffline && (
              <div style={{
                flexShrink:0, alignSelf:'center',
                display:'flex', alignItems:'center', gap:8,
                padding:'5px 14px', borderRadius:6,
                background:'rgba(255,82,82,0.12)', border:'1px solid rgba(255,82,82,0.25)',
              }}>
                <span style={{ width:7, height:7, borderRadius:'50%', background:'#ff5252', boxShadow:'0 0 8px #ff5252' }} />
                <span style={{ fontFamily:MONO, fontSize:11, fontWeight:700, color:'rgba(255,255,255,0.85)', letterSpacing:'0.08em', textTransform:'uppercase' }}>SDR Offline — No live data</span>
              </div>
            )}

            {/* ── Header ── */}
            {forceMobile ? (
              <div style={{ display:'flex', flexDirection:'column', alignItems:'center', flexShrink:0, gap:6 }}>
                <div style={{ display:'flex', alignItems:'center', gap:16 }}>
                  <motion.span
                    animate={{ opacity:[1,0.2,1] }}
                    transition={{ duration:2.2, repeat:Infinity, ease:'easeInOut' }}
                    style={{ width:7, height:7, borderRadius:'50%', background:CYAN,
                      boxShadow:`0 0 8px ${CYAN}80`, display:'inline-block', flexShrink:0 }}
                  />
                  <UnitToggle unit={unit} onChange={setUnit} />
                  <Stat label={t('sigint.avg')} value={`${avg}`} accent="rgba(255,255,255,0.70)" />
                  <Stat label={t('sigint.active')} value={hotCount} accent={CYAN} glow />
                  <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:2 }}>
                    <div style={{ display:'flex', alignItems:'center', gap:4 }}>
                      <motion.span
                        animate={ redCount > 0 ? { opacity:[1,0.3,1] } : { opacity:0.3 }}
                        transition={{ duration:0.9, repeat: redCount > 0 ? Infinity : 0 }}
                        style={{ width:6, height:6, borderRadius:'50%',
                          background: redCount > 0 ? RED : 'rgba(255,87,34,0.4)',
                          boxShadow: redCount > 0 ? `0 0 8px ${RED}` : 'none',
                          display:'inline-block', flexShrink:0 }}
                      />
                      <span style={{ fontFamily:SYS, fontSize:18, fontWeight:600,
                        color: redCount > 0 ? '#ff5722' : 'rgba(255,87,34,0.35)',
                        letterSpacing:'-0.02em', lineHeight:'1' }}>{redCount}</span>
                    </div>
                    <span style={{ fontFamily:SYS, fontSize:8, letterSpacing:'0.05em',
                      fontWeight:500, color:'rgba(255,87,34,0.50)',
                      textTransform:'uppercase', whiteSpace:'nowrap' }}>{t('sigint.below45')}</span>
                  </div>
                </div>
                <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                  <div style={{ width:80, height:3, borderRadius:2, background:'rgba(255,255,255,0.08)', overflow:'hidden' }}>
                    <motion.div
                      style={{ height:'100%', borderRadius:2,
                        background: countdown <= 3 ? '#ff5722' : CYAN,
                        boxShadow: `0 0 6px ${countdown <= 3 ? '#ff5722' : CYAN}88`,
                      }}
                      animate={{ width: `${(countdown / REFRESH_SEC) * 100}%` }}
                      transition={{ duration: 0.4, ease: 'linear' }}
                    />
                  </div>
                  <span style={{ fontFamily:MONO, fontSize:9, fontWeight:700,
                    color: countdown <= 3 ? '#ff5722' : 'rgba(0,229,255,0.55)',
                    letterSpacing:'0.04em' }}>{countdown}s</span>
                </div>
              </div>
            ) : (
              <div style={{ display:'grid', gridTemplateColumns:'1fr auto 1fr',
                alignItems:'center', flexShrink:0, gap:'8px 16px' }}>
                <div style={{ display:'flex', alignItems:'center', gap:24 }}>
                  <Stat label={t('sigint.avgSignal')} value={`${displayVal(parseFloat(avg), unit).toFixed(1)} ${unitLabel(unit)}`} accent="rgba(255,255,255,0.70)" />
                  <Stat label={t('sigint.active')}     value={hotCount}     accent={CYAN} glow />
                  <div onClick={openLogs} style={{ cursor:'pointer', display:'flex',
                    flexDirection:'column', alignItems:'center', gap:3,
                    padding:'6px 10px', borderRadius:8,
                    background: redCount > 0 ? 'rgba(255,87,34,0.08)' : 'transparent',
                    border: redCount > 0 ? '1px solid rgba(255,87,34,0.20)' : '1px solid transparent',
                    transition:'all 0.3s' }}>
                    <div style={{ display:'flex', alignItems:'center', gap:5 }}>
                      <motion.span
                        animate={ redCount > 0 ? { opacity:[1,0.3,1] } : { opacity:0.3 }}
                        transition={{ duration:0.9, repeat: redCount > 0 ? Infinity : 0 }}
                        style={{ width:6, height:6, borderRadius:'50%',
                          background: redCount > 0 ? RED : 'rgba(255,87,34,0.4)',
                          boxShadow: redCount > 0 ? `0 0 8px ${RED}` : 'none',
                          display:'inline-block', flexShrink:0 }}
                      />
                      <span style={{ fontFamily:SYS, fontSize:20, fontWeight:600,
                        color: redCount > 0 ? '#ff5722' : 'rgba(255,87,34,0.35)',
                        letterSpacing:'-0.02em', lineHeight:'1' }}>{redCount}</span>
                    </div>
                    <span style={{ fontFamily:SYS, fontSize:9, letterSpacing:'0.05em',
                      fontWeight:500, color:'rgba(255,87,34,0.50)',
                      textTransform:'uppercase', whiteSpace:'nowrap' }}>{t('sigint.below45')}</span>
                  </div>
                </div>

                <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:4, justifySelf:'center' }}>
                  <span style={{
                    fontFamily:MONO, fontSize:'clamp(11px, 1.2vw, 13px)', fontWeight:700, color:CYAN,
                    letterSpacing:'0.06em', textTransform:'uppercase',
                    textShadow:`0 0 10px ${CYAN}55`,
                  }}>{currentSdrLabel}</span>
                  {selectedRtys && (
                    <span style={{
                      fontFamily:MONO, fontSize:9, color:'rgba(255,255,255,0.30)',
                      letterSpacing:'0.04em',
                    }}>{selectedRtys}</span>
                  )}
                  <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                    <div style={{ width:60, height:3, borderRadius:2, background:'rgba(255,255,255,0.08)', overflow:'hidden' }}>
                      <motion.div
                        style={{ height:'100%', borderRadius:2,
                          background: countdown <= 3 ? '#ff5722' : CYAN,
                          boxShadow: `0 0 6px ${countdown <= 3 ? '#ff5722' : CYAN}88`,
                        }}
                        animate={{ width: `${(countdown / REFRESH_SEC) * 100}%` }}
                        transition={{ duration: 0.4, ease: 'linear' }}
                      />
                    </div>
                    <span style={{ fontFamily:MONO, fontSize:9, fontWeight:700,
                      color: countdown <= 3 ? '#ff5722' : 'rgba(0,229,255,0.55)',
                      letterSpacing:'0.04em', minWidth:14, textAlign:'right' }}>{countdown}s</span>
                  </div>
                </div>

                <div style={{ display:'flex', alignItems:'center', justifyContent:'flex-end', justifySelf:'end', gap: 12 }}>
                  <UnitToggle unit={unit} onChange={setUnit} />
                  <motion.button
                    whileHover={{ opacity:1, scale:1.08 }} whileTap={{ scale:0.94 }}
                    onClick={() => setOpen(false)}
                    style={{ background:'rgba(255,255,255,0.06)', border:'1px solid rgba(255,255,255,0.09)',
                      borderRadius:10, cursor:'pointer',
                      color:'rgba(255,255,255,0.40)', fontSize:18, lineHeight:1,
                      padding:'6px 12px', fontFamily:SYS }}
                  >×</motion.button>
                </div>
              </div>
            )}

            {/* ── Divider ── */}
            <div style={{ height:1, flexShrink:0,
              background:'rgba(255,255,255,0.06)' }} />

            {/* ── Tab content area — deferred until slide-in finishes ── */}
            {contentReady && forceMobile && (
            <div style={{ flex:1, minHeight:0, position:'relative', overflow:'hidden' }}>
              <AnimatePresence mode="wait" initial={false}>
                {mobileTab === 'dashboard' ? (
                  <motion.div
                    key="tab-dashboard"
                    initial={{ opacity:0, x:-24 }}
                    animate={{ opacity:1, x:0 }}
                    exit={{ opacity:0, x:-24 }}
                    transition={{ duration:0.22, ease:'easeOut' }}
                    style={{ position:'absolute', inset:0, overflowY:'auto', padding:'4px 2px' }}
                  >
                    <div style={{
                      display:'grid', gridTemplateColumns:'1fr 44px 120px 52px',
                      gap:10, padding:'0 12px 6px',
                      fontFamily:SYS, fontSize:10, fontWeight:600,
                      color:'rgba(255,255,255,0.22)', letterSpacing:'0.08em', textTransform:'uppercase',
                    }}>
                      <span>{t('sigint.station')}</span>
                      <span style={{ textAlign:'right' }}>{t('sigint.age')}</span>
                      <span style={{ textAlign:'center' }}>{t('sigint.level')}</span>
                      <span style={{ textAlign:'right' }}>{t('sigint.dbfs')}</span>
                    </div>
                    {data.map(s => {
                      const f = s.freq.toFixed(1);
                      const jitter = jitterRef.current[f] || 0;
                      const visualDbfs = Math.max(cal.min, Math.min(cal.max, s.dbfs + jitter));
                      const p = pct(visualDbfs, cal.min, cal.max);
                      const isRed = s.dbfs < cal.red;
                      return <MobileListRow key={s.name} s={s} p={p} isRed={isRed} lastUpdated={lastUpdateRef.current[f]} unit={unit} />;
                    })}
                  </motion.div>
                ) : (
                  <motion.div
                    key="tab-history"
                    initial={{ opacity:0, x:24 }}
                    animate={{ opacity:1, x:0 }}
                    exit={{ opacity:0, x:24 }}
                    transition={{ duration:0.22, ease:'easeOut' }}
                    style={{ position:'absolute', inset:0, display:'flex', flexDirection:'column' }}
                  >
                    <MobileHistoryTab />
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
            )}

            {/* ── Bottom tab bar (mobile only) ── */}
            {forceMobile && (
            <div style={{
              flexShrink:0, display:'flex',
              borderTop:'1px solid rgba(255,255,255,0.07)',
              paddingTop:8, paddingBottom: 'env(safe-area-inset-bottom, 8px)',
              gap:0,
            }}>
              {[{ id:'dashboard', label:t('sigint.dashboard') }, { id:'history', label:t('sigint.history') }].map(tab => {
                const active = mobileTab === tab.id;
                return (
                  <motion.button
                    key={tab.id}
                    whileTap={{ scale:0.94 }}
                    onClick={() => setMobileTab(tab.id)}
                    style={{
                      flex:1, background:'none', border:'none', cursor:'pointer',
                      display:'flex', flexDirection:'column', alignItems:'center', gap:4,
                      padding:'8px 0',
                      position:'relative',
                    }}
                  >
                    {active && (
                      <motion.div
                        layoutId="tab-indicator"
                        style={{
                          position:'absolute', top:0, left:'20%', right:'20%',
                          height:2, borderRadius:2,
                          background: CYAN,
                          boxShadow:`0 0 8px ${CYAN}`,
                        }}
                        transition={{ type:'spring', stiffness:400, damping:30 }}
                      />
                    )}
                    <span style={{
                      fontFamily:SYS, fontSize:12, fontWeight: active ? 600 : 400,
                      color: active ? CYAN : 'rgba(255,255,255,0.35)',
                      letterSpacing:'0.02em',
                      transition:'color 0.18s',
                    }}>{tab.label}</span>
                  </motion.button>
                );
              })}
            </div>
            )}

            {contentReady && !forceMobile && (
            <DesktopBarChart
              data={data}
              barH={barH}
              freqH={freqH}
              jitterRef={jitterRef}
              lastUpdateRef={lastUpdateRef}
              STATIONS={STATIONS}
              cal={cal}
              currentSdrId={currentSdrId}
              unit={unit}
            />
            )}
          </motion.div>
        )}
      </AnimatePresence>

    </>
  );
});
