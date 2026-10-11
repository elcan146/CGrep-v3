import React, { useRef, useEffect, useState, useMemo, useCallback, memo, lazy, Suspense } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from './auth/AuthContext';
import { useTranslation } from './i18n/LanguageContext';
import tracker from './ActivityTracker';
import TextType from './components/TextType';
import notificationSound from './assets/info.mp3';
import { AnimatePresence, motion } from 'framer-motion';
const SigintDashboard    = lazy(() => import('./SigintDashboard'));
const AnalyticsDashboard = lazy(() => import('./AnalyticsDashboard'));
const AdminPanel         = lazy(() => import('./auth/AdminPanel'));
const ProfileModal       = lazy(() => import('./auth/ProfileModal'));
const RightPanel         = lazy(() => import('./RightPanel'));
import AsciiWave from '@/components/lightswind/ascii-wave';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { useTexture, useGLTF, Html } from '@react-three/drei';
import * as THREE from 'three';
import { LineSegments2 } from 'three/examples/jsm/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/examples/jsm/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js';
import regionMask from './region_mask.png';
import regionGeoJSON from './region.json';
import antennaGlb from './antenna.glb';
import mainPeakGlb from './mainpeak.glb';
import useSdrThresholds from './useSdrThresholds';

tracker.start();

// ---------------------------------------------------------------------------
// Tactical Design Tokens
// ---------------------------------------------------------------------------
const SYS   = `'Space Grotesk',sans-serif`;
const MONO  = `'JetBrains Mono',monospace`;
const DIGIT = `DSDigit,'JetBrains Mono',monospace`;

const CYAN   = '#00e5ff';
const AMBER  = '#ffaf00';
const ORANGE = '#ff5722';
const PURPLE = '#4a00e0';
const SLATE  = '#8fa0bc';
const VOID   = '#05070b';

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

const GLASS = {
  background: 'rgba(10,14,23,0.75)',
  border: '1px solid rgba(0,229,255,0.15)',
  borderRadius: 16,
  boxShadow: '0 8px 32px 0 rgba(0,0,0,0.5)',
  transform: 'translateZ(0)',
};

const STATUS_DOT = { ONLINE: '#00e5ff', WARNING: '#ffaf00', OFFLINE: '#ff5722' };

const CHAT_WS_BASE = window.location.port === '5173'
  ? 'ws://127.0.0.1:8000'
  : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;

const NOTIFICATION_SOUND = notificationSound;
const NOTIFICATION_VOLUME = 0.5;

let audioUnlocked = false;
let hasPendingSound = false;

function playNotificationSound() {
  try {
    if (!audioUnlocked) {
      hasPendingSound = true;
      console.log('[notif] sound queued until first click/key');
      return;
    }
    const audio = new Audio(NOTIFICATION_SOUND);
    audio.volume = NOTIFICATION_VOLUME;
    console.log('[notif] playing sound', NOTIFICATION_SOUND);
    audio.play().catch(err => console.warn('[notif] audio play failed', err));
  } catch (e) { console.warn('[notif] play error', e); }
}

// ---------------------------------------------------------------------------
// Global CSS — tactical glassmorphism + interference glitch
// ---------------------------------------------------------------------------
const _css = `
  * { box-sizing: border-box; }
  @keyframes ap-pulse {
    0%,100% { opacity:1; transform:scale(1); }
    50%     { opacity:0.5; transform:scale(1.4); }
  }
  @keyframes ap-fadein {
    from { opacity:0; transform:translateY(6px); }
    to   { opacity:1; transform:translateY(0); }
  }
  @keyframes ap-ticker {
    0% { transform: translateX(0); }
    100% { transform: translateX(-50%); }
  }
  @keyframes ap-slidein {
    from { opacity:0; transform:scale(0.94) translateY(12px); }
    to   { opacity:1; transform:scale(1)    translateY(0); }
  }
  @keyframes wx-slide-in-right {
    from { opacity:0; transform:translateX(22px); }
    to   { opacity:1; transform:translateX(0); }
  }
  @keyframes wx-slide-in-left {
    from { opacity:0; transform:translateX(-22px); }
    to   { opacity:1; transform:translateX(0); }
  }
  .wx-slide-right { animation: wx-slide-in-right 0.32s cubic-bezier(0.25,1,0.5,1) both; }
  .wx-slide-left  { animation: wx-slide-in-left  0.32s cubic-bezier(0.25,1,0.5,1) both; }
  @keyframes wx-bar {
    from { width:100%; }
    to   { width:0%; }
  }
  .ap-row {
    transition: background 0.18s ease;
    border-radius: 8px;
  }
  .ap-row:hover { background: rgba(0,229,255,0.055) !important; }
  .ap-panel-scroll::-webkit-scrollbar { width: 4px; }
  .ap-panel-scroll::-webkit-scrollbar-track { background: transparent; }
  .ap-panel-scroll::-webkit-scrollbar-thumb { background: #00e5ff; border-radius: 0; }
  .ap-panel-scroll { scrollbar-width: thin; scrollbar-color: #00e5ff transparent; }
  @keyframes map-tooltip-pop {
    0%   { opacity: 0; transform: translateX(-6px); }
    100% { opacity: 1; transform: translateX(0); }
  }
  .map-antenna-tooltip {
    display: inline-block;
    pointer-events: none;
    animation: map-tooltip-pop 0.22s ease-out both;
    text-align: center;
    background: #000000;
    padding: 4px 10px;
  }
  .map-antenna-tooltip .text-type__content {
    font-family: 'DSDigit', 'JetBrains Mono', monospace;
    font-size: 35px;
    font-weight: 400;
    color: #ffffff;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    white-space: nowrap;
    text-shadow: 0 1px 3px rgba(0,0,0,0.9);
  }
`;
if (typeof document !== 'undefined' && !document.getElementById('ap-css')) {
  const s = document.createElement('style');
  s.id = 'ap-css'; s.textContent = _css;
  document.head.appendChild(s);
}

// ---------------------------------------------------------------------------
// Data — 7 antenna transmission hubs (UV positions on Region map)
// ---------------------------------------------------------------------------
const ANTENNA_HUBS = [
  { id:1, name:'Site-1',     uvX:0.830, uvY:0.420 },
  { id:2, name:'Site-7',     uvX:0.630, uvY:0.240 },
  { id:3, name:'Site-6',   uvX:0.520, uvY:0.340 },
  { id:4, name:'Site-4',    uvX:0.290, uvY:0.400 },
  { id:5, name:'Site-8',     uvX:0.330, uvY:0.610 },
  { id:6, name:'Site-3', uvX:0.490, uvY:0.610 },
  { id:7, name:'Site-5',  uvX:0.650, uvY:0.770 },
];

// ---------------------------------------------------------------------------
// Data — 20 real Regioni FM radio stations (must match STATIONS in SigintDashboard.jsx)
// ---------------------------------------------------------------------------
const _st = (f,n,s='ONLINE') => ({freq:f, name:n, status:s});
const RADIO_STATIONS = [
  _st('88.7','Planet FM'),
  _st('90.0','İctimai Radio'),
  _st('90.7','Yurd FM'),
  _st('92.5','Dalğa FM'),
  _st('93.0','Sağlam Radio'),
  _st('93.3','Kəpəz FM / Site-1 Retro FM'),
  _st('97.7','Site-1 Hit FM'),
  _st('100.0','ASAN Radio'),
  _st('100.5','TMB Radio','WARNING'),
  _st('101.0','Radio Antenn'),
  _st('101.1','Enerji FM'),
  _st('102.0','ANS FM'),
  _st('102.8','CBC Radio'),
  _st('103.0','Xəzər FM'),
  _st('103.3','Araz FM'),
  _st('104.0','Radio Space'),
  _st('105.0','Azərbaycan Radiosu'),
  _st('106.0','106 FM'),
  _st('106.3','Azad Azərbaycan'),
  _st('107.0','Real Radio','OFFLINE'),
  _st('107.7','Avto FM'),
];

// ---------------------------------------------------------------------------
// Hub city lat/lon for Open-Meteo lookups
// ---------------------------------------------------------------------------
const HUB_GEO = {
  'Site-1':     { lat:40.37, lon:49.85 },
  'Site-7':     { lat:41.36, lon:48.52 },
  'Site-6':   { lat:40.98, lon:47.85 },
  'Site-4':    { lat:40.68, lon:46.36 },
  'Site-8':     { lat:39.75, lon:46.75 },
  'Site-3': { lat:39.77, lon:47.62 },
  'Site-5':  { lat:39.03, lon:48.67 },
};

const HUB_TO_SDR_ID = {
  'Site-1':     'site_1',
  'Site-7':     'site_7',
  'Site-6':   'site_6',
  'Site-4':    'site_4',
  'Site-8':     'site_8',
  'Site-3': 'site_3',
  'Site-5':  'site_5',
};

const WMO_DESC = {
  0:'Clear',1:'Mainly Clear',2:'Partly Cloudy',3:'Overcast',
  45:'Foggy',48:'Rime Fog',51:'Light Drizzle',53:'Drizzle',
  61:'Light Rain',63:'Rain',71:'Light Snow',73:'Snow',
  80:'Showers',95:'Thunderstorm',
};

// ---------------------------------------------------------------------------
// NodePingRings — 3 staggered concentric rings radiating from a hub node
// ---------------------------------------------------------------------------
function NodePingRings({ wx, wz, stagger = 0, color = '#00e5ff', isOffline = false }) {
  const rings = [useRef(), useRef(), useRef()];
  const isOfflineRef = useRef(isOffline);
  const colorRef = useRef(color);
  useEffect(() => { isOfflineRef.current = isOffline; colorRef.current = color; }, [isOffline, color]);

  useFrame(({ clock }) => {
    const t = clock.elapsedTime + stagger;
    rings.forEach((ref, i) => {
      if (!ref.current) return;
      const speed = isOfflineRef.current ? 0.9 : 0.45;
      const phase = ((t * speed + i * 0.33) % 1.0); // 0→1 loop, offset per ring
      const scale = 0.06 + phase * 0.32;            // expands 0.06 → 0.38
      const opacity = (1.0 - phase) * (1.0 - phase) * 0.75; // quad fade
      ref.current.scale.setScalar(scale);
      ref.current.material.opacity = opacity;
      ref.current.material.color.set(colorRef.current);
    });
  });

  return (
    <group position={[wx, 0.04, wz]}>
      {rings.map((ref, i) => (
        <mesh key={i} ref={ref} rotation={[-Math.PI / 2, 0, 0]} raycast={null}>
          <ringGeometry args={[0.85, 1.0, 24]} />
          <meshBasicMaterial color={color} transparent opacity={0} depthWrite={false} blending={2} />
        </mesh>
      ))}
    </group>
  );
}

// ---------------------------------------------------------------------------
// HubNode — glowing sphere + ping rings, staggered per hub id
// ---------------------------------------------------------------------------
function HubNode({ hub, sdrNodes }) {
  const meshRef  = useRef();
  const haloRef  = useRef();
  const [wx, wz] = uvToWorld(hub.uvX, hub.uvY);
  const stagger  = hub.id * 1.37; // golden-ratio-ish stagger so pings desync

  const sdrId     = HUB_TO_SDR_ID[hub.name];
  const isOffline = sdrId ? sdrNodes?.find(n => n.id === sdrId)?.online === false : false;
  const color     = isOffline ? '#ff2200' : '#00e5ff';
  const isOfflineRef = useRef(isOffline);
  useEffect(() => { isOfflineRef.current = isOffline; }, [isOffline]);

  useFrame(({ clock }) => {
    if (!meshRef.current) return;
    const t = clock.elapsedTime;
    const pulse = 0.5 + 0.5 * Math.sin((isOfflineRef.current ? 6 : 2.2) * t + hub.id);
    meshRef.current.material.opacity = 0.65 + 0.25 * pulse;
    if (haloRef.current) haloRef.current.material.opacity = 0.07 + 0.07 * pulse;
  });

  return (
    <group>
      <NodePingRings wx={wx} wz={wz} stagger={stagger} color={color} isOffline={isOffline} />
      <mesh ref={haloRef} position={[wx, 0.06, wz]} raycast={null}>
        <sphereGeometry args={[0.09, 8, 8]} />
        <meshBasicMaterial color={color} transparent opacity={0.08} depthWrite={false} />
      </mesh>
      <mesh ref={meshRef} position={[wx, 0.06, wz]} raycast={null}>
        <sphereGeometry args={[0.030, 8, 8]} />
        <meshBasicMaterial color={color} transparent opacity={0.85} />
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------------------
// HubMarkers — all hub nodes
// ---------------------------------------------------------------------------
function HubMarkers({ sdrNodes }) {
  return <>{ANTENNA_HUBS.map(h => <HubNode key={h.id} hub={h} sdrNodes={sdrNodes} />)}</>;
}

// ---------------------------------------------------------------------------
// Per-city color palette (retained for laser/shader effects)
// ---------------------------------------------------------------------------
const CITY_DATA = [
  { name: 'Site-7',     css: '#ff0000', gl: [1.0, 0.0, 0.0] },
  { name: 'Site-6',   css: '#ff0000', gl: [1.0, 0.0, 0.0] },
  { name: 'Site-4',    css: '#ff0000', gl: [1.0, 0.0, 0.0] },
  { name: 'Site-8',     css: '#ff0000', gl: [1.0, 0.0, 0.0] },
  { name: 'Site-3', css: '#ff0000', gl: [1.0, 0.0, 0.0] },
  { name: 'Site-5',  css: '#ff0000', gl: [1.0, 0.0, 0.0] },
];

// ---------------------------------------------------------------------------
// GLSL — Vertex Shader  [WD1 ctOS — clean]
// Dense city-block terracing, data-tower spikes, rare UV tear.
// ---------------------------------------------------------------------------
const vertexShader = `
uniform float uTime;
uniform float uPeakIntensity;
uniform float uNoiseScale;
uniform float uPeakRadius;
uniform vec2  uPeakPosition;
uniform float uHoverActive;
uniform vec2  uHoverPosition;
uniform float uHoverRadius;
uniform float uHoverHeight;
uniform sampler2D uAlphaMap;
uniform int   uCustomPeakCount;
uniform vec2  uCustomPeakPositions[10];
uniform float uCustomPeakRadii[10];
uniform float uCustomPeakHeights[10];
uniform float uLaserProgress[10];

varying float vHeight;
varying float vZ;
varying vec2  vUv;

float h21(vec2 p) {
  p = fract(p * vec2(127.1, 311.7));
  p += dot(p, p + 19.19);
  return fract(p.x * p.y);
}
float h11(float n) { return fract(sin(n) * 43758.5453); }

void main() {
  vUv = uv;
  vec3 pos = position;

  float edgeDist = min(min(vUv.x,1.0-vUv.x), min(vUv.y,1.0-vUv.y));
  float edgeFade = smoothstep(0.0, 0.08, edgeDist);

  // ── City-block terracing — tiles snap to discrete levels on a slow clock ───
  float gridSize = 32.0;
  vec2  cell     = floor(vUv * gridSize);
  float timeBkt  = floor(uTime * 0.5);           // level refresh every 2 s
  float tileH    = h21(cell + timeBkt * 0.61);
  float tileStep = floor(tileH * 6.0) / 6.0;    // 6 discrete height levels
  float baseZ    = tileStep * 0.018;

  // Gentle per-tile breathing
  float ph = h21(cell * 2.3) * 6.283;
  baseZ += sin(uTime * 0.6 + ph) * 0.004;

  // ── Rare single-tile data-spike: 1 tile fires every ~1 s ──────────────────
  float spikeBkt  = floor(uTime * 1.0);
  float spikeSeed = h11(spikeBkt * 5.13);
  vec2  spikeCell = floor(vec2(spikeSeed * 31.0, h11(spikeSeed*2.9)*29.0));
  float isSpike   = 1.0 - smoothstep(0.0, 1.2, length(cell - spikeCell));
  float spikeFrac = fract(uTime * 1.0);
  // Rise fast (0’0.2), hold (0.2’0.6), fall (0.6’1.0)
  float spikeH    = isSpike * smoothstep(0.0,0.2,spikeFrac) * (1.0-smoothstep(0.6,1.0,spikeFrac)) * 0.055;
  baseZ += spikeH * edgeFade;

  float z = 0.012 + baseZ;

  // Site-1 peak
  float distToPeak = distance(vUv, uPeakPosition);
  if (distToPeak < uPeakRadius) {
    float peakH  = 0.30 + 0.06*(0.5+0.5*sin(uTime*0.5));
    float needle = 1.0 - smoothstep(0.0, uPeakRadius * 0.30, distToPeak);
    z += peakH * needle * needle * edgeFade;
  }

  // Hover peak
  if (uHoverActive > 0.5) {
    float dh = distance(vUv, uHoverPosition);
    if (dh < uHoverRadius) {
      float f = 1.0 - smoothstep(0.0, uHoverRadius, dh);
      z += uHoverHeight * f * f * edgeFade;
    }
  }

  // Custom city peaks
  float peakBrightness = 0.0;
  for (int i = 0; i < 10; i++) {
    if (i >= uCustomPeakCount) break;
    float dc = distance(vUv, uCustomPeakPositions[i]);
    if (dc < uCustomPeakRadii[i]) {
      float f = 1.0 - smoothstep(0.0, uCustomPeakRadii[i], dc);
      z += uCustomPeakHeights[i] * f * f * edgeFade;
      peakBrightness += f * 0.4;
    }
  }
  if (distToPeak < uPeakRadius)
    peakBrightness += (1.0 - distToPeak/uPeakRadius)*0.5;

  pos.z = z;
  vZ = z;
  vHeight = z + peakBrightness;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
}
`;

// ---------------------------------------------------------------------------
// GLSL — Fragment Shader  [WD1 ctOS — clean]
// Strict navy/teal/cyan/orange palette. Dense grid. Data-refresh tiles.
// Subtle UV tear glitch. Dual scan rings. No green. No random flash.
// ---------------------------------------------------------------------------
const fragmentShader = `
uniform sampler2D uAlphaMap;
uniform vec3  uLaserColors[10];
uniform float uTime;
uniform int   uCustomPeakCount;
uniform vec2  uCustomPeakPositions[10];
uniform float uCustomPeakRadii[10];
uniform vec2  uPeakPosition;
uniform float uPeakRadius;
uniform float uLaserProgress[10];
uniform float uChargeProgress[10];
uniform float uImpactProgress[10];
uniform float uLaunchProgress[10];

varying float vHeight;
varying float vZ;
varying vec2  vUv;

float h21(vec2 p) {
  p = fract(p * vec2(127.1, 311.7));
  p += dot(p, p + 19.19);
  return fract(p.x * p.y);
}
float h11(float n) { return fract(sin(n) * 43758.5453); }


void main() {
  float mask = texture2D(uAlphaMap, vUv).r;
  if (mask < 0.5) { gl_FragColor = vec4(0.0); return; }

  // ── Strict WD1 palette — navy/teal/cyan/blue only ──────────────────────────
  vec3 navy    = vec3(0.008, 0.014, 0.038);   // near-black navy floor
  vec3 teal    = vec3(0.00,  0.76,  0.82);    // ctOS teal grid
  vec3 cyan    = vec3(0.55,  0.95,  1.00);    // bright cyan intersections
  vec3 cyanHot = vec3(0.20,  0.88,  1.00);    // hot cyan for Site-1 aura/towers
  vec3 white   = vec3(1.00,  1.00,  1.00);

  // ── Grid — major 32×32, minor 96×96 ───────────────────────────────────────
  float majN = 32.0;
  float minN = 96.0;
  vec2  majUv = fract(vUv * majN);
  vec2  minUv = fract(vUv * minN);

  // Per-row slow brightness variation (teal lines vary 0.5–1.0)
  float rowV = 0.50 + h11(floor(vUv.y * majN) * 0.71 + floor(uTime * 0.4)) * 0.50;

  float majW = 0.028;  // fine crisp lines
  float minW = 0.016;
  float majLine = max(
    1.0 - smoothstep(0.0, majW, min(majUv.x, 1.0-majUv.x)),
    1.0 - smoothstep(0.0, majW, min(majUv.y, 1.0-majUv.y))
  );
  float minLine = max(
    1.0 - smoothstep(0.0, minW, min(minUv.x, 1.0-minUv.x)),
    1.0 - smoothstep(0.0, minW, min(minUv.y, 1.0-minUv.y))
  );

  // Intersection dots: where both X and Y major lines cross, brighten to cyan
  float majX = 1.0 - smoothstep(0.0, majW, min(majUv.x, 1.0-majUv.x));
  float majY = 1.0 - smoothstep(0.0, majW, min(majUv.y, 1.0-majUv.y));
  float crossDot = majX * majY;   // bright only at exact crossings

  float gridLine = (majLine * 0.92 + minLine * 0.30) * rowV;

  // ── Subtle chromatic fringe on major lines only ───────────────────────────
  float fringe   = 0.0018;
  float majLineR = max(
    1.0 - smoothstep(0.0, majW, min(fract(vUv.x*majN+fringe), 1.0-fract(vUv.x*majN+fringe))),
    majY
  );
  float majLineB = max(
    1.0 - smoothstep(0.0, majW, min(fract(vUv.x*majN-fringe), 1.0-fract(vUv.x*majN-fringe))),
    majY
  );

  // ── Data-refresh tile: one tile every ~1 s flickers bright then fades ──────
  vec2  dCell = floor(vUv * majN);
  float dBkt  = floor(uTime * 1.0);
  float dSeed = h11(dBkt * 5.13);
  vec2  dTarget = floor(vec2(dSeed * 31.0, h11(dSeed*2.9)*29.0));
  float isDCell = 1.0 - smoothstep(0.0, 1.0, length(dCell - dTarget));
  float dFrac   = fract(uTime * 1.0);
  // Fast bright flash then slow fade — stays in teal, never white
  float dFlash  = isDCell * smoothstep(0.0,0.15,dFrac) * (1.0-smoothstep(0.2,0.8,dFrac)) * 0.60;

  // ── Dual ctOS scan rings from Site-1 ───────────────────────────────────────
  float dScan  = distance(vUv, uPeakPosition);
  float spd    = 0.13;
  float r1 = fract(uTime * spd);
  float r2 = fract(uTime * spd + 0.5);
  float sW = 0.005;
  float scan1 = (1.0 - smoothstep(0.0, sW, abs(dScan - r1*0.58))) * (1.0-r1) * (1.0-r1);
  float scan2 = (1.0 - smoothstep(0.0, sW, abs(dScan - r2*0.58))) * (1.0-r2) * (1.0-r2);
  float scanP = (scan1 + scan2) * 0.70;

  // ── Slow scrolling scanline dimming (very subtle) ─────────────────────────
  float scanDim = 0.92 + 0.08 * sin(vUv.y * 180.0 + uTime * 6.0);

  // ── Height ─────────────────────────────────────────────────────────────────
  float zNorm     = smoothstep(0.012, 0.26, vZ);
  float towerBlend = smoothstep(0.62, 1.0, zNorm);
  vec3  towerCol    = mix(teal, cyanHot, towerBlend * 0.95);
  towerCol = mix(towerCol, white, smoothstep(0.93, 1.0, zNorm) * 0.88);

  // ── Compose ───────────────────────────────────────────────────────────────
  vec3 color = navy;

  // Grid lines with chromatic fringe (teal body, faint cyan/blue edges)
  float gb = gridLine * (0.40 + scanP * 0.22);
  color.r = mix(color.r, teal.r * 0.80, (majLineR * 0.90 + minLine * 0.22) * rowV * (0.40 + scanP*0.22));
  color.g = mix(color.g, teal.g,        gb);
  color.b = mix(color.b, cyan.b,        (majLineB * 0.90 + minLine * 0.22) * rowV * (0.40 + scanP*0.22));

  // Intersection dots — brighter cyan node at every crossing
  color = mix(color, cyan, crossDot * 0.80);

  // Scan ring bloom — teal only
  color = mix(color, teal * 1.8, scanP * 0.55);

  // Data-refresh tile glow — cyan tint, no white
  color = mix(color, cyan * 1.4, dFlash);

  // Height towers
  color = mix(color, towerCol, zNorm * 0.88);

  // Subtle scanline dim
  color *= scanDim;

  // ── Alpha ─────────────────────────────────────────────────────────────────
  float alpha = mix(gb * 0.85, 0.88, zNorm);
  alpha = max(alpha, gridLine    * 0.55);
  alpha = max(alpha, scanP       * 0.50);
  alpha = max(alpha, crossDot    * 0.70);
  alpha = max(alpha, dFlash      * 0.60);
  // ── Site-1 cyan aura — tight radial glow, no long ray ──────────────────────
  float dAura = distance(vUv, uPeakPosition);
  // 1 at center, 0 at edge of radius — edge0 < edge1 required
  float aura  = 1.0 - smoothstep(0.0, uPeakRadius * 1.4, dAura);
  color = mix(color, cyanHot, aura * 0.62);
  alpha = max(alpha, aura * 0.78);

  // ── Projectile bolts ──────────────────────────────────────────────────────
  for (int i = 0; i < 10; i++) {
    if (i >= uCustomPeakCount) break;
    float prog = uLaserProgress[i];
    if (prog <= 0.0 || prog >= 1.0) continue;
    vec2  ls = uCustomPeakPositions[i];
    vec2  le = uPeakPosition;
    vec2  dir  = normalize(le - ls);
    vec2  perp = vec2(-dir.y, dir.x);
    vec2  ctr  = ls + (le - ls) * prog;
    vec2  rel  = vUv - ctr;
    float axial  = dot(rel, dir);
    float radial = dot(rel, perp);
    vec3  city   = uLaserColors[i];
    float boltLen = 0.014; float boltRad = 0.0025;
    float glowLen = 0.020; float glowRad = 0.006;
    float inBolt = step(abs(axial), boltLen) * (1.0 - smoothstep(0.0, boltRad, abs(radial)));
    float inGlow = step(abs(axial), glowLen) * (1.0 - smoothstep(0.0, glowRad, abs(radial)));
    float taper  = 1.0 - smoothstep(boltLen * 0.4, boltLen, abs(axial));
    color = mix(color, city * 8.0,  inGlow * 0.90);
    color = mix(color, city * 14.0, inBolt * taper * 0.95);
    color = mix(color, white, inBolt * taper * smoothstep(boltRad*0.5, 0.0, abs(radial)));
  }

  // ── Impact rings ─────────────────────────────────────────────────────────
  float dPeak = distance(vUv, uPeakPosition);
  for (int i = 0; i < 10; i++) {
    if (i >= uCustomPeakCount) break;
    float imp = uImpactProgress[i];
    if (imp < 0.01) continue;
    vec3  city = uLaserColors[i];
    float fade = (1.0-imp)*(1.0-imp);
    float r = imp*0.06; float w = 0.004;
    color = mix(color, mix(city,white,0.5), (1.0-smoothstep(0.0,w,abs(dPeak-r)))*fade*0.85);
  }

  // ── Launch rings ──────────────────────────────────────────────────────────
  for (int i = 0; i < 10; i++) {
    if (i >= uCustomPeakCount) break;
    float lnch = uLaunchProgress[i];
    if (lnch < 0.01) continue;
    vec3  city = uLaserColors[i];
    float fade = (1.0-lnch)*(1.0-lnch);
    float r = lnch*0.06; float w = 0.004;
    float dC = distance(vUv, uCustomPeakPositions[i]);
    color = mix(color, mix(city,white,0.5), (1.0-smoothstep(0.0,w,abs(dC-r)))*fade*0.85);
  }

  gl_FragColor = vec4(color, alpha);
}`;

// ---------------------------------------------------------------------------
// PopulationDots — renders OSM populated places projected onto the 3D map
// ---------------------------------------------------------------------------
const PopulationDots = memo(function PopulationDots() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    // ── Camera constants (match FixedCamera exactly) ─────────────────────────
    const FOV_HALF_TAN = Math.tan((35 * Math.PI / 180) / 2);
    const CAM_ANGLE = -20 * Math.PI / 180;
    const camX =  7.5 * Math.sin(CAM_ANGLE);
    const camY =  9.5;
    const camZ =  7.5 * Math.cos(CAM_ANGLE);

    // View basis (lookAt 0,0,0.2)
    function norm(x,y,z){ const l=Math.sqrt(x*x+y*y+z*z); return [x/l,y/l,z/l]; }
    const [zx,zy,zz] = norm(camX-0, camY-0, camZ-0.2);
    const [rx,ry,rz] = norm(zy*0-zz*1, zz*0-zx*0, zx*1-zy*0); // cross(up,z)
    // Corrected right axis: cross([0,1,0], zAxis)
    const r2 = norm(1*zz-0*zy, 0*zx-0*zz, 0*zy-1*zx);
    const [ux,uy,uz] = [zy*r2[2]-zz*r2[1], zz*r2[0]-zx*r2[2], zx*r2[1]-zy*r2[0]];

    // Geographic bounds
    const LON_MIN=44.8414, LON_MAX=50.6127, LAT_MIN=38.4425, LAT_MAX=41.8393;

    // Project lon/lat → NDC [nx, ny] or null if behind camera
    function projectLonLat(lon, lat) {
      const u = (lon-LON_MIN)/(LON_MAX-LON_MIN);
      const v = (lat-LAT_MIN)/(LAT_MAX-LAT_MIN);
      const wx=(u-0.5)*10, wy=0, wz=(v-0.5)*8;
      const dx=wx-camX, dy=wy-camY, dz=wz-camZ;
      const vvx=r2[0]*dx+r2[1]*dy+r2[2]*dz;
      const vvy=ux*dx+uy*dy+uz*dz;
      const vvz=-(zx*dx+zy*dy+zz*dz);
      if(vvz<=0) return null;
      return [vvx/vvz, vvy/vvz]; // divide by vvz; apply f+aspect at draw time
    }

    // Pre-project all points once — store as Float32Array per rank
    // Format per rank: [nx, ny, nx, ny, ...]  (NDC before aspect/fov scaling)
    const NDC = [[], [], [], []];

    function buildNDC(data) {
      NDC.forEach(a => a.length = 0);
      for (const [lon, lat, rank] of data) {
        const p = projectLonLat(lon, lat);
        if (!p) continue;
        NDC[rank].push(p[0], p[1]);
      }
    }

    // rank 0=hamlet, 1=village, 2=town, 3=city
    const STYLES = [
      { color:'rgba(0,210,230,0.45)', r:1.5, blur:0 },
      { color:'rgba(0,225,240,0.70)', r:2.0, blur:0 },
      { color:'rgba(0,240,255,0.90)', r:3.0, blur:6 },
      { color:'rgba(255,255,255,1.0)', r:5.0, blur:14 },
    ];

    function draw() {
      const W=canvas.width, H=canvas.height;
      const aspect=W/H;
      const fx = 1/(FOV_HALF_TAN*aspect); // horizontal focal length
      const fy = 1/FOV_HALF_TAN;          // vertical focal length
      ctx.clearRect(0,0,W,H);
      for(let rank=0;rank<4;rank++) {
        const {color,r,blur}=STYLES[rank];
        const arr=NDC[rank];
        if(!arr.length) continue;
        ctx.shadowBlur=blur;
        ctx.shadowColor=blur?'rgba(0,210,255,0.7)':'transparent';
        ctx.fillStyle=color;
        // Build one Path2D for all dots of this rank — single fill call
        const path=new Path2D();
        for(let i=0;i<arr.length;i+=2) {
          const sx=((arr[i]*fx)+1)*0.5*W;
          const sy=(1-arr[i+1]*fy)*0.5*H;
          if(sx<-10||sx>W+10||sy<-10||sy>H+10) continue;
          path.arc(sx,sy,r,0,Math.PI*2);
          path.closePath();
        }
        ctx.fill(path);
      }
      ctx.shadowBlur=0;
    }

    function resize() {
      canvas.width  = window.innerWidth;
      canvas.height = window.innerHeight;
      draw();
    }

    fetch('/population/places.json')
      .then(r => r.json())
      .then(data => {
        buildNDC(data);
        resize();
      })
      .catch(e => console.error('[PopulationDots]', e));

    window.addEventListener('resize', resize);
    resize();
    return () => window.removeEventListener('resize', resize);
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed', inset: 0,
        width: '100vw', height: '100vh',
        zIndex: 2,
        pointerEvents: 'none',
      }}
    />
  );
});

// ---------------------------------------------------------------------------
// MapPainter — admin-only click-to-place glowing dot clusters, saved to DB
// ---------------------------------------------------------------------------
const API_BASE = window.location.port === '5173' ? 'http://127.0.0.1:8000' : '';

// Shared projection helper (same camera as FixedCamera)
const _CAM_ANGLE = -20 * Math.PI / 180;
const _camX =  7.5 * Math.sin(_CAM_ANGLE);
const _camY =  9.5;
const _camZ =  7.5 * Math.cos(_CAM_ANGLE);
const _FOV_HALF_TAN = Math.tan((35 * Math.PI / 180) / 2);

function _norm(x,y,z){ const l=Math.sqrt(x*x+y*y+z*z); return [x/l,y/l,z/l]; }
const _zAxis = _norm(_camX,      _camY,      _camZ-0.2);
const _rAxis = _norm(1*_zAxis[2]-0*_zAxis[1], 0*_zAxis[0]-0*_zAxis[2], 0*_zAxis[1]-1*_zAxis[0]);
const _uAxis = [
  _zAxis[1]*_rAxis[2]-_zAxis[2]*_rAxis[1],
  _zAxis[2]*_rAxis[0]-_zAxis[0]*_rAxis[2],
  _zAxis[0]*_rAxis[1]-_zAxis[1]*_rAxis[0],
];

const LON_MIN_P=44.8414, LON_MAX_P=50.6127, LAT_MIN_P=38.4425, LAT_MAX_P=41.8393;

function projectToScreen(lon, lat, W, H) {
  const u=(lon-LON_MIN_P)/(LON_MAX_P-LON_MIN_P);
  const v=(lat-LAT_MIN_P)/(LAT_MAX_P-LAT_MIN_P);
  const wx=(u-0.5)*10, wy=0, wz=(v-0.5)*8;
  const dx=wx-_camX, dy=wy-_camY, dz=wz-_camZ;
  const vx=_rAxis[0]*dx+_rAxis[1]*dy+_rAxis[2]*dz;
  const vy=_uAxis[0]*dx+_uAxis[1]*dy+_uAxis[2]*dz;
  const vz=-(_zAxis[0]*dx+_zAxis[1]*dy+_zAxis[2]*dz);
  if(vz<=0) return null;
  const aspect=W/H;
  const fx=1/(_FOV_HALF_TAN*aspect), fy=1/_FOV_HALF_TAN;
  return [((vx/vz*fx)+1)*0.5*W, (1-vy/vz*fy)*0.5*H];
}

// Inverse: screen pixel → approximate lon/lat via ray-plane intersection
function screenToLonLat(sx, sy, W, H) {
  const aspect=W/H;
  const fx=1/(_FOV_HALF_TAN*aspect), fy=1/_FOV_HALF_TAN;
  // NDC
  const ndx=(sx/W*2-1)/fx, ndy=(1-sy/H*2)/fy;
  // Ray direction in world space
  const rdx=_rAxis[0]*ndx+_uAxis[0]*ndy-_zAxis[0];
  const rdy=_rAxis[1]*ndx+_uAxis[1]*ndy-_zAxis[1];
  const rdz=_rAxis[2]*ndx+_uAxis[2]*ndy-_zAxis[2];
  // Intersect with y=0 plane
  if(Math.abs(rdy)<1e-6) return null;
  const t=-_camY/rdy;
  if(t<0) return null;
  const wx=_camX+rdx*t, wz=_camZ+rdz*t;
  const u=wx/10+0.5, v=wz/8+0.5;
  const lon=u*(LON_MAX_P-LON_MIN_P)+LON_MIN_P;
  const lat=v*(LAT_MAX_P-LAT_MIN_P)+LAT_MIN_P;
  if(lon<LON_MIN_P-1||lon>LON_MAX_P+1||lat<LAT_MIN_P-1||lat>LAT_MAX_P+1) return null;
  return [lon, lat];
}

// Build a glow sprite texture once — large soft radial gradient for a blurry look
function _makeGlowTexture() {
  const sz = 128;
  const c = document.createElement('canvas');
  c.width = c.height = sz;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(sz/2,sz/2,0, sz/2,sz/2,sz/2);
  g.addColorStop(0,    'rgba(220,255,255,1)');
  g.addColorStop(0.08, 'rgba(0,232,255,0.85)');
  g.addColorStop(0.25, 'rgba(0,200,255,0.45)');
  g.addColorStop(0.55, 'rgba(0,140,220,0.15)');
  g.addColorStop(1,    'rgba(0,80,180,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0,0,sz,sz);
  const tex = new THREE.CanvasTexture(c);
  return tex;
}
const _glowTex = _makeGlowTexture();

// lon/lat → Three.js world XZ (map plane is 10×8, y=0.02 above surface)
function lonLatToWorld(lon, lat) {
  const u = (lon - LON_MIN_P) / (LON_MAX_P - LON_MIN_P);
  const v = (lat - LAT_MIN_P) / (LAT_MAX_P - LAT_MIN_P);
  return [(u - 0.5) * 10, 0.02, (v - 0.5) * 8];
}

// R3F component — lives inside <Canvas>, depth-sorted below antennas
function AnnotationSprites({ dots = [], dotScale = 1, animOpacity = 0.40 }) {
  const { invalidate } = useThree();
  useEffect(() => { invalidate(); }, [animOpacity, dots, dotScale]);
  const spriteSize = 0.38 * dotScale;

  return (
    <group renderOrder={1}>
      {dots.map((dot, i) => {
        const [wx, wy, wz] = lonLatToWorld(dot.lon, dot.lat);
        return (
          <sprite key={i} position={[wx, wy, wz]} scale={[spriteSize, spriteSize, 1]} renderOrder={1} raycast={null}>
            <spriteMaterial
              map={_glowTex}
              color="#00e8ff"
              transparent
              opacity={animOpacity}
              depthWrite={false}
              blending={THREE.AdditiveBlending}
            />
          </sprite>
        );
      })}
    </group>
  );
}

const MapPainter = memo(function MapPainter({ isAdmin, showToolbar = true, onDotsChange, onScaleChange, onReady, dotScale }) {
  const [painting,  setPainting]  = useState(false);
  const [erasing,   setErasing]   = useState(false);
  const [pending,   setPending]   = useState([]);
  const [saved,     setSaved]     = useState([]);
  const [saving,    setSaving]    = useState(false);
  const [status,    setStatus]    = useState('');

  // Notify parent whenever dots change
  useEffect(() => {
    onDotsChange?.([...saved, ...pending]);
  }, [saved, pending]);

  // Load saved annotations on mount — fire onReady after fetch resolves
  useEffect(() => {
    fetch(`${API_BASE}/api/map-annotations`, { credentials:'include' })
      .then(r => r.ok ? r.json() : [])
      .then(data => { setSaved(data); onReady?.(); })
      .catch(() => { onReady?.(); });
  }, []);

  // Click handler on invisible overlay — place or erase
  const handleClick = (e) => {
    if (!isAdmin) return;
    const W = window.innerWidth, H = window.innerHeight;
    const sx = e.clientX, sy = e.clientY;
    if (erasing) {
      const hit = (dots, setter) => {
        let best = null, bestD = 24;
        for (const d of dots) {
          const p = projectToScreen(d.lon, d.lat, W, H);
          if (!p) continue;
          const dist = Math.hypot(p[0]-sx, p[1]-sy);
          if (dist < bestD) { bestD = dist; best = d; }
        }
        if (best) { setter(arr => arr.filter(d => d !== best)); return true; }
        return false;
      };
      if (!hit(pending, setPending)) hit(saved, setSaved);
      return;
    }
    if (!painting) return;
    const ll = screenToLonLat(sx, sy, W, H);
    if (!ll) return;
    setPending(p => [...p, { lon:ll[0], lat:ll[1], id: Date.now() }]);
  };

  const handleSave = async () => {
    setSaving(true); setStatus('Saving…');
    try {
      const all = [
        ...saved.map(a   => ({ lon:a.lon, lat:a.lat, label:a.label||'', color:'#00e5ff', radius:8 })),
        ...pending.map(a => ({ lon:a.lon, lat:a.lat, label:'',          color:'#00e5ff', radius:8 })),
      ];
      const res = await fetch(`${API_BASE}/api/map-annotations`, {
        method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(all),
      });
      if (res.ok) {
        const fresh = await fetch(`${API_BASE}/api/map-annotations`, { credentials:'include' });
        setSaved(await fresh.json());
        setPending([]);
        setStatus(`Saved ${all.length} dot${all.length!==1?'s':''}`);
        setTimeout(() => setStatus(''), 3000);
      } else { setStatus('Save failed'); }
    } catch(e) { setStatus('Error: '+e.message); }
    finally { setSaving(false); }
  };

  const handleClearAll = async () => {
    if (!window.confirm('Clear ALL saved annotations?')) return;
    await fetch(`${API_BASE}/api/map-annotations`, { method:'DELETE', credentials:'include' });
    setSaved([]); setPending([]); setStatus('Cleared');
    setTimeout(() => setStatus(''), 2000);
  };

  return (
    <>
      {/* Invisible click catcher — only active when painting/erasing */}
      {isAdmin && showToolbar && (painting || erasing) && (
        <div onClick={handleClick} style={{
          position:'fixed', inset:0, zIndex:10,
          cursor: erasing ? 'cell' : 'crosshair',
        }} />
      )}
      {/* Admin toolbar */}
      {isAdmin && showToolbar && (
        <div style={{
          position:'fixed', bottom:24, left:'50%', transform:'translateX(-50%)',
          zIndex:200, display:'flex', alignItems:'center', gap:10,
          background:'rgba(0,8,18,0.92)', border:'1px solid rgba(0,180,255,0.3)',
          borderRadius:8, padding:'8px 16px', backdropFilter:'blur(12px)',
          fontFamily:'monospace', fontSize:12, color:'#00e5ff',
          boxShadow:'0 0 24px rgba(0,180,255,0.2)', pointerEvents:'auto',
        }}>
          <button onClick={() => { setPainting(p=>!p); setErasing(false); if(painting) setPending([]); }}
            style={{
              background: painting?'rgba(0,180,255,0.25)':'rgba(0,180,255,0.10)',
              border:`1px solid ${painting?'#00e8ff':'rgba(0,180,255,0.35)'}`,
              color:'#00e8ff', borderRadius:5, padding:'4px 12px',
              cursor:'pointer', fontFamily:'monospace', fontSize:12,
            }}>
            {painting ? '◉ PAINTING' : '✎ Place Dots'}
          </button>
          <button onClick={() => { setErasing(e=>!e); setPainting(false); }}
            style={{
              background: erasing?'rgba(255,60,60,0.2)':'rgba(0,180,255,0.10)',
              border:`1px solid ${erasing?'#ff4444':'rgba(0,180,255,0.3)'}`,
              color: erasing?'#ff6666':'#00e5ff',
              borderRadius:5, padding:'4px 10px', cursor:'pointer', fontFamily:'monospace', fontSize:12,
            }}>
            {erasing ? '⌫ ERASE' : '⌫ Erase'}
          </button>
          <label style={{display:'flex',alignItems:'center',gap:6,color:'rgba(0,210,255,0.7)'}}>
            <span style={{fontSize:10}}>●</span>
            <input type="range" min="0.3" max="3" step="0.05" value={dotScale}
              onChange={e => onScaleChange?.(Number(e.target.value))}
              style={{width:72, accentColor:'#00e5ff', cursor:'pointer'}}/>
            <span style={{fontSize:10}}>●●</span>
          </label>
          {painting && pending.length>0 && <span style={{color:'#00e8ff'}}>{pending.length} new</span>}
          {(pending.length>0||saved.length>0) && (
            <button onClick={handleSave} disabled={saving||pending.length===0}
              style={{
                background:'rgba(0,255,100,0.15)', border:'1px solid rgba(0,255,100,0.4)',
                color:'#00ff64', borderRadius:5, padding:'4px 12px', cursor:'pointer',
                fontFamily:'monospace', fontSize:12, opacity:pending.length===0?0.4:1,
              }}>
              {saving ? 'Saving…' : `💾 Save (${pending.length} new)`}
            </button>
          )}
          {saved.length>0 && (
            <button onClick={handleClearAll}
              style={{
                background:'rgba(255,0,60,0.12)', border:'1px solid rgba(255,0,60,0.3)',
                color:'#ff3344', borderRadius:5, padding:'4px 10px', cursor:'pointer',
                fontFamily:'monospace', fontSize:12,
              }}>
              🗑 Clear All
            </button>
          )}
          {status && <span style={{color:'#aaffcc', marginLeft:4}}>{status}</span>}
          {!painting && saved.length>0 && (
            <span style={{color:'rgba(0,200,255,0.5)'}}>{saved.length} dot{saved.length!==1?'s':''} on map</span>
          )}
        </div>
      )}
    </>
  );
});

// Map a frequency to its RTYS/station name within an SDR's config
function getStationInfo(sdrId, freq) {
  const sdr = SDR_RTYS_MAP[sdrId];
  if (!sdr) return null;
  const f = Number(freq).toFixed(1);
  if (f === '223.9') return { rtys: 'DAB+', name: '12A' };
  for (const [rtysName, entries] of Object.entries(sdr.stations)) {
    const entry = entries.find(([ff]) => Number(ff).toFixed(1) === f);
    if (entry) return { rtys: rtysName, name: entry[1] };
  }
  return null;
}
// ---------------------------------------------------------------------------
function MapParticles() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    let maskData = null, maskW = 0, maskH = 0;
    let animId = null;
    let running = true;
    let lastTs = 0;

    const maskImg = new Image();
    maskImg.crossOrigin = 'anonymous';
    maskImg.src = regionMask;

    // Bounds where the 3D map renders on screen — adjust if needed
    const MX = 0.10, MY = 0.08, MW = 0.75, MH = 0.84;

    const W = () => canvas.width;
    const H = () => canvas.height;

    // Fast pixel lookup using cached array — no per-frame getImageData
    function insideMask(x, y) {
      if (!maskData) return false;
      const u = (x / W() - MX) / MW;
      const v = (y / H() - MY) / MH;
      if (u < 0.005 || u > 0.995 || v < 0.005 || v > 0.995) return false;
      const px = Math.min(maskW - 1, Math.floor(u * maskW));
      const py = Math.min(maskH - 1, Math.floor(v * maskH));
      return maskData[(py * maskW + px) * 4] > 160;
    }

    const N = 24;            // was 45 — fewer particles, far fewer line checks
    const LINK_DIST = 100;   // slightly tighter link radius
    let pts = [];

    function initParticles() {
      pts = [];
      let attempts = 0;
      while (pts.length < N && attempts < N * 30) {
        attempts++;
        const x = (MX + Math.random() * MW) * W();
        const y = (MY + Math.random() * MH) * H();
        if (insideMask(x, y)) {
          pts.push({
            x, y,
            vx: (Math.random() - 0.5) * 0.4,
            vy: (Math.random() - 0.5) * 0.3,
          });
        }
      }
    }

    function resize() {
      canvas.width  = window.innerWidth;
      canvas.height = window.innerHeight;
      if (maskData) initParticles();
    }

    maskImg.onload = () => {
      maskW = maskImg.naturalWidth;
      maskH = maskImg.naturalHeight;
      const mc = document.createElement('canvas');
      mc.width = maskW; mc.height = maskH;
      const mctx = mc.getContext('2d', { willReadFrequently: true });
      mctx.drawImage(maskImg, 0, 0);
      try {
        maskData = mctx.getImageData(0, 0, maskW, maskH).data;
      } catch(e) {
        // CORS fallback: treat entire bounding box as valid
        maskData = new Uint8ClampedArray(maskW * maskH * 4).fill(255);
      }
      resize();
      loop();
    };

    window.addEventListener('resize', resize);
    resize();

    const FRAME_MS = 1000 / 10; // cap at 10fps to cut CPU usage

    function loop(ts) {
      animId = requestAnimationFrame(loop);
      if (!running || document.hidden) return;
      if (ts - lastTs < FRAME_MS) return;
      lastTs = ts;

      const w = W(), h = H();
      ctx.clearRect(0, 0, w, h);

      // Move & bounce inside mask
      for (const p of pts) {
        p.x += p.vx;
        p.y += p.vy;
        const nx = p.x + p.vx * 8;
        const ny = p.y + p.vy * 8;
        if (!insideMask(nx, p.y)) p.vx *= -1;
        if (!insideMask(p.x, ny)) p.vy *= -1;
        p.x = Math.max(0, Math.min(w, p.x));
        p.y = Math.max(0, Math.min(h, p.y));
      }

      // Draw lines in a single batched path per alpha bucket to cut state changes
      const LINK_DIST2 = LINK_DIST * LINK_DIST;
      ctx.lineWidth = 0.8;
      for (let a = 0; a < pts.length; a++) {
        for (let b = a + 1; b < pts.length; b++) {
          const dx = pts[a].x - pts[b].x, dy = pts[a].y - pts[b].y;
          const dist2 = dx * dx + dy * dy;
          if (dist2 > LINK_DIST2) continue;
          const alpha = (1 - Math.sqrt(dist2) / LINK_DIST) * 0.45;
          ctx.strokeStyle = `rgba(0,196,216,${alpha.toFixed(3)})`;
          ctx.beginPath();
          ctx.moveTo(pts[a].x, pts[a].y);
          ctx.lineTo(pts[b].x, pts[b].y);
          ctx.stroke();
        }
      }

      // Draw dots in one batched Path2D
      const dotPath = new Path2D();
      for (const p of pts) {
        dotPath.arc(p.x, p.y, 2, 0, Math.PI * 2);
        dotPath.closePath();
      }
      ctx.fillStyle = 'rgba(0,229,255,0.75)';
      ctx.fill(dotPath);
    }

    const onVis = () => {
      if (document.hidden) {
        running = false;
      } else {
        running = true;
        lastTs = 0;
      }
    };
    document.addEventListener('visibilitychange', onVis);

    return () => {
      running = false;
      if (animId) cancelAnimationFrame(animId);
      window.removeEventListener('resize', resize);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed', inset: 0,
        width: '100vw', height: '100vh',
        zIndex: 2,
        pointerEvents: 'none',
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// WaveSurface  (reads animation data from animRef — zero re-render overhead)
// ---------------------------------------------------------------------------
function WaveSurface({
  noiseScale, peakRadius, peakPosition,
  hoverRadius, hoverHeight,
  hoverActiveRef, hoverPositionRef,
  customPeaks, animRef, onPeakClick, peakIntRef,
}) {
  const meshRef      = useRef();
  const materialRef  = useRef();
  const alphaMap     = useTexture(regionMask);

  useFrame((state) => {
    if (!materialRef.current) return;
    const u    = materialRef.current.uniforms;
    const anim = animRef.current;

    u.uTime.value          = state.clock.elapsedTime;
    u.uPeakIntensity.value = peakIntRef.current;
    u.uNoiseScale.value    = noiseScale;
    u.uPeakRadius.value    = peakRadius;
    u.uPeakPosition.value  = peakPosition;
    u.uHoverActive.value   = hoverActiveRef.current ? 1.0 : 0.0;
    u.uHoverPosition.value = hoverPositionRef.current;
    u.uHoverRadius.value   = hoverRadius;
    u.uHoverHeight.value   = hoverHeight;
    u.uCustomPeakCount.value = customPeaks.length;
    for (let i = 0; i < 10; i++) {
      if (i < customPeaks.length) {
        u.uCustomPeakPositions.value[i].set(customPeaks[i].x, customPeaks[i].y);
        u.uCustomPeakRadii.value[i]   = customPeaks[i].radius;
        u.uCustomPeakHeights.value[i] = customPeaks[i].height;
      } else {
        u.uCustomPeakPositions.value[i].set(0, 0);
        u.uCustomPeakRadii.value[i]   = 0;
        u.uCustomPeakHeights.value[i] = 0;
      }
      u.uLaserProgress.value[i]  = anim.laserProgress[i];
      u.uChargeProgress.value[i] = anim.chargeProgress[i];
      u.uImpactProgress.value[i] = anim.impactProgress[i];
      u.uLaunchProgress.value[i] = anim.launchProgress[i];
    }
  });

  const uniforms = {
    uTime:                 { value: 0 },
    uPeakIntensity:        { value: 0 },
    uNoiseScale:           { value: noiseScale },
    uPeakRadius:           { value: peakRadius },
    uPeakPosition:         { value: peakPosition },
    uHoverActive:          { value: 0 },
    uHoverPosition:        { value: [0, 0] },
    uHoverRadius:          { value: hoverRadius },
    uHoverHeight:          { value: hoverHeight },
    uAlphaMap:             { value: alphaMap },
    uCustomPeakCount:      { value: 0 },
    uCustomPeakPositions:  { value: Array.from({ length: 10 }, () => new THREE.Vector2(0, 0)) },
    uCustomPeakRadii:      { value: new Float32Array(10) },
    uCustomPeakHeights:    { value: new Float32Array(10) },
    uLaserProgress:        { value: new Float32Array(10) },
    uChargeProgress:       { value: new Float32Array(10) },
    uImpactProgress:       { value: new Float32Array(10) },
    uLaunchProgress:       { value: new Float32Array(10) },
    uLaserColors:          { value: Array.from({ length: 10 }, (_, i) => i < CITY_DATA.length ? new THREE.Vector3(...CITY_DATA[i].gl) : new THREE.Vector3(0, 0, 0)) },
  };

  const handlePointerMove = (e) => {
    e.stopPropagation();
    if (!e.uv) return;
    const uv = [e.uv.x, e.uv.y];
    hoverActiveRef.current   = true;
    hoverPositionRef.current = uv;
    onPeakClick?.(uv, 'hover');
  };
  const handlePointerOut = () => {
    hoverActiveRef.current = false;
    onPeakClick?.(null, 'out');
  };
  const handleClick = (e) => {
    e.stopPropagation();
    if (e.uv) onPeakClick?.([e.uv.x, e.uv.y], 'click');
  };

  return (
    <mesh
      ref={meshRef}
      rotation={[-Math.PI / 2, 0, 0]}
      scale={[1, -1, 1]}
      onPointerMove={handlePointerMove}
      onPointerOut={handlePointerOut}
      onClick={handleClick}
    >
      <planeGeometry args={[10, 8, 48, 48]} />
      <shaderMaterial
        ref={materialRef}
        vertexShader={vertexShader}
        fragmentShader={fragmentShader}
        uniforms={uniforms}
        transparent={true}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        side={THREE.FrontSide}
      />
    </mesh>
  );
}

// UV (0,0)=(-5,-4) -> world(-5,y,-4), UV(1,1)=(5,4) -> world(5,y,4)
function uvToWorld(uvX, uvY) {
  return [(uvX - 0.5) * 10, (uvY - 0.5) * 8];
}

// ---------------------------------------------------------------------------
// NeonOutline — GeoJSON border rendered as bright cyan LineSegments
// ---------------------------------------------------------------------------
function NeonOutline({ isAlert = false }) {
  const geo = React.useMemo(() => {
    // Compute bounds matching generate_mask.py exactly (5% padding)
    let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
    for (const feat of regionGeoJSON.features) {
      const geom = feat.geometry;
      const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
      for (const poly of polys)
        for (const ring of poly)
          for (const [lon, lat] of ring) {
            if (lon < minLon) minLon = lon; if (lon > maxLon) maxLon = lon;
            if (lat < minLat) minLat = lat; if (lat > maxLat) maxLat = lat;
          }
    }
    const pad = 0.05;
    const lonSpan = maxLon - minLon; const latSpan = maxLat - minLat;
    minLon -= lonSpan * pad; maxLon += lonSpan * pad;
    minLat -= latSpan * pad; maxLat += latSpan * pad;

    const lonRange = maxLon - minLon;
    const latRange = maxLat - minLat;

    // lon/lat -> UV -> world XZ
    // Lat is flipped: mask was drawn with PIL (Y=0 at top), plane scale=[1,-1,1] compensates
    const toWorld = (lon, lat) => {
      const uvX = (lon - minLon) / lonRange;
      const uvY = 1.0 - (lat - minLat) / latRange;
      return [(uvX - 0.5) * 10, (uvY - 0.5) * 8];
    };

    const positions = [];
    for (const feat of regionGeoJSON.features) {
      const geom = feat.geometry;
      const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
      for (const poly of polys) {
        for (const ring of poly) {
          for (let j = 0; j < ring.length - 1; j++) {
            const [x0, z0] = toWorld(ring[j][0],   ring[j][1]);
            const [x1, z1] = toWorld(ring[j+1][0], ring[j+1][1]);
            positions.push(x0, 0.04, z0,  x1, 0.04, z1);
          }
        }
      }
    }
    const g = new LineSegmentsGeometry();
    // LineSegmentsGeometry expects flat [x0,y0,z0, x1,y1,z1, ...]
    g.setPositions(positions);
    return g;
  }, []);

  const mat = React.useMemo(() => new LineMaterial({
    color: 0x00e5ff,
    linewidth: 1.5,
    transparent: false,
    depthWrite: false,
    resolution: new THREE.Vector2(window.innerWidth, window.innerHeight),
  }), []);

  useEffect(() => {
    const onResize = () => mat.resolution.set(window.innerWidth, window.innerHeight);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [mat]);

  return <primitive object={new LineSegments2(geo, mat)} raycast={null} />;
}

// Replace all mesh materials with a MeshStandardMaterial in the given color
function applyNeonMaterial(obj, color, emissiveIntensity = 2.5) {
  const col = new THREE.Color(color);
  obj.traverse(child => {
    if (child.isMesh) {
      child.material = new THREE.MeshStandardMaterial({
        color: col,
        emissive: col,
        emissiveIntensity,
        roughness: 0.3,
        metalness: 0.7,
      });
    }
  });
}

// ---------------------------------------------------------------------------
// Strip morph targets from cloned GLBs — they are static here and morph
// evaluation drives the expensive getVertexPosition() seen in the profile.
// ---------------------------------------------------------------------------
function stripMorphTargets(obj) {
  obj.traverse(child => {
    if (child.isMesh && child.geometry) {
      child.geometry.morphAttributes = {};
      child.geometry.morphTargetsRelative = false;
      child.morphTargetInfluences = [];
    }
  });
}

// ---------------------------------------------------------------------------
// MapTooltip — Watch Dogs style label: right-side, per-letter glitch, readable
// ---------------------------------------------------------------------------
function MapTooltip({ name }) {
  return (
    <div className="map-antenna-tooltip">
      <TextType
        text={name}
        as="span"
        className="map-antenna-tooltip-text"
        typingSpeed={55}
        deletingSpeed={35}
        pauseDuration={1500}
        showCursor={false}
        loop={false}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// AntennaModel — city antenna with 3D Html tooltip on pointer hover
// ---------------------------------------------------------------------------
function AntennaModel({ uvX, uvY, hoverActiveRef, hoverUvRef, color = '#00ffee', rotationY = 0, stationData, sdrId, sdrNodes, onSelect }) {
  const { scene } = useGLTF(antennaGlb);
  const ref = useRef();
  const targetY   = useRef(0.0);
  const targetEmi = useRef(4.0);
  const [pointerOver, setPointerOver] = useState(false);
  const [wx, wz] = uvToWorld(uvX, uvY);
  const matsRef = useRef([]);
  const origEmissiveRef = useRef([]);

  const isOffline = sdrId ? sdrNodes?.find(n => n.id === sdrId)?.online === false : false;
  const isOfflineRef = useRef(isOffline);
  useEffect(() => { isOfflineRef.current = isOffline; }, [isOffline]);

  const cloned = React.useMemo(() => {
    const c = scene.clone(true);
    const mats = [];
    const origEmissive = [];
    c.traverse(child => {
      if (child.isMesh && child.material) {
        const ms = Array.isArray(child.material) ? child.material : [child.material];
        const clonedMs = ms.map(m => m.clone());
        child.material = Array.isArray(child.material) ? clonedMs : clonedMs[0];
        clonedMs.forEach(m => {
          m.emissiveIntensity = (m.emissiveIntensity || 0) + 4.0;
          m.needsUpdate = true;
          origEmissive.push(m.emissive.clone());
        });
        mats.push(...clonedMs);
      }
    });
    stripMorphTargets(c);
    matsRef.current = mats;
    origEmissiveRef.current = origEmissive;
    return c;
  }, [scene]);

  useFrame(({ clock }) => {
    if (!ref.current) return;
    let goal    = 0.0;
    let emiGoal = pointerOver ? 12.0 : 4.0;
    if (hoverActiveRef.current) {
      const dx = hoverUvRef.current[0] - uvX;
      const dy = hoverUvRef.current[1] - uvY;
      const dist = Math.hypot(dx, dy);
      if (dist < 0.12) goal = 0.45 * 0.3125 * (1.0 - dist / 0.12);
    }
    targetY.current   += (goal    - targetY.current)   * 0.10;
    targetEmi.current += (emiGoal - targetEmi.current)  * 0.12;
    ref.current.position.y = targetY.current;

    const t = clock.elapsedTime;
    const offline = isOfflineRef.current;
    const flash = offline ? (0.65 + 0.35 * Math.sin(t * 5 + uvX * 10)) : 1.0;
    const emi = targetEmi.current * flash;
    const red = new THREE.Color('#ff2200');
    for (let i = 0; i < matsRef.current.length; i++) {
      const m = matsRef.current[i];
      m.emissiveIntensity = emi;
      m.emissive.copy(offline ? red : origEmissiveRef.current[i]);
    }
  });

  useEffect(() => () => { document.body.style.cursor = 'auto'; }, []);

  const handlePointerOver = (e) => {
    e.stopPropagation();
    setPointerOver(true);
    document.body.style.cursor = 'pointer';
  };
  const handlePointerOut = (e) => {
    e.stopPropagation();
    setPointerOver(false);
    document.body.style.cursor = 'auto';
  };
  const handleClick = (e) => {
    e.stopPropagation();
    if (sdrId && onSelect) onSelect(sdrId);
  };

  return (
    <group>
      <primitive ref={ref} object={cloned} raycast={null}
        position={[wx, 0.0, wz]}
        scale={0.018}
        rotation={[0, rotationY, 0]}
      />
      <mesh
        position={[wx, 0.25, wz]}
        onPointerOver={handlePointerOver}
        onPointerOut={handlePointerOut}
        onClick={handleClick}
      >
        <sphereGeometry args={[0.25, 16, 16]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      {pointerOver && stationData && (
        <Html position={[wx + 0.35, 0.45, wz]} center distanceFactor={6} zIndexRange={[100,200]}>
          <MapTooltip name={stationData.name} />
        </Html>
      )}
    </group>
  );
}

// ---------------------------------------------------------------------------
// MainPeakModel — special Site-1 GLB, larger, always slightly raised, lifts on hover
// ---------------------------------------------------------------------------
function MainPeakModel({ uvX, uvY, hoverActiveRef, hoverUvRef, sdrNodes, onSelect }) {
  const { scene } = useGLTF(mainPeakGlb);
  const ref = useRef();
  const targetY = useRef(0.90);
  const [wx, wz] = uvToWorld(uvX, uvY);
  const [pointerOver, setPointerOver] = useState(false);
  const matsRef = useRef([]);
  const origEmissiveRef = useRef([]);

  const sdrId = 'site_1';
  const isOffline = sdrNodes?.find(n => n.id === sdrId)?.online === false;
  const isOfflineRef = useRef(isOffline);
  useEffect(() => { isOfflineRef.current = isOffline; }, [isOffline]);

  const cloned = React.useMemo(() => {
    const c = scene.clone(true);
    const mats = [];
    const origEmissive = [];
    c.traverse(child => {
      if (child.isMesh && child.material) {
        const ms = Array.isArray(child.material) ? child.material : [child.material];
        const clonedMs = ms.map(m => m.clone());
        child.material = Array.isArray(child.material) ? clonedMs : clonedMs[0];
        clonedMs.forEach(m => {
          m.emissiveIntensity = (m.emissiveIntensity || 0) + 2.0;
          m.needsUpdate = true;
          origEmissive.push(m.emissive.clone());
        });
        mats.push(...clonedMs);
      }
    });
    stripMorphTargets(c);
    c.raycast = null;
    matsRef.current = mats;
    origEmissiveRef.current = origEmissive;
    return c;
  }, [scene]);

  useFrame(({ clock }) => {
    if (!ref.current) return;
    let goal = 0.90;
    if (hoverActiveRef.current) {
      const dx = hoverUvRef.current[0] - uvX;
      const dy = hoverUvRef.current[1] - uvY;
      const dist = Math.hypot(dx, dy);
      if (dist < 0.16) goal = 0.90 + 0.65 * 0.3125 * (1.0 - dist / 0.16);
    }
    if (pointerOver) goal += 0.25;
    targetY.current += (goal - targetY.current) * 0.10;
    ref.current.position.y = targetY.current;

    const t = clock.elapsedTime;
    const offline = isOfflineRef.current;
    const flash = offline ? (0.65 + 0.35 * Math.sin(t * 5)) : 1.0;
    const red = new THREE.Color('#ff2200');
    for (let i = 0; i < matsRef.current.length; i++) {
      const m = matsRef.current[i];
      m.emissiveIntensity = (2.0 + (pointerOver ? 4.0 : 0)) * flash;
      m.emissive.copy(offline ? red : origEmissiveRef.current[i]);
    }
  });

  useEffect(() => () => { document.body.style.cursor = 'auto'; }, []);

  const handlePointerOver = (e) => {
    e.stopPropagation();
    setPointerOver(true);
    document.body.style.cursor = 'pointer';
  };
  const handlePointerOut = (e) => {
    e.stopPropagation();
    setPointerOver(false);
    document.body.style.cursor = 'auto';
  };
  const handleClick = (e) => {
    e.stopPropagation();
    if (onSelect) onSelect(sdrId);
  };

  return (
    <group>
      <primitive ref={ref} object={cloned} raycast={null}
        position={[wx, 0.90, wz]}
        scale={0.36}
        rotation={[0, 0, 0]}
      />
      <mesh
        position={[wx, 1.1, wz]}
        onPointerOver={handlePointerOver}
        onPointerOut={handlePointerOut}
        onClick={handleClick}
      >
        <sphereGeometry args={[0.55, 16, 16]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      {pointerOver && (
        <Html position={[wx + 0.55, 1.25, wz]} center distanceFactor={6} zIndexRange={[100,200]}>
          <MapTooltip name="Site-1" />
        </Html>
      )}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Ticker — drives frameloop="demand" at 20fps to cut 3D CPU load
// ---------------------------------------------------------------------------
function Ticker() {
  const { invalidate } = useThree();
  useEffect(() => {
    const id = setInterval(() => { if (!document.hidden) invalidate(); }, 1000 / 20);
    return () => clearInterval(id);
  }, [invalidate]);
  return null;
}

// ---------------------------------------------------------------------------
// Fixed Camera — also handles zoom-to-Site-1 transition
// ---------------------------------------------------------------------------
const _zoomTarget = new THREE.Vector3();
function FixedCamera({ zoomRef }) {
  const { camera } = useThree();
  useEffect(() => {
    const angle = -20 * (Math.PI / 180);
    const distance = 7.5;
    const height = 9.5;
    camera.position.set(distance * Math.sin(angle), height, distance * Math.cos(angle));
    camera.lookAt(0, 0, 0.2);
    camera.updateProjectionMatrix();
  }, [camera]);

  useFrame(() => {
    if (!zoomRef || !zoomRef.current.active) return;
    const z = zoomRef.current;
    z.t = Math.min(z.t + 0.018, 1.0);
    const ease = z.t < 0.5 ? 2*z.t*z.t : -1+(4-2*z.t)*z.t;
    _zoomTarget.set(z.targetX, z.targetY, z.targetZ);
    camera.position.lerp(_zoomTarget, ease * 0.06);
    camera.lookAt(z.lookX, z.lookY, z.lookZ);
    camera.fov = THREE.MathUtils.lerp(camera.fov, 12, ease * 0.06);
    camera.updateProjectionMatrix();
  });
  return null;
}


// Status colours — tactical palette (reused from top-level token)

// ---------------------------------------------------------------------------
// Mini chart primitives for the collapsed history tab
// ---------------------------------------------------------------------------
function MiniDonutChart({ local, r2, size = 52 }) {
  const total = Math.max(local + r2, 1);
  const cx = size / 2;
  const cy = size / 2;
  const strokeWidth = 5;
  const r = (size - strokeWidth) / 2 - 1;
  const c = 2 * Math.PI * r;
  const localPct = local / total;
  const r2Pct = r2 / total;
  const arc = (pct, offset, color) => {
    const dash = pct * c;
    const gap = c - dash;
    return (
      <circle
        cx={cx} cy={cy} r={r}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={`${dash} ${gap}`}
        transform={`rotate(${offset * 360 - 90} ${cx} ${cy})`}
        strokeLinecap="butt"
      />
    );
  };
  return (
    <svg width={size} height={size} style={{ display: 'block' }}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={strokeWidth} />
      {arc(localPct, 0, CYAN)}
      {arc(r2Pct, localPct, PURPLE)}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// History row helpers (kept outside so the memoized row can access them)
// ---------------------------------------------------------------------------
const fmtTime = (ts) => {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('az', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
};
const fmtFreq = (freq) => Math.abs(freq - 223.936) < 0.01 ? 'DAB+' : freq.toFixed(3);
const dbfsColor = (dbfs, cal = null) => {
  const min = cal?.db_min ?? DBFS_MIN;
  const max = cal?.db_max ?? DBFS_MAX;
  const p = Math.max(0, Math.min(1, (dbfs - min) / (max - min)));
  const { r, g, b } = _colorAt(p);
  return `rgb(${r},${g},${b})`;
};

// ---------------------------------------------------------------------------
// HistoryRow — memoized row so unchanged rows don't re-render on every poll
// ---------------------------------------------------------------------------
const HistoryRow = memo(function HistoryRow({ row, isNew, thresholds }) {
  const th = thresholds[row.sdr_id] || {};
  const cal = { db_min: th.db_min ?? DBFS_MIN, db_max: th.db_max ?? DBFS_MAX, noise_floor: th.noise_floor ?? DBFS_MIN, signal_threshold: th.signal_threshold ?? DBFS_MAX };
  const dbfsColorVal = dbfsColor(row.dbfs, cal);
  const freq = fmtFreq(row.freq);
  const time = fmtTime(row.ts);
  return (
    <div
      style={{ display: 'grid', gridTemplateColumns: '56px 60px 1fr 50px 40px', gap: 6, padding: '7px 8px', borderRadius: 7, marginBottom: 2, border: '1px solid rgba(255,255,255,0.04)' }}
    >
      <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.28)', whiteSpace: 'nowrap' }}>{time}</span>
      <span style={{ fontFamily: MONO, fontSize: 9, color: '#00e5ff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{row.sdr_id || '—'}</span>
      <span style={{ fontFamily: DIGIT, fontSize: 16, color: dbfsColorVal, letterSpacing: '0.04em', textShadow: `0 0 6px ${dbfsColorVal}55` }}>{freq}</span>
      <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, textAlign: 'right', color: row.dbfs < (th.noise_floor ?? DBFS_MIN) ? '#ff5722' : dbfsColorVal }}>{row.dbfs.toFixed(1)}</span>
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

// ---------------------------------------------------------------------------
// StationPanel — memo'd so only scanTick/playingIdx/audio changes re-render it.
// Canvas tree is completely isolated from these updates.
// ---------------------------------------------------------------------------
const _TELE_DEFAULT = { cpu: 0, ram: 0, db_mb: 0, backup_local_mb: 0, backup_r2_mb: 0, backup_running: false, backup_error: '', r2_configured: false, active_users: 0, batch_requests_per_sec: 0, network_io_mbps: 0, sdr_status: false, db_status: false, whatsapp_status: false, drops_24h: 0 };
const StationPanel = memo(function StationPanel({ tab = 'history', onTabChange, expanded = false, onExpandedChange, tele = _TELE_DEFAULT, active = true }) {
  const { t } = useTranslation();
  const thresholds = useSdrThresholds();
  const [rows, setRows] = useState([]);
  const [newIds, setNewIds] = useState(new Set());
  const knownIdsRef = useRef(new Set());
  const [showExport, setShowExport] = useState(false);
  const [localExpanded, setLocalExpanded] = useState(false);

  // Count history events in the last 24h that fall on the main RTYS frequencies for each SDR.
  const filteredDrops24h = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return rows.reduce((count, r) => {
      if (!r.ts || !r.sdr_id) return count;
      const ts = new Date(Number(r.ts) * 1000).getTime();
      if (Number.isNaN(ts) || ts < cutoff) return count;
      const mainRtys = MAIN_RTYS_BY_API_ID[r.sdr_id];
      if (!mainRtys) return count;
      const rtysEntries = SDR_RTYS_MAP[r.sdr_id]?.stations?.[mainRtys];
      if (!rtysEntries) return count;
      const freq = Number(r.freq).toFixed(1);
      return rtysEntries.some(([f]) => Number(f).toFixed(1) === freq) ? count + 1 : count;
    }, 0);
  }, [rows]);
  const [localTab, setLocalTab] = useState('history');
  const activeTab = onTabChange ? tab : localTab;
  const activeExpanded = onExpandedChange ? expanded : localExpanded;
  const setActiveTab = (t) => { if (onTabChange) onTabChange(t); else setLocalTab(t); };
  const setActiveExpanded = (v) => { if (onExpandedChange) onExpandedChange(v); else setLocalExpanded(v); };

  const pad = n => String(n).padStart(2, '0');
  const localIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const [exportFrom, setExportFrom] = useState(() => { const d = new Date(); d.setHours(d.getHours() - 1); return localIso(d); });
  const [exportTo, setExportTo] = useState(() => localIso(new Date()));
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let fetchId = null;
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
          setNewIds(prev => { const n = new Set(prev); fresh.forEach(id => n.add(id)); return n; });
          setTimeout(() => setNewIds(prev => { const n = new Set(prev); fresh.forEach(id => n.delete(id)); return n; }), 900);
        }
        knownIdsRef.current = incoming;
        setRows(data);
      } catch { /* ignore */ }
    };
    const start = () => { if (!active) return; fetchHistory(); fetchId = setInterval(fetchHistory, 10000); };
    const stop  = () => { clearInterval(fetchId); fetchId = null; };
    const onVis = () => { if (document.hidden || !active) { stop(); } else { stop(); start(); } };
    if (!active) {
      return () => { cancelled = true; stop(); };
    }
    document.addEventListener('visibilitychange', onVis);
    start();
    return () => { cancelled = true; stop(); document.removeEventListener('visibilitychange', onVis); };
  }, [active]);

  const TABS = [
    { id: 'history', label: t('top.downtime') },
    { id: 'system', label: t('sigint.history') },
  ];

  const runBackup = async () => {
    tracker.track('BACKUP_CLICK', 'UI', {});
    try { await fetch('/api/backup/run', { method: 'POST', credentials: 'include' }); } catch { /* ignore */ }
  };

  const localDbPct = Math.min((tele.backup_local_mb / (30 * 1024)) * 100, 100);
  const cloudDbPct = Math.min((tele.backup_r2_mb / (10 * 1024)) * 100, 100);
  const cpuColor = tele.cpu > 80 ? ORANGE : tele.cpu > 50 ? AMBER : CYAN;
  const ramColor = tele.ram > 85 ? ORANGE : tele.ram > 60 ? AMBER : CYAN;

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

  // SDR fleet summary color: all online=green, any missing=yellow, majority offline=red
  const sdrNodes = tele.sdr_nodes && tele.sdr_nodes.length > 0 ? tele.sdr_nodes : [{ id: 'SDR', online: tele.sdr_status }];
  const sdrOnline = sdrNodes.filter(n => n.online).length;
  const sdrTotal  = sdrNodes.length;
  const sdrPct    = sdrTotal > 0 ? sdrOnline / sdrTotal : 0;
  const sdrColor  = sdrPct === 1 ? '#00e676' : sdrPct >= 0.6 ? '#ffaf00' : '#ff5252';

  const collapsedView = (
    <div style={{
      position: 'absolute', inset: 0,
      opacity: activeExpanded ? 0 : 1,
      transition: 'opacity 0.2s',
      pointerEvents: activeExpanded ? 'none' : 'auto',
      display: 'flex', flexDirection: 'column',
      padding: '10px 8px',
      overflow: 'hidden',
      background: 'linear-gradient(180deg, rgba(10,14,22,0.98) 0%, rgba(8,12,18,0.95) 100%)',
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, height: '100%' }}>
        {/* SDR Status summary */}
        <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '6px 7px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'rgba(255,255,255,0.25)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{t('sigint.sdrStatus')}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: sdrColor, boxShadow: `0 0 6px ${sdrColor}` }} />
            <span style={{ fontFamily: DIGIT, fontSize: 12, color: sdrColor }}>{sdrOnline}/{sdrTotal}</span>
          </div>
        </div>

        {/* DB + WA */}
        <div style={{ display: 'flex', justifyContent: 'space-between', background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '5px 7px' }}>
          <StatusDot on={tele.db_status} label="DB" />
          <StatusDot on={tele.whatsapp_status} label="WA" />
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
              <span style={{ fontFamily: DIGIT, fontSize: 14, color: cpuColor, letterSpacing: '0.02em' }}>{tele.cpu.toFixed(1)}%</span>
            </div>
            <MiniBar pct={tele.cpu} color={cpuColor} />
          </div>
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '6px 7px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', fontWeight: 600 }}>{t('sigint.ram')}</span>
              <span style={{ fontFamily: DIGIT, fontSize: 14, color: ramColor, letterSpacing: '0.02em' }}>{tele.ram.toFixed(1)}%</span>
            </div>
            <MiniBar pct={tele.ram} color={ramColor} />
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
          <span style={{ fontFamily: DIGIT, fontSize: 12, color: CYAN }}>{tele.active_users}</span>
        </div>

        {/* SDR LIST — fixed 20-slot 2-col grid */}
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

  let expandedContent;
  if (activeTab === 'history') {
    expandedContent = (
      <>
        <div style={{ display: 'grid', gridTemplateColumns: '56px 60px 1fr 50px 40px', gap: 6, padding: '6px 14px', fontSize: 9, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.22)', flexShrink: 0 }}>
          <span>{t('sigint.time')}</span><span>SDR</span><span>{t('sigint.freq')}</span>
          <span style={{ textAlign: 'right' }}>{t('sigint.dbfs')}</span>
          <span style={{ textAlign: 'right' }}>{t('sigint.snr')}</span>
        </div>
        <div className="ap-panel-scroll" style={{ flex: 1, overflowY: 'auto', padding: '0 6px 0' }}>
          {rows.map(r => (
            <HistoryRow key={r.id} row={r} isNew={newIds.has(r.id)} thresholds={thresholds} />
          ))}
        </div>
      </>
    );
  } else {
    expandedContent = (
      <div className="ap-panel-scroll" style={{ flex: 1, overflowY: 'auto', padding: '14px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* ── SDR STATUS Summary ── */}
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: sdrColor, boxShadow: `0 0 8px ${sdrColor}` }} />
              <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.5)', fontWeight: 700, letterSpacing: '0.06em' }}>{t('sigint.sdrStatus')}</span>
            </div>
            <span style={{ fontFamily: DIGIT, fontSize: 16, color: sdrColor }}>{sdrOnline}/{sdrTotal}</span>
          </div>

          {/* ── DB + WA status ── */}
          <div style={{ display: 'flex', gap: 6 }}>
            {[{ label: t('sigint.rfDb'), on: tele.db_status }, { label: t('sigint.whatsapp'), on: tele.whatsapp_status }].map(({ label, on }) => (
              <div key={label} style={{ flex: 1, background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '7px 6px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                <div style={{ width: 7, height: 7, borderRadius: '50%', background: on ? '#00e676' : '#ff5252', boxShadow: `0 0 7px ${on ? '#00e676' : '#ff5252'}` }} />
                <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.3)', fontWeight: 600, letterSpacing: '0.08em' }}>{label}</span>
                <span style={{ fontSize: 9, fontWeight: 700, color: on ? '#00e676' : '#ff5252' }}>{on ? t('common.on') : t('common.off')}</span>
              </div>
            ))}
          </div>

          {/* ── Drops 24h ── */}
          <div style={{ background: 'linear-gradient(135deg, rgba(255,175,0,0.07) 0%, rgba(255,87,34,0.04) 100%)', border: '1px solid rgba(255,175,0,0.12)', borderRadius: 10, padding: '10px 14px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>{t('sigint.drops24h')} · {t('sigint.below65')}</span>
              <span style={{ fontFamily: DIGIT, fontSize: 30, color: filteredDrops24h > 0 ? AMBER : CYAN, lineHeight: 1, textShadow: `0 0 14px ${filteredDrops24h > 0 ? AMBER : CYAN}66` }}>{(filteredDrops24h ?? 0).toLocaleString()}</span>
            </div>
            <motion.div
              animate={{ opacity: [1, 0.25, 1], filter: ['drop-shadow(0 0 6px #00e5ff)', 'drop-shadow(0 0 0px #00e5ff)', 'drop-shadow(0 0 6px #00e5ff)'] }}
              transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
              style={{ fontSize: 30, lineHeight: 1 }}
            >📡</motion.div>
          </div>

          {/* ── CPU / RAM / DB Size ── */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
            {[
              { label: t('sigint.cpu'), value: `${tele.cpu.toFixed(1)}%`, color: tele.cpu > 80 ? ORANGE : tele.cpu > 50 ? AMBER : CYAN },
              { label: t('sigint.ram'), value: `${tele.ram.toFixed(1)}%`, color: tele.ram > 85 ? ORANGE : tele.ram > 60 ? AMBER : CYAN },
              { label: t('sigint.db'), value: tele.db_mb >= 1024 ? `${(tele.db_mb/1024).toFixed(1)}G` : `${tele.db_mb}M`, color: 'rgba(255,255,255,0.55)' },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 4px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
                <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.10em', textTransform: 'uppercase' }}>{label}</span>
                <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700, color }}>{value}</span>
              </div>
            ))}
          </div>

          {/* ── Users / Req/s / Net ── */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
            {[
              { label: t('sigint.users'), value: tele.active_users, color: CYAN },
              { label: t('sigint.reqPerSec'), value: tele.batch_requests_per_sec, color: AMBER },
              { label: t('sigint.net'), value: `${tele.network_io_mbps.toFixed(1)}M`, color: 'rgba(255,255,255,0.55)' },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 4px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
                <span style={{ fontSize: 8, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.10em', textTransform: 'uppercase' }}>{label}</span>
                <span style={{ fontFamily: DIGIT, fontSize: 15, color }}>{value}</span>
              </div>
            ))}
          </div>

          <div style={{ height: 1, background: 'rgba(255,255,255,0.06)' }} />

          {/* ── Backup ── */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>{t('sigint.backup')}</span>
            <button onClick={runBackup} disabled={tele.backup_running} style={{
              background: 'rgba(0,229,255,0.10)', border: '1px solid rgba(0,229,255,0.30)',
              borderRadius: 6, color: tele.backup_running ? 'rgba(0,229,255,0.35)' : CYAN,
              fontSize: 10, fontWeight: 600, padding: '5px 11px', cursor: tele.backup_running ? 'default' : 'pointer', fontFamily: SYS, letterSpacing: '0.05em',
            }}>{tele.backup_running ? t('sigint.backupRunning') : t('sigint.runBackup')}</button>
          </div>

          {/* backup sizes — stacked rows so they always fit */}
          {[
            { label: t('sigint.local'), mb: tele.backup_local_mb, pct: null, color: 'rgba(255,255,255,0.55)' },
            { label: t('sigint.r2Cloud'), mb: tele.backup_r2_mb, pct: tele.r2_configured ? ((tele.backup_r2_mb / 10240) * 100).toFixed(1) : null, color: tele.r2_configured ? CYAN : 'rgba(255,255,255,0.2)' },
          ].map(({ label, mb, pct, color }) => (
            <div key={label} style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '7px 10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>{label}</span>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
                <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 700, color }}>{mb >= 1024 ? `${(mb/1024).toFixed(2)} GB` : `${mb} MB`}</span>
                {pct && <span style={{ fontSize: 9, color: 'rgba(0,229,255,0.5)' }}>{pct}%</span>}
                {!tele.r2_configured && label === t('sigint.r2Cloud') && <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)' }}>{t('sigint.notSet')}</span>}
              </div>
            </div>
          ))}

          {tele.backup_error ? (
            <div style={{ fontSize: 10, color: 'rgba(255,87,34,0.75)', fontFamily: MONO, wordBreak: 'break-all', lineHeight: 1.4, background: 'rgba(255,87,34,0.06)', borderRadius: 6, padding: 8 }}>
              {tele.backup_error}
            </div>
          ) : null}

          <div style={{ height: 1, background: 'rgba(255,255,255,0.06)' }} />

          {/* ── SDR LIST ── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.10em', textTransform: 'uppercase', fontWeight: 700 }}>{t('sigint.sdrList')}</span>
            <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: 8, padding: '8px 10px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 8px' }}>
              {Array.from({ length: 20 }, (_, i) => {
                const n = sdrNodes[i];
                if (n) {
                  const c = n.online ? '#00e676' : '#ff5252';
                  const ageSec = n.last_seen ? Math.floor(Date.now() / 1000 - n.last_seen) : null;
                  const ageLabel = !n.online && ageSec !== null ? (ageSec < 60 ? `${ageSec}s ago` : ageSec < 3600 ? `${Math.floor(ageSec/60)}m ago` : `${Math.floor(ageSec/3600)}h ago`) : null;
                  return (
                    <div key={n.id} style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                        <div style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: c, boxShadow: `0 0 5px ${c}` }} />
                        <span style={{ fontSize: 10, fontFamily: MONO, fontWeight: 600, color: n.online ? 'rgba(255,255,255,0.7)' : 'rgba(255,82,82,0.8)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.id}</span>
                      </div>
                      {ageLabel && <span style={{ fontSize: 8, fontFamily: MONO, color: 'rgba(255,82,82,0.5)', paddingLeft: 12 }}>{ageLabel}</span>}
                    </div>
                  );
                }
                return (
                  <div key={`empty-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <div style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: 'rgba(255,255,255,0.06)' }} />
                    <span style={{ fontSize: 10, fontFamily: MONO, color: 'rgba(255,255,255,0.1)' }}>—</span>
                  </div>
                );
              })}
            </div>
          </div>

        </div>
      </div>
    );
  }

  return (
    <>
      <motion.div
        style={{
          position: 'absolute', top: 52, left: 0, bottom: 34,
          zIndex: 10, fontFamily: SYS, userSelect: 'none',
          pointerEvents: 'auto',
          overflow: 'hidden',
          background: 'linear-gradient(180deg, rgba(8,11,18,0.95) 0%, rgba(11,15,23,0.88) 100%)',
          borderRight: '1px solid rgba(255,255,255,0.04)',
          boxShadow: activeExpanded ? '8px 0 32px rgba(0,0,0,0.45)' : 'none',
        }}
        initial={{ width: 150 }}
        animate={{ width: activeExpanded ? 320 : 150 }}
        transition={{ duration: 0.28, ease: 'easeOut' }}
        onMouseEnter={() => setActiveExpanded(true)}
        onMouseLeave={() => setActiveExpanded(false)}
      >
        {collapsedView}

        <div style={{
          position: 'absolute', inset: 0,
          opacity: activeExpanded ? 1 : 0,
          transition: 'opacity 0.2s',
          pointerEvents: activeExpanded ? 'auto' : 'none',
          display: 'flex', flexDirection: 'column',
          width: 320,
        }}>
          {/* Header */}
          <div style={{ padding: '14px 16px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: '#fff', letterSpacing: '-0.01em', marginBottom: 1 }}>{t('sigint.panel')}</div>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                {activeTab === 'history' && (
                  <button onClick={() => { setShowExport(true); tracker.track('EXPORT_OPEN', 'UI', {}); }} style={{
                    background: 'rgba(0,229,255,0.10)', border: '1px solid rgba(0,229,255,0.30)',
                    borderRadius: 7, color: CYAN, fontSize: 10, fontWeight: 600,
                    padding: '5px 11px', cursor: 'pointer', fontFamily: SYS, letterSpacing: '0.05em',
                  }}>{t('sigint.export')}</button>
                )}
              </div>
            </div>
          </div>

          {/* Tab bar */}
          <div style={{ display: 'flex', borderBottom: '1px solid rgba(255,255,255,0.05)', flexShrink: 0 }}>
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                style={{
                  flex: 1, background: 'transparent', border: 'none',
                  padding: '10px 0', cursor: 'pointer', fontFamily: SYS,
                  fontSize: 11, fontWeight: activeTab === t.id ? 700 : 500,
                  letterSpacing: '0.06em', textTransform: 'uppercase',
                  color: activeTab === t.id ? CYAN : 'rgba(255,255,255,0.30)',
                  borderBottom: `2px solid ${activeTab === t.id ? CYAN : 'transparent'}`,
                  textShadow: activeTab === t.id ? `0 0 8px ${CYAN}55` : 'none',
                  transition: 'all 0.18s',
                }}
              >{t.label}</button>
            ))}
          </div>

          {expandedContent}
        </div>
      </motion.div>

      {/* Export modal — portalled to body to escape pointerEvents:none UI layer */}
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
              <div style={{ display: 'flex', gap: 6 }}>
                {[['1h', 1], ['6h', 6], ['24h', 24], ['7d', 168]].map(([lbl, hrs]) => (
                  <button key={lbl} onClick={() => {
                    const now = new Date(); const f = new Date(now - hrs * 3600000);
                    setExportFrom(localIso(f)); setExportTo(localIso(now));
                  }} style={{
                    flex: 1, background: 'rgba(0,229,255,0.07)', border: '1px solid rgba(0,229,255,0.18)',
                    borderRadius: 8, color: 'rgba(0,229,255,0.80)', fontSize: 11, fontWeight: 600,
                    padding: '6px 0', cursor: 'pointer', fontFamily: SYS, letterSpacing: '0.04em',
                    transition: 'all 0.15s',
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
                  tracker.track('EXPORT_DOWNLOAD', 'UI', { from: exportFrom, to: exportTo });
                  try {
                    const fromTs = exportFrom ? new Date(exportFrom).getTime() / 1000 : 0;
                    const toTs = exportTo ? new Date(exportTo).getTime() / 1000 : 0;
                    const controller = new AbortController();
                    const tid = setTimeout(() => controller.abort(), 30000);
                    const res = await fetch(`/api/export?from_ts=${fromTs}&to_ts=${toTs}`, { signal: controller.signal });
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
}, (prev, next) => {
  if (!prev.active && !next.active) return true;
  return prev.tab === next.tab &&
         prev.onTabChange === next.onTabChange &&
         prev.expanded === next.expanded &&
         prev.onExpandedChange === next.onExpandedChange &&
         prev.active === next.active &&
         prev.tele === next.tele;
});

// Given history rows, find sustained drops: a frequency must stay below
// thresholdDbfs continuously for at least minDurationSec (default 60s).
// Gaps larger than gapSec between consecutive readings end a run.
function findSustainedDrops(rows, thresholdDbfs, minDurationSec = 60, gapSec = 30) {
  const groups = {};
  for (const r of rows) {
    if (!r.sdr_id || !Number.isFinite(Number(r.freq))) continue;
    const key = `${r.sdr_id}|${Number(r.freq).toFixed(1)}`;
    (groups[key] ||= []).push(r);
  }

  const events = [];
  for (const groupRows of Object.values(groups)) {
    groupRows.sort((a, b) => Number(a.ts) - Number(b.ts));
    let runStart = null;
    let runEnd = null;
    const finishRun = () => {
      if (!runStart || !runEnd) return;
      const duration = Number(runEnd.ts) - Number(runStart.ts);
      if (duration >= minDurationSec) {
        events.push({ ...runEnd, _sustainedDurationSec: duration });
      }
      runStart = null;
      runEnd = null;
    };
    for (const r of groupRows) {
      const below = Number(r.dbfs) < thresholdDbfs;
      if (!below) { finishRun(); continue; }
      if (!runStart) {
        runStart = r;
        runEnd = r;
      } else if (Number(r.ts) - Number(runEnd.ts) > gapSec) {
        finishRun();
        runStart = r;
        runEnd = r;
      } else {
        runEnd = r;
      }
    }
    finishRun();
  }
  return events.sort((a, b) => Number(b.ts) - Number(a.ts));
}

// ---------------------------------------------------------------------------
// Weather fallback for the bottom ticker
// ---------------------------------------------------------------------------
const WEATHER_COORDS = {
  'site_1': { name: 'Bakı', lat: 40.4093, lon: 49.8671 },
  'site_2': { name: 'Bakı', lat: 40.4093, lon: 49.8671 },
  'site_3': { name: 'Beyləqan', lat: 39.7720, lon: 47.6175 },
  'site_4': { name: 'Gəncə', lat: 40.6828, lon: 46.3606 },
  'site_5': { name: 'Masallı', lat: 39.0353, lon: 48.6654 },
  'site_6': { name: 'Qəbələ', lat: 40.9982, lon: 47.8700 },
  'site_7': { name: 'Site-7', lat: 41.3612, lon: 48.5124 },
  'site_8': { name: 'Şuşa', lat: 39.7667, lon: 46.7494 },
};

function weatherLabel(code) {
  if (code == null) return '—';
  if (code === 0) return 'Clear';
  if (code <= 3) return 'Partly cloudy';
  if (code <= 48) return 'Fog';
  if (code <= 55) return 'Drizzle';
  if (code <= 65) return 'Rain';
  if (code <= 75) return 'Snow';
  if (code <= 82) return 'Showers';
  if (code <= 86) return 'Snow showers';
  if (code <= 99) return 'Thunderstorm';
  return '—';
}

async function fetchRegionWeather({ name, lat, lon }) {
  try {
    const url = `/api/weather?lat=${lat}&lon=${lon}`;
    const res = await fetch(url, { credentials: 'include' });
    if (!res.ok) return null;
    const data = await res.json();
    const cw = data.current_weather;
    if (!cw) return null;
    return { name, temp: cw.temperature, code: cw.weathercode };
  } catch { return null; }
}

// ---------------------------------------------------------------------------
// DropTicker — animated bottom news-ticker for recent drop events
// ---------------------------------------------------------------------------
const DROP_THRESHOLD = -65; // dBFS
const TICKER_DURATION_MS = 90000;
const DROP_TICKER_SPEED = 0.2;
const DropTicker = memo(function DropTicker() {
  const { t } = useTranslation();
  const [items, setItems] = useState([]);
  const [weather, setWeather] = useState([]);
  const trackRef = useRef(null);
  const elapsedRef = useRef(0);
  const pausedRef = useRef(false);

  // Smooth, continuous marquee driven by elapsed time; survives item updates.
  // Pauses when the cursor is over the bar. Capped at 30fps to cut CPU usage.
  useEffect(() => {
    let rafId;
    let lastNow = performance.now();
    let lastTs = 0;
    const FRAME_MS = 1000 / 30;
    const loop = (now) => {
      rafId = requestAnimationFrame(loop);
      if (document.hidden || now - lastTs < FRAME_MS) return;
      lastTs = now;
      const delta = now - lastNow;
      lastNow = now;
      if (!pausedRef.current) {
        elapsedRef.current = (elapsedRef.current + delta * DROP_TICKER_SPEED) % TICKER_DURATION_MS;
      }
      const pct = (elapsedRef.current / TICKER_DURATION_MS) * 50; // 0% -> -50% over one loop
      if (trackRef.current) {
        trackRef.current.style.transform = `translate3d(-${pct}%, 0, 0)`;
      }
    };
    rafId = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafId);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let fetchId = null;
    const load = async () => {
      try {
        const res = await fetch('/api/drops?hours=24&min_duration=60&max_gap=30&use_calibrated=1', { credentials: 'include' });
        if (!res.ok || cancelled) return;
        const events = await res.json();
        if (cancelled) return;
        const drops = [];
        for (const r of events) {
          const info = getStationInfo(r.sdr_id, r.freq);
          if (!info) continue;
          const mainRtys = MAIN_RTYS_BY_API_ID[r.sdr_id];
          if (!mainRtys || info.rtys !== mainRtys) continue;
          const dbuv = (Number(r.dbfs) + 107).toFixed(1);
          drops.push({
            id: r.id,
            sdrId: r.sdr_id,
            rtys: info.rtys,
            name: info.name,
            freq: Number(r.freq).toFixed(1),
            dbuv,
            time: fmtTime(r.ts),
          });
          if (drops.length >= 30) break;
        }
        setItems(prev => {
          if (prev.length === drops.length && prev.every((p, i) => p.id === drops[i].id)) return prev;
          return drops;
        });
      } catch { /* ignore */ }
    };
    load();
    fetchId = setInterval(load, 30000);
    return () => { cancelled = true; clearInterval(fetchId); };
  }, [t]);

  // Fetch regional weather as a fallback when there are no sustained drops.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const results = await Promise.all(Object.values(WEATHER_COORDS).map(fetchRegionWeather));
      const nextWeather = results.filter(Boolean);
      if (!cancelled) {
        setWeather(prev => {
          if (prev.length === nextWeather.length && prev.every((p, i) => p.name === nextWeather[i].name && p.temp === nextWeather[i].temp && p.code === nextWeather[i].code)) return prev;
          return nextWeather;
        });
      }
    };
    load();
    const id = setInterval(load, 6 * 60 * 60 * 1000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const hasDrops = items.length > 0;
  const hasWeather = weather.length > 0;
  // Duplicate items for a seamless marquee loop
  const marqueeItems = hasDrops
    ? [...items, ...items]
    : hasWeather
      ? [...weather, ...weather]
      : [];

  return (
    <div style={{
      position: 'absolute',
      bottom: 0, left: 0, right: 0,
      height: 34,
      background: 'linear-gradient(90deg, rgba(5,8,14,0.98) 0%, rgba(8,12,22,0.96) 50%, rgba(5,8,14,0.98) 100%)',
      borderTop: '1px solid rgba(0,229,255,0.18)',
      boxShadow: '0 -4px 24px rgba(0,229,255,0.08)',
      overflow: 'hidden',
      display: 'flex',
      alignItems: 'center',
      zIndex: 101,
      pointerEvents: 'auto',
    }}
      onMouseEnter={() => { pausedRef.current = true; }}
      onMouseLeave={() => { pausedRef.current = false; }}
    >
      <div style={{ flex: 1, overflow: 'hidden', maskImage: 'linear-gradient(90deg, transparent 0%, black 8%, black 92%, transparent 100%)' }}>
        <div ref={trackRef} style={{
          display: 'inline-flex',
          alignItems: 'center',
          whiteSpace: 'nowrap',
          willChange: 'transform',
        }}>
          {hasDrops ? marqueeItems.map((item, i) => (
            <span key={`${item.id}-${i}`} style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              padding: '0 28px',
              fontFamily: MONO,
              fontSize: 12,
              color: 'rgba(255,255,255,0.80)',
              letterSpacing: '0.02em',
            }}>
              <span style={{ color: CYAN, fontWeight: 600 }}>{item.sdrId}</span>
              <span style={{ color: 'rgba(255,255,255,0.25)' }}>—</span>
              <span style={{ color: AMBER }}>{item.rtys}</span>
              <span style={{ color: 'rgba(255,255,255,0.25)' }}>—</span>
              <span>{item.name}</span>
              <span style={{ color: 'rgba(255,255,255,0.25)' }}>—</span>
              <span style={{ color: CYAN, fontWeight: 600 }}>{item.freq} MHz</span>
              <span style={{ color: 'rgba(255,255,255,0.25)' }}>—</span>
              <span style={{ color: '#ff7043', fontWeight: 700 }}>{item.dbuv} dBµV</span>
              <span style={{ color: 'rgba(255,255,255,0.25)' }}>—</span>
              <span style={{ color: 'rgba(255,255,255,0.45)' }}>{item.time}</span>
            </span>
          )) : hasWeather ? marqueeItems.map((item, i) => (
            <span key={`w-${i}`} style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              padding: '0 28px',
              fontFamily: MONO,
              fontSize: 12,
              color: 'rgba(255,255,255,0.80)',
              letterSpacing: '0.02em',
            }}>
              <span style={{ color: CYAN, fontWeight: 600 }}>{item.name}</span>
              <span style={{ color: 'rgba(255,255,255,0.25)' }}>—</span>
              <span style={{ color: '#ff7043', fontWeight: 700 }}>{item.temp.toFixed(1)}°C</span>
              <span style={{ color: 'rgba(255,255,255,0.25)' }}>—</span>
              <span>{weatherLabel(item.code)}</span>
            </span>
          )) : (
            <span style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              padding: '0 28px',
              fontFamily: MONO,
              fontSize: 12,
              color: 'rgba(255,255,255,0.35)',
              letterSpacing: '0.02em',
            }}>
              {t('sigint.noDropsOrWeather')}
            </span>
          )}
        </div>
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------
// SDR → RTYS master data (sourced from SDR-RTYS.md)
// stations: { rtysName: [ [freq, 'StationName'], ... ] }
// rtys array is auto-sorted high→low by station count via _buildSdrEntry()
// ---------------------------------------------------------------------------
function _buildSdrEntry(label, stationsRaw) {
  // stationsRaw: { rtysName: [[freq, name], ...] }
  const stations = {};
  for (const [rtys, entries] of Object.entries(stationsRaw)) {
    // deduplicate by freq key
    const seen = new Set();
    stations[rtys] = entries.filter(([f]) => {
      const k = f.toFixed(3); if (seen.has(k)) return false; seen.add(k); return true;
    });
  }
  const rtys = Object.keys(stations).sort((a, b) => stations[b].length - stations[a].length);
  return { label, rtys, stations };
}

const SDR_RTYS_MAP = {
  site_1: _buildSdrEntry('Site-1 (Günəşli)', {
    'BTQK': [
      [88.7,'Planet'],[90.0,'İctimai'],[90.7,'Yurd'],[92.5,'Dalğa'],[93.0,'Sağlam'],[93.3,'Retro'],
      [97.7,'Hit'],[99.3,'XY(İctimai)'],[100.0,'Asan'],[100.5,'TMB'],[101.1,'Enerji'],[101.3,'XY'],
      [103.0,'Xəzər'],[103.3,'Araz'],[104.0,'Space'],[105.0,'Azərbaycan'],[105.5,'Media'],
      [106.0,'A.Azərb.'],[106.3,'A.Azərb.'],[107.0,'Real'],[107.7,'Avto'],[223.936,'DAB+'],
    ],
    'Gilgilçay': [
      [93.6,'A.Azərb.'],[95.4,'Space'],[95.7,'TMB'],[96.0,'Real'],[97.2,'Avto'],[98.5,'Media'],[99.0,'Asan'],[100.8,'Araz'],
    ],
    'İran kənar': [
      [91.9,'Varzesh'],[93.9,'Ardabil'],[95.9,'Maaref'],[97.9,'Javan'],[98.3,'Egtesad'],[101.9,'Farhang'],
    ],
    'Lerik RTYS': [
      [87.7,'Hit'],[89.0,'Xəzər'],[89.3,'İctimai'],[91.3,'Real'],[92.7,'Asan'],[93.7,'Yurd'],
      [96.5,'Retro'],[101.5,'Azərb.'],[102.3,'Enerji'],[102.8,'TMB'],[103.5,'Araz'],[104.8,'A.Azərb.'],
      [105.3,'Space'],[105.7,'Media'],[107.5,'Avto'],
    ],
    'Türkmənistan kənar': [[99.7,'Türkmənistan'],[99.8,'Miras']],
    'Xizi RTYS':  [[89.6,'Azərb.'],[103.8,'İctimai']],
    'Yergüc RTYS':[[88.0,'Hit'],[90.3,'Media'],[91.7,'Retro'],[92.3,'Asan'],[94.1,'Energi'],[94.9,'Space'],[98.1,'İctimai'],[98.7,'Avto'],[104.5,'Azərb.']],
  }),
  site_2: _buildSdrEntry('Günəşli 2', {
    'BTQK': [
      [88.7,'Planet'],[90.0,'İctimai'],[90.7,'Yurd'],[92.5,'Dalğa'],[93.0,'Sağlam'],[93.3,'Retro'],
      [97.7,'Hit'],[99.3,'XY(İctimai)'],[100.0,'Asan'],[100.5,'TMB'],[101.1,'Enerji'],[101.3,'XY'],
      [103.0,'Xəzər'],[103.3,'Araz'],[104.0,'Space'],[105.0,'Azərbaycan'],[105.5,'Media'],
      [106.0,'A.Azərb.'],[106.3,'A.Azərb.'],[107.0,'Real'],[107.7,'Avto'],[223.936,'DAB+'],
    ],
    'Gilgilçay': [
      [93.6,'A.Azərb.'],[95.4,'Space'],[95.7,'TMB'],[96.0,'Real'],[97.2,'Avto'],[98.5,'Media'],[99.0,'Asan'],[100.8,'Araz'],
    ],
    'İran kənar': [
      [91.9,'Varzesh'],[93.9,'Ardabil'],[95.9,'Maaref'],[97.9,'Javan'],[98.3,'Egtesad'],[101.9,'Farhang'],
    ],
    'Lerik RTYS': [
      [87.7,'Hit'],[89.0,'Xəzər'],[89.3,'İctimai'],[91.3,'Real'],[92.7,'Asan'],[93.7,'Yurd'],
      [96.5,'Retro'],[101.5,'Azərb.'],[102.3,'Enerji'],[102.8,'TMB'],[103.5,'Araz'],[104.8,'A.Azərb.'],
      [105.3,'Space'],[105.7,'Media'],[107.5,'Avto'],
    ],
    'Türkmənistan kənar': [[99.7,'Türkmənistan'],[99.8,'Miras']],
    'Xizi RTYS':  [[89.6,'Azərb.'],[103.8,'İctimai']],
    'Yergüc RTYS':[[88.0,'Hit'],[90.3,'Media'],[91.7,'Retro'],[92.3,'Asan'],[94.1,'Energi'],[94.9,'Space'],[98.1,'İctimai'],[98.7,'Avto'],[104.5,'Azərb.']],
  }),
  site_3: _buildSdrEntry('Beyləqan', {
    'Şuşa RTYS': [
      [88.7,'İctimai'],[89.2,'Xəzər'],[93.5,'TMB'],[96.0,'Yurd'],[97.1,'Hit'],[98.3,'A.Azərb.'],
      [98.6,'Araz'],[99.0,'Real'],[99.4,'Asan'],[100.7,'Retro'],[101.7,'Azərb.'],[102.7,'CBC'],
      [104.3,'Enerji'],[105.1,'Beynəlx.'],[105.5,'Media'],[106.3,'Space'],[107.3,'Avto'],
    ],
    'Gülüstan RTYS': [
      [87.5,'Space'],[88.0,'Azərb.'],[89.6,'Xəzər'],[90.5,'A.Azərb.'],[91.0,'TMB'],[91.8,'Yurd'],
      [95.1,'Retro'],[95.6,'Asan'],[97.3,'Araz'],[99.6,'Hit'],[101.3,'Enerji'],[102.5,'İctimai'],[106.7,'Avto'],[107.0,'Media'],
    ],
    'İran (kənar yayım)': [
      [88.3,'Javan'],[89.9,'İran'],[91.9,'Varzesh'],[93.9,'Ardabil'],[94.4,'Sh.Azərb.'],
      [95.9,'Maaref'],[97.9,'Javan'],[99.3,'İRİB Azeri'],[99.9,'Quran'],[101.9,'Farhang'],[103.9,'Payam'],[106.4,'Farhang'],
    ],
    'Lerik RTYS': [
      [87.7,'Hit'],[89.0,'Xəzər'],[89.3,'İctimai'],[92.7,'Asan'],[93.7,'Yurd'],
      [96.5,'Retro'],[101.5,'Azərb.'],[102.3,'Enerji'],[102.8,'TMB'],[103.5,'Araz'],[104.8,'A.Azərb.'],[105.3,'Space'],[107.5,'Avto'],
    ],
    'Təpəcənnət RTYS': [[91.6,'İctimai'],[93.0,'Araz'],[94.6,'Azərb.'],[94.9,'Yurd'],[96.3,'Xəzər'],[96.9,'Media'],[104.1,'Enerji'],[107.8,'Avto']],
    'Daşkəsən RTYS': [[93.3,'Kəpəz'],[100.5,'TMB'],[103.7,'Avto']],
    'İvanovka RTYS': [[98.8,'Real']],
    'Turşsu RTYS':   [[91.3,'İctimai'],[105.7,'Azərb.']],
  }),
  site_4: _buildSdrEntry('Gəncə', {
    'Daşkəsən RTYS': [
      [88.3,'Retro'],[89.0,'Xəzər'],[90.0,'İctimai'],[92.0,'Hit'],[93.3,'Kəpəz'],[93.6,'Real'],
      [95.8,'Yurd'],[96.5,'Asan'],[99.2,'Space'],[100.5,'TMB'],[101.0,'Enerji'],[101.5,'Azərb.'],
      [103.3,'Araz'],[103.7,'Avto'],[105.3,'Media'],[107.5,'A.Azərb.'],
    ],
    'Təpəcənnət RTYS': [
      [87.7,'TMB'],[91.6,'İctimai'],[92.4,'Real'],[93.0,'Araz'],[94.6,'Azərb.'],[94.9,'Yurd'],
      [96.3,'Xəzər'],[96.9,'Media'],[99.8,'Asan'],[102.0,'A.Azərb.'],[104.1,'Enerji'],[107.8,'Avto'],
    ],
    'Poylu RTYS': [
      [87.5,'Xəzər'],[88.4,'Retro'],[88.7,'Yurd'],[89.4,'Space'],[90.9,'Hit'],[91.5,'Media'],
      [96.1,'Real'],[97.0,'Araz'],[100.0,'Asan'],[102.7,'Kəpəz'],[103.2,'İctimai'],[107.7,'Avto'],
    ],
    'Gülüstan RTYS': [
      [88.0,'Azərb.'],[89.6,'Xəzər'],[90.5,'A.Azərb.'],[91.0,'TMB'],[91.8,'Yurd'],
      [95.1,'Retro'],[95.6,'Asan'],[97.3,'Araz'],[99.6,'Hit'],[101.3,'Enerji'],[102.5,'İctimai'],[106.7,'Avto'],[107.0,'Media'],
    ],
    'Gürcüstan kənar': [[91.1,'Eti1'],[100.3,'İmedi'],[104.7,'İmedi']],
    'Gəncə RTYS':      [[94.3,'A.Azərb.'],[106.0,'Space']],
    'İvanovka RTYS':   [[98.8,'Real']],
  }),
  site_5: _buildSdrEntry('Masallı', {
    'İran (kənar)': [
      [88.3,'Javan'],[89.9,'İran'],[90.3,'İran'],[91.9,'Varzesh'],[92.3,'Varzesh'],[93.9,'Ardabil'],
      [94.1,'Ardabil'],[95.9,'Maaref'],[96.1,'Maaref'],[97.9,'Javan'],[98.3,'Eghtesad'],
      [99.3,'İRİB Azeri'],[99.9,'Quran'],[100.3,'Quran'],[101.3,'İRİB Azeri'],[101.9,'Farhang'],
      [103.9,'Payam'],[104.3,'Payam'],[106.3,'Farhang'],
    ],
    'Lerik RTYS': [
      [87.7,'Hit'],[89.0,'Xəzər'],[89.3,'İctimai'],[91.3,'Real'],[92.7,'Asan'],[93.7,'Yurd'],
      [96.5,'Retro'],[101.5,'Azərb.'],[102.3,'Enerji'],[102.8,'TMB'],[103.5,'Araz'],[104.8,'A.Azərb.'],
      [105.3,'Space'],[105.7,'Media'],[107.5,'Avto'],
    ],
    'Astara RTYS': [
      [88.5,'Avto'],[89.5,'Enerji'],[91.5,'Space'],[93.5,'Azərb.'],[94.3,'Real'],[96.7,'Hit'],
      [97.2,'Xəzər'],[97.8,'TMB'],[98.1,'A.Azərb.'],[98.5,'Media'],[99.5,'Retro'],[102.0,'Asan'],[104.6,'İctimai'],[107.3,'Araz'],
    ],
    'Lənkəran RTYS': [[104.0,'Space'],[107.8,'A.Azərb.']],
  }),
  site_6: _buildSdrEntry('Qəbələ', {
    'Şuşa RTYS': [
      [88.7,'İctimai'],[89.2,'Xəzər'],[93.5,'TMB'],[96.0,'Yurd'],[97.1,'Hit'],[98.3,'A.Azərb.'],
      [98.6,'Araz'],[99.0,'Real'],[99.4,'Asan'],[100.7,'Retro'],[101.7,'Azərb.'],[102.7,'CBC'],
      [104.3,'Enerji'],[105.1,'Beynəlx.'],[105.5,'Media'],[106.3,'Space'],[107.3,'Avto'],
    ],
    'Təpəcənnət RTYS': [
      [87.7,'TMB'],[91.6,'İctimai'],[92.4,'Real'],[93.0,'Araz'],[94.6,'Azərb.'],[94.9,'Yurd'],
      [96.3,'Xəzər'],[96.9,'Media'],[99.8,'Asan'],[102.0,'A.Azərb.'],[104.1,'Enerji'],[107.8,'Avto'],
    ],
    'Daşkəsən RTYS': [
      [88.3,'Retro'],[90.0,'İctimai'],[92.0,'Hit'],[93.3,'Kəpəz'],[93.6,'Real'],[95.8,'Yurd'],
      [96.5,'Asan'],[99.2,'Space'],[100.5,'TMB'],[101.0,'Enerji'],[101.5,'Azərb.'],[103.3,'Araz'],[103.7,'Avto'],[107.5,'A.Azərb.'],
    ],
    'Gülüstan RTYS': [
      [87.5,'Space'],[88.0,'Azərb.'],[89.6,'Xəzər'],[90.5,'A.Azərb.'],[91.0,'TMB'],[91.8,'Yurd'],
      [95.1,'Retro'],[95.6,'Asan'],[97.3,'Araz'],[99.6,'Hit'],[101.3,'Enerji'],[102.5,'İctimai'],[106.7,'Avto'],[107.0,'Media'],
    ],
    'İvanovka RTYS': [
      [89.0,'Xəzər'],[89.3,'İctimai'],[92.7,'Asan'],[93.7,'Yurd'],[98.8,'Real'],
      [102.3,'Enerji'],[102.8,'TMB'],[103.5,'Araz'],[104.8,'A.Azərb.'],[105.3,'Space'],[105.7,'Media'],
    ],
    'İran (kənar yayim)': [
      [89.9,'İran'],[91.9,'Varzesh'],[93.9,'Ardabil'],[94.4,'Sh.Azərb.'],[95.9,'Maaref'],
      [97.9,'Javan'],[99.3,'İRİB Azeri'],[99.9,'Quran'],[101.9,'Farhang'],[103.9,'Payam'],[106.4,'Farhang'],
    ],
    'Şahyeri RTYS': [
      [90.7,'Avto'],[92.1,'A.Azərb.'],[92.5,'İctimai'],[95.3,'Asan'],[96.7,'TMB'],
      [97.5,'Azərb.'],[97.8,'Retro'],[98.1,'Enerji'],[103.0,'Xəzər'],[105.9,'Media'],
    ],
    'Gəncə RTYS':        [[94.3,'A.Azərb.'],[106.0,'Space']],
    'Şirvan RTYS':       [[106.5,'Azərb.']],
    'Ermənistan kənar':  [[90.3,'Yerevan']],
  }),
  site_7: _buildSdrEntry('Site-7', {
    'Yergüc RTYS': [
      [87.7,'Yurd'],[88.0,'Hit'],[88.6,'Xəzər'],[89.3,'Real'],[89.9,'TMB'],[90.3,'Media'],
      [91.7,'Retro'],[92.3,'Asan'],[92.9,'Araz'],[94.1,'Energi'],[94.9,'Space'],
      [98.1,'İctimai'],[98.7,'Avto'],[99.7,'A.Azərb.'],[104.5,'Azərb.'],
    ],
    'Rusiya kənar': [
      [93.1,'Assa'],[98.4,'Zvezda'],[99.4,'Avto'],[100.1,'Priboy'],[101.8,'7.Nebo'],
      [102.7,'Vatan'],[103.7,'Mayak'],[104.3,'Vatan'],[104.9,'Rossiya'],[106.0,'Strana'],[106.4,'Dorojnoye'],[106.8,'Vesti'],
    ],
    'Şahdağ ARTS': [
      [89.7,'İctimai'],[93.3,'Retro'],[93.7,'Real'],[97.0,'Media'],[97.6,'Avto'],
      [100.5,'TMB'],[102.0,'Xəzər'],[102.5,'Asan'],[105.8,'Azərb.'],[107.0,'Hit'],[107.3,'A.Azərb.'],[107.7,'Yurd'],
    ],
    'Site-7 RTYS': [
      [89.0,'Real'],[90.9,'Retro'],[92.1,'İctimai'],[92.6,'Yurd'],[94.3,'Space'],
      [96.5,'Asan'],[101.0,'Enerji'],[102.3,'Avto'],[103.0,'Xəzər'],[103.3,'Araz'],[105.5,'Media'],[106.3,'A.Azərb.'],
    ],
    'Cibir RTYS': [[88.3,'İctimai'],[101.5,'Azərb.']],
  }),
  site_8: _buildSdrEntry('Şuşa', {
    'Şuşa RTYS': [
      [88.7,'İctimai'],[89.2,'Xəzər'],[93.5,'TMB'],[96.0,'Yurd'],[97.1,'Hit'],[98.3,'A.Azərb.'],
      [98.6,'Araz'],[99.0,'Real'],[99.4,'Asan'],[100.7,'Retro'],[101.7,'Azərb.'],[102.7,'CBC'],
      [104.3,'Enerji'],[105.1,'Beynəlx.'],[105.5,'Media'],[106.3,'Space'],[107.3,'Avto'],
    ],
    'Gülüstan RTYS': [
      [87.5,'Space'],[88.0,'Azərb.'],[89.6,'Xəzər'],[90.5,'A.Azərb.'],[91.0,'TMB'],[91.8,'Yurd'],
      [95.1,'Retro'],[95.6,'Asan'],[97.3,'Araz'],[99.6,'Hit'],[101.3,'Enerji'],[102.5,'İctimai'],[106.7,'Avto'],[107.0,'Media'],
    ],
    'Təpəcənnət RTYS': [
      [87.7,'TMB'],[91.6,'İctimai'],[92.4,'Real'],[93.0,'Araz'],[94.6,'Azərb.'],[94.9,'Yurd'],
      [96.3,'Xəzər'],[96.9,'Media'],[99.8,'Asan'],[102.0,'A.Azərb.'],[104.1,'Enerji'],[107.8,'Avto'],
    ],
    'Şahyeri RTYS': [
      [90.7,'Avto'],[92.1,'A.Azərb.'],[92.5,'İctimai'],[95.3,'Asan'],[96.7,'TMB'],
      [97.5,'Azərb.'],[97.8,'Retro'],[98.1,'Enerji'],[103.0,'Xəzər'],[103.3,'Araz'],[105.9,'Media'],
    ],
    'İvanovka RTYS': [[98.8,'Real']],
  }),
};

const SDR_IDS = Object.keys(SDR_RTYS_MAP);

const MAIN_RTYS_BY_API_ID = {
  'site_1': 'BTQK',
  'site_2': 'BTQK',
  'site_3': 'Şuşa RTYS',
  'site_4': 'Daşkəsən RTYS',
  'site_5': 'Lerik RTYS',
  'site_6': 'Şuşa RTYS',
  'site_7': 'Yergüc RTYS',
  'site_8': 'Şuşa RTYS',
};

function TopBar({
  activeMainTab, setActiveMainTab,
  analyticsTab, setAnalyticsTab,
  adminTab, setAdminTab,
  onProfileOpen,
  selectedSdrId, setSelectedSdrId,
  selectedRtys, setSelectedRtys,
  sdrNodes,
}) {
  const sdrOnlineMap = Object.fromEntries((sdrNodes || []).map(n => [n.id, n.online]));
  const { logout, user } = useAuth();
  const { t, lang, toggleLanguage } = useTranslation();

  const MAIN = [
    { id: 'dashboard', label: t('top.dashboard') },
    { id: 'home',      label: t('top.home') },
    { id: 'analytics', label: t('top.analytics') },
    ...(user?.is_admin ? [{ id: 'admin', label: t('top.admin') }] : []),
  ];

  const currentSdrData = SDR_RTYS_MAP[selectedSdrId] || SDR_RTYS_MAP[SDR_IDS[0]];
  const rtysNames = currentSdrData?.rtys || [];

  const SUB_ANALYTICS = [
    { id: 'overview', label: 'Overview',         action: () => { setActiveMainTab('analytics'); setAnalyticsTab(0); } },
    { id: 'spectrum', label: t('top.spectrum'), action: () => { setActiveMainTab('analytics'); setAnalyticsTab(1); } },
  ];

  const SUB_ADMIN = [
    { id: 'session',     label: t('top.session'),     action: () => { setAdminTab(0); setActiveMainTab('admin'); } },
    { id: 'users',       label: t('top.users'),       action: () => { setAdminTab(1); setActiveMainTab('admin'); } },
    { id: 'roles',       label: t('top.roles'),       action: () => { setAdminTab(2); setActiveMainTab('admin'); } },
    { id: 'audit',       label: t('top.audit'),       action: () => { setAdminTab(3); setActiveMainTab('admin'); } },
    { id: 'activity',    label: t('top.activity'),    action: () => { setAdminTab(4); setActiveMainTab('admin'); } },
    { id: 'whatsapp',    label: t('top.whatsapp'),    action: () => { setAdminTab(5); setActiveMainTab('admin'); } },
    { id: 'backup',      label: t('top.backup'),     action: () => { setAdminTab(6); setActiveMainTab('admin'); } },
    { id: 'broadcast',   label: t('top.broadcast'),  action: () => { setAdminTab(7); setActiveMainTab('admin'); } },
    { id: 'mapdots',     label: t('top.mapDots'),    action: () => { setAdminTab(8); setActiveMainTab('admin'); } },
  ];

  const subMap = {
    home: [],
    dashboard: [],
    analytics: SUB_ANALYTICS,
    admin: SUB_ADMIN,
  };
  const subTabs = subMap[activeMainTab] || [];

  const activeSubId = activeMainTab === 'analytics'
    ? (analyticsTab === 0 ? 'overview' : 'spectrum')
    : activeMainTab === 'admin'
    ? SUB_ADMIN[adminTab]?.id
    : null;

  const handleMain = (id) => {
    if (id === 'admin') {
      tracker.track('ADMIN_PANEL_OPEN', 'UI', {});
    } else if (id === 'analytics') {
      tracker.track('ANALYTICS_OPEN', 'UI', {});
    } else if (id === 'dashboard') {
      tracker.track('DASHBOARD_OPEN', 'UI', {});
    } else {
      tracker.track('HOME_OPEN', 'UI', {});
    }
    setActiveMainTab(id);
  };

  return (
    <>
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, zIndex: 100,
        pointerEvents: 'none',
      }}>
        <div style={{
          position: 'relative',
          zIndex: 1,
          background: 'linear-gradient(180deg, rgba(8,11,17,0.98) 0%, rgba(13,17,25,0.92) 50%, rgba(8,11,17,0.78) 100%)',
          backdropFilter: 'blur(6px)',
          WebkitBackdropFilter: 'blur(6px)',
          pointerEvents: 'auto',
          fontFamily: SYS,
          userSelect: 'none',
          borderBottom: '1px solid rgba(255,255,255,0.03)',
          boxShadow: '0 10px 40px rgba(0,0,0,0.45), inset 0 -1px 0 rgba(255,255,255,0.02)',
        }}>
          <div style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            zIndex: 0,
            pointerEvents: 'none',
            opacity: 0.35,
          }}>
            <AsciiWave color="#00e5ff" speed={0.5} />
          </div>
          {/* Main row */}
          <div style={{
            position: 'relative', zIndex: 1,
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            height: 52,
            padding: '0 24px',
          }}>
            {/* Left: logo + status */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%', backgroundColor: '#00e5ff',
                animation: 'ap-pulse 2.4s ease-in-out infinite', display: 'inline-block',
                boxShadow: '0 0 10px rgba(0,229,255,0.6)',
              }} />
              <span style={{
                fontSize: 14, fontWeight: 700, color: 'rgba(255,255,255,0.92)',
                letterSpacing: '0.06em', textTransform: 'uppercase',
              }}>
                cgrep
              </span>
            </div>

            {/* Center: CS2-style tabs — always truly centred via absolute positioning */}
            <div style={{ position: 'absolute', left: 0, right: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}>
            <div style={{ display: 'flex', alignItems: 'center', pointerEvents: 'auto' }}>
              {MAIN.map(tab => {
                const active = activeMainTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => handleMain(tab.id)}
                    style={{
                      position: 'relative',
                      background: 'transparent', border: 'none',
                      color: active ? CYAN : 'rgba(255,255,255,0.45)',
                      fontSize: 13, fontWeight: 700,
                      letterSpacing: '0.08em', textTransform: 'uppercase',
                      padding: '17px 26px',
                      cursor: 'pointer', fontFamily: SYS,
                      transition: 'color 0.18s',
                      textShadow: active ? `0 0 12px ${CYAN}66` : 'none',
                    }}
                    onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'rgba(255,255,255,0.80)'; }}
                    onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'rgba(255,255,255,0.45)'; }}
                  >
                    {tab.label}
                    {active && (
                      <motion.span
                        layoutId="cs2-active-tab"
                        style={{
                          position: 'absolute', bottom: 0, left: '10%', right: '10%', height: 3,
                          background: `linear-gradient(90deg, transparent 0%, ${CYAN} 50%, transparent 100%)`,
                          borderRadius: 2,
                          boxShadow: `0 0 12px ${CYAN}88`,
                        }}
                      />
                    )}
                  </button>
                );
              })}
            </div>
            </div>

            {/* Right: user / language toggle / profile / logout */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {user && (
                <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.04em' }}>
                  {user.display_name || user.username}
                </span>
              )}
              <button
                onClick={toggleLanguage}
                title={lang === 'en' ? 'Switch to Regioni' : 'Switch to English'}
                style={{
                  background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.12)',
                  borderRadius: 6, color: 'rgba(255,255,255,0.55)', fontSize: 10,
                  fontFamily: SYS, fontWeight: 700, letterSpacing: '0.05em',
                  cursor: 'pointer', padding: '5px 9px', transition: 'all 0.18s', minWidth: 34,
                }}
                onMouseEnter={e => { e.currentTarget.style.background='rgba(255,255,255,0.10)'; e.currentTarget.style.color='rgba(255,255,255,0.85)'; }}
                onMouseLeave={e => { e.currentTarget.style.background='rgba(255,255,255,0.04)'; e.currentTarget.style.color='rgba(255,255,255,0.55)'; }}
              >
                {lang === 'en' ? 'EN' : 'AZ'}
              </button>
              {user && (
                <button
                  onClick={onProfileOpen}
                  style={{
                    background: 'rgba(0,229,255,0.06)', border: '1px solid rgba(0,229,255,0.18)',
                    borderRadius: 6, color: 'rgba(0,229,255,0.65)', fontSize: 10,
                    fontFamily: SYS, fontWeight: 700, letterSpacing: '0.1em',
                    cursor: 'pointer', padding: '5px 11px', transition: 'all 0.18s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background='rgba(0,229,255,0.13)'; e.currentTarget.style.color=CYAN; }}
                  onMouseLeave={e => { e.currentTarget.style.background='rgba(0,229,255,0.06)'; e.currentTarget.style.color='rgba(0,229,255,0.65)'; }}
                >
                  {t('top.profile')}
                </button>
              )}
              {user && (
                <button
                  onClick={logout}
                  style={{
                    background: 'rgba(255,87,34,0.08)', border: 'none',
                    borderRadius: 6, color: 'rgba(255,87,34,0.70)', fontSize: 10,
                    fontFamily: SYS, fontWeight: 700, letterSpacing: '0.1em',
                    cursor: 'pointer', padding: '5px 11px', transition: 'all 0.18s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background='rgba(255,87,34,0.16)'; e.currentTarget.style.color='#ff7043'; }}
                  onMouseLeave={e => { e.currentTarget.style.background='rgba(255,87,34,0.08)'; e.currentTarget.style.color='rgba(255,87,34,0.70)'; }}
                >
                  {t('top.logout')}
                </button>
              )}
            </div>
          </div>

          {/* Functional submenu row — analytics & admin */}
          {subTabs.length > 0 && (
            <div style={{
              position: 'relative', zIndex: 1,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              gap: 28,
              height: 34,
              background: 'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.035) 50%, rgba(255,255,255,0) 100%)',
              borderTop: '1px solid rgba(255,255,255,0.025)',
            }}>
              {subTabs.map(sub => {
                const active = activeSubId === sub.id;
                return (
                  <button
                    key={sub.id}
                    onClick={sub.action}
                    style={{
                      background: 'transparent', border: 'none',
                      fontSize: 10, fontWeight: active ? 700 : 500,
                      letterSpacing: '0.08em', textTransform: 'uppercase',
                      color: active ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.30)',
                      cursor: 'pointer', fontFamily: SYS,
                      padding: '9px 14px',
                      transition: 'color 0.18s',
                      textShadow: active ? '0 0 8px rgba(255,255,255,0.25)' : 'none',
                    }}
                    onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'rgba(255,255,255,0.60)'; }}
                    onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'rgba(255,255,255,0.30)'; }}
                  >
                    {sub.label}
                  </button>
                );
              })}
            </div>
          )}

          {/* SDR subbar — shown only on Dashboard */}
          {activeMainTab === 'dashboard' && (
            <div style={{
              position: 'relative', zIndex: 1,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              gap: 4,
              height: 34,
              background: 'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.035) 50%, rgba(255,255,255,0) 100%)',
              borderTop: '1px solid rgba(255,255,255,0.025)',
              overflowX: 'auto',
              paddingLeft: 16, paddingRight: 16,
            }}>
              {SDR_IDS.map(id => {
                const active = selectedSdrId === id;
                // undefined = not yet loaded (assume online); false = confirmed offline
                const online = sdrOnlineMap[id] !== false;
                const offlineGray = !online;
                return (
                  <button
                    key={id}
                    onClick={() => { setSelectedSdrId(id); setSelectedRtys(SDR_RTYS_MAP[id].rtys[0]); }}
                    style={{
                      background: active
                        ? (offlineGray ? 'rgba(255,255,255,0.06)' : 'rgba(0,229,255,0.10)')
                        : 'transparent',
                      border: active
                        ? (offlineGray ? '1px solid rgba(255,255,255,0.15)' : '1px solid rgba(0,229,255,0.30)')
                        : '1px solid transparent',
                      borderRadius: 5,
                      fontSize: 10, fontWeight: active ? 700 : 500,
                      letterSpacing: '0.07em', textTransform: 'lowercase',
                      color: active
                        ? (offlineGray ? 'rgba(255,255,255,0.45)' : CYAN)
                        : (offlineGray ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.32)'),
                      cursor: 'pointer', fontFamily: SYS,
                      padding: '5px 13px',
                      transition: 'all 0.18s',
                      textShadow: active && !offlineGray ? `0 0 8px ${CYAN}55` : 'none',
                      whiteSpace: 'nowrap',
                      filter: offlineGray ? 'grayscale(1)' : 'none',
                      display: 'flex', alignItems: 'center', gap: 5,
                    }}
                    onMouseEnter={e => { if (!active) { e.currentTarget.style.color = offlineGray ? 'rgba(255,255,255,0.35)' : 'rgba(255,255,255,0.65)'; e.currentTarget.style.background='rgba(255,255,255,0.04)'; } }}
                    onMouseLeave={e => { if (!active) { e.currentTarget.style.color = offlineGray ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.32)'; e.currentTarget.style.background='transparent'; } }}
                  >
                    {offlineGray && (
                      <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'rgba(255,255,255,0.25)', flexShrink: 0, display: 'inline-block' }} />
                    )}
                    {SDR_RTYS_MAP[id].label}
                  </button>
                );
              })}
            </div>
          )}

          {/* RTYS subbar — shown only on Dashboard */}
          {activeMainTab === 'dashboard' && rtysNames.length > 0 && (
            <div style={{
              position: 'relative', zIndex: 1,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              gap: 4,
              height: 34,
              background: 'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(0,229,255,0.018) 50%, rgba(255,255,255,0) 100%)',
              borderTop: '1px solid rgba(0,229,255,0.06)',
              overflowX: 'auto',
              paddingLeft: 16, paddingRight: 16,
            }}>
              {rtysNames.map(name => {
                const active = selectedRtys === name;
                return (
                  <button
                    key={name}
                    onClick={() => setSelectedRtys(name)}
                    style={{
                      background: active ? 'rgba(0,229,255,0.07)' : 'transparent',
                      border: active ? '1px solid rgba(0,229,255,0.22)' : '1px solid transparent',
                      borderRadius: 5,
                      fontSize: 10, fontWeight: active ? 700 : 400,
                      letterSpacing: '0.05em',
                      color: active ? 'rgba(0,229,255,0.90)' : 'rgba(255,255,255,0.25)',
                      cursor: 'pointer', fontFamily: SYS,
                      padding: '5px 10px',
                      transition: 'all 0.18s',
                      whiteSpace: 'nowrap',
                      display: 'flex', alignItems: 'center', gap: 5,
                    }}
                    onMouseEnter={e => { if (!active) { e.currentTarget.style.color='rgba(255,255,255,0.60)'; e.currentTarget.style.background='rgba(255,255,255,0.03)'; } }}
                    onMouseLeave={e => { if (!active) { e.currentTarget.style.color='rgba(255,255,255,0.25)'; e.currentTarget.style.background='transparent'; } }}
                  >
                    <span>{name}</span>
                    <span style={{
                      fontSize: 9, fontWeight: 700,
                      color: active ? CYAN : 'rgba(255,255,255,0.18)',
                      background: active ? 'rgba(0,229,255,0.12)' : 'rgba(255,255,255,0.06)',
                      borderRadius: 3, padding: '1px 4px', lineHeight: 1.4,
                    }}>{currentSdrData?.stations?.[name]?.length ?? 0}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default function App() {
  const { user: authUser } = useAuth();
  const { t } = useTranslation();
  const isAdmin = authUser?.is_admin === true;
  const [annotationDots,  setAnnotationDots]  = useState([]);
  const [annotationScale, setAnnotationScale] = useState(1.0);
  const [dotsReady,       setDotsReady]       = useState(false);
  const [introOpacity,    setIntroOpacity]    = useState(1.0);   // black overlay: 1=hidden map, 0=visible
  const [dotAnimOpacity,  setDotAnimOpacity]  = useState(0.40);  // sprite opacity during intro
  const dotsReadyRef = useRef(false);
  const noop = useCallback(() => {}, []);
  const handleMapReady = useCallback(() => {
    if (dotsReadyRef.current) return;
    dotsReadyRef.current = true;
    setDotsReady(true);
    setTimeout(() => setIntroOpacity(0), 50);
    const START = 1.0, END = 0.40, DURATION = 1500;
    const t0 = performance.now();
    let lastTs = 0;
    const FRAME_MS = 1000 / 20;
    const tick = (now) => {
      if (document.hidden) { requestAnimationFrame(tick); return; }
      if (now - lastTs < FRAME_MS) { requestAnimationFrame(tick); return; }
      lastTs = now;
      const p = Math.min(1, (now - t0) / DURATION);
      const ease = 1 - Math.pow(1 - p, 3);
      setDotAnimOpacity(START + (END - START) * ease);
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [setDotsReady, setIntroOpacity, setDotAnimOpacity]);

  // ── Mobile detection ───────────────────────────────────────────────────────
  const _isPhoneUA = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  const [isMobile, setIsMobile] = useState(() => _isPhoneUA || window.innerWidth <= 768);
  // CS2-style menu state
  const [activeMainTab, setActiveMainTab] = useState('home');
  const [analyticsTab, setAnalyticsTab] = useState(0);
  const [adminTab, setAdminTab] = useState(0);
  const [leftPanelTab, setLeftPanelTab] = useState('history');
  const [leftPanelExpanded, setLeftPanelExpanded] = useState(false);
  const [rightPanelExpanded, setRightPanelExpanded] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [showMapDots, setShowMapDots] = useState(true);
  const [showMapPainter, setShowMapPainter] = useState(false);
  const [selectedSdrId, setSelectedSdrId] = useState(SDR_IDS[0]);
  const [selectedRtys, setSelectedRtys] = useState(() => SDR_RTYS_MAP[SDR_IDS[0]].rtys[0]);

  // Telemetry state for top-level indicators (SDR online status etc.)
  const [tele, setTele] = useState({ cpu: 0, ram: 0, db_mb: 0, backup_local_mb: 0, backup_r2_mb: 0, backup_running: false, backup_error: '', r2_configured: false, active_users: 0, batch_requests_per_sec: 0, network_io_mbps: 0, sdr_status: false, db_status: false, whatsapp_status: false, drops_24h: 0 });
  useEffect(() => {
    let id = null;
    const fetchTele = async () => {
      if (document.hidden) return;
      try {
        const res = await fetch('/api/telemetry?_=' + Date.now(), { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        setTele(data);
      } catch { /* ignore */ }
    };
    const start = () => { fetchTele(); id = setInterval(fetchTele, 15000); };
    const stop = () => { clearInterval(id); id = null; };
    const onVis = () => { if (document.hidden) { stop(); } else { stop(); start(); } };
    document.addEventListener('visibilitychange', onVis);
    start();
    return () => { stop(); document.removeEventListener('visibilitychange', onVis); };
  }, []);

  const sigintOpen = activeMainTab === 'dashboard';
  const showAnalytics = activeMainTab === 'analytics';
  const [analyticsVisited, setAnalyticsVisited] = useState(false);
  useEffect(() => { if (showAnalytics) setAnalyticsVisited(true); }, [showAnalytics]);
  const [homeVisited, setHomeVisited] = useState(false);
  useEffect(() => { if (activeMainTab === 'home') setHomeVisited(true); }, [activeMainTab]);
  useEffect(() => {
    const onResize = () => setIsMobile(_isPhoneUA || window.innerWidth <= 768);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const [peakRadius,     setPeakRadius]     = useState(0.05);
  const [peakX,          setPeakX]          = useState(0.830);
  const [peakY,          setPeakY]          = useState(0.420);
  const [hoverRadius,    setHoverRadius]    = useState(0.080);
  const [hoverHeight,    setHoverHeight]    = useState(0.08);
  const [showSecretPanel] = useState(false);
  const fadeOverlayRef  = useRef(null);                     // imperative fade — no re-renders
  // stationScan is a ref (stationScanRef) — no state, updated via scanTick
  const [playingIdx,     setPlayingIdx]     = useState(null);
  const [audioQuality,   setAudioQuality]   = useState(0);
  const [audioState,     setAudioState]     = useState('silence');
  const [logRunning,     setLogRunning]     = useState(false);
  const [logPath,        setLogPath]        = useState('');
  const [logMode,        setLogMode]        = useState('20mhz'); // '2mhz' | '20mhz'
  const [panoramicPeaks, setPanoramicPeaks] = useState([]);
  const [sigintPeaks,    setSigintPeaks]    = useState([]);
  const [isInterferenceDetected, setIsInterferenceDetected] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  // Site-1 UV=(0.830,0.420) → world x=(0.83-0.5)*10=3.3, z=(0.42-0.5)*8=-0.64
  const cameraZoomRef = useRef({ active: false, t: 0,
    targetX: 3.3, targetY: 1.8, targetZ: 2.2,
    lookX: 3.3, lookY: 0.2, lookZ: -0.64 });
  const REMOVED_REGIONS = useMemo(() => new Set(['Zaqatala','Qazax','Sirvan','Astara']), []);

  const [customPeaks, setCustomPeaks] = useState([
    { id: 2,  name: 'Site-7',     x: 0.630, y: 0.240, radius: 0.050, height: 0.12, rotation:  36 * Math.PI / 180 },
    { id: 3,  name: 'Site-6',   x: 0.520, y: 0.340, radius: 0.050, height: 0.12, rotation:  61 * Math.PI / 180 },
    { id: 5,  name: 'Site-4',    x: 0.290, y: 0.400, radius: 0.050, height: 0.12, rotation: 100 * Math.PI / 180 },
    { id: 6,  name: 'Site-8',     x: 0.330, y: 0.610, radius: 0.050, height: 0.12, rotation: 105 * Math.PI / 180 },
    { id: 7,  name: 'Site-3', x: 0.490, y: 0.610, radius: 0.050, height: 0.12, rotation: 118 * Math.PI / 180 },
    { id: 9,  name: 'Site-5',  x: 0.650, y: 0.770, radius: 0.050, height: 0.12, rotation: 154 * Math.PI / 180 },
  ]);

  // ── Animation data stored in a ref — no re-renders at 60fps ────────────────
  const animRef = useRef({
    laserProgress:  new Float32Array(10),
    chargeProgress: new Float32Array(10),
    impactProgress: new Float32Array(10),
    launchProgress: new Float32Array(10),
  });

  // ── Misc refs ───────────────────────────────────────────────────────────────
  const laserDurationRef  = useRef(3.0);
  const customPeaksRef    = useRef(customPeaks);
  const audioContextRef   = useRef(null);
  const hissNodeRef       = useRef(null);
  const gainNodeRef       = useRef(null);
  const isOverPeakRef     = useRef(false);
  const wsRef             = useRef(null);
  // Shared hover refs — passed to WaveSurface AND AntennaModel
  const hoverActiveRef    = useRef(false);
  const hoverPositionRef  = useRef([0, 0]);
  // Site-1 wave intensity — ref so WaveSurface reads it without causing re-renders
  const peakIntRef        = useRef(0.0);
  // stationScanRef shared with StationPanel (panel owns its own tick state)
  const stationScanRef    = useRef({});
  // SDR peaks for OverviewDashboard — updated from WebSocket in real-time
  const sdrPeaksRef       = useRef({});

  const peakPosition = useMemo(() => [peakX, peakY], [peakX, peakY]);

  useEffect(() => { customPeaksRef.current = customPeaks; }, [customPeaks]);

  // ── Single WS — all SDR data flows through here ────────────────────────
  useEffect(() => {
    let ws, t, _backoff = 2000;
    let reconnectAttempts = 0;
    const MAX_RECONNECT_ATTEMPTS = 10;
    
    const connect = () => {
      if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
        console.warn('Max reconnection attempts reached, stopping reconnection');
        return;
      }
      
      try {
        const _wsProto2 = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const wsUrl = `${_wsProto2}://${window.location.host}/ws/sdr`;
        console.log(`Connecting to WebSocket: ${wsUrl} (attempt ${reconnectAttempts + 1})`);
        
        ws = new WebSocket(wsUrl);
        ws.onopen = () => { 
          console.log('WebSocket connected successfully');
          setWsConnected(true); 
          _backoff = 2000; 
          reconnectAttempts = 0;
        };
        
        ws.onmessage = e => {
          try {
            const d = JSON.parse(e.data);
            // Site-1 intensity → ref only, no re-render
            if (typeof d.peak_intensity === 'number') peakIntRef.current = d.peak_intensity;
            // interference flag
            if (typeof d.is_interference === 'boolean') setIsInterferenceDetected(d.is_interference);
            // station scan → ref only; StationPanel owns its own refresh tick
            if (d.station_scan && typeof d.station_scan === 'object') {
              Object.assign(stationScanRef.current, d.station_scan);
            }
            // only setState when value actually changes
            if (d.playing_idx  !== undefined) setPlayingIdx(p  => p  === d.playing_idx  ? p  : d.playing_idx);
            if (d.audio_quality !== undefined) setAudioQuality(p => p  === d.audio_quality ? p  : d.audio_quality);
            if (d.audio_state   !== undefined) setAudioState(p   => p  === d.audio_state   ? p  : d.audio_state);
            if (d.log_running   !== undefined) setLogRunning(p   => p  === d.log_running   ? p  : d.log_running);
            if (d.log_path && d.log_path !== '') setLogPath(p => p === d.log_path ? p : d.log_path);
            if (d.panoramic_peaks !== undefined) setPanoramicPeaks(p => {
              const s1 = JSON.stringify(p), s2 = JSON.stringify(d.panoramic_peaks);
              return s1 === s2 ? p : d.panoramic_peaks;
            });
            if (d.sigint_peaks !== undefined) setSigintPeaks(p => {
              const s1 = JSON.stringify(p), s2 = JSON.stringify(d.sigint_peaks);
              return s1 === s2 ? p : d.sigint_peaks;
            });
            // SDR peaks for OverviewDashboard — ref only, no re-render
            if (d.sdr_peaks) sdrPeaksRef.current = d.sdr_peaks;
          } catch (error) {
            console.error('Failed to parse WebSocket message:', error, 'Data:', e.data);
          }
        };
        
        ws.onerror = (error) => { 
          console.error('WebSocket error:', error);
          setWsConnected(false); 
        };
        
        ws.onclose = (ev) => {
          console.log(`WebSocket closed with code: ${ev.code}, reason: ${ev.reason}`);
          setWsConnected(false);
          clearTimeout(t);
          reconnectAttempts++;
          
          // 4001/4003 = auth error set by server, 1006 = abnormal close (403 upgrade rejection)
          // Back off aggressively to avoid hammering the server while unauthenticated
          const noAuth = ev.code === 1006 || ev.code === 4001 || ev.code === 4003;
          const delay = noAuth ? 30000 : Math.min(_backoff, 30000);
          if (!noAuth) _backoff = Math.min(_backoff * 2, 30000);
          
          console.log(`Reconnecting in ${delay}ms (backoff: ${_backoff}ms, attempts: ${reconnectAttempts})`);
          t = setTimeout(connect, delay);
        };
      } catch (error) {
        console.error('Failed to create WebSocket:', error);
        reconnectAttempts++;
        const delay = Math.min(_backoff, 30000);
        _backoff = Math.min(_backoff * 2, 30000);
        t = setTimeout(connect, delay);
      }
    };
    connect();

    // Immediately reconnect when user returns to the tab
    const onVisible = () => {
      if (!document.hidden && ws && ws.readyState !== WebSocket.OPEN && ws.readyState !== WebSocket.CONNECTING) {
        clearTimeout(t);
        _backoff = 2000;
        connect();
      }
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      clearTimeout(t);
      document.removeEventListener('visibilitychange', onVisible);
      if (ws) ws.close();
    };
  }, []);

  // Prime notification audio on first user gesture so autoplay policy allows it
  useEffect(() => {
    let unlocked = false;
    const unlock = () => {
      if (unlocked) return;
      unlocked = true;
      const a = new Audio(NOTIFICATION_SOUND);
      a.muted = true;
      a.play().then(() => {
        a.pause();
        a.currentTime = 0;
        a.muted = false;
        audioUnlocked = true;
        console.log('[notif] audio unlocked');
        if (hasPendingSound) {
          hasPendingSound = false;
          playNotificationSound();
        }
      }).catch(err => console.warn('[notif] unlock failed', err));
    };
    const events = ['click', 'pointerdown', 'touchstart', 'keydown'];
    events.forEach(e => window.addEventListener(e, unlock, { once: true, capture: true }));
    return () => events.forEach(e => window.removeEventListener(e, unlock, { capture: true }));
  }, []);

  // ── Chat notification sound socket (App-level so it works on every tab) ──
  useEffect(() => {
    if (!authUser?.id) return;
    let ws, t;
    const connect = () => {
      try {
        ws = new WebSocket(`${CHAT_WS_BASE}/ws/chat`);
        ws.onmessage = (e) => {
          try {
            const d = JSON.parse(e.data);
            console.log('[notif] ws raw', d);
            if (d.type !== 'message') return;
            const isOwn = String(d.user_id) === String(authUser?.id) && (!d.sender_type || d.sender_type === 'user');
            console.log('[notif] isOwn', isOwn, 'authUser', authUser?.id, 'd.user_id', d.user_id, 'sender_type', d.sender_type);
            if (!isOwn) playNotificationSound();
          } catch (err) { console.warn('[notif] ws message error', err); }
        };
        ws.onclose = () => { t = setTimeout(connect, 3000); };
        ws.onerror = () => ws?.close();
        const ping = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 25000);
        ws._ping = ping;
      } catch { /* ignore */ }
    };
    connect();
    return () => {
      clearTimeout(t);
      if (ws) { clearInterval(ws._ping); ws.onclose = null; ws.close(); }
    };
  }, [authUser?.id]);

  const tuneStation = useCallback((idx) => {
    fetch('http://127.0.0.1:8000/radio/tune', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idx }),
    }).catch(() => {});
    setPlayingIdx(idx);
  }, []);

  const stopStation = useCallback(() => {
    fetch('http://127.0.0.1:8000/radio/untune', { method: 'POST' }).catch(() => {});
    setPlayingIdx(null);
  }, []);

  const toggleLog = useCallback(() => {
    if (logRunning) {
      fetch('http://127.0.0.1:8000/radio/log', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'stop' }),
      }).catch(() => {});
      setLogRunning(false);
    } else {
      fetch('http://127.0.0.1:8000/radio/log', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start' }),
      }).then(r => r.json()).then(d => {
        if (d.ok) { setLogRunning(true); setLogPath(d.path || ''); }
      }).catch(() => {});
    }
  }, [logRunning]);

  const toggleLogMode = useCallback((mode) => {
    setLogMode(mode);
    fetch('http://127.0.0.1:8000/radio/log-mode', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }).catch(() => {});
  }, []);

  const navigateToSdr = useCallback((sdrId) => {
    if (!sdrId || !SDR_RTYS_MAP[sdrId]) return;
    tracker.track('MAP_ANTENNA_CLICK', 'UI', { sdrId });
    setActiveMainTab('dashboard');
    setSelectedSdrId(sdrId);
    setSelectedRtys(SDR_RTYS_MAP[sdrId].rtys[0]);
  }, [setActiveMainTab, setSelectedSdrId, setSelectedRtys]);

  // Load saved peaks on mount (only if previously saved)
  useEffect(() => {
    const saved = localStorage.getItem('customPeaks');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (parsed && parsed.length > 0) setCustomPeaks(parsed.filter(p => !REMOVED_REGIONS.has(p.name)));
      } catch (_) {}
    }
  }, []);

  // ── Laser firing ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const IMPACT_DUR = 0.5;
    const anim = animRef.current;

    const fireLaser = (peakIndex) => {
      const FIRE_DUR = laserDurationRef.current;
      const startTime = performance.now();
      let lastTs = 0;
      const FRAME_MS = 1000 / 20;

      const tick = (now) => {
        if (document.hidden) { requestAnimationFrame(tick); return; }
        if (now - lastTs < FRAME_MS) { requestAnimationFrame(tick); return; }
        lastTs = now;
        const elapsed = (now - startTime) / 1000;

        const LAUNCH_DUR = 0.4;
        if (elapsed < LAUNCH_DUR) {
          // ─ Launch ring at source city ─────────────────────────────────────
          anim.launchProgress[peakIndex] = elapsed / LAUNCH_DUR;
          anim.laserProgress[peakIndex]  = 0;
          anim.impactProgress[peakIndex] = 0;
          requestAnimationFrame(tick);

        } else if (elapsed < LAUNCH_DUR + FIRE_DUR) {
          // ─ Projectile travels ─────────────────────────────────────────────
          anim.launchProgress[peakIndex] = 0;
          anim.chargeProgress[peakIndex] = 0;
          anim.laserProgress[peakIndex]  = (elapsed - LAUNCH_DUR) / FIRE_DUR;
          anim.impactProgress[peakIndex] = 0;
          requestAnimationFrame(tick);

        } else if (elapsed < LAUNCH_DUR + FIRE_DUR + IMPACT_DUR) {
          // ─ Impact ring at Site-1 ────────────────────────────────────────────
          anim.laserProgress[peakIndex]  = 0;
          anim.launchProgress[peakIndex] = 0;
          anim.impactProgress[peakIndex] = (elapsed - LAUNCH_DUR - FIRE_DUR) / IMPACT_DUR;
          requestAnimationFrame(tick);

        } else {
          // ─ Done ───────────────────────────────────────────────────────────
          anim.chargeProgress[peakIndex] = 0;
          anim.laserProgress[peakIndex]  = 0;
          anim.impactProgress[peakIndex] = 0;
          anim.launchProgress[peakIndex] = 0;
        }
      };

      requestAnimationFrame(tick);
    };

    // Schedule each peak independently with random delay (1–10 s)
    const timers = [];
    const scheduleForPeak = (index) => {
      const delay = Math.random() * 9000 + 1000;
      const t = setTimeout(() => {
        fireLaser(index);
        scheduleForPeak(index);
      }, delay);
      timers.push(t);
    };
    customPeaksRef.current.forEach((_, i) => scheduleForPeak(i));

    return () => timers.forEach(t => clearTimeout(t));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Control WebSocket (audio) ───────────────────────────────────────────────
  useEffect(() => {
    const _wsProto3 = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${_wsProto3}://${window.location.host}/ws/control`);
    wsRef.current = ws;
    ws.onopen  = () => console.log('Control WS connected');
    ws.onerror = (err) => console.warn('Control WS error:', err);
    return () => { if (ws.readyState === WebSocket.OPEN) ws.close(); };
  }, []);

  const sendWsCommand = (command) => {
    if (wsRef.current?.readyState === WebSocket.OPEN)
      wsRef.current.send(JSON.stringify({ command }));
  };

  // ── Audio — pre-warm on mount to avoid hover stutter ────────────────────────
  useEffect(() => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      audioContextRef.current = ctx;
      // Build noise buffer eagerly (off the critical hover path)
      const bufSize = ctx.sampleRate; // 1 s loop is enough, lighter than 2 s
      const buf  = ctx.createBuffer(1, bufSize, ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < bufSize; i++) data[i] = Math.random() * 2 - 1;
      const noise  = ctx.createBufferSource();
      noise.buffer = buf;
      noise.loop   = true;
      const filter = ctx.createBiquadFilter();
      filter.type  = 'bandpass';
      filter.frequency.value = 3000;
      filter.Q.value = 1;
      const gain = ctx.createGain();
      gain.gain.value = 0; // silent until hover
      noise.connect(filter);
      filter.connect(gain);
      gain.connect(ctx.destination);
      noise.start();
      hissNodeRef.current = noise;
      gainNodeRef.current = gain;
      // Keep context suspended until first user gesture
      // (browser autoplay policy — it will be in 'suspended' state already)
    } catch (e) { console.warn('Audio pre-warm failed:', e); }
  }, []);

  const handleUserInteraction = () => {
    if (audioContextRef.current?.state === 'suspended')
      audioContextRef.current.resume();
  };

  const fadeInAudio = () => {
    if (gainNodeRef.current && audioContextRef.current) {
      const now = audioContextRef.current.currentTime;
      gainNodeRef.current.gain.cancelScheduledValues(now);
      gainNodeRef.current.gain.setValueAtTime(gainNodeRef.current.gain.value, now);
      gainNodeRef.current.gain.linearRampToValueAtTime(0.15, now + 0.5);
    }
  };

  const fadeOutAudio = () => {
    if (gainNodeRef.current && audioContextRef.current) {
      const now = audioContextRef.current.currentTime;
      gainNodeRef.current.gain.cancelScheduledValues(now);
      gainNodeRef.current.gain.setValueAtTime(gainNodeRef.current.gain.value, now);
      gainNodeRef.current.gain.linearRampToValueAtTime(0, now + 1.0);
    }
  };

  // ── Surface event handler ────────────────────────────────────────────────────
  const handleSurfaceEvent = (uv, type) => {
    if (type === 'hover' && uv) {
      const dist = Math.hypot(uv[0] - peakX, uv[1] - peakY);
      if (dist < peakRadius * 2) {
        if (!isOverPeakRef.current) { isOverPeakRef.current = true; /* fadeInAudio(); */ }
      } else {
        if (isOverPeakRef.current)  { isOverPeakRef.current = false; /* fadeOutAudio(); */ }
      }
    } else if (type === 'out') {
      if (isOverPeakRef.current) { isOverPeakRef.current = false; /* fadeOutAudio(); */ }
    } else if (type === 'click' && uv) {
      handleUserInteraction();
      const dist = Math.hypot(uv[0] - peakX, uv[1] - peakY);
      /* DISABLED: Site-1 click zoom + spectrum switch
      if (dist < peakRadius * 3) {
        cameraZoomRef.current.active = true;
        cameraZoomRef.current.t = 0;
        const overlay = fadeOverlayRef.current;
        if (overlay) overlay.style.display = 'block';
        let start = null;
        const FADE_MS = 1400;
        const tick = (now) => {
          if (!start) start = now;
          const p = Math.min((now - start) / FADE_MS, 1.0);
          if (overlay) overlay.style.opacity = p;
          if (p < 1.0) requestAnimationFrame(tick);
          else {
            fetch('http://127.0.0.1:8000/switch/spectrum_core', { method: 'POST' })
              .then(r => r.json()).then(d => console.log('Switch:', d))
              .catch(e => console.warn('Switch unavailable:', e.message));
            setTimeout(() => {
              cameraZoomRef.current.active = false;
              if (overlay) { overlay.style.opacity = 0; overlay.style.display = 'none'; }
            }, 800);
          }
        };
        requestAnimationFrame(tick);
      }
      */
    }
  };

  // ── Peak editor helpers ──────────────────────────────────────────────────────
  const updateCustomPeak = (id, key, val) =>
    setCustomPeaks(p => p.map(pk => pk.id === id ? { ...pk, [key]: val } : pk));
  const removeCustomPeak = (id) =>
    setCustomPeaks(p => p.filter(pk => pk.id !== id));
  const savePeaks  = () => localStorage.setItem('customPeaks', JSON.stringify(customPeaks));
  const loadPeaks  = () => {
    const s = localStorage.getItem('customPeaks');
    if (s) { try { setCustomPeaks(JSON.parse(s).filter(p => !REMOVED_REGIONS.has(p.name))); } catch (_) {} }
  };

  // ── MOBILE: show only SigintDashboard ────────────────────────────────────
  if (isMobile) {
    return (
      <div style={{ position:'fixed', inset:0, background:'#050a0f' }}>
        <Suspense fallback={<div style={{ width:'100%', height:'100%' }} />}>
          <SigintDashboard hidden={false} peaks={sigintPeaks} forceMobile sdrNodes={tele.sdr_nodes} />
        </Suspense>
      </div>
    );
  }

  return (
    <>
    {/* ── INTRO OVERLAY — hides map until dots are loaded, then fades out ── */}
    <div style={{
      position:'fixed', inset:0, zIndex:50, pointerEvents:'none',
      background:'#050505',
      opacity: introOpacity,
      transition: dotsReady ? 'opacity 0.6s ease' : 'none',
    }} />
    {/* ── CANVAS LAYER — fixed full-screen background, never constrained ── */}
    <div style={{ position:'fixed', inset:0, width:'100vw', height:'100vh', backgroundColor:'#050505', zIndex:0 }}>
      <Canvas
        dpr={[1, 1]}
        gl={{ antialias: false, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.25, powerPreference: 'low-power' }}
        camera={{ fov: 35, near: 0.1, far: 100 }}
        resize={{ debounce: 200 }}
        frameloop="demand"
      >
        <Ticker />
        <FixedCamera zoomRef={cameraZoomRef} />
        <ambientLight intensity={1.4} color="#6699ff" />
        <directionalLight position={[5, -5, 8]} intensity={1.6} color="#ffffff" />
        <pointLight position={[0, 6, 0]} intensity={3.5} color="#00eeff" distance={18} />
        <NeonOutline isAlert={isInterferenceDetected} />
        <WaveSurface
          noiseScale={300}
          peakRadius={peakRadius}
          peakPosition={peakPosition}
          hoverRadius={hoverRadius}
          hoverHeight={hoverHeight}
          hoverActiveRef={hoverActiveRef}
          hoverPositionRef={hoverPositionRef}
          customPeaks={customPeaks}
          animRef={animRef}
          onPeakClick={handleSurfaceEvent}
          peakIntRef={peakIntRef}
        />
        {/* Annotation glow sprites — inside WebGL, rendered before antennas */}
        {/* DISABLED: <AnnotationSprites dots={annotationDots} dotScale={annotationScale} animOpacity={dotAnimOpacity} /> */}
        <AnnotationSprites dots={[]} dotScale={annotationScale} animOpacity={dotAnimOpacity} />
        {/* Site-1 peak model */}
        <MainPeakModel uvX={peakX} uvY={peakY}
          hoverActiveRef={hoverActiveRef} hoverUvRef={hoverPositionRef}
          sdrNodes={tele.sdr_nodes} onSelect={navigateToSdr} />
        {/* Elegant hub sphere markers */}
        <HubMarkers sdrNodes={tele.sdr_nodes} />
        {/* City antenna GLBs still drive laser/shader uniforms */}
        {customPeaks.map((p, i) => (
          <AntennaModel key={p.id}
            uvX={p.x} uvY={p.y}
            hoverActiveRef={hoverActiveRef} hoverUvRef={hoverPositionRef}
            color={CITY_DATA[i % CITY_DATA.length].css}
            rotationY={p.rotation ?? 0}
            stationData={{ name: p.name }}
            sdrId={HUB_TO_SDR_ID[p.name]}
            sdrNodes={tele.sdr_nodes}
            onSelect={navigateToSdr} />
        ))}
      </Canvas>
      {/* Zoom-to-Site-1 fade overlay — imperative, no state re-renders */}
      <div ref={fadeOverlayRef} style={{
        display:'none', position:'absolute', inset:0, zIndex:99,
        pointerEvents:'none', background:'rgba(0,0,0,1)', opacity:0,
      }} />
    </div>{/* end canvas layer */}

    {/* ── POPULATION DOTS — OSM populated places projected to map ── */}
    {showMapDots && <PopulationDots />}
    {/* ── PARTICLES LAYER — triangles network masked to map shape ── */}
    {/* DISABLED: clashes with shader grid — re-enable to restore: <MapParticles /> */}
    {/* ── MAP PAINTER — admin dot annotations, visible to all ── */}
    {/* Dots hidden: showToolbar forced false, onDotsChange suppressed — re-enable to restore */}
    <MapPainter isAdmin={isAdmin} showToolbar={false} dotScale={annotationScale}
      onDotsChange={noop}
      onScaleChange={setAnnotationScale}
      onReady={handleMapReady} />

    {/* ── UI LAYER — floats above canvas, pass-through on empty areas ── */}
    <div style={{ position:'fixed', inset:0, zIndex:100, pointerEvents:'none' }}>

      {/* ── TOP CS2 MENU ─────────────────────────────────────────────── */}
      <TopBar
        activeMainTab={activeMainTab}
        setActiveMainTab={setActiveMainTab}
        analyticsTab={analyticsTab}
        setAnalyticsTab={setAnalyticsTab}
        adminTab={adminTab}
        setAdminTab={setAdminTab}
        onProfileOpen={() => setShowProfile(true)}
        selectedSdrId={selectedSdrId}
        setSelectedSdrId={setSelectedSdrId}
        selectedRtys={selectedRtys}
        setSelectedRtys={setSelectedRtys}
        sdrNodes={tele.sdr_nodes}
      />

      {/* ── ADMIN full-page view ──────────────────────────────────────── */}
      {activeMainTab === 'admin' && (
        <div style={{
          position: 'absolute', top: 86, left: 0, right: 0, bottom: 0,
          overflowY: 'auto', pointerEvents: 'auto',
          background: 'linear-gradient(180deg, rgba(8,11,18,0.99) 0%, rgba(5,7,13,1.0) 100%)',
          borderTop: '1px solid rgba(255,255,255,0.04)',
        }}>
          <Suspense fallback={null}>
            <AdminPanel tab={adminTab} showMapPainter={showMapPainter} setShowMapPainter={setShowMapPainter} />
          </Suspense>
        </div>
      )}

      {/* ── LEFT PANEL — kept after first home visit for instant switching ── */}
      {homeVisited && (
        <div style={{ display: activeMainTab === 'home' ? 'block' : 'none' }}>
          <StationPanel
            tab={leftPanelTab}
            onTabChange={setLeftPanelTab}
            expanded={leftPanelExpanded}
            onExpandedChange={setLeftPanelExpanded}
            tele={tele}
            active={activeMainTab === 'home'}
          />
        </div>
      )}

      {/* ── RIGHT PANEL — Chat + AI ───────────────────────── */}
      {homeVisited && (
        <div style={{ display: activeMainTab === 'home' ? 'block' : 'none' }}>
          <Suspense fallback={null}>
            <RightPanel
              expanded={rightPanelExpanded}
              onExpandedChange={setRightPanelExpanded}
            />
          </Suspense>
        </div>
      )}

      {/* ── BOTTOM DROP TICKER ── */}
      {activeMainTab === 'home' && <DropTicker />}

      {/* ── SIGINT left-panel (toggled from the top menu) ──────────────── */}
      <Suspense fallback={null}>
        <SigintDashboard
          open={sigintOpen}
          onOpenChange={(v) => { if (!v) setActiveMainTab('home'); }}
          hidden={playingIdx !== null || logMode === '2mhz'}
          peaks={sigintPeaks}
          selectedSdrId={selectedSdrId}
          selectedRtys={selectedRtys}
          sdrRtysMap={SDR_RTYS_MAP}
          topOffset={120}
          sdrNodes={tele.sdr_nodes}
        />
      </Suspense>


      {/* ── Secret panel (same as original) ──────────────────────────────── */}
      {showSecretPanel && (
        <div style={{
          position:'absolute', bottom:40, left:'50%', transform:'translateX(-50%)',
          pointerEvents:'auto', backgroundColor:'rgba(10,10,15,0.95)',
          zIndex:100,
          padding:20, borderRadius:12, border:'1px solid #333',
          minWidth:350, maxHeight:'70vh', overflowY:'auto',
        }}>
          <h3 style={{ color:'#00ffb8', margin:'0 0 15px 0', fontSize:13, letterSpacing:'0.15em', fontFamily:'monospace' }}>CONTROLS</h3>

          <div style={{ display:'flex', gap:8, marginBottom:15 }}>
            {[['Save','#2196F3',savePeaks],['Load','#FF9800',loadPeaks]].map(([label,bg,fn]) => (
              <button key={label} onClick={fn} style={{
                backgroundColor:bg, color:'#fff', border:'none',
                padding:'8px 16px', fontSize:12, borderRadius:4, cursor:'pointer', flex:1,
              }}>{label}</button>
            ))}
          </div>

          {customPeaks.map((peak) => (
            <div key={peak.id} style={{
              backgroundColor:'rgba(255,255,255,0.05)', padding:12,
              borderRadius:8, marginBottom:10, border:'1px solid #333',
            }}>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
                <input
                  type="text" value={peak.name}
                  onChange={e => updateCustomPeak(peak.id, 'name', e.target.value)}
                  style={{ backgroundColor:'rgba(0,0,0,0.3)', color:'#fff', border:'1px solid #444', padding:'4px 8px', fontSize:12, borderRadius:4, width:150 }}
                />
                <button onClick={() => removeCustomPeak(peak.id)} style={{
                  backgroundColor:'#f44336', color:'#fff', border:'none',
                  padding:'4px 8px', fontSize:10, borderRadius:4, cursor:'pointer',
                }}>Remove</button>
              </div>
              <label style={{ color:'#aaa', fontSize:11, display:'block', marginBottom:2 }}>
                X: {peak.x.toFixed(3)}
              </label>
              <input type="range" min={0} max={1} step={0.001} value={peak.x}
                onChange={e => updateCustomPeak(peak.id, 'x', parseFloat(e.target.value))}
                style={{ width:'100%', marginBottom:4, accentColor:'#ff9900' }} />
              <label style={{ color:'#aaa', fontSize:11, display:'block', marginBottom:2 }}>
                Y: {peak.y.toFixed(3)}
              </label>
              <input type="range" min={0} max={1} step={0.001} value={peak.y}
                onChange={e => updateCustomPeak(peak.id, 'y', parseFloat(e.target.value))}
                style={{ width:'100%', marginBottom:4, accentColor:'#ff9900' }} />
              <label style={{ color:'#aaa', fontSize:11, display:'block', marginBottom:2 }}>
                Rotation: {Math.round((peak.rotation ?? 0) * 180 / Math.PI)}°
              </label>
              <input type="range" min={0} max={6.283} step={0.01} value={peak.rotation ?? 0}
                onChange={e => updateCustomPeak(peak.id, 'rotation', parseFloat(e.target.value))}
                style={{ width:'100%', marginBottom:4, accentColor:'#00ffb8' }} />
            </div>
          ))}
          {customPeaks.length === 0 && (
            <p style={{ color:'#666', fontSize:12, textAlign:'center', margin:20 }}>
              No custom peaks. Click "Add Peak" to create one.
            </p>
          )}
        </div>
      )}
      {/* ── DISCONNECT OVERLAY ── */}
      {!wsConnected && (
        <div className="disconnect-overlay">
          <span>{t('disconnect.title')}</span>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: 18, padding: '8px 22px',
              background: 'transparent', border: '1px solid rgba(255,87,34,0.6)',
              color: '#ff7043', fontFamily: 'var(--font-mono)', fontSize: 11,
              letterSpacing: '0.12em', textTransform: 'uppercase', cursor: 'pointer',
              borderRadius: 4,
            }}
          >{t('disconnect.reconnect')}</button>
        </div>
      )}
    </div>{/* end UI layer */}

    {/* ── Analytics Dashboard overlay — mounted after first visit, kept for instant switching ── */}
    {analyticsVisited && (
      <div style={{ display: showAnalytics ? 'block' : 'none' }}>
        <Suspense fallback={null}>
          <AnalyticsDashboard
            tab={analyticsTab}
            onTabChange={setAnalyticsTab}
            sdrPeaksRef={sdrPeaksRef}
            active={showAnalytics}
            tele={tele}
          />
        </Suspense>
      </div>
    )}

    {/* ── Profile Modal ── */}
    <AnimatePresence>
      {showProfile && (
        <Suspense fallback={null}>
          <ProfileModal onClose={() => setShowProfile(false)} />
        </Suspense>
      )}
    </AnimatePresence>

    </>
  );
}
