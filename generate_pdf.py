"""Generate a PDF with the full UI breakdown response."""
from fpdf import FPDF

class PDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(40, 40, 40)
        self.cell(0, 8, 'SDR/CGrep Codebase UI Element Breakdown', ln=True, align='C')
        self.ln(2)
        self.set_draw_color(180, 180, 180)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f'Page {self.page_no()}', align='C')

    def chapter_title(self, title):
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(20, 20, 20)
        self.cell(0, 10, title, ln=True)
        self.ln(2)

    def section_title(self, title):
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(40, 40, 40)
        self.cell(0, 8, title, ln=True)
        self.ln(1)

    def subsection_title(self, title):
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(60, 60, 60)
        self.cell(0, 6, title, ln=True)

    def body_text(self, text):
        self.set_font('Helvetica', '', 9)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def code_block(self, lines):
        self.set_font('Courier', '', 7)
        self.set_text_color(50, 50, 50)
        self.set_fill_color(245, 245, 245)
        for line in lines:
            self.cell(0, 4, line, ln=True, fill=True)
        self.ln(2)

    def bullet(self, text):
        self.set_font('Helvetica', '', 9)
        self.set_text_color(30, 30, 30)
        self.cell(4, 5, '', ln=0)
        self.cell(4, 5, chr(149), ln=0)
        self.multi_cell(0, 5, text)

pdf = PDF()
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_page()

# ---------------------------------------------------------------------------
# Title Page
# ---------------------------------------------------------------------------
pdf.set_font('Helvetica', 'B', 20)
pdf.set_text_color(20, 20, 20)
pdf.cell(0, 20, 'Exhaustive UI Element Breakdown', ln=True, align='C')
pdf.set_font('Helvetica', '', 12)
pdf.cell(0, 10, 'SDR / CGrep Codebase  |  Every Library, Style, Font & Asset', ln=True, align='C')
pdf.ln(20)
pdf.set_font('Helvetica', 'I', 10)
pdf.cell(0, 8, 'Generated from codebase inspection of:', ln=True, align='C')
pdf.cell(0, 8, 'calculate.py, dashboard.py, server.py, sdr-dashboard/', ln=True, align='C')
pdf.add_page()

# ---------------------------------------------------------------------------
# 1. calculate.py
# ---------------------------------------------------------------------------
pdf.chapter_title('1. calculate.py  -  Pure DSP (No UI)')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\calculate.py  (842 lines)\n'
    'Purpose: Stateless signal-processing math. No UI framework, no widgets, no styles.\n'
    'Libraries: numpy, scipy.signal (optional), math, typing\n'
    'UI Relevance: Zero. It only exports functions like compute_fft_db, wfm_demod_block, '
    'and assess_audio_quality for other files to consume.\n'
    'Constants include AUDIO_RATE=48_000, FM_DEVIATION=75_000, DEEMPH_TAU_US=75.0, '
    'and various hardware defaults for RTL-SDR setup.'
)

# ---------------------------------------------------------------------------
# 2. dashboard.py
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.chapter_title('2. dashboard.py  -  PyQt6 Desktop App')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\dashboard.py  (1 183 lines)\n'
    'Libraries: PyQt6 (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, '
    'QGridLayout, QLabel, QSlider, QPushButton), PyQt6.QtCore (Qt, QTimer, QThread, pyqtSignal), '
    'PyQt6.QtGui (QFont), pyqtgraph, sounddevice'
)

pdf.section_title('Window & Root')
pdf.body_text(
    'MainWindow(QMainWindow)\n'
    '  Title: "Region SDR Radio Network"\n'
    '  Default size: 1280 x 820\n'
    '  Root stylesheet: background:#0e0e0e;\n'
    '  pyqtgraph config: background #111111, foreground #aaaaaa'
)

pdf.section_title('Top Panel - Site-1 Live Audio')
pdf.body_text(
    'Container: QWidget with background:#111111; border-radius:10px;\n\n'
    'City label: QLabel("BLACK CIRCLE  Site-1  -  Live FM Radio")\n'
    '  Font: QFont("monospace", 11, QFont.Weight.Bold)\n'
    '  Style: color:#ff3355; letter-spacing:1px;\n\n'
    'Frequency display: QLabel("99.5 MHz")\n'
    '  Font: QFont("monospace", 34, QFont.Weight.Bold)\n'
    '  Style: color:#00ffcc; (switches to #ffaa44 in simulation mode)\n\n'
    'Signal label: QLabel("starting...")\n'
    '  Style: color:#444; font-family:monospace; font-size:11px;\n\n'
    'Station label: QLabel("RADIO  -")\n'
    '  Font: QFont("monospace", 12, QFont.Weight.Bold)\n'
    '  Active color: #ffaa44, inactive: #444\n\n'
    'Song label: QLabel("MUSICAL NOTE  -")\n'
    '  Font: QFont("monospace", 10)\n'
    '  Active color: #888, inactive: #333'
)

pdf.section_title('Sliders')
pdf.body_text(
    'Frequency slider (QSlider, Horizontal):\n'
    '  Range: 880-1080 (represents 88.0-108.0 MHz)\n'
    '  Handle: background:#00ffcc; border-radius:5px; width:14px; height:14px;\n'
    '  Groove: background:#222; height:4px; border-radius:2px;\n'
    '  Fill: background:#00aa88;\n\n'
    'Volume slider (QSlider, Horizontal):\n'
    '  Range: 0-100\n'
    '  Handle: background:#ffaa00;\n'
    '  Fill: background:#886600;'
)

pdf.section_title('RDS Grid')
pdf.body_text(
    'Labels built with _rds_lbl() helper:\n'
    '  Font: QFont("monospace", 9)\n'
    '  Style: color:#888;\n'
    '  Value color: #ffcc66'
)

pdf.section_title('Simulated City Panels (Bottom Row)')
pdf.body_text(
    '5 simulated cities: Site-9, Zaqatala, Site-8 (and implied others)\n'
    'Each panel: QWidget with background:#111111; border-radius:8px;\n'
    'City label: bold HTML <b style=color:{col}>, with a span at color:#444;font-size:10px\n'
    'Play button: QPushButton("PLAY"), 28x22px\n'
    '  Unchecked: background:#222; color:{city_col}; border-radius:4px; font:bold 10px monospace;\n'
    '  Checked: background:{city_col}; color:#111;\n'
    'Curve colors: #88ccff (Site-9), #ff88aa (Zaqatala), #ffbb44 (Site-8)'
)

pdf.section_title('Helper Styles')
pdf.body_text(
    '_lbl() helper: color:#555; font-family:monospace; font-size:10px;'
)

# ---------------------------------------------------------------------------
# 3. server.py
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.chapter_title('3. server.py  -  FastAPI Backend')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\server.py  (110 lines)\n'
    'UI Role: None. It is a headless WebSocket/HTTP server.\n'
    'Libraries: fastapi, uvicorn, websockets\n'
    'Optional: pywebview import (commented/fallback) for launching a native window, '
    'but the file itself defines no UI elements.'
)

# ---------------------------------------------------------------------------
# 4. sdr-dashboard/ React WebGL Dashboard
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.chapter_title('4. sdr-dashboard/  -  React WebGL Dashboard')
pdf.body_text('This is the primary UI. Below is every single detail.')

pdf.section_title('4.1 Build Tool & Dependencies')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\sdr-dashboard\\package.json\n\n'
    'Package                | Version  | Purpose\n'
    'react                  | ^19.2.6  | UI framework\n'
    'react-dom              | ^19.2.6  | DOM renderer\n'
    'vite                   | ^8.0.12  | Build tool / dev server\n'
    '@vitejs/plugin-react   | ^6.0.1   | React Fast Refresh for Vite\n'
    '@react-three/fiber     | ^9.6.1   | React renderer for Three.js\n'
    '@react-three/drei      | ^10.7.7  | R3F helpers (useTexture, useGLTF, Html)\n'
    '@react-three/postprocessing | ^3.0.4 | Bloom, Vignette effects\n'
    'three                  | ^0.184.0 | Core 3D engine\n'
    'framer-motion          | ^12.40.0 | Spring animations (motion, AnimatePresence)\n'
    'recharts               | ^3.8.1   | Charts (available in bundle)\n'
    'eslint                 | ^10.3.0  | Linting'
)

pdf.section_title('4.2 HTML Entry Point')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\sdr-dashboard\\index.html\n\n'
    'Meta: charset=UTF-8, viewport for mobile\n'
    'Favicon: /favicon.svg\n'
    'Title: sdr-dashboard\n'
    'Preconnect: fonts.googleapis.com and fonts.gstatic.com\n'
    'Google Font: Inter (weights 400, 500, 600, 700, 800) with display=swap\n\n'
    'Local Font Face:\n'
    '  @font-face {\n'
    '    font-family: DSDigit;\n'
    '    src: url(/DS-DIGIT.TTF) format(truetype);\n'
    '    font-weight: normal; font-style: normal; font-display: swap;\n'
    '  }'
)

pdf.section_title('4.3 Global CSS')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\sdr-dashboard\\src\\index.css\n\n'
    'CSS Variables (light mode):\n'
    '  --text: #6b6375    --text-h: #08060d    --bg: #fff\n'
    '  --border: #e5e4e7  --code-bg: #f4f3ec   --accent: #aa3bff\n'
    '  --accent-bg: rgba(170,59,255,0.1)\n'
    '  --accent-border: rgba(170,59,255,0.5)\n'
    '  --social-bg: rgba(244,243,236,0.5)\n'
    '  --shadow: soft drop shadow\n\n'
    'Dark mode (prefers-color-scheme: dark):\n'
    '  --text: #9ca3af    --text-h: #f3f4f6    --bg: #16171d\n'
    '  --border: #2e303a  --code-bg: #1f2028   --accent: #c084fc\n'
    '  --shadow: heavier dark shadow\n\n'
    'Font stacks:\n'
    '  --sans: system-ui, Segoe UI, Roboto, sans-serif\n'
    '  --heading: system-ui, Segoe UI, Roboto, sans-serif\n'
    '  --mono: ui-monospace, Consolas, monospace\n\n'
    'Base: font: 18px/145% var(--sans), letter-spacing: 0.18px\n'
    'Body reset: margin:0; padding:0; width:100%; height:100%; overflow:hidden; background:#000;\n'
    'Root div: 100vw x 100vh, overflow:hidden'
)

pdf.body_text(
    'File: f:\\SDR\\CGrep\\sdr-dashboard\\src\\App.css\n\n'
    'Mostly boilerplate Vite template styles (.counter, .hero, #center, #next-steps, .ticks) '
    'that are not actively used by the current dashboard but remain in the bundle.\n'
    'Uses CSS nesting (& > div, &:hover).\n'
    'Defines var(--accent), var(--border), etc. from index.css.'
)

pdf.section_title('4.4 Main Entry')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\sdr-dashboard\\src\\main.jsx\n\n'
    'React StrictMode\n'
    'createRoot(document.getElementById(root))\n'
    'Imports ./index.css'
)

pdf.section_title('4.5 App.jsx  -  Full Breakdown')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\sdr-dashboard\\src\\App.jsx  (1 577 lines)\n\n'
    'Injected Global Styles (Lines 15-51):\n'
    '  A raw <style> block injected into <head> at runtime with ID ap-css\n'
    '  Keyframes: ap-pulse, ap-fadein, wx-slide-in-right, wx-slide-in-left, wx-bar\n'
    '  Classes: wx-slide-right, wx-slide-left, ap-row, ap-panel-scroll\n'
    '  ap-row hover: background: rgba(255,255,255,0.055)\n'
    '  ap-panel-scroll: hides scrollbar (width: 0px / scrollbar-width: none)'
)

pdf.body_text(
    'Design Tokens (Lines 53-60):\n'
    '  Font: APPLE_FONT = system-ui,-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,sans-serif\n'
    '  Glass panel object (GLASS):\n'
    '    background: rgba(14,14,16,0.92)\n'
    '    border: 1px solid rgba(255,255,255,0.08)\n'
    '    borderRadius: 16\n'
    '    boxShadow: 0 8px 32px rgba(0,0,0,0.50)\n'
    '    transform: translateZ(0) (compositing hint)'
)

pdf.body_text(
    'Data Constants:\n'
    '  11 Antenna Hubs (ANTENNA_HUBS): Site-1, Zaqatala, Site-7, Site-6, Qazax,\n'
    '    Site-4, Site-8, Site-3, Sirvan, Site-5, Astara - each with uvX, uvY\n\n'
    '  20 FM Stations (RADIO_STATIONS): Planet FM, Ictimai Radio, Yurd FM,\n'
    '    Dalga FM, Saglam Radio, Kepaz FM / Site-1 Retro FM, Site-1 Hit FM,\n'
    '    ASAN Radio, TMB Radio, Radio Antenn, Enerji FM, CBC Radio,\n'
    '    Xezer FM, Araz FM, Radio Space, Region Radiosu, 106 FM,\n'
    '    Azad Region, Real Radio, Avto FM\n'
    '  Statuses: ONLINE, WARNING, OFFLINE\n\n'
    '  Hub Geo coordinates (HUB_GEO): lat/lon for Open-Meteo API\n'
    '  WMO weather descriptions (WMO_DESC): Clear, Foggy, Light Rain, etc.'
)

pdf.add_page()
pdf.subsection_title('Component: HubInfoWidget (Lines 133-239)')
pdf.body_text(
    'A weather card in the top-left.\n\n'
    'Position: absolute, top:20, left:20, zIndex:10\n'
    'Size: width:200px, padding 14px 18px\n'
    'Font: APPLE_FONT\n\n'
    'Layout:\n'
    '  Header row with LEFT-POINTING ANGLE BRACKET / RIGHT-POINTING ANGLE BRACKET navigation buttons\n'
    '    Button style: background:none, border:none, color:rgba(255,255,255,0.50), fontSize:18\n'
    '  Live dot: 5x5px circle, #0a84ff, ap-pulse animation (2.4s)\n'
    '  Hub name: fontSize:10, fontWeight:600, color:rgba(255,255,255,0.75),\n'
    '            letterSpacing:0.08em, textTransform:uppercase\n'
    '  Temperature: fontSize:32, fontWeight:200, #fff\n'
    '  Unit C: fontSize:11, fontWeight:300, rgba(255,255,255,0.40)\n'
    '  Weather text: fontSize:12, fontWeight:300, rgba(255,255,255,0.55)\n'
    '  Divider: borderTop:1px solid rgba(255,255,255,0.07)\n'
    '  Wind & Hub ID sub-stats: labels at fontSize:8, rgba(255,255,255,0.25), uppercase;\n'
    '                           values at fontSize:13, rgba(255,255,255,0.70)\n\n'
    'Progress bar (auto-advance timer):\n'
    '  Track: height:2, background:rgba(255,255,255,0.07), borderRadius:1\n'
    '  Fill: background:rgba(10,132,255,0.55), animation wx-bar 10s linear both'
)

pdf.subsection_title('Component: HubNode / HubMarkers (Lines 244-273)')
pdf.body_text(
    '3D glowing sphere markers rendered inside the R3F Canvas.\n\n'
    'Halo: sphereGeometry args=[0.09, 16, 16], meshBasicMaterial color=#0a84ff,\n'
    '      transparent opacity=0.08, depthWrite=false\n'
    'Core: sphereGeometry args=[0.030, 16, 16], meshBasicMaterial color=#0a84ff,\n'
    '      transparent opacity=0.85\n'
    'Animation: pulsating opacity via useFrame (0.65 + 0.25 * sin(t * 2.2 + hub.id))'
)

pdf.subsection_title('City Color Palette (CITY_DATA) (Lines 278-289)')
pdf.body_text(
    'Per-city CSS colors (stored for shader/laser effects):\n'
    '  Zaqatala #00ffff, Site-7 #4dff00, Site-6 #ffd900, Qazax #3380ff,\n'
    '  Site-4 #ff7300, Site-8 #cc00ff, Site-3 #00ff80,\n'
    '  Sirvan #ff3399, Site-5 #80ff00, Astara #ff1a1a'
)

pdf.add_page()
pdf.subsection_title('Custom GLSL Shaders')
pdf.body_text(
    'Vertex Shader (Lines 296-394):\n'
    '  Uniforms: uTime, uPeakIntensity, uNoiseScale, uPeakRadius, uPeakPosition,\n'
    '    uHoverActive, uHoverPosition, uHoverRadius, uHoverHeight, uAlphaMap,\n'
    '    uCustomPeakCount, arrays for peaks, uLaserProgress\n'
    '  Includes a Simplex noise implementation (snoise)\n'
    '  Displaces Z with 4 layered noise waves (wave1-4)\n'
    '  Adds Site-1 needle peak, hover peak, and custom city peaks\n'
    '  Outputs: vHeight, vZ, vUv\n\n'
    'Fragment Shader (Lines 400-512):\n'
    '  Samples alpha mask (uAlphaMap) to discard outside Region\n'
    '  Volumetric height gradient:\n'
    '    voidColor = vec3(0.0, 0.02, 0.05)  (near-black)\n'
    '    cyanColor = vec3(0.0, 0.90, 1.00)  (electric cyan)\n'
    '    redColor  = vec3(1.0, 0.10, 0.20)  (hot red)\n'
    '    Mixes based on zNorm with smoothstep\n'
    '  Site-1 aura: tight red glow near peak tip\n'
    '  Projectile bolts (lasers): capsule-shaped, city-colored, with inner white core\n'
    '  Impact rings at Site-1 and launch rings at source cities\n'
    '  Blending: THREE.AdditiveBlending'
)

pdf.subsection_title('Component: WaveSurface (Lines 517-621)')
pdf.body_text(
    'The main terrain mesh.\n\n'
    'Geometry: planeGeometry args=[10, 8, 256, 256] (high-segment plane)\n'
    'Transform: rotation=[-Math.PI/2, 0, 0], scale=[1, -1, 1]\n'
    'Material: shaderMaterial with the custom GLSL above\n'
    '  transparent=true, depthWrite=false,\n'
    '  blending=THREE.AdditiveBlending, side=THREE.FrontSide\n\n'
    'Interaction:\n'
    '  onPointerMove: sets hover refs, calls onPeakClick(uv, hover)\n'
    '  onPointerOut: clears hover\n'
    '  onClick: triggers Site-1 zoom if within radius'
)

pdf.subsection_title('Component: NeonOutline (Lines 631-685)')
pdf.body_text(
    'GeoJSON border drawn as cyan line segments.\n\n'
    'Material: lineBasicMaterial color="#00e5ff", linewidth=1,\n'
    '          transparent opacity=1.0, depthWrite=false\n'
    'Computed from region.json coordinates'
)

pdf.subsection_title('Helper: applyNeonMaterial (Lines 688-701)')
pdf.body_text(
    'Replaces all mesh materials in a GLB with:\n'
    '  MeshStandardMaterial\n'
    '  emissiveIntensity default 2.5\n'
    '  roughness: 0.3, metalness: 0.7'
)

pdf.add_page()
pdf.subsection_title('Component: AntennaModel (Lines 706-778)')
pdf.body_text(
    'City antenna GLB (antenna.glb).\n\n'
    'Imported via useGLTF(antennaGlb)\n'
    'Cloned scene with emissive boost +4.0\n'
    'Scale: 0.018\n\n'
    'Hover lift: lerps Y position based on distance to hover UV\n'
    '  (0.45 * 0.3125 * (1 - dist/0.12))\n'
    'Emissive lerp: 4.0 -> 12.0 on pointer hover\n\n'
    'Tooltip: @react-three/drei Html with inline style:\n'
    '  fontFamily: APPLE_FONT\n'
    '  fontSize: 11, fontWeight: 600, #fff\n'
    '  textShadow: 0 0 10px {color}, 0 1px 3px rgba(0,0,0,0.9)\n'
    '  whiteSpace: nowrap, letterSpacing: 0.02em'
)

pdf.subsection_title('Component: MainPeakModel (Lines 783-820)')
pdf.body_text(
    'Special Site-1 GLB (mainpeak.glb).\n\n'
    'Scale: 0.36\n'
    'Base Y: 0.90\n'
    'Hover lift: up to +0.65 * 0.3125\n'
    'Emissive boost +2.0'
)

pdf.subsection_title('Component: FixedCamera (Lines 826-849)')
pdf.body_text(
    'Default position: computed from angle -20 degrees, distance 7.5, height 9.5\n'
    'Look-at: (0, 0, 0.2)\n'
    'Zoom-to-Site-1: lerps FOV to 12, position to (3.3, 1.8, 2.2),\n'
    '             lookAt to Site-1 world coordinates'
)

pdf.add_page()
pdf.subsection_title('Component: StationPanel (Lines 858-1030)')
pdf.body_text(
    'Right-side control panel. Wrapped in memo to isolate from Canvas re-renders.\n\n'
    'Position: absolute, top:20, right:20, bottom:20, width:260\n'
    'Glass: spreads GLASS object\n'
    'Font: APPLE_FONT\n'
    'Animation: ap-fadein 0.5s 0.1s ease both\n\n'
    'Header (Lines 898-976):\n'
    '  Title: "FM Broadcast", fontSize:15, fontWeight:600, #fff\n'
    '  Subtitle: dynamic state text\n'
    '  Log controls (when not playing):\n'
    '    Start/Stop log button: STOP SIGN Stop Log / RECORDING Log CSV\n'
    '      Active: background:rgba(255,159,10,0.18), border:rgba(255,159,10,0.45), #ff9f0a\n'
    '      Inactive: background:rgba(255,255,255,0.07), border:rgba(255,255,255,0.15)\n'
    '    Mode switch: SATELLITE ANTENNA 2 MHz / GLOBE 20 MHz\n'
    '      Active pill: background:rgba(10,132,255,0.22), border:rgba(10,132,255,0.55), #0a84ff\n'
    '  Audio status (when playing):\n'
    '    Badge background:\n'
    '      signal -> rgba(48,209,88,0.15) / #30d158\n'
    '      noise  -> rgba(255,159,10,0.15) / #ff9f0a\n'
    '      silence-> rgba(255,255,255,0.07)\n'
    '    Pulsing dot on signal (ap-pulse 1.8s)\n'
    '    Quality bar: 80x3px track, fill width = audioQuality * 100%\n'
    '    Stop button: background:rgba(255,69,58,0.12), border:rgba(255,69,58,0.30), #ff453a\n\n'
    'Station List (Lines 979-1021):\n'
    '  Scrollable, hidden scrollbar (ap-panel-scroll)\n'
    '  Each row: display:flex, padding:7px 8px, borderRadius:8\n'
    '  Playing row: background:rgba(10,132,255,0.12), outline:1px solid rgba(10,132,255,0.30)\n'
    '  Signal dot (left):\n'
    '    Strong (>=-47 dBm): #30d158\n'
    '    Medium (>=-57 dBm): #ff9f0a\n'
    '    Weak: #ff453a\n'
    '    Pulse animation on strong\n'
    '  Audio dot (left):\n'
    '    SNR >= 12 dB -> #30d158\n'
    '    SNR >= 6 dB  -> #ff9f0a\n'
    '    else         -> #ff453a\n'
    '  Frequency: fontSize:13, fontWeight:600, #0a84ff (playing) or default\n'
    '  Station name: fontSize:11, dynamic color based on SNR\n'
    '  Scan details: fontSize:8, fontWeight:300, shows {pwr} dBm {snr} dB SNR\n\n'
    'Footer:\n'
    '  SDR-NET v2.4.1 Site-1, Region\n'
    '  fontSize:10, fontWeight:300, rgba(255,255,255,0.25)'
)

pdf.add_page()
pdf.subsection_title('Main App Component (Lines 1035-1577)')
pdf.body_text(
    'State & Refs:\n'
    '  peakRadius, peakX, peakY, hoverRadius, hoverHeight\n'
    '  showSecretPanel\n'
    '  playingIdx, audioQuality, audioState\n'
    '  logRunning, logPath, logMode (2mhz / 20mhz)\n'
    '  panoramicPeaks\n'
    '  cameraZoomRef for imperative camera animation\n'
    '  customPeaks (10 cities)\n'
    '  animRef (laser progress arrays, no re-renders)\n'
    '  audioContextRef, hissNodeRef, gainNodeRef for pre-warmed WebAudio\n'
    '  fadeOverlayRef for imperative DOM fade\n\n'
    'Canvas Setup (Lines 1368-1415):\n'
    '  dpr={1}\n'
    '  WebGL: antialias=true, toneMapping=THREE.ACESFilmicToneMapping,\n'
    '         toneMappingExposure=1.25, powerPreference=high-performance\n'
    '  Camera: fov:35, near:0.1, far:100\n'
    '  Resize debounce: 200ms\n\n'
    'Scene Contents:\n'
    '  <FixedCamera zoomRef={cameraZoomRef} />\n'
    '  <ambientLight intensity={1.2} color="#6699ff" />\n'
    '  <directionalLight position={[5, -5, 8]} intensity={1.4} color="#ffffff" />\n'
    '  <pointLight position={[0, 6, 0]} intensity={3.0} color="#00eeff" distance={18} />\n'
    '  <pointLight position={[-3, 4, -2]} intensity={1.5} color="#8855ff" distance={12} />\n'
    '  <NeonOutline />\n'
    '  <WaveSurface ... />\n'
    '  <MainPeakModel ... />\n'
    '  <HubMarkers />\n'
    '  10x <AntennaModel ... /> (one per custom peak)\n'
    '  <EffectComposer>:\n'
    '    <Bloom luminanceThreshold={0.3} luminanceSmoothing={0.7} intensity={2.5} mipmapBlur={true} />\n'
    '    <Vignette eskil={false} offset={0.25} darkness={0.82} />'
)

pdf.body_text(
    'UI Layer (Lines 1424-1574):\n\n'
    'Top Header Bar:\n'
    '  Centered pill using GLASS + borderRadius:22\n'
    '  Height: 44px, padding 0 28px\n'
    '  Live dot: #30d158, ap-pulse 2.4s\n'
    '  Text: "Region State Radio Monitoring", fontSize:12, fontWeight:500,\n'
    '        rgba(255,255,255,0.85)\n'
    '  Divider: 1x20px at rgba(255,255,255,0.10)\n'
    '  Stats: "FM Band Monitor" (#30d158), "21 stations" (rgba(255,255,255,0.40))\n\n'
    'Bottom Status Bar:\n'
    '  Centered pill, padding:6px 18px, borderRadius:20\n'
    '  Items: Noise Floor -85 dBm, Sweep 00:04:12, Classification Restricted\n'
    '  Label color: rgba(255,255,255,0.20), value: rgba(255,255,255,0.40)\n\n'
    'Secret Button / Panel:\n'
    '  Button: "cgrep v1.1", background:rgba(20,20,20,0.8), color:#555,\n'
    '          border:1px solid #333, fontFamily:monospace\n'
    '  Panel: background:rgba(10,10,15,0.95), border:1px solid #333, minWidth:350\n'
    '  Controls header: color:#00ffb8, fontFamily:monospace, letterSpacing:0.15em\n'
    '  Save/Load buttons: #2196F3 / #FF9800\n'
    '  Peak editors: input[type=text], input[type=range]\n'
    '    accentColor:#ff9900 (XY) and #00ffb8 (rotation)'
)

# ---------------------------------------------------------------------------
# 4.6 SigintDashboard.jsx
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.chapter_title('4.6 SigintDashboard.jsx  -  FM Signal Dashboard')
pdf.body_text(
    'File: f:\\SDR\\CGrep\\sdr-dashboard\\src\\SigintDashboard.jsx  (546 lines)\n\n'
    'Injected Styles (Lines 5-21):\n'
    '  @keyframes sigBlink { 0%,100%{opacity:1} 50%{opacity:0} }\n'
    '  .sig-blink { animation: sigBlink 0.65s ease-in-out infinite; }\n'
    '  @keyframes sigFlash { 0%,100%{opacity:0} 50%{opacity:0.18} }\n'
    '  .sig-flash { animation: sigFlash 0.65s ease-in-out infinite; }'
)

pdf.body_text(
    'Design Tokens (Lines 23-35):\n'
    '  SYS   = Inter,system-ui,-apple-system,SF Pro Display,sans-serif\n'
    '  MONO  = Inter,SF Pro Display,system-ui,sans-serif\n'
    '  DIGIT = DSDigit,Inter,system-ui,sans-serif\n'
    '  CYAN  = #3b82f6, RED = #ef4444\n'
    '  DBFS_MIN = -100, DBFS_MAX = -20, RED_THRESHOLD_DBFS = -75\n\n'
    'Dynamic Heights:\n'
    '  TOTAL_H() = max(180, window.innerHeight - 180)\n'
    '  FREQ_LABEL_H() = TOTAL_H() / 3\n'
    '  getBarH() = TOTAL_H() - FREQ_LABEL_H()'
)

pdf.body_text(
    'Color Math (Lines 37-73):\n'
    '  3-stop interpolation for LED bars:\n'
    '    Weak signal (CR):   #ef4444 (soft red)\n'
    '    Mid signal (CDK):   #1d4ed8 (deep blue)\n'
    '    Strong signal (CP): #3b82f6 (vibrant blue)\n'
    '  Function colorAt(p) interpolates with cubic easing in red-to-purple zone.'
)

pdf.body_text(
    'Station Data (Lines 85-106):\n'
    '  20 whitelisted stations: 88.7, 90.0, 90.7, 92.5, 93.0, 93.3, 97.7,\n'
    '    100.0, 100.5, 101.0, 101.1, 102.8, 103.0, 103.3, 104.0, 105.0,\n'
    '    106.0, 106.3, 107.0, 107.7 MHz.'
)

pdf.subsection_title('Component: Stat (Lines 113-128)')
pdf.body_text(
    'display:flex, flexDirection:column, alignItems:center, gap:2\n'
    'Value: fontSize:22, fontWeight:700, custom accent color, letterSpacing:-0.03em\n'
    'Optional textShadow glow\n'
    'Label: fontSize:10, letterSpacing:0.06em, fontWeight:500,\n'
    '       rgba(255,255,255,0.28), uppercase'
)

pdf.subsection_title('Spring Configs (Lines 130-132)')
pdf.body_text(
    'PANEL_SPRING: {type:spring, stiffness:340, damping:34, mass:1.0}\n'
    'TAB_SPRING:   {type:spring, stiffness:420, damping:40, mass:0.7}\n'
    'BAR_SPRING:   {type:spring, stiffness:85,  damping:17, mass:0.9}'
)

pdf.add_page()
pdf.subsection_title('Component: LEDColumn (Lines 134-245)')
pdf.body_text(
    'Custom physics-driven LED meter.\n\n'
    'Constants:\n'
    '  HOLD_MS = 400  (peak hold time)\n'
    '  GRAVITY = colH * 1.8  (peak fall speed)\n'
    '  LERP_K  = 0.14\n\n'
    'Structure:\n'
    '  Unlit segments: background: rgba(255,255,255,0.05)\n'
    '                  threshold segment: rgba(255,59,48,0.20)\n'
    '  Lit clip: motion.div with height: barMV (driven by useMotionValue)\n'
    '  Each lit segment gets its own interpolated RGB + per-segment glow\n'
    '  Threshold segment: background: rgba(255,59,48,0.95),\n'
    '                     boxShadow: 0 0 6px 2px rgba(255,59,48,0.80)\n'
    '  Lit segment glow: 0 0 5px rgba(r,g,b,0.9), 0 0 12px rgba(r,g,b,0.45)\n\n'
    'Peak indicator: 2px white line, rgba(255,255,255,0.80),\n'
    '  boxShadow: 0 0 4px 1px rgba(255,255,255,0.75),\n'
    '             0 0 14px 3px rgba(255,255,255,0.28)\n\n'
    'Red alert: sig-flash class overlay, rgba(239,68,68,1)'
)

pdf.subsection_title('Main SigintDashboard Export (Lines 247-546)')
pdf.body_text(
    'State: open, data, barH, freqH, redCount, belowRef\n\n'
    'Panel Shell (Lines 338-494):\n'
    '  AnimatePresence + motion.div\n'
    '  Entry: x:"-100%" -> x:0\n'
    '  Exit: x:"-100%"\n'
    '  Background: rgba(18,22,28,0.82)\n'
    '  backdropFilter: blur(30px) saturate(140%)\n'
    '  borderRight: 1px solid rgba(255,255,255,0.08)\n'
    '  boxShadow: inset 0 1px 0 rgba(255,255,255,0.06), 8px 0 40px rgba(0,0,0,0.4)\n'
    '  Padding: 32px 44px 20px, gap 18px'
)

pdf.body_text(
    'Header Row (Lines 359-412):\n'
    '  3-column grid (1fr auto 1fr)\n'
    '  Left stats: Avg Signal, Active (cyan glow), Below threshold (red pulse)\n'
    '  Center title: cyan dot (#3b82f6, pulsing 2.2s), "FM Signal Dashboard",\n'
    '                fontSize:18, fontWeight:600\n'
    '  Right close button: MULTIPLICATION SIGN, background:rgba(255,255,255,0.06),\n'
    '                     border:1px solid rgba(255,255,255,0.09), borderRadius:10'
)

pdf.body_text(
    'Chart Area (Lines 419-492):\n'
    '  Background: rgba(12,15,20,0.45), borderRadius:20,\n'
    '              border:1px solid rgba(255,255,255,0.05)\n'
    '  20 vertical columns, flex:1, maxWidth:48, gap:8\n'
    '  Per-station layout:\n'
    '    1. dBFS label: fontSize:10, fontWeight:500, red if below threshold\n'
    '    2. LED column: LEDColumn component with litCount and threshSeg\n'
    '    3. FM frequency label:\n'
    '       Font: DIGIT (DSDigit), fontSize:34, fontWeight:700, #ef4444\n'
    '       writingMode:vertical-rl, transform:rotate(180deg)\n'
    '       Dynamic glow based on signal strength\n'
    '       Blinking: sig-blink class when isRed'
)

pdf.body_text(
    'Toggle Tab (Lines 498-542):\n'
    '  Position: fixed, left:0, top:50%\n'
    '  borderRadius: "0 14px 14px 0"\n'
    '  Background: rgba(5,7,11,0.94)\n'
    '  Border: 1px solid rgba(0,229,255,0.18), no left border\n'
    '  Backdrop blur: 20px\n'
    '  Contents:\n'
    '    Pulsing green dot: #30d158, boxShadow:0 0 9px #30d158\n'
    '    Text: "Dashboard", writingMode:vertical-rl, transform:rotate(180deg)\n'
    '    Chevron: RIGHT-POINTING ANGLE BRACKET, color:CYAN, textShadow:0 0 8px {CYAN}'
)

# ---------------------------------------------------------------------------
# 4.7 Assets
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.chapter_title('4.7 Assets')
pdf.body_text(
    'File                          | Type   | Usage\n'
    '------------------------------|--------|-----------------------------------\n'
    'DS-DIGIT.TTF                  | Font   | @font-face DSDigit for FM labels\n'
    'favicon.svg                   | Icon   | Browser tab icon\n'
    'region_mask.png           | PNG    | Shader alpha mask for terrain shape\n'
    'region.json               | GeoJSON| NeonOutline border geometry\n'
    'antenna.glb                   | GLB    | City antenna 3D model\n'
    'mainpeak.glb                  | GLB    | Site-1 special peak 3D model'
)

# ---------------------------------------------------------------------------
# Summary Table
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.chapter_title('Summary Table')
pdf.body_text(
    'File           | UI Framework      | Key Libraries                | Key Fonts                           | Visual Style\n'
    '---------------|-------------------|------------------------------|-------------------------------------|------------------------------------------\n'
    'calculate.py   | None              | numpy, scipy                 | -                                   | -\n'
    'dashboard.py   | PyQt6             | pyqtgraph, sounddevice       | monospace (Qt)                      | Dark grey #0e0e0e, neon cyan #00ffcc,\n'
    '               |                   |                              |                                     | orange #ffaa00\n'
    'server.py      | None              | fastapi, uvicorn             | -                                   | -\n'
    'sdr-dashboard  | React 19 + R3F    | three, @react-three/*,       | Inter, DSDigit, SF Pro Display,     | Glassmorphism, additive blending,\n'
    '               |                   | framer-motion                | system-ui                           | neon cyan/red, vertical-rl labels,\n'
    '               |                   |                              |                                     | LED physics bars'
)

# ---------------------------------------------------------------------------
pdf.output('SDR_DRI_UI_Breakdown.pdf')
print('PDF generated: SDR_DRI_UI_Breakdown.pdf')
