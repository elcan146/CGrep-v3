import React, { lazy, Suspense } from 'react';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { useTranslation } from './i18n/LanguageContext';
const LoginPage        = lazy(() => import('./auth/LoginPage'));
const MobileVerifyPage = lazy(() => import('./auth/MobileVerifyPage'));
const App              = lazy(() => import('./App'));

const VOID = '#05070b';
const CYAN = '#00e5ff';

function LoadingScreen() {
  return (
    <div style={{
      minHeight: '100vh', background: VOID,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <svg width="36" height="36" viewBox="0 0 40 40" fill="none" style={{ animation: 'spin 2s linear infinite' }}>
        <circle cx="20" cy="20" r="16" stroke="rgba(0,229,255,0.2)" strokeWidth="3"/>
        <path d="M20 4 A16 16 0 0 1 36 20" stroke={CYAN} strokeWidth="3" strokeLinecap="round"/>
      </svg>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function AccessDenied() {
  const { logout } = useAuth();
  const { t } = useTranslation();
  return (
    <div style={{
      minHeight: '100vh', background: VOID, color: '#fff',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      fontFamily: `'Space Grotesk', sans-serif`, gap: 16,
    }}>
      <div style={{ fontSize: 13, letterSpacing: '0.2em', color: 'rgba(255,87,34,0.7)', textTransform: 'uppercase' }}>
        {t('accessDenied.label')}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700 }}>{t('accessDenied.title')}</div>
      <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.4)' }}>
        {t('accessDenied.message')}
      </div>
      <button
        onClick={logout}
        style={{
          marginTop: 12, padding: '10px 24px',
          background: 'none', border: '1px solid rgba(255,255,255,0.15)',
          borderRadius: 8, color: 'rgba(255,255,255,0.4)',
          fontSize: 13, cursor: 'pointer',
        }}
      >
        {t('accessDenied.logout')}
      </button>
    </div>
  );
}

/**
 * Wraps a page component with a permission gate.
 * If the user lacks the required permission, renders <AccessDenied />.
 */
export function RequirePagePermission({ permission, children }) {
  const { user, hasPermission, loading } = useAuth();
  if (loading) return <LoadingScreen />;
  if (!user)   return <Suspense fallback={<LoadingScreen />}><LoginPage /></Suspense>;
  if (!hasPermission(permission)) return <AccessDenied />;
  return children;
}

function Router() {
  const { user, loading } = useAuth();

  if (loading) return <LoadingScreen />;

  // Handle /verify-qr/:sessionId routes for mobile QR flow
  const path = window.location.pathname;
  const qrMatch = path.match(/^\/verify-qr\/([a-f0-9-]{36})$/i);
  if (qrMatch) {
    return <MobileVerifyPage sessionId={qrMatch[1]} />;
  }

  // Not logged in → show login
  if (!user) return <LoginPage />;

  // Logged in → show full dashboard (dashboard itself uses RequirePermission internally)
  return <App />;
}

export default function AppShell() {
  return (
    <AuthProvider>
      <Suspense fallback={<LoadingScreen />}>
        <Router />
      </Suspense>
    </AuthProvider>
  );
}
