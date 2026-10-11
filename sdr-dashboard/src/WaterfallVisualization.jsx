import React, { useState, useEffect, useRef } from 'react';

/**
 * WaterfallVisualization
 * 
 * Waterfall spectrogram component - displays FFT data history as scrolling heatmap
 * Can be embedded anywhere and connected to any data source
 * 
 * Props:
 *  - fftData: Array of FFT values (typically 256-2048 bins)
 *  - historyLength: Number of rows to display (default: 100)
 *  - updateInterval: ms between updates (default: 500)
 *  - colorScheme: 'thermal' | 'plasma' | 'viridis' | 'grayscale' (default: 'thermal')
 *  - dbfsMin: Minimum dBFS (default: -100)
 *  - dbfsMax: Maximum dBFS (default: -20)
 *  - title: Optional title
 *  - showFrequencyAxis: Show frequency labels (default: true)
 *  - width: Custom width (default: 100%)
 *  - height: Custom height (default: 400px)
 */

const COLOR_MAPS = {
  thermal: [
    '#000000', // black
    '#1a0033', '#330066', '#4d0099', '#6600cc',
    '#7f00ff', '#9933ff', '#b366ff', '#cc99ff',
    '#ff3300', '#ff6600', '#ff9900', '#ffcc00',
    '#ffff00', '#ffffff',
  ],
  plasma: [
    '#0d0887', '#46039f', '#7201a8', '#9c179e',
    '#bd3786', '#d8576b', '#ed7953', '#fb9f3a',
    '#fdca26', '#f0f921',
  ],
  viridis: [
    '#440154', '#482878', '#3e4a89', '#31688e',
    '#26828e', '#35b779', '#6ece58', '#b5de2b', '#fde724',
  ],
  grayscale: [
    '#000000', '#1a1a1a', '#333333', '#4d4d4d',
    '#666666', '#808080', '#999999', '#b3b3b3',
    '#cccccc', '#e6e6e6', '#ffffff',
  ],
};

function getColorForIntensity(intensity, colorScheme = 'thermal') {
  const palette = COLOR_MAPS[colorScheme] || COLOR_MAPS.thermal;
  const index = Math.floor(Math.max(0, Math.min(1, intensity)) * (palette.length - 1));
  return palette[index];
}

export default function WaterfallVisualization({
  fftData = [],
  historyLength = 100,
  updateInterval = 500,
  colorScheme = 'thermal',
  dbfsMin = -100,
  dbfsMax = -20,
  title = 'Waterfall',
  showFrequencyAxis = true,
  width = '100%',
  height = 400,
}) {
  const canvasRef = useRef(null);
  const historyRef = useRef([]);
  const animationFrameRef = useRef(null);
  const lastUpdateRef = useRef(Date.now());

  // Normalize FFT data to 0-1 range
  const normalizeData = (data) => {
    if (!data || data.length === 0) return [];
    return data.map(val => {
      const clamped = Math.max(dbfsMin, Math.min(dbfsMax, val));
      return (clamped - dbfsMin) / (dbfsMax - dbfsMin);
    });
  };

  // Update history when new FFT data arrives
  useEffect(() => {
    const now = Date.now();
    if (now - lastUpdateRef.current >= updateInterval) {
      const normalized = normalizeData(fftData);
      if (normalized.length > 0) {
        historyRef.current.unshift(normalized);
        if (historyRef.current.length > historyLength) {
          historyRef.current.pop();
        }
        lastUpdateRef.current = now;
      }
    }
  }, [fftData, updateInterval, historyLength, dbfsMin, dbfsMax]);

  // Draw on canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let lastTs = 0;
    const FRAME_MS = 1000 / 10;

    const draw = (ts) => {
      animationFrameRef.current = requestAnimationFrame(draw);
      if (document.hidden) return;
      if (ts - lastTs < FRAME_MS) return;
      lastTs = ts;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const width = canvas.offsetWidth;
      const height = canvas.offsetHeight;
      canvas.width = width * window.devicePixelRatio;
      canvas.height = height * window.devicePixelRatio;
      ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

      // Clear background
      ctx.fillStyle = 'rgba(10, 14, 23, 0.8)';
      ctx.fillRect(0, 0, width, height);

      const history = historyRef.current;
      if (history.length === 0) {
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.font = '12px monospace';
        ctx.fillText('Waiting for data...', width / 2 - 50, height / 2);
        return;
      }

      const binCount = history[0].length;
      const binWidth = width / binCount;
      const rowHeight = Math.max(1, height / historyLength);

      // Draw each row of history (newest at top)
      history.forEach((row, rowIdx) => {
        const y = rowIdx * rowHeight;
        row.forEach((intensity, binIdx) => {
          const x = binIdx * binWidth;
          const color = getColorForIntensity(intensity, colorScheme);
          ctx.fillStyle = color;
          ctx.fillRect(x, y, binWidth + 1, rowHeight + 1);
        });
      });

      // Draw grid overlay (optional)
      ctx.strokeStyle = 'rgba(0,229,255,0.05)';
      ctx.lineWidth = 0.5;
      for (let i = 0; i <= historyLength; i += 10) {
        ctx.beginPath();
        ctx.moveTo(0, (i / historyLength) * height);
        ctx.lineTo(width, (i / historyLength) * height);
        ctx.stroke();
      }

    };

    animationFrameRef.current = requestAnimationFrame(draw);

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [historyLength, colorScheme]);

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
      width: width,
      padding: 20,
      background: 'rgba(10,14,23,0.5)',
      border: '1px solid rgba(0,229,255,0.1)',
      borderRadius: 12,
      backdropFilter: 'blur(8px)',
    }}>
      {title && (
        <div style={{
          fontFamily: `'Space Grotesk', sans-serif`,
          fontSize: 16,
          fontWeight: 600,
          color: '#00e5ff',
          letterSpacing: '-0.02em',
          textTransform: 'uppercase',
        }}>
          {title}
        </div>
      )}

      <div style={{
        position: 'relative',
        width: '100%',
        height: height,
        borderRadius: 8,
        overflow: 'hidden',
        background: 'linear-gradient(135deg, rgba(10,14,23,0.9) 0%, rgba(20,30,50,0.9) 100%)',
        border: '1px solid rgba(0,229,255,0.1)',
      }}>
        <canvas
          ref={canvasRef}
          style={{
            width: '100%',
            height: '100%',
            display: 'block',
          }}
        />

        {/* Frequency axis labels */}
        {showFrequencyAxis && (
          <div style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            display: 'flex',
            justifyContent: 'space-between',
            padding: '4px 8px',
            fontSize: 10,
            fontFamily: `'JetBrains Mono', monospace`,
            color: 'rgba(0,229,255,0.4)',
            background: 'rgba(10,14,23,0.5)',
            pointerEvents: 'none',
          }}>
            <span>0 Hz</span>
            <span>Mid</span>
            <span>Max</span>
          </div>
        )}

        {/* Time axis label */}
        <div style={{
          position: 'absolute',
          top: 4,
          right: 8,
          fontSize: 9,
          fontFamily: `'JetBrains Mono', monospace`,
          color: 'rgba(0,229,255,0.3)',
          pointerEvents: 'none',
        }}>
          Time →
        </div>
      </div>

      {/* Legend */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        paddingTop: 8,
        borderTop: '1px solid rgba(0,229,255,0.1)',
        fontSize: 11,
        fontFamily: `'JetBrains Mono', monospace`,
        color: 'rgba(255,255,255,0.4)',
      }}>
        <div style={{ display: 'flex', gap: 16 }}>
          <div>
            <span style={{ display: 'inline-block', width: 12, height: 12, background: '#000000', marginRight: 6, borderRadius: 2 }} />
            {dbfsMin} dBFS
          </div>
          <div>
            <span style={{ display: 'inline-block', width: 12, height: 12, background: '#ffffff', marginRight: 6, borderRadius: 2 }} />
            {dbfsMax} dBFS
          </div>
        </div>
        <span>Color: {colorScheme}</span>
      </div>
    </div>
  );
}
