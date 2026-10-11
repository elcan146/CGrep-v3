import React, { useState, useRef, useCallback } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useAuth } from './AuthContext';
import { useTranslation } from '../i18n/LanguageContext';

const CYAN  = '#00e5ff';
const SYS   = `'Space Grotesk', sans-serif`;
const MONO  = `'JetBrains Mono', monospace`;
const API_BASE = window.location.port === '5173' ? 'http://127.0.0.1:8000' : '';

const glass = {
  background: 'rgba(8,12,22,0.97)',
  border: '1px solid rgba(0,229,255,0.18)',
  borderRadius: 20,
  boxShadow: '0 0 0 1px rgba(0,229,255,0.06), 0 24px 64px rgba(0,0,0,0.8)',
  backdropFilter: 'blur(24px)',
};

function resizeAndCompressAvatar(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width  = 1024;
      canvas.height = 1024;
      const ctx = canvas.getContext('2d');

      const sw = img.width;
      const sh = img.height;
      const scale = Math.max(1024 / sw, 1024 / sh);
      const dw = sw * scale;
      const dh = sh * scale;
      const dx = (1024 - dw) / 2;
      const dy = (1024 - dh) / 2;

      ctx.fillStyle = '#000';
      ctx.fillRect(0, 0, 1024, 1024);
      ctx.drawImage(img, dx, dy, dw, dh);
      URL.revokeObjectURL(url);

      let quality = 0.85;
      let dataUrl = canvas.toDataURL('image/jpeg', quality);

      while (dataUrl.length > 600_000 && quality > 0.40) {
        quality -= 0.07;
        dataUrl = canvas.toDataURL('image/jpeg', quality);
      }
      // Return only the raw base64 (strip data-URI prefix to reduce payload)
      const b64 = dataUrl.split(',')[1];
      resolve(b64);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Image load failed')); };
    img.src = url;
  });
}

export default function ProfileModal({ onClose }) {
  const { t } = useTranslation();
  const { user, setUser } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name || '');
  const [avatarPreview, setAvatarPreview] = useState(user?.avatar_url || null);
  const [avatarB64, setAvatarB64]   = useState(null);
  const [saving, setSaving]         = useState(false);
  const [msg, setMsg]               = useState(null);
  const fileRef = useRef();

  const handleFileChange = useCallback(async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setMsg({ type: 'err', text: t('profile.imageRequired') });
      return;
    }
    if (file.type === 'image/svg+xml' || file.name.toLowerCase().endsWith('.svg')) {
      setMsg({ type: 'err', text: 'SVG files are not allowed for security reasons' });
      return;
    }
    try {
      const b64 = await resizeAndCompressAvatar(file);
      setAvatarPreview('data:image/jpeg;base64,' + b64);
      setAvatarB64(b64);
      setMsg(null);
    } catch {
      setMsg({ type: 'err', text: t('profile.processFailed') });
    }
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setMsg(null);
    const body = { display_name: displayName.trim() };
    if (avatarB64) body.avatar_b64 = avatarB64;
    try {
      const res = await fetch(`${API_BASE}/api/user/profile`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        let detail = t('profile.saveFailed');
        try { const j = await res.json(); detail = j.detail || detail; } catch { /* ignore */ }
        throw new Error(`${res.status}: ${detail}`);
      }
      const data = await res.json();
      setUser(prev => ({ ...prev, display_name: data.display_name, avatar_url: data.avatar_url }));
      setMsg({ type: 'ok', text: t('profile.updated') });
      setTimeout(onClose, 900);
    } catch (e) {
      setMsg({ type: 'err', text: e.message });
    } finally {
      setSaving(false);
    }
  }, [displayName, avatarB64, setUser, onClose]);

  const avatarSrc = avatarPreview
    ? (avatarPreview.startsWith('data:') ? avatarPreview : avatarPreview)
    : null;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(10px)',
        fontFamily: SYS,
      }}
      onClick={onClose}
    >
      <motion.div
        initial={{ opacity: 0, y: 28, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 16, scale: 0.97 }}
        transition={{ duration: 0.22, ease: 'easeOut' }}
        onClick={e => e.stopPropagation()}
        style={{ ...glass, width: 380, padding: '28px 28px 24px', display: 'flex', flexDirection: 'column', gap: 22 }}
      >
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#fff', letterSpacing: '-0.01em', marginBottom: 3 }}>
              {t('profile.title')}
            </div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.28)', fontWeight: 300 }}>
              {t('profile.subtitle')}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.10)',
              borderRadius: 8, color: 'rgba(255,255,255,0.50)', fontSize: 14, cursor: 'pointer',
              width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
              lineHeight: 1, padding: 0,
            }}
          >✕</button>
        </div>

        {/* Avatar */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
          <div
            onClick={() => fileRef.current?.click()}
            style={{
              width: 96, height: 96, borderRadius: '50%', cursor: 'pointer',
              border: '2px solid rgba(0,229,255,0.30)',
              background: 'rgba(255,255,255,0.05)',
              overflow: 'hidden', position: 'relative',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              transition: 'border-color 0.2s',
            }}
            onMouseEnter={e => e.currentTarget.style.borderColor = CYAN}
            onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(0,229,255,0.30)'}
          >
            {avatarSrc ? (
              <img src={avatarSrc} alt="avatar" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : (
              <div style={{ fontSize: 36, color: 'rgba(255,255,255,0.18)', userSelect: 'none' }}>
                {(user?.display_name || user?.username || '?')[0].toUpperCase()}
              </div>
            )}
            <div style={{
              position: 'absolute', inset: 0,
              background: 'rgba(0,0,0,0.45)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              opacity: 0, transition: 'opacity 0.2s',
            }}
              onMouseEnter={e => e.currentTarget.style.opacity = '1'}
              onMouseLeave={e => e.currentTarget.style.opacity = '0'}
            >
              <span style={{ fontSize: 11, color: '#fff', fontWeight: 600, letterSpacing: '0.06em' }}>{t('profile.change')}</span>
            </div>
          </div>
          <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.gif,.webp" style={{ display: 'none' }} onChange={handleFileChange} />
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', textAlign: 'center', lineHeight: 1.5 }}>
            {t('profile.uploadHint')}
          </div>
        </div>

        {/* Username (readonly) */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.28)' }}>
            {t('profile.usernameReadonly')}
          </span>
          <div style={{
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.07)',
            borderRadius: 10, padding: '10px 14px',
            fontSize: 13, fontFamily: MONO,
            color: 'rgba(255,255,255,0.35)',
          }}>
            {user?.username}
          </div>
        </div>

        {/* Display Name */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.28)' }}>
            {t('profile.displayName')}
          </span>
          <input
            type="text"
            value={displayName}
            onChange={e => setDisplayName(e.target.value)}
            maxLength={100}
            placeholder={user?.username}
            style={{
              background: 'rgba(255,255,255,0.05)',
              border: '1px solid rgba(0,229,255,0.15)',
              borderRadius: 10, color: '#fff', padding: '10px 14px',
              fontSize: 13, fontFamily: MONO, outline: 'none', colorScheme: 'dark',
              transition: 'border-color 0.15s', width: '100%', boxSizing: 'border-box',
            }}
            onFocus={e => e.target.style.borderColor = 'rgba(0,229,255,0.45)'}
            onBlur={e => e.target.style.borderColor = 'rgba(0,229,255,0.15)'}
          />
        </div>

        {/* Status message */}
        {msg && (
          <div style={{
            padding: '9px 13px', borderRadius: 8, fontSize: 12, fontFamily: MONO,
            background: msg.type === 'ok' ? 'rgba(0,229,255,0.07)' : 'rgba(255,87,34,0.08)',
            border: `1px solid ${msg.type === 'ok' ? 'rgba(0,229,255,0.2)' : 'rgba(255,87,34,0.25)'}`,
            color: msg.type === 'ok' ? CYAN : '#ff7043',
          }}>
            {msg.text}
          </div>
        )}

        {/* Save button */}
        <button
          disabled={saving}
          onClick={e => { e.stopPropagation(); handleSave(); }}
          style={{
            background: saving ? 'rgba(0,229,255,0.05)' : 'linear-gradient(135deg,rgba(0,229,255,0.20),rgba(74,0,224,0.20))',
            border: `1px solid ${saving ? 'rgba(0,229,255,0.12)' : 'rgba(0,229,255,0.35)'}`,
            borderRadius: 12, color: saving ? 'rgba(0,229,255,0.35)' : CYAN,
            fontSize: 13, fontWeight: 700, padding: '13px', cursor: saving ? 'default' : 'pointer',
            fontFamily: SYS, letterSpacing: '0.06em', textTransform: 'uppercase',
            transition: 'all 0.2s',
            boxShadow: saving ? 'none' : '0 0 20px rgba(0,229,255,0.10)',
          }}
        >
          {saving ? t('profile.saving') : t('profile.save')}
        </button>
      </motion.div>
    </motion.div>
  );
}
