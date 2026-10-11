import React, { useState, useEffect } from 'react';
import { startRegistration, startAuthentication } from '@simplewebauthn/browser';
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

/**
 * Opened on mobile when the QR code is scanned.
 * URL format: /verify-qr/:sessionId
 *
 * Flow:
 *  1. Fetch session info to get the username
 *  2. Generate WebAuthn assertion options
 *  3. startAuthentication (triggers Face ID / fingerprint)
 *  4. POST /api/qr/mobile-verify — backend marks desktop session as verified
 */
export default function MobileVerifyPage({ sessionId }) {
  const { t } = useTranslation();
  const [phase,          setPhase]          = useState('loading');
  const [message,        setMessage]        = useState('');
  const [username,       setUsername]       = useState('');
  const [isRegistration, setIsRegistration] = useState(false);
  const [nextStepToken,  setNextStepToken]  = useState('');
  const [pin,            setPin]            = useState('');
  const [pinError,       setPinError]       = useState('');  // only shown after server rejects

  useEffect(() => {
    init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Don't auto-trigger biometric — wait for PIN entry first

  const init = async () => {
    setPhase('loading');
    try {
      const res = await fetch(`${API}/api/qr/status/${sessionId}`);
      if (!res.ok) { setPhase('error'); setMessage(t('verify.invalidSession')); return; }
      const data = await res.json();
      if (!data.alive) { setPhase('expired'); return; }
      if (data.is_registration !== undefined) setIsRegistration(data.is_registration);
      if (data.next_step_token) setNextStepToken(data.next_step_token);
      if (data.username) {
        setUsername(data.username);
        setPhase('pin');  // require PIN before biometric
        return;
      }
      setPhase('pin');
    } catch {
      setPhase('error');
      setMessage(t('verify.networkError'));
    }
  };

  const submitPin = () => {
    if (pin.trim().length !== 6) { setPinError(t('verify.invalidPin')); return; }
    setPinError('');
    runWebAuthn(username);
  };

  const runWebAuthn = async (overrideUsername) => {
    setPhase('prompting');
    try {
      const uname = (overrideUsername || username).trim();
      if (!uname) { setPhase('need_username'); return; }

      let credential;
      if (isRegistration) {
        // First-time: register passkey on this phone
        const optRes = await fetch(`${API}/api/webauthn/register/generate`, {
          credentials: 'include',
          headers: { 'Content-Type': 'application/json', 'X-Next-Step-Token': nextStepToken },
        });
        if (!optRes.ok) { const e = await optRes.json(); throw new Error(e.detail || t('verify.optionsFailed')); }
        const options = await optRes.json();
        try {
          credential = await startRegistration({ optionsJSON: options });
        } catch (err) {
          if (err.name === 'NotAllowedError') throw new Error(t('verify.cancelled'));
          throw err;
        }
        // Verify registration
        const verRes = await fetch(`${API}/api/webauthn/register/verify`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ credential, deviceName: navigator.userAgent.includes('iPhone') ? 'iPhone' : 'Mobile Device' }),
        });
        if (!verRes.ok) { const e = await verRes.json(); throw new Error(e.detail || t('verify.registrationFailed')); }
        // Now authenticate to unlock the desktop QR session
        const authOptRes = await fetch(
          `${API}/api/webauthn/authenticate/generate?username=${encodeURIComponent(uname)}`,
          { credentials: 'include' }
        );
        if (!authOptRes.ok) { const e = await authOptRes.json(); throw new Error(e.detail || t('verify.optionsFailed')); }
        const authOptions = await authOptRes.json();
        try {
          credential = await startAuthentication({ optionsJSON: authOptions });
        } catch (err) {
          if (err.name === 'NotAllowedError') throw new Error(t('verify.cancelled'));
          throw err;
        }
      } else {
        // Normal: authenticate with existing passkey
        const optRes = await fetch(
          `${API}/api/webauthn/authenticate/generate?username=${encodeURIComponent(uname)}`,
          { credentials: 'include' }
        );
        if (!optRes.ok) { const e = await optRes.json(); throw new Error(e.detail || t('verify.optionsFailed')); }
        const options = await optRes.json();
        try {
          credential = await startAuthentication({ optionsJSON: options });
        } catch (err) {
          if (err.name === 'NotAllowedError') throw new Error(t('verify.cancelled'));
          throw err;
        }
      }

      const verRes = await fetch(`${API}/api/qr/mobile-verify`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId, credential: { ...credential, pin: pin.trim() } }),
      });
      if (!verRes.ok) {
        const e = await verRes.json();
        if (e.detail === 'Invalid PIN') {
          setPhase('pin'); setPinError(t('verify.wrongPin'));
          return;
        }
        throw new Error(e.detail || t('verify.verificationFailed'));
      }

      setPhase('success');
      setMessage(t('verify.unlocked'));
    } catch (err) {
      setPhase('error');
      setMessage(err.message || t('verify.unexpected'));
    }
  };

  const renderContent = () => {
    if (phase === 'loading' || phase === 'prompting') {
      return (
        <>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 24 }}>
            <svg width="40" height="40" viewBox="0 0 40 40" fill="none" style={{ animation: 'spin 2s linear infinite' }}>
              <circle cx="20" cy="20" r="16" stroke="rgba(0,229,255,0.2)" strokeWidth="3"/>
              <path d="M20 4 A16 16 0 0 1 36 20" stroke={CYAN} strokeWidth="3" strokeLinecap="round"/>
            </svg>
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#fff', marginBottom: 8 }}>
            {phase === 'loading' ? t('common.loading') : t('verify.awaitingBiometric')}
          </div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.35)', fontFamily: MONO }}>
            {t('verify.completePrompt')}
          </div>
        </>
      );
    }

    if (phase === 'pin' || phase === 'need_username') {
      return (
        <>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#fff', marginBottom: 6 }}>{t('verify.enterDesktopPin')}</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.35)', fontFamily: MONO, marginBottom: 24 }}>
            {t('verify.pinDescription')}
          </div>

          {username && (
            <div style={{ fontSize: 12, color: 'rgba(0,229,255,0.6)', fontFamily: MONO, marginBottom: 16 }}>{t('verify.account')}: {username}</div>
          )}

          <input
            value={pin}
            onChange={e => setPin(e.target.value.replace(/\D/g, '').slice(0, 6))}
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={6}
            placeholder="000000"
            autoFocus
            style={{
              width: '100%', padding: '14px 0', textAlign: 'center',
              background: 'rgba(0,229,255,0.04)',
              border: `1px solid ${pinError ? 'rgba(255,87,34,0.5)' : 'rgba(0,229,255,0.2)'}`,
              borderRadius: 8, color: '#fff', fontSize: 28,
              fontFamily: MONO, letterSpacing: '0.3em',
              outline: 'none', boxSizing: 'border-box',
            }}
            onKeyDown={e => e.key === 'Enter' && submitPin()}
          />
          {pinError && (
            <div style={{ fontSize: 12, color: '#ff7043', fontFamily: MONO, marginTop: 8 }}>{pinError}</div>
          )}

          <button onClick={submitPin} style={{
            marginTop: 20, width: '100%', padding: '13px 0',
            background: 'linear-gradient(135deg, rgba(0,229,255,0.18) 0%, rgba(0,229,255,0.08) 100%)',
            border: '1px solid rgba(0,229,255,0.35)',
            borderRadius: 10, color: '#fff', fontSize: 14, fontWeight: 700,
            letterSpacing: '0.1em', fontFamily: SYS, cursor: 'pointer',
          }}>
            {t('verify.continue')}
          </button>
        </>
      );
    }

    if (phase === 'success') {
      return (
        <>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 24 }}>
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="20" stroke={CYAN} strokeWidth="2" fill="rgba(0,229,255,0.08)"/>
              <path d="M15 24L21 30L33 18" stroke={CYAN} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: CYAN, marginBottom: 10 }}>{t('verify.verified')}</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.4)', fontFamily: MONO }}>{message}</div>
        </>
      );
    }

    if (phase === 'expired') {
      return (
        <>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#ffaf00', marginBottom: 10 }}>{t('verify.qrExpired')}</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.4)', fontFamily: MONO, marginBottom: 24 }}>
            {t('verify.qrExpiredDesc')}
          </div>
        </>
      );
    }

    if (phase === 'error') {
      return (
        <>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 24 }}>
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="20" stroke="#ff5722" strokeWidth="2" fill="rgba(255,87,34,0.08)"/>
              <path d="M18 18L30 30M30 18L18 30" stroke="#ff5722" strokeWidth="2.5" strokeLinecap="round"/>
            </svg>
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#ff7043', marginBottom: 10 }}>{t('verify.error')}</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.4)', fontFamily: MONO, marginBottom: 24 }}>{message}</div>
          <button onClick={() => { setPhase('pin'); setMessage(''); }} style={{
            width: '100%', padding: '12px 0',
            background: 'rgba(0,229,255,0.08)', border: '1px solid rgba(0,229,255,0.25)',
            borderRadius: 10, color: '#fff', fontSize: 14, fontWeight: 700,
            fontFamily: SYS, cursor: 'pointer',
          }}>
            {t('verify.tryAgain')}
          </button>
        </>
      );
    }

    return null;
  };

  return (
    <div style={{
      minHeight: '100vh', background: VOID,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: SYS, padding: 20,
    }}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>

      <div style={{ ...glassStyle, width: '100%', maxWidth: 360, padding: '44px 32px', textAlign: 'center' }}>
        <div style={{ fontSize: 11, letterSpacing: '0.2em', color: 'rgba(0,229,255,0.5)', marginBottom: 20, textTransform: 'uppercase' }}>
          {t('common.appName')}
        </div>
        {renderContent()}
      </div>
    </div>
  );
}
