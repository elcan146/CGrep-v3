import React, { useState, useEffect, useRef } from 'react';
import { motion, useMotionValue } from 'framer-motion';

/**
 * SpectrumVisualization
 * 
 * Standalone spectrum graph component using LED-style bars.
 * Can be embedded anywhere and connected to any data source (WebSocket, HTTP polling, SQL DB, etc.)
 * 
 * Props:
 *  - data: Array of { freq, dbfs } objects or raw FFT array
 *  - onDataChange: Optional callback when data updates
 *  - height: Custom height in pixels (default: auto-responsive)
 *  - colorScheme: 'default' | 'monochrome' | 'thermal' (default: 'default')
 *  - showLabels: Show frequency labels (default: true)
 *  - title: Optional title to display
 *  - dbfsMin: Minimum dBFS value (default: -100)
 *  - dbfsMax: Maximum dBFS value (default: -20)
 */

// Design tokens
const SYS = `'Space Grotesk', 'Segoe UI', sans-serif`;
const MONO = `'JetBrains Mono', 'Courier New', monospace`;
const CYAN = '#00e5ff';
const ORANGE = '#ff5722';
const PURPLE = '#4a00e0';

// Color schemes
const COLOR_SCHEMES = {
  default: {
    low: { r: 0xff, g: 0x57, b: 0x22 },    // Orange
    mid: { r: 0x4a, g: 0x00, b: 0xe0 },    // Purple
    high: { r: 0x00, g: 0xe5, b: 0xff },   // Cyan
  },
  monochrome: {
    low: { r: 0x40, g: 0x40, b: 0x40 },    // Dark gray
    mid: { r: 0x80, g: 0x80, b: 0x80 },    // Gray
    high: { r: 0xff, g: 0xff, b: 0xff },   // White
  },
  thermal: {
    low: { r: 0x00, g: 0x00, b: 0x00 },    // Black
    mid: { r: 0xff, g: 0x7f, b: 0x00 },    // Orange
    high: { r: 0xff, g: 0x00, b: 0x00 },   // Red
  },
};

function interpolateColor(p, scheme) {
  const colorDef = COLOR_SCHEMES[scheme] || COLOR_SCHEMES.default;
  if (p >= 0.5) {
    const t = (p - 0.5) / 0.5;
    return {
      r: Math.round(colorDef.mid.r + (colorDef.high.r - colorDef.mid.r) * t),
      g: Math.round(colorDef.mid.g + (colorDef.high.g - colorDef.mid.g) * t),
      b: Math.round(colorDef.mid.b + (colorDef.high.b - colorDef.mid.b) * t),
    };
  } else {
    const t = (p / 0.5);
    const c = t * t * t;
    return {
      r: Math.round(colorDef.low.r + (colorDef.mid.r - colorDef.low.r) * c),
      g: Math.round(colorDef.low.g + (colorDef.mid.g - colorDef.low.g) * c),
      b: Math.round(colorDef.low.b + (colorDef.mid.b - colorDef.low.b) * c),
    };
  }
}

function SpectrumBar({ value, label, height, barWidth, dbfsMin, dbfsMax, colorScheme, showLabel }) {
  const pct = Math.max(0, Math.min(1, (value - dbfsMin) / (dbfsMax - dbfsMin)));
  const barHeightPx = pct * height;
  const color = interpolateColor(pct, colorScheme);
  const colorStr = `rgb(${color.r},${color.g},${color.b})`;

  const motionValue = useMotionValue(barHeightPx);

  React.useEffect(() => {
    motionValue.set(barHeightPx);
  }, [barHeightPx, motionValue]);

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: 8,
      flex: 1,
    }}>
      <div style={{
        width: barWidth,
        height: height,
        background: 'rgba(255,255,255,0.05)',
        borderRadius: 4,
        position: 'relative',
        overflow: 'hidden',
      }}>
        <motion.div
          style={{
            height: motionValue,
            width: '100%',
            background: colorStr,
            borderRadius: 4,
            position: 'absolute',
            bottom: 0,
            left: 0,
            boxShadow: `0 0 12px rgba(${color.r},${color.g},${color.b},0.8)`,
            transition: 'background 0.05s',
          }}
        />
      </div>
      {showLabel && label && (
        <span style={{
          fontFamily: MONO,
          fontSize: 10,
          color: 'rgba(255,255,255,0.5)',
          fontWeight: 500,
          textAlign: 'center',
          width: '100%',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          {label}
        </span>
      )}
    </div>
  );
}

export default function SpectrumVisualization({
  data = [],
  onDataChange,
  height = 300,
  colorScheme = 'default',
  showLabels = true,
  title,
  dbfsMin = 47,
  dbfsMax = 107,
}) {
  const [displayData, setDisplayData] = useState(data);
  const containerRef = useRef(null);
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => {
    const updateWidth = () => {
      if (containerRef.current) {
        setContainerWidth(containerRef.current.offsetWidth);
      }
    };
    updateWidth();
    const resizeObserver = new ResizeObserver(updateWidth);
    if (containerRef.current) {
      resizeObserver.observe(containerRef.current);
    }
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    setDisplayData(data);
    if (onDataChange) onDataChange(data);
  }, [data, onDataChange]);

  // Calculate bar width based on number of bars and container width
  const barCount = Math.max(1, displayData.length);
  const barWidth = Math.max(4, Math.floor(containerWidth / barCount) - 2);

  return (
    <div
      ref={containerRef}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        padding: 20,
        background: 'rgba(10,14,23,0.5)',
        border: '1px solid rgba(0,229,255,0.1)',
        borderRadius: 12,
        backdropFilter: 'blur(8px)',
        width: '100%',
        minHeight: 'auto',
      }}
    >
      {title && (
        <div style={{
          fontFamily: SYS,
          fontSize: 16,
          fontWeight: 600,
          color: CYAN,
          letterSpacing: '-0.02em',
          textTransform: 'uppercase',
        }}>
          {title}
        </div>
      )}

      <div
        style={{
          display: 'flex',
          gap: 4,
          alignItems: 'flex-end',
          justifyContent: 'center',
          height: height,
          minWidth: 0,
        }}
      >
        {displayData.length > 0 ? (
          displayData.map((item, idx) => (
            <SpectrumBar
              key={idx}
              value={typeof item === 'object' ? item.dbfs : item}
              label={typeof item === 'object' ? item.label || item.freq : `${idx}`}
              height={height}
              barWidth={barWidth}
              dbfsMin={dbfsMin}
              dbfsMax={dbfsMax}
              colorScheme={colorScheme}
              showLabel={showLabels}
            />
          ))
        ) : (
          <div style={{
            color: 'rgba(255,255,255,0.3)',
            fontFamily: MONO,
            fontSize: 12,
            textAlign: 'center',
          }}>
            Waiting for data...
          </div>
        )}
      </div>

      {displayData.length > 0 && (
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          paddingTop: 8,
          borderTop: '1px solid rgba(0,229,255,0.1)',
          fontSize: 11,
          color: 'rgba(255,255,255,0.4)',
          fontFamily: MONO,
        }}>
          <span>{dbfsMin} dBFS</span>
          <span>{dbfsMax} dBFS</span>
        </div>
      )}
    </div>
  );
}
