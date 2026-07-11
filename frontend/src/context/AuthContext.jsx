import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { auth as authApi } from '../shared/api';
import { clearAccessToken, setAccessToken } from '../shared/api/httpClient';
import { clearCache } from '../shared/api/resourceCache';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    const token = localStorage.getItem('taali_access_token');
    if (!token) {
      localStorage.removeItem('taali_user');
      return null;
    }
    const saved = localStorage.getItem('taali_user');
    try {
      return saved ? JSON.parse(saved) : null;
    } catch {
      localStorage.removeItem('taali_user');
      return null;
    }
  });
  const [loading, setLoading] = useState(() => Boolean(localStorage.getItem('taali_access_token')));

  const isAuthenticated = !!user;

  // Finish signing a user in once we already hold an access token (login
  // returns one; so does accept-invite). Store the token, then fetch and
  // cache the profile — the exact tail of `login`, shared so callers never
  // hand-roll localStorage writes.
  const completeLogin = useCallback(async (accessToken) => {
    setAccessToken(accessToken);
    const { data: profile } = await authApi.me();
    localStorage.setItem('taali_user', JSON.stringify(profile));
    setUser(profile);
    return profile;
  }, []);

  const login = useCallback(async (email, password) => {
    const { data } = await authApi.login(email, password);
    return completeLogin(data.access_token);
  }, [completeLogin]);

  const register = useCallback(async (userData) => {
    const { data } = await authApi.register(userData);
    return data;
  }, []);

  const logout = useCallback(() => {
    clearAccessToken();
    localStorage.removeItem('taali_user');
    // Drop any cached per-account data (e.g. role workspaces) so the next user
    // to sign in on this tab can't briefly see the previous user's data.
    clearCache();
    setUser(null);
  }, []);

  // Listen for forced logout (401 interceptor)
  useEffect(() => {
    const handleLogout = () => {
      setUser(null);
    };
    window.addEventListener('auth:logout', handleLogout);
    return () => window.removeEventListener('auth:logout', handleLogout);
  }, []);

  // Validate token on mount, even when a cached user exists.
  useEffect(() => {
    const token = localStorage.getItem('taali_access_token');
    if (!token) {
      localStorage.removeItem('taali_user');
      setUser(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    authApi.me()
      .then(({ data }) => {
        setUser(data);
        localStorage.setItem('taali_user', JSON.stringify(data));
      })
      .catch(() => {
        clearAccessToken();
        localStorage.removeItem('taali_user');
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  // If token disappears while state still has a user, force logout state sync.
  useEffect(() => {
    const token = localStorage.getItem('taali_access_token');
    if (!token && user) {
      setUser(null);
    }
  }, [user]);

  return (
    <AuthContext.Provider value={{ user, isAuthenticated, loading, login, completeLogin, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

export default AuthContext;
