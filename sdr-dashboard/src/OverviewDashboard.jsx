import React, { useState, useMemo, useCallback, useEffect, useRef, memo } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { ArrowUp, ArrowDown, RotateCcw, ZoomIn, ZoomOut, Loader2 } from 'lucide-react';
import { useTranslation } from './i18n/LanguageContext';
import { useAuth } from './auth/AuthContext';
import useSdrThresholds from './useSdrThresholds';

// ── Design tokens ──────────────────────────────────────────────────────────────
const SYS   = `'Space Grotesk', sans-serif`;
const MONO  = `'JetBrains Mono', monospace`;
const DIGIT = `DSDigit,'JetBrains Mono',monospace`;
const CYAN  = '#00e5ff';
const AMBER = '#ffaf00';
const GREEN = '#69ff47';
const PURPLE = '#a855f7';
const RED    = '#ff5252';
const PINK   = '#f472b6';
const TEAL   = '#2dd4bf';
const ORANGE = '#fb923c';
const GUNASLI2 = '#34d399';

const GLASS = {
  background: 'rgba(8,12,22,0.92)',
  border: '1px solid rgba(0,229,255,0.12)',
  borderRadius: 16,
  backdropFilter: 'blur(20px)',
  boxShadow: '0 8px 48px rgba(0,0,0,0.8)',
};

const GLASS_DARK = {
  background: 'rgba(5,8,16,0.96)',
  border: '1px solid rgba(0,229,255,0.07)',
  borderRadius: 12,
  backdropFilter: 'blur(16px)',
};

// ── Session in-memory cache for heavy Analytics API calls ────────────────────────
const _apiCache = new Map(); // key -> { data, ts }
const _getCached = (key, ttl) => {
  const entry = _apiCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.ts > ttl) return null;
  return entry.data;
};
const _setCached = (key, data) => _apiCache.set(key, { data, ts: Date.now() });

// ── Helpers for real-time scan rows (matches Home left panel) ───────────────────
const DBFS_MIN = -90;
const DBFS_MAX = -20;
function _colorAt(p) {
  const CR = { r:0xff, g:0x57, b:0x22 };
  const CDK = { r:0x4a, g:0x00, b:0xe0 };
  const CP  = { r:0x00, g:0xe5, b:0xff };
  if (p >= 0.45) {
    const t = (p - 0.45) / 0.55;
    return { r: Math.round(CDK.r + (CP.r - CDK.r) * t), g: Math.round(CDK.g + (CP.g - CDK.g) * t), b: Math.round(CDK.b + (CP.b - CDK.b) * t) };
  }
  const t = p / 0.45;
  return { r: Math.round(CR.r + (CDK.r - CR.r) * t), g: Math.round(CR.g + (CDK.g - CR.g) * t), b: Math.round(CR.b + (CDK.b - CR.b) * t) };
}
const dbfsColor = (dbfs, cal = null) => {
  const min = cal?.db_min ?? DBFS_MIN;
  const max = cal?.db_max ?? DBFS_MAX;
  const p = Math.max(0, Math.min(1, (dbfs - min) / (max - min)));
  const { r, g, b } = _colorAt(p);
  return `rgb(${r},${g},${b})`;
};
const fmtFreq = (freq) => Math.abs(freq - 223.936) < 0.01 ? 'DAB+' : freq.toFixed(3);

// Inject styles once
if (typeof document !== 'undefined' && !document.getElementById('overview-style')) {
  const s = document.createElement('style');
  s.id = 'overview-style';
  s.textContent = `
    @keyframes ovFadeUp { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:translateY(0); } }
    @keyframes ovPulse  { 0%,100% { opacity:0.5; } 50% { opacity:1; } }
    @keyframes ovBarGrow { from { width:0; } to { width:var(--w); } }
    @keyframes ovSpin { from { transform:rotate(0deg); } to { transform:rotate(360deg); } }
    @keyframes ovRangeValue { from { opacity:0; transform:translateX(-50%) translateY(3px); } to { opacity:1; transform:translateX(-50%) translateY(0); } }
    @keyframes dhSliceIn { from { opacity:0; transform:scale(0.8); } to { opacity:1; transform:scale(1); } }
    .ov-fade-up { animation: ovFadeUp 0.35s cubic-bezier(.16,1,.3,1) both; }
    .ov-pulse   { animation: ovPulse 1.8s ease-in-out infinite; }
    .ov-spin    { animation: ovSpin 1s linear infinite; }
    .ov-menu-item:hover { background: rgba(0,229,255,0.08) !important; }
    .ov-menu-item.active { background: rgba(0,229,255,0.14) !important; border-color: rgba(0,229,255,0.4) !important; }
    .ov-tab-btn:hover { background: rgba(0,229,255,0.1) !important; }
    .ov-tab-btn.active { background: rgba(0,229,255,0.18) !important; color: #00e5ff !important; border-bottom: 2px solid #00e5ff !important; }
    .ov-scrollable { overflow-y: auto; }
    .ov-scrollable::-webkit-scrollbar { width: 8px; }
    .ov-scrollable::-webkit-scrollbar-track { background: rgba(0,229,255,0.04); border-radius: 4px; }
    .ov-scrollable::-webkit-scrollbar-thumb { background: rgba(0,229,255,0.25); border-radius: 4px; }
    .ov-scrollable::-webkit-scrollbar-thumb:hover { background: rgba(0,229,255,0.4); }
    .ov-ti-btn:hover { background: rgba(0,229,255,0.1) !important; }
    .ov-ti-btn.active { background: rgba(0,229,255,0.18) !important; color: #00e5ff !important; }
  `;
  document.head.appendChild(s);
}

// ── Full SDR / RTYS / Station frequency data ──────────────────────────────────
// Derived from sdr-rtys.txt and SDR-RTYS.md
const SDR_DATA = {
  Site-3: {
    label: 'Beyləqan SDR',
    color: CYAN,
    rtys: {
      'Daşkəsən RTYS':    [{ name:'Avto', freq:103.7 }, { name:'Kəpəz', freq:93.3 }, { name:'TMB', freq:100.5 }],
      'Gülüstan RTYS':    [{ name:'Hit', freq:99.6 }, { name:'Araz', freq:97.3 }, { name:'Asan', freq:95.6 }, { name:'Retro', freq:95.1 }, { name:'Yurd', freq:91.8 }, { name:'TMB', freq:91.0 }, { name:'A.Azərbaycan', freq:90.5 }, { name:'Xəzər', freq:89.6 }, { name:'Azərbaycan', freq:88.0 }, { name:'Space', freq:87.5 }, { name:'Media', freq:107.0 }, { name:'Avto', freq:106.7 }, { name:'İctimai', freq:102.5 }, { name:'Enerji', freq:101.3 }],
      'Lerik RTYS':       [{ name:'Azərbaycan', freq:101.5 }, { name:'Enerji', freq:102.3 }, { name:'TMB', freq:102.8 }, { name:'Araz', freq:103.5 }, { name:'A.Azərbaycan', freq:104.8 }, { name:'Space', freq:105.3 }, { name:'Avto', freq:107.5 }, { name:'Hit', freq:87.7 }, { name:'Xəzər', freq:89.0 }, { name:'İctimai', freq:89.3 }, { name:'Asan', freq:92.7 }, { name:'Yurd', freq:93.7 }, { name:'Retro', freq:96.5 }],
      'Turşsu RTYS':      [{ name:'İctimai', freq:91.3 }, { name:'Azərbaycan', freq:105.7 }],
      'Təpəcənnət RTYS':  [{ name:'Enerji', freq:104.1 }, { name:'Avto', freq:107.8 }, { name:'İctimai', freq:91.6 }, { name:'Araz', freq:93.0 }, { name:'Azərbaycan', freq:94.6 }, { name:'Yurd', freq:94.9 }, { name:'Xəzər', freq:96.3 }, { name:'Media', freq:96.9 }],
      'İvanovka RTYS':    [{ name:'Real', freq:98.8 }],
      'Şuşa RTYS':        [{ name:'Retro', freq:100.7 }, { name:'Azərbaycan', freq:101.7 }, { name:'CBC', freq:102.7 }, { name:'Enerji', freq:104.3 }, { name:'Beynəlxalq', freq:105.1 }, { name:'Media', freq:105.5 }, { name:'Space', freq:106.3 }, { name:'Avto', freq:107.3 }, { name:'İctimai', freq:88.7 }, { name:'Xəzər', freq:89.2 }, { name:'TMB', freq:93.5 }, { name:'Yurd', freq:96.0 }, { name:'Hit', freq:97.1 }, { name:'A.Azərbaycan', freq:98.3 }, { name:'Araz', freq:98.6 }, { name:'Real', freq:99.0 }, { name:'Asan', freq:99.4 }],
      'İran (kənar)':     [{ name:'Quran', freq:99.9 }, { name:'İRİB Azeri', freq:99.3 }, { name:'Javan', freq:97.9 }, { name:'Maaref', freq:95.9 }, { name:'Sh.Azerbaycan', freq:94.4 }, { name:'Ardabil', freq:93.9 }, { name:'Varzesh', freq:91.9 }, { name:'İran', freq:89.9 }, { name:'Farhang', freq:106.4 }, { name:'Payam', freq:103.9 }, { name:'Farhang', freq:101.9 }, { name:'Javan(vurulur)', freq:88.3 }],
    }
  },
  Site-4: {
    label: 'Gəncə SDR',
    color: AMBER,
    rtys: {
      'Daşkəsən RTYS':    [{ name:'TMB', freq:100.5 }, { name:'Enerji', freq:101.0 }, { name:'Azərbaycan', freq:101.5 }, { name:'Araz', freq:103.3 }, { name:'Avto', freq:103.7 }, { name:'Media', freq:105.3 }, { name:'A.Azərbaycan', freq:107.5 }, { name:'Retro', freq:88.3 }, { name:'Xəzər', freq:89.0 }, { name:'İctimai', freq:90.0 }, { name:'Hit', freq:92.0 }, { name:'Kəpəz', freq:93.3 }, { name:'Real', freq:93.6 }, { name:'Yurd', freq:95.8 }, { name:'Asan', freq:96.5 }, { name:'Space', freq:99.2 }],
      'Gülüstan RTYS':    [{ name:'Enerji', freq:101.3 }, { name:'İctimai', freq:102.5 }, { name:'Avto', freq:106.7 }, { name:'Media', freq:107.0 }, { name:'Azərbaycan', freq:88.0 }, { name:'Xəzər', freq:89.6 }, { name:'A.Azərbaycan', freq:90.5 }, { name:'TMB', freq:91.0 }, { name:'Yurd', freq:91.8 }, { name:'Retro', freq:95.1 }, { name:'Asan', freq:95.6 }, { name:'Araz', freq:97.3 }, { name:'Hit', freq:99.6 }],
      'Gəncə RTYS':       [{ name:'A.Azərbaycan', freq:94.3 }, { name:'Space', freq:106.0 }],
      'Poylu RTYS':       [{ name:'Asan', freq:100.0 }, { name:'Kəpəz', freq:102.7 }, { name:'İctimai', freq:103.2 }, { name:'Avto', freq:107.7 }, { name:'Xəzər', freq:87.5 }, { name:'Retro', freq:88.4 }, { name:'Yurd', freq:88.7 }, { name:'Space', freq:89.4 }, { name:'Hit', freq:90.9 }, { name:'Media', freq:91.5 }, { name:'Real', freq:96.1 }, { name:'Araz', freq:97.0 }],
      'Təpəcənnət RTYS':  [{ name:'A.Azərbaycan', freq:102.0 }, { name:'Enerji', freq:104.1 }, { name:'Avto', freq:107.8 }, { name:'TMB', freq:87.7 }, { name:'İctimai', freq:91.6 }, { name:'Real', freq:92.4 }, { name:'Araz', freq:93.0 }, { name:'Azərbaycan', freq:94.6 }, { name:'Yurd', freq:94.9 }, { name:'Xəzər', freq:96.3 }, { name:'Media', freq:96.9 }, { name:'Asan', freq:99.8 }],
      'İvanovka RTYS':    [{ name:'Real', freq:98.8 }],
      'Gürcüstan (kənar)': [{ name:'Eti1', freq:91.1 }, { name:'İmedi', freq:100.3 }, { name:'İmedi', freq:104.7 }],
    }
  },
  gunasli: {
    label: 'Günəşli 1 SDR',
    color: GREEN,
    rtys: {
      'BTQK':             [{ name:'Asan', freq:100.0 }, { name:'TMB', freq:100.5 }, { name:'Enerji', freq:101.1 }, { name:'XY', freq:101.3 }, { name:'Xəzər', freq:103.0 }, { name:'Araz', freq:103.3 }, { name:'Space', freq:104.0 }, { name:'Azərbaycan', freq:105.0 }, { name:'Media', freq:105.5 }, { name:'A.Azərbaycan', freq:106.0 }, { name:'A.Azərbaycan', freq:106.3 }, { name:'Real', freq:107.0 }, { name:'Avto', freq:107.7 }, { name:'Planet', freq:88.7 }, { name:'İctimai', freq:90.0 }, { name:'Yurd', freq:90.7 }, { name:'Dalga', freq:92.5 }, { name:'Saglam', freq:93.0 }, { name:'Retro', freq:93.3 }, { name:'Hit', freq:97.7 }, { name:'XY(İctimai)', freq:99.3 }],
      'Gilgilçay RTYS':   [{ name:'Araz', freq:100.8 }, { name:'A.Azərbaycan', freq:93.6 }, { name:'Space', freq:95.4 }, { name:'TMB', freq:95.7 }, { name:'Real', freq:96.0 }, { name:'Avto', freq:97.2 }, { name:'Media', freq:98.5 }, { name:'Asan', freq:99.0 }],
      'Lerik RTYS':       [{ name:'Azərbaycan', freq:101.5 }, { name:'Enerji', freq:102.3 }, { name:'TMB', freq:102.8 }, { name:'Araz', freq:103.5 }, { name:'A.Azərbaycan', freq:104.8 }, { name:'Space', freq:105.3 }, { name:'Media', freq:105.7 }, { name:'Avto', freq:107.5 }, { name:'Hit', freq:87.7 }, { name:'Xəzər', freq:89.0 }, { name:'İctimai', freq:89.3 }, { name:'Real', freq:91.3 }, { name:'Asan', freq:92.7 }, { name:'Yurd', freq:93.7 }, { name:'Retro', freq:96.5 }],
      'Xizi RTYS':        [{ name:'Azərbaycan', freq:89.6 }, { name:'İctimai', freq:103.8 }],
      'Yergüc RTYS':      [{ name:'Azərbaycan', freq:104.5 }, { name:'Hit', freq:88.0 }, { name:'Media', freq:90.3 }, { name:'Retro', freq:91.7 }, { name:'Asan', freq:92.3 }, { name:'Enerji', freq:94.1 }, { name:'Space', freq:94.9 }, { name:'İctimai', freq:98.1 }, { name:'Avto', freq:98.7 }],
      'İran (kənar)':     [{ name:'Farhang', freq:101.9 }, { name:'Varzesh', freq:91.9 }, { name:'Ardabil', freq:93.9 }, { name:'Maaref', freq:95.9 }, { name:'Javan', freq:97.9 }, { name:'Egtesad', freq:98.3 }],
      'Türkmənistan (kənar)': [{ name:'Türkmənistan', freq:99.7 }, { name:'Miras', freq:99.8 }],
    }
  },
  gunasli2: {
    label: 'Günəşli 2 SDR',
    color: GUNASLI2,
    rtys: {
      'BTQK':             [{ name:'Asan', freq:100.0 }, { name:'TMB', freq:100.5 }, { name:'Enerji', freq:101.1 }, { name:'XY', freq:101.3 }, { name:'Xəzər', freq:103.0 }, { name:'Araz', freq:103.3 }, { name:'Space', freq:104.0 }, { name:'Azərbaycan', freq:105.0 }, { name:'Media', freq:105.5 }, { name:'A.Azərbaycan', freq:106.0 }, { name:'A.Azərbaycan', freq:106.3 }, { name:'Real', freq:107.0 }, { name:'Avto', freq:107.7 }, { name:'Planet', freq:88.7 }, { name:'İctimai', freq:90.0 }, { name:'Yurd', freq:90.7 }, { name:'Dalga', freq:92.5 }, { name:'Saglam', freq:93.0 }, { name:'Retro', freq:93.3 }, { name:'Hit', freq:97.7 }, { name:'XY(İctimai)', freq:99.3 }],
      'Gilgilçay RTYS':   [{ name:'Araz', freq:100.8 }, { name:'A.Azərbaycan', freq:93.6 }, { name:'Space', freq:95.4 }, { name:'TMB', freq:95.7 }, { name:'Real', freq:96.0 }, { name:'Avto', freq:97.2 }, { name:'Media', freq:98.5 }, { name:'Asan', freq:99.0 }],
      'Lerik RTYS':       [{ name:'Azərbaycan', freq:101.5 }, { name:'Enerji', freq:102.3 }, { name:'TMB', freq:102.8 }, { name:'Araz', freq:103.5 }, { name:'A.Azərbaycan', freq:104.8 }, { name:'Space', freq:105.3 }, { name:'Media', freq:105.7 }, { name:'Avto', freq:107.5 }, { name:'Hit', freq:87.7 }, { name:'Xəzər', freq:89.0 }, { name:'İctimai', freq:89.3 }, { name:'Real', freq:91.3 }, { name:'Asan', freq:92.7 }, { name:'Yurd', freq:93.7 }, { name:'Retro', freq:96.5 }],
      'Xizi RTYS':        [{ name:'Azərbaycan', freq:89.6 }, { name:'İctimai', freq:103.8 }],
      'Yergüc RTYS':      [{ name:'Azərbaycan', freq:104.5 }, { name:'Hit', freq:88.0 }, { name:'Media', freq:90.3 }, { name:'Retro', freq:91.7 }, { name:'Asan', freq:92.3 }, { name:'Enerji', freq:94.1 }, { name:'Space', freq:94.9 }, { name:'İctimai', freq:98.1 }, { name:'Avto', freq:98.7 }],
      'İran (kənar)':     [{ name:'Farhang', freq:101.9 }, { name:'Varzesh', freq:91.9 }, { name:'Ardabil', freq:93.9 }, { name:'Maaref', freq:95.9 }, { name:'Javan', freq:97.9 }, { name:'Egtesad', freq:98.3 }],
      'Türkmənistan (kənar)': [{ name:'Türkmənistan', freq:99.7 }, { name:'Miras', freq:99.8 }],
    }
  },
  Site-5: {
    label: 'Masallı SDR',
    color: PURPLE,
    rtys: {
      'Astara RTYS':      [{ name:'Asan', freq:102.0 }, { name:'İctimai', freq:104.6 }, { name:'Araz', freq:107.3 }, { name:'Avto', freq:88.5 }, { name:'Enerji', freq:89.5 }, { name:'Space', freq:91.5 }, { name:'Azərbaycan', freq:93.5 }, { name:'Real', freq:94.3 }, { name:'Hit', freq:96.7 }, { name:'Xəzər', freq:97.2 }, { name:'TMB', freq:97.8 }, { name:'A.Azərbaycan', freq:98.1 }, { name:'Media', freq:98.5 }, { name:'Retro', freq:99.5 }],
      'Lerik RTYS':       [{ name:'Azərbaycan', freq:101.5 }, { name:'Enerji', freq:102.3 }, { name:'TMB', freq:102.8 }, { name:'Araz', freq:103.5 }, { name:'A.Azərbaycan', freq:104.8 }, { name:'Space', freq:105.3 }, { name:'Media', freq:105.7 }, { name:'Avto', freq:107.5 }, { name:'Hit', freq:87.7 }, { name:'Xəzər', freq:89.0 }, { name:'İctimai', freq:89.3 }, { name:'Real', freq:91.3 }, { name:'Asan', freq:92.7 }, { name:'Yurd', freq:93.7 }, { name:'Retro', freq:96.5 }],
      'Lənkəran RTYS':    [{ name:'Space', freq:104.0 }, { name:'A.Azərbaycan', freq:107.8 }],
      'İran (kənar)':     [{ name:'Quran', freq:100.3 }, { name:'Farhang', freq:101.9 }, { name:'Payam', freq:104.3 }, { name:'Farhang', freq:106.3 }, { name:'İran', freq:89.9 }, { name:'İran', freq:90.3 }, { name:'Varzesh', freq:91.9 }, { name:'Maaref', freq:95.9 }, { name:'Javan', freq:97.9 }, { name:'Eghtesad', freq:98.3 }],
    }
  },
  Site-6: {
    label: 'Qəbələ SDR',
    color: PINK,
    rtys: {
      'Daşkəsən RTYS':    [{ name:'Retro', freq:88.3 }, { name:'İctimai', freq:90.0 }, { name:'Hit', freq:92.0 }, { name:'Kəpəz', freq:93.3 }, { name:'Real', freq:93.6 }, { name:'Yurd', freq:95.8 }, { name:'Asan', freq:96.5 }, { name:'Space', freq:99.2 }, { name:'TMB', freq:100.5 }, { name:'Enerji', freq:101.0 }, { name:'Azərbaycan', freq:101.5 }, { name:'Araz', freq:103.3 }, { name:'Avto', freq:103.7 }, { name:'A.Azərbaycan', freq:107.5 }],
      'Gülüstan RTYS':    [{ name:'Space', freq:87.5 }, { name:'Azərbaycan', freq:88.0 }, { name:'Xəzər', freq:89.6 }, { name:'A.Azərbaycan', freq:90.5 }, { name:'TMB', freq:91.0 }, { name:'Yurd', freq:91.8 }, { name:'Retro', freq:95.1 }, { name:'Asan', freq:95.6 }, { name:'Araz', freq:97.3 }, { name:'Hit', freq:99.6 }, { name:'Enerji', freq:101.3 }, { name:'İctimai', freq:102.5 }, { name:'Avto', freq:106.7 }, { name:'Media', freq:107.0 }],
      'Gəncə RTYS':       [{ name:'A.Azərbaycan', freq:94.3 }, { name:'Space', freq:106.0 }],
      'İvanovka RTYS':    [{ name:'Real', freq:98.8 }, { name:'Xəzər', freq:89.0 }, { name:'İctimai', freq:89.3 }, { name:'Asan', freq:92.7 }, { name:'Yurd', freq:93.7 }, { name:'Enerji', freq:102.3 }, { name:'TMB', freq:102.8 }, { name:'Araz', freq:103.5 }, { name:'A.Azərbaycan', freq:104.8 }, { name:'Space', freq:105.3 }, { name:'Media', freq:105.7 }],
      'Şahyeri RTYS':     [{ name:'Avto', freq:90.7 }, { name:'A.Azərbaycan', freq:92.1 }, { name:'İctimai', freq:92.5 }, { name:'Asan', freq:95.3 }, { name:'TMB', freq:96.7 }, { name:'Azərbaycan', freq:97.5 }, { name:'Retro', freq:97.8 }, { name:'Enerji', freq:98.1 }, { name:'Xəzər', freq:103.0 }, { name:'Media', freq:105.9 }],
      'Şirvan RTYS':      [{ name:'Azərbaycan', freq:106.5 }],
      'Şuşa RTYS':        [{ name:'İctimai', freq:88.7 }, { name:'Xəzər', freq:89.2 }, { name:'TMB', freq:93.5 }, { name:'Yurd', freq:96.0 }, { name:'Hit', freq:97.1 }, { name:'A.Azərbaycan', freq:98.3 }, { name:'Araz', freq:98.6 }, { name:'Real', freq:99.0 }, { name:'Asan', freq:99.4 }, { name:'Retro', freq:100.7 }, { name:'Azərbaycan', freq:101.7 }, { name:'CBC', freq:102.7 }, { name:'Enerji', freq:104.3 }, { name:'Beynəlxalq', freq:105.1 }, { name:'Media', freq:105.5 }, { name:'Space', freq:106.3 }, { name:'Avto', freq:107.3 }],
      'Təpəcənnət RTYS':  [{ name:'TMB', freq:87.7 }, { name:'İctimai', freq:91.6 }, { name:'Real', freq:92.4 }, { name:'Araz', freq:93.0 }, { name:'Azərbaycan', freq:94.6 }, { name:'Yurd', freq:94.9 }, { name:'Xəzər', freq:96.3 }, { name:'Media', freq:96.9 }, { name:'Asan', freq:99.8 }, { name:'A.Azərbaycan', freq:102.0 }, { name:'Enerji', freq:104.1 }, { name:'Avto', freq:107.8 }],
      'İran (kənar)':     [{ name:'Iran', freq:89.9 }, { name:'Varzesh', freq:91.9 }, { name:'Ardabil', freq:93.9 }, { name:'Sh.Azerbaycan', freq:94.4 }, { name:'Maaref', freq:95.9 }, { name:'Javan', freq:97.9 }, { name:'IRIB Azeri', freq:99.3 }, { name:'Quran', freq:99.9 }, { name:'Farhang', freq:101.9 }, { name:'Payam', freq:103.9 }, { name:'Farhang', freq:106.4 }],
      'Ermənistan (kənar)': [{ name:'Yeravan', freq:90.3 }],
    }
  },
  Site-7: {
    label: 'Site-7 SDR',
    color: TEAL,
    rtys: {
      'Cibir RTYS':       [{ name:'İctimai', freq:88.3 }, { name:'Azərbaycan', freq:101.5 }],
      'Site-7 RTYS':        [{ name:'Real', freq:89.0 }, { name:'Retro', freq:90.9 }, { name:'İctimai', freq:92.1 }, { name:'Yurd', freq:92.6 }, { name:'Space', freq:94.3 }, { name:'Asan', freq:96.5 }, { name:'Enerji', freq:101.0 }, { name:'Avto', freq:102.3 }, { name:'Xəzər', freq:103.0 }, { name:'Araz', freq:103.3 }, { name:'Media', freq:105.5 }, { name:'A.Azərbaycan', freq:106.3 }],
      'Şahdağ ARTS':      [{ name:'İctimai', freq:89.7 }, { name:'Retro', freq:93.3 }, { name:'Real', freq:93.7 }, { name:'Media', freq:97.0 }, { name:'Avto', freq:97.6 }, { name:'TMB', freq:100.5 }, { name:'Xəzər', freq:102.0 }, { name:'Asan', freq:102.5 }, { name:'Azərbaycan', freq:105.8 }, { name:'Hit', freq:107.0 }, { name:'A.Azərbaycan', freq:107.3 }, { name:'Yurd', freq:107.7 }],
      'Yergüc RTYS':      [{ name:'Yurd', freq:87.7 }, { name:'Hit', freq:88.0 }, { name:'Xəzər', freq:88.6 }, { name:'Real', freq:89.3 }, { name:'TMB', freq:89.9 }, { name:'Media', freq:90.3 }, { name:'Retro', freq:91.7 }, { name:'Asan', freq:92.3 }, { name:'Araz', freq:92.9 }, { name:'Enerji', freq:94.1 }, { name:'Space', freq:94.9 }, { name:'İctimai', freq:98.1 }, { name:'Avto', freq:98.7 }, { name:'A.Azərbaycan', freq:99.7 }, { name:'Azərbaycan', freq:104.5 }],
      'Rusiya (kənar)':   [{ name:'Assa', freq:93.1 }, { name:'Zvezda', freq:98.4 }, { name:'Avto', freq:99.4 }, { name:'Priboy', freq:100.1 }, { name:'Sedmoye nebo', freq:101.8 }, { name:'Vatan', freq:102.7 }, { name:'Mayak', freq:103.7 }, { name:'Vatan', freq:104.3 }, { name:'Rossiya', freq:104.9 }, { name:'Strana qor', freq:106.0 }, { name:'Dorojnoye', freq:106.4 }, { name:'Vesti', freq:106.8 }],
    }
  },
  Site-8: {
    label: 'Şuşa SDR',
    color: ORANGE,
    rtys: {
      'Gülüstan RTYS':    [{ name:'Space', freq:87.5 }, { name:'Azərbaycan', freq:88.0 }, { name:'Xəzər', freq:89.6 }, { name:'A.Azərbaycan', freq:90.5 }, { name:'TMB', freq:91.0 }, { name:'Yurd', freq:91.8 }, { name:'Retro', freq:95.1 }, { name:'Asan', freq:95.6 }, { name:'Araz', freq:97.3 }, { name:'Hit', freq:99.6 }, { name:'Enerji', freq:101.3 }, { name:'İctimai', freq:102.5 }, { name:'Avto', freq:106.7 }, { name:'Media', freq:107.0 }],
      'Şahyeri RTYS':     [{ name:'Avto', freq:90.7 }, { name:'A.Azərbaycan', freq:92.1 }, { name:'İctimai', freq:92.5 }, { name:'Asan', freq:95.3 }, { name:'TMB', freq:96.7 }, { name:'Azərbaycan', freq:97.5 }, { name:'Retro', freq:97.8 }, { name:'Enerji', freq:98.1 }, { name:'Xəzər', freq:103.0 }, { name:'Araz', freq:103.3 }, { name:'Media', freq:105.9 }],
      'Şuşa RTYS':        [{ name:'İctimai', freq:88.7 }, { name:'Xəzər', freq:89.2 }, { name:'TMB', freq:93.5 }, { name:'Yurd', freq:96.0 }, { name:'Hit', freq:97.1 }, { name:'A.Azərbaycan', freq:98.3 }, { name:'Araz', freq:98.6 }, { name:'Real', freq:99.0 }, { name:'Asan', freq:99.4 }, { name:'Retro', freq:100.7 }, { name:'Azərbaycan', freq:101.7 }, { name:'CBC', freq:102.7 }, { name:'Enerji', freq:104.3 }, { name:'Beynəlxalq', freq:105.1 }, { name:'Media', freq:105.5 }, { name:'Space', freq:106.3 }, { name:'Avto', freq:107.3 }],
      'Təpəcənnət RTYS':  [{ name:'TMB', freq:87.7 }, { name:'İctimai', freq:91.6 }, { name:'Real', freq:92.4 }, { name:'Araz', freq:93.0 }, { name:'Azərbaycan', freq:94.6 }, { name:'Yurd', freq:94.9 }, { name:'Xəzər', freq:96.3 }, { name:'Media', freq:96.9 }, { name:'Asan', freq:99.8 }, { name:'A.Azərbaycan', freq:102.0 }, { name:'Enerji', freq:104.1 }, { name:'Avto', freq:107.8 }],
      'İvanovka RTYS':    [{ name:'Real', freq:98.8 }],
    }
  },
};

const SDR_IDS = Object.keys(SDR_DATA);
const SDR_API_IDS = { Site-3: 'site_3', Site-4: 'site_4', gunasli: 'site_1', gunasli2: 'site_2', Site-5: 'site_5', Site-6: 'site_6', Site-7: 'site_7', Site-8: 'site_8' };
const API_ID_TO_SDR = Object.fromEntries(Object.entries(SDR_API_IDS).map(([k, v]) => [v, k]));
const MAIN_RTYS_BY_SDR = {
  Site-3: 'Şuşa RTYS',
  Site-4: 'Daşkəsən RTYS',
  gunasli: 'BTQK',
  gunasli2: 'BTQK',
  Site-5: 'Lerik RTYS',
  Site-6: 'Şuşa RTYS',
  Site-7: 'Yergüc RTYS',
  Site-8: 'Şuşa RTYS',
};
const telemetryKey = (sdrId, freq) => `${sdrId}|${Number(freq).toFixed(3)}`;
const _freqIndex = new WeakMap();
const _buildFreqIndex = (readings) => {
  const sorted = readings
    .filter(r => Number.isFinite(Number(r?.freq)))
    .map(r => ({ freq: Number(r.freq), reading: r }))
    .sort((a, b) => a.freq - b.freq);
  return {
    sorted,
    getClosest: (frequency) => {
      const f = Number(frequency);
      if (!Number.isFinite(f)) return null;
      let lo = 0, hi = sorted.length - 1, pos = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const midF = sorted[mid].freq;
        if (midF < f) { lo = mid + 1; }
        else if (midF > f) { hi = mid - 1; }
        else { pos = mid; break; }
      }
      const candidates = [];
      if (pos !== -1) candidates.push(sorted[pos]);
      else {
        if (hi >= 0) candidates.push(sorted[hi]);
        if (lo < sorted.length) candidates.push(sorted[lo]);
      }
      let best = null;
      for (const c of candidates) {
        const d = Math.abs(c.freq - f);
        if (d >= 0.15) continue;
        if (!best || d < Math.abs(best.freq - f)) best = c;
      }
      return best ? best.reading : null;
    }
  };
};
const findFrequencyMatch = (readings, frequency) => {
  if (!Array.isArray(readings) || readings.length === 0) return null;
  if (readings.length < 30) {
    return readings.reduce((closest, reading) => {
      if (!Number.isFinite(Number(reading?.freq))) return closest;
      const distance = Math.abs(Number(reading.freq) - Number(frequency));
      if (distance >= 0.15) return closest;
      return !closest || distance < Math.abs(Number(closest.freq) - Number(frequency)) ? reading : closest;
    }, null);
  }
  let index = _freqIndex.get(readings);
  if (!index) {
    index = _buildFreqIndex(readings);
    _freqIndex.set(readings, index);
  }
  return index.getClosest(frequency);
};

// ── Smart search helpers ──────────────────────────────────────────────────────
const AZERI_MAP = {
  'Ə':'E','ə':'e','Ş':'S','ş':'s','Ç':'C','ç':'c','Ğ':'G','ğ':'g','Ü':'U','ü':'u','Ö':'O','ö':'o','İ':'I','ı':'i','Â':'A','â':'a','Î':'I','î':'i','Û':'U','û':'u',
};
const normalizeAzeri = (s) => s.replace(/[ƏŞÇĞÜÖİÂÎÛəşçğüöıâîû]/g, ch => AZERI_MAP[ch] || ch);
const normalizeSearch = (s) => normalizeAzeri(s).toLowerCase().replace(/[^a-z0-9]/g, '');

function levenshtein(a, b) {
  if (a.length < b.length) [a, b] = [b, a];
  if (b.length === 0) return a.length;
  let prev = new Array(b.length + 1).fill(0).map((_, i) => i);
  let curr = new Array(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    curr[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost);
    }
    [prev, curr] = [curr, prev];
  }
  return prev[b.length];
}

function fuzzyMatch(text, query) {
  const nText = normalizeSearch(text);
  const nQuery = normalizeSearch(query);
  if (!nQuery) return true;
  if (nText.includes(nQuery)) return true;
  if (nQuery.length >= 3) {
    const maxDist = Math.min(2, Math.floor(nQuery.length / 3));
    if (levenshtein(nText, nQuery) <= maxDist) return true;
    const words = text.split(/[\s/–-]+/).map(normalizeSearch).filter(Boolean);
    for (const w of words) {
      if (w.startsWith(nQuery)) return true;
      if (levenshtein(w, nQuery) <= maxDist) return true;
    }
  }
  return false;
}

function parseFreqToken(token) {
  const cleaned = token.replace(/[\u00A0]/g, '').replace(/,/g, '.').replace(/[^0-9.-]/g, '');
  if (!/^-?\d+(\.\d+)?$/.test(cleaned)) return null;
  const num = parseFloat(cleaned);
  if (!Number.isFinite(num)) return null;
  const isInteger = !cleaned.includes('.');
  const int = Math.abs(Math.round(num));
  let value;
  if (!isInteger) {
    value = num;
  } else if (int <= 300) {
    value = num;
  } else if (int >= 8750 && int <= 10800) {
    value = num / 100;
  } else if (int > 10000) {
    value = num / 1000;
  } else {
    value = num;
  }
  return { value, isInteger };
}

function tokenizeSearch(q) {
  const textTokens = [];
  const freqTokens = [];
  q.trim().split(/\s+/).forEach(token => {
    if (!token) return;
    const f = parseFreqToken(token);
    if (f != null) freqTokens.push(f);
    else textTokens.push(token);
  });
  return { textTokens, freqTokens };
}

// ── Interactive Pie/Donut Chart ───────────────────────────────────────────────
function InteractivePieChart({ slices, size = 420, onHover, onSelect, selectedIdx, innerRadius, outerRadius, centerLabel }) {
  const canvasRef = useRef(null);
  const hoveredRef = useRef(-1);
  const animRef = useRef({});
  const rafRef = useRef(null);

  const DPR = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1;
  const R = outerRadius ?? size * 0.38;
  const INNER_R = innerRadius ?? size * 0.21;
  const EXPLODE = size * 0.045;
  const cx = size / 2, cy = size / 2;

  // Build arc data
  const arcs = useMemo(() => {
    const total = slices.reduce((a, s) => a + s.value, 0);
    if (total === 0) return [];
    let start = -Math.PI / 2;
    return slices.map((s, i) => {
      const sweep = (s.value / total) * Math.PI * 2;
      const mid = start + sweep / 2;
      const arc = { ...s, start, sweep, mid, total, pct: ((s.value / total) * 100).toFixed(1) };
      start += sweep;
      return arc;
    });
  }, [slices]);

  // Hit-test: which slice is under (mx, my)?
  const hitTest = useCallback((mx, my) => {
    const dx = mx - cx, dy = my - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < INNER_R || dist > R + EXPLODE + 8) return -1;
    let angle = Math.atan2(dy, dx);
    if (angle < -Math.PI / 2) angle += Math.PI * 2;
    for (let i = 0; i < arcs.length; i++) {
      const a = arcs[i];
      let s = a.start, e = a.start + a.sweep;
      if (s < -Math.PI / 2) s += Math.PI * 2;
      if (e < s) e += Math.PI * 2;
      let a2 = angle < s ? angle + Math.PI * 2 : angle;
      if (a2 >= s && a2 <= e) return i;
    }
    return -1;
  }, [arcs, cx, cy, R, INNER_R, EXPLODE]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || arcs.length === 0) return;
    const ctx = canvas.getContext('2d');
    const w = size * DPR, h = size * DPR;
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.scale(DPR, DPR);

    const hov = hoveredRef.current;

    arcs.forEach((arc, i) => {
      const isHov = i === hov;
      const isSel = i === selectedIdx;
      const explode = (isHov || isSel) ? EXPLODE : 0;
      const ox = explode * Math.cos(arc.mid);
      const oy = explode * Math.sin(arc.mid);

      // Shadow glow on hover
      if (isHov || isSel) {
        ctx.shadowColor = arc.color;
        ctx.shadowBlur = 18;
      } else {
        ctx.shadowBlur = 0;
      }

      ctx.beginPath();
      ctx.moveTo(cx + ox, cy + oy);
      ctx.arc(cx + ox, cy + oy, R, arc.start, arc.start + arc.sweep);
      ctx.lineTo(cx + ox, cy + oy);
      ctx.closePath();
      ctx.fillStyle = arc.color;
      ctx.globalAlpha = isHov || isSel ? 1 : 0.78;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.shadowBlur = 0;

      // Separator stroke
      ctx.strokeStyle = 'rgba(5,8,16,0.85)';
      ctx.lineWidth = 2.5;
      ctx.stroke();

      // Outer arc highlight on hover
      if (isHov || isSel) {
        ctx.beginPath();
        ctx.arc(cx + ox, cy + oy, R + 4, arc.start + 0.02, arc.start + arc.sweep - 0.02);
        ctx.strokeStyle = arc.color;
        ctx.lineWidth = 2;
        ctx.globalAlpha = 0.6;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      // % label on slice (only if sweep big enough and pct meaningful)
      if (arc.sweep > 0.38 && Number(arc.pct) >= 5) {
        const labelR = R * 0.70;
        const lx = cx + ox + labelR * Math.cos(arc.mid);
        const ly = cy + oy + labelR * Math.sin(arc.mid);
        ctx.fillStyle = 'rgba(255,255,255,0.95)';
        ctx.font = `700 ${Math.max(10, Math.round(size * 0.022))}px 'Space Grotesk', sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(`${arc.pct}%`, lx, ly);
      }
    });

    // Donut hole
    ctx.shadowBlur = 0;
    ctx.beginPath();
    ctx.arc(cx, cy, INNER_R, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(5,8,16,0.97)';
    ctx.fill();

    // Center label
    const activeArc = (hov >= 0 && arcs[hov]) ? arcs[hov] : (selectedIdx >= 0 && arcs[selectedIdx]) ? arcs[selectedIdx] : null;
    const labelText = activeArc ? (centerLabel ?? activeArc.label) : null;
    if (activeArc) {
      ctx.fillStyle = activeArc.color;
      ctx.font = `700 ${Math.round(size * 0.065)}px 'Space Grotesk', sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(activeArc.value, cx, cy - size * 0.06);
      ctx.fillStyle = 'rgba(255,255,255,0.45)';
      ctx.font = `400 ${Math.round(size * 0.042)}px 'Space Grotesk', sans-serif`;
      ctx.fillText(`${activeArc.pct}%`, cx, cy + size * 0.02);
      if (labelText) {
        ctx.fillStyle = 'rgba(255,255,255,0.75)';
        ctx.font = `500 ${Math.round(size * 0.04)}px 'Space Grotesk', sans-serif`;
        ctx.fillText(labelText, cx, cy + size * 0.09);
      }
    } else {
      const total = arcs[0]?.total ?? 0;
      ctx.fillStyle = CYAN;
      ctx.font = `700 ${Math.round(size * 0.072)}px 'Space Grotesk', sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(total, cx, cy - size * 0.04);
      ctx.fillStyle = 'rgba(255,255,255,0.3)';
      ctx.font = `400 ${Math.round(size * 0.042)}px 'Space Grotesk', sans-serif`;
      ctx.fillText('drops', cx, cy + size * 0.03);
    }

    ctx.restore();
  }, [arcs, size, DPR, cx, cy, R, INNER_R, EXPLODE, selectedIdx, centerLabel]);

  // Redraw on data / selectedIdx change
  useEffect(() => { draw(); }, [draw]);

  // Set canvas physical size
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = size * DPR;
    canvas.height = size * DPR;
    draw();
  }, [size, DPR, draw]);

  const handleMouseMove = useCallback((e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const idx = hitTest(mx, my);
    if (idx !== hoveredRef.current) {
      hoveredRef.current = idx;
      onHover && onHover(idx >= 0 ? arcs[idx] : null, idx);
      draw();
    }
  }, [arcs, hitTest, draw, onHover]);

  const handleMouseLeave = useCallback(() => {
    hoveredRef.current = -1;
    onHover && onHover(null, -1);
    draw();
  }, [draw, onHover]);

  const handleClick = useCallback((e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const idx = hitTest(mx, my);
    onSelect && onSelect(idx >= 0 ? arcs[idx] : null, idx);
  }, [arcs, hitTest, onSelect]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: size, height: size, cursor: 'pointer', display: 'block' }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onClick={handleClick}
    />
  );
}

// ── Simple pretty animated SVG donut for Drop History ────────────────────────────
function polar(cx, cy, r, angle) {
  return { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
}

function donutSlicePath(cx, cy, innerR, outerR, startAngle, endAngle) {
  const startOut = polar(cx, cy, outerR, startAngle);
  const endOut = polar(cx, cy, outerR, endAngle);
  const startIn = polar(cx, cy, innerR, startAngle);
  const endIn = polar(cx, cy, innerR, endAngle);
  const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
  return [
    `M ${startOut.x} ${startOut.y}`,
    `A ${outerR} ${outerR} 0 ${largeArc} 1 ${endOut.x} ${endOut.y}`,
    `L ${endIn.x} ${endIn.y}`,
    `A ${innerR} ${innerR} 0 ${largeArc} 0 ${startIn.x} ${startIn.y}`,
    'Z',
  ].join(' ');
}

function DropHistoryPieChart({ data, size, innerRadius, outerRadius, onSelect }) {
  const [hoveredIdx, setHoveredIdx] = useState(-1);
  const total = useMemo(() => data.reduce((a, s) => a + s.value, 0), [data]);

  const arcs = useMemo(() => {
    let start = -Math.PI / 2;
    return data.map(d => {
      const sweep = total ? (d.value / total) * Math.PI * 2 : 0;
      const arc = { ...d, start, end: start + sweep, mid: start + sweep / 2, sweep };
      start += sweep;
      return arc;
    });
  }, [data, total]);

  const activeSlice = hoveredIdx >= 0 ? arcs[hoveredIdx] : null;
  const centerPct = useMemo(() => {
    if (!activeSlice || !total) return null;
    return ((activeSlice.value / total) * 100).toFixed(1);
  }, [activeSlice, total]);

  const c = size / 2;
  const padRad = data.length > 8 ? 0.02 : 0.04;

  return (
    <div style={{
      position: 'relative', width: size, height: size, flexShrink: 0,
      outline: 'none', userSelect: 'none', WebkitUserSelect: 'none', WebkitTapHighlightColor: 'transparent',
    }}>
      <svg
        width={size} height={size} viewBox={`0 0 ${size} ${size}`}
        style={{ display: 'block', overflow: 'visible', outline: 'none' }}
      >
        <g transform={`translate(${c}, ${c})`}>
          {arcs.map((arc, idx) => {
            const isHover = hoveredIdx === idx;
            const gap = padRad / 2;
            const d = donutSlicePath(0, 0, innerRadius, outerRadius, arc.start + gap, arc.end - gap);
            const tx = isHover ? Math.cos(arc.mid) * 8 : 0;
            const ty = isHover ? Math.sin(arc.mid) * 8 : 0;
            return (
              <path
                key={idx}
                d={d}
                fill={arc.color}
                stroke="rgba(5,8,16,0.85)"
                strokeWidth={2}
                style={{
                  transform: `translate(${tx}px, ${ty}px)`,
                  transformOrigin: 'center',
                  transition: 'transform 0.18s ease, filter 0.18s ease',
                  cursor: 'pointer',
                  filter: isHover ? `drop-shadow(0 0 10px ${arc.color})` : 'none',
                }}
                onMouseEnter={() => setHoveredIdx(idx)}
                onMouseLeave={() => setHoveredIdx(-1)}
                onClick={() => onSelect(arc)}
              />
            );
          })}
        </g>
      </svg>
      <div style={{
        position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)',
        textAlign: 'center', pointerEvents: 'none', width: innerRadius * 1.5,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4,
      }}>
        {activeSlice ? (
          <>
            <div style={{
              fontFamily: SYS, fontSize: Math.max(10, Math.round(size * 0.026)),
              color: 'rgba(255,255,255,0.85)', fontWeight: 600, lineHeight: 1.3,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%',
            }}>{activeSlice.label}</div>
            <div style={{
              fontFamily: SYS, fontSize: Math.max(14, Math.round(size * 0.045)),
              color: activeSlice.color || CYAN, fontWeight: 700, lineHeight: 1.1,
            }}>{activeSlice.value}</div>
            <div style={{
              fontFamily: SYS, fontSize: Math.max(9, Math.round(size * 0.022)),
              color: 'rgba(255,255,255,0.45)', lineHeight: 1.1,
            }}>{centerPct}%</div>
          </>
        ) : (
          <>
            <div style={{
              fontFamily: SYS, fontSize: Math.max(16, Math.round(size * 0.055)),
              color: CYAN, fontWeight: 700, lineHeight: 1.1,
            }}>{total}</div>
            <div style={{
              fontFamily: SYS, fontSize: Math.max(9, Math.round(size * 0.024)),
              color: 'rgba(255,255,255,0.35)', lineHeight: 1.2,
            }}>drops</div>
          </>
        )}
      </div>
    </div>
  );
}

// ── STATIONS SUB-TAB ───────────────────────────────────────────────────────────
const TIME_INTERVALS = [
  { id: '1h',  label: '1 Hour' },
  { id: '6h',  label: '6 Hours' },
  { id: '24h', label: '24 Hours' },
  { id: '7d',  label: '7 Days' },
  { id: '30d', label: '30 Days' },
];

const ALL_SDR = '__all__';
const ALL_RTYS = '__all_rtys__';
const PIE_COLORS = [CYAN, AMBER, GREEN, PURPLE, PINK, TEAL, ORANGE, RED, '#818cf8', '#34d399', '#f87171', '#fbbf24'];

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

function StationsTab({ sdrPeaksRef }) {
  const [selectedSdr, setSelectedSdr] = useState(ALL_SDR);
  const [selectedRtys, setSelectedRtys] = useState(ALL_RTYS);
  const [selectedStation, setSelectedStation] = useState(null); // null = all stations
  const [timeInterval, setTimeInterval] = useState('24h');
  const [searchQ, setSearchQ] = useState('');
  const [stationSearch, setStationSearch] = useState('');
  const [stationSort, setStationSort] = useState('count'); // 'count' | 'name'
  const [view, setView] = useState('signals'); // 'signals' | 'insights'
  const [unit, setUnit] = useState('dbuv'); // 'dbuv' | 'dbm'
  const [hoveredRange, setHoveredRange] = useState(null);
  const [selectedSignalId, setSelectedSignalId] = useState(null);
  const [realTelemetry, setRealTelemetry] = useState(null);
  const [telemetryByFrequency, setTelemetryByFrequency] = useState({});
  const telemetryByFrequencyRef = useRef({});
  const lastTimeIntervalRef = useRef(timeInterval);

  useEffect(() => {
    telemetryByFrequencyRef.current = telemetryByFrequency;
  }, [telemetryByFrequency]);

  // fm_dab_sweep.py now logs raw dBFS (DB_OFFSET_DB=0). To keep the UI
  // showing the same numbers as before, dBm is displayed as the stored
  // raw dBFS value and dBµV adds the legacy 107 dB offset.
  const displayVal = useCallback((v) => unit === 'dbm' ? v : v + 107, [unit]);
  const unitLabel = unit === 'dbm' ? 'dBm' : 'dBµV';

  const isAllSdr = selectedSdr === ALL_SDR;
  const sdrInfo = isAllSdr ? null : SDR_DATA[selectedSdr];
  const rtysNames = isAllSdr ? [] : Object.keys(sdrInfo.rtys);

  // Reset RTYS + station when SDR changes
  useEffect(() => {
    setSelectedRtys(ALL_RTYS);
    setSelectedStation(null);
  }, [selectedSdr]);

  // Reset station when RTYS changes
  useEffect(() => {
    setSelectedStation(null);
  }, [selectedRtys]);

  // Compute raw freqs depending on SDR/RTYS selection (before station filter)
  const scopeFreqs = useMemo(() => {
    if (isAllSdr) {
      return SDR_IDS.flatMap(id =>
        Object.entries(SDR_DATA[id].rtys).flatMap(([rtysName, freqs]) =>
          freqs.map(f => ({ ...f, rtys: rtysName, sdr: id }))
        )
      );
    }
    if (selectedRtys === ALL_RTYS) {
      return Object.entries(sdrInfo.rtys).flatMap(([rtysName, freqs]) =>
        freqs.map(f => ({ ...f, rtys: rtysName, sdr: selectedSdr }))
      );
    }
    return (sdrInfo.rtys[selectedRtys] || []).map(f => ({ ...f, rtys: selectedRtys, sdr: selectedSdr }));
  }, [isAllSdr, selectedSdr, selectedRtys]);

  // All distinct station names within current scope, with count + RTYS labels
  const distinctStations = useMemo(() => {
    const map = {};
    scopeFreqs.forEach(({ name, rtys }) => {
      if (!map[name]) map[name] = { count: 0, rtys: new Set() };
      map[name].count++;
      if (rtys) map[name].rtys.add(rtys);
    });
    const entries = Object.entries(map).map(([name, { count, rtys }]) => [name, count, Array.from(rtys)]);
    if (stationSort === 'count') {
      return entries.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    }
    return entries.sort((a, b) => a[0].localeCompare(b[0]));
  }, [scopeFreqs, stationSort]);

  // rawFreqs = scopeFreqs filtered by selectedStation
  const rawFreqs = useMemo(() => {
    if (!selectedStation) return scopeFreqs;
    return scopeFreqs.filter(f => f.name === selectedStation);
  }, [scopeFreqs, selectedStation]);

  // Filtered distinct station list by stationSearch
  const visibleStations = useMemo(() => {
    if (!stationSearch.trim()) return distinctStations;
    const { textTokens } = tokenizeSearch(stationSearch);
    if (!textTokens.length) return distinctStations;
    return distinctStations.filter(([name, , rtys]) => {
      const haystacks = [name, ...rtys].map(s => normalizeAzeri(s).toLowerCase());
      return textTokens.every(token => haystacks.some(h => fuzzyMatch(h, token)));
    });
  }, [distinctStations, stationSearch]);

  // Real-time telemetry from WebSocket peaks (no HTTP polling)
  useEffect(() => {
    if (!rawFreqs.length) return;
    let cancelled = false;

    // Clear cached telemetry when the selected time window changes so the
    // new interval is actually fetched instead of reusing stale 24h values.
    if (lastTimeIntervalRef.current !== timeInterval) {
      lastTimeIntervalRef.current = timeInterval;
      telemetryByFrequencyRef.current = {};
      setTelemetryByFrequency({});
    }

    // Attempt to read from WebSocket ref
    const syncFromRef = () => {
      try {
        const peaksData = sdrPeaksRef?.current;
        if (!peaksData || Object.keys(peaksData).length === 0) return false;
        const next = {};
        rawFreqs.forEach(station => {
          const sourceId = SDR_API_IDS[station.sdr || selectedSdr];
          if (!sourceId) return;
          const sdrEntry = peaksData[sourceId];
          if (!sdrEntry || !sdrEntry.peaks) return;
          const match = findFrequencyMatch(sdrEntry.peaks, station.freq);
          if (match) {
            next[telemetryKey(sourceId, station.freq)] = {
              freq: station.freq,
              power: match.dbfs,
              min: match.dbfs,
              peak: match.dbfs,
              snr: null,
              noise_floor: null,
              samples: 1,
              timestamp: match.ts ? new Date(match.ts * 1000).toISOString() : null,
            };
          }
        });
        if (Object.keys(next).length === 0) return false;
        if (!cancelled) {
          setTelemetryByFrequency(prev => {
            const merged = {};
            Object.keys(next).forEach(key => {
              const old = prev[key];
              const cur = next[key];
              if (old) {
                merged[key] = { ...cur, min: Math.min(old.min ?? cur.power, cur.power), peak: Math.max(old.peak ?? cur.power, cur.power), samples: (old.samples || 0) + 1 };
              } else {
                merged[key] = cur;
              }
            });
            Object.keys(prev).forEach(key => { if (!merged[key]) merged[key] = prev[key]; });
            return merged;
          });
        }
        return true;
      } catch { return false; }
    };

    // Fallback HTTP fetch
    const fetchFromApi = async () => {
      try {
        const missingStations = rawFreqs.filter(station => {
          const sourceId = SDR_API_IDS[station.sdr || selectedSdr];
          return sourceId && !telemetryByFrequencyRef.current[telemetryKey(sourceId, station.freq)];
        });
        if (!missingStations.length) return;
        const grouped = missingStations.reduce((groups, station) => {
          const sourceId = SDR_API_IDS[station.sdr || selectedSdr];
          if (!groups[sourceId]) groups[sourceId] = [];
          groups[sourceId].push(station);
          return groups;
        }, {});
        const responses = await Promise.all(Object.entries(grouped).map(async ([sdrId, stations]) => {
          const params = new URLSearchParams({ freqs: stations.map(station => station.freq.toFixed(3)).join(','), window: timeInterval });
          const cacheKey = `/api/sdr/${encodeURIComponent(sdrId)}/telemetry?${params.toString()}`;
          const cached = _getCached(cacheKey, 30000);
          if (cached) return { sdrId, stations, data: cached };
          const response = await fetch(`/api/sdr/${encodeURIComponent(sdrId)}/telemetry?${params}`, { credentials: 'include' });
          if (!response.ok) return { sdrId, stations, data: { signals: [] } };
          const data = await response.json();
          _setCached(cacheKey, data);
          return { sdrId, stations, data };
        }));
        if (cancelled) return;
        const next = {};
        responses.forEach(({ sdrId, stations, data }) => stations.forEach(station => {
          const signal = findFrequencyMatch(data.signals, station.freq);
          if (signal) next[telemetryKey(sdrId, station.freq)] = { ...signal, freq: station.freq };
        }));
        if (Object.keys(next).length) {
          setTelemetryByFrequency(previous => ({ ...previous, ...next }));
        }
      } catch {
        if (!cancelled) setTelemetryByFrequency({});
      }
    };

    // Try ref first; if empty, do one HTTP load, then keep ticking from ref
    syncFromRef();
    fetchFromApi();

    const timer = window.setInterval(() => {
      syncFromRef();
      fetchFromApi();
    }, 15000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [rawFreqs, selectedSdr, sdrPeaksRef, timeInterval]);

  const metrics = useMemo(() => rawFreqs.map(station => {
    const sourceId = SDR_API_IDS[station.sdr || selectedSdr];
    const reading = telemetryByFrequency[telemetryKey(sourceId, station.freq)];
    if (!reading) return { ...station, min: null, peak: null, avg: null, power: null, snr: null, noiseFloor: null, timestamp: null, samples: null, hasData: false };
    return { ...station, min: reading.min, peak: reading.peak, avg: reading.power, power: reading.power, snr: reading.snr, noiseFloor: reading.noise_floor, timestamp: reading.timestamp, samples: reading.samples, hasData: true };
  }), [rawFreqs, selectedSdr, telemetryByFrequency]);

  const filtered = useMemo(() => {
    if (!searchQ.trim()) return metrics;
    const { textTokens, freqTokens } = tokenizeSearch(searchQ);
    if (!textTokens.length && !freqTokens.length) return metrics;
    return metrics.filter(m => {
      const sdrKey = m.sdr || selectedSdr;
      const sdrLabel = SDR_DATA[sdrKey]?.label || '';
      const haystacks = [m.name, m.rtys || '', sdrLabel].map(s => normalizeAzeri(s).toLowerCase());
      const textMatch = textTokens.length === 0 || textTokens.every(token => haystacks.some(h => fuzzyMatch(h, token)));
      if (!textMatch) return false;
      if (freqTokens.length === 0) return true;
      return freqTokens.some(({ value }) => Math.abs(m.freq - value) < 0.05);
    });
  }, [metrics, searchQ, selectedSdr]);

  const signalId = useCallback((m) => `${m.name}|${m.freq}|${m.rtys || ''}|${m.sdr || ''}`, []);
  const selectedSignal = useMemo(() => metrics.find(m => signalId(m) === selectedSignalId) || null, [metrics, selectedSignalId, signalId]);
  const liveSignal = useMemo(() => {
    if (!selectedSignal || !realTelemetry) return null;
    return realTelemetry.signals?.find(signal => Math.abs(signal.freq - selectedSignal.freq) < 0.15) || null;
  }, [selectedSignal, realTelemetry]);

  const signalChart = useMemo(() => {
    const history = realTelemetry?.history || [];
    if (history.length < 2) return null;
    const low = Math.min(...history.map(point => point.noise_floor ?? point.power)) - 2;
    const high = Math.max(...history.map(point => point.power)) + 2;
    const toPoints = (key) => history.map((point, index) => `${(index / (history.length - 1)) * 100},${82 - (((point[key] ?? point.power) - low) / (high - low || 1)) * 66}`).join(' ');
    return { powerPoints: toPoints('power'), noisePoints: toPoints('noise_floor') };
  }, [realTelemetry]);

  // Build real-time telemetry for selected signal from WebSocket peaks
  const signalHistoryRef = useRef([]);
  const prevSignalIdRef = useRef(null);
  useEffect(() => {
    if (!selectedSignalId) {
      setRealTelemetry(null);
      signalHistoryRef.current = [];
      prevSignalIdRef.current = null;
      return undefined;
    }
    // Reset history only when signal actually changes
    if (prevSignalIdRef.current !== selectedSignalId) {
      signalHistoryRef.current = [];
      prevSignalIdRef.current = selectedSignalId;
    }
    const sig = metrics.find(m => signalId(m) === selectedSignalId);
    if (!sig) return undefined;
    const sourceSdrId = SDR_API_IDS[sig.sdr || selectedSdr];
    if (!sourceSdrId) return undefined;
    const tick = () => {
      const peaksData = sdrPeaksRef?.current;
      if (!peaksData) return;
      const sdrEntry = peaksData[sourceSdrId];
      if (!sdrEntry || !sdrEntry.peaks) return;
      const match = sdrEntry.peaks.find(p => Math.abs(p.freq - sig.freq) < 0.15);
      if (!match) return;
      const power = match.dbfs;
      const noiseFloor = power - 15;
      const point = { timestamp: new Date().toISOString(), power, noise_floor: noiseFloor, snr: 15 };
      signalHistoryRef.current = [...signalHistoryRef.current.slice(-35), point];
      setRealTelemetry({
        signals: [{ freq: match.freq, power, min: power, peak: power, snr: point.snr, noise_floor: noiseFloor, samples: signalHistoryRef.current.length, timestamp: point.timestamp }],
        history: [...signalHistoryRef.current],
      });
    };
    tick();
    const timer = window.setInterval(tick, 2000);
    return () => window.clearInterval(timer);
  }, [selectedSignalId, selectedSdr, sdrPeaksRef, metrics, signalId]);

  useEffect(() => {
    if (selectedSignalId && !selectedSignal) setSelectedSignalId(null);
  }, [selectedSignalId, selectedSignal]);

  // Stats
  const metricsWithData = metrics.filter(m => m.hasData);
  const overallPeak = metricsWithData.length ? Math.max(...metricsWithData.map(m => m.peak)) : null;
  const overallMin  = metricsWithData.length ? Math.min(...metricsWithData.map(m => m.min))  : null;
  const activeCount = metrics.length;

  // ── Insight computations (over the full scope, not filtered) ──
  // Group by station name → list of freq entries with source info
  const byName = useMemo(() => {
    const map = {};
    rawFreqs.forEach(({ name, freq, rtys, sdr }) => {
      if (!map[name]) map[name] = [];
      const label = SDR_DATA[sdr]?.label || sdr;
      const key = `${freq.toFixed(3)}|${sdr}|${rtys}`;
      if (!map[name].some(e => e.key === key)) {
        map[name].push({ key, freq, rtys, sdr, label });
      }
    });
    return Object.entries(map)
      .filter(([, entries]) => entries.length > 1)
      .map(([name, entries]) => [name, entries.sort((a, b) => a.freq - b.freq)])
      .sort((a, b) => b[1].length - a[1].length);
  }, [rawFreqs]);

  // Group by frequency → list of station entries with source info
  const byFreq = useMemo(() => {
    const map = {};
    rawFreqs.forEach(({ name, freq, rtys, sdr }) => {
      const key = freq.toFixed(1);
      if (!map[key]) map[key] = [];
      const label = SDR_DATA[sdr]?.label || sdr;
      const id = `${name}|${sdr}|${rtys}`;
      if (!map[key].some(e => e.id === id)) {
        map[key].push({ id, name, rtys, sdr, label });
      }
    });
    return Object.entries(map)
      .filter(([, entries]) => entries.length > 1)
      .map(([freq, entries]) => [freq, entries])
      .sort((a, b) => b[1].length - a[1].length);
  }, [rawFreqs]);

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 164px)', gap: 0, overflow: 'hidden' }}>

      {/* ── LEFT PANEL — Stations list ─────────────────────────── */}
      <div style={{
        width: 220, flexShrink: 0, display: 'flex', flexDirection: 'column',
        borderRight: '1px solid rgba(0,229,255,0.07)',
        background: 'rgba(5,8,16,0.6)',
        overflow: 'hidden',
      }}>

        {/* Compact SDR + RTYS scope filters */}
        <div style={{ padding: '10px 10px 6px', borderBottom: '1px solid rgba(255,255,255,0.05)', flexShrink: 0 }}>
          <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)', letterSpacing: '0.12em', textTransform: 'uppercase', fontFamily: SYS, marginBottom: 5 }}>Scope</div>
          {/* SDR chips */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginBottom: 5 }}>
            <button onClick={() => setSelectedSdr(ALL_SDR)} style={{
              fontSize: 9, fontFamily: SYS, padding: '2px 7px', borderRadius: 4, cursor: 'pointer',
              background: selectedSdr === ALL_SDR ? 'rgba(0,229,255,0.18)' : 'rgba(255,255,255,0.05)',
              border: `1px solid ${selectedSdr === ALL_SDR ? 'rgba(0,229,255,0.45)' : 'rgba(255,255,255,0.08)'}`,
              color: selectedSdr === ALL_SDR ? CYAN : 'rgba(255,255,255,0.4)',
            }}>All</button>
            {SDR_IDS.map(id => (
              <button key={id} onClick={() => setSelectedSdr(id)} style={{
                fontSize: 9, fontFamily: SYS, padding: '2px 7px', borderRadius: 4, cursor: 'pointer',
                background: selectedSdr === id ? `${SDR_DATA[id].color}28` : 'rgba(255,255,255,0.05)',
                border: `1px solid ${selectedSdr === id ? SDR_DATA[id].color : 'rgba(255,255,255,0.08)'}`,
                color: selectedSdr === id ? SDR_DATA[id].color : 'rgba(255,255,255,0.35)',
              }}>{SDR_DATA[id].label.replace(' SDR', '')}</button>
            ))}
          </div>
          {/* RTYS chips — only when a specific SDR chosen */}
          {!isAllSdr && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
              <button onClick={() => setSelectedRtys(ALL_RTYS)} style={{
                fontSize: 8, fontFamily: SYS, padding: '2px 6px', borderRadius: 4, cursor: 'pointer',
                background: selectedRtys === ALL_RTYS ? 'rgba(0,229,255,0.14)' : 'rgba(255,255,255,0.04)',
                border: `1px solid ${selectedRtys === ALL_RTYS ? 'rgba(0,229,255,0.4)' : 'rgba(255,255,255,0.07)'}`,
                color: selectedRtys === ALL_RTYS ? CYAN : 'rgba(255,255,255,0.3)',
              }}>All RTYS</button>
              {rtysNames.map(name => (
                <button key={name} onClick={() => setSelectedRtys(name)} style={{
                  fontSize: 8, fontFamily: SYS, padding: '2px 6px', borderRadius: 4, cursor: 'pointer',
                  background: selectedRtys === name ? 'rgba(0,229,255,0.12)' : 'rgba(255,255,255,0.04)',
                  border: `1px solid ${selectedRtys === name ? 'rgba(0,229,255,0.35)' : 'rgba(255,255,255,0.07)'}`,
                  color: selectedRtys === name ? '#e0f7ff' : 'rgba(255,255,255,0.28)',
                  maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{name}</button>
              ))}
            </div>
          )}
        </div>

        {/* Station search */}
        <div style={{ padding: '7px 10px', flexShrink: 0, borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
          <input
            value={stationSearch}
            onChange={e => setStationSearch(e.target.value)}
            placeholder="Search stations…"
            style={{
              width: '100%', padding: '5px 9px', borderRadius: 6,
              background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(0,229,255,0.1)',
              color: '#fff', fontSize: 10, fontFamily: SYS, outline: 'none', boxSizing: 'border-box',
            }}
          />
        </div>

        {/* Station list header + count + sort */}
        <div style={{ padding: '6px 10px 4px', flexShrink: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)', textTransform: 'uppercase', letterSpacing: '0.12em', fontFamily: SYS }}>Stations</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.18)', fontFamily: MONO }}>{visibleStations.length}</span>
            <div style={{ display: 'flex', gap: 2, background: 'rgba(255,255,255,0.04)', borderRadius: 6, padding: 2 }}>
              {[
                ['count', 'Count'],
                ['name', 'Name'],
              ].map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setStationSort(id)}
                  style={{
                    fontSize: 8, fontFamily: SYS, padding: '2px 6px', borderRadius: 4, cursor: 'pointer',
                    border: 'none',
                    background: stationSort === id ? 'rgba(0,229,255,0.2)' : 'transparent',
                    color: stationSort === id ? CYAN : 'rgba(255,255,255,0.35)',
                  }}
                >{label}</button>
              ))}
            </div>
          </div>
        </div>

        {/* All stations row */}
        <div
          onClick={() => setSelectedStation(null)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '6px 10px', marginBottom: 0, cursor: 'pointer', flexShrink: 0,
            background: selectedStation === null ? 'rgba(0,229,255,0.12)' : 'transparent',
            borderBottom: '1px solid rgba(255,255,255,0.04)',
          }}
        >
          <span style={{ fontSize: 11, fontWeight: 700, color: selectedStation === null ? CYAN : 'rgba(255,255,255,0.5)', fontFamily: SYS }}>All Stations</span>
          <span style={{ fontSize: 9, fontFamily: MONO, color: 'rgba(255,255,255,0.25)' }}>{distinctStations.length}</span>
        </div>

        {/* Scrollable station list */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {visibleStations.map(([name, cnt, rtys]) => {
            const isActive = selectedStation === name;
            return (
              <div
                key={name}
                onClick={() => setSelectedStation(isActive ? null : name)}
                className="ov-menu-item"
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '6px 10px', cursor: 'pointer',
                  background: isActive ? 'rgba(0,229,255,0.1)' : 'transparent',
                  borderLeft: isActive ? `2px solid ${CYAN}` : '2px solid transparent',
                }}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 11, color: isActive ? '#fff' : 'rgba(255,255,255,0.6)', fontFamily: SYS, fontWeight: isActive ? 600 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {name}
                  </div>
                  <div style={{ fontSize: 8, color: isActive ? 'rgba(0,229,255,0.6)' : 'rgba(255,255,255,0.25)', fontFamily: SYS, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 1 }}>
                    {rtys.slice(0, 2).join(' · ')}{rtys.length > 2 && ` · +${rtys.length - 2}`}
                  </div>
                </div>
                <span style={{ fontSize: 9, fontFamily: MONO, color: isActive ? CYAN : 'rgba(255,255,255,0.2)', flexShrink: 0, marginLeft: 6 }}>{cnt}</span>
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div style={{ padding: '5px 10px', borderTop: '1px solid rgba(255,255,255,0.04)', fontSize: 8, color: 'rgba(255,255,255,0.15)', fontFamily: SYS, flexShrink: 0 }}>
          {scopeFreqs.length} freq entries
        </div>
      </div>

      {/* ── MAIN CONTENT ─────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Header row: title + time interval */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 20px 10px',
          borderBottom: '1px solid rgba(0,229,255,0.07)',
          flexShrink: 0,
        }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {!isAllSdr && <span style={{ width: 10, height: 10, borderRadius: '50%', background: sdrInfo.color, display: 'inline-block', boxShadow: `0 0 8px ${sdrInfo.color}` }} />}
              <span style={{ fontSize: 14, fontWeight: 700, color: '#fff', fontFamily: SYS }}>
                {isAllSdr ? 'All Regions' : (selectedRtys === ALL_RTYS ? 'All RTYS' : selectedRtys)}
              </span>
              <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', fontFamily: SYS }}>
                {isAllSdr ? '— All SDRs' : `— ${sdrInfo.label}`}
              </span>
            </div>
            <div style={{ fontSize: 10, color: 'rgba(0,229,255,0.4)', fontFamily: MONO, marginTop: 2 }}>
              {activeCount} frequencies · FM band 87.5–108 MHz
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <UnitToggle unit={unit} onChange={setUnit} />

            {/* View toggle */}
            <div style={{ display: 'flex', gap: 2, background: 'rgba(255,255,255,0.04)', borderRadius: 8, padding: 3 }}>
              {[['signals','Signals'],['insights','Insights']].map(([id, label]) => (
                <button key={id} onClick={() => setView(id)} style={{
                  padding: '4px 14px', borderRadius: 6, fontSize: 10, fontFamily: SYS, fontWeight: 600,
                  cursor: 'pointer', border: 'none',
                  background: view === id ? 'rgba(0,229,255,0.2)' : 'transparent',
                  color: view === id ? CYAN : 'rgba(255,255,255,0.35)',
                  transition: 'all 0.15s',
                }}>{label}</button>
              ))}
            </div>
          </div>

        {/* Time interval pills */}
          <div style={{ display: 'flex', gap: 4 }}>
            {TIME_INTERVALS.map(ti => (
              <button
                key={ti.id}
                className={`ov-ti-btn${timeInterval === ti.id ? ' active' : ''}`}
                onClick={() => setTimeInterval(ti.id)}
                style={{
                  padding: '5px 12px', borderRadius: 20, fontSize: 10, fontFamily: SYS, fontWeight: 600,
                  cursor: 'pointer',
                  background: timeInterval === ti.id ? 'rgba(0,229,255,0.18)' : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${timeInterval === ti.id ? 'rgba(0,229,255,0.5)' : 'rgba(255,255,255,0.1)'}`,
                  color: timeInterval === ti.id ? CYAN : 'rgba(255,255,255,0.45)',
                  transition: 'all 0.15s ease',
                }}
              >
                {ti.label}
              </button>
            ))}
          </div>
        </div>

        {/* KPI strip */}
        <div style={{
          display: 'flex', gap: 10, padding: '10px 20px',
          borderBottom: '1px solid rgba(0,229,255,0.06)',
          flexShrink: 0,
        }}>
          {[
            { label: `Peak Signal (${unitLabel})`, value: overallPeak != null ? `${displayVal(overallPeak).toFixed(1)} ${unitLabel}` : '–', color: GREEN, toggle: true },
            { label: `Minimum Signal (${unitLabel})`, value: overallMin != null ? `${displayVal(overallMin).toFixed(1)} ${unitLabel}` : '–', color: RED, toggle: true },
            { label: 'Active Frequencies', value: activeCount, color: GREEN },
            { label: 'Band Range', value: rawFreqs.length ? `${Math.min(...rawFreqs.map(f=>f.freq)).toFixed(1)}–${Math.max(...rawFreqs.map(f=>f.freq)).toFixed(1)} MHz` : '–', color: CYAN },
          ].map((kpi, i) => (
            <div
              key={i}
              onClick={kpi.toggle ? () => setUnit(prev => prev === 'dbuv' ? 'dbm' : 'dbuv') : undefined}
              style={{
                ...GLASS_DARK,
                padding: '8px 14px', flex: 1,
                display: 'flex', flexDirection: 'column', gap: 2,
                cursor: kpi.toggle ? 'pointer' : 'default',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={kpi.toggle ? e => { e.currentTarget.style.background = 'rgba(0,229,255,0.12)'; e.currentTarget.style.borderColor = 'rgba(0,229,255,0.25)'; } : undefined}
              onMouseLeave={kpi.toggle ? e => { e.currentTarget.style.background = GLASS_DARK.background; e.currentTarget.style.borderColor = GLASS_DARK.border.split(' ')[2]; } : undefined}
            >
              <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.35)', textTransform: 'uppercase', letterSpacing: '0.12em', fontFamily: SYS }}>{kpi.label}</span>
              <span style={{ fontSize: 16, fontWeight: 700, color: kpi.color, fontFamily: MONO }}>{kpi.value}</span>
            </div>
          ))}
        </div>

        <AnimatePresence mode="wait">
          {view === 'signals' && (
            <motion.div key="signals" initial={{ opacity:0, x:10 }} animate={{ opacity:1, x:0 }} exit={{ opacity:0, x:-10 }} transition={{ duration:0.2 }}
              style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden' }}>
              {/* Search */}
              <div style={{ padding: '8px 20px', flexShrink: 0, display: selectedSignal ? 'none' : undefined }}>
                <input
                  value={searchQ}
                  onChange={e => setSearchQ(e.target.value)}
                  placeholder="Search station or frequency…"
                  style={{
                    width: '100%', padding: '7px 12px', borderRadius: 8,
                    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(0,229,255,0.12)',
                    color: '#fff', fontSize: 11, fontFamily: SYS, outline: 'none',
                    boxSizing: 'border-box',
                  }}
                />
              </div>
              <AnimatePresence initial={false}>
                {selectedSignal && (
                  <motion.div
                    key={selectedSignalId}
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.18 }}
                    style={{ flex: 1, margin: '8px 20px 20px', padding: '20px', borderRadius: 12, background: 'linear-gradient(135deg, rgba(0,229,255,0.12), rgba(105,255,71,0.06))', border: '1px solid rgba(0,229,255,0.3)', boxShadow: '0 0 24px rgba(0,229,255,0.08)', overflowY: 'auto' }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 10 }}>
                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                          <span style={{ width: 7, height: 7, borderRadius: '50%', background: liveSignal ? GREEN : AMBER, boxShadow: `0 0 8px ${liveSignal ? GREEN : AMBER}`, animation: 'ovPulse 1.5s ease-in-out infinite' }} />
                          <span style={{ color: '#fff', fontSize: 13, fontWeight: 700, fontFamily: SYS }}>{selectedSignal.name}</span>
                          <span style={{ color: CYAN, fontSize: 12, fontWeight: 700, fontFamily: MONO }}>{selectedSignal.freq.toFixed(1)} MHz</span>
                        </div>
                        <div style={{ marginTop: 3, color: 'rgba(255,255,255,0.38)', fontSize: 9, fontFamily: SYS }}>{selectedSignal.rtys || 'Selected signal'} · {liveSignal ? 'LIVE TELEMETRY' : (realTelemetry === null ? 'LOADING…' : 'NO LIVE DATA')}</div>
                      </div>
                      <button onClick={() => setSelectedSignalId(null)} style={{ border: '1px solid rgba(255,255,255,0.14)', borderRadius: 5, background: 'rgba(0,0,0,0.2)', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', fontSize: 10, fontFamily: SYS, padding: '6px 9px' }}>← Back to signals</button>
                    </div>
                    {liveSignal ? (
                      <>
                        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(190px, 1fr)', gap: 10, marginBottom: 14 }}>
                          <div style={{ height: 132, padding: '11px 12px', borderRadius: 8, background: 'rgba(3,8,16,0.45)', border: '1px solid rgba(0,229,255,0.13)' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5, color: 'rgba(255,255,255,0.42)', fontFamily: MONO, fontSize: 9, letterSpacing: '0.08em' }}>
                              <span>POWER VS NOISE FLOOR</span><span>LAST 36s</span>
                            </div>
                            <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ width: '100%', height: 92, overflow: 'visible' }}>
                              {[20, 40, 60, 80].map(y => <line key={y} x1="0" x2="100" y1={y} y2={y} stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />)}
                              <polyline points={signalChart?.noisePoints} fill="none" stroke={RED} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
                              <polyline points={signalChart?.powerPoints} fill="none" stroke={GREEN} strokeWidth="1.8" vectorEffect="non-scaling-stroke" style={{ filter: 'drop-shadow(0 0 3px rgba(105,255,71,0.8))' }} />
                            </svg>
                            <div style={{ display: 'flex', gap: 12, marginTop: 1, fontFamily: MONO, fontSize: 8 }}><span style={{ color: GREEN }}>— POWER</span><span style={{ color: RED }}>— NOISE FLOOR</span></div>
                          </div>
                          <div style={{ height: 132, padding: '11px 12px', borderRadius: 8, background: 'rgba(3,8,16,0.45)', border: '1px solid rgba(0,229,255,0.13)' }}>
                            <div style={{ color: 'rgba(255,255,255,0.42)', fontFamily: MONO, fontSize: 9, letterSpacing: '0.08em' }}>SIGNAL RANGE</div>
                            <div style={{ marginTop: 18, position: 'relative', height: 11, borderRadius: 8, background: 'linear-gradient(90deg, #ff5252, #ffaf00, #69ff47)' }}>
                              {selectedSignal.min != null && selectedSignal.peak != null && selectedSignal.peak !== selectedSignal.min && (
                                <div style={{ position: 'absolute', top: -4, left: `${Math.max(0, Math.min(100, ((liveSignal.power - selectedSignal.min) / (selectedSignal.peak - selectedSignal.min)) * 100))}%`, width: 18, height: 18, borderRadius: '50%', background: '#fff', border: `3px solid ${CYAN}`, boxShadow: `0 0 10px ${CYAN}`, transform: 'translateX(-50%)' }} />
                              )}
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 9, fontFamily: MONO, fontSize: 9 }}><span style={{ color: RED }}>{selectedSignal.min != null ? displayVal(selectedSignal.min).toFixed(1) : '–'}</span><span style={{ color: GREEN }}>{selectedSignal.peak != null ? displayVal(selectedSignal.peak).toFixed(1) : '–'}</span></div>
                            <div style={{ marginTop: 18, color: 'rgba(255,255,255,0.36)', fontSize: 9, fontFamily: SYS }}>Current power position within the selected interval.</div>
                          </div>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 7 }}>
                          {[
                            { label: 'LIVE POWER', value: `${displayVal(liveSignal.power).toFixed(1)} ${unitLabel}`, color: GREEN },
                            { label: 'NOISE FLOOR', value: liveSignal.noise_floor != null ? `${displayVal(liveSignal.noise_floor).toFixed(1)} ${unitLabel}` : '–', color: RED },
                            { label: 'SNR', value: liveSignal.snr != null ? `${liveSignal.snr.toFixed(1)} dB` : '–', color: CYAN },
                            { label: 'SAMPLES', value: liveSignal.samples ?? '–', color: CYAN },
                            { label: 'LAST UPDATED', value: liveSignal.timestamp ? new Date(liveSignal.timestamp).toLocaleTimeString() : '–', color: 'rgba(255,255,255,0.8)' },
                          ].map(detail => (
                            <div key={detail.label} style={{ minWidth: 0, padding: '7px 8px', borderRadius: 6, background: 'rgba(4,9,17,0.55)', border: '1px solid rgba(255,255,255,0.06)' }}>
                              <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.34)', fontFamily: SYS, letterSpacing: '0.08em', whiteSpace: 'nowrap' }}>{detail.label}</div>
                              <div style={{ marginTop: 3, color: detail.color, fontSize: 12, fontWeight: 700, fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{detail.value}</div>
                            </div>
                          ))}
                        </div>
                      </>
                    ) : selectedSignal.hasData ? (
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 7 }}>
                        {[
                          { label: 'POWER', value: `${displayVal(selectedSignal.power).toFixed(1)} ${unitLabel}`, color: GREEN },
                          { label: 'PEAK', value: `${displayVal(selectedSignal.peak).toFixed(1)} ${unitLabel}`, color: GREEN },
                          { label: 'MIN', value: `${displayVal(selectedSignal.min).toFixed(1)} ${unitLabel}`, color: RED },
                          { label: 'SNR', value: selectedSignal.snr != null ? `${selectedSignal.snr.toFixed(1)} dB` : '–', color: CYAN },
                        ].map(detail => (
                          <div key={detail.label} style={{ minWidth: 0, padding: '7px 8px', borderRadius: 6, background: 'rgba(4,9,17,0.55)', border: '1px solid rgba(255,255,255,0.06)' }}>
                            <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.34)', fontFamily: SYS, letterSpacing: '0.08em', whiteSpace: 'nowrap' }}>{detail.label}</div>
                            <div style={{ marginTop: 3, color: detail.color, fontSize: 12, fontWeight: 700, fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{detail.value}</div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div style={{ textAlign: 'center', padding: '24px 0', color: 'rgba(255,255,255,0.25)', fontSize: 11, fontFamily: SYS }}>
                        {realTelemetry === null ? 'Loading telemetry…' : 'No telemetry data available for this frequency.'}
                      </div>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
              <div style={{ flex: 1, overflowY: 'auto', padding: '4px 20px 20px', display: selectedSignal ? 'none' : undefined }}>
                  {filtered.map((m, i) => {
                    const hasTelemetry = m.hasData;
                    const displayPeak = hasTelemetry ? displayVal(m.peak) : 0;
                    const displayMin  = hasTelemetry ? displayVal(m.min) : 0;
                    const range = unit === 'dbm' ? { min: -120, max: 0 } : { min: 0, max: 120 };
                    const peakPct = hasTelemetry ? Math.max(0, Math.min(100, ((displayPeak - range.min) / (range.max - range.min)) * 100)) : 0;
                    const minPct  = hasTelemetry ? Math.max(0, Math.min(100, ((displayMin - range.min) / (range.max - range.min)) * 100)) : 0;
                    const spanPct = Math.max(0, peakPct - minPct);
                    return (
                      <div
                        key={`${m.name}-${m.freq}-${i}`}
                        onClick={() => setSelectedSignalId(signalId(m))}
                        className="ov-fade-up"
                        style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px', marginBottom: 4, borderRadius: 8, cursor: 'pointer', background: selectedSignalId === signalId(m) ? 'rgba(0,229,255,0.09)' : 'rgba(255,255,255,0.025)', border: `1px solid ${selectedSignalId === signalId(m) ? 'rgba(0,229,255,0.38)' : 'rgba(255,255,255,0.045)'}` }}
                      >
                        <div style={{ width: 200, flexShrink: 0 }}>
                          <div style={{ fontSize: 15, fontWeight: 600, color: hasTelemetry ? '#e0f7ff' : 'rgba(255,255,255,0.55)', fontFamily: SYS, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.name}</div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ fontSize: 13, color: hasTelemetry ? CYAN : 'rgba(0,229,255,0.5)', fontFamily: MONO }}>{m.freq.toFixed(1)} MHz</span>
                            {m.rtys && <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', fontFamily: SYS, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.rtys.replace(' RTYS', '')}</span>}
                          </div>
                          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: SYS, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 1 }}>
                            {SDR_DATA[m.sdr || selectedSdr]?.label || ''}
                          </div>
                        </div>
                        {hasTelemetry ? (
                          <div
                            onMouseEnter={() => setHoveredRange(i)}
                            onMouseLeave={() => setHoveredRange(null)}
                            style={{ flex: 1, position: 'relative', height: 22 }}
                          >
                            <div style={{ position: 'absolute', inset: '-10px 0', zIndex: 1 }} />
                            <div style={{ position: 'absolute', top: 9, left: 0, right: 0, height: 4, background: 'rgba(255,255,255,0.06)', borderRadius: 2 }} />
                            <div style={{ position: 'absolute', top: 9, left: `${minPct}%`, width: `${spanPct}%`, height: 4, background: `linear-gradient(90deg, ${RED}, ${AMBER}, ${GREEN})`, borderRadius: 3, opacity: hoveredRange === i ? 1 : 0.7, boxShadow: hoveredRange === i ? `0 0 7px ${RED}, 0 0 14px ${AMBER}, 0 0 20px ${GREEN}` : 'none' }} />
                            {/* Peak dot + tooltip */}
                            <div style={{ position: 'absolute', top: 1, left: `calc(${peakPct}% - 8px)`, width: 16, height: 16, zIndex: 2 }}>
                              <div style={{ position: 'absolute', top: 6, left: 6, width: 10, height: 10, borderRadius: '50%', background: GREEN, boxShadow: hoveredRange === i ? `0 0 10px ${GREEN}` : `0 0 6px ${GREEN}` }} />
                            </div>
                            {/* Min dot + tooltip */}
                            <div style={{ position: 'absolute', top: 1, left: `calc(${minPct}% - 8px)`, width: 16, height: 16, zIndex: 2 }}>
                              <div style={{ position: 'absolute', top: 6, left: 6, width: 10, height: 10, borderRadius: '50%', background: RED, boxShadow: hoveredRange === i ? `0 0 10px ${RED}` : `0 0 6px ${RED}` }} />
                            </div>
                            {hoveredRange === i && (
                              <>
                                <div style={{ position: 'absolute', bottom: 22, left: `${minPct}%`, transform: 'translateX(-50%)', padding: '2px 5px', borderRadius: 3, background: 'rgba(8,12,22,0.96)', fontSize: 15, fontWeight: 700, fontFamily: MONO, color: RED, whiteSpace: 'nowrap', pointerEvents: 'none', zIndex: 3, animation: 'ovRangeValue 0.16s ease-out both' }}>
                                  {displayVal(m.min).toFixed(1)}
                                </div>
                                <div style={{ position: 'absolute', bottom: 22, left: `${peakPct}%`, transform: 'translateX(-50%)', padding: '2px 5px', borderRadius: 3, background: 'rgba(8,12,22,0.96)', fontSize: 15, fontWeight: 700, fontFamily: MONO, color: GREEN, whiteSpace: 'nowrap', pointerEvents: 'none', zIndex: 3, animation: 'ovRangeValue 0.16s ease-out both' }}>
                                  {displayVal(m.peak).toFixed(1)}
                                </div>
                              </>
                            )}
                          </div>
                        ) : (
                          <div style={{ flex: 1, display: 'flex', alignItems: 'center', height: 22 }}>
                            <div style={{ width: '100%', height: 4, background: 'rgba(255,255,255,0.04)', borderRadius: 2, position: 'relative' }}>
                              <div style={{ position: 'absolute', top: -5, left: '50%', transform: 'translateX(-50%)', fontSize: 11, color: 'rgba(255,255,255,0.2)', fontFamily: SYS, whiteSpace: 'nowrap' }}>awaiting data</div>
                            </div>
                          </div>
                        )}
                        <div style={{ width: 160, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, justifyContent: 'flex-end', whiteSpace: 'nowrap' }}>
                          {hasTelemetry ? (
                            <>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                                <ArrowUp size={15} color={GREEN} strokeWidth={2.5} />
                                <span style={{ fontSize: 15, fontFamily: MONO, color: GREEN }}>{displayVal(m.peak).toFixed(1)}</span>
                              </div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                                <ArrowDown size={15} color={RED} strokeWidth={2.5} />
                                <span style={{ fontSize: 15, fontFamily: MONO, color: RED }}>{displayVal(m.min).toFixed(1)}</span>
                              </div>
                              <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: SYS }}>{unitLabel}</span>
                            </>
                          ) : (
                            <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.18)', fontFamily: SYS }}>No telemetry</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                {filtered.length === 0 && (
                  <div style={{ textAlign: 'center', color: 'rgba(255,255,255,0.2)', fontFamily: SYS, fontSize: 12, padding: '40px 0' }}>
                    No stations match your search.
                  </div>
                )}
              </div>
            </motion.div>
          )}

          {view === 'insights' && (
            <motion.div key="insights" initial={{ opacity:0, x:10 }} animate={{ opacity:1, x:0 }} exit={{ opacity:0, x:-10 }} transition={{ duration:0.2 }}
              style={{ flex:1, display:'flex', gap:0, overflow:'hidden' }}>

              {/* ── Panel A: same name, multiple freqs ── */}
              <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', borderRight:'1px solid rgba(255,255,255,0.06)' }}>
                <div style={{ padding:'12px 16px 8px', flexShrink:0, borderBottom:'1px solid rgba(255,255,255,0.05)' }}>
                  <div style={{ fontSize:17, fontWeight:700, color:'#fff', fontFamily:SYS }}>Same Name · Multiple Freqs</div>
                  <div style={{ fontSize:14, color:'rgba(255,255,255,0.28)', fontFamily:SYS, marginTop:2 }}>
                    {byName.length} stations broadcast on {byName.reduce((a,[,e])=>a+e.length,0)} frequencies
                  </div>
                </div>
                <div style={{ flex:1, overflowY:'auto', padding:'8px 16px 16px' }}>
                  {byName.length === 0 && (
                    <div style={{ color:'rgba(255,255,255,0.2)', fontSize:17, fontFamily:SYS, padding:'24px 0', textAlign:'center' }}>No duplicates in this scope</div>
                  )}
                  {byName.map(([name, entries], i) => {
                    const maxF = Math.max(...entries.map(e => e.freq));
                    return (
                      <motion.div key={name} initial={{ opacity:0, y:6 }} animate={{ opacity:1, y:0 }} transition={{ duration:0.18, delay: Math.min(i*0.03,0.3) }}
                        style={{ marginBottom:20 }}>
                        <div style={{ display:'flex', justifyContent:'space-between', marginBottom:8 }}>
                          <span style={{ fontSize:17, fontWeight:600, color:'rgba(255,255,255,0.85)', fontFamily:SYS }}>{name}</span>
                          <span style={{ fontSize:14, color:CYAN, fontFamily:MONO }}>{entries.length} freqs</span>
                        </div>
                        {entries.map((e, j) => {
                          const pct = (e.freq / 108) * 100;
                          return (
                            <div key={e.key} style={{ display:'flex', alignItems:'center', gap:8, marginBottom:5 }}>
                              <div style={{ width:90, fontSize:14, color:'rgba(255,255,255,0.35)', fontFamily:MONO, textAlign:'right', flexShrink:0 }}>{e.freq.toFixed(1)}</div>
                              <div style={{ flex:1, height:9, background:'rgba(255,255,255,0.06)', borderRadius:3, overflow:'hidden' }}>
                                <motion.div
                                  initial={{ width:0 }} animate={{ width:`${pct}%` }}
                                  transition={{ duration:0.6, delay: Math.min(i*0.03,0.3) + j*0.04, ease:[0.16,1,0.3,1] }}
                                  style={{ height:'100%', background: PIE_COLORS[i % PIE_COLORS.length], borderRadius:3, opacity:0.8 }}
                                />
                              </div>
                              <div style={{ width:48, fontSize:12, color:'rgba(255,255,255,0.2)', fontFamily:MONO, textAlign:'right' }}>{pct.toFixed(0)}%</div>
                              <div style={{ width:180, fontSize:12, color:'rgba(255,255,255,0.4)', fontFamily:SYS, textAlign:'right', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{e.label.replace(' SDR','')} · {e.rtys.replace(' RTYS','')}</div>
                            </div>
                          );
                        })}
                      </motion.div>
                    );
                  })}
                </div>
              </div>

              {/* ── Panel B: same freq, multiple stations ── */}
              <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden' }}>
                <div style={{ padding:'12px 16px 8px', flexShrink:0, borderBottom:'1px solid rgba(255,255,255,0.05)' }}>
                  <div style={{ fontSize:17, fontWeight:700, color:'#fff', fontFamily:SYS }}>Same Freq · Multiple Stations</div>
                  <div style={{ fontSize:14, color:'rgba(255,255,255,0.28)', fontFamily:SYS, marginTop:2 }}>
                    {byFreq.length} frequencies shared by {byFreq.reduce((a,[,e])=>a+e.length,0)} stations
                  </div>
                </div>
                <div style={{ flex:1, overflowY:'auto', padding:'8px 16px 16px' }}>
                  {byFreq.length === 0 && (
                    <div style={{ color:'rgba(255,255,255,0.2)', fontSize:17, fontFamily:SYS, padding:'24px 0', textAlign:'center' }}>No collisions in this scope</div>
                  )}
                  {byFreq.map(([freq, entries], i) => (
                    <motion.div key={freq} initial={{ opacity:0, y:6 }} animate={{ opacity:1, y:0 }} transition={{ duration:0.18, delay: Math.min(i*0.03,0.3) }}
                      style={{ marginBottom:20 }}>
                      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:8 }}>
                        <span style={{ fontSize:17, fontWeight:700, color:CYAN, fontFamily:MONO }}>{freq} MHz</span>
                        <span style={{ fontSize:14, color:'rgba(255,255,255,0.35)', fontFamily:SYS }}>{entries.length} stations</span>
                      </div>
                      {entries.map((e, j) => {
                        const pct = ((j + 1) / entries.length) * 100;
                        return (
                          <div key={e.id} style={{ display:'flex', alignItems:'center', gap:8, marginBottom:5 }}>
                            <div style={{ flex:1, minWidth:0 }}>
                              <div style={{ fontSize:15, color:'rgba(255,255,255,0.7)', fontFamily:SYS, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{e.name}</div>
                              <div style={{ fontSize:12, color:'rgba(255,255,255,0.35)', fontFamily:SYS, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{e.label.replace(' SDR','')} · {e.rtys.replace(' RTYS','')}</div>
                            </div>
                            <div style={{ width:120, height:9, background:'rgba(255,255,255,0.06)', borderRadius:3, overflow:'hidden', flexShrink:0 }}>
                              <motion.div
                                initial={{ width:0 }} animate={{ width:'100%' }}
                                transition={{ duration:0.5, delay: Math.min(i*0.03,0.3) + j*0.05, ease:[0.16,1,0.3,1] }}
                                style={{ height:'100%', background: PIE_COLORS[(i + 3) % PIE_COLORS.length], borderRadius:3, opacity:0.75 }}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </motion.div>
                  ))}
                </div>
              </div>

            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

// ── RTYS PIE CHART SUB-TAB ────────────────────────────────────────────────────
function RtysTab() {
  const [selectedSdr, setSelectedSdr] = useState(null); // null = all
  const [hoveredSlice, setHoveredSlice] = useState(null);
  const [selectedSliceIdx, setSelectedSliceIdx] = useState(-1);
  const [pieSize, setPieSize] = useState(390);
  const pieColRef = useRef(null);

  // Per-SDR aggregated slices
  const sdrAggSlices = useMemo(() => SDR_IDS.map(id => {
    const sdr = SDR_DATA[id];
    const total = Object.values(sdr.rtys).reduce((s, v) => s + v.length, 0);
    return { id, label: sdr.label, value: total, color: sdr.color };
  }), []);

  // Per-RTYS slices when a SDR is selected
  const detailSlices = useMemo(() => {
    if (!selectedSdr) return null;
    const sdr = SDR_DATA[selectedSdr];
    return Object.entries(sdr.rtys).map(([name, freqs], i) => ({
      id: name, label: name, rtysName: name,
      value: freqs.length,
      color: PIE_COLORS[i % PIE_COLORS.length],
    }));
  }, [selectedSdr]);

  const activeSlices = selectedSdr && detailSlices ? detailSlices : sdrAggSlices;
  const grandTotal = sdrAggSlices.reduce((a, s) => a + s.value, 0);

  // Reset selection when switching context
  useEffect(() => { setSelectedSliceIdx(-1); setHoveredSlice(null); }, [selectedSdr]);

  // Measure center column width and derive pie size
  useEffect(() => {
    const el = pieColRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      const w = entries[0].contentRect.width;
      // pie = min(column width - 40px padding, column height * 0.72)
      const h = entries[0].contentRect.height;
      const size = Math.max(200, Math.min(w - 40, h * 0.68));
      setPieSize(Math.round(size));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const handlePieSelect = useCallback((slice, idx) => {
    if (!slice) { setSelectedSliceIdx(-1); return; }
    if (!selectedSdr && slice.id && SDR_DATA[slice.id]) {
      // Drill: clicking an SDR-level slice switches to that SDR
      setSelectedSdr(slice.id);
      setSelectedSliceIdx(-1);
    } else if (selectedSdr) {
      // Within a SDR: toggle RTYS selection
      setSelectedSliceIdx(prev => prev === idx ? -1 : idx);
    }
  }, [selectedSdr]);

  // What the user has pinned via click (SDR level uses selectedSdr, RTYS level uses selectedSliceIdx)
  // Hovered slice wins for live preview, else fall back to pinned
  const focusSlice = hoveredSlice ?? (selectedSliceIdx >= 0 ? activeSlices[selectedSliceIdx] : null);

  // Detail tiles
  const detailTiles = useMemo(() => {
    // Inside a SDR: show only hovered/selected RTYS, else all RTYS of that SDR
    if (selectedSdr) {
      if (focusSlice && focusSlice.rtysName) {
        const rtysName = focusSlice.rtysName;
        const freqs = SDR_DATA[selectedSdr].rtys[rtysName] || [];
        return [{ sdrId: selectedSdr, rtysName, freqs }];
      }
      return Object.entries(SDR_DATA[selectedSdr].rtys).map(([k, v]) => ({ sdrId: selectedSdr, rtysName: k, freqs: v }));
    }
    // All-regions view: hovered SDR slice previews that SDR's RTYS
    if (focusSlice && focusSlice.id && SDR_DATA[focusSlice.id]) {
      const id = focusSlice.id;
      return Object.entries(SDR_DATA[id].rtys).map(([k, v]) => ({ sdrId: id, rtysName: k, freqs: v }));
    }
    return SDR_IDS.flatMap(id => Object.entries(SDR_DATA[id].rtys).map(([k, v]) => ({ sdrId: id, rtysName: k, freqs: v })));
  }, [focusSlice, selectedSdr]);

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 164px)', overflow: 'hidden' }}>

      {/* ── LEFT sidebar ─────────────────────────── */}
      <div style={{
        width: 200, flexShrink: 0,
        borderRight: '1px solid rgba(0,229,255,0.07)',
        background: 'rgba(5,8,16,0.6)',
        overflowY: 'auto', padding: '14px 10px',
      }}>
        <div style={{ fontSize: 9, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.14em', textTransform: 'uppercase', fontFamily: SYS, marginBottom: 8 }}>
          Filter by SDR
        </div>
        <button
          className={`ov-menu-item${!selectedSdr ? ' active' : ''}`}
          onClick={() => { setSelectedSdr(null); setSelectedSliceIdx(-1); }}
          style={{
            width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '7px 9px', marginBottom: 3,
            background: !selectedSdr ? 'rgba(0,229,255,0.14)' : 'transparent',
            border: `1px solid ${!selectedSdr ? 'rgba(0,229,255,0.35)' : 'transparent'}`,
            borderRadius: 8, cursor: 'pointer',
          }}
        >
          <span style={{ fontSize: 11, color: !selectedSdr ? '#fff' : 'rgba(255,255,255,0.55)', fontFamily: SYS, fontWeight: !selectedSdr ? 700 : 400 }}>All Regions</span>
          <span style={{ fontSize: 9, fontFamily: MONO, color: CYAN }}>{grandTotal}</span>
        </button>
        {SDR_IDS.map(id => {
          const s = SDR_DATA[id];
          const cnt = Object.values(s.rtys).reduce((a, v) => a + v.length, 0);
          const isActive = selectedSdr === id;
          return (
            <button key={id}
              className={`ov-menu-item${isActive ? ' active' : ''}`}
              onClick={() => { setSelectedSdr(id); setSelectedSliceIdx(-1); }}
              style={{
                width: '100%', display: 'flex', alignItems: 'center', gap: 7,
                padding: '7px 9px', marginBottom: 3,
                background: isActive ? 'rgba(0,229,255,0.12)' : 'transparent',
                border: `1px solid ${isActive ? 'rgba(0,229,255,0.3)' : 'transparent'}`,
                borderRadius: 8, cursor: 'pointer', transition: 'all 0.15s ease',
              }}
            >
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: s.color, flexShrink: 0 }} />
              <span style={{ fontSize: 11, color: isActive ? '#e0f7ff' : 'rgba(255,255,255,0.5)', fontFamily: SYS, fontWeight: isActive ? 600 : 400, flex: 1, textAlign: 'left' }}>{s.label}</span>
              <span style={{ fontSize: 9, fontFamily: MONO, color: isActive ? CYAN : 'rgba(255,255,255,0.22)' }}>{cnt}</span>
            </button>
          );
        })}

        {/* Back button when drilled in */}
        {selectedSdr && (
          <button
            onClick={() => { setSelectedSdr(null); setSelectedSliceIdx(-1); }}
            style={{
              marginTop: 10, width: '100%', padding: '6px 9px', borderRadius: 7,
              background: 'rgba(0,229,255,0.06)', border: '1px solid rgba(0,229,255,0.18)',
              color: 'rgba(0,229,255,0.7)', fontSize: 10, fontFamily: SYS, cursor: 'pointer',
            }}
          >
            ← All Regions
          </button>
        )}
      </div>

      {/* ── CENTER: big interactive pie ──────────────────────── */}
      <div ref={pieColRef} style={{
        flex: '0 0 40%', minWidth: 320, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'flex-start',
        borderRight: '1px solid rgba(0,229,255,0.07)',
        padding: '18px 0 20px', overflowY: 'auto',
        background: 'rgba(5,8,16,0.3)',
      }}>
        {/* Title */}
        <div style={{ fontSize: 10, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.14em', textTransform: 'uppercase', fontFamily: SYS, marginBottom: 6, textAlign: 'center' }}>
          {selectedSdr ? `${SDR_DATA[selectedSdr].label} — RTYS Breakdown` : 'All Regions — Frequency Share'}
        </div>
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: SYS, marginBottom: 14, textAlign: 'center' }}>
          {hoveredSlice
            ? <span style={{ color: hoveredSlice.color, fontWeight: 600 }}>{hoveredSlice.label || hoveredSlice.rtysName}</span>
            : <span>Hover or click a slice</span>
          }
        </div>

        <InteractivePieChart
          slices={activeSlices}
          size={pieSize}
          selectedIdx={selectedSliceIdx}
          onHover={(slice, idx) => setHoveredSlice(slice)}
          onSelect={handlePieSelect}
        />

        {/* Tooltip card below pie */}
        <AnimatePresence mode="wait">
          {hoveredSlice && (
            <motion.div
              key={hoveredSlice.label || hoveredSlice.rtysName}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.18 }}
              style={{
                marginTop: 16, padding: '10px 20px',
                background: `${hoveredSlice.color}18`,
                border: `1px solid ${hoveredSlice.color}55`,
                borderRadius: 10, textAlign: 'center', width: 300,
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 700, color: hoveredSlice.color, fontFamily: SYS }}>
                {hoveredSlice.label || hoveredSlice.rtysName}
              </div>
              <div style={{ display: 'flex', justifyContent: 'center', gap: 20, marginTop: 6 }}>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.45)', fontFamily: SYS }}>
                  <span style={{ color: '#fff', fontWeight: 700, fontSize: 14 }}>{hoveredSlice.value}</span> freqs
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.45)', fontFamily: SYS }}>
                  <span style={{ color: hoveredSlice.color, fontWeight: 700, fontSize: 14 }}>
                    {((hoveredSlice.value / grandTotal) * 100).toFixed(1)}%
                  </span> share
                </span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Legend */}
        <div style={{ marginTop: 18, width: 380, display: 'flex', flexWrap: 'wrap', gap: '6px 14px', justifyContent: 'center' }}>
          {activeSlices.map((s, i) => {
            const isFocused = hoveredSlice === s || selectedSliceIdx === i;
            return (
              <div
                key={s.id || s.rtysName || i}
                onClick={() => handlePieSelect(s, i)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer',
                  opacity: hoveredSlice && hoveredSlice !== s ? 0.4 : 1,
                  transition: 'opacity 0.15s',
                }}
              >
                <span style={{
                  width: 8, height: 8, borderRadius: '50%', background: s.color, flexShrink: 0,
                  boxShadow: isFocused ? `0 0 7px ${s.color}` : 'none',
                  transition: 'box-shadow 0.15s',
                }} />
                <span style={{ fontSize: 10, color: isFocused ? '#fff' : 'rgba(255,255,255,0.55)', fontFamily: SYS }}>
                  {s.label || s.rtysName}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── RIGHT: simple frequency list ───────────────── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Header */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '0 16px', height: 30,
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          background: 'rgba(255,255,255,0.03)',
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 9, fontWeight: 600, color: 'rgba(255,255,255,0.3)', textTransform: 'uppercase', letterSpacing: '0.1em', fontFamily: SYS }}>Frequencies</span>
          <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)', fontFamily: MONO }}>
            {detailTiles.reduce((a, t) => a + t.freqs.length, 0)} freqs
          </span>
        </div>
        {/* Simple list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 16px 16px' }}>
          <AnimatePresence mode="popLayout">
            {detailTiles.map(({ sdrId, rtysName, freqs }, i) => {
              const sdr = SDR_DATA[sdrId];
              return (
                <motion.div
                  key={`${sdrId}-${rtysName}`}
                  layout
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -12 }}
                  transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.25) }}
                  style={{
                    marginBottom: 14,
                    padding: '10px 12px',
                    borderRadius: 10,
                    background: 'rgba(255,255,255,0.03)',
                    border: '1px solid rgba(255,255,255,0.05)',
                    borderLeft: `2px solid ${sdr.color}`,
                  }}
                >
                  {/* RTYS title */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#fff', fontFamily: SYS }}>{rtysName}</span>
                    {!selectedSdr && (
                      <span style={{ fontSize: 9, color: sdr.color, fontFamily: SYS, opacity: 0.85 }}>{sdr.label.replace(' SDR', '')}</span>
                    )}
                  </div>
                  {/* Freq chips */}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px 6px' }}>
                    {freqs.map((f, j) => (
                      <motion.span
                        key={j}
                        initial={{ opacity: 0, scale: 0.85 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ duration: 0.15, delay: Math.min(i * 0.03 + j * 0.015, 0.4) }}
                        style={{
                          fontSize: 10, fontFamily: MONO, color: 'rgba(255,255,255,0.7)',
                          background: 'rgba(255,255,255,0.05)',
                          borderRadius: 5, padding: '2px 7px',
                          border: '1px solid rgba(255,255,255,0.08)',
                        }}
                      >
                        {f.freq.toFixed(1)} <span style={{ color: 'rgba(255,255,255,0.35)', marginLeft: 3 }}>· {f.name}</span>
                      </motion.span>
                    ))}
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

// ── DOWNTIME SUB-TAB (moved from Analytics) ────────────────────────────────────
const DIGIT_FONT = "'DSDigit', monospace";

// Module-level client cache — shared across remounts, cleared after 5 min
const _outageCache = {};
const _outageCacheTs = {};
const _CACHE_MS = 5 * 60 * 1000;

function _isFresh(m) {
  return _outageCache[m] && (Date.now() - (_outageCacheTs[m] ?? 0)) < _CACHE_MS;
}

function Spinner() {
  return (
    <div style={{ display:'flex', justifyContent:'center', alignItems:'center', padding:'48px 0' }}>
      <div className="ov-spin" style={{ width:28, height:28, borderRadius:'50%', border:`2px solid rgba(0,229,255,0.2)`, borderTopColor:CYAN }} />
    </div>
  );
}

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

function DowntimeTab() {
  const { t } = useTranslation();
  const sdrNav = useSdrNavigation();
  const [data, setData] = useState(() => _outageCache['7d'] ?? null);
  const [busy, setBusy] = useState(false);
  const [errMsg, setErrMsg] = useState(null);
  const [mode, setMode] = useState('7d');
  const [hoveredBar, setHoveredBar] = useState(null);

  const load = useCallback(async (m, sdrId = '', sdr = null) => {
    const cacheKey = sdrId ? `${m}:${sdrId}` : m;
    if (_outageCache[cacheKey]) setData(_outageCache[cacheKey]);
    if (_isFresh(cacheKey)) return;
    setBusy(true); setErrMsg(null);
    try {
      const threshold = sdr?.thresholds?.noise_floor ?? DBFS_MIN;
      const sdrParam = sdrId ? `&sdr_id=${encodeURIComponent(sdrId)}` : '';
      const r = await fetch(`/api/analytics/outages?mode=${m}&threshold=${threshold}${sdrParam}`, { credentials: 'include' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      _outageCache[cacheKey] = d;
      _outageCacheTs[cacheKey] = Date.now();
      setData(d);
    }
    catch (e) { setErrMsg(e.message); }
    finally { setBusy(false); }
  }, []);

  useEffect(() => {
    load(mode, sdrNav.currentSdrId, sdrNav.currentSdr);
  }, [sdrNav.currentSdrId, mode]);

  const filteredStations = useMemo(() => {
    if (!data?.stations) return [];
    const sdrKey = API_ID_TO_SDR[sdrNav.currentSdrId];
    const mainRtys = sdrKey ? MAIN_RTYS_BY_SDR[sdrKey] : null;
    if (!mainRtys) return data.stations;
    const allowedFreqs = new Set((SDR_DATA[sdrKey].rtys[mainRtys] || []).map(f => Number(f.freq).toFixed(1)));
    return data.stations.filter(s => allowedFreqs.has(Number(s.freq).toFixed(1)));
  }, [data, sdrNav.currentSdrId]);

  const maxDrops = useMemo(() => {
    if (!filteredStations.length) return 1;
    return Math.max(1, ...filteredStations.flatMap(s => s.buckets || []));
  }, [filteredStations]);

  const getColor = (count) => {
    if (count === 0) return null;
    const ratio = count / maxDrops;
    if (ratio > 0.5) return '#ff4444';
    if (ratio > 0.25) return '#ffaa00';
    return '#00e5ff';
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding:'0 20px 20px', overflowY:'auto' }}>
      <SdrNavigator {...sdrNav} />
      <div style={{ display: 'flex', gap: 8 }}>
        {['24h', '7d', '14d'].map(k => (
          <button
            key={k}
            onClick={() => { setMode(k); load(k, sdrNav.currentSdrId, sdrNav.currentSdr); }}
            style={{
              padding: '8px 20px',
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 600,
              fontFamily: SYS,
              cursor: 'pointer',
              background: mode === k ? 'rgba(0,229,255,0.2)' : 'rgba(255,255,255,0.05)',
              border: `1px solid ${mode === k ? CYAN : 'rgba(255,255,255,0.15)'}`,
              color: mode === k ? CYAN : 'rgba(255,255,255,0.5)',
            }}
          >
            {k === '24h' ? t('analytics.outage.24h') : k === '7d' ? t('analytics.outage.7d') : t('analytics.outage.14d')}
          </button>
        ))}
      </div>
      {busy ? <Spinner /> : data ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {filteredStations.map((station, sIdx) => (
            <motion.div
              key={station.freq}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.3, delay: sIdx * 0.02 }}
              style={{
                background: 'rgba(8,12,22,0.95)',
                border: '1px solid rgba(0,229,255,0.1)',
                borderRadius: 8,
                padding: '8px 10px',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}
            >
              <div style={{ width: 90, flexShrink: 0 }}>
                <div style={{
                  fontFamily: DIGIT_FONT,
                  fontSize: 22,
                  color: station.total > 0 ? CYAN : 'rgba(255,255,255,0.3)',
                  textShadow: station.total > 0 ? `0 0 12px ${CYAN}44` : 'none',
                  lineHeight: 1,
                }}>{station.freq.toFixed(1)}</div>
              </div>
              <div style={{ 
                flex: 1, 
                display: 'grid', 
                gridTemplateColumns: `repeat(${station.buckets.length}, 1fr)`,
                gap: 1, 
                height: 50, 
                alignItems: 'flex-end',
                minWidth: 0,
              }}>
                {station.buckets.map((count, i) => {
                  const color = getColor(count);
                  const baseHeight = count > 0 ? Math.max(8, (count / maxDrops) * 36) : 4;
                  const isHovered = hoveredBar?.key === `${sIdx}-${i}`;
                  const hoverHeight = isHovered && count > 0 ? baseHeight + 6 : baseHeight;
                  
                  return (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        position: 'relative',
                        height: '100%',
                        justifyContent: 'flex-end',
                        minWidth: 0,
                      }}
                      onMouseEnter={() => setHoveredBar({ key: `${sIdx}-${i}`, count, label: data.labels[i], color })}
                      onMouseLeave={() => setHoveredBar(null)}
                    >
                      {isHovered && count > 0 && (
                        <div style={{
                          position: 'absolute',
                          bottom: '100%',
                          marginBottom: 8,
                          background: 'rgba(5,7,15,0.98)',
                          border: `1.5px solid ${color}`,
                          borderRadius: 6,
                          padding: '6px 10px',
                          fontSize: 12,
                          color: color,
                          fontFamily: MONO,
                          fontWeight: 700,
                          whiteSpace: 'nowrap',
                          zIndex: 100,
                          boxShadow: `0 4px 20px ${color}33`,
                        }}>
                          {data.labels[i]}: {count.toLocaleString()} {t('analytics.outage.drops')}
                        </div>
                      )}
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: hoverHeight, opacity: 1 }}
                        transition={{ 
                          height: { duration: 0.3, ease: [0.16, 1, 0.3, 1] },
                          opacity: { duration: 0.4, delay: i * 0.01 + sIdx * 0.015 }
                        }}
                        style={{
                          width: '100%',
                          minWidth: 2,
                          maxWidth: '100%',
                          background: count > 0 
                            ? `linear-gradient(180deg, ${color} 0%, ${color}dd 60%, ${color}88 100%)`
                            : 'rgba(255,255,255,0.06)',
                          borderRadius: 3,
                          border: 'none',
                          boxShadow: count > 0 
                            ? (isHovered 
                                ? `0 0 20px ${color}, 0 0 40px ${color}66, 0 -5px 15px ${color}44`
                                : `0 0 6px ${color}33, 0 2px 4px rgba(0,0,0,0.3)`) 
                            : 'none',
                          cursor: count > 0 ? 'pointer' : 'default',
                        }}
                      />
                      <div style={{
                        marginTop: 4,
                        fontSize: 8,
                        fontFamily: MONO,
                        color: count > 0 ? 'rgba(255,255,255,0.6)' : 'rgba(255,255,255,0.2)',
                        textAlign: 'center',
                        height: 10,
                        lineHeight: '10px',
                        fontWeight: count > 0 ? 500 : 400,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        width: '100%',
                      }}>{data.labels[i]}</div>
                    </div>
                  );
                })}
              </div>
              <div style={{
                width: 36,
                textAlign: 'right',
                fontFamily: DIGIT_FONT,
                fontSize: 18,
                color: station.total > 10 ? '#ff4444' : station.total > 3 ? '#ffaa00' : station.total > 0 ? CYAN : 'rgba(255,255,255,0.2)',
                textShadow: station.total > 0 ? `0 0 10px currentColor` : 'none',
              }}>{station.total >= 1000000 ? `${(station.total/1000000).toFixed(1)}M` : station.total >= 1000 ? `${(station.total/1000).toFixed(station.total < 10000 ? 1 : 0)}k` : station.total}</div>
            </motion.div>
          ))}
        </div>
      ) : null}
      {errMsg && <div style={{ textAlign: 'center', color: '#ff4444', fontFamily: MONO }}>{errMsg}</div>}
    </div>
  );
}

// ── DROP HISTORY SUB-TAB ───────────────────────────────────────────────────────
function getStationInfoForApiId(apiId, freq, tolerance = 0.05) {
  const sdrKey = API_ID_TO_SDR[apiId];
  const f = Number(freq);
  const fStr = f.toFixed(1);
  if (fStr === '223.9') return { rtys: 'DAB+', name: '12A' };
  if (!sdrKey || !SDR_DATA[sdrKey]) return null;
  let best = null;
  for (const [rtysName, entries] of Object.entries(SDR_DATA[sdrKey].rtys)) {
    for (const entry of entries) {
      const diff = Math.abs(Number(entry.freq) - f);
      if (diff <= tolerance && (!best || diff < best.diff)) {
        best = { rtys: rtysName, name: entry.name, diff };
      }
    }
  }
  return best;
}

const fmtTime = (ts) => {
  const d = new Date(Number(ts) * 1000);
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return `${h}:${m}:${s}`;
};

const formatDuration = (sec) => {
  const dur = Math.max(0, Math.round(sec));
  const h = String(Math.floor(dur / 3600)).padStart(2, '0');
  const m = String(Math.floor((dur % 3600) / 60)).padStart(2, '0');
  const s = String(dur % 60).padStart(2, '0');
  return `${h}:${m}:${s}`;
};

const OverviewHistoryRow = memo(function OverviewHistoryRow({ row, isNew, thresholds }) {
  const th = thresholds[row.sdr_id] || {};
  const cal = { db_min: th.db_min ?? DBFS_MIN, db_max: th.db_max ?? DBFS_MAX, noise_floor: th.noise_floor ?? DBFS_MIN, signal_threshold: th.signal_threshold ?? DBFS_MAX };
  const color = dbfsColor(row.dbfs, cal);
  return (
    <div
      style={{ display: 'grid', gridTemplateColumns: '56px 60px 1fr 50px 40px', gap: 6, padding: '7px 8px', borderRadius: 7, marginBottom: 2, border: '1px solid rgba(255,255,255,0.04)' }}
    >
      <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.28)', whiteSpace: 'nowrap' }}>{fmtTime(row.ts)}</span>
      <span style={{ fontFamily: MONO, fontSize: 9, color: '#00e5ff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{row.sdr_id || '—'}</span>
      <span style={{ fontFamily: DIGIT, fontSize: 16, color: color, letterSpacing: '0.04em', textShadow: `0 0 6px ${color}55` }}>{fmtFreq(row.freq)}</span>
      <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, textAlign: 'right', color: row.dbfs < (th.noise_floor ?? DBFS_MIN) ? '#ff5722' : color }}>{row.dbfs.toFixed(1)}</span>
      <span style={{ fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.35)', textAlign: 'right' }}>{row.snr.toFixed(1)}</span>
    </div>
  );
}, (prev, next) => {
  return prev.row.id === next.row.id &&
         prev.row.ts === next.row.ts &&
         prev.row.freq === next.row.freq &&
         prev.row.dbfs === next.row.dbfs &&
         prev.row.snr === next.row.snr &&
         prev.row.sdr_id === next.row.sdr_id &&
         prev.isNew === next.isNew &&
         prev.thresholds === next.thresholds;
});

// ── COLLAPSED SDR STATUS PANEL (matches Home tab left panel) ─────────────────────
function useHistoryRows(active = true) {
  const [rows, setRows] = useState([]);
  const [newIds, setNewIds] = useState(new Set());
  const knownIdsRef = useRef(new Set());

  useEffect(() => {
    let cancelled = false;
    let fetchId = null;
    if (!active) return () => { clearInterval(fetchId); };
    const fetchHistory = async () => {
      if (document.hidden || cancelled) return;
      const cacheKey = '/api/history';
      const cached = _getCached(cacheKey, 15000);
      if (cached) {
        if (!cancelled) {
          setRows(cached);
          knownIdsRef.current = new Set(cached.map(r => r.id));
        }
        return;
      }
      try {
        const res = await fetch('/api/history', { credentials: 'include' });
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (cancelled) return;
        _setCached(cacheKey, data);
        const incoming = new Set();
        const fresh = [];
        data.forEach(r => {
          incoming.add(r.id);
          if (!knownIdsRef.current.has(r.id)) fresh.push(r.id);
        });
        if (fresh.length > 0) {
          setNewIds(prev => { const n = new Set(prev); fresh.forEach(id => n.add(id)); return n; });
          setTimeout(() => setNewIds(prev => { const n = new Set(prev); fresh.forEach(id => n.delete(id)); return n; }), 900);
        }
        knownIdsRef.current = incoming;
        setRows(data);
      } catch { /* ignore */ }
    };
    const start = () => { fetchHistory(); fetchId = setInterval(fetchHistory, 10000); };
    const stop = () => { clearInterval(fetchId); fetchId = null; };
    const onVis = () => { if (document.hidden) { stop(); } else { stop(); start(); } };
    document.addEventListener('visibilitychange', onVis);
    start();
    return () => { cancelled = true; stop(); document.removeEventListener('visibilitychange', onVis); };
  }, [active]);

  return { rows, newIds };
}

function SdrStatusMini({ tele, rows }) {
  const { t } = useTranslation();

  const filteredDrops24h = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return rows.reduce((count, r) => {
      if (!r.ts || !r.sdr_id) return count;
      const ts = new Date(Number(r.ts) * 1000).getTime();
      if (Number.isNaN(ts) || ts < cutoff) return count;
      const sdrKey = API_ID_TO_SDR[r.sdr_id];
      if (!sdrKey) return count;
      const mainRtys = MAIN_RTYS_BY_SDR[sdrKey];
      if (!mainRtys) return count;
      const entries = SDR_DATA[sdrKey]?.rtys?.[mainRtys];
      if (!entries) return count;
      const freq = Number(r.freq).toFixed(1);
      return entries.some((e) => Number(e.freq).toFixed(1) === freq) ? count + 1 : count;
    }, 0);
  }, [rows]);

  const sdrNodes = tele?.sdr_nodes && tele.sdr_nodes.length > 0 ? tele.sdr_nodes : [{ id: 'SDR', online: tele?.sdr_status }];
  const sdrOnline = sdrNodes.filter(n => n.online).length;
  const sdrTotal = sdrNodes.length;
  const sdrPct = sdrTotal > 0 ? sdrOnline / sdrTotal : 0;
  const sdrColor = sdrPct === 1 ? '#00e676' : sdrPct >= 0.6 ? '#ffaf00' : '#ff5252';

  const cpuColor = (tele?.cpu ?? 0) > 80 ? ORANGE : (tele?.cpu ?? 0) > 50 ? AMBER : CYAN;
  const ramColor = (tele?.ram ?? 0) > 85 ? ORANGE : (tele?.ram ?? 0) > 60 ? AMBER : CYAN;
  const localDbPct = Math.min(((tele?.backup_local_mb ?? 0) / (30 * 1024)) * 100, 100);
  const cloudDbPct = Math.min(((tele?.backup_r2_mb ?? 0) / (10 * 1024)) * 100, 100);

  const MiniBar = ({ pct, color, h = 3 }) => (
    <div style={{ height: h, borderRadius: h / 2, background: 'rgba(255,255,255,0.06)', overflow: 'hidden', marginTop: 3 }}>
      <div style={{ width: `${pct}%`, height: '100%', borderRadius: h / 2, background: color, boxShadow: `0 0 6px ${color}66` }} />
    </div>
  );

  const StatusDot = ({ on, label }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <div style={{ width: 6, height: 6, borderRadius: '50%', background: on ? '#00e676' : '#ff5252', boxShadow: `0 0 6px ${on ? '#00e676' : '#ff5252'}` }} />
      <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)', fontWeight: 600 }}>{label}</span>
    </div>
  );

  return (
    <div style={{
      height: '100%',
      display: 'flex', flexDirection: 'column',
      padding: '10px 8px',
      overflowY: 'auto',
      background: 'linear-gradient(180deg, rgba(10,14,22,0.98) 0%, rgba(8,12,18,0.95) 100%)',
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
        <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '6px 7px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'rgba(255,255,255,0.25)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{t('sigint.sdrStatus')}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: sdrColor, boxShadow: `0 0 6px ${sdrColor}` }} />
            <span style={{ fontFamily: DIGIT, fontSize: 12, color: sdrColor }}>{sdrOnline}/{sdrTotal}</span>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '5px 7px' }}>
          <StatusDot on={tele?.db_status} label="DB" />
          <StatusDot on={tele?.whatsapp_status} label="WA" />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '7px 8px' }}>
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', fontWeight: 600, letterSpacing: '0.06em' }}>{t('sigint.drops24h')}</span>
          <span style={{ fontFamily: DIGIT, fontSize: 22, color: filteredDrops24h > 0 ? AMBER : CYAN, lineHeight: 1 }}>{filteredDrops24h}</span>
          <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>{t('sigint.below65')}</span>
        </div>

        <div style={{ fontSize: 12, fontWeight: 700, color: 'rgba(255,255,255,0.25)', textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>{t('sigint.system')}</div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '6px 7px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', fontWeight: 600 }}>{t('sigint.cpu')}</span>
              <span style={{ fontFamily: DIGIT, fontSize: 14, color: cpuColor, letterSpacing: '0.02em' }}>{(tele?.cpu ?? 0).toFixed(1)}%</span>
            </div>
            <MiniBar pct={tele?.cpu ?? 0} color={cpuColor} />
          </div>
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '6px 7px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', fontWeight: 600 }}>{t('sigint.ram')}</span>
              <span style={{ fontFamily: DIGIT, fontSize: 14, color: ramColor, letterSpacing: '0.02em' }}>{(tele?.ram ?? 0).toFixed(1)}%</span>
            </div>
            <MiniBar pct={tele?.ram ?? 0} color={ramColor} />
          </div>
        </div>

        <div style={{ fontSize: 12, fontWeight: 700, color: 'rgba(255,255,255,0.25)', textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center' }}>{t('sigint.data')}</div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '6px 7px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', fontWeight: 600 }}>{t('sigint.local')}</span>
              <span style={{ fontFamily: DIGIT, fontSize: 12, color: 'rgba(255,255,255,0.75)', letterSpacing: '0.02em' }}>{localDbPct.toFixed(1)}%</span>
            </div>
            <MiniBar pct={localDbPct} color={CYAN} />
          </div>
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '6px 7px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', fontWeight: 600 }}>{t('sigint.cloud')}</span>
              <span style={{ fontFamily: DIGIT, fontSize: 12, color: 'rgba(255,255,255,0.75)', letterSpacing: '0.02em' }}>{cloudDbPct.toFixed(1)}%</span>
            </div>
            <MiniBar pct={cloudDbPct} color={PURPLE} />
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.03)', borderRadius: 6, padding: '5px 7px' }}>
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', fontWeight: 600 }}>{t('sigint.users')}</span>
          <span style={{ fontFamily: DIGIT, fontSize: 12, color: CYAN }}>{tele?.active_users ?? 0}</span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'rgba(255,255,255,0.25)', textTransform: 'uppercase', letterSpacing: '0.08em', textAlign: 'center', display: 'block' }}>{t('sigint.sdrList')}</span>
          <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: 8, padding: '6px 7px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 6px' }}>
            {Array.from({ length: 20 }, (_, i) => {
              const n = sdrNodes[i];
              if (n) {
                const c = n.online ? '#00e676' : '#ff5252';
                const ageSec = n.last_seen ? Math.floor(Date.now() / 1000 - n.last_seen) : null;
                const ageLabel = !n.online && ageSec !== null ? (ageSec < 60 ? `${ageSec}s` : ageSec < 3600 ? `${Math.floor(ageSec/60)}m` : `${Math.floor(ageSec/3600)}h`) : null;
                return (
                  <div key={n.id} style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0 }}>
                    <div style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: c, boxShadow: `0 0 4px ${c}` }} />
                    <span style={{ fontSize: 9, fontFamily: MONO, fontWeight: 600, color: n.online ? 'rgba(255,255,255,0.65)' : 'rgba(255,82,82,0.8)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.id}{ageLabel ? ` ${ageLabel}` : ''}</span>
                  </div>
                );
              }
              return (
                <div key={`empty-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <div style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: 'rgba(255,255,255,0.06)' }} />
                  <span style={{ fontSize: 9, fontFamily: MONO, color: 'rgba(255,255,255,0.1)' }}>—</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── EXPANDED SDR HISTORY PANEL (matches Home left panel expanded view) ────────────
function SdrHistoryExpanded({ rows, newIds }) {
  const { t } = useTranslation();
  const [showExport, setShowExport] = useState(false);
  const [exporting, setExporting] = useState(false);
  const pad = n => String(n).padStart(2, '0');
  const localIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const [exportFrom, setExportFrom] = useState(() => { const d = new Date(); d.setHours(d.getHours() - 24); return localIso(d); });
  const [exportTo, setExportTo] = useState(() => localIso(new Date()));
  const { fullFrom, fullTo } = useMemo(() => {
    if (!rows.length) return { fullFrom: null, fullTo: null };
    const minTs = Math.min(...rows.map(r => r.ts));
    const maxTs = Math.max(...rows.map(r => r.ts));
    return { fullFrom: localIso(new Date(minTs * 1000)), fullTo: localIso(new Date(maxTs * 1000)) };
  }, [rows]);
  const thresholds = useSdrThresholds();

  return (
    <>
      <div style={{
        width: 320, height: '100%',
        display: 'flex', flexDirection: 'column',
        background: 'linear-gradient(180deg, rgba(8,11,18,0.95) 0%, rgba(11,15,23,0.88) 100%)',
        borderRight: '1px solid rgba(255,255,255,0.04)',
      }}>
        <div style={{ padding: '14px 16px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: '#fff', letterSpacing: '-0.01em' }}>{t('sigint.panel')}</div>
            <button onClick={() => setShowExport(true)} style={{
              background: 'rgba(0,229,255,0.10)', border: '1px solid rgba(0,229,255,0.30)',
              borderRadius: 7, color: CYAN, fontSize: 10, fontWeight: 600,
              padding: '5px 11px', cursor: 'pointer', fontFamily: SYS, letterSpacing: '0.05em',
            }}>{t('sigint.export')}</button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '56px 60px 1fr 50px 40px', gap: 6, padding: '6px 14px', fontSize: 9, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.22)', flexShrink: 0 }}>
          <span>{t('sigint.time')}</span><span>SDR</span><span>{t('sigint.freq')}</span>
          <span style={{ textAlign: 'right' }}>{t('sigint.dbfs')}</span>
          <span style={{ textAlign: 'right' }}>{t('sigint.snr')}</span>
        </div>
        <div className="ov-scrollable" style={{ flex: 1, overflowY: 'auto', padding: '0 6px 0' }}>
          {rows.map(r => (
            <OverviewHistoryRow key={r.id} row={r} isNew={newIds.has(r.id)} thresholds={thresholds} />
          ))}
        </div>
      </div>

      {createPortal(
        <AnimatePresence>
          {showExport && (
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.18 }}
              style={{
                position: 'fixed', inset: 0, zIndex: 9999,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'rgba(0,0,0,0.85)',
              }} onClick={() => setShowExport(false)}>
              <motion.div
                initial={{ opacity: 0, y: 24, scale: 0.95 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 16, scale: 0.97 }}
                transition={{ duration: 0.22, ease: 'easeOut' }}
                onClick={e => e.stopPropagation()}
                style={{
                  background: 'rgba(8,12,22,0.97)',
                  border: '1px solid rgba(0,229,255,0.18)',
                  borderRadius: 20,
                  boxShadow: '0 0 0 1px rgba(0,229,255,0.06), 0 24px 64px rgba(0,0,0,0.7), 0 0 40px rgba(0,229,255,0.06)',
                  padding: '28px 28px 24px',
                  width: 340, display: 'flex', flexDirection: 'column', gap: 20, fontFamily: SYS,
                }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: '#fff', letterSpacing: '-0.01em', marginBottom: 3 }}>{t('sigint.exportCsv')}</div>
                    <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.30)', fontWeight: 300 }}>{t('sigint.selectRange')}</div>
                  </div>
                  <button onClick={() => setShowExport(false)} style={{
                    background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.10)',
                    borderRadius: 8, color: 'rgba(255,255,255,0.50)', fontSize: 14, cursor: 'pointer',
                    width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    lineHeight: 1, padding: 0, transition: 'all 0.15s',
                  }}>✕</button>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {[
                    ['1h', 1],
                    ['6h', 6],
                    ['12h', 12],
                    ['24h', 24],
                    ['3d', 72],
                    ['7d', 168],
                    ...(fullFrom ? [['Full', null]] : []),
                  ].map(([lbl, hrs]) => (
                    <button key={lbl} onClick={() => {
                      if (hrs) {
                        const now = new Date();
                        const f = new Date(now - hrs * 3600000);
                        setExportFrom(localIso(f));
                        setExportTo(localIso(now));
                      } else {
                        setExportFrom(fullFrom);
                        setExportTo(fullTo);
                      }
                    }} style={{
                      flex: 1, background: 'rgba(0,229,255,0.07)', border: '1px solid rgba(0,229,255,0.18)',
                      borderRadius: 8, color: 'rgba(0,229,255,0.80)', fontSize: 11, fontWeight: 600,
                      padding: '6px 0', cursor: 'pointer', fontFamily: SYS, letterSpacing: '0.04em',
                      transition: 'all 0.15s', minWidth: '12%',
                    }}>{lbl}</button>
                  ))}
                </div>
                {[[t('common.from'), exportFrom, setExportFrom], [t('common.to'), exportTo, setExportTo]].map(([label, val, setter]) => (
                  <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.28)' }}>{label}</span>
                    <input type="datetime-local" value={val} onChange={e => setter(e.target.value)}
                      style={{
                        width: '100%', boxSizing: 'border-box',
                        background: 'rgba(255,255,255,0.05)',
                        border: '1px solid rgba(0,229,255,0.15)',
                        borderRadius: 10, color: '#fff', padding: '10px 14px',
                        fontSize: 13, fontFamily: MONO, outline: 'none', colorScheme: 'dark',
                        transition: 'border-color 0.15s',
                      }}
                      onFocus={e => e.target.style.borderColor = 'rgba(0,229,255,0.45)'}
                      onBlur={e => e.target.style.borderColor = 'rgba(0,229,255,0.15)'}
                    />
                  </div>
                ))}
                <div style={{ height: 1, background: 'rgba(255,255,255,0.06)', margin: '0 -4px' }} />
                <button
                  disabled={exporting}
                  onClick={async () => {
                    setExporting(true);
                    try {
                      const fromTs = exportFrom ? new Date(exportFrom).getTime() / 1000 : 0;
                      const toTs = exportTo ? new Date(exportTo).getTime() / 1000 : 0;
                      const controller = new AbortController();
                      const tid = setTimeout(() => controller.abort(), 30000);
                      const res = await fetch(`/api/export?from_ts=${fromTs}&to_ts=${toTs}`, { signal: controller.signal, credentials: 'include' });
                      clearTimeout(tid);
                      if (!res.ok) throw new Error(`Export failed: ${res.status}`);
                      const blob = await res.blob();
                      const a = document.createElement('a');
                      a.href = URL.createObjectURL(blob);
                      a.download = `cgrep_${exportFrom.replace(/[T:]/g, '-')}_to_${exportTo.replace(/[T:]/g, '-')}.csv`;
                      a.click(); URL.revokeObjectURL(a.href);
                      setShowExport(false);
                    } catch (_e) {
                      console.error('Export error:', _e);
                    } finally {
                      setExporting(false);
                    }
                  }}
                  style={{
                    background: exporting ? 'rgba(0,229,255,0.05)' : 'linear-gradient(135deg,rgba(0,229,255,0.20),rgba(74,0,224,0.20))',
                    border: `1px solid ${exporting ? 'rgba(0,229,255,0.12)' : 'rgba(0,229,255,0.35)'}`,
                    borderRadius: 12, color: exporting ? 'rgba(0,229,255,0.35)' : CYAN,
                    fontSize: 13, fontWeight: 700, padding: '13px', cursor: exporting ? 'default' : 'pointer',
                    fontFamily: SYS, letterSpacing: '0.06em', textTransform: 'uppercase',
                    transition: 'all 0.2s',
                    boxShadow: exporting ? 'none' : '0 0 20px rgba(0,229,255,0.10)',
                  }}
                >{exporting ? t('sigint.preparing') : t('sigint.downloadCsv')}</button>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      , document.body)}
    </>
  );
}

const DropHistoryRow = memo(function DropHistoryRow({ d, selectedDrop, onClick, displayPower, unitLabel }) {
  const active = selectedDrop?.id === d.id && selectedDrop?.freq === d.freq && selectedDrop?.sdrId === d.sdrId;
  const dur = Math.max(0, Math.round(d.durationSec));
  const hh = String(Math.floor(dur / 3600)).padStart(2, '0');
  const mm = String(Math.floor((dur % 3600) / 60)).padStart(2, '0');
  const ss = String(dur % 60).padStart(2, '0');
  return (
    <div
      onClick={() => onClick(d)}
      style={{
        position: 'relative',
        padding: '10px 14px 10px 16px',
        background: active ? 'rgba(0,229,255,0.10)' : 'rgba(0,229,255,0.03)',
        border: `1px solid ${active ? 'rgba(0,229,255,0.35)' : 'rgba(0,229,255,0.08)'}`,
        borderRadius: 10,
        overflow: 'hidden',
        cursor: 'pointer',
        transition: 'all 0.15s ease',
      }}
    >
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, background: d.sdrColor }} />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{ fontFamily: SYS, fontSize: 10, color: CYAN, fontWeight: 700, flexShrink: 0 }}>{d.sdrId}</span>
          <span style={{ fontFamily: SYS, fontSize: 13, color: '#fff', fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.name}</span>
          <span style={{
            fontFamily: SYS, fontSize: 9, color: 'rgba(255,255,255,0.55)',
            padding: '1px 5px', background: 'rgba(255,255,255,0.06)', borderRadius: 4,
            whiteSpace: 'nowrap', flexShrink: 0,
          }}>{d.rtys}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: MONO, fontSize: 11, flexShrink: 0 }}>
          <span style={{ color: AMBER }}>{d.freq} MHz</span>
          <span style={{ color: '#ff7043', fontWeight: 700 }}>{displayPower(d.rawDbfs)} {unitLabel}</span>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, fontFamily: SYS, fontSize: 10, color: 'rgba(255,255,255,0.5)' }}>
        <span>Duration <b style={{ color: 'rgba(255,255,255,0.85)' }}>{hh}:{mm}:{ss}</b></span>
        <span style={{ fontFamily: MONO }}>{d.startTime} → {d.endTime}</span>
      </div>
    </div>
  );
}, (prev, next) => {
  return prev.d === next.d &&
         prev.selectedDrop === next.selectedDrop &&
         prev.onClick === next.onClick &&
         prev.displayPower === next.displayPower &&
         prev.unitLabel === next.unitLabel;
});

const FilterButton = memo(function FilterButton({ mode, label, filterMode, setFilterMode }) {
  return (
    <button
      onClick={() => setFilterMode(mode)}
      style={{
        fontFamily: SYS,
        fontSize: 11,
        padding: '4px 10px',
        borderRadius: 6,
        cursor: 'pointer',
        border: `1px solid ${filterMode === mode ? 'rgba(0,229,255,0.5)' : 'rgba(255,255,255,0.1)'}`,
        background: filterMode === mode ? 'rgba(0,229,255,0.12)' : 'rgba(255,255,255,0.04)',
        color: filterMode === mode ? CYAN : 'rgba(255,255,255,0.45)',
      }}
    >{label}</button>
  );
});

const UnitButton = memo(function UnitButton({ mode, label, unit, setUnit }) {
  return (
    <button
      onClick={() => setUnit(mode)}
      style={{
        fontFamily: SYS,
        fontSize: 11,
        padding: '4px 10px',
        borderRadius: 6,
        cursor: 'pointer',
        border: `1px solid ${unit === mode ? 'rgba(255,175,0,0.5)' : 'rgba(255,255,255,0.1)'}`,
        background: unit === mode ? 'rgba(255,175,0,0.12)' : 'rgba(255,255,255,0.04)',
        color: unit === mode ? AMBER : 'rgba(255,255,255,0.45)',
      }}
    >{label}</button>
  );
});

const DropHistoryRightPanel = memo(function DropHistoryRightPanel({ pieColRef, filteredDrops, pieSlices, pieSize, selectedDrop, scanLoading, scanError, scanRows, onBack, unitLabel, pieLevel, pieTitle, onPieSelect, onPieBack }) {
  const exportScanRows = useCallback(() => {
    if (!scanRows.length) return;
    const header = ['RTYS','Station','id','timestamp','frequency_mhz','power_dbfs','floor_dbfs','snr_db','sdr_id'];
    const lines = [header, ...scanRows.map(r => [
      selectedDrop.rtys,
      selectedDrop.name,
      r.id,
      r.timestamp,
      r.frequency_mhz,
      r.power_dbfs,
      r.floor_dbfs,
      r.snr_db,
      r.sdr_id,
    ])];
    const csv = lines.map(row => row.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `drop_scan_${selectedDrop.sdrId}_${selectedDrop.freq}_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [scanRows, selectedDrop]);

  if (!selectedDrop) {
    return (
      <div ref={pieColRef} style={{
        flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', padding: '20px 0', overflow: 'hidden',
      }}>
        <div style={{ fontSize: 10, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.14em', textTransform: 'uppercase', fontFamily: SYS, marginBottom: 6 }}>
          {pieTitle}
        </div>
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: SYS, marginBottom: 6 }}>
          {filteredDrops.length} total drops
        </div>
        {pieLevel !== 'region' && (
          <button
            onClick={onPieBack}
            style={{
              fontFamily: SYS, fontSize: 10, color: CYAN, cursor: 'pointer',
              background: 'rgba(0,229,255,0.08)', border: '1px solid rgba(0,229,255,0.2)',
              borderRadius: 6, padding: '4px 10px', marginBottom: 10,
            }}
          >← Back</button>
        )}
        {pieSlices.length === 0 ? (
          <div style={{ color: 'rgba(255,255,255,0.3)', fontFamily: SYS, fontSize: 13, marginTop: 40 }}>No data</div>
        ) : (
          <DropHistoryPieChart
            data={pieSlices}
            size={pieSize}
            innerRadius={pieSize * 0.32}
            outerRadius={pieSize * 0.44}
            onSelect={onPieSelect}
          />
        )}
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '14px 18px', borderBottom: '1px solid rgba(0,229,255,0.08)', flexShrink: 0,
      }}>
        <div>
          <div style={{ fontFamily: SYS, fontSize: 13, color: '#fff', fontWeight: 600 }}>{selectedDrop.name}</div>
          <div style={{ fontFamily: SYS, fontSize: 10, color: 'rgba(255,255,255,0.45)' }}>
            {selectedDrop.sdrId} · {selectedDrop.freq} MHz · {selectedDrop.rtys}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            onClick={exportScanRows}
            disabled={scanLoading || !scanRows.length}
            style={{
              fontFamily: SYS, fontSize: 10, color: scanLoading || !scanRows.length ? 'rgba(0,229,255,0.35)' : CYAN, cursor: scanLoading || !scanRows.length ? 'default' : 'pointer',
              background: 'rgba(0,229,255,0.08)', border: '1px solid rgba(0,229,255,0.2)',
              borderRadius: 6, padding: '5px 10px',
            }}
          >Export rows</button>
          <button
            onClick={onBack}
            style={{
              fontFamily: SYS, fontSize: 10, color: CYAN, cursor: 'pointer',
              background: 'rgba(0,229,255,0.08)', border: '1px solid rgba(0,229,255,0.2)',
              borderRadius: 6, padding: '5px 10px',
            }}
          >← Back to chart</button>
        </div>
      </div>
      <div className="ov-scrollable" style={{ flex: 1, minHeight: 0, padding: '12px 16px' }}>
        {scanLoading ? (
          <div style={{ textAlign: 'center', padding: 30, color: 'rgba(255,255,255,0.4)', fontFamily: SYS }}>Loading scan rows…</div>
        ) : scanError ? (
          <div style={{ textAlign: 'center', padding: 30, color: '#ff6b6b', fontFamily: SYS, fontSize: 12 }}>{scanError}</div>
        ) : scanRows.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 30, color: 'rgba(255,255,255,0.3)', fontFamily: SYS }}>
            No scan rows found
            {selectedDrop?.debugUrl && (
              <div style={{ marginTop: 10, fontSize: 10, color: 'rgba(255,255,255,0.2)', wordBreak: 'break-all' }}>
                {selectedDrop.debugUrl}
                <br />
                Debug (paste in browser): {selectedDrop.debugUrl.replace('/drop_rows?', '/drop_rows_debug?').replace(/&freq=[^&]*/, '')}
              </div>
            )}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{
              display: 'grid', gridTemplateColumns: '80px 160px 90px 90px 90px 70px 90px',
              gap: 8, padding: '0 8px 6px', fontFamily: SYS, fontSize: 9,
              color: 'rgba(0,229,255,0.55)', letterSpacing: '0.08em', textTransform: 'uppercase',
            }}>
              <span>id</span>
              <span>timestamp</span>
              <span>freq</span>
              <span>power</span>
              <span>floor</span>
              <span>snr</span>
              <span>sdr-id</span>
            </div>
            {scanRows.map((row, idx) => (
              <div key={idx} style={{
                display: 'grid', gridTemplateColumns: '80px 160px 90px 90px 90px 70px 90px',
                gap: 8, padding: '8px 10px', borderRadius: 6,
                background: 'rgba(0,229,255,0.04)', border: '1px solid rgba(0,229,255,0.06)',
                fontFamily: MONO, fontSize: 11, color: 'rgba(255,255,255,0.85)',
              }}>
                <span style={{ color: 'rgba(255,255,255,0.5)' }}>{row.id}</span>
                <span>{row.timestamp || '—'}</span>
                <span>{row.frequency_mhz}</span>
                <span style={{ color: '#ff7043' }}>{row.power_dbfs}</span>
                <span style={{ color: 'rgba(255,255,255,0.5)' }}>{row.floor_dbfs}</span>
                <span style={{ color: CYAN }}>{row.snr_db}</span>
                <span style={{ color: 'rgba(255,255,255,0.5)' }}>{row.sdr_id}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});

const DropHistoryTab = memo(function DropHistoryTab({ drops: rawDrops, loading: parentLoading, error: parentError, refreshDrops, tele, active }) {
  const { t } = useTranslation();
  const { rows, newIds } = useHistoryRows(active);
  const [filterMode, setFilterMode] = useState('known'); // 'known' | 'main'
  const [unit, setUnit] = useState('dbm'); // 'dbm' | 'dbuv'
  const [selectedDrop, setSelectedDrop] = useState(null);
  const [scanRows, setScanRows] = useState([]);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState(null);
  const [visibleCount, setVisibleCount] = useState(50);
  const [pieSize, setPieSize] = useState(() => Math.min(Math.floor(window.innerWidth * 0.45), Math.floor(window.innerHeight * 0.65)));
  const [pieLevel, setPieLevel] = useState('region'); // 'region' | 'rtys' | 'station'
  const [pieSdr, setPieSdr] = useState(null);
  const [pieRtys, setPieRtys] = useState(null);
  const pieColRef = useRef(null);

  const loading = parentLoading ?? true;
  const error = parentError;

  const drops = useMemo(() => {
    if (!Array.isArray(rawDrops)) return [];
    return rawDrops.map(r => {
      const info = getStationInfoForApiId(r.sdr_id, r.freq);
      const sdrKey = API_ID_TO_SDR[r.sdr_id];
      const sdrColor = sdrKey ? SDR_DATA[sdrKey]?.color || CYAN : CYAN;
      const mainRtys = sdrKey ? MAIN_RTYS_BY_SDR[sdrKey] : null;
      const rawDbfs = Number(r.dbfs);
      const startTs = Number(r.ts) - (r.duration_sec || 0);
      const endTs = Number(r.ts);
      return {
        id: r.id,
        sdrId: r.sdr_id,
        sdrKey,
        sdrColor,
        rtys: info?.rtys || '—',
        name: info?.name || 'Unknown',
        isKnown: !!info?.name,
        isMain: !!info?.rtys && info.rtys === mainRtys,
        freq: Number(r.freq).toFixed(1),
        rawFreq: Number(r.freq),
        rawDbfs,
        durationSec: r.duration_sec || 0,
        floorDbfs: r.floor_dbfs ?? '',
        snrDb: r.snr_db ?? '',
        startTime: fmtTime(startTs),
        endTime: fmtTime(r.ts),
        startTs,
        endTs,
        debugUrl: `/api/drop_rows?sdr_id=${encodeURIComponent(r.sdr_id)}&freq=${encodeURIComponent(Number(r.freq))}&start_ts=${Math.floor(startTs)}&end_ts=${Math.ceil(endTs)}`,
      };
    });
  }, [rawDrops]);

  useEffect(() => {
    if (!selectedDrop) {
      setScanRows([]);
      setScanError(null);
      return;
    }
    let cancelled = false;
    const loadRows = async () => {
      setScanLoading(true);
      setScanError(null);
      try {
        const start = Math.floor(selectedDrop.startTs);
        const end = Math.ceil(selectedDrop.endTs);
        const url = `/api/drop_rows?sdr_id=${encodeURIComponent(selectedDrop.sdrId)}&freq=${encodeURIComponent(selectedDrop.rawFreq)}&start_ts=${start}&end_ts=${end}`;
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`${res.status}: ${text || 'Failed to load rows'}`);
        }
        const rows = await res.json();
        if (!cancelled) setScanRows(rows);
      } catch (e) {
        if (!cancelled) setScanError(e.message);
      } finally {
        if (!cancelled) setScanLoading(false);
      }
    };
    loadRows();
    return () => { cancelled = true; };
  }, [selectedDrop]);

  useEffect(() => {
    const el = pieColRef.current;
    if (!active || !el) return;
    const ro = new ResizeObserver(entries => {
      const w = entries[0].contentRect.width;
      const h = entries[0].contentRect.height;
      const minDim = Math.min(w, h);
      setPieSize(Math.max(400, Math.floor(minDim * 0.92)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [active]);

  const filteredDrops = useMemo(() => {
    if (filterMode === 'known') return drops.filter(d => d.durationSec >= 180 && d.isKnown);
    return drops.filter(d => d.durationSec >= 180 && d.isMain);
  }, [drops, filterMode]);

  const displayedDrops = useMemo(() => filteredDrops.slice(0, visibleCount), [filteredDrops, visibleCount]);

  useEffect(() => { setVisibleCount(50); }, [filterMode, drops]);

  useEffect(() => {
    setPieLevel('region');
    setPieSdr(null);
    setPieRtys(null);
  }, [filterMode, selectedDrop]);

  const { pieSlices, pieTitle } = useMemo(() => {
    if (pieLevel === 'rtys' && pieSdr) {
      const sdr = SDR_DATA[pieSdr] || { label: pieSdr, color: CYAN };
      const counts = {};
      filteredDrops.filter(d => d.sdrKey === pieSdr).forEach(d => {
        const key = d.rtys || '—';
        counts[key] = (counts[key] || 0) + 1;
      });
      return {
        pieTitle: `${sdr.label} — RTYS Drops`,
        pieSlices: Object.entries(counts).map(([name, value], i) => ({
          id: name, label: name, value,
          color: PIE_COLORS[i % PIE_COLORS.length],
        })),
      };
    }
    if (pieLevel === 'station' && pieSdr && pieRtys) {
      const sdr = SDR_DATA[pieSdr] || { label: pieSdr };
      const counts = {};
      filteredDrops.filter(d => d.sdrKey === pieSdr && d.rtys === pieRtys).forEach(d => {
        const key = d.name || 'Unknown';
        counts[key] = (counts[key] || 0) + 1;
      });
      return {
        pieTitle: `${sdr.label} / ${pieRtys} — Station Drops`,
        pieSlices: Object.entries(counts).map(([name, value], i) => ({
          id: name, label: name, value,
          color: PIE_COLORS[i % PIE_COLORS.length],
        })),
      };
    }
    const counts = {};
    filteredDrops.forEach(d => {
      const key = d.sdrKey || d.sdrId || 'Unknown';
      counts[key] = (counts[key] || 0) + 1;
    });
    return {
      pieTitle: 'All Regions — Drop Distribution',
      pieSlices: Object.entries(counts).map(([key, value]) => {
        const sdr = SDR_DATA[key];
        return {
          id: key, label: sdr?.label || key, value,
          color: sdr?.color || PIE_COLORS[0],
        };
      }),
    };
  }, [filteredDrops, pieLevel, pieSdr, pieRtys]);

  const handlePieSelect = useCallback((slice) => {
    if (!slice) return;
    if (pieLevel === 'region') {
      setPieSdr(slice.id);
      setPieLevel('rtys');
    } else if (pieLevel === 'rtys') {
      setPieRtys(slice.id);
      setPieLevel('station');
    }
  }, [pieLevel]);

  const handlePieBack = useCallback(() => {
    if (pieLevel === 'station') {
      setPieRtys(null);
      setPieLevel('rtys');
    } else if (pieLevel === 'rtys') {
      setPieSdr(null);
      setPieLevel('region');
    }
  }, [pieLevel]);

  const displayPower = useCallback((raw) => {
    if (unit === 'dbm') return raw.toFixed(1);
    return (raw + 107).toFixed(1);
  }, [unit]);
  const unitLabel = unit === 'dbm' ? 'dBm' : 'dBµV';

  const handleBack = useCallback(() => setSelectedDrop(null), []);

  const exportDropHistoryCsv = useCallback(() => {
    if (!filteredDrops.length) return;
    const header = ['SDR','RTYS','Station','Frequency (MHz)','Start','End','Duration','Power (dBm)','Floor','SNR'];
    const lines = [header];
    filteredDrops.forEach(d => {
      lines.push([
        d.sdrId,
        d.rtys,
        d.name,
        d.freq,
        d.startTime,
        d.endTime,
        formatDuration(d.durationSec),
        displayPower(d.rawDbfs),
        d.floorDbfs,
        d.snrDb,
      ]);
    });
    const csv = lines.map(row => row.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `drop_history_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [filteredDrops, displayPower]);

  const handleDropClick = useCallback((d) => {
    setSelectedDrop(prev => {
      if (prev &&
        prev.id === d.id &&
        prev.freq === d.freq &&
        prev.sdrId === d.sdrId &&
        prev.startTime === d.startTime) return null;
      return d;
    });
  }, []);

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
      <div style={{ width: 150, flexShrink: 0, borderRight: '1px solid rgba(255,255,255,0.04)', overflow: 'hidden' }}>
        <SdrStatusMini tele={tele} rows={rows} />
      </div>
      <div style={{ width: 320, flexShrink: 0, overflow: 'hidden' }}>
        <SdrHistoryExpanded rows={rows} newIds={newIds} />
      </div>
      <div className="ov-scrollable" style={{ flex: 1, minWidth: 360, padding: '14px 16px', borderRight: '1px solid rgba(0,229,255,0.08)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
          <span />
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <FilterButton mode="known" label="All" filterMode={filterMode} setFilterMode={setFilterMode} />
            <FilterButton mode="main" label="Main" filterMode={filterMode} setFilterMode={setFilterMode} />
            <div style={{ width: 1, height: 14, background: 'rgba(255,255,255,0.1)', margin: '0 3px' }} />
            <UnitButton mode="dbm" label="dBm" unit={unit} setUnit={setUnit} />
            <UnitButton mode="dbuv" label="dBµV" unit={unit} setUnit={setUnit} />
            <button
              onClick={exportDropHistoryCsv}
              disabled={!filteredDrops.length}
              style={{
                fontFamily: SYS, fontSize: 10, fontWeight: 600, color: filteredDrops.length ? CYAN : 'rgba(0,229,255,0.35)',
                background: 'rgba(0,229,255,0.08)', border: '1px solid rgba(0,229,255,0.25)',
                borderRadius: 6, padding: '4px 10px', cursor: filteredDrops.length ? 'pointer' : 'default',
              }}
            >{t('sigint.export')}</button>
            <span style={{ fontFamily: MONO, fontSize: 10, color: CYAN, marginLeft: 6 }}>{filteredDrops.length}</span>
          </div>
        </div>
        {error ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#ff6b6b', fontFamily: SYS, fontSize: 12 }}>{error}</div>
        ) : loading && drops.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: 'rgba(255,255,255,0.3)', fontFamily: SYS, fontSize: 13 }}>
            <Loader2 className="ov-spin" size={28} color="rgba(0,229,255,0.8)" style={{ margin: '0 auto 12px', display: 'block' }} />
            Loading…
          </div>
        ) : filteredDrops.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: 'rgba(255,255,255,0.3)', fontFamily: SYS, fontSize: 13 }}>No sustained drops found</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {displayedDrops.map(d => (
              <DropHistoryRow
                key={d.id}
                d={d}
                selectedDrop={selectedDrop}
                onClick={handleDropClick}
                displayPower={displayPower}
                unitLabel={unitLabel}
              />
            ))}
            {displayedDrops.length < filteredDrops.length && (
              <button
                onClick={() => setVisibleCount(c => Math.min(c + 50, filteredDrops.length))}
                style={{
                  margin: '8px 0 4px',
                  padding: '10px 16px',
                  borderRadius: 8,
                  border: '1px solid rgba(0,229,255,0.35)',
                  background: 'rgba(0,229,255,0.08)',
                  color: CYAN,
                  fontFamily: SYS,
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: 'pointer',
                  alignSelf: 'center',
                }}
              >
                Show more
              </button>
            )}
          </div>
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <DropHistoryRightPanel
          pieColRef={pieColRef}
          filteredDrops={filteredDrops}
          pieSlices={pieSlices}
          pieSize={pieSize}
          selectedDrop={selectedDrop}
          scanLoading={scanLoading}
          scanError={scanError}
          scanRows={scanRows}
          onBack={handleBack}
          unitLabel={unitLabel}
          pieLevel={pieLevel}
          pieTitle={pieTitle}
          onPieSelect={handlePieSelect}
          onPieBack={handlePieBack}
        />
      </div>
    </div>
  );
}, (prev, next) => {
  return prev.active === next.active &&
         prev.sdrPeaksRef === next.sdrPeaksRef &&
         shallowEq(prev.tele, next.tele);
});

function SimplifiedTab({ sdrPeaksRef, active }) {
  // Default to the region that currently feeds live data (site_1 -> gunasli)
  // instead of the alphabetically-first Beyləqan, which has no active telemetry.
  const [selectedSdr, setSelectedSdr] = useState('gunasli');
  const [selectedRtys, setSelectedRtys] = useState(ALL_RTYS);
  const [timeInterval, setTimeInterval] = useState('24h');
  const { user } = useAuth();
  const thresholdStorageKey = user ? `sdr-simplified-threshold-${user.username}` : null;
  const [threshold, setThreshold] = useState(-60);
  const [unit, setUnit] = useState('dbm');
  const [sortBy, setSortBy] = useState('default');
  const [telemetry, setTelemetry] = useState({});

  // Load saved threshold for the current user
  useEffect(() => {
    if (!thresholdStorageKey) return;
    const saved = localStorage.getItem(thresholdStorageKey);
    if (saved == null) return;
    const parsed = Number(saved);
    if (Number.isFinite(parsed)) setThreshold(parsed);
  }, [thresholdStorageKey]);

  // Persist threshold changes
  useEffect(() => {
    if (!thresholdStorageKey) return;
    localStorage.setItem(thresholdStorageKey, String(threshold));
  }, [thresholdStorageKey, threshold]);

  const displayVal = useCallback((v) => unit === 'dbm' ? v : v + 107, [unit]);
  const unitLabel = unit === 'dbm' ? 'dBm' : 'dBµV';
  const thresholdDisplay = displayVal(threshold);
  const telemetryRef = useRef({});
  const loadedTelemetryScopesRef = useRef(new Set());
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [viewport, setViewport] = useState({ width: 960, height: 620 });
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const isRtysFocused = selectedRtys !== ALL_RTYS;
  const chartRef = useRef(null);
  const dragRef = useRef(null);

  const rtysEntries = useMemo(() => Object.entries(SDR_DATA[selectedSdr].rtys).filter(([rtys]) => selectedRtys === ALL_RTYS || rtys === selectedRtys), [selectedSdr, selectedRtys]);
  const stations = useMemo(() => rtysEntries.flatMap(([rtys, entries]) => entries.map(entry => ({ ...entry, sdr: selectedSdr, rtys }))), [rtysEntries, selectedSdr]);

  useEffect(() => {
    setSelectedRtys(ALL_RTYS);
    setIsInitialLoading(true);
  }, [selectedSdr]);

  useEffect(() => {
    if (!chartRef.current) return undefined;
    const observer = new ResizeObserver(([entry]) => setViewport({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(chartRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    telemetryRef.current = telemetry;
  }, [telemetry]);


  useEffect(() => {
    let cancelled = false;
    const sync = async () => {
      const live = sdrPeaksRef?.current || {};
      const next = {};
      stations.forEach(station => {
        const sdrId = SDR_API_IDS[station.sdr];
        const match = findFrequencyMatch(live[sdrId]?.peaks, station.freq);
        if (match) {
          const key = telemetryKey(sdrId, station.freq);
          const previous = telemetryRef.current[key];
          next[key] = { min: Math.min(previous?.min ?? match.dbfs, match.dbfs), peak: Math.max(previous?.peak ?? match.dbfs, match.dbfs) };
        }
      });
      if (Object.keys(next).length && !cancelled) {
        setTelemetry(previous => ({ ...previous, ...next }));
      }
      const scopeKey = `${selectedSdr}|${selectedRtys}|${timeInterval}`;
      const needsHistoricalLoad = !loadedTelemetryScopesRef.current.has(scopeKey);
      const missingStations = stations.filter(station => {
        const key = telemetryKey(SDR_API_IDS[station.sdr], station.freq);
        return needsHistoricalLoad || (!next[key] && !telemetryRef.current[key]);
      });
      if (!missingStations.length) return;
      const grouped = missingStations.reduce((result, station) => {
        const sdrId = SDR_API_IDS[station.sdr];
        if (!result[sdrId]) result[sdrId] = [];
        result[sdrId].push(station);
        return result;
      }, {});
      try {
        const responses = await Promise.all(Object.entries(grouped).map(async ([sdrId, scopedStations]) => {
          const params = new URLSearchParams({ freqs: scopedStations.map(station => station.freq.toFixed(3)).join(','), window: timeInterval });
          const cacheKey = `/api/sdr/${encodeURIComponent(sdrId)}/telemetry?${params.toString()}`;
          const cached = _getCached(cacheKey, 30000);
          if (cached) return { sdrId, stations: scopedStations, data: cached };
          const response = await fetch(`/api/sdr/${encodeURIComponent(sdrId)}/telemetry?${params}`, { credentials: 'include' });
          if (!response.ok) return null;
          const data = await response.json();
          _setCached(cacheKey, data);
          return { sdrId, stations: scopedStations, data };
        }));
        if (cancelled) return;
        loadedTelemetryScopesRef.current.add(scopeKey);
        const fetched = {};
        responses.filter(Boolean).forEach(({ sdrId, stations: scopedStations, data }) => scopedStations.forEach(station => {
          const signal = findFrequencyMatch(data.signals, station.freq);
          if (signal) fetched[telemetryKey(sdrId, station.freq)] = { min: signal.min, peak: signal.peak };
        }));
        if (Object.keys(fetched).length) setTelemetry(previous => ({ ...previous, ...fetched }));
      } catch { }
    };
    sync().finally(() => { if (!cancelled) setIsInitialLoading(false); });
    const interval = window.setInterval(sync, 15000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [stations, selectedSdr, selectedRtys, sdrPeaksRef, timeInterval]);

  const readings = useMemo(() => stations.map(station => {
    const value = telemetry[telemetryKey(SDR_API_IDS[station.sdr], station.freq)];
    return { ...station, min: value?.min, peak: value?.peak };
  }), [stations, telemetry]);
  const sortedReadings = useMemo(() => {
    if (sortBy === 'default') return readings;
    // Group by RTYS preserving the original RTYS order, then sort inside each group.
    const groups = [];
    const groupMap = {};
    readings.forEach(r => {
      if (!groupMap[r.rtys]) {
        groupMap[r.rtys] = [];
        groups.push(groupMap[r.rtys]);
      }
      groupMap[r.rtys].push(r);
    });
    const sorter = (a, b) => {
      switch (sortBy) {
        case 'freq': return a.freq - b.freq;
        case 'peak': return (b.peak ?? -Infinity) - (a.peak ?? -Infinity);
        case 'min': return (b.min ?? -Infinity) - (a.min ?? -Infinity);
        case 'name': return a.name.localeCompare(b.name);
        default: return 0;
      }
    };
    return groups.flatMap(group => [...group].sort(sorter));
  }, [readings, sortBy]);
  const readingsWithData = useMemo(() => sortedReadings.filter(station => station.min != null && station.peak != null), [sortedReadings]);

  const rtysGroups = useMemo(() => {
    const groups = [];
    let current = null;
    sortedReadings.forEach((reading, index) => {
      if (!current || current.name !== reading.rtys) {
        current = { name: reading.rtys, start: index, count: 0 };
        groups.push(current);
      }
      current.count++;
    });
    return groups;
  }, [sortedReadings]);

  const domain = useMemo(() => {
    if (!readingsWithData.length) return unit === 'dbm' ? { low: -120, high: 0 } : { low: -13, high: 107 };
    const values = readingsWithData.flatMap(reading => [reading.min, reading.peak]).map(displayVal);
    return { low: Math.floor(Math.min(...values) / 5) * 5 - 5, high: Math.ceil(Math.max(...values) / 5) * 5 + 5 };
  }, [readingsWithData, unit, displayVal]);
  const graphWidth = Math.max(360, stations.length * 86 + 128);
  const graphHeight = Math.max(360, viewport.height);
  const gridTicks = useMemo(() => {
    const ticks = [];
    for (let value = Math.ceil(domain.low / 2) * 2; value <= domain.high; value += 2) ticks.push(value);
    return ticks;
  }, [domain]);
  const chartTop = 64;
  const chartBottom = 112;
  const usableHeight = graphHeight - chartTop - chartBottom;
  const toY = value => chartTop + ((domain.high - value) / Math.max(1, domain.high - domain.low)) * usableHeight;
  const baseline = toY(domain.low);
  const thresholdY = Math.max(chartTop + 8, Math.min(baseline - 8, toY(displayVal(threshold))));
  const showValueLabels = zoom >= 0.85;
  const viewWidth = isRtysFocused ? graphWidth : Math.min(graphWidth, viewport.width / zoom);
  const viewHeight = isRtysFocused ? graphHeight : Math.min(graphHeight, viewport.height / zoom);
  const clampPan = useCallback((next) => ({ x: Math.max(0, Math.min(graphWidth - viewWidth, next.x)), y: Math.max(0, Math.min(graphHeight - viewHeight, next.y)) }), [graphHeight, graphWidth, viewHeight, viewWidth]);
  const setZoomAt = useCallback((targetZoom, anchorX = viewport.width / 2, anchorY = viewport.height / 2) => {
    const nextZoom = Math.max(0.1, Math.min(4, Number(targetZoom.toFixed(2))));
    const nextViewWidth = Math.min(graphWidth, viewport.width / nextZoom);
    const nextViewHeight = Math.min(graphHeight, viewport.height / nextZoom);
    const anchorGraphX = pan.x + (anchorX / viewport.width) * viewWidth;
    const anchorGraphY = pan.y + (anchorY / viewport.height) * viewHeight;
    setZoom(nextZoom);
    setPan({
      x: Math.max(0, Math.min(graphWidth - nextViewWidth, anchorGraphX - (anchorX / viewport.width) * nextViewWidth)),
      y: Math.max(0, Math.min(graphHeight - nextViewHeight, anchorGraphY - (anchorY / viewport.height) * nextViewHeight)),
    });
  }, [graphHeight, graphWidth, pan, viewHeight, viewWidth, viewport]);
  const adjustZoom = amount => setZoomAt(zoom + amount);
  const fitAllFrequencies = () => {
    setZoomAt(Math.min(1, viewport.width / graphWidth), 0, 0);
  };
  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }); };
  const handlePointerDown = event => {
    dragRef.current = { x: event.clientX, y: event.clientY, pan };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const handlePointerMove = event => {
    if (!dragRef.current) return;
    const next = { x: dragRef.current.pan.x - (event.clientX - dragRef.current.x) / zoom, y: dragRef.current.pan.y - (event.clientY - dragRef.current.y) / zoom };
    setPan(clampPan(next));
  };
  const handlePointerUp = () => { dragRef.current = null; };

  useEffect(() => {
    setPan(current => clampPan(current));
  }, [clampPan]);

  const buttonStyle = active => ({ padding: '7px 11px', borderRadius: 7, border: `1px solid ${active ? 'rgba(0,229,255,0.7)' : 'rgba(255,255,255,0.12)'}`, background: active ? 'rgba(0,229,255,0.16)' : 'rgba(255,255,255,0.04)', color: active ? CYAN : 'rgba(255,255,255,0.62)', font: `600 10px ${SYS}`, cursor: 'pointer', whiteSpace: 'nowrap' });

  return (
    <div style={{ height: 'calc(100vh - 128px)', display: 'flex', flexDirection: 'column', background: '#f7f9fa', color: '#0b1116', overflow: 'hidden' }}>
      <div style={{ padding: '10px 20px', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, background: '#0a111a', borderBottom: '1px solid rgba(0,229,255,0.25)', flexShrink: 0 }}>
        <span style={{ color: 'rgba(255,255,255,0.4)', font: `700 9px ${SYS}`, letterSpacing: '0.14em' }}>REGION</span>
        {SDR_IDS.map(sdr => <button key={sdr} onClick={() => { setSelectedSdr(sdr); resetView(); }} style={{ ...buttonStyle(selectedSdr === sdr), color: selectedSdr === sdr ? SDR_DATA[sdr].color : 'rgba(255,255,255,0.62)' }}>{SDR_DATA[sdr].label.replace(' SDR', '')}</button>)}
      </div>
      <div style={{ padding: '11px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, background: 'linear-gradient(180deg, #ffffff, #f4f7f9)', borderBottom: '1px solid #dbe3e8', flexShrink: 0 }}>
        <div><div style={{ font: `700 18px ${SYS}`, color: '#14212b' }}>{SDR_DATA[selectedSdr].label}</div><div style={{ marginTop: 2, color: '#75838d', font: `600 9px ${SYS}`, letterSpacing: '0.11em' }}>SIGNAL RANGE VIEW</div></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 3, color: '#71808b', font: `700 8px ${SYS}`, letterSpacing: '0.1em' }}>SORT<select aria-label="Sort by" value={sortBy} onChange={event => setSortBy(event.target.value)} style={{ padding: '7px 9px', border: '1px solid #ccd7de', borderRadius: 7, color: '#263640', background: '#fff', font: `600 11px ${SYS}` }}><option value="default">RTYS</option><option value="freq">Frequency</option><option value="peak">Peak</option><option value="min">Minimum</option><option value="name">Name</option></select></label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 3, color: '#71808b', font: `700 8px ${SYS}`, letterSpacing: '0.1em' }}>RTYS<select aria-label="RTYS" value={selectedRtys} onChange={event => { const value = event.target.value; setSelectedRtys(value); setZoom(1); setPan({ x: 0, y: 0 }); }} style={{ padding: '7px 9px', border: '1px solid #ccd7de', borderRadius: 7, color: '#263640', background: '#fff', font: `600 11px ${SYS}`, maxWidth: 190 }}><option value={ALL_RTYS}>All RTYS</option>{Object.keys(SDR_DATA[selectedSdr].rtys).map(rtys => <option key={rtys} value={rtys}>{rtys}</option>)}</select></label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 3, color: '#71808b', font: `700 8px ${SYS}`, letterSpacing: '0.1em' }}>INTERVAL<select aria-label="Time interval" value={timeInterval} onChange={event => setTimeInterval(event.target.value)} style={{ padding: '7px 9px', border: '1px solid #ccd7de', borderRadius: 7, color: '#263640', background: '#fff', font: `600 11px ${SYS}` }}>{TIME_INTERVALS.map(interval => <option key={interval.id} value={interval.id}>{interval.label}</option>)}</select></label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 3, color: '#71808b', font: `700 8px ${SYS}`, letterSpacing: '0.1em' }}>RED LINE<input aria-label="Red line threshold" type="number" step="1" value={thresholdDisplay} onChange={event => setThreshold(unit === 'dbm' ? (Number(event.target.value) || 0) : (Number(event.target.value) || 0) - 107)} style={{ width: 62, padding: '7px 9px', border: '1px solid #f1aaaa', borderRadius: 7, color: '#b82020', background: '#fff', font: `700 11px ${MONO}` }} /></label>
          <UnitToggle unit={unit} onChange={setUnit} />
          {isRtysFocused ? <div style={{ paddingTop: 12, color: '#54656f', font: `700 10px ${MONO}` }}>100%</div> : <div style={{ display: 'flex', alignItems: 'center', gap: 4, paddingTop: 12 }}><button onClick={fitAllFrequencies} style={buttonStyle(false)}>Fit all</button><button aria-label="Zoom out" onClick={() => adjustZoom(-0.2)} style={buttonStyle(false)}><ZoomOut size={14} /></button><span style={{ minWidth: 40, textAlign: 'center', color: '#354452', font: `700 11px ${MONO}` }}>{Math.round(zoom * 100)}%</span><button aria-label="Zoom in" onClick={() => adjustZoom(0.2)} style={buttonStyle(false)}><ZoomIn size={14} /></button><button onClick={resetView} style={buttonStyle(false)}><RotateCcw size={13} /></button></div>}
        </div>
      </div>
      <div ref={chartRef} onWheel={event => { event.preventDefault(); if (isRtysFocused) return; const bounds = event.currentTarget.getBoundingClientRect(); setZoomAt(zoom + (event.deltaY < 0 ? 0.15 : -0.15), event.clientX - bounds.left, event.clientY - bounds.top); }} onPointerDown={event => { if (!isRtysFocused) handlePointerDown(event); }} onPointerMove={event => { if (!isRtysFocused) handlePointerMove(event); }} onPointerUp={handlePointerUp} onPointerCancel={handlePointerUp} style={{ flex: 1, overflow: 'hidden', cursor: isRtysFocused ? 'default' : (dragRef.current ? 'grabbing' : 'grab'), touchAction: 'none', background: '#fff' }}>
        {readingsWithData.length ? <svg width="100%" height="100%" viewBox={`${pan.x} ${pan.y} ${viewWidth} ${viewHeight}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }}>
          {gridTicks.map(value => { const y = toY(value); return <g key={value}><line x1="64" x2={graphWidth - 24} y1={y} y2={y} stroke="#dbe2e7" strokeWidth="0.75" /><text x="55" y={y + 4} textAnchor="end" fill="#52616b" fontSize="9" fontFamily={MONO}>{value}</text></g>; })}
          <line x1="64" x2={graphWidth - 24} y1={baseline} y2={baseline} stroke="#7d8991" strokeWidth="1.5" />
          <line x1="64" x2={graphWidth - 24} y1={thresholdY} y2={thresholdY} stroke="#e12626" strokeWidth="2" strokeDasharray="7 4" />
          <text x={graphWidth - 26} y={thresholdY - 5} textAnchor="end" fill="#c41212" fontWeight="700" fontSize="10" fontFamily={MONO}>{thresholdDisplay} {unitLabel}</text>
          {rtysGroups.map((group, index) => { const startX = 64 + group.start * 86; const endX = 64 + (group.start + group.count) * 86; return <g key={group.name}>{index > 0 && <line x1={startX} x2={startX} y1={chartTop} y2={graphHeight - 26} stroke="#526671" strokeWidth="2.5" />}<line x1={startX} x2={endX} y1={graphHeight - 38} y2={graphHeight - 38} stroke="#9cabb5" strokeWidth="1" /><text x={(startX + endX) / 2} y={graphHeight - 14} textAnchor="middle" fill="#31444f" fontWeight="700" fontSize="13" fontFamily={SYS}>{group.name}</text></g>; })}
          {sortedReadings.map((reading, index) => { const x = 76 + index * 86; if (reading.min == null || reading.peak == null) return <g key={`${reading.rtys}-${reading.freq}-${index}`}>{showValueLabels && <text x={x + 32} y={baseline + 18} textAnchor="middle" fill="#89949b" fontSize="10" fontFamily={SYS}>{reading.name}</text>}<text x={x + 32} y={baseline + 32} textAnchor="middle" fill="#72808a" fontSize="9" fontFamily={MONO}>{reading.freq.toFixed(1)} MHz</text></g>; const peakY = toY(displayVal(reading.peak)); const minY = toY(displayVal(reading.min)); return <g key={`${reading.sdr}-${reading.rtys}-${reading.freq}-${index}`}><rect x={x} y={peakY} width="31" height={Math.max(1, baseline - peakY)} fill="#39c90a" /><rect x={x + 34} y={minY} width="31" height={Math.max(1, baseline - minY)} fill="#f10b0b" /><text x={x + 15} y={Math.max(18, peakY - 8)} textAnchor="middle" fill="#148000" fontWeight="700" fontSize="10" fontFamily={MONO}>{showValueLabels ? displayVal(reading.peak).toFixed(1) : ''}</text><text x={x + 49} y={Math.max(18, minY - 8)} textAnchor="middle" fill="#c10000" fontWeight="700" fontSize="10" fontFamily={MONO}>{showValueLabels ? displayVal(reading.min).toFixed(1) : ''}</text>{showValueLabels && <text x={x + 32} y={baseline + 18} textAnchor="middle" fill="#15232c" fontWeight="700" fontSize="10" fontFamily={SYS}>{reading.name}</text>}<text x={x + 32} y={baseline + 32} textAnchor="middle" fill="#72808a" fontSize="9" fontFamily={MONO}>{reading.freq.toFixed(1)} MHz</text></g>; })}
        </svg> : (
          <div style={{ height: '100%', display: 'grid', placeItems: 'center', padding: '0 24px', textAlign: 'center' }}>
            <div>
              <div style={{ color: '#65727b', font: `600 14px ${SYS}`, marginBottom: 6 }}>
                {isInitialLoading ? 'Waiting for station telemetry…' : `No telemetry data for ${SDR_DATA[selectedSdr].label}.`}
              </div>
              {!isInitialLoading && (
                <div style={{ color: '#8a96a0', font: `400 12px ${SYS}` }}>
                  Try selecting a different region above, or check that the SDR collector is running.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main OverviewDashboard ─────────────────────────────────────────────────────
const SUB_TABS = [
  { id: 'stations', label: 'Stations' },
  { id: 'simple',   label: 'Simplified' },
  { id: 'rtys',     label: 'RTYS' },
  { id: 'drops',    label: 'Drop History' },
];

const shallowEq = (a, b) => {
  if (a === b) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || !a || !b) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) if (a[k] !== b[k]) return false;
  return true;
};

export default memo(function OverviewDashboard({ sdrPeaksRef, tele, active }) {
  const [subTab, setSubTab] = useState('simple');
  const [drops, setDrops] = useState([]);
  const [dropsLoading, setDropsLoading] = useState(true);
  const [dropsError, setDropsError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    if (!active) return () => { };
    const cacheKey = '/api/drops?hours=24&use_calibrated=1';
    const cached = _getCached(cacheKey, 10000);
    if (cached) {
      setDrops(cached);
      setDropsLoading(false);
    }
    const load = async () => {
      if (!active || cancelled) return;
      if (_getCached(cacheKey, 10000)) return;
      try {
        const res = await fetch('/api/drops?hours=24&use_calibrated=1', { credentials: 'include' });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`${res.status}: ${text || 'Failed to load drops'}`);
        }
        const events = await res.json();
        if (!cancelled) {
          _setCached(cacheKey, events);
          setDrops(events);
          setDropsError(null);
        }
      } catch (e) {
        if (!cancelled) setDropsError(e.message);
      } finally {
        if (!cancelled) setDropsLoading(false);
        }
    };
    load();
    const id = setInterval(load, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, [active]);

  const refreshDrops = useCallback(async () => {
    const cacheKey = '/api/drops?hours=24&use_calibrated=1';
    try {
      setDropsLoading(true);
      const res = await fetch('/api/drops?hours=24&use_calibrated=1', { credentials: 'include' });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status}: ${text || 'Failed to load drops'}`);
      }
      const events = await res.json();
      _setCached(cacheKey, events);
      setDrops(events);
      setDropsError(null);
    } catch (e) {
      setDropsError(e.message);
    } finally {
      setDropsLoading(false);
    }
  }, []);

  return (
    <motion.div
      key="overview"
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
      style={{
        position: 'fixed', top: 86, left: 0, right: 0, bottom: 0, zIndex: 50,
        background: 'radial-gradient(ellipse at 25% 15%, rgba(0,229,255,0.04) 0%, rgba(5,7,15,0.99) 60%)',
        display: 'flex', flexDirection: 'column',
        fontFamily: SYS, overflow: 'hidden',
      }}
    >
      {/* Sub-tab bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 2, padding: '0 20px',
        borderBottom: '1px solid rgba(0,229,255,0.09)',
        background: 'rgba(5,8,16,0.85)', flexShrink: 0, height: 42,
      }}>
        <span style={{ fontSize: 9, color: 'rgba(0,229,255,0.35)', letterSpacing: '0.16em', textTransform: 'uppercase', fontFamily: SYS, marginRight: 10 }}>
          Overview
        </span>
        {SUB_TABS.map(t => (
          <button
            key={t.id}
            className={`ov-tab-btn${subTab === t.id ? ' active' : ''}`}
            onClick={() => setSubTab(t.id)}
            style={{
              padding: '0 18px', height: 42, fontSize: 11, fontFamily: SYS, fontWeight: 600,
              cursor: 'pointer', background: 'transparent',
              border: 'none',
              borderBottom: subTab === t.id ? `2px solid ${CYAN}` : '2px solid transparent',
              color: subTab === t.id ? CYAN : 'rgba(255,255,255,0.4)',
              transition: 'all 0.15s ease',
              letterSpacing: '0.06em', textTransform: 'uppercase',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {active && subTab === 'stations' && <StationsTab sdrPeaksRef={sdrPeaksRef} />}
        {active && subTab === 'simple'   && <SimplifiedTab sdrPeaksRef={sdrPeaksRef} />}
        {active && subTab === 'rtys'     && <RtysTab />}
        <div style={{ display: (subTab === 'drops' && active) ? 'flex' : 'none', flex: 1, overflow: 'hidden', flexDirection: 'column' }}>
          <DropHistoryTab drops={drops} loading={dropsLoading} error={dropsError} refreshDrops={refreshDrops} tele={tele} active={subTab === 'drops' && active} />
        </div>
      </div>
    </motion.div>
  );
});
