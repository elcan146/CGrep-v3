import React, { useState } from 'react';
import { useAuth, API } from './AuthContext';
import { useTranslation } from '../i18n/LanguageContext';
import QRScreen from './QRScreen';
import MobilePasskeyPrompt from './MobilePasskeyPrompt';
import GlobeBackground from './GlobeBackground';
import { startAuthentication } from '@simplewebauthn/browser';

const VOID = '#05070b';
const CYAN = '#00e5ff';
const SYS  = `'Space Grotesk', sans-serif`;
const MONO = `'JetBrains Mono', monospace`;

const glassStyle = {
  background: 'rgba(10,14,23,0.92)',
  border: '1px solid rgba(0,229,255,0.18)',
  borderRadius: 20,
  boxShadow: '0 8px 48px 0 rgba(0,0,0,0.7), 0 0 0 1px rgba(0,229,255,0.06)',
  backdropFilter: 'blur(24px)',
};

function InputField({ label, type, value, onChange, disabled }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <label style={{
        display: 'block', fontSize: 11, fontWeight: 600,
        color: 'rgba(0,229,255,0.6)', letterSpacing: '0.1em',
        textTransform: 'uppercase', marginBottom: 8, fontFamily: SYS,
      }}>{label}</label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        autoComplete={type === 'password' ? 'current-password' : 'username'}
        style={{
          width: '100%', padding: '12px 16px',
          background: 'rgba(0,229,255,0.04)',
          border: '1px solid rgba(0,229,255,0.2)',
          borderRadius: 10, color: '#fff', fontSize: 15,
          fontFamily: MONO, outline: 'none',
          transition: 'border-color 0.2s',
          boxSizing: 'border-box',
        }}
        onFocus={e => e.target.style.borderColor = CYAN}
        onBlur={e  => e.target.style.borderColor = 'rgba(0,229,255,0.2)'}
      />
    </div>
  );
}

function SubmitBtn({ label, loading, disabled }) {
  return (
    <button
      type="submit"
      disabled={loading || disabled}
      style={{
        width: '100%', padding: '13px 0',
        background: loading || disabled
          ? 'rgba(0,229,255,0.08)'
          : 'linear-gradient(135deg, rgba(0,229,255,0.18) 0%, rgba(0,229,255,0.08) 100%)',
        border: `1px solid ${loading || disabled ? 'rgba(0,229,255,0.1)' : 'rgba(0,229,255,0.35)'}`,
        borderRadius: 10, color: loading || disabled ? 'rgba(255,255,255,0.3)' : '#fff',
        fontSize: 14, fontWeight: 700, letterSpacing: '0.1em',
        fontFamily: SYS, cursor: loading || disabled ? 'not-allowed' : 'pointer',
        transition: 'all 0.2s',
      }}
    >
      {label}
    </button>
  );
}

export default function LoginPage() {
  const { setUser, checkSession } = useAuth();
  const { t, lang, toggleLanguage } = useTranslation();

  const [username,  setUsername]  = useState('');
  const [password,  setPassword]  = useState('');
  const [error,     setError]     = useState('');
  const [busy,      setBusy]      = useState(false);

  // Phase state: 'login' | 'qr' | 'mobile_passkey'
  const [phase,          setPhase]          = useState('login');
  const [qrData,         setQrData]         = useState(null);
  const [nextToken,      setNextToken]      = useState('');
  const [isRegistration, setIsRegistration] = useState(false);

  const _isMobileDevice = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
    || (navigator.maxTouchPoints > 1 && /Mac/.test(navigator.userAgent)); // iPad

  const handlePassword = async (e) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const clientType = _isMobileDevice ? 'mobile' : 'desktop';
      const res = await fetch(`${API}/api/auth/verify-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password, clientType }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || t('login.error')); return; }

      setNextToken(data.nextStepToken);

      // Desktop always uses QR — phone does the biometric (register or authenticate)
      // Mobile goes straight to passkey prompt on the device
      const isDesktop = data.clientType === 'desktop';

      if (data.status === 'needs_registration' || data.status === 'needs_passkey') {
        if (isDesktop) {
          const qrRes = await fetch(`${API}/api/qr/generate`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'X-Next-Step-Token': data.nextStepToken },
          });
          const qrJson = await qrRes.json();
          if (!qrRes.ok) { setError(qrJson.detail || t('login.error')); return; }
          setQrData({ ...qrJson, isRegistration: data.status === 'needs_registration' });
          setPhase('qr');
        } else {
          setIsRegistration(data.status === 'needs_registration');
          setPhase('mobile_passkey');
        }
      }
    } catch {
      setError(t('login.networkError'));
    } finally {
      setBusy(false);
    }
  };

  if (phase === 'qr' && qrData) {
    return (
      <QRScreen
        qrData={qrData}
        username={username}
        onSuccess={() => checkSession()}
        onCancel={() => { setPhase('login'); setQrData(null); }}
      />
    );
  }

  if (phase === 'mobile_passkey') {
    return (
      <MobilePasskeyPrompt
        username={username}
        nextToken={nextToken}
        isRegistration={isRegistration}
        onSuccess={() => checkSession()}
        onCancel={() => setPhase('login')}
      />
    );
  }

  return (
    <div style={{
      minHeight: '100vh', background: VOID,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: SYS, padding: 20,
      position: 'relative',
    }}>
      <GlobeBackground />
      
      {/* Ambient glow */}
      <div style={{
        position: 'fixed', top: '30%', left: '50%', transform: 'translate(-50%,-50%)',
        width: 600, height: 400, borderRadius: '50%',
        background: 'radial-gradient(ellipse at center, rgba(0,229,255,0.04) 0%, transparent 70%)',
        pointerEvents: 'none',
        zIndex: 1,
      }} />

      <div style={{ ...glassStyle, width: '100%', maxWidth: 420, padding: '44px 40px', zIndex: 2, position: 'relative' }}>
        {/* Logo / title */}
        <div style={{ textAlign: 'center', marginBottom: 36 }}>
          <div style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 52, height: 52, borderRadius: 14,
            background: 'rgba(0,229,255,0.08)',
            border: '1px solid rgba(0,229,255,0.2)',
            marginBottom: 16,
          }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path d="M12 2L2 7V17L12 22L22 17V7L12 2Z" stroke={CYAN} strokeWidth="1.5" strokeLinejoin="round"/>
              <circle cx="12" cy="12" r="3" fill={CYAN} opacity="0.7"/>
              <path d="M12 9V7M12 17V15M9 12H7M17 12H15" stroke={CYAN} strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </div>
          <div style={{ fontSize: 11, letterSpacing: '0.2em', color: 'rgba(0,229,255,0.5)', marginBottom: 6, textTransform: 'uppercase' }}>
            cgrep
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#fff' }}>
            {t('login.title')}
          </div>
        </div>

        <form onSubmit={handlePassword}>
          <InputField label={t('login.username')} type="text"     value={username} onChange={setUsername} disabled={busy} />
          <InputField label={t('login.password')} type="password" value={password} onChange={setPassword} disabled={busy} />

          {error && (
            <div style={{
              padding: '10px 14px', marginBottom: 16, borderRadius: 8,
              background: 'rgba(255,87,34,0.1)', border: '1px solid rgba(255,87,34,0.3)',
              color: '#ff7043', fontSize: 13, fontFamily: MONO,
            }}>
              {error}
            </div>
          )}

          <SubmitBtn label={busy ? t('login.verifying') : t('login.verify')} loading={busy} />
        </form>

        <div style={{
          marginTop: 24, textAlign: 'center', fontSize: 11,
          color: 'rgba(255,255,255,0.18)', letterSpacing: '0.06em',
        }}>
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
        </div>
      </div>
    </div>
  );
}
