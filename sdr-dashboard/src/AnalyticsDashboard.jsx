import React, { useState, useMemo, useEffect, useRef, useCallback, memo } from 'react';
import OverviewDashboard from './OverviewDashboard';
import { motion } from 'framer-motion';
import { useTranslation } from './i18n/LanguageContext';

// ── Design tokens ─────────────────────────────────────────────────────────────
const SYS   = `'Space Grotesk', sans-serif`;
const MONO  = `'JetBrains Mono', monospace`;
const CYAN  = '#00e5ff';
const AMBER = '#ffaf00';
const PURPLE = '#a855f7';
const RED   = '#ff5722';
const GREEN = '#69ff47';

const GLASS = {
  background: 'rgba(8,12,22,0.92)',
  border: '1px solid rgba(0,229,255,0.12)',
  borderRadius: 16,
  backdropFilter: 'blur(20px)',
  boxShadow: '0 8px 48px rgba(0,0,0,0.8)',
};

// ── Session cache for spectrum endpoints ────────────────────────────────────────
const _spectrumCache = new Map();
const _getSpectrumCached = (key, ttl) => {
  const entry = _spectrumCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.ts > ttl) return null;
  return entry.data;
};
const _setSpectrumCached = (key, data) => _spectrumCache.set(key, { data, ts: Date.now() });

// ── Shared keyframes ──────────────────────────────────────────────────────────
if (typeof document !== 'undefined' && !document.getElementById('analytics-style')) {
  const s = document.createElement('style');
  s.id = 'analytics-style';
  s.textContent = `
    @keyframes anlFadeUp { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:translateY(0); } }
    @keyframes anlBarGrow { from { transform:scaleY(0); } to { transform:scaleY(1); } }
    @keyframes anlPulse { 0%,100% { opacity:0.6; } 50% { opacity:1; } }
    @keyframes anlSpin { from { transform:rotate(0deg); } to { transform:rotate(360deg); } }
    @keyframes anlShimmer {
      0% { background-position: -200% 0; }
      100% { background-position: 200% 0; }
    }
    .anl-fade-up { animation: anlFadeUp 0.4s cubic-bezier(.16,1,.3,1) both; }
    .anl-bar { transform-origin: bottom; animation: anlBarGrow 0.6s cubic-bezier(.16,1,.3,1) both; }
    .anl-spin { animation: anlSpin 1s linear infinite; }
  `;
  document.head.appendChild(s);
}

// ── Utility ───────────────────────────────────────────────────────────────────

const fmt2 = n => n != null ? n.toFixed(1) : '–';
const fmtTs = iso => {
  if (!iso) return '–';
  const d = new Date(iso);
  return d.toLocaleString('en-GB', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' });
};

// ── Shared SDR Navigation Hook ───────────────────────────────────────────────
function useSdrNavigation() {
  const [sdrList, setSdrList] = useState([]);
  const [sdrIdx, setSdrIdx] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const fetchList = async () => {
      try {
        const res = await fetch('/api/sdr/list', { credentials: 'include' });
        if (!res.ok || cancelled) return;
        const list = await res.json();
        if (!cancelled) setSdrList(list);
      } catch { /* ignore */ }
    };
    fetchList();
    return () => { cancelled = true; };
  }, []);

  const currentSdr = sdrList[sdrIdx] || null;
  const currentSdrId = currentSdr ? currentSdr.id : '';
  const currentSdrLabel = currentSdr ? (currentSdr.label || currentSdr.id) : '';

  const navigate = useCallback((dir) => {
    if (sdrList.length === 0) return;
    setSdrIdx(prev => {
      const next = prev + dir;
      if (next < 0) return sdrList.length - 1;
      if (next >= sdrList.length) return 0;
      return next;
    });
  }, [sdrList.length]);

  return { sdrList, sdrIdx, currentSdr, currentSdrId, currentSdrLabel, navigate };
}

// ── SDR Navigation Arrows UI ─────────────────────────────────────────────────
function SdrNavigator({ sdrList, sdrIdx, currentSdrId, currentSdrLabel, navigate }) {
  if (sdrList.length === 0) return null;
  return (
    <div style={{
      display:'flex', alignItems:'center', justifyContent:'center', gap:16,
      marginBottom:16,
    }}>
      <motion.button
        whileHover={{ scale:1.15, color:'#00e5ff' }}
        whileTap={{ scale:0.9 }}
        onClick={() => navigate(-1)}
        style={{
          background:'rgba(0,229,255,0.08)', border:'1px solid rgba(0,229,255,0.25)',
          borderRadius:8, cursor:'pointer', color:'rgba(255,255,255,0.55)',
          fontSize:18, lineHeight:1, padding:'6px 12px', fontFamily:SYS,
          transition:'color 0.15s',
        }}
      >◀</motion.button>
      <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:2, minWidth:120 }}>
        <span style={{
          fontFamily:MONO, fontSize:14, fontWeight:700, color:CYAN,
          letterSpacing:'0.06em', textTransform:'uppercase',
          textShadow:`0 0 10px ${CYAN}55`,
        }}>{currentSdrLabel}</span>
        <span style={{
          fontFamily:MONO, fontSize:9, color:'rgba(255,255,255,0.30)',
          letterSpacing:'0.04em',
        }}>{currentSdrId} · {sdrIdx + 1}/{sdrList.length}</span>
      </div>
      <motion.button
        whileHover={{ scale:1.15, color:'#00e5ff' }}
        whileTap={{ scale:0.9 }}
        onClick={() => navigate(1)}
        style={{
          background:'rgba(0,229,255,0.08)', border:'1px solid rgba(0,229,255,0.25)',
          borderRadius:8, cursor:'pointer', color:'rgba(255,255,255,0.55)',
          fontSize:18, lineHeight:1, padding:'6px 12px', fontFamily:SYS,
          transition:'color 0.15s',
        }}
      >▶</motion.button>
    </div>
  );
}

function Spinner() {
  return (
    <div style={{ display:'flex', justifyContent:'center', alignItems:'center', padding:'48px 0' }}>
      <div className="anl-spin" style={{ width:28, height:28, borderRadius:'50%', border:`2px solid rgba(0,229,255,0.2)`, borderTopColor:CYAN }} />
    </div>
  );
}

function KpiCard({ label, value, sub, color = CYAN, delay = 0 }) {
  return (
    <motion.div
      initial={{ opacity:0, y:10 }} animate={{ opacity:1, y:0 }}
      transition={{ delay, duration: 0.4, ease: [.16,1,.3,1] }}
      style={{
        ...GLASS, padding:'18px 22px', flex:1, minWidth:140,
        borderColor: `${color}22`,
        boxShadow: `0 0 24px ${color}08, 0 8px 32px rgba(0,0,0,0.6)`,
      }}
    >
      <div style={{ fontSize:10, letterSpacing:'0.12em', textTransform:'uppercase', color:`${color}88`, marginBottom:8, fontFamily:SYS }}>{label}</div>
      <div style={{ fontSize:28, fontWeight:700, color, fontFamily:MONO, letterSpacing:'-0.03em', lineHeight:1 }}>{value}</div>
      {sub && <div style={{ fontSize:11, color:'rgba(255,255,255,0.25)', marginTop:6, fontFamily:SYS }}>{sub}</div>}
    </motion.div>
  );
}

// ── Dashboard 2: RF Anomaly / Interference Tracker ────────────────────────────
const TYPE_COLOUR = { interference: AMBER, jamming: RED, detection: CYAN };

const SEV_COLOUR = {
  critical: RED,
  warning:  AMBER,
  ok:       GREEN,
  info:     'rgba(255,255,255,0.35)',
};

const SEV_BG = {
  critical: 'rgba(255,87,34,0.12)',
  warning:  'rgba(255,175,0,0.10)',
  ok:       'rgba(105,255,71,0.08)',
  info:     'rgba(255,255,255,0.04)',
};

function ClassificationBadge({ sev, label }) {
  const icons = { critical:'🚨', warning:'⚠️', ok:'✅', info:'🌫️' };
  return (
    <span style={{
      display:'inline-flex', alignItems:'center', gap:4,
      padding:'2px 8px', borderRadius:4,
      background: SEV_BG[sev] || 'rgba(255,255,255,0.04)',
      border:`1px solid ${SEV_COLOUR[sev] || 'rgba(255,255,255,0.15)'}44`,
      fontSize:9, fontFamily:MONO, fontWeight:700,
      color: SEV_COLOUR[sev] || 'rgba(255,255,255,0.35)',
      letterSpacing:'0.06em', whiteSpace:'nowrap',
    }}>
      {icons[sev]}{label}
    </span>
  );
}

// ── Shared spectrum poll hook — fetches /api/spectrum/latest every 1s (like History tab) ──
function useSpectrumWS(sdrId = '', dbRange = null) {
  const targetRef = useRef([]);  // latest raw dBFS values (lerp source)
  const smoothRef = useRef([]);  // lerped values — read by NeonSpectrumCanvas RAF
  const rawRef    = useRef([]);  // raw dBFS values — read by WaterfallCanvas RAF
  const freqsRef  = useRef([]);
  const [dynamicRange, setDynamicRange] = useState(null);

  const effectiveRange = useMemo(() => {
    return dynamicRange || dbRange || { db_min: -90, db_max: -20 };
  }, [dynamicRange, dbRange]);

  useEffect(() => {
    setDynamicRange(null);
  }, [sdrId, dbRange?.db_min, dbRange?.db_max]);

  useEffect(() => {
    let dead = false;

    const computeRange = (fft) => {
      if (!fft.length) return null;
      const dataMin = Math.min(...fft);
      const dataMax = Math.max(...fft);
      const cfgMin = dbRange?.db_min;
      const cfgMax = dbRange?.db_max;
      if (cfgMin != null && cfgMax != null && dataMin >= cfgMin && dataMax <= cfgMax) return null;
      const pad = Math.max(5, (dataMax - dataMin) * 0.12);
      return {
        db_min: Math.floor((dataMin - pad) / 5) * 5,
        db_max: Math.ceil((dataMax + pad) / 5) * 5,
      };
    };

    const poll = async () => {
      if (dead) return;
      const cacheKey = `/api/spectrum/latest?sdr_id=${sdrId || ''}`;
      const cached = _getSpectrumCached(cacheKey, 8000);
      if (cached) {
        const { fft, freqs } = cached;
        if (fft.length) {
          const newDynamic = computeRange(fft);
          if (newDynamic) setDynamicRange(newDynamic);
          freqsRef.current = freqs;
          rawRef.current = fft;
          if (targetRef.current.length !== fft.length) {
            targetRef.current = [...fft];
            smoothRef.current = [...fft];
          } else {
            targetRef.current = fft;
          }
        }
        return;
      }
      try {
        const sdrParam = sdrId ? `?sdr_id=${encodeURIComponent(sdrId)}` : '';
        const r = await fetch(`/api/spectrum/latest${sdrParam}`, { credentials: 'include' });
        if (!r.ok || dead) return;
        const d = await r.json();
        const fft   = d.fft   ?? [];
        const freqs = d.freqs ?? [];
        _setSpectrumCached(cacheKey, { fft, freqs });
        if (!fft.length) return;
        const newDynamic = computeRange(fft);
        if (newDynamic) setDynamicRange(newDynamic);
        freqsRef.current = freqs;
        rawRef.current   = fft;
        if (targetRef.current.length !== fft.length) {
          targetRef.current = [...fft];
          smoothRef.current = [...fft];
        } else {
          targetRef.current = fft;
        }
      } catch(_) {}
    };

    const start = () => { poll(); return setInterval(poll, 5000); };
    let id = start();
    const onVis = () => { if (document.hidden) { clearInterval(id); id = null; } else if (!id) { id = start(); } };
    document.addEventListener('visibilitychange', onVis);
    return () => { dead = true; clearInterval(id); document.removeEventListener('visibilitychange', onVis); };
  }, [sdrId, dbRange?.db_min, dbRange?.db_max]);

  return { smoothRef, targetRef, rawRef, freqsRef, dbRange: effectiveRange };
}

// ── Neon Spectrum Canvas ───────────────────────────────────────────────────────
function NeonSpectrumCanvas({ smoothRef, targetRef, freqsRef, dbRange, isOffline = false }) {
  const canvasRef  = useRef(null);
  const rafRef     = useRef(null);
  const tRef       = useRef(0);
  const lastTsRef  = useRef(null);
  const mouseRef   = useRef(null); // { x, y } in CSS pixels, null when outside

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const onMove = (e) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };
    const onLeave = () => { mouseRef.current = null; };
    canvas.addEventListener('mousemove', onMove);
    canvas.addEventListener('mouseleave', onLeave);

    const ML=48,MR=12,MT=14,MB=28;
    const DB_MIN=dbRange?.db_min ?? -90, DB_MAX=dbRange?.db_max ?? -20;
    const FREQ_MIN=88, FREQ_MAX=108; // MHz
    const normV = v => Math.max(0, Math.min(1, (v-DB_MIN)/(DB_MAX-DB_MIN)));

    const FRAME_MS = 1000/10;
    const draw = (ts) => {
      rafRef.current = requestAnimationFrame(draw);
      if (document.hidden) return;
      if (lastTsRef.current && (ts - lastTsRef.current) < FRAME_MS) return;
      const dt = lastTsRef.current ? Math.min((ts-lastTsRef.current)/1000, 0.1) : 0.016;
      lastTsRef.current = ts;
      tRef.current += dt;

      if (isOffline) {
        const dpr = window.devicePixelRatio || 1;
        const cW = canvas.offsetWidth, cH = canvas.offsetHeight;
        if (canvas.width !== cW * dpr || canvas.height !== cH * dpr) {
          canvas.width = cW * dpr;
          canvas.height = cH * dpr;
        }
        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const W = cW, H = cH;
        ctx.fillStyle = '#08090f';
        ctx.fillRect(0, 0, W, H);

        // gray filter / dim
        ctx.fillStyle = 'rgba(40, 42, 50, 0.65)';
        ctx.fillRect(0, 0, W, H);

        const ML = 48, MR = 12, MT = 14, MB = 28;
        const PW = W - ML - MR, PH = H - MT - MB;
        const y = MT + PH;
        ctx.strokeStyle = 'rgba(140, 140, 140, 0.75)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(ML, y);
        ctx.lineTo(ML + PW, y);
        ctx.stroke();

        ctx.font = 'bold 18px "Space Grotesk", "Segoe UI", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = 'rgba(180, 180, 180, 0.55)';
        ctx.fillText('OFFLINE', W / 2, H / 2);
        return;
      }

      const target = targetRef.current;
      const smooth = smoothRef.current;
      if (!target.length) return;
      const k = 1 - Math.pow(0.01, dt);
      for (let i = 0; i < target.length; i++) smooth[i] = (smooth[i] ?? target[i]) + (target[i] - (smooth[i] ?? target[i])) * k;
      const fft = smooth;

      const dpr = window.devicePixelRatio||1;
      const cW=canvas.offsetWidth, cH=canvas.offsetHeight;
      if (canvas.width!==cW*dpr||canvas.height!==cH*dpr){canvas.width=cW*dpr;canvas.height=cH*dpr;}
      const ctx=canvas.getContext('2d');
      ctx.setTransform(dpr,0,0,dpr,0,0);
      const W=cW,H=cH,PW=W-ML-MR,PH=H-MT-MB,t=tRef.current;

      ctx.fillStyle='#08090f'; ctx.fillRect(0,0,W,H);
      ctx.font='10px "Segoe UI Mono",monospace'; ctx.textAlign='right';
      // Dynamic Y-axis labels based on per-SDR calibration range
      const dbStep = Math.max(5, Math.round((DB_MAX - DB_MIN) / 6 / 5) * 5) || 10;
      const dbStart = Math.ceil(DB_MIN / dbStep) * dbStep;
      for (let db = dbStart; db <= DB_MAX; db += dbStep) {
        const yn=(db-DB_MIN)/(DB_MAX-DB_MIN), y=MT+PH-yn*PH;
        const hi = db === dbStart || Math.abs(db - (DB_MIN + (DB_MAX-DB_MIN)*0.33)) < dbStep;
        ctx.strokeStyle=hi?'rgba(0,229,255,0.22)':'rgba(0,229,255,0.10)';
        ctx.lineWidth=hi?1:0.5; ctx.setLineDash(hi?[5,4]:[]);
        ctx.beginPath();ctx.moveTo(ML,y);ctx.lineTo(ML+PW,y);ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle='rgba(0,229,255,0.85)';
        ctx.fillText(Math.round(db)+' dB',ML-6,y+3.5);
      }
      ctx.textAlign='center';
      for (const f of [88,90,92,94,96,98,100,102,104,106,108]) {
        const x=ML+((f-FREQ_MIN)/(FREQ_MAX-FREQ_MIN))*PW, maj=f%4===0||f===88||f===108;
        ctx.strokeStyle=maj?'rgba(0,229,255,0.5)':'rgba(0,229,255,0.2)'; ctx.lineWidth=maj?1:0.5;
        ctx.beginPath();ctx.moveTo(x,MT+PH);ctx.lineTo(x,MT+PH+4);ctx.stroke();
        ctx.strokeStyle='rgba(0,229,255,0.09)'; ctx.lineWidth=0.5;
        ctx.beginPath();ctx.moveTo(x,MT);ctx.lineTo(x,MT+PH);ctx.stroke();
        ctx.fillStyle='rgba(0,229,255,0.85)'; ctx.fillText(f+' MHz',x,MT+PH+MB-4);
      }
      ctx.strokeStyle='rgba(0,229,255,0.35)'; ctx.lineWidth=1;
      ctx.beginPath();ctx.moveTo(ML,MT);ctx.lineTo(ML,MT+PH);ctx.stroke();
      ctx.beginPath();ctx.moveTo(ML,MT+PH);ctx.lineTo(ML+PW,MT+PH);ctx.stroke();

      // ── build smoothed spectrum points array for hover lookup ──
      const step = PW / (fft.length - 1);
      const noiseFloor = dbRange?.noise_floor ?? -90;
      const specPts = fft.map((v, i) => {
        const sh = v>noiseFloor ? Math.sin(t*1.4+i*0.3)*1.2 : (Math.sin(t*2.1+i*1.1)*0.9+Math.sin(t*3.7+i*2.3)*0.7)*0.55;
        return { x: ML+i*step, y: MT+PH-normV(v+sh)*(PH-4), raw: v };
      });

      ctx.save(); ctx.beginPath(); ctx.rect(ML,MT,PW,PH); ctx.clip();
      const scp=(scale, pts)=>{
        ctx.beginPath();
        for(let i=0;i<pts.length;i++){
          const {x,y}=pts[i];
          if(i===0){ctx.moveTo(x,y);continue;}
          const px2=pts[i-1].x, py=pts[i-1].y;
          ctx.bezierCurveTo((px2+x)/2,py,(px2+x)/2,y,x,y);
        } ctx.stroke();
      };
      ctx.lineJoin='round';
      for(let li=0;li<8;li++){
        const tt=li/7; let r,g,b,a;
        if(tt<0.5){const s=tt/0.5;r=Math.round(15+s*40);g=Math.round(10+s*40);b=Math.round(100+s*155);a=0.12+s*0.25;}
        else{const s=(tt-0.5)/0.5;r=Math.round(55+s*145);g=Math.round(50+s*200);b=255;a=0.37+s*0.55;}
        ctx.strokeStyle=`rgba(${r},${g},${b},${a})`;ctx.lineWidth=tt>0.88?1:0.7;scp(tt,specPts);
      }
      ctx.strokeStyle='rgba(140,220,255,0.30)';ctx.lineWidth=3.5;scp(1,specPts);
      ctx.strokeStyle='#d8f4ff';ctx.lineWidth=1;scp(1,specPts);

      // ── hover overlay (lines/dot inside clip, tooltip outside) ──
      let tooltipData = null;
      const mouse = mouseRef.current;
      if (mouse && mouse.x >= ML && mouse.x <= ML+PW && fft.length > 1) {
        const mx = mouse.x;

        // find nearest FFT bin
        const binF = (mx - ML) / PW * (fft.length - 1);
        const bin  = Math.max(0, Math.min(fft.length - 1, Math.round(binF)));
        const peakDb = fft[bin];

        // local floor: min over ±30-bin neighbourhood
        const R = 30;
        let floorDb = peakDb;
        for (let j = Math.max(0, bin-R); j <= Math.min(fft.length-1, bin+R); j++) {
          if (fft[j] < floorDb) floorDb = fft[j];
        }
        const snr = Math.max(0, peakDb - floorDb);

        // frequency at cursor
        const freqs = freqsRef ? freqsRef.current : [];
        let freqMHz;
        if (freqs.length > 1) {
          const fBin = Math.max(0, Math.min(freqs.length-1, Math.round(binF)));
          freqMHz = freqs[fBin];
        } else {
          freqMHz = FREQ_MIN + (mx - ML) / PW * (FREQ_MAX - FREQ_MIN);
        }

        // nearest spectrum Y at cursor X
        const nearPt = specPts[bin];
        const dotY = nearPt ? nearPt.y : MT;

        // vertical red line
        ctx.strokeStyle = 'rgba(255,40,40,0.90)';
        ctx.lineWidth   = 1;
        ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(mx, MT); ctx.lineTo(mx, MT+PH); ctx.stroke();

        // highlighted spectrum top line (bright cyan) — redraw scp at scale=1
        ctx.strokeStyle = '#00e5ff';
        ctx.lineWidth   = 1.8;
        ctx.shadowColor = '#00e5ff';
        ctx.shadowBlur  = 10;
        scp(1, specPts);
        ctx.shadowBlur  = 0;

        // dot on spectrum line at cursor
        ctx.beginPath();
        ctx.arc(mx, dotY, 4, 0, Math.PI*2);
        ctx.fillStyle = '#ff2828';
        ctx.shadowColor = '#ff2828'; ctx.shadowBlur = 8;
        ctx.fill();
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 1;
        ctx.stroke();
        ctx.shadowBlur = 0;

        // Save tooltip info to draw after clip restore
        tooltipData = { mx, dotY, freqMHz, peakDb, floorDb, snr };
      }

      ctx.restore();

      // ── Draw tooltip OUTSIDE clip region so it's never cut off ──
      if (tooltipData) {
        const { mx, dotY, freqMHz, peakDb, floorDb, snr } = tooltipData;
        const lines = [
          `VFO:   ${freqMHz.toFixed(3)} MHz`,
          `Peak:  ${peakDb.toFixed(1)} dB`,
          `Floor: ${floorDb.toFixed(1)} dB`,
          `SNR:   ${snr.toFixed(1)} dB`,
        ];
        ctx.font = 'bold 11px "Segoe UI Mono",monospace';
        ctx.textAlign = 'left';
        const lh = 17, pad = 10;
        const bw = 176, bh = lines.length * lh + pad * 2 - 4;
        let bx = mx + 14;
        let by = dotY - bh / 2;
        // Clamp within full canvas bounds
        if (bx + bw > W - 4) bx = mx - bw - 14;
        if (bx < 4) bx = 4;
        if (by < 4) by = 4;
        if (by + bh > H - 4) by = H - bh - 4;

        ctx.fillStyle = 'rgba(8,9,18,0.92)';
        ctx.strokeStyle = 'rgba(0,229,255,0.50)';
        ctx.lineWidth = 1;
        const rr = 6;
        ctx.beginPath();
        ctx.moveTo(bx+rr,by); ctx.lineTo(bx+bw-rr,by);
        ctx.arcTo(bx+bw,by,bx+bw,by+rr,rr);
        ctx.lineTo(bx+bw,by+bh-rr);
        ctx.arcTo(bx+bw,by+bh,bx+bw-rr,by+bh,rr);
        ctx.lineTo(bx+rr,by+bh);
        ctx.arcTo(bx,by+bh,bx,by+bh-rr,rr);
        ctx.lineTo(bx,by+rr);
        ctx.arcTo(bx,by,bx+rr,by,rr);
        ctx.closePath();
        ctx.fill(); ctx.stroke();

        lines.forEach((line, i) => {
          ctx.fillStyle = i === 0 ? '#00e5ff' : 'rgba(255,255,255,0.88)';
          ctx.fillText(line, bx+pad, by+pad+i*lh+10);
        });
      }
    };
    rafRef.current = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(rafRef.current);
      canvas.removeEventListener('mousemove', onMove);
      canvas.removeEventListener('mouseleave', onLeave);
    };
  }, [dbRange?.db_min, dbRange?.db_max, isOffline]);

  return <canvas ref={canvasRef} style={{width:'100%',height:200,display:'block',borderRadius:8,cursor:'crosshair', filter: isOffline ? 'grayscale(1)' : undefined}} />;
}

// ── Waterfall canvas — pixel-scroll, WebSocket driven ─────────────────────────
function WaterfallCanvas({ rawRef, dbRange }) {
  const canvasRef = useRef(null);
  const rafRef    = useRef(null);
  const offRef    = useRef(null);
  const offCTXRef = useRef(null);
  const subYRef   = useRef(0);
  const lastTsRef = useRef(null);
  const ML=48,MR=12,MT=14,MB=28;
  const WF_SPD=30;

  const DBFS_MIN = dbRange?.db_min ?? -90;
  const DBFS_MAX = dbRange?.db_max ?? -20;
  const rangeSpan = Math.max(1, DBFS_MAX - DBFS_MIN);
  const norm = v => Math.max(0, Math.min(1, (v - DBFS_MIN) / rangeSpan));

  const wfRGB = (v) => {
    let r,g,b;
    if(v<0.3){const t=v/0.3;r=Math.round(t*30);g=Math.round(t*10);b=Math.round(t*120);}
    else if(v<0.6){const t=(v-0.3)/0.3;r=Math.round(30+t*25);g=Math.round(10+t*50);b=Math.round(120+t*135);}
    else if(v<0.85){const t=(v-0.6)/0.25;r=55;g=Math.round(60+t*169);b=255;}
    else{const t=(v-0.85)/0.15;r=Math.round(55+t*200);g=Math.round(229+t*26);b=255;}
    return [r,g,b];
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ensureOff = () => {
      const dpr=window.devicePixelRatio||1;
      const PW=Math.round((canvas.offsetWidth-ML-MR)*dpr);
      const PH=Math.round((canvas.offsetHeight-MT-MB)*dpr);
      if(PW<=0||PH<=0) return false;
      if(!offRef.current||offRef.current.width!==PW||offRef.current.height!==PH){
        const c=document.createElement('canvas');c.width=PW;c.height=PH;
        const cx=c.getContext('2d');cx.fillStyle='#08090f';cx.fillRect(0,0,PW,PH);
        offRef.current=c;offCTXRef.current=cx;subYRef.current=0;
      }
      return true;
    };

    const FRAME_MS = 1000/10;
    const draw = (ts) => {
      rafRef.current=requestAnimationFrame(draw);
      if (document.hidden) return;
      if(lastTsRef.current&&(ts-lastTsRef.current)<FRAME_MS) return;
      const dt=lastTsRef.current?Math.min((ts-lastTsRef.current)/1000,0.1):0.016;
      lastTsRef.current=ts;
      if(!ensureOff()) return;

      const dpr=window.devicePixelRatio||1;
      const cW=canvas.offsetWidth,cH=canvas.offsetHeight;
      if(canvas.width!==cW*dpr||canvas.height!==cH*dpr){canvas.width=cW*dpr;canvas.height=cH*dpr;}
      const off=offRef.current,offCTX=offCTXRef.current;
      const PW=off.width,PH=off.height,ctx=canvas.getContext('2d');

      // scroll + paint new row
      subYRef.current+=WF_SPD*dt*dpr;
      const row=rawRef.current;
      if(subYRef.current>=1&&row.length){
        const shift=Math.floor(subYRef.current);
        const img=offCTX.getImageData(0,0,PW,PH-shift);
        offCTX.fillStyle='#08090f';offCTX.fillRect(0,0,PW,PH);
        offCTX.putImageData(img,0,shift);
        // paint new row at top using ImageData for speed
        const rowImg=offCTX.createImageData(PW,shift);
        const px=rowImg.data;
        for(let x=0;x<PW;x++){
          const bi=Math.floor(x/PW*row.length);
          const [r,g,b]=wfRGB(norm(row[Math.min(bi,row.length-1)]));
          for(let y=0;y<shift;y++){const idx=(y*PW+x)*4;px[idx]=r;px[idx+1]=g;px[idx+2]=b;px[idx+3]=255;}
        }
        offCTX.putImageData(rowImg,0,0);
        subYRef.current-=shift;
      }

      // blit to screen
      ctx.setTransform(dpr,0,0,dpr,0,0);
      ctx.fillStyle='#08090f';ctx.fillRect(0,0,cW,cH);
      const plotW=cW-ML-MR,plotH=cH-MT-MB;
      ctx.drawImage(off,0,0,PW,PH,ML,MT,plotW,plotH);

      // Y legend
      const legendW=7,legendX=ML-legendW-14;
      const grad=ctx.createLinearGradient(0,MT,0,MT+plotH);
      grad.addColorStop(0,'#fff');grad.addColorStop(0.15,'#00e5ff');
      grad.addColorStop(0.4,'rgb(55,60,255)');grad.addColorStop(0.7,'rgb(30,10,120)');grad.addColorStop(1,'#08090f');
      ctx.fillStyle=grad;ctx.fillRect(legendX,MT,legendW,plotH);
      ctx.strokeStyle='rgba(0,229,255,0.25)';ctx.lineWidth=0.5;ctx.strokeRect(legendX,MT,legendW,plotH);
      ctx.font='9px "Segoe UI Mono",monospace';ctx.textAlign='right';
      const step = Math.max(5, Math.round((DBFS_MAX - DBFS_MIN) / 6 / 5) * 5) || 10;
      for (let db = Math.ceil(DBFS_MAX / step) * step; db >= DBFS_MIN; db -= step) {
        const yn = (db - DBFS_MIN) / rangeSpan, y = MT + plotH - yn * plotH;
        if (y < MT || y > MT + plotH) continue;
        ctx.fillStyle='rgba(0,229,255,0.85)';ctx.fillText(String(Math.round(db)),legendX-3,y+3.5);
        ctx.strokeStyle='rgba(0,229,255,0.5)';ctx.lineWidth=0.5;
        ctx.beginPath();ctx.moveTo(legendX,y);ctx.lineTo(legendX+legendW,y);ctx.stroke();
      }
      // X axis
      ctx.font='10px "Segoe UI Mono",monospace';ctx.textAlign='center';
      for(const f of [88,90,92,94,96,98,100,102,104,106,108]){
        const x=ML+((f-88)/20)*plotW,maj=f%4===0||f===88||f===108;
        ctx.strokeStyle=maj?'rgba(0,229,255,0.5)':'rgba(0,229,255,0.2)';ctx.lineWidth=maj?1:0.5;
        ctx.beginPath();ctx.moveTo(x,MT+plotH);ctx.lineTo(x,MT+plotH+4);ctx.stroke();
        ctx.fillStyle='rgba(0,229,255,0.85)';ctx.fillText(f+' MHz',x,MT+plotH+MB-4);
      }
      ctx.strokeStyle='rgba(0,229,255,0.35)';ctx.lineWidth=1;
      ctx.beginPath();ctx.moveTo(ML,MT);ctx.lineTo(ML,MT+plotH);ctx.stroke();
      ctx.beginPath();ctx.moveTo(ML,MT+plotH);ctx.lineTo(ML+plotW,MT+plotH);ctx.stroke();
    };
    rafRef.current=requestAnimationFrame(draw);
    return ()=>cancelAnimationFrame(rafRef.current);
  }, [DBFS_MIN, DBFS_MAX, rangeSpan, norm]);

  return <canvas ref={canvasRef} style={{width:'100%',height:280,display:'block',borderRadius:8}} />;
}

// ── AnomalyDashboard ──────────────────────────────────────────────────────────
function AnomalyDashboard() {
  const { t } = useTranslation();
  const wsRefs = useSpectrumWS();
  const [data,    setData]    = useState(null);
  const [wfData,  setWfData]  = useState(null);
  const [busy,    setBusy]    = useState(false);
  const [errMsg,  setErrMsg]  = useState(null);
  const [lastTs,  setLastTs]  = useState(null);
  const [countdown, setCountdown] = useState(30);

  const loadClassifier = useCallback(async () => {
    setBusy(true); setErrMsg(null);
    try {
      const r = await fetch('/api/analytics/anomalies', { credentials:'include' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setData(d);
      setLastTs(new Date().toLocaleTimeString('en-GB'));
    } catch(e) { setErrMsg(e.message); }
    finally { setBusy(false); }
  }, []);

  const loadWaterfall = useCallback(async () => {
    try {
      const r = await fetch('/api/analytics/waterfall', { credentials:'include' });
      if (!r.ok) return;
      setWfData(await r.json());
    } catch(_) {}
  }, []);

  // Initial load
  useEffect(() => {
    loadClassifier();
    loadWaterfall();
  }, []);

  // 5s waterfall poll — live data
  useEffect(() => {
    const wfInterval = setInterval(loadWaterfall, 5000);
    return () => clearInterval(wfInterval);
  }, [loadWaterfall]);

  // 30s auto-refresh for classifier
  useEffect(() => {
    setCountdown(30);
    const interval = setInterval(() => {
      setCountdown(c => {
        if (c <= 1) {
          loadClassifier();
          return 30;
        }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [loadClassifier]);

  // Filter: only licensed, interference, jamming (drop background/transient)
  const classifications = (data?.classifications ?? []).filter(
    c => c.severity !== 'info'
  );

  const critCount = classifications.filter(c => c.severity === 'critical').length;
  const warnCount = classifications.filter(c => c.severity === 'warning').length;
  const okCount   = classifications.filter(c => c.severity === 'ok').length;

  const freqMin = wfData?.freqs?.[0] ?? 88;
  const freqMax = wfData?.freqs?.[wfData.freqs.length - 1] ?? 108;

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      {/* KPI row */}
      {data && (
        <div style={{ display:'flex', gap:12, flexWrap:'wrap' }}>
          <KpiCard label={t('analytics.anomaly.licensed')}           value={okCount}    color={GREEN}  delay={0}    />
          <KpiCard label={t('analytics.anomaly.interference')}       value={warnCount}  color={AMBER}  delay={0.06} />
          <KpiCard label={t('analytics.anomaly.jamming')} value={critCount}  color={RED}    delay={0.12} />
        </div>
      )}

      {/* Toolbar */}
      <div style={{ display:'flex', alignItems:'center', gap:12 }}>
        <div style={{ fontSize:9, color:'rgba(255,255,255,0.2)', fontFamily:MONO }}>
          {lastTs ? `${t('analytics.anomaly.updated')} ${lastTs}` : t('common.loading')}
        </div>
        <div style={{
          display:'flex', alignItems:'center', gap:6,
          padding:'3px 10px', borderRadius:20,
          background:'rgba(0,229,255,0.05)', border:'1px solid rgba(0,229,255,0.12)',
        }}>
          <div style={{
            width:6, height:6, borderRadius:'50%',
            background: countdown <= 5 ? CYAN : 'rgba(0,229,255,0.3)',
            boxShadow: countdown <= 5 ? `0 0 8px ${CYAN}` : 'none',
            transition:'all 0.3s',
          }} />
          <span style={{ fontSize:9, color:'rgba(0,229,255,0.5)', fontFamily:MONO }}>{t('analytics.anomaly.countdown', { n: countdown })}</span>
        </div>
        <button
          onClick={() => { loadClassifier(); loadWaterfall(); setCountdown(30); }}
          style={{
            padding:'5px 14px', borderRadius:6, fontSize:10, fontWeight:700,
            fontFamily:SYS, cursor:'pointer',
            background:'rgba(0,229,255,0.08)', border:'1px solid rgba(0,229,255,0.22)', color:CYAN,
          }}
        >{t('analytics.anomaly.refresh')}</button>
        {busy && <Spinner />}
      </div>

      {/* Split layout: table LEFT, waterfall RIGHT */}
      <div style={{ display:'flex', gap:16, alignItems:'flex-start' }}>

        {/* ── Smart Analysis Table ─────────────────────────────────── */}
        <div style={{ ...GLASS, padding:'16px 18px', flex:'0 0 520px', minWidth:0 }}>
          <div style={{ display:'flex', alignItems:'baseline', justifyContent:'space-between', marginBottom:10 }}>
            <span style={{ fontSize:10, color:'rgba(0,229,255,0.4)', letterSpacing:'0.12em', textTransform:'uppercase', fontFamily:SYS }}>
              {t('analytics.anomaly.classifier')}
            </span>
            <span style={{ fontSize:9, color:'rgba(255,255,255,0.15)', fontFamily:MONO }}>{t('analytics.anomaly.window')}</span>
          </div>

          {/* Legend */}
          <div style={{ display:'flex', gap:10, flexWrap:'wrap', marginBottom:12 }}>
            {[
              { sev:'critical', text:t('analytics.anomaly.legendJamming') },
              { sev:'warning',  text:t('analytics.anomaly.legendInterference') },
              { sev:'ok',       text:t('analytics.anomaly.legendLicensed') },
            ].map(({ sev, text }) => (
              <div key={sev} style={{ display:'flex', alignItems:'center', gap:4 }}>
                <div style={{ width:6, height:6, borderRadius:'50%', background: SEV_COLOUR[sev] }} />
                <span style={{ fontSize:9, color:'rgba(255,255,255,0.28)', fontFamily:SYS }}>{text}</span>
              </div>
            ))}
          </div>

          {/* Column headers */}
          <div style={{
            display:'grid',
            gridTemplateColumns:'56px 50px 50px 50px 1fr',
            gap:'3px 6px', fontSize:8, letterSpacing:'0.1em',
            color:'rgba(0,229,255,0.3)', textTransform:'uppercase',
            fontFamily:SYS, paddingBottom:7,
            borderBottom:'1px solid rgba(255,255,255,0.05)', marginBottom:4,
          }}>
            <span>{t('analytics.anomaly.time')}</span>
            <span>{t('analytics.anomaly.freq')}</span>
            <span>{t('analytics.anomaly.snr')}</span>
            <span>{t('analytics.anomaly.sigma')}</span>
            <span>{t('analytics.anomaly.classification')}</span>
          </div>

          <div style={{ maxHeight:460, overflowY:'auto' }}>
            {classifications.length === 0 ? (
              <div style={{ textAlign:'center', padding:'28px 0', color:'rgba(255,255,255,0.18)', fontFamily:MONO, fontSize:11 }}>
                {t('analytics.anomaly.noSignals')}
              </div>
            ) : classifications.map((c, i) => (
              <motion.div
                key={`${c.frequency_mhz}-${c.classification}`}
                initial={{ opacity:0, x:-8 }}
                animate={{ opacity:1, x:0 }}
                transition={{ duration:0.22, delay: Math.min(i, 12) * 0.02 }}
                style={{
                  display:'grid',
                  gridTemplateColumns:'56px 50px 50px 50px 1fr',
                  gap:'3px 6px',
                  padding:'6px 0',
                  borderBottom:'1px solid rgba(255,255,255,0.04)',
                  background: c.severity === 'critical'
                    ? 'rgba(255,87,34,0.04)'
                    : c.severity === 'warning'
                    ? 'rgba(255,175,0,0.03)'
                    : 'transparent',
                }}
              >
                <span style={{ fontFamily:MONO, fontSize:9, color:'rgba(255,255,255,0.25)' }}>
                  {data?.analyzed_at ? new Date(data.analyzed_at).toLocaleTimeString('en-GB', { hour:'2-digit', minute:'2-digit', second:'2-digit' }) : '–'}
                </span>
                <span style={{ fontFamily:MONO, fontSize:10, fontWeight:700, color: SEV_COLOUR[c.severity] }}>
                  {c.frequency_mhz}
                </span>
                <span style={{ fontFamily:MONO, fontSize:10, color:'rgba(255,255,255,0.6)' }}>
                  {c.snr_mean}
                </span>
                <span style={{ fontFamily:MONO, fontSize:10, color: c.snr_std >= 4 ? AMBER : 'rgba(255,255,255,0.35)' }}>
                  {c.snr_std}
                </span>
                <div style={{ display:'flex', flexDirection:'column', gap:2, minWidth:0 }}>
                  <ClassificationBadge sev={c.severity} label={c.label} />
                  {c.station_name && (
                    <span style={{ fontSize:8, color:'rgba(255,255,255,0.2)', fontFamily:SYS, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
                      {c.station_name}
                    </span>
                  )}
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        {/* ── Live Spectrum + Waterfall ────────────────────────────── */}
        <div style={{ ...GLASS, padding:'16px 18px', flex:1, minWidth:0, display:'flex', flexDirection:'column', gap:12 }}>
          <div style={{ display:'flex', alignItems:'baseline', justifyContent:'space-between' }}>
            <span style={{ fontSize:10, color:'rgba(0,229,255,0.4)', letterSpacing:'0.12em', textTransform:'uppercase', fontFamily:SYS }}>
              {t('analytics.spectrum.title')}
            </span>
            <div style={{ display:'flex', gap:12, alignItems:'center' }}>
              {wfData?.classifications?.some(c => c.severity === 'critical') && (
                <div style={{ display:'flex', alignItems:'center', gap:5 }}>
                  <div style={{ width:7, height:7, borderRadius:'50%', background:RED, boxShadow:`0 0 8px ${RED}` }} />
                  <span style={{ fontSize:9, color:RED, fontFamily:MONO, fontWeight:700 }}>{t('analytics.anomaly.jammingAlert')}</span>
                </div>
              )}
              {wfData?.classifications?.some(c => c.severity === 'warning') && (
                <div style={{ display:'flex', alignItems:'center', gap:5 }}>
                  <div style={{ width:7, height:7, borderRadius:'50%', background:AMBER, boxShadow:`0 0 6px ${AMBER}` }} />
                  <span style={{ fontSize:9, color:AMBER, fontFamily:MONO, fontWeight:700 }}>{t('analytics.anomaly.interferenceAlert')}</span>
                </div>
              )}
            </div>
          </div>
          <NeonSpectrumCanvas smoothRef={wsRefs.smoothRef} targetRef={wsRefs.targetRef} freqsRef={wsRefs.freqsRef} dbRange={wsRefs.dbRange} />
          <WaterfallCanvas rawRef={wsRefs.rawRef} dbRange={wsRefs.dbRange} />
        </div>
      </div>

      {errMsg && (
        <div style={{ textAlign:'center', padding:'12px 0', color:RED, fontFamily:MONO, fontSize:11 }}>
          {t('analytics.anomaly.error', { msg: errMsg })}
        </div>
      )}
    </div>
  );
}

// ── Dashboard 4: Spectral Occupancy Heatmap ───────────────────────────────────
function heatColor(t) {
  // t in [0,1]: dark-blue → purple → orange-red
  if (t < 0.33) {
    const s = t / 0.33;
    return [Math.round(5 + s * 40), 0, Math.round(80 + s * 150)];
  } else if (t < 0.66) {
    const s = (t - 0.33) / 0.33;
    return [Math.round(45 + s * 140), 0, Math.round(230 - s * 120)];
  } else {
    const s = (t - 0.66) / 0.34;
    return [Math.round(185 + s * 70), Math.round(s * 87), Math.round(110 - s * 100)];
  }
}

function OccupancyDashboard() {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const dataRef = useRef(null);
  const rafRef  = useRef(null);

  useEffect(() => {
    setBusy(true); setError(null);
    fetch('/api/analytics/occupancy', { credentials: 'include' })
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(d => { dataRef.current = d; setData(d); })
      .catch(e => setError(e.message))
      .finally(() => setBusy(false));
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, []);

  // drawOnCanvas — called either when data arrives OR when canvas mounts
  const drawOnCanvas = useCallback((canvas) => {
    const d = dataRef.current;
    if (!canvas || !d) return;
    const { freqs, slots, matrix, min_power, max_power } = d;
    if (!freqs.length || !slots.length || !matrix.length) return;

    const W = freqs.length;
    const H = slots.length;
    const minP = min_power ?? 17;
    const maxP = max_power ?? 87;
    const range = maxP - minP || 1;

    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      canvas.width  = W;
      canvas.height = H;
      const ctx = canvas.getContext('2d');
      const img = ctx.createImageData(W, H);
      const px = img.data;
      for (let row = 0; row < H; row++) {
        const rowData = matrix[row];
        for (let col = 0; col < W; col++) {
          const v = rowData ? rowData[col] : null;
          const idx = (row * W + col) * 4;
          if (v == null) {
            px[idx]=5; px[idx+1]=7; px[idx+2]=15; px[idx+3]=255;
          } else {
            const t = Math.max(0, Math.min(1, (v - minP) / range));
            const [r, g, b] = heatColor(t);
            px[idx]=r; px[idx+1]=g; px[idx+2]=b; px[idx+3]=255;
          }
        }
      }
      ctx.putImageData(img, 0, 0);
    });
  }, []);

  // callback ref — fires when canvas DOM node mounts/unmounts
  const canvasRef = useCallback((canvas) => {
    if (canvas) drawOnCanvas(canvas);
  }, [drawOnCanvas]);

  // also re-draw when data arrives (canvas already mounted on re-renders)
  const canvasElRef = useRef(null);
  const combinedRef = useCallback((canvas) => {
    canvasElRef.current = canvas;
    if (canvas) drawOnCanvas(canvas);
  }, [drawOnCanvas]);

  useEffect(() => {
    if (data && canvasElRef.current) drawOnCanvas(canvasElRef.current);
  }, [data, drawOnCanvas]);

  // Freq label helpers
  const freqLabels = useMemo(() => {
    if (!data?.freqs) return [];
    const step = Math.max(1, Math.floor(data.freqs.length / 12));
    return data.freqs.map((f, i) => (i % step === 0 ? { i, f } : null)).filter(Boolean);
  }, [data?.freqs]);

  const timeLabels = useMemo(() => {
    if (!data?.slots) return [];
    const step = Math.max(1, Math.floor(data.slots.length / 8));
    return data.slots.map((s, i) => (i % step === 0 ? { i, s } : null)).filter(Boolean);
  }, [data?.slots]);

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:20 }}>
      {busy ? <Spinner /> : error ? (
        <div style={{ textAlign:'center', padding:'32px 0', color:RED, fontFamily:MONO, fontSize:12 }}>
          {t('analytics.occupancy.failed', { msg: error })}
        </div>
      ) : !data || !data.freqs?.length ? (
        <div style={{ textAlign:'center', padding:'48px 0', color:'rgba(255,255,255,0.18)', fontFamily:MONO, fontSize:12 }}>
          {t('analytics.occupancy.noData')}
        </div>
      ) : (
        <motion.div
          initial={{ opacity:0, y:12 }} animate={{ opacity:1, y:0 }}
          transition={{ duration:0.6, ease:[.16,1,.3,1] }}
        >
          <div style={{ fontSize:11, color:'rgba(0,229,255,0.5)', letterSpacing:'0.12em', textTransform:'uppercase', marginBottom:12, fontFamily:SYS }}>
            {t('analytics.occupancy.title')}
          </div>

          {/* Heatmap container */}
          <div style={{ ...GLASS, padding:'16px 20px 20px' }}>
            <div style={{ display:'flex', gap:8 }}>
              {/* Y axis labels (time) */}
              <div style={{ width:34, position:'relative', flexShrink:0 }}>
                {timeLabels.map(({ i, s }) => (
                  <div key={i} style={{
                    position:'absolute', right:4, top:`${(i / data.slots.length) * 100}%`,
                    fontSize:8, color:'rgba(255,255,255,0.22)', fontFamily:MONO, transform:'translateY(-50%)', whiteSpace:'nowrap',
                  }}>{s}</div>
                ))}
              </div>
              {/* Canvas */}
              <div style={{ flex:1, position:'relative' }}>
                <canvas
                  ref={combinedRef}
                  style={{
                    width:'100%', display:'block',
                    imageRendering:'pixelated',
                    borderRadius:6,
                    boxShadow:'0 0 32px rgba(0,0,0,0.6)',
                  }}
                />
                {/* X axis labels (freq) — absolutely positioned below canvas */}
                <div style={{ position:'relative', height:18, marginTop:4 }}>
                  {freqLabels.map(({ i, f }) => (
                    <div key={i} style={{
                      position:'absolute', left:`${(i / data.freqs.length) * 100}%`,
                      fontSize:8, color:'rgba(255,255,255,0.22)', fontFamily:MONO, transform:'translateX(-50%)',
                    }}>{f}</div>
                  ))}
                </div>
              </div>
            </div>

            {/* Colour scale legend */}
            <div style={{ display:'flex', alignItems:'center', gap:12, marginTop:20 }}>
              <span style={{ fontSize:9, color:'rgba(255,255,255,0.25)', fontFamily:MONO }}>{t('analytics.occupancy.weak')}</span>
              <div style={{
                flex:1, height:8, borderRadius:4,
                background:'linear-gradient(to right, rgba(5,5,80,1), rgba(74,0,110,1), rgb(255,87,34))',
                boxShadow:'0 0 12px rgba(255,87,34,0.3)',
              }} />
              <span style={{ fontSize:9, color:'rgba(255,255,255,0.25)', fontFamily:MONO }}>{t('analytics.occupancy.strong')}</span>
            </div>
          </div>

          {/* Stats */}
          {data && (
            <div style={{ display:'flex', gap:12, flexWrap:'wrap', marginTop:16 }}>
              <KpiCard label={t('analytics.occupancy.freqBins')} value={data.freqs.length} sub={t('analytics.occupancy.band')} color={CYAN} delay={0} />
              <KpiCard label={t('analytics.occupancy.timeSlots')} value={data.slots.length} sub={t('analytics.occupancy.buckets')} color={PURPLE} delay={0.08} />
              <KpiCard label={t('analytics.occupancy.coverage')} value={`${data.slots.length * data.freqs.length} cells`} sub={t('analytics.occupancy.view')} color={AMBER} delay={0.16} />
            </div>
          )}
        </motion.div>
      )}
    </div>
  );
}

// ── Spectrum Tab ───────────────────────────────────────────────────────────────
function SpectrumTab({ tele }) {
  const sdrNav = useSdrNavigation();
  const dbRange = sdrNav.currentSdr?.thresholds || null;
  const online = tele?.sdr_nodes?.find(n => n.id === sdrNav.currentSdrId)?.online;
  const isOffline = online === false;
  const wsRefs = useSpectrumWS(sdrNav.currentSdrId, dbRange);
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      {/* SDR Region Navigation */}
      <SdrNavigator {...sdrNav} />

      <div style={{ ...GLASS, padding:'16px 18px', display:'flex', flexDirection:'column', gap:12 }}>
        <span style={{ fontSize:10, color:'rgba(0,229,255,0.4)', letterSpacing:'0.12em', textTransform:'uppercase', fontFamily:SYS }}>
                  </span>
        <NeonSpectrumCanvas smoothRef={wsRefs.smoothRef} targetRef={wsRefs.targetRef} freqsRef={wsRefs.freqsRef} dbRange={wsRefs.dbRange} isOffline={isOffline} />
      </div>
      <div style={{ ...GLASS, padding:'16px 18px', display:'flex', flexDirection:'column', gap:12 }}>
        <span style={{ fontSize:10, color:'rgba(0,229,255,0.4)', letterSpacing:'0.12em', textTransform:'uppercase', fontFamily:SYS }}>
          
        </span>
        <WaterfallCanvas rawRef={wsRefs.rawRef} dbRange={wsRefs.dbRange} />
      </div>
    </div>
  );
}

// ── Main AnalyticsDashboard component ─────────────────────────────────────────
const DASHBOARDS = (t) => [
  { id:'overview',  label:'Overview',           icon:'', subtitle:'' },
  { id:'spectrum',  label:t('top.spectrum'),    icon:'', subtitle:'' },
];

const shallowEq = (a, b) => {
  if (a === b) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || !a || !b) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) if (a[k] !== b[k]) return false;
  return true;
};

export default memo(function AnalyticsDashboard({ tab: externalTab, onTabChange, sdrPeaksRef, tele, active }) {
  const { t } = useTranslation();
  const dashboards = useMemo(() => DASHBOARDS(t), [t]);
  const [internalTab, setInternalTab] = useState(0);
  const tab = externalTab ?? internalTab;
  const setTab = (i) => {
    setInternalTab(i);
    if (onTabChange) onTabChange(i);
  };

  return (
    <div style={{
      position:'fixed', top:86, left:0, right:0, bottom:0, zIndex:50,
      background:'radial-gradient(ellipse at 30% 20%, rgba(0,229,255,0.04) 0%, rgba(5,7,15,0.98) 60%)',
      overflowY:'auto', fontFamily:SYS,
    }}>
      {/* Content */}
      <div style={{ padding:'12px 32px 40px', maxWidth:'95vw', margin:'0 auto', width:'100%' }}>
        {/* Subtitle */}
        <div style={{ marginBottom:20 }}>
          <div style={{ fontSize:12, color:'rgba(255,255,255,0.25)', fontFamily:SYS }}>{dashboards[tab]?.subtitle}</div>
        </div>

        {/* Keep OverviewDashboard always mounted so data is instantly available */}
        <div style={{ display: tab === 0 && active ? 'block' : 'none' }}>
          <OverviewDashboard sdrPeaksRef={sdrPeaksRef} tele={tele} active={active} />
        </div>
        {active && tab === 1 && (
          <div>
            <SpectrumTab tele={tele} />
          </div>
        )}
      </div>
    </div>
  );
}, (prev, next) => {
  return prev.active === next.active &&
         prev.tab === next.tab &&
         prev.sdrPeaksRef === next.sdrPeaksRef &&
         shallowEq(prev.tele, next.tele) &&
         prev.onTabChange === next.onTabChange;
});
