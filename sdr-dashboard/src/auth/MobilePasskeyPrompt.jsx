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
 * Used in two modes:
 *  1. Registration (isRegistration=true) — first-time passkey setup
 *  2. Authentication (isRegistration=false) — native mobile login
 *
 * The component auto-triggers the WebAuthn prompt on mount.
 */
export default function MobilePasskeyPrompt({ username, nextToken, onSuccess, onCancel, isRegistration = false }) {
  const { t } = useTranslation();
  const [status,  setStatus]  = useState('idle');  // idle | prompting | success | error
  const [message, setMessage] = useState('');

  useEffect(() => {
    trigger();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const trigger = async () => {
    setStatus('prompting');
    setMessage('');
    try {
      if (isRegistration) {
        await doRegistration();
      } else {
        await doAuthentication();
      }
    } catch (err) {
      setStatus('error');
      setMessage(err.message || t('passkey.failed'));
    }
  };

  const doRegistration = async () => {
    const optRes = await fetch(`${API}/api/webauthn/register/generate`, {
      credentials: 'include',
      headers: nextToken ? { 'X-Next-Step-Token': nextToken } : {},
    });
    if (!optRes.ok) {
      const e = await optRes.json();
      throw new Error(e.detail || t('passkey.registrationOptionsFailed'));
    }
    const options = await optRes.json();

    let attResp;
    try {
      attResp = await startRegistration({ optionsJSON: options });
    } catch (err) {
      if (err.name === 'NotAllowedError') throw new Error(t('passkey.cancelled'));
      throw err;
    }

    const verRes = await fetch(`${API}/api/webauthn/register/verify`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(nextToken ? { 'X-Next-Step-Token': nextToken } : {}),
      },
      body: JSON.stringify({ credential: attResp, deviceName: navigator.platform || 'Mobile Device' }),
    });
    if (!verRes.ok) {
      const e = await verRes.json();
      throw new Error(e.detail || t('passkey.registrationVerifyFailed'));
    }
    setStatus('success');
    setMessage(t('passkey.registered'));
    setTimeout(onSuccess, 1000);
  };

  const doAuthentication = async () => {
    const optRes = await fetch(`${API}/api/webauthn/authenticate/generate?username=${encodeURIComponent(username)}`, {
      credentials: 'include',
    });
    if (!optRes.ok) {
      const e = await optRes.json();
      throw new Error(e.detail || t('passkey.authOptionsFailed'));
    }
    const options = await optRes.json();

    let assertionResp;
    try {
      assertionResp = await startAuthentication({ optionsJSON: options });
    } catch (err) {
      if (err.name === 'NotAllowedError') throw new Error(t('passkey.cancelled'));
      throw err;
    }

    const verRes = await fetch(`${API}/api/webauthn/authenticate/verify`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ credential: assertionResp }),
    });
    if (!verRes.ok) {
      const e = await verRes.json();
      throw new Error(e.detail || t('passkey.authVerifyFailed'));
    }
    setStatus('success');
    setMessage(t('passkey.authenticated'));
    setTimeout(onSuccess, 800);
  };

  const icons = {
    prompting: (
      <svg width="40" height="40" viewBox="0 0 40 40" fill="none" style={{ animation: 'spin 2s linear infinite' }}>
        <circle cx="20" cy="20" r="16" stroke="rgba(0,229,255,0.2)" strokeWidth="3"/>
        <path d="M20 4 A16 16 0 0 1 36 20" stroke={CYAN} strokeWidth="3" strokeLinecap="round"/>
      </svg>
    ),
    success: (
      <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
        <circle cx="20" cy="20" r="16" stroke={CYAN} strokeWidth="2" fill="rgba(0,229,255,0.08)"/>
        <path d="M13 20L18 25L27 15" stroke={CYAN} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    ),
    error: (
      <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
        <circle cx="20" cy="20" r="16" stroke="#ff5722" strokeWidth="2" fill="rgba(255,87,34,0.08)"/>
        <path d="M15 15L25 25M25 15L15 25" stroke="#ff5722" strokeWidth="2.5" strokeLinecap="round"/>
      </svg>
    ),
    idle: null,
  };

  const getTitle = (s) => {
    if (s === 'prompting') return isRegistration ? t('passkey.registerTitle') : t('passkey.biometricTitle');
    if (s === 'success') return t('passkey.verified');
    if (s === 'error') return t('passkey.verificationFailed');
    return isRegistration ? t('passkey.registerPasskey') : t('passkey.biometricLogin');
  };

  const getSubtitle = (s) => {
    if (s === 'prompting') return t('passkey.promptMessage');
    if (s === 'success' || s === 'error') return message;
    return '';
  };

  return (
    <div style={{
      minHeight: '100vh', background: VOID,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: SYS, padding: 20,
    }}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>

      <div style={{ ...glassStyle, width: '100%', maxWidth: 380, padding: '48px 40px', textAlign: 'center' }}>
        <div style={{ fontSize: 11, letterSpacing: '0.2em', color: 'rgba(0,229,255,0.5)', marginBottom: 20, textTransform: 'uppercase' }}>
          {isRegistration ? t('passkey.firstTimeSetup') : t('passkey.step2')}
        </div>

        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 24 }}>
          {icons[status]}
        </div>

        <div style={{ fontSize: 22, fontWeight: 700, color: status === 'error' ? '#ff7043' : '#fff', marginBottom: 10 }}>
          {getTitle(status)}
        </div>

        <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.4)', fontFamily: MONO, marginBottom: 32, minHeight: 36 }}>
          {getSubtitle(status)}
        </div>

        {/* Biometric icon visual */}
        {status === 'prompting' && (
          <div style={{
            display: 'flex', justifyContent: 'center', marginBottom: 28,
          }}>
            <svg width="64" height="64" viewBox="0 0 64 64" fill="none" opacity="0.6">
              <rect x="8" y="8" width="48" height="48" rx="14" stroke={CYAN} strokeWidth="1.5"/>
              <circle cx="32" cy="26" r="8" stroke={CYAN} strokeWidth="1.5"/>
              <path d="M18 44 C18 36 46 36 46 44" stroke={CYAN} strokeWidth="1.5" strokeLinecap="round"/>
              <path d="M24 26 C24 22 40 22 40 26" stroke={CYAN} strokeWidth="1.5" strokeLinecap="round" opacity="0.4"/>
            </svg>
          </div>
        )}

        {status === 'error' && (
          <button onClick={trigger} style={{
            width: '100%', padding: '12px 0', marginBottom: 12,
            background: 'rgba(0,229,255,0.08)',
            border: '1px solid rgba(0,229,255,0.25)',
            borderRadius: 10, color: '#fff', fontSize: 14,
            fontWeight: 700, letterSpacing: '0.1em',
            fontFamily: SYS, cursor: 'pointer',
          }}>
            {t('passkey.tryAgain')}
          </button>
        )}

        <button onClick={onCancel} style={{
          background: 'none', border: 'none',
          color: 'rgba(255,255,255,0.25)', fontSize: 12,
          cursor: 'pointer', fontFamily: SYS,
          textDecoration: 'underline', padding: '4px 0',
        }}>
          {t('passkey.backToLogin')}
        </button>
      </div>
    </div>
  );
}
