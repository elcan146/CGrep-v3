import React, { useState, useEffect, useRef, useCallback, memo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAuth } from './auth/AuthContext';
import { useTranslation } from './i18n/LanguageContext';
import { Plus, Download, X, Play, FileIcon } from 'lucide-react';
import aibotImg from '../public/aibot.png';
import groupchatImg from '../public/groupchat.png';

const CYAN   = '#00e5ff';
const PURPLE = '#7c3aed';
const AMBER  = '#ffaf00';
const SYS    = `'Space Grotesk', sans-serif`;
const MONO   = `'JetBrains Mono', monospace`;

const API_BASE = window.location.port === '5173' ? 'http://127.0.0.1:8000' : '';

function lastSeenText(isoStr, t) {
  if (!isoStr) return null;
  const diff = Math.floor((Date.now() - new Date(isoStr + (isoStr.endsWith('Z') ? '' : 'Z'))) / 1000);
  if (diff < 60)  return t('chat.lastSeen.justNow');
  if (diff < 3600) return t('chat.lastSeen.minutes', { n: Math.floor(diff / 60) });
  if (diff < 86400) return t('chat.lastSeen.hours', { n: Math.floor(diff / 3600) });
  return t('chat.lastSeen.days', { n: Math.floor(diff / 86400) });
}

const WS_BASE  = window.location.port === '5173'
  ? 'ws://127.0.0.1:8000'
  : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;

const PANEL_W_COLLAPSED = 90;
const RAIL_W_EXPANDED   = 200;
const PANEL_W_EXPANDED  = 200 + 400;
const SYSTEM_ROOM = 'system';
const GLOBAL_ROOM = 'global';

function dmRoomName(uid1, uid2) {
  const [a, b] = [String(uid1), String(uid2)].sort();
  return `dm_${a}_${b}`;
}
function isDmRoom(room) { return room && room.startsWith('dm_'); }

// ── Inject minimal CSS for bell animation & scrollbar ──────────────────────
const _css = `
  @keyframes rp-bell {
    0%,100% { opacity:1; filter:drop-shadow(0 0 0px #00e5ff); }
    50%      { opacity:0.3; filter:drop-shadow(0 0 8px #00e5ff); }
  }
  @keyframes rp-dot {
    0%,80%,100% { transform: scale(0.6); opacity:0.4; }
    40%          { transform: scale(1);   opacity:1;   }
  }
  .rp-bell-flash { animation: rp-bell 0.8s ease-in-out infinite; }
  .rp-msglist::-webkit-scrollbar { width: 3px; }
  .rp-msglist::-webkit-scrollbar-track { background: transparent; }
  .rp-msglist::-webkit-scrollbar-thumb { background: rgba(0,229,255,0.25); border-radius: 2px; }
  .rp-msglist { scrollbar-width: thin; scrollbar-color: rgba(0,229,255,0.25) transparent; }
`;
if (typeof document !== 'undefined' && !document.getElementById('rp-css2')) {
  const s = document.createElement('style');
  s.id = 'rp-css2'; s.textContent = _css;
  document.head.appendChild(s);
}

// ── Avatar helper ──────────────────────────────────────────────────────────
function UserAvatar({ user_id, display_name, username, avatar_url, size = 28 }) {
  const label  = (display_name || username || '?')[0].toUpperCase();
  const colors = ['#00e5ff','#7c3aed','#ffaf00','#ff5722','#00e676','#e91e8c'];
  const color  = colors[((user_id || username || '?').charCodeAt(0)) % colors.length];
  const src    = avatar_url
    ? (avatar_url.startsWith('http') ? avatar_url : `${API_BASE}${avatar_url}`)
    : null;

  return (
    <div style={{
      width: size, height: size, borderRadius: '50%',
      overflow: 'hidden', flexShrink: 0, position: 'relative',
      border: '1px solid rgba(255,255,255,0.10)',
      background: `${color}22`,
    }}>
      {src ? (
        <img
          src={src}
          alt={label}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      ) : (
        <div style={{
          width: '100%', height: '100%',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: size * 0.42, fontWeight: 700, color,
        }}>
          {label}
        </div>
      )}
    </div>
  );
}

// ── File content parser ────────────────────────────────────────────────────
function parseFileContent(content) {
  try {
    const parsed = JSON.parse(content);
    if (parsed && parsed.type === 'file') return parsed;
  } catch { /* not JSON */ }
  return null;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── File attachment renderers ──────────────────────────────────────────────
function FileAttachment({ fileData, onImageClick, onVideoClick }) {
  const { file_type, file_url, file_name, file_size } = fileData;
  const fullUrl = file_url.startsWith('http') ? file_url : `${API_BASE}${file_url}`;

  if (file_type === 'image') {
    return (
      <img
        src={fullUrl}
        alt={file_name}
        onClick={() => onImageClick(fullUrl)}
        style={{
          maxWidth: '100%', maxHeight: 220, borderRadius: 8,
          cursor: 'pointer', objectFit: 'contain', display: 'block',
          marginTop: 4,
        }}
      />
    );
  }

  if (file_type === 'video') {
    return (
      <div
        onClick={() => onVideoClick(fullUrl)}
        style={{
          position: 'relative', maxWidth: '100%', maxHeight: 200,
          borderRadius: 8, overflow: 'hidden', cursor: 'pointer',
          background: '#000', display: 'inline-block', marginTop: 4,
        }}
      >
        <video
          src={fullUrl}
          style={{ maxWidth: '100%', maxHeight: 200, display: 'block' }}
          preload="metadata"
          muted
        />
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'rgba(0,0,0,0.4)',
        }}>
          <div style={{
            width: 44, height: 44, borderRadius: '50%',
            background: 'rgba(255,255,255,0.9)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Play size={20} color="#000" fill="#000" style={{ marginLeft: 2 }} />
          </div>
        </div>
      </div>
    );
  }

  // Generic file — download card
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.10)',
      borderRadius: 8, padding: '8px 12px', marginTop: 4, maxWidth: 260,
    }}>
      <FileIcon size={18} color="rgba(255,255,255,0.5)" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: '#fff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {file_name}
        </div>
        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)' }}>
          {formatFileSize(file_size)}
        </div>
      </div>
      <a
        href={fullUrl}
        download={file_name}
        onClick={e => e.stopPropagation()}
        style={{ color: 'rgba(255,255,255,0.5)', display: 'flex', alignItems: 'center', cursor: 'pointer' }}
      >
        <Download size={16} />
      </a>
    </div>
  );
}

// ── Lightbox overlay ──────────────────────────────────────────────────────
function Lightbox({ type, src, onClose }) {
  if (!src) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 99999,
        background: 'rgba(0,0,0,0.85)', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer',
      }}
    >
      <button
        onClick={onClose}
        style={{
          position: 'absolute', top: 16, right: 16,
          background: 'rgba(255,255,255,0.1)', border: 'none',
          borderRadius: '50%', width: 36, height: 36, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      >
        <X size={20} color="#fff" />
      </button>
      {type === 'image' ? (
        <img
          src={src}
          onClick={e => e.stopPropagation()}
          style={{ maxWidth: '90vw', maxHeight: '90vh', borderRadius: 8, objectFit: 'contain' }}
          alt=""
        />
      ) : (
        <video
          src={src}
          onClick={e => e.stopPropagation()}
          controls
          autoPlay
          style={{ maxWidth: '90vw', maxHeight: '90vh', borderRadius: 8 }}
        />
      )}
    </div>
  );
}

// ── Discord-style message renderer ─────────────────────────────────────────
const MessageBubble = memo(function MessageBubble({ msg, isOwn, onImageClick, onVideoClick }) {
  const { t } = useTranslation();
  const label = msg.display_name || msg.username || t('chat.unknown');
  const ts    = new Date(msg.timestamp).toLocaleTimeString('az', { hour: '2-digit', minute: '2-digit', hour12: false });
  const fileData = parseFileContent(msg.content);

  const isAi = msg.sender_type === 'ai' || msg.sender_type === 'system';
  const nameColor = isAi ? '#b39ddb' : isOwn ? CYAN : 'rgba(255,255,255,0.85)';
  const align = isOwn ? 'flex-end' : 'flex-start';

  return (
    <div style={{
      display: 'flex', gap: isOwn ? 10 : 12, padding: '4px 16px',
      flexDirection: isOwn ? 'row-reverse' : 'row',
      transition: 'background 0.1s',
    }}
      onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.025)'; }}
      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
    >
      {/* Avatar */}
      <div style={{ flexShrink: 0, paddingTop: 2 }}>
        {isAi ? (
          <div style={{
            width: 36, height: 36, borderRadius: '50%', overflow: 'hidden', flexShrink: 0,
            border: '1px solid rgba(124,58,237,0.35)',
          }}>
            <img src={aibotImg} alt={t('chat.grepbot')} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          </div>
        ) : (
          <UserAvatar user_id={msg.user_id} display_name={msg.display_name} username={msg.username} avatar_url={msg.avatar_url} size={36} />
        )}
      </div>
      {/* Content */}
      <div style={{ flex: 1, minWidth: 0, maxWidth: '75%' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 3, justifyContent: isOwn ? 'flex-end' : 'flex-start' }}>
          {isOwn ? (
            <>
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)', fontFamily: MONO }}>{ts}</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: nameColor, fontFamily: SYS }}>{label}</span>
            </>
          ) : (
            <>
              <span style={{ fontSize: 13, fontWeight: 700, color: nameColor, fontFamily: SYS }}>{isAi ? t('chat.grepbot') : label}</span>
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)', fontFamily: MONO }}>{ts}</span>
            </>
          )}
        </div>
        <div style={{ textAlign: isOwn ? 'right' : 'left' }}>
          {fileData ? (
            <FileAttachment fileData={fileData} onImageClick={onImageClick} onVideoClick={onVideoClick} />
          ) : (
            <div style={{
              fontSize: 13, color: 'rgba(255,255,255,0.85)', lineHeight: 1.55,
              fontFamily: SYS, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {msg.content}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

// ── AI typing indicator ───────────────────────────────────────────────────
function AiTyping() {
  const { t } = useTranslation();
  return (
    <div style={{ display: 'flex', gap: 12, padding: '4px 16px', alignItems: 'flex-start' }}>
      <div style={{
        width: 36, height: 36, borderRadius: '50%', flexShrink: 0, overflow: 'hidden',
        border: '1px solid rgba(124,58,237,0.35)',
      }}>
        <img src={aibotImg} alt={t('chat.grepbot')} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </div>
      <div style={{ paddingTop: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: '#b39ddb', fontFamily: SYS }}>{t('chat.grepbot')}</span>
        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
          {[0,1,2].map(i => (
            <div key={i} style={{
              width: 7, height: 7, borderRadius: '50%',
              background: 'rgba(124,58,237,0.6)',
              animation: `rp-dot 1.2s ${i * 0.2}s infinite ease-in-out`,
            }} />
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Conv sidebar item ─────────────────────────────────────────────────────
function ConvItem({ Icon, name, preview, active, onClick, accent }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', flexDirection: 'column', gap: 3,
        padding: '9px 14px', textAlign: 'left', width: '100%',
        background: active ? `${accent}14` : 'transparent',
        border: 'none', cursor: 'pointer',
        borderLeft: `2px solid ${active ? accent : 'transparent'}`,
        transition: 'background 0.15s, border-color 0.15s',
      }}
      onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; }}
      onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon size={14} color={active ? accent : 'rgba(255,255,255,0.4)'} strokeWidth={2} />
        <span style={{ fontSize: 12, fontWeight: 600, color: active ? '#fff' : 'rgba(255,255,255,0.55)', fontFamily: SYS, letterSpacing: '-0.01em' }}>
          {name}
        </span>
      </div>
      {preview && (
        <div style={{
          fontSize: 10, color: 'rgba(255,255,255,0.26)', paddingLeft: 22,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 156,
        }}>
          {preview}
        </div>
      )}
    </button>
  );
}

// ── Collapsed sidebar tile — circular avatar with glow ring ──────────────
function SidebarTile({ src, fallback, fallbackColor = CYAN, label, sublabel, active, accent, onClick, online, ringOverride, showLabel, showNotification = false }) {
  const ringColor = ringOverride
    ? ringOverride
    : online ? '#00e676' : 'rgba(255,255,255,0.16)';
  const boxShadow = ringOverride
    ? `0 0 10px 3px ${ringOverride}99`
    : online ? '0 0 10px 3px rgba(0,230,118,0.60)' : 'none';

  return (
    <div
      onClick={onClick}
      title={label}
      style={{
        display: 'flex', alignItems: 'center',
        justifyContent: showLabel ? 'flex-start' : 'center',
        padding: showLabel ? '7px 12px' : '7px 0',
        gap: showLabel ? 10 : 0,
        flexShrink: 0, width: '100%',
        cursor: onClick ? 'pointer' : 'default',
        transition: 'opacity 0.15s, background 0.15s',
        background: active ? `${accent || ringColor}10` : 'transparent',
        borderLeft: active ? `2px solid ${accent || ringColor}` : '2px solid transparent',
      }}
      onMouseEnter={e => { if (onClick) e.currentTarget.style.opacity = '0.78'; }}
      onMouseLeave={e => { e.currentTarget.style.opacity = '1'; }}
    >
      <div style={{ position: 'relative', width: 58, height: 58, flexShrink: 0 }}>
        <div style={{
          width: '100%', height: '100%', borderRadius: '50%',
          overflow: 'hidden',
          border: `6px solid ${ringColor}`,
          boxShadow,
          transition: 'border-color 0.3s, box-shadow 0.3s',
        }}>
          {src ? (
            <img src={src} alt={label} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
          ) : (
            <div style={{
              width: '100%', height: '100%',
              background: fallbackColor ? `${fallbackColor}22` : 'rgba(255,255,255,0.07)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 20, fontWeight: 700, color: fallbackColor, fontFamily: SYS,
            }}>
              {fallback}
            </div>
          )}
        </div>
        {showNotification && (
          <div style={{
            position: 'absolute', bottom: -4, right: -2,
            width: 14, height: 14, borderRadius: '50%',
            background: '#ff1744', border: '2px solid #080b12',
            boxShadow: '0 0 6px rgba(255,23,68,0.6)', zIndex: 2,
          }} />
        )}
      </div>
      {showLabel && (
        <div style={{ minWidth: 0, overflow: 'hidden' }}>
          <div style={{
            fontSize: 13, fontWeight: 600, color: active ? '#fff' : 'rgba(255,255,255,0.75)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            fontFamily: SYS,
          }}>
            {label}
          </div>
          {sublabel && (
            <div style={{
              fontSize: 10, color: online ? '#00e676' : 'rgba(255,255,255,0.30)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              fontFamily: SYS, marginTop: 1,
            }}>
              {sublabel}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Chat input area ──────────────────────────────────────────────────────
function ChatInputArea({ activeRoom, aiLoading, onSend, onFileUpload, dmUserName }) {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const inputRef = useRef(null);
  const fileRef = useRef(null);

  const send = () => {
    const text = input.trim();
    if (!text || (activeRoom === SYSTEM_ROOM && aiLoading)) return;
    onSend(text);
    setInput('');
  };

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const handleFileChange = (e) => {
    const file = e.target.files?.[0];
    if (file && onFileUpload) onFileUpload(file);
    e.target.value = '';
  };

  const showPlus = activeRoom !== SYSTEM_ROOM;

  return (
    <div style={{
      padding: '8px 10px', borderTop: '1px solid rgba(255,255,255,0.06)',
      flexShrink: 0,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 8, padding: '8px 12px',
      }}>
        {showPlus && (
          <button
            onClick={() => fileRef.current?.click()}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'rgba(255,255,255,0.4)', display: 'flex', alignItems: 'center',
              padding: 0, flexShrink: 0,
            }}
          >
            <Plus size={18} strokeWidth={2} />
          </button>
        )}
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder={activeRoom === SYSTEM_ROOM ? t('chat.placeholder.ai') : isDmRoom(activeRoom) ? t('chat.placeholder.dm', { name: dmUserName || t('chat.user') }) : t('chat.placeholder.global')}
          style={{
            flex: 1, background: 'none', border: 'none', outline: 'none',
            color: '#fff', fontSize: 13, fontFamily: SYS,
            lineHeight: 1.4, minWidth: 0,
          }}
        />
      </div>
      {showPlus && (
        <input
          ref={fileRef}
          type="file"
          style={{ display: 'none' }}
          accept=".png,.jpg,.jpeg,.gif,.webp,.mp4,.mov,.mp3,.wav,.m4a,.ogg,.txt,.md,.rtf,.pdf,.doc,.docx,.ppt,.pptx,.zip,.rar,.json,.csv,.xml"
          onChange={handleFileChange}
        />
      )}
    </div>
  );
}

// ── Main RightPanel ───────────────────────────────────────────────────────
export default memo(function RightPanel({ expanded, onExpandedChange }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [localExpanded, setLocalExpanded] = useState(false);
  const isExpanded   = onExpandedChange ? expanded : localExpanded;
  const setExpanded  = onExpandedChange ? onExpandedChange : setLocalExpanded;

  const [activeRoom, setActiveRoom] = useState(SYSTEM_ROOM);
  const systemRoom = user?.id ? `system_${user.id}` : SYSTEM_ROOM;
  const [globalMsgs, setGlobalMsgs] = useState([]);
  const [systemMsgs, setSystemMsgs] = useState([]);
  const [dmMsgs, setDmMsgs] = useState({});       // { room: [msgs] }
  const [onlineUsers, setOnlineUsers] = useState([]);
  const [allUsers, setAllUsers]         = useState([]);
  const [aiLoading, setAiLoading]   = useState(false);
  const [unreadRooms, setUnreadRooms] = useState(new Set());
  useEffect(() => { console.log('[chat] unreadRooms', [...unreadRooms]); }, [unreadRooms]);
  // Clear the dot for the currently open chat whenever the panel is expanded
  useEffect(() => {
    if (isExpanded) {
      setUnreadRooms(prev => { const next = new Set(prev); next.delete(activeRoom); return next; });
    }
  }, [isExpanded, activeRoom]);
  const [queueInfo, setQueueInfo]   = useState({ requestId: null, position: 0, eta: 0, status: '' });
  const [etaLeft, setEtaLeft]       = useState(0);

  useEffect(() => {
    if (queueInfo.requestId && queueInfo.status === 'queued' && queueInfo.eta > 0) {
      setEtaLeft(queueInfo.eta);
      const timer = setInterval(() => setEtaLeft(prev => Math.max(0, prev - 1)), 1000);
      return () => clearInterval(timer);
    } else {
      setEtaLeft(0);
    }
  }, [queueInfo.requestId, queueInfo.eta, queueInfo.status]);

  const mountedRef = useRef(true);
  const lastRequestIdRef = useRef('');

  const addMessageRef = useRef(null);
  const addMessage = useCallback((msg) => {
    if (!mountedRef.current) return;
    console.log('[chat] addMessage', msg, 'activeRoom', activeRoom, 'userId', user?.id);
    const room = msg.room;
    if (room === GLOBAL_ROOM || room === 'global') {
      setGlobalMsgs(prev => prev.find(m => m.id === msg.id) ? prev : [...prev, msg]);
      if ((!isExpanded || activeRoom !== GLOBAL_ROOM) && String(msg.user_id) !== String(user?.id)) {
        console.log('[chat] mark global unread');
        setUnreadRooms(prev => new Set([...prev, GLOBAL_ROOM]));
      }
    } else if (isDmRoom(room)) {
      setDmMsgs(prev => {
        const roomMsgs = prev[room] || [];
        if (roomMsgs.find(m => m.id === msg.id)) return prev;
        return { ...prev, [room]: [...roomMsgs, msg] };
      });
      if ((!isExpanded || activeRoom !== room) && String(msg.user_id) !== String(user?.id)) {
        console.log('[chat] mark dm unread', room);
        setUnreadRooms(prev => new Set([...prev, room]));
      }
    } else {
      setSystemMsgs(prev => prev.find(m => m.id === msg.id) ? prev : [...prev, msg]);
      if ((!isExpanded || activeRoom !== SYSTEM_ROOM) && msg.sender_type !== 'user') {
        console.log('[chat] mark system unread');
        setUnreadRooms(prev => new Set([...prev, SYSTEM_ROOM]));
      }
    }
  }, [activeRoom, user?.id, isExpanded]);
  addMessageRef.current = addMessage;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (!user?.id) return;
    const load = async () => {
      try {
        const [gr, sr, ur] = await Promise.all([
          fetch(`${API_BASE}/api/chat/history?room=global&limit=100`, { credentials: 'include' }),
          fetch(`${API_BASE}/api/chat/history?room=system_${user.id}&limit=100`, { credentials: 'include' }),
          fetch(`${API_BASE}/api/chat/users`, { credentials: 'include' }),
        ]);
        if (gr.ok) { const d = await gr.json(); if (mountedRef.current) setGlobalMsgs(d); }
        if (sr.ok) { const d = await sr.json(); if (mountedRef.current) setSystemMsgs(d); }
        if (ur.ok) { const d = await ur.json(); if (mountedRef.current) setAllUsers(d); }
      } catch { /* ignore */ }
    };
    load();
  }, [user?.id]);

  useEffect(() => {
    let timer = null;
    let ws = null;
    const connect = () => {
      try {
        ws = new WebSocket(`${WS_BASE}/ws/chat`);
        ws.onmessage = (e) => {
          try {
            const d = JSON.parse(e.data);
            console.log('[chat] ws raw', d);
            if (!mountedRef.current) return;
            if (d.type === 'message') addMessageRef.current?.(d);
            else if (d.type === 'queue_update') {
              if (d.request_id === lastRequestIdRef.current) {
                setQueueInfo({ requestId: d.request_id, position: d.position, eta: d.eta_seconds, status: d.status });
              }
            }
            else if (d.type === 'clear_room') {
              if (d.room === 'global') setGlobalMsgs([]);
              if (d.room === systemRoom) setSystemMsgs([]);
            }
            else if (d.type === 'presence') {
              const users = d.users || [];
              setOnlineUsers(users);
              const onlineIds = new Set(users.map(u => u.user_id));
              setAllUsers(prev => prev.map(u => ({ ...u, online: onlineIds.has(u.user_id) })));
              fetch(`${API_BASE}/api/chat/users`, { credentials: 'include' })
                .then(r => r.ok ? r.json() : null)
                .then(d => { if (d && mountedRef.current) setAllUsers(d); })
                .catch(() => {});
            }
          } catch { /* ignore */ }
        };
        ws.onclose = () => { if (mountedRef.current) timer = setTimeout(connect, 3000); };
        ws.onerror = () => ws.close();
        const ping = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
        }, 25000);
        ws._ping = ping;
      } catch { /* ignore */ }
    };
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(timer);
      if (ws) { clearInterval(ws._ping); ws.onclose = null; ws.close(); }
    };
  }, []);

  const sendMessageFromText = useCallback(async (text) => {
    if (!text) return;
    if (activeRoom === SYSTEM_ROOM) {
      if (aiLoading) return;
      const requestId = `q_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      lastRequestIdRef.current = requestId;
      setAiLoading(true);
      setQueueInfo({ requestId, position: null, eta: 0, status: 'queued' });
      try {
        await fetch(`${API_BASE}/api/chat/ai`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: text,
            history: systemMsgs.slice(-10).map(m => ({ sender_type: m.sender_type, content: m.content })),
            room: systemRoom,
            request_id: requestId,
          }),
        });
      } catch { /* ignore */ }
      setQueueInfo({ requestId: null, position: 0, eta: 0, status: '' });
      setAiLoading(false);
    } else if (isDmRoom(activeRoom)) {
      const optimisticId = `opt_${Date.now()}`;
      const optimistic = {
        id: optimisticId, room: activeRoom,
        user_id: user?.id, sender_type: 'user', content: text,
        display_name: user?.display_name, username: user?.username,
        avatar_url: user?.avatar_url, timestamp: new Date().toISOString(),
      };
      setDmMsgs(prev => ({ ...prev, [activeRoom]: [...(prev[activeRoom] || []), optimistic] }));
      try {
        const res = await fetch(`${API_BASE}/api/chat/send`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: text, room: activeRoom }),
        });
        if (res.ok) {
          const real = await res.json();
          setDmMsgs(prev => ({
            ...prev,
            [activeRoom]: (prev[activeRoom] || []).map(m => m.id === optimisticId ? { ...real } : m),
          }));
        }
      } catch { /* ignore */ }
    } else {
      const optimisticId = `opt_${Date.now()}`;
      const optimistic = {
        id: optimisticId, room: GLOBAL_ROOM,
        user_id: user?.id, sender_type: 'user', content: text,
        display_name: user?.display_name, username: user?.username,
        avatar_url: user?.avatar_url, timestamp: new Date().toISOString(),
      };
      setGlobalMsgs(prev => [...prev, optimistic]);
      try {
        const res = await fetch(`${API_BASE}/api/chat/send`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: text, room: GLOBAL_ROOM }),
        });
        if (res.ok) {
          const real = await res.json();
          setGlobalMsgs(prev => prev.map(m => m.id === optimisticId ? { ...real } : m));
        }
      } catch { /* ignore */ }
    }
  }, [activeRoom, aiLoading, systemMsgs, systemRoom, user]);

  const handleNewSession = useCallback(async () => {
    if (!user?.id) return;
    const room = `system_${user.id}`;
    setSystemMsgs([]);
    setAiLoading(false);
    setQueueInfo({ requestId: null, position: 0, eta: 0, status: '' });
    lastRequestIdRef.current = null;
    try {
      const res = await fetch(`${API_BASE}/api/chat/clear?room=${encodeURIComponent(room)}`, {
        method: 'DELETE', credentials: 'include',
      });
      if (!res.ok) console.warn('[chat] clear failed', res.status);
    } catch { /* ignore */ }
  }, [user?.id]);

  const switchRoom = useCallback((room) => {
    setActiveRoom(room);
    setUnreadRooms(prev => { const next = new Set(prev); next.delete(room); return next; });
    // Load DM history if not already loaded
    if (isDmRoom(room) && !dmMsgs[room]) {
      fetch(`${API_BASE}/api/chat/history?room=${encodeURIComponent(room)}&limit=100`, { credentials: 'include' })
        .then(r => r.ok ? r.json() : [])
        .then(msgs => { if (mountedRef.current) setDmMsgs(prev => ({ ...prev, [room]: msgs })); })
        .catch(() => {});
    }
  }, [dmMsgs]);

  const currentMsgs = activeRoom === SYSTEM_ROOM
    ? systemMsgs
    : isDmRoom(activeRoom)
      ? (dmMsgs[activeRoom] || [])
      : globalMsgs;
  const scrollRef = useRef(null);
  const [lightbox, setLightbox] = useState(null); // { type: 'image'|'video', src }

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [currentMsgs.length, aiLoading]);

  const handleFileUpload = useCallback(async (file) => {
    const room = activeRoom === SYSTEM_ROOM ? GLOBAL_ROOM : activeRoom;
    const ext = (file.name || '').split('.').pop().toLowerCase();
    if (ext === 'svg' || file.type === 'image/svg+xml') {
      alert('SVG files are not allowed for security reasons');
      return;
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('room', room);
    try {
      const res = await fetch(`${API_BASE}/api/chat/upload`, {
        method: 'POST', credentials: 'include',
        body: formData,
      });
      if (res.ok) {
        const msg = await res.json();
        if (isDmRoom(room)) {
          setDmMsgs(prev => {
            const msgs = prev[room] || [];
            if (msgs.find(m => m.id === msg.id)) return prev;
            return { ...prev, [room]: [...msgs, msg] };
          });
        } else {
          setGlobalMsgs(prev => prev.find(m => m.id === msg.id) ? prev : [...prev, msg]);
        }
      }
    } catch { /* ignore */ }
  }, [activeRoom]);

  return (
    <motion.div
      style={{
        position: 'absolute', top: 52, right: 0, bottom: 34,
        zIndex: 10, fontFamily: SYS, userSelect: 'none',
        pointerEvents: 'auto', overflow: 'hidden',
        background: 'linear-gradient(180deg, rgba(8,11,18,0.97) 0%, rgba(10,14,22,0.93) 100%)',
        borderLeft: '1px solid rgba(255,255,255,0.05)',
        boxShadow: isExpanded ? '-12px 0 40px rgba(0,0,0,0.5)' : 'none',
        display: 'flex', flexDirection: 'row',
      }}
      initial={{ width: PANEL_W_COLLAPSED }}
      animate={{ width: isExpanded ? PANEL_W_EXPANDED : PANEL_W_COLLAPSED }}
      transition={{ duration: 0.28, ease: 'easeOut' }}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
    >
      {/* ── Chat area (right side, revealed as panel widens) ── */}
      <motion.div
        style={{
          flex: 1, display: 'flex', minWidth: 0, overflow: 'hidden',
          flexDirection: 'row', order: 2,
        }}
        animate={{ opacity: isExpanded ? 1 : 0 }}
        transition={{ duration: 0.18, delay: isExpanded ? 0.10 : 0 }}
      >
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {/* Header */}
          {(() => {
            const isSystem = activeRoom === SYSTEM_ROOM;
            const isGlobal = activeRoom === GLOBAL_ROOM;
            const isDm = isDmRoom(activeRoom);
            let headerAvatar = isSystem ? aibotImg : isGlobal ? groupchatImg : null;
            let headerName  = isSystem ? t('chat.grepbotAI') : isGlobal ? t('chat.globalChat') : '';
            let headerRing  = PURPLE;
            let headerSub = null;
            if (isSystem && aiLoading && queueInfo.requestId) {
              if (queueInfo.status === 'processing') {
                headerSub = <span style={{ color: PURPLE, fontSize: 10, fontFamily: MONO }}>GrepBot is answering...</span>;
              } else if (queueInfo.position != null) {
                const seconds = etaLeft > 0 ? etaLeft : queueInfo.eta;
                headerSub = <span style={{ color: PURPLE, fontSize: 10, fontFamily: MONO }}>{`Queue #${queueInfo.position} · ~${seconds}s`}</span>;
              } else {
                headerSub = <span style={{ color: PURPLE, fontSize: 10, fontFamily: MONO }}>{t('chat.thinking')}</span>;
              }
            }
            if (isDm) {
              const otherUser = allUsers.find(u => activeRoom === dmRoomName(user?.id, u.user_id));
              if (otherUser) {
                headerName = otherUser.display_name || otherUser.username;
                headerAvatar = otherUser.avatar_url
                  ? (otherUser.avatar_url.startsWith('http') ? otherUser.avatar_url : `${API_BASE}${otherUser.avatar_url}`)
                  : null;
                headerRing = otherUser.online ? '#00e676' : 'rgba(255,255,255,0.2)';
                headerSub = otherUser.online
                  ? <span style={{ color: '#00e676' }}>{t('chat.online')}</span>
                  : <span style={{ color: 'rgba(255,255,255,0.38)' }}>{lastSeenText(otherUser.last_seen, t) || t('chat.offline')}</span>;
              } else {
                headerName = t('chat.directMessage');
              }
            }
            return (
              <div style={{
                padding: '10px 14px', borderBottom: '1px solid rgba(255,255,255,0.06)',
                display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
              }}>
                <div style={{
                  width: 42, height: 42, borderRadius: '50%', overflow: 'hidden', flexShrink: 0,
                  border: `3px solid ${headerRing}`,
                  boxShadow: `0 0 10px 2px ${headerRing}66`,
                }}>
                  {headerAvatar
                    ? <img src={headerAvatar} alt={headerName} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                    : <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: headerRing, fontSize: 16, fontWeight: 700, color: '#fff' }}>{(headerName || '?')[0].toUpperCase()}</div>
                  }
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: '#fff', letterSpacing: '-0.01em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {headerName}
                  </div>
                  {headerSub && <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.38)', marginTop: 1 }}>{headerSub}</div>}
                </div>
                {isSystem && (
                  <button
                    onClick={handleNewSession}
                    title={t('chat.clearHistory')}
                    style={{
                      flexShrink: 0, padding: '4px 10px', fontSize: 10, fontFamily: MONO,
                      background: 'rgba(124,58,237,0.12)', border: '1px solid rgba(124,58,237,0.30)',
                      borderRadius: 6, color: '#b39ddb', cursor: 'pointer', letterSpacing: '0.04em',
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'rgba(124,58,237,0.25)'; e.currentTarget.style.color = '#d1b3ff'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'rgba(124,58,237,0.12)'; e.currentTarget.style.color = '#b39ddb'; }}
                  >
                    {t('chat.newSession')}
                  </button>
                )}
              </div>
            );
          })()}

          {/* Messages */}
          <div ref={scrollRef} className="rp-msglist" style={{
            flex: 1, minHeight: 0, overflowY: 'scroll', overflowX: 'hidden',
            display: 'flex', flexDirection: 'column', paddingTop: 8, paddingBottom: 4,
          }}>
            {currentMsgs.length === 0 && activeRoom !== SYSTEM_ROOM && (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, opacity: 0.3, gap: 8 }}>
                <div style={{ fontSize: 36 }}>💬</div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.4)', fontFamily: SYS }}>{t('chat.empty')}</div>
              </div>
            )}
            {currentMsgs.map(msg => (
              <div key={msg.id}>
                <MessageBubble
                  msg={msg}
                  isOwn={String(msg.user_id) === String(user?.id)}
                  onImageClick={(src) => setLightbox({ type: 'image', src })}
                  onVideoClick={(src) => setLightbox({ type: 'video', src })}
                />
              </div>
            ))}
            {activeRoom === SYSTEM_ROOM && aiLoading && <AiTyping />}
          </div>

          {/* Input */}
          <ChatInputArea
            activeRoom={activeRoom}
            aiLoading={aiLoading}
            onSend={sendMessageFromText}
            onFileUpload={handleFileUpload}
            dmUserName={isDmRoom(activeRoom) ? (() => {
              const other = allUsers.find(u => activeRoom === dmRoomName(user?.id, u.user_id));
              return other?.display_name || other?.username;
            })() : null}
          />
        </div>
      </motion.div>

      {/* ── Rail (always visible, left side, expands to show labels) ── */}
      <motion.div
        animate={{ width: isExpanded ? RAIL_W_EXPANDED : PANEL_W_COLLAPSED }}
        transition={{ duration: 0.28, ease: 'easeOut' }}
        style={{
          flexShrink: 0, height: '100%',
          display: 'flex', flexDirection: 'column',
          borderRight: isExpanded ? '1px solid rgba(255,255,255,0.07)' : 'none',
          overflowY: 'auto', overflowX: 'hidden',
          order: 1,
        }}
        className="rp-msglist"
      >
        {/* AI Bot tile */}
        <SidebarTile
          src={aibotImg}
          label={t('chat.grepbotAI')}
          active={activeRoom === SYSTEM_ROOM}
          accent={PURPLE}
          online
          ringOverride={PURPLE}
          onClick={() => switchRoom(SYSTEM_ROOM)}
          showNotification={unreadRooms.has(SYSTEM_ROOM)}
          showLabel={isExpanded}
        />

        {/* Global Chat tile */}
        <SidebarTile
          src={groupchatImg}
          label={t('chat.globalChat')}
          active={activeRoom === GLOBAL_ROOM}
          accent={CYAN}
          online
          ringOverride={PURPLE}
          onClick={() => switchRoom(GLOBAL_ROOM)}
          showNotification={unreadRooms.has(GLOBAL_ROOM)}
          showLabel={isExpanded}
        />

        {/* All users — online first, clickable for DM */}
        {[...allUsers]
          .filter(u => String(u.user_id) !== String(user?.id))
          .sort((a, b) => (b.online ? 1 : 0) - (a.online ? 1 : 0))
          .map(u => {
            const src = u.avatar_url
              ? (u.avatar_url.startsWith('http') ? u.avatar_url : `${API_BASE}${u.avatar_url}`)
              : null;
            const initial = (u.display_name || u.username || '?')[0].toUpperCase();
            const colors = ['#00e5ff','#7c3aed','#ffaf00','#ff5722','#00e676','#e91e8c'];
            const color  = colors[((u.user_id || u.username || '?').charCodeAt(0)) % colors.length];
            const room = dmRoomName(user?.id, u.user_id);
            return (
              <SidebarTile
                key={u.user_id}
                src={src}
                fallback={initial}
                fallbackColor={color}
                label={u.display_name || u.username}
                sublabel={!u.online && u.last_seen ? lastSeenText(u.last_seen, t) : (u.online ? t('chat.online') : null)}
                online={u.online}
                active={activeRoom === room}
                onClick={() => switchRoom(room)}
                showNotification={unreadRooms.has(room)}
                showLabel={isExpanded}
              />
            );
          })}
      </motion.div>

      {/* Lightbox */}
      {lightbox && (
        <Lightbox type={lightbox.type} src={lightbox.src} onClose={() => setLightbox(null)} />
      )}
    </motion.div>
  );
});
