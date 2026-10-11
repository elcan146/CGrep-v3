import React, { useRef, useEffect } from 'react';

const AsciiWave = ({
  className = '',
  color = '#FF4500',
  speed = 1,
}) => {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Low-res offscreen buffer: draw 1/2-scale, then nearest-neighbor blit.
    const offCanvas = document.createElement('canvas');
    const offCtx = offCanvas.getContext('2d', { willReadFrequently: false });
    if (!offCtx) return;

    let animationId;
    let time = 0;
    let lastTs = 0;
    const FRAME_MS = 1000 / 15;
    const SCALE = 0.5;
    const chars = ' .:+x*#'.split('');
    const fontSize = 16;
    const columnWidth = 16;

    const resize = () => {
      if (!container || !canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const cssW = container.clientWidth;
      const cssH = container.clientHeight;
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      offCanvas.width = Math.max(1, Math.floor(cssW * SCALE));
      offCanvas.height = Math.max(1, Math.floor(cssH * SCALE));
    };

    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();

    const draw = (ts) => {
      animationId = requestAnimationFrame(draw);
      if (document.hidden) return;
      if (ts - lastTs < FRAME_MS) return;
      lastTs = ts;

      const width = container.clientWidth;
      const height = container.clientHeight;
      const offW = offCanvas.width;
      const offH = offCanvas.height;

      offCtx.clearRect(0, 0, offW, offH);
      offCtx.font = `${fontSize}px monospace`;
      offCtx.fillStyle = color;
      offCtx.textBaseline = 'alphabetic';

      const columns = Math.ceil(offW / columnWidth);
      const rows = Math.ceil(offH / fontSize);

      for (let x = 0; x < columns; x++) {
        const shapeBase = Math.sin(x * 0.1) * 0.6 + Math.cos(x * 0.25) * 0.4;
        const breath = Math.sin(time * 0.002 * speed) * 0.1;
        const flicker = Math.sin(time * 0.008 * speed + x * 100) * 0.05;
        const noise = shapeBase + breath + flicker;
        const columnHeightNormal = Math.max(0.15, (noise + 1) / 2 * 0.6 + 0.15);
        const activeRows = Math.floor(columnHeightNormal * rows);

        for (let y = rows - 1; y > rows - activeRows; y--) {
          const flowShift = time * 0.005 * speed;
          const charNoise = Math.sin((y * 0.2) - flowShift + x * 10);
          const distFromTop = (y - (rows - activeRows));
          const fade = Math.min(1, distFromTop / 6);
          const normalizedNoise = (charNoise + 1) / 2;
          const charIndex = Math.floor(normalizedNoise * chars.length);
          const char = chars[Math.min(charIndex, chars.length - 1)];
          const posX = x * columnWidth;
          const posY = y * fontSize;

          if (Math.random() > 0.95) continue;

          offCtx.globalAlpha = fade;
          offCtx.fillText(char, posX, posY);
        }
      }

      offCtx.globalAlpha = 1.0;

      ctx.clearRect(0, 0, width, height);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(offCanvas, 0, 0, width, height);
      time += 16;
    };

    animationId = requestAnimationFrame(draw);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(animationId);
      offCanvas.width = 0;
      offCanvas.height = 0;
    };
  }, [color, speed]);

  return (
    <div ref={containerRef} className={`w-full h-full overflow-hidden ${className}`}>
      <canvas ref={canvasRef} className="block w-full h-full" />
    </div>
  );
};

export default AsciiWave;
