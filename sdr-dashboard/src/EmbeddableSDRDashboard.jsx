import React, { useState, useEffect, useRef, useCallback } from 'react';
import SpectrumVisualization from './SpectrumVisualization';
import WaterfallVisualization from './WaterfallVisualization';

/**
 * EmbeddableSDRDashboard
 * 
 * Complete spectrum + waterfall visualization component for embedding on any website.
 * Supports multiple data source modes: WebSocket, HTTP polling, or direct data injection.
 * 
 * Props:
 *  - mode: 'websocket' | 'polling' | 'direct' (default: 'websocket')
 *  - dataSource: URL or callback function (required for modes other than 'direct')
 *  - onData: Callback when new data arrives (for mode='direct')
 *  - updateInterval: ms between polling/updates (default: 500)
 *  - colorScheme: 'default' | 'monochrome' | 'thermal' (default: 'default')
 *  - dbfsMin/dbfsMax: dBFS range (default: -100 to -20)
 *  - layout: 'side-by-side' | 'stacked' (default: 'stacked')
 *  - showStats: Show statistics panel (default: true)
 *  - title: Dashboard title
 * 
 * Example usage:
 *  <EmbeddableSDRDashboard 
 *    mode="polling"
 *    dataSource="http://your-server.com/api/spectrum"
 *    updateInterval={1000}
 *  />
 */

const SYS = `'Space Grotesk', 'Segoe UI', sans-serif`;
const MONO = `'JetBrains Mono', 'Courier New', monospace`;
const CYAN = '#00e5ff';

function StatCard({ label, value, unit, accent }) {
  return (
    <div style={{
      flex: 1,
      padding: 12,
      background: 'rgba(0,229,255,0.05)',
      border: `1px solid ${accent || 'rgba(0,229,255,0.2)'}`,
      borderRadius: 8,
      textAlign: 'center',
    }}>
      <div style={{
        fontFamily: MONO,
        fontSize: 20,
        fontWeight: 600,
        color: accent || CYAN,
        marginBottom: 4,
      }}>
        {value}
      </div>
      <div style={{
        fontFamily: SYS,
        fontSize: 11,
        color: 'rgba(255,255,255,0.4)',
        letterSpacing: '0.05em',
        textTransform: 'uppercase',
      }}>
        {label} {unit}
      </div>
    </div>
  );
}

export default function EmbeddableSDRDashboard({
  mode = 'websocket',
  dataSource,
  onData,
  updateInterval = 500,
  colorScheme = 'default',
  dbfsMin = -100,
  dbfsMax = -20,
  layout = 'stacked',
  showStats = true,
  title = 'SDR Spectrum Dashboard',
}) {
  const [spectrumData, setSpectrumData] = useState([]);
  const [fftData, setFftData] = useState([]);
  const [stats, setStats] = useState({ avg: 0, max: 0, min: 0, peak: null });
  const [status, setStatus] = useState('connecting');
  const [error, setError] = useState(null);

  const wsRef = useRef(null);
  const pollIntervalRef = useRef(null);
  const lastUpdateRef = useRef(Date.now());

  // Parse spectrum data from various sources
  const parseSpectrumData = useCallback((rawData) => {
    try {
      // If it's a single FFT array, use it
      if (Array.isArray(rawData) && !Array.isArray(rawData[0])) {
        return rawData.map((val, idx) => ({
          freq: idx,
          dbfs: val,
          label: `${idx}`,
        }));
      }

      // If it's already formatted with freq/dbfs objects
      if (Array.isArray(rawData) && rawData[0]?.dbfs !== undefined) {
        return rawData;
      }

      // If it's an object with multiple properties, extract relevant data
      if (rawData.fft) {
        setFftData(rawData.fft);
        return rawData.fft.map((val, idx) => ({
          freq: idx,
          dbfs: val,
          label: `${idx}`,
        }));
      }

      // If it's an object with freq/dbfs keys
      if (rawData.peaks && Array.isArray(rawData.peaks)) {
        return rawData.peaks.map(p => ({
          freq: p.freq,
          dbfs: p.dbfs,
          label: p.label || `${p.freq}`,
        }));
      }

      // Default: treat as FFT array
      return Array.isArray(rawData) ? rawData : [];
    } catch (e) {
      console.error('Error parsing spectrum data:', e);
      return [];
    }
  }, []);

  // Calculate statistics
  const calculateStats = useCallback((data) => {
    if (!data || data.length === 0) {
      return { avg: 0, max: 0, min: 0, peak: null };
    }

    const values = data.map(d => (typeof d === 'number' ? d : d.dbfs));
    const avg = (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1);
    const max = Math.max(...values).toFixed(1);
    const min = Math.min(...values).toFixed(1);
    const peak = data[values.indexOf(Math.max(...values))]?.label || 'N/A';

    setStats({ avg, max, min, peak });
  }, []);

  // WebSocket mode
  useEffect(() => {
    if (mode !== 'websocket' || !dataSource) return;

    const connectWS = () => {
      try {
        wsRef.current = new WebSocket(dataSource);
        wsRef.current.onopen = () => {
          setStatus('connected');
          setError(null);
        };
        wsRef.current.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            const parsed = parseSpectrumData(data);
            setSpectrumData(parsed);
            calculateStats(parsed);
            if (onData) onData(data);
          } catch (e) {
            console.error('WebSocket message parse error:', e);
          }
        };
        wsRef.current.onerror = () => {
          setStatus('error');
          setError('WebSocket connection failed');
        };
        wsRef.current.onclose = () => {
          setStatus('disconnected');
          // Attempt reconnect
          setTimeout(connectWS, 3000);
        };
      } catch (e) {
        setStatus('error');
        setError(e.message);
      }
    };

    connectWS();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [mode, dataSource, parseSpectrumData, calculateStats, onData]);

  // Polling mode
  useEffect(() => {
    if (mode !== 'polling' || !dataSource || typeof dataSource !== 'string') return;

    const poll = async () => {
      try {
        const response = await fetch(dataSource);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const parsed = parseSpectrumData(data);
        setSpectrumData(parsed);
        calculateStats(parsed);
        setStatus('connected');
        setError(null);
        if (onData) onData(data);
      } catch (e) {
        setStatus('error');
        setError(e.message);
      }
    };

    // Initial poll
    poll();

    // Set up polling interval
    pollIntervalRef.current = setInterval(poll, updateInterval);

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [mode, dataSource, updateInterval, parseSpectrumData, calculateStats, onData]);

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 20,
      width: '100%',
      maxWidth: '1400px',
      margin: '0 auto',
      padding: 20,
      background: 'linear-gradient(135deg, rgba(10,14,23,0.5) 0%, rgba(20,30,50,0.5) 100%)',
      borderRadius: 16,
      fontFamily: SYS,
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        paddingBottom: 12,
        borderBottom: '1px solid rgba(0,229,255,0.1)',
      }}>
        <div>
          <h1 style={{
            fontFamily: SYS,
            fontSize: 24,
            fontWeight: 700,
            color: CYAN,
            margin: 0,
            letterSpacing: '-0.02em',
          }}>
            {title}
          </h1>
          <p style={{
            fontFamily: MONO,
            fontSize: 11,
            color: 'rgba(255,255,255,0.3)',
            margin: '4px 0 0 0',
            letterSpacing: '0.05em',
            textTransform: 'uppercase',
          }}>
            Real-time spectrum analyzer
          </p>
        </div>

        {/* Status indicator */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 14px',
          background: status === 'connected' ? 'rgba(0,229,255,0.1)' : 'rgba(255,87,34,0.1)',
          border: `1px solid ${status === 'connected' ? 'rgba(0,229,255,0.3)' : 'rgba(255,87,34,0.3)'}`,
          borderRadius: 8,
        }}>
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: status === 'connected' ? '#00e5ff' : '#ff5722',
              boxShadow: status === 'connected' ? '0 0 8px #00e5ff' : '0 0 8px #ff5722',
            }}
          />
          <span style={{
            fontFamily: MONO,
            fontSize: 10,
            color: status === 'connected' ? '#00e5ff' : '#ff5722',
            fontWeight: 600,
            textTransform: 'uppercase',
          }}>
            {status}
          </span>
        </div>
      </div>

      {/* Error message */}
      {error && (
        <div style={{
          padding: 12,
          background: 'rgba(255,87,34,0.1)',
          border: '1px solid rgba(255,87,34,0.3)',
          borderRadius: 8,
          color: '#ff5722',
          fontFamily: MONO,
          fontSize: 12,
        }}>
          ⚠️ {error}
        </div>
      )}

      {/* Stats */}
      {showStats && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
          gap: 12,
        }}>
          <StatCard label="Average" value={stats.avg} unit="dBFS" />
          <StatCard label="Peak" value={stats.max} unit="dBFS" accent="#ff5722" />
          <StatCard label="Floor" value={stats.min} unit="dBFS" />
          <StatCard label="Peak Freq" value={stats.peak} />
        </div>
      )}

      {/* Visualizations */}
      <div
        style={{
          display: layout === 'side-by-side' ? 'grid' : 'flex',
          gridTemplateColumns: layout === 'side-by-side' ? '1fr 1fr' : undefined,
          flexDirection: layout !== 'side-by-side' ? 'column' : undefined,
          gap: 20,
        }}
      >
        <SpectrumVisualization
          data={spectrumData}
          height={300}
          colorScheme={colorScheme}
          dbfsMin={dbfsMin}
          dbfsMax={dbfsMax}
          showLabels={false}
          title="Spectrum"
        />

        <WaterfallVisualization
          fftData={fftData.length > 0 ? fftData : spectrumData.map(d => d.dbfs || d)}
          historyLength={100}
          updateInterval={updateInterval}
          colorScheme={colorScheme === 'default' ? 'thermal' : colorScheme}
          dbfsMin={dbfsMin}
          dbfsMax={dbfsMax}
          title="Waterfall"
          height={300}
        />
      </div>

      {/* Footer info */}
      <div style={{
        paddingTop: 12,
        borderTop: '1px solid rgba(0,229,255,0.1)',
        fontSize: 11,
        color: 'rgba(255,255,255,0.2)',
        fontFamily: MONO,
        textAlign: 'center',
      }}>
        {mode === 'websocket' && `WebSocket: ${dataSource}`}
        {mode === 'polling' && `Polling (${updateInterval}ms): ${dataSource}`}
        {mode === 'direct' && `Direct mode - use onData prop`}
      </div>
    </div>
  );
}
