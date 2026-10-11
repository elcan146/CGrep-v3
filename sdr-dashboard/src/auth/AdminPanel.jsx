import React, { useState, useEffect, useCallback } from 'react';
import { API, useAuth } from './AuthContext';
import tracker from '../ActivityTracker';
import { useTranslation } from '../i18n/LanguageContext';

const VOID  = '#05070b';
const CYAN  = '#00e5ff';
const AMBER = '#ffaf00';
const RED   = '#ff5722';
const GREEN = '#69ff47';
const SYS   = `'Space Grotesk', sans-serif`;
const MONO  = `'JetBrains Mono', monospace`;

const fmtTime = (iso) => {
  if (!iso) return '—';
  const s = iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z';
  return new Date(s).toLocaleString('az-AZ', { timeZone: 'Asia/Site-1', hour12: false });
};

const glass = {
  background: 'rgba(10,14,23,0.97)',
  border: '1px solid rgba(0,229,255,0.18)',
  borderRadius: 16,
  boxShadow: '0 8px 48px rgba(0,0,0,0.85)',
  backdropFilter: 'blur(24px)',
};

function getTabs(t) {
  return [
    t('admin.tabs.session'),
    t('admin.tabs.users'),
    t('admin.tabs.roleMatrix'),
    t('admin.tabs.auditLogs'),
    t('admin.tabs.activity'),
    t('admin.tabs.whatsapp'),
    t('admin.tabs.backup'),
    t('admin.tabs.broadcast'),
    t('admin.tabs.mapDots'),
  ];
}

// ---------------------------------------------------------------------------
// Shared sub-components
// ---------------------------------------------------------------------------
function Row({ label, value, dim }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, alignItems: 'baseline' }}>
      <span style={{ fontSize: 11, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        {label}
      </span>
      <span style={{ fontSize: 12, color: dim ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.75)', fontFamily: MONO }}>
        {value}
      </span>
    </div>
  );
}

function Msg({ text, type }) {
  if (!text) return null;
  return (
    <div style={{
      padding: '9px 13px', borderRadius: 8, fontSize: 12, fontFamily: MONO,
      background: type === 'ok' ? 'rgba(0,229,255,0.07)' : 'rgba(255,87,34,0.08)',
      border: `1px solid ${type === 'ok' ? 'rgba(0,229,255,0.2)' : 'rgba(255,87,34,0.25)'}`,
      color: type === 'ok' ? CYAN : RED, marginBottom: 10,
    }}>
      {text}
    </div>
  );
}

function Btn({ onClick, disabled, danger, children, small }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: small ? '5px 12px' : '10px 18px',
        background: danger ? 'rgba(255,87,34,0.1)' : 'rgba(0,229,255,0.07)',
        border: `1px solid ${danger ? 'rgba(255,87,34,0.4)' : 'rgba(0,229,255,0.25)'}`,
        borderRadius: 8,
        color: danger ? '#ff7043' : CYAN,
        fontSize: small ? 11 : 13, fontWeight: 600,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        fontFamily: SYS, letterSpacing: '0.05em',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </button>
  );
}

function RecipientChips({ items, onRemove, color = CYAN, disabled = false }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
      {items.map((n, idx) => (
        <div key={n + idx} style={{
          display: 'flex', alignItems: 'center', gap: 6,
          background: `${color}14`, border: `1px solid ${color}33`,
          borderRadius: 7, padding: '5px 10px', fontSize: 12, fontFamily: MONO, color: '#fff',
        }}>
          {n}
          <button
            onClick={() => onRemove(idx)}
            disabled={disabled}
            style={{
              background: 'transparent', border: 'none', color: RED, cursor: disabled ? 'not-allowed' : 'pointer',
              fontSize: 14, lineHeight: 1, padding: 0,
            }}
          >×</button>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 1 — Session
// ---------------------------------------------------------------------------
function SessionTab() {
  const { t } = useTranslation();
  const { logout } = useAuth();
  const [info,    setInfo]    = useState(null);
  const [busy,    setBusy]    = useState(false);
  const [msg,     setMsg]     = useState('');
  const [msgType, setMsgType] = useState('ok');
  const [confirm, setConfirm] = useState(false);
  const fmt = iso => iso ? new Date(iso).toLocaleString() : '—';

  useEffect(() => {
    fetch(`${API}/api/admin/session-info`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null).then(d => d && setInfo(d)).catch(() => {});
  }, []);

  const doLogoutAll = async () => {
    setBusy(true); setMsg('');
    try {
      const r = await fetch(`${API}/api/admin/logout-all`, { method: 'POST', credentials: 'include' });
      const d = await r.json();
      if (r.ok) {
        setMsgType('ok'); setMsg(t('admin.session.allSessionsInvalidated'));
        setConfirm(false);
        setTimeout(() => logout(), 1500);
      } else {
        setMsgType('err'); setMsg(d.detail || t('common.failed'));
      }
    } catch { setMsgType('err'); setMsg(t('common.networkError')); }
    finally  { setBusy(false); }
  };

  return (
    <div>
      {info && (
        <div style={{
          background: 'rgba(0,229,255,0.04)', border: '1px solid rgba(0,229,255,0.12)',
          borderRadius: 10, padding: '14px 16px', marginBottom: 20,
        }}>
          <Row label={t('admin.session.loggedInAs')}    value={info.username} />
          <Row label={t('admin.session.sessionStarted')} value={fmt(info.session_issued_at)} />
          <Row label={t('admin.session.expiresAt')}      value={fmt(info.session_expires_at)} />
          <Row label={t('admin.session.sessionTTL')}     value={`${info.session_ttl_hours} hours`} />
          <Row label={t('admin.session.lastLogoutAll')} value={fmt(info.global_invalidated_at)} dim />
        </div>
      )}
      <Msg text={msg} type={msgType} />
      {!confirm ? (
        <Btn onClick={() => setConfirm(true)} disabled={busy} danger>{t('admin.session.forceLogout')}</Btn>
      ) : (
        <div style={{
          background: 'rgba(255,87,34,0.07)', border: '1px solid rgba(255,87,34,0.25)',
          borderRadius: 10, padding: 14, marginBottom: 10,
        }}>
          <div style={{ fontSize: 12, color: '#ff7043', marginBottom: 12, fontFamily: MONO }}>
            {t('admin.session.confirm')}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn onClick={doLogoutAll} disabled={busy} danger>
              {busy ? t('admin.session.loggingOut') : t('admin.session.yesLogout')}
            </Btn>
            <Btn onClick={() => setConfirm(false)}>{t('admin.session.cancel')}</Btn>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 2 — User Management
// ---------------------------------------------------------------------------
function UsersTab() {
  const { t } = useTranslation();
  const { user: me } = useAuth();
  const [users,    setUsers]    = useState([]);
  const [roles,    setRoles]    = useState([]);
  const [busy,     setBusy]     = useState(false);
  const [msg,      setMsg]      = useState('');
  const [msgType,  setMsgType]  = useState('ok');
  const [selected,  setSelected]  = useState(null);
  const [assignId,  setAssignId]  = useState('');
  const [logins,    setLogins]    = useState([]);
  const [loginBusy, setLoginBusy] = useState(false);
  const [sessions,  setSessions]  = useState([]);
  const [sesBusy,   setSesBusy]   = useState(false);

  const myMaxPriority = me?.is_admin ? 999
    : (me?.roles?.length ? Math.max(...me.roles.map(r => r.priority_level)) : 0);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [ur, rr] = await Promise.all([
        fetch(`${API}/api/admin/users`, { credentials: 'include' }),
        fetch(`${API}/api/admin/roles`, { credentials: 'include' }),
      ]);
      if (ur.ok) setUsers(await ur.json());
      if (rr.ok) setRoles(await rr.json());
    } catch {}
    finally { setBusy(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const flash = (text, type = 'ok') => { setMsg(text); setMsgType(type); setTimeout(() => setMsg(''), 3500); };

  const selectUser = async (u) => {
    if (selected?.id === u.id) { setSelected(null); setLogins([]); setSessions([]); return; }
    setSelected(u); setLogins([]); setSessions([]); setLoginBusy(true); setSesBusy(true);
    try {
      const [lr, sr] = await Promise.all([
        fetch(`${API}/api/admin/users/${u.id}/logins`, { credentials: 'include' }),
        fetch(`${API}/api/admin/users/${u.id}/sessions`, { credentials: 'include' }),
      ]);
      if (lr.ok) setLogins(await lr.json());
      if (sr.ok) setSessions(await sr.json());
    } catch {}
    finally { setLoginBusy(false); setSesBusy(false); }
  };

  const deviceIcon = s => s?.is_mobile ? '📱' : s?.is_tablet ? '�' : '🖥';

  const doAssign = async (userId, roleId) => {
    const r = await fetch(`${API}/api/admin/roles/assign`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, role_id: roleId }),
    });
    const d = await r.json();
    if (r.ok) { flash(t('admin.users.roleAssigned')); load(); }
    else       flash(d.detail || t('common.failed'), 'err');
  };

  const doRemove = async (userId, roleId) => {
    const r = await fetch(`${API}/api/admin/roles/remove`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, role_id: roleId }),
    });
    const d = await r.json();
    if (r.ok) { flash(t('admin.users.roleRemoved')); load(); }
    else       flash(d.detail || t('common.failed'), 'err');
  };

  const assignableRoles = roles.filter(r => r.priority_level <= myMaxPriority);

  return (
    <div>
      <Msg text={msg} type={msgType} />
      {busy && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', marginBottom: 10 }}>{t('common.loading')}</div>}

      {/* User table */}
      <div style={{ overflowY: 'auto', maxHeight: 340 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(0,229,255,0.1)' }}>
              {[t('admin.users.user'), t('admin.users.roles'), t('admin.users.status'), t('admin.users.actions')].map(h => (
                <th key={h} style={{
                  textAlign: 'left', padding: '6px 8px', fontSize: 10,
                  color: 'rgba(0,229,255,0.5)', letterSpacing: '0.12em',
                  textTransform: 'uppercase', fontWeight: 600,
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr
                key={u.id}
                style={{
                  borderBottom: '1px solid rgba(255,255,255,0.04)',
                  background: selected?.id === u.id ? 'rgba(0,229,255,0.05)' : 'transparent',
                }}
              >
                <td style={{ padding: '8px 8px', color: '#fff', fontFamily: MONO }}>
                  {u.username}
                  {u.is_admin && <span style={{ marginLeft: 5, fontSize: 9, color: AMBER, background: 'rgba(255,175,0,0.1)', padding: '1px 5px', borderRadius: 4 }}>{t('admin.users.admin')}</span>}
                </td>
                <td style={{ padding: '8px 8px' }}>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {u.roles.length === 0
                      ? <span style={{ color: 'rgba(255,255,255,0.2)', fontSize: 11 }}>{t('admin.users.noRole')}</span>
                      : u.roles.map(r => (
                        <span key={r.id} style={{
                          fontSize: 10, padding: '2px 7px', borderRadius: 5,
                          background: 'rgba(0,229,255,0.1)', color: CYAN,
                          border: '1px solid rgba(0,229,255,0.2)',
                        }}>{r.name}</span>
                      ))
                    }
                  </div>
                </td>
                <td style={{ padding: '8px 8px' }}>
                  <span style={{ fontSize: 10, color: u.is_active ? GREEN : RED }}>
                    {u.is_active ? t('common.active') : t('common.inactive')}
                  </span>
                </td>
                <td style={{ padding: '8px 8px' }}>
                  <Btn small onClick={() => selectUser(u)}>
                    {selected?.id === u.id ? t('admin.users.done') : t('admin.users.view')}
                  </Btn>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Expanded user panel */}
      {selected && (
        <div style={{
          marginTop: 12, padding: 14,
          background: 'rgba(0,229,255,0.04)', border: '1px solid rgba(0,229,255,0.15)',
          borderRadius: 10,
        }}>
          <div style={{ fontSize: 12, color: CYAN, marginBottom: 12, fontFamily: MONO }}>
            <strong>{selected.username}</strong>
            {selected.is_admin && <span style={{ marginLeft: 6, fontSize: 9, color: AMBER, background: 'rgba(255,175,0,0.1)', padding: '1px 5px', borderRadius: 4 }}>{t('admin.users.admin')}</span>}
          </div>

          {/* Browser sessions — every device that used the app */}
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>{t('admin.users.browserSessions')}</div>
          {sesBusy
            ? <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', fontFamily: MONO, marginBottom: 10 }}>{t('common.loading')}</div>
            : sessions.length === 0
              ? <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', fontFamily: MONO, marginBottom: 10 }}>{t('admin.users.noSessions')}</div>
              : <div style={{ marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 5, maxHeight: 220, overflowY: 'auto' }}>
                  {sessions.map((s, i) => (
                    <div key={i} style={{
                      padding: '9px 12px', borderRadius: 8, fontSize: 11,
                      background: 'rgba(0,0,0,0.35)', border: '1px solid rgba(255,255,255,0.07)',
                      fontFamily: MONO,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                        <span style={{ fontSize: 16 }}>{deviceIcon(s)}</span>
                        <span style={{ color: '#fff', fontWeight: 700, fontSize: 12 }}>{s.model || s.device}</span>
                        <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', marginLeft: 'auto' }}>{s.request_count} reqs</span>
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 10, color: 'rgba(255,255,255,0.45)' }}>
                        <span>🌐 {s.browser}</span>
                        <span>💻 {s.os}{s.arch ? ` (${s.arch})` : ''}</span>
                        <span>📍 {s.ip_address || '—'}</span>
                      </div>
                      <div style={{ marginTop: 4, fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>
                        First: {fmtTime(s.first_seen)} · Last: {fmtTime(s.last_seen)}
                      </div>
                      {s.raw && (
                        <div style={{ marginTop: 3, fontSize: 9, color: 'rgba(255,255,255,0.15)', fontFamily: MONO, wordBreak: 'break-all' }}>
                          {s.raw}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
          }

          {/* Login history */}
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>{t('admin.users.loginAttempts')}</div>
          {loginBusy
            ? <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', fontFamily: MONO, marginBottom: 10 }}>{t('common.loading')}</div>
            : logins.length === 0
              ? <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', fontFamily: MONO, marginBottom: 10 }}>{t('admin.users.noLogins')}</div>
              : <div style={{ marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 140, overflowY: 'auto' }}>
                  {logins.map((l, i) => (
                    <div key={i} style={{
                      padding: '6px 10px', borderRadius: 6, fontSize: 10,
                      background: l.action === 'LOGIN_SUCCESS' ? 'rgba(0,229,255,0.04)' : 'rgba(255,87,34,0.06)',
                      border: `1px solid ${l.action === 'LOGIN_SUCCESS' ? 'rgba(0,229,255,0.1)' : 'rgba(255,87,34,0.15)'}`,
                      fontFamily: MONO, color: 'rgba(255,255,255,0.55)',
                      display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap',
                    }}>
                      <span style={{ color: l.action === 'LOGIN_SUCCESS' ? GREEN : RED }}>
                        {l.action === 'LOGIN_SUCCESS' ? '✓' : '✗'} {l.action === 'LOGIN_SUCCESS' ? t('admin.users.success') : t('admin.users.failed')}
                      </span>
                      <span>{l.ip_address || '—'}</span>
                      <span style={{ color: 'rgba(255,255,255,0.25)' }}>{fmtTime(l.timestamp)}</span>
                    </div>
                  ))}
                </div>
          }

          <div style={{ borderTop: '1px solid rgba(255,255,255,0.07)', marginBottom: 12 }} />

          {/* Remove current roles */}
          {selected.roles.length > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.1em' }}>{t('admin.users.removeRole')}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {selected.roles.map(r => {
                  const canEdit = r.priority_level <= myMaxPriority;
                  return (
                    <button
                      key={r.id}
                      disabled={!canEdit}
                      onClick={() => doRemove(selected.id, r.id).then(() => setSelected(prev => prev ? { ...prev, roles: prev.roles.filter(x => x.id !== r.id) } : null))}
                      style={{
                        fontSize: 11, padding: '3px 10px', borderRadius: 6, cursor: canEdit ? 'pointer' : 'not-allowed',
                        background: 'rgba(255,87,34,0.08)', border: '1px solid rgba(255,87,34,0.3)',
                        color: canEdit ? RED : 'rgba(255,87,34,0.3)', fontFamily: SYS, opacity: canEdit ? 1 : 0.5,
                      }}
                    >✕ {r.name}</button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Assign new role */}
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.1em' }}>{t('admin.users.assignRole')}</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <select
              value={assignId}
              onChange={e => setAssignId(e.target.value)}
              style={{
                flex: 1, background: 'rgba(0,0,0,0.5)', border: '1px solid rgba(0,229,255,0.2)',
                borderRadius: 7, color: '#fff', fontSize: 12, padding: '6px 10px', fontFamily: SYS,
              }}
            >
              <option value="">{t('admin.users.selectRole')}</option>
              {assignableRoles
                .filter(r => !selected.roles.find(sr => sr.id === r.id))
                .map(r => (
                  <option key={r.id} value={r.id}>{r.name} (p={r.priority_level})</option>
                ))}
            </select>
            <Btn
              small
              disabled={!assignId}
              onClick={() => {
                doAssign(selected.id, assignId).then(() => {
                  const role = roles.find(r => r.id === assignId);
                  if (role) setSelected(prev => prev ? { ...prev, roles: [...prev.roles, role] } : null);
                  setAssignId('');
                });
              }}
            >{t('admin.users.assign')}</Btn>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 3 — Role Matrix
// ---------------------------------------------------------------------------
function RoleMatrixTab() {
  const { t } = useTranslation();
  const { user: me } = useAuth();
  const [roles,   setRoles]   = useState([]);
  const [perms,   setPerms]   = useState([]);
  const [busy,    setBusy]    = useState(false);
  const [saving,  setSaving]  = useState(null); // role_id currently saving
  const [msg,     setMsg]     = useState('');
  const [msgType, setMsgType] = useState('ok');
  const [draft,   setDraft]   = useState({}); // { role_id: Set<perm_id> }
  const [newRole, setNewRole] = useState({ name: '', priority_level: 0, open: false });
  const [newPerm, setNewPerm] = useState({ name: '', open: false });

  const myMaxPriority = me?.is_admin ? 999
    : (me?.roles?.length ? Math.max(...me.roles.map(r => r.priority_level)) : 0);
  const myPermSet = new Set(me?.permissions || []);

  const flash = (text, type = 'ok') => { setMsg(text); setMsgType(type); setTimeout(() => setMsg(''), 3500); };

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [rr, pr] = await Promise.all([
        fetch(`${API}/api/admin/roles`,       { credentials: 'include' }),
        fetch(`${API}/api/admin/permissions`, { credentials: 'include' }),
      ]);
      const rData = rr.ok ? await rr.json() : [];
      const pData = pr.ok ? await pr.json() : [];
      setRoles(rData);
      setPerms(pData);
      const d = {};
      rData.forEach(r => { d[r.id] = new Set(r.permissions.map(p => p.id)); });
      setDraft(d);
    } catch {}
    finally { setBusy(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = (roleId, permId) => {
    const rolePriority = roles.find(r => r.id === roleId)?.priority_level ?? 0;
    if (rolePriority > myMaxPriority && !me?.is_admin) return; // can't edit higher roles
    const permName = perms.find(p => p.id === permId)?.name ?? '';
    if (!myPermSet.has(permName) && !me?.is_admin) return; // can't assign perms you don't have
    setDraft(prev => {
      const s = new Set(prev[roleId] || []);
      if (s.has(permId)) s.delete(permId); else s.add(permId);
      return { ...prev, [roleId]: s };
    });
  };

  const save = async (roleId) => {
    setSaving(roleId);
    const permIds = [...(draft[roleId] || [])];
    try {
      const r = await fetch(`${API}/api/admin/roles/${roleId}/permissions`, {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role_id: roleId, permission_ids: permIds }),
      });
      const d = await r.json();
      if (r.ok) { flash(t('admin.roles.saved', { name: roles.find(x => x.id === roleId)?.name })); load(); }
      else       flash(d.detail || t('common.failed'), 'err');
    } catch { flash(t('common.networkError'), 'err'); }
    finally { setSaving(null); }
  };

  const createRole = async () => {
    if (!newRole.name.trim()) return;
    const r = await fetch(`${API}/api/admin/roles`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newRole.name.trim(), priority_level: Number(newRole.priority_level) }),
    });
    const d = await r.json();
    if (r.ok) { flash(t('admin.roles.created', { name: d.name })); setNewRole({ name: '', priority_level: 0, open: false }); load(); }
    else       flash(d.detail || t('common.failed'), 'err');
  };

  const createPerm = async () => {
    if (!newPerm.name.trim()) return;
    const r = await fetch(`${API}/api/admin/permissions`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newPerm.name.trim() }),
    });
    const d = await r.json();
    if (r.ok) { flash(t('admin.roles.permissionCreated', { name: d.name })); setNewPerm({ name: '', open: false }); load(); }
    else       flash(d.detail || t('common.failed'), 'err');
  };

  const tdStyle = { padding: '6px 10px', borderBottom: '1px solid rgba(255,255,255,0.04)', verticalAlign: 'middle' };
  const thStyle = { padding: '6px 8px', fontSize: 10, color: 'rgba(0,229,255,0.55)', letterSpacing: '0.1em', textTransform: 'uppercase', textAlign: 'center', borderBottom: '1px solid rgba(0,229,255,0.15)' };

  return (
    <div>
      <Msg text={msg} type={msgType} />
      {busy && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', marginBottom: 8 }}>{t('common.loading')}</div>}

      {/* Matrix */}
      <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: 320, marginBottom: 14 }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
          <thead>
            <tr>
              <th style={{ ...thStyle, textAlign: 'left', minWidth: 160 }}>{t('admin.roles.permission')}</th>
              {roles.map(r => {
                const editable = r.priority_level <= myMaxPriority || me?.is_admin;
                return (
                  <th key={r.id} style={{ ...thStyle }}>
                    <div>{r.name}</div>
                    <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)' }}>p={r.priority_level}</div>
                    <button
                      onClick={() => save(r.id)}
                      disabled={!editable || saving === r.id}
                      style={{
                        marginTop: 4, fontSize: 9, padding: '2px 8px', borderRadius: 4,
                        background: editable ? 'rgba(0,229,255,0.1)' : 'rgba(255,255,255,0.03)',
                        border: `1px solid ${editable ? 'rgba(0,229,255,0.3)' : 'rgba(255,255,255,0.08)'}`,
                        color: editable ? CYAN : 'rgba(255,255,255,0.2)',
                        cursor: editable ? 'pointer' : 'not-allowed', fontFamily: SYS,
                      }}
                    >
                      {saving === r.id ? t('admin.roles.saving') : t('admin.roles.save')}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {perms.map(p => {
              const canAssign = myPermSet.has(p.name) || me?.is_admin;
              return (
                <tr key={p.id} style={{ background: canAssign ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
                  <td style={{ ...tdStyle, fontFamily: MONO, color: canAssign ? '#fff' : 'rgba(255,255,255,0.3)', fontSize: 11 }}>
                    {p.name}
                    {!canAssign && <span style={{ marginLeft: 6, fontSize: 9, color: 'rgba(255,87,34,0.4)' }}>{t('admin.roles.locked')}</span>}
                  </td>
                  {roles.map(r => {
                    const editable = (r.priority_level <= myMaxPriority || me?.is_admin) && canAssign;
                    const checked  = draft[r.id]?.has(p.id) ?? false;
                    return (
                      <td key={r.id} style={{ ...tdStyle, textAlign: 'center' }}>
                        <div
                          onClick={() => editable && toggle(r.id, p.id)}
                          style={{
                            width: 18, height: 18, borderRadius: 4, margin: '0 auto',
                            background: checked ? CYAN : 'rgba(255,255,255,0.05)',
                            border: `1px solid ${checked ? CYAN : 'rgba(255,255,255,0.15)'}`,
                            cursor: editable ? 'pointer' : 'not-allowed',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            transition: 'background 0.15s',
                          }}
                        >
                          {checked && <span style={{ fontSize: 10, color: VOID, fontWeight: 900 }}>✓</span>}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Create role / perm row */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {!newRole.open ? (
          <Btn small onClick={() => setNewRole(p => ({ ...p, open: true }))}>{t('admin.roles.newRole')}</Btn>
        ) : (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              placeholder={t('admin.roles.roleName')}
              value={newRole.name}
              onChange={e => setNewRole(p => ({ ...p, name: e.target.value }))}
              style={{ ...inputStyle, width: 120 }}
            />
            <input
              type="number"
              placeholder={t('admin.roles.priority')}
              value={newRole.priority_level}
              onChange={e => setNewRole(p => ({ ...p, priority_level: e.target.value }))}
              style={{ ...inputStyle, width: 70 }}
            />
            <Btn small onClick={createRole}>{t('common.create')}</Btn>
            <Btn small onClick={() => setNewRole({ name: '', priority_level: 0, open: false })}>✕</Btn>
          </div>
        )}

        {!newPerm.open ? (
          <Btn small onClick={() => setNewPerm(p => ({ ...p, open: true }))}>{t('admin.roles.newPermission')}</Btn>
        ) : (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input
              placeholder={t('admin.roles.permissionExample')}
              value={newPerm.name}
              onChange={e => setNewPerm(p => ({ ...p, name: e.target.value }))}
              style={{ ...inputStyle, width: 160 }}
            />
            <Btn small onClick={createPerm}>{t('common.create')}</Btn>
            <Btn small onClick={() => setNewPerm({ name: '', open: false })}>✕</Btn>
          </div>
        )}
      </div>
    </div>
  );
}

const inputStyle = {
  background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(0,229,255,0.2)',
  borderRadius: 6, color: '#fff', fontSize: 12, padding: '5px 9px', fontFamily: SYS,
  outline: 'none',
};

// ---------------------------------------------------------------------------
// Tab 4 — Audit Logs
// ---------------------------------------------------------------------------

const ACTION_COLOURS = {
  LOGIN_SUCCESS:      { bg: 'rgba(105,255,71,0.12)',  border: 'rgba(105,255,71,0.35)',  text: '#69ff47' },
  LOGIN_FAILED:       { bg: 'rgba(255,87,34,0.12)',   border: 'rgba(255,87,34,0.4)',    text: '#ff7043' },
  LOGOUT:             { bg: 'rgba(255,175,0,0.1)',    border: 'rgba(255,175,0,0.3)',    text: '#ffaf00' },
  LOGOUT_ALL:         { bg: 'rgba(255,87,34,0.15)',   border: 'rgba(255,87,34,0.5)',    text: '#ff5722' },
  ROLE_ASSIGNED:      { bg: 'rgba(0,229,255,0.1)',    border: 'rgba(0,229,255,0.3)',    text: '#00e5ff' },
  ROLE_REMOVED:       { bg: 'rgba(255,175,0,0.12)',   border: 'rgba(255,175,0,0.35)',   text: '#ffca28' },
  BACKUP_TRIGGERED:   { bg: 'rgba(156,39,176,0.12)',  border: 'rgba(156,39,176,0.4)',   text: '#ce93d8' },
};
const ACTION_DEFAULT = { bg: 'rgba(255,255,255,0.05)', border: 'rgba(255,255,255,0.15)', text: 'rgba(255,255,255,0.6)' };

const KNOWN_ACTIONS = ['LOGIN_SUCCESS','LOGIN_FAILED','LOGOUT','LOGOUT_ALL','ROLE_ASSIGNED','ROLE_REMOVED','BACKUP_TRIGGERED'];
const PAGE_SIZE = 20;

function ActionBadge({ action }) {
  const c = ACTION_COLOURS[action] || ACTION_DEFAULT;
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px', borderRadius: 5, fontSize: 10, fontWeight: 700,
      fontFamily: MONO, letterSpacing: '0.06em', whiteSpace: 'nowrap',
      background: c.bg, border: `1px solid ${c.border}`, color: c.text,
    }}>
      {action}
    </span>
  );
}

function JsonModal({ data, onClose }) {
  const { t } = useTranslation();
  const lines = JSON.stringify(data, null, 2).split('\n');
  const colour = (line) => {
    if (/^\s*"[^"]+":/.test(line)) return '#80cbc4';
    if (/"[^"]+"/.test(line)) return '#a5d6a7';
    if (/true|false|null/.test(line)) return '#ff8a65';
    if (/\d/.test(line)) return '#fff59d';
    return 'rgba(255,255,255,0.55)';
  };
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 10001,
        background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        animation: 'ap-fadein 0.18s ease both',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          ...glass,
          width: '100%', maxWidth: 500, padding: '20px 22px',
          maxHeight: '70vh', display: 'flex', flexDirection: 'column',
          animation: 'ap-slidein 0.2s cubic-bezier(.16,1,.3,1) both',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <span style={{ fontSize: 11, letterSpacing: '0.15em', color: 'rgba(0,229,255,0.6)', textTransform: 'uppercase' }}>
            {t('admin.audit.eventDetails')}
          </span>
          <button onClick={onClose} style={{
            background: 'none', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 6, color: 'rgba(255,255,255,0.4)', fontSize: 14,
            width: 28, height: 28, cursor: 'pointer',
          }}>✕</button>
        </div>
        <pre style={{
          overflowY: 'auto', margin: 0, fontSize: 12, fontFamily: MONO,
          background: 'rgba(0,0,0,0.35)', borderRadius: 8, padding: '12px 14px',
          flex: 1, lineHeight: 1.7,
        }}>
          {lines.map((line, i) => (
            <span key={i} style={{ color: colour(line), display: 'block' }}>{line}</span>
          ))}
        </pre>
      </div>
    </div>
  );
}

function AuditTab() {
  const { t } = useTranslation();
  const [logs,      setLogs]      = useState([]);
  const [total,     setTotal]     = useState(0);
  const [page,      setPage]      = useState(0);
  const [busy,      setBusy]      = useState(false);
  const [modal,     setModal]     = useState(null);
  const [filterAction, setFilterAction] = useState('');
  const [filterStart,  setFilterStart]  = useState('');
  const [filterEnd,    setFilterEnd]    = useState('');

  const load = useCallback(async (pg = 0, fa = filterAction, fs = filterStart, fe = filterEnd) => {
    setBusy(true);
    try {
      const params = new URLSearchParams({ limit: PAGE_SIZE, offset: pg * PAGE_SIZE });
      if (fa) params.set('action', fa);
      if (fs) params.set('start_date', fs);
      if (fe) params.set('end_date', fe);
      const r = await fetch(`${API}/api/admin/audit-logs?${params}`, { credentials: 'include' });
      if (r.ok) {
        const d = await r.json();
        setTotal(d.total);
        setLogs(d.logs);
      }
    } catch {}
    finally { setBusy(false); }
  }, [filterAction, filterStart, filterEnd]);

  useEffect(() => { load(0); }, []);

  const applyFilters = () => { setPage(0); load(0, filterAction, filterStart, filterEnd); };
  const clearFilters = () => {
    setFilterAction(''); setFilterStart(''); setFilterEnd('');
    setPage(0); load(0, '', '', '');
  };
  const goPage = (p) => { setPage(p); load(p, filterAction, filterStart, filterEnd); };

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const fmtTs = fmtTime;

  return (
    <div style={{ animation: 'ap-fadein 0.3s ease both' }}>
      {modal && <JsonModal data={modal} onClose={() => setModal(null)} />}

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14, alignItems: 'flex-end' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 10, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>{t('admin.audit.action')}</span>
          <select
            value={filterAction}
            onChange={e => setFilterAction(e.target.value)}
            style={{ ...inputStyle, minWidth: 160 }}
          >
            <option value="">{t('admin.audit.allActions')}</option>
            {KNOWN_ACTIONS.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 10, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>{t('admin.audit.from')}</span>
          <input type="date" value={filterStart} onChange={e => setFilterStart(e.target.value)} style={{ ...inputStyle, colorScheme: 'dark' }} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 10, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>{t('admin.audit.to')}</span>
          <input type="date" value={filterEnd} onChange={e => setFilterEnd(e.target.value)} style={{ ...inputStyle, colorScheme: 'dark' }} />
        </div>
        <div style={{ display: 'flex', gap: 6, alignSelf: 'flex-end' }}>
          <Btn small onClick={applyFilters} disabled={busy}>{t('common.apply')}</Btn>
          <Btn small onClick={clearFilters} disabled={busy}>{t('common.clear')}</Btn>
        </div>
        <div style={{ marginLeft: 'auto', alignSelf: 'flex-end', fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: MONO }}>
          {total} {t('admin.audit.events')}{total !== 1 ? '' : ''}
        </div>
      </div>

      {/* Table */}
      {busy ? (
        <div style={{ textAlign: 'center', padding: '32px 0', color: 'rgba(0,229,255,0.4)', fontSize: 12, fontFamily: MONO }}>
          {t('admin.audit.loading')}
        </div>
      ) : logs.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '32px 0', color: 'rgba(255,255,255,0.2)', fontSize: 12, fontFamily: MONO }}>
          {t('admin.audit.noEvents')}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {/* Column headers */}
          <div style={{
            display: 'grid', gridTemplateColumns: '130px 80px 1fr 72px 100px 56px',
            gap: 8, padding: '0 10px 6px', marginBottom: 2,
          }}>
            {[t('admin.audit.timestamp'), t('admin.audit.actor'), t('admin.audit.action'), t('admin.audit.resource'), t('admin.audit.ipAddress'), ''].map(h => (
              <span key={h} style={{ fontSize: 9, letterSpacing: '0.12em', color: 'rgba(0,229,255,0.35)', textTransform: 'uppercase' }}>{h}</span>
            ))}
          </div>

          {logs.map((log, idx) => (
            <div
              key={log.id}
              style={{
                display: 'grid', gridTemplateColumns: '130px 80px 1fr 72px 100px 56px',
                gap: 8, padding: '8px 10px', borderRadius: 8,
                background: idx % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent',
                border: '1px solid rgba(255,255,255,0.04)',
                alignItems: 'center',
                animation: `ap-fadein 0.25s ${idx * 0.03}s ease both`,
                transition: 'background 0.15s',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,229,255,0.04)'}
              onMouseLeave={e => e.currentTarget.style.background = idx % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent'}
            >
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.45)', fontFamily: MONO, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {fmtTs(log.timestamp)}
              </span>
              <span style={{ fontSize: 11, color: log.actor ? 'rgba(255,255,255,0.7)' : 'rgba(255,255,255,0.2)', fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {log.actor || t('admin.users.noRole')}
              </span>
              <div><ActionBadge action={log.action} /></div>
              <span style={{ fontSize: 10, color: 'rgba(255,175,0,0.65)', fontFamily: MONO, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {log.resource_type}
              </span>
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {log.ip_address || t('admin.users.noRole')}
              </span>
              <button
                onClick={() => setModal(log.details && Object.keys(log.details).length ? log.details : { note: t('common.noDetails') })}
                style={{
                  background: 'rgba(0,229,255,0.06)', border: '1px solid rgba(0,229,255,0.2)',
                  borderRadius: 5, color: 'rgba(0,229,255,0.6)', fontSize: 10,
                  fontFamily: MONO, cursor: 'pointer', padding: '3px 7px',
                  transition: 'all 0.15s',
                }}
                onMouseEnter={e => { e.currentTarget.style.background='rgba(0,229,255,0.14)'; e.currentTarget.style.color='#00e5ff'; }}
                onMouseLeave={e => { e.currentTarget.style.background='rgba(0,229,255,0.06)'; e.currentTarget.style.color='rgba(0,229,255,0.6)'; }}
              >
                {t('common.json')}
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 8, marginTop: 16 }}>
          <Btn small onClick={() => goPage(page - 1)} disabled={page === 0 || busy}>{t('common.prev')}</Btn>
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', fontFamily: MONO }}>
            {page + 1} / {totalPages}
          </span>
          <Btn small onClick={() => goPage(page + 1)} disabled={page >= totalPages - 1 || busy}>{t('common.next')}</Btn>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 5 — Activity Log (comprehensive request + UI event feed)
// ---------------------------------------------------------------------------

const CATEGORY_COLOURS = {
  API:   { bg: 'rgba(0,229,255,0.08)',   border: 'rgba(0,229,255,0.25)',  text: '#00e5ff' },
  UI:    { bg: 'rgba(105,255,71,0.08)',  border: 'rgba(105,255,71,0.25)', text: '#69ff47' },
  WS:    { bg: 'rgba(255,175,0,0.08)',   border: 'rgba(255,175,0,0.25)',  text: '#ffaf00' },
  ERROR: { bg: 'rgba(255,87,34,0.12)',   border: 'rgba(255,87,34,0.35)',  text: '#ff5722' },
  NAV:   { bg: 'rgba(156,39,176,0.10)',  border: 'rgba(156,39,176,0.35)', text: '#ce93d8' },
};
const CAT_DEFAULT = { bg: 'rgba(255,255,255,0.04)', border: 'rgba(255,255,255,0.12)', text: 'rgba(255,255,255,0.5)' };
const KNOWN_CATEGORIES = ['API', 'UI', 'WS', 'ERROR', 'NAV'];
const ACT_PAGE = 25;

const STATUS_COLOUR = (code) => {
  if (!code) return 'rgba(255,255,255,0.25)';
  if (code < 300) return '#69ff47';
  if (code < 400) return '#ffaf00';
  if (code < 500) return '#ff8a65';
  return '#ff5722';
};

function CatBadge({ cat }) {
  const c = CATEGORY_COLOURS[cat] || CAT_DEFAULT;
  return (
    <span style={{
      display: 'inline-block', padding: '2px 7px', borderRadius: 5,
      fontSize: 9, fontWeight: 700, fontFamily: MONO, letterSpacing: '0.08em',
      background: c.bg, border: `1px solid ${c.border}`, color: c.text, whiteSpace: 'nowrap',
    }}>
      {cat}
    </span>
  );
}

function ActivityTab() {
  const { t } = useTranslation();
  const [logs,      setLogs]      = useState([]);
  const [total,     setTotal]     = useState(0);
  const [page,      setPage]      = useState(0);
  const [busy,      setBusy]      = useState(false);
  const [modal,     setModal]     = useState(null);
  const [filterCat,   setFilterCat]   = useState('');
  const [filterStart, setFilterStart] = useState('');
  const [filterEnd,   setFilterEnd]   = useState('');

  const load = useCallback(async (pg = 0, fc = filterCat, fs = filterStart, fe = filterEnd) => {
    setBusy(true);
    try {
      const params = new URLSearchParams({ limit: ACT_PAGE, offset: pg * ACT_PAGE });
      if (fc) params.set('category', fc);
      if (fs) params.set('start_date', fs);
      if (fe) params.set('end_date', fe);
      const r = await fetch(`${API}/api/admin/activity-logs?${params}`, { credentials: 'include' });
      if (r.ok) { const d = await r.json(); setTotal(d.total); setLogs(d.logs); }
    } catch {}
    finally { setBusy(false); }
  }, [filterCat, filterStart, filterEnd]);

  useEffect(() => { load(0); }, []);

  const apply  = () => { setPage(0); load(0, filterCat, filterStart, filterEnd); };
  const clear  = () => { setFilterCat(''); setFilterStart(''); setFilterEnd(''); setPage(0); load(0,'','',''); };
  const goPage = (p) => { setPage(p); load(p, filterCat, filterStart, filterEnd); };

  const totalPages = Math.ceil(total / ACT_PAGE);
  const fmtTs = fmtTime;

  return (
    <div style={{ animation: 'ap-fadein 0.3s ease both' }}>
      {modal && <JsonModal data={modal} onClose={() => setModal(null)} />}

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14, alignItems: 'flex-end' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 10, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>{t('admin.activity.category')}</span>
          <select value={filterCat} onChange={e => setFilterCat(e.target.value)} style={{ ...inputStyle, minWidth: 120 }}>
            <option value="">{t('admin.activity.all')}</option>
            {KNOWN_CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 10, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>{t('admin.activity.from')}</span>
          <input type="date" value={filterStart} onChange={e => setFilterStart(e.target.value)} style={{ ...inputStyle, colorScheme: 'dark' }} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 10, color: 'rgba(0,229,255,0.45)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>{t('admin.activity.to')}</span>
          <input type="date" value={filterEnd} onChange={e => setFilterEnd(e.target.value)} style={{ ...inputStyle, colorScheme: 'dark' }} />
        </div>
        <div style={{ display: 'flex', gap: 6, alignSelf: 'flex-end' }}>
          <Btn small onClick={apply} disabled={busy}>{t('common.apply')}</Btn>
          <Btn small onClick={clear} disabled={busy}>{t('common.clear')}</Btn>
        </div>
        <div style={{ marginLeft: 'auto', alignSelf: 'flex-end', fontSize: 11, color: 'rgba(255,255,255,0.22)', fontFamily: MONO }}>
          {total.toLocaleString()} {t('admin.activity.events')}{total !== 1 ? '' : ''}
        </div>
      </div>

      {/* Table */}
      {busy ? (
        <div style={{ textAlign: 'center', padding: '32px 0', color: 'rgba(0,229,255,0.4)', fontSize: 12, fontFamily: MONO }}>{t('admin.activity.loading')}</div>
      ) : logs.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '32px 0', color: 'rgba(255,255,255,0.2)', fontSize: 12, fontFamily: MONO }}>{t('admin.activity.noEvents')}</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {/* Column headers */}
          <div style={{ display: 'grid', gridTemplateColumns: '110px 65px 42px 55px 1fr 85px 38px', gap: 6, padding: '0 8px 6px' }}>
            {[t('admin.activity.timestamp'),t('admin.activity.user'),t('admin.activity.cat'),t('admin.activity.status'),t('admin.activity.event'),t('admin.activity.ip'),''].map(h => (
              <span key={h} style={{ fontSize: 9, letterSpacing: '0.12em', color: 'rgba(0,229,255,0.3)', textTransform: 'uppercase' }}>{h}</span>
            ))}
          </div>

          {logs.map((log, idx) => (
            <div
              key={log.id}
              style={{
                display: 'grid', gridTemplateColumns: '110px 65px 42px 55px 1fr 85px 38px',
                gap: 6, padding: '7px 8px', borderRadius: 7,
                background: idx % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent',
                border: '1px solid rgba(255,255,255,0.03)',
                alignItems: 'center',
                animation: `ap-fadein 0.2s ${Math.min(idx, 10) * 0.025}s ease both`,
                transition: 'background 0.12s',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,229,255,0.035)'}
              onMouseLeave={e => e.currentTarget.style.background = idx % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent'}
            >
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)', fontFamily: MONO, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {fmtTs(log.timestamp)}
              </span>
              <span style={{ fontSize: 10, color: log.username ? 'rgba(255,255,255,0.65)' : 'rgba(255,255,255,0.18)', fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {log.username || t('admin.users.noRole')}
              </span>
              <div><CatBadge cat={log.category} /></div>
              <span style={{ fontSize: 10, fontFamily: MONO, fontWeight: 700, color: STATUS_COLOUR(log.status_code) }}>
                {log.status_code || t('admin.users.noRole')}
                {log.duration_ms != null && (
                  <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.2)', fontWeight: 400 }}> {log.duration_ms}ms</span>
                )}
              </span>
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.5)', fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={log.event_type}>
                {log.event_type}
              </span>
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)', fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {log.ip_address || t('admin.users.noRole')}
              </span>
              <button
                onClick={() => setModal({ event_type: log.event_type, path: log.path, user_agent: log.user_agent, session_id: log.session_id, payload: log.payload })}
                style={{
                  background: 'rgba(0,229,255,0.05)', border: '1px solid rgba(0,229,255,0.18)',
                  borderRadius: 4, color: 'rgba(0,229,255,0.5)', fontSize: 9,
                  fontFamily: MONO, cursor: 'pointer', padding: '2px 5px', transition: 'all 0.15s',
                }}
                onMouseEnter={e => { e.currentTarget.style.background='rgba(0,229,255,0.12)'; e.currentTarget.style.color='#00e5ff'; }}
                onMouseLeave={e => { e.currentTarget.style.background='rgba(0,229,255,0.05)'; e.currentTarget.style.color='rgba(0,229,255,0.5)'; }}
              >
                ···
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 8, marginTop: 14 }}>
          <Btn small onClick={() => goPage(page - 1)} disabled={page === 0 || busy}>{t('common.prev')}</Btn>
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', fontFamily: MONO }}>{page + 1} / {totalPages}</span>
          <Btn small onClick={() => goPage(page + 1)} disabled={page >= totalPages - 1 || busy}>{t('common.next')}</Btn>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 6 — WhatsApp / Green API test
// ---------------------------------------------------------------------------
function WhatsAppTab() {
  const { t } = useTranslation();
  const { API: API_BASE } = useAuth();
  const API_PREFIX = API_BASE || '';

  const [configured, setConfigured] = useState(null);
  const [numbers, setNumbers]     = useState([]);
  const [groups, setGroups]       = useState([]);
  const [numberInput, setNumberInput] = useState('');
  const [groupInput, setGroupInput]   = useState('');
  const [testMsg, setTestMsg]   = useState('');
  const [dropAlerts, setDropAlerts] = useState(false);
  const [busy,    setBusy]      = useState(false);
  const [msg,     setMsg]       = useState('');
  const [msgType, setMsgType]   = useState('ok');

  const show = (text, type = 'ok') => { setMsgType(type); setMsg(text); };

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API_PREFIX}/whatsapp/status`, { credentials: 'include' });
      const d = r.ok ? await r.json() : {};
      setConfigured(!!d.configured);
      setNumbers(Array.isArray(d.numbers) ? d.numbers : []);
      setGroups(Array.isArray(d.groups) ? d.groups : []);
      setDropAlerts(!!d.drop_alerts_enabled);
    } catch { show(t('common.networkError'), 'err'); }
  }, [API_PREFIX, t]);

  useEffect(() => { load(); }, [load]);

  const normalizePhone = (raw) => {
    let v = raw.replace(/\s/g, '').replace(/\+/g, '');
    if (!v) return null;
    if (!v.includes('@')) v = `${v}@c.us`;
    if (!/^\d+@c\.us$/i.test(v)) {
      show(t('admin.whatsapp.invalidPhone'), 'err');
      return null;
    }
    return v.toLowerCase();
  };

  const normalizeGroup = (raw) => {
    let v = raw.replace(/\s/g, '');
    if (!v) return null;
    if (!v.includes('@')) v = `${v}@g.us`;
    if (!/^\d+@g\.us$/i.test(v)) {
      show(t('admin.whatsapp.invalidGroup'), 'err');
      return null;
    }
    return v.toLowerCase();
  };

  const addNumber = () => {
    const v = normalizePhone(numberInput);
    if (!v) return;
    if (numbers.includes(v)) {
      show(t('admin.whatsapp.alreadyAdded'), 'err');
      return;
    }
    setNumbers([...numbers, v]);
    setNumberInput('');
    setMsg('');
  };

  const removeNumber = (idx) => {
    setNumbers(numbers.filter((_, i) => i !== idx));
  };

  const addGroup = () => {
    const v = normalizeGroup(groupInput);
    if (!v) return;
    if (groups.includes(v)) {
      show(t('admin.whatsapp.alreadyAddedGroup'), 'err');
      return;
    }
    setGroups([...groups, v]);
    setGroupInput('');
    setMsg('');
  };

  const removeGroup = (idx) => {
    setGroups(groups.filter((_, i) => i !== idx));
  };

  const saveRecipients = async () => {
    setBusy(true); setMsg('');
    try {
      const [nr, gr] = await Promise.all([
        fetch(`${API_PREFIX}/whatsapp/numbers`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ numbers }),
        }),
        fetch(`${API_PREFIX}/whatsapp/groups`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ groups }),
        }),
      ]);
      const nd = await nr.json().catch(() => ({}));
      const gd = await gr.json().catch(() => ({}));
      if (nr.ok && gr.ok) {
        setNumbers(nd.numbers || numbers);
        setGroups(gd.groups || groups);
        show(t('admin.whatsapp.saved'), 'ok');
      } else {
        show(nd.detail || gd.detail || t('admin.whatsapp.saveFailed'), 'err');
      }
    } catch { show(t('common.networkError'), 'err'); }
    finally { setBusy(false); }
  };

  const sendTest = async () => {
    if (!numbers.length && !groups.length) { show(t('admin.whatsapp.noRecipients'), 'err'); return; }
    setBusy(true); setMsg('');
    try {
      const r = await fetch(`${API_PREFIX}/whatsapp/test`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ numbers, groups, message: testMsg.trim() || undefined }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        const failures = Array.isArray(d.results)
          ? d.results.filter(item => !item.ok).map(item => `${item.phone || item.chat_id}: ${item.detail || 'failed'}`)
          : [];
        if (failures.length) {
          show(`${t('admin.whatsapp.testSent', { sent: d.sent, total: d.total })}\n${failures.join('\n')}`, 'err');
        } else {
          show(t('admin.whatsapp.testSent', { sent: d.sent, total: d.total }), 'ok');
        }
      } else {
        show(d.detail || t('admin.whatsapp.testFailed'), 'err');
      }
    } catch { show(t('common.networkError'), 'err'); }
    finally { setBusy(false); }
  };

  const toggleDropAlerts = async () => {
    const next = !dropAlerts;
    setBusy(true); setMsg('');
    try {
      const r = await fetch(`${API_PREFIX}/whatsapp/drop_alerts`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok) {
        setDropAlerts(!!d.enabled);
        show(`Drop alerts ${d.enabled ? 'enabled' : 'disabled'}`, 'ok');
      } else {
        show(d.detail || 'Failed to toggle drop alerts', 'err');
      }
    } catch { show(t('common.networkError'), 'err'); }
    finally { setBusy(false); }
  };

  const testRandomDropAlert = async () => {
    setBusy(true); setMsg('');
    try {
      const r = await fetch(`${API_PREFIX}/whatsapp/test_drop_alert`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        show('Test drop alert sent', 'ok');
      } else {
        show(d.detail || 'No sustained drops found to test', 'err');
      }
    } catch { show(t('common.networkError'), 'err'); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <Msg text={msg} type={msgType} />
      <div style={{
        background: 'rgba(37,211,102,0.05)', border: '1px solid rgba(37,211,102,0.2)',
        borderRadius: 12, padding: '20px 22px', marginBottom: 16,
        display: 'flex', alignItems: 'center', gap: 16,
      }}>
        <div style={{
          width: 44, height: 44, borderRadius: '50%',
          background: configured ? 'rgba(37,211,102,0.15)' : 'rgba(255,87,34,0.1)',
          border: `2px solid ${configured ? '#25D366' : RED}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 22, transition: 'all 0.3s',
          boxShadow: configured ? '0 0 18px rgba(37,211,102,0.3)' : 'none',
        }}>📱</div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#fff', marginBottom: 3 }}>{t('admin.whatsapp.title')}</div>
          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', fontFamily: MONO }}>
            {configured === null ? t('admin.whatsapp.loading')
              : configured
                ? <span style={{ color: '#25D366' }}>{t('admin.whatsapp.configured')}</span>
                : <span style={{ color: RED }}>{t('admin.whatsapp.notConfigured')}</span>}
          </div>
        </div>
      </div>

      <div style={{ marginBottom: 18, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Btn onClick={toggleDropAlerts} disabled={busy || configured !== true} small>
          {dropAlerts ? 'Disable drop alerts' : 'Enable drop alerts'}
        </Btn>
        <Btn onClick={testRandomDropAlert} disabled={busy || configured !== true} small>
          Test drop alert
        </Btn>
        <span style={{
          fontSize: 11,
          color: dropAlerts ? '#25D366' : 'rgba(255,255,255,0.35)',
          fontFamily: SYS,
          fontWeight: dropAlerts ? 700 : 400,
        }}>
          {configured !== true ? 'WhatsApp not configured'
            : dropAlerts ? 'Auto alerts ON'
            : 'Auto alerts OFF'}
        </span>
      </div>

      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', marginBottom: 10, fontFamily: SYS }}>
          {t('admin.whatsapp.recipients')}
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <input
            value={numberInput}
            onChange={e => setNumberInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addNumber(); }}
            placeholder={t('admin.whatsapp.phonePlaceholder')}
            disabled={busy}
            style={{
              flex: 1,
              background: 'rgba(0,229,255,0.04)', border: `1px solid ${msgType === 'err' && numberInput ? RED : 'rgba(0,229,255,0.2)'}`,
              borderRadius: 8, padding: '10px 12px', color: '#fff', fontSize: 13, fontFamily: MONO,
              outline: 'none',
            }}
          />
          <Btn onClick={addNumber} disabled={busy || !numberInput.trim()} small>{t('admin.whatsapp.add')}</Btn>
        </div>

        {numbers.length === 0 && (
          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: MONO, marginBottom: 10 }}>
            {t('admin.whatsapp.noRecipients')}
          </div>
        )}
        <RecipientChips items={numbers} onRemove={removeNumber} color={CYAN} disabled={busy} />
      </div>

      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', marginBottom: 10, fontFamily: SYS }}>
          {t('admin.whatsapp.groups')}
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <input
            value={groupInput}
            onChange={e => setGroupInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addGroup(); }}
            placeholder={t('admin.whatsapp.groupPlaceholder')}
            disabled={busy}
            style={{
              flex: 1,
              background: 'rgba(105,255,71,0.04)', border: `1px solid ${msgType === 'err' && groupInput ? RED : 'rgba(105,255,71,0.2)'}`,
              borderRadius: 8, padding: '10px 12px', color: '#fff', fontSize: 13, fontFamily: MONO,
              outline: 'none',
            }}
          />
          <Btn onClick={addGroup} disabled={busy || !groupInput.trim()} small>{t('admin.whatsapp.add')}</Btn>
        </div>

        {groups.length === 0 && (
          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: MONO, marginBottom: 10 }}>
            {t('admin.whatsapp.noGroups')}
          </div>
        )}
        <RecipientChips items={groups} onRemove={removeGroup} color={GREEN} disabled={busy} />

        <Btn onClick={saveRecipients} disabled={busy}>{busy ? t('common.saving') : t('common.save')}</Btn>
      </div>

      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', marginBottom: 10, fontFamily: SYS }}>
          {t('admin.whatsapp.testMessage')}
        </div>
        <textarea
          value={testMsg}
          onChange={e => setTestMsg(e.target.value)}
          placeholder={t('admin.whatsapp.messagePlaceholder')}
          disabled={busy}
          rows={3}
          style={{
            width: '100%', resize: 'vertical',
            background: 'rgba(0,229,255,0.04)', border: '1px solid rgba(0,229,255,0.2)',
            borderRadius: 8, padding: 10, color: '#fff', fontSize: 12, fontFamily: SYS,
            boxSizing: 'border-box', outline: 'none', marginBottom: 10,
          }}
        />
        <Btn onClick={sendTest} disabled={busy || (!numbers.length && !groups.length)}>
          {busy ? t('admin.whatsapp.sending') : t('admin.whatsapp.sendTest')}
        </Btn>
      </div>

      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: MONO, lineHeight: 1.6, marginTop: 14 }}>
        {t('admin.whatsapp.description')}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 7 — Backup
// ---------------------------------------------------------------------------
function BackupTab() {
  const { t } = useTranslation();
  const [status,  setStatus]  = useState(null);
  const [busy,    setBusy]    = useState(false);
  const [msg,     setMsg]     = useState('');
  const [msgType, setMsgType] = useState('ok');

  const fmt = iso => iso ? new Date(iso).toLocaleString() : '—';

  const loadStatus = async () => {
    try {
      const r = await fetch('/api/telemetry', { credentials: 'include' });
      if (r.ok) { const d = await r.json(); setStatus(d); }
    } catch {}
  };

  useEffect(() => { loadStatus(); }, []);

  const runNow = async () => {
    if (busy) return;
    setBusy(true); setMsg('');
    try {
      const r = await fetch('/api/backup/run', { method: 'POST', credentials: 'include' });
      const d = await r.json();
      if (r.ok) {
        setMsgType('ok');
        setMsg(d.status === 'already_running' ? t('admin.backup.alreadyRunning') : t('admin.backup.started'));
        setTimeout(loadStatus, 3000);
      } else {
        setMsgType('err'); setMsg(d.detail || t('admin.backup.failed'));
      }
    } catch { setMsgType('err'); setMsg(t('common.networkError')); }
    finally  { setBusy(false); }
  };

  const running = status?.backup_running;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Msg text={msg} type={msgType} />

      {/* Status card */}
      <div style={{
        background: 'rgba(0,229,255,0.04)', border: '1px solid rgba(0,229,255,0.12)',
        borderRadius: 10, padding: '14px 16px',
      }}>
        {status ? (
          <>
            <Row label={t('admin.backup.lastLocal')}  value={fmt(status.backup_last_local)} />
            <Row label={t('admin.backup.lastR2')}      value={fmt(status.backup_last_r2)} />
            <Row label={t('admin.backup.localSize')}          value={status.backup_local_mb != null ? `${status.backup_local_mb} MB` : t('admin.users.noRole')} />
            <Row label={t('admin.backup.r2Size')}             value={status.backup_r2_mb    != null ? `${status.backup_r2_mb} MB`    : t('admin.users.noRole')} />
            <Row label={t('admin.backup.r2Configured')}       value={status.r2_configured ? t('common.yes') : t('common.no')} dim={!status.r2_configured} />
            {status.backup_error && (
              <div style={{
                marginTop: 8, padding: '7px 10px', borderRadius: 7, fontSize: 11,
                background: 'rgba(255,87,34,0.08)', border: '1px solid rgba(255,87,34,0.25)',
                color: '#ff7043', fontFamily: MONO,
              }}>
                {status.backup_error}
              </div>
            )}
          </>
        ) : (
          <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', fontFamily: MONO }}>{t('common.loading')}</div>
        )}
      </div>

      {/* Run now */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button
          onClick={runNow}
          disabled={busy || running}
          style={{
            padding: '11px 26px', borderRadius: 8, fontSize: 13, fontWeight: 700,
            fontFamily: SYS, letterSpacing: '0.05em',
            cursor: busy || running ? 'not-allowed' : 'pointer',
            opacity: busy || running ? 0.6 : 1,
            background: 'rgba(0,229,255,0.08)',
            border: '1px solid rgba(0,229,255,0.35)',
            color: CYAN, transition: 'all 0.2s',
          }}
        >
          {running ? t('admin.backup.running') : busy ? t('admin.backup.starting') : t('admin.backup.runNow')}
        </button>
        {running && (
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', fontFamily: MONO }}>{t('admin.backup.inProgress')}</span>
        )}
      </div>

      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)', fontFamily: MONO, lineHeight: 1.6 }}>
        {t('admin.backup.description')} {' '}
        <span style={{ color: CYAN }}>{t('admin.backup.auto')}</span> {t('admin.backup.autoSuffix')}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 8 — Map Dots
// ---------------------------------------------------------------------------
function MapDotsTab({ showMapPainter, setShowMapPainter }) {
  const { t } = useTranslation();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 560 }}>
      <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.3)', lineHeight: 1.6 }}>
        {t('admin.mapDots.description')}
      </div>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(0,229,255,0.12)',
        borderRadius: 12, padding: '16px 20px',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#fff' }}>{t('admin.mapDots.toolbar')}</span>
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', fontFamily: MONO }}>
            {t('admin.mapDots.toolbarDesc')}
          </span>
        </div>
        <button
          onClick={() => setShowMapPainter(v => !v)}
          style={{
            padding: '8px 22px', borderRadius: 8, fontSize: 12, fontWeight: 700,
            fontFamily: MONO, cursor: 'pointer', transition: 'all 0.15s',
            background: showMapPainter ? 'rgba(0,229,255,0.14)' : 'rgba(255,255,255,0.06)',
            border: `1px solid ${showMapPainter ? 'rgba(0,229,255,0.45)' : 'rgba(255,255,255,0.15)'}`,
            color: showMapPainter ? CYAN : 'rgba(255,255,255,0.45)',
          }}
        >
          {showMapPainter ? t('common.on') : t('common.off')}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 9 — Broadcast
// ---------------------------------------------------------------------------
function BroadcastTab() {
  const { t } = useTranslation();
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [msg, setMsg]         = useState(null);
  const [room, setRoom]       = useState('global');
  const [clearing, setClearing] = useState(false);

  const send = async () => {
    if (!message.trim() || sending) return;
    setSending(true);
    setMsg(null);
    try {
      const res = await fetch(`${API}/api/admin/broadcast`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message.trim(), room }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || t('chat.broadcastFailed'));
      }
      setMsg({ type: 'ok', text: t('chat.broadcast', { room }) });
      setMessage('');
    } catch (e) {
      setMsg({ type: 'err', text: e.message });
    } finally {
      setSending(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 560 }}>
      <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.3)', lineHeight: 1.6 }}>
        {t('admin.broadcast.description')}
      </div>

      {/* Room selector */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.28)' }}>
          {t('admin.broadcast.targetRoom')}
        </span>
        <div style={{ display: 'flex', gap: 8 }}>
          {['global', 'system'].map(r => (
            <button
              key={r}
              onClick={() => setRoom(r)}
              style={{
                padding: '6px 14px',
                background: room === r ? 'rgba(0,229,255,0.12)' : 'rgba(255,255,255,0.04)',
                border: `1px solid ${room === r ? 'rgba(0,229,255,0.40)' : 'rgba(255,255,255,0.10)'}`,
                borderRadius: 8, color: room === r ? CYAN : 'rgba(255,255,255,0.45)',
                fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: MONO,
                transition: 'all 0.15s',
              }}
            >
              #{r}
            </button>
          ))}
        </div>
      </div>

      {/* Message */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.28)' }}>
          {t('admin.broadcast.broadcastMessage')}
        </span>
        <textarea
          value={message}
          onChange={e => setMessage(e.target.value)}
          maxLength={2000}
          rows={5}
          placeholder={t('admin.broadcast.placeholder')}
          style={{
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(0,229,255,0.15)',
            borderRadius: 10, color: '#fff', padding: '12px 14px',
            fontSize: 13, fontFamily: MONO, resize: 'vertical',
            outline: 'none', colorScheme: 'dark', lineHeight: 1.5,
            transition: 'border-color 0.15s',
          }}
          onFocus={e => e.target.style.borderColor = 'rgba(0,229,255,0.40)'}
          onBlur={e => e.target.style.borderColor = 'rgba(0,229,255,0.15)'}
        />
        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', textAlign: 'right', fontFamily: MONO }}>
          {message.length}/2000
        </div>
      </div>

      {msg && <Msg text={msg.text} type={msg.type} />}

      <Btn onClick={send} disabled={sending || !message.trim()}>
        {sending ? t('admin.broadcast.sending') : t('admin.broadcast.send')}
      </Btn>

      {/* ── Danger zone ── */}
      <div style={{ borderTop: '1px solid rgba(255,87,34,0.15)', paddingTop: 20, marginTop: 4, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', textTransform: 'uppercase', color: 'rgba(255,87,34,0.5)' }}>
          {t('admin.broadcast.dangerZone')}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'rgba(255,87,34,0.04)', border: '1px solid rgba(255,87,34,0.15)', borderRadius: 10, padding: '14px 16px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: '#fff' }}>{t('admin.broadcast.clearGlobal')}</span>
            <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.28)', fontFamily: MONO }}>{t('admin.broadcast.clearDesc')}</span>
          </div>
          <button
            disabled={clearing}
            onClick={async () => {
              if (!window.confirm(t('chat.clearConfirm'))) return;
              setClearing(true);
              setMsg(null);
              try {
                const res = await fetch(`${API}/api/chat/clear?room=global`, { method: 'DELETE', credentials: 'include' });
                if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || t('common.failed'));
                setMsg({ type: 'ok', text: t('chat.cleared') });
              } catch (e) {
                setMsg({ type: 'err', text: e.message });
              } finally {
                setClearing(false);
              }
            }}
            style={{
              flexShrink: 0, padding: '7px 16px', borderRadius: 8, fontSize: 12, fontWeight: 700,
              fontFamily: MONO, cursor: clearing ? 'not-allowed' : 'pointer', transition: 'all 0.15s',
              background: 'rgba(255,87,34,0.12)', border: '1px solid rgba(255,87,34,0.35)',
              color: RED, opacity: clearing ? 0.5 : 1,
            }}
          >
            {clearing ? t('admin.broadcast.clearing') : t('admin.broadcast.clear')}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main AdminPanel component
// ---------------------------------------------------------------------------
export default function AdminPanel({ tab = 0, showMapPainter, setShowMapPainter }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const TABS = getTabs(t);

  return (
    <div style={{
      position: 'absolute', inset: 0,
      fontFamily: SYS, pointerEvents: 'auto',
      overflowY: 'auto',
      padding: '28px 32px',
      background: 'linear-gradient(180deg, rgba(8,11,18,0.0) 0%, rgba(8,11,18,0.0) 100%)',
    }}>
      <div style={{ maxWidth: 820, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 0 }}>
        {/* Header */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 10, letterSpacing: '0.2em', color: 'rgba(0,229,255,0.5)', textTransform: 'uppercase', marginBottom: 4 }}>
            {t('admin.adminTitle')}
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#fff', letterSpacing: '-0.01em' }}>
            {TABS[tab] ?? t('admin.controlPanel')}
          </div>
        </div>

        {/* Tab content */}
        <div style={{ minHeight: 0 }}>
          {tab === 0 && <SessionTab />}
          {tab === 1 && <UsersTab />}
          {tab === 2 && <RoleMatrixTab />}
          {tab === 3 && <AuditTab />}
          {tab === 4 && <ActivityTab />}
          {tab === 5 && <WhatsAppTab />}
          {tab === 6 && <BackupTab />}
          {tab === 7 && <BroadcastTab />}
          {tab === 8 && <MapDotsTab showMapPainter={showMapPainter} setShowMapPainter={setShowMapPainter} />}
        </div>
      </div>
    </div>
  );
}
