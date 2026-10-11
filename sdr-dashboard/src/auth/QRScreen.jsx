import React, { useEffect, useRef, useState } from 'react';
import { API } from './AuthContext';
import { useTranslation } from '../i18n/LanguageContext';

const VOID = '#05070b';
const CYAN = '#00e5ff';
const SYS  = `'Space Grotesk', sans-serif`;
const MONO = `'JetBrains Mono', monospace`;

const glassStyle = {
  background: 'rgba(10,14,23,0.92)',
  border: '1px solid rgba(0,229,255,0.18)',
  borderRadius: 20,
  boxShadow: '0 8px 48px 0 rgba(0,0,0,0.7)',
  backdropFilter: 'blur(24px)',
};

/* Minimal QR encoder — uses the browser's Canvas API + a tiny data-URL QR lib.
   We load qrcode via a dynamic import from the npm package. */
function QRCode({ url, size = 220 }) {
  const canvasRef = useRef();

  useEffect(() => {
    if (!url || !canvasRef.current) return;
    import('qrcode').then(QR => {
      QR.toCanvas(canvasRef.current, url, {
        width: size,
        margin: 2,
        color: { dark: '#00e5ff', light: '#05070b' },
      });
    }).catch(() => {
      // Fallback: draw placeholder
      const ctx = canvasRef.current.getContext('2d');
      ctx.fillStyle = '#05070b';
      ctx.fillRect(0, 0, size, size);
      ctx.fillStyle = '#00e5ff';
      ctx.font = '12px monospace';
      ctx.fillText(t('qr.qrUnavailable'), 10, size / 2);
    });
  }, [url, size]);

  return (
    <div style={{
      padding: 12,
      background: '#05070b',
      border: '1px solid rgba(0,229,255,0.25)',
      borderRadius: 12,
      display: 'inline-block',
    }}>
      <canvas ref={canvasRef} width={size} height={size} />
    </div>
  );
}

function CountdownBar({ expiresIn }) {
  const { t } = useTranslation();
  const [remaining, setRemaining] = useState(expiresIn);

  useEffect(() => {
    const id = setInterval(() => setRemaining(r => Math.max(0, r - 1)), 1000);
    return () => clearInterval(id);
  }, []);

  const pct = (remaining / expiresIn) * 100;
  const color = pct > 40 ? CYAN : pct > 15 ? '#ffaf00' : '#ff5722';

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', fontFamily: MONO }}>{t('qr.expiresIn')}</span>
        <span style={{ fontSize: 11, color, fontFamily: MONO, fontWeight: 700 }}>
          {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')}
        </span>
      </div>
      <div style={{ height: 3, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{
          height: '100%', borderRadius: 2,
          background: color,
          width: `${pct}%`,
          transition: 'width 1s linear, background 0.5s',
        }} />
      </div>
    </div>
  );
}

export default function QRScreen({ qrData, username, onSuccess, onCancel }) {
  const { t } = useTranslation();
  const [status,     setStatus]     = useState('pending'); // 'pending' | 'verified' | 'expired'
  const [verifyUrl,  setVerifyUrl]  = useState('');
  const pollRef = useRef(null);

  useEffect(() => {
    fetch(`${API}/api/config`, { credentials: 'include' })
      .then(r => r.json())
      .then(cfg => {
        const base = cfg.publicBaseUrl || window.location.origin;
        setVerifyUrl(`${base}/verify-qr/${qrData.sessionId}`);
      })
      .catch(() => {
        setVerifyUrl(`${window.location.origin}/verify-qr/${qrData.sessionId}`);
      });
  }, [qrData.sessionId]);

  useEffect(() => {
    if (!qrData?.sessionId) return;

    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/qr/poll/${qrData.sessionId}`, {
          credentials: 'include',
          headers: { 'X-Poll-Token': qrData.pollToken || '' },
        });
        if (!res.ok) return;
        const data = await res.json();
        setStatus(data.status);

        if (data.status === 'verified') {
          clearInterval(pollRef.current);
          setTimeout(onSuccess, 600);
        }
        if (data.status === 'expired') {
          clearInterval(pollRef.current);
        }
      } catch {
        // Network hiccup — keep polling
      }
    }, 2000);

    return () => clearInterval(pollRef.current);
  }, [qrData, onSuccess]);

  const statusMap = {
    pending:  { label: t('qr.waiting'), color: 'rgba(255,255,255,0.4)' },
    verified: { label: t('qr.verified'), color: '#00e5ff' },
    expired:  { label: t('qr.expired'),  color: '#ff5722' },
  };
  const { label, color } = statusMap[status] || statusMap.pending;

  return (
    <div style={{
      minHeight: '100vh', background: VOID,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: SYS, padding: 20,
    }}>
      <div style={{ ...glassStyle, width: '100%', maxWidth: 420, padding: '44px 40px', textAlign: 'center' }}>
        <div style={{ fontSize: 11, letterSpacing: '0.2em', color: 'rgba(0,229,255,0.5)', marginBottom: 8, textTransform: 'uppercase' }}>
          {t('qr.step2')}
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: '#fff', marginBottom: 6 }}>
          {t('qr.scanTitle')}
        </div>
        <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.35)', marginBottom: 28, fontFamily: MONO }}>
          {username}
        </div>

        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 16 }}>
          <QRCode url={verifyUrl} size={220} />
        </div>

        {/* PIN — must be entered on the phone to prove physical proximity */}
        {qrData.pin && (
          <div style={{
            marginBottom: 20,
            padding: '14px 20px',
            background: 'rgba(0,229,255,0.06)',
            border: '1px solid rgba(0,229,255,0.3)',
            borderRadius: 12,
          }}>
            <div style={{ fontSize: 10, letterSpacing: '0.18em', color: 'rgba(0,229,255,0.5)', textTransform: 'uppercase', marginBottom: 8 }}>{t('qr.enterPin')}</div>
            <div style={{
              fontSize: 38, fontWeight: 900, letterSpacing: '0.25em',
              fontFamily: MONO, color: CYAN,
              textShadow: '0 0 20px rgba(0,229,255,0.5)',
            }}>{qrData.pin}</div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', marginTop: 6, fontFamily: MONO }}>
              {t('qr.doNotShare')}
            </div>
          </div>
        )}

        {/* Status indicator */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginBottom: 16 }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%',
            background: color,
            boxShadow: status === 'verified' ? `0 0 10px ${color}` : 'none',
            display: 'inline-block',
            animation: status === 'pending' ? 'ap-pulse 2s ease-in-out infinite' : 'none',
          }} />
          <span style={{ fontSize: 13, color, fontFamily: MONO }}>{label}</span>
        </div>

        {qrData.expiresIn && <CountdownBar expiresIn={qrData.expiresIn} />}

        {/* Instructions */}
        <div style={{
          marginTop: 24, padding: '14px 16px',
          background: 'rgba(0,229,255,0.04)',
          border: '1px solid rgba(0,229,255,0.1)',
          borderRadius: 10, textAlign: 'left',
        }}>
          <div style={{ fontSize: 11, color: CYAN, fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}></div>
          {[].map((step, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, marginBottom: 6 }}>
              <span style={{ fontSize: 11, color: 'rgba(0,229,255,0.4)', fontFamily: MONO, minWidth: 16 }}>{i + 1}.</span>
              <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', fontFamily: MONO }}>{step}</span>
            </div>
          ))}
        </div>

        {status === 'expired' && (
          <button onClick={onCancel} style={{
            marginTop: 20, width: '100%', padding: '11px 0',
            background: 'transparent', border: '1px solid rgba(255,87,34,0.4)',
            borderRadius: 10, color: '#ff7043', fontSize: 13,
            fontFamily: SYS, fontWeight: 600, cursor: 'pointer',
          }}>
            {t('qr.tryAgain')}
          </button>
        )}

        {status === 'pending' && (
          <button onClick={onCancel} style={{
            marginTop: 16, background: 'none', border: 'none',
            color: 'rgba(255,255,255,0.25)', fontSize: 12, cursor: 'pointer',
            fontFamily: SYS, textDecoration: 'underline',
          }}>
            {t('qr.cancel')}
          </button>
        )}
      </div>
    </div>
  );
}
