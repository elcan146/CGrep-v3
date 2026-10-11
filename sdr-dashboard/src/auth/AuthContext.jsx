import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'; // v2

const API = window.location.port === '5173'
  ? 'http://127.0.0.1:8000'   // Vite dev server proxies to backend
  : '';                         // Production: same-origin relative URLs go through any WAF/proxy

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user,        setUser]        = useState(undefined); // undefined = loading
  const [permissions, setPermissions] = useState([]);
  const [loading,     setLoading]     = useState(true);

  const checkSession = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/auth/me`, { credentials: 'include' });
      if (res.ok) {
        const data = await res.json();
        setUser(data);
        setPermissions(data.permissions || []);
      } else {
        setUser(null);
        setPermissions([]);
      }
    } catch {
      setUser(null);
      setPermissions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { checkSession(); }, [checkSession]);

  const logout = useCallback(async () => {
    await fetch(`${API}/api/auth/logout`, { method: 'POST', credentials: 'include' });
    setUser(null);
    setPermissions([]);
  }, []);

  const hasPermission = useCallback(
    (perm) => (user?.is_admin) || permissions.includes(perm),
    [user, permissions],
  );

  return (
    <AuthContext.Provider value={{ user, loading, permissions, setUser, logout, checkSession, hasPermission }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
export { API };
