import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { auth as authApi } from '../shared/api/authClient';
import { clearAccessToken, setAccessToken } from '../shared/api/httpClient';
import { clearCache } from '../shared/api/resourceCache';
import { clearDocumentCache } from '../shared/api/documentCache';
import { clearJobTrackingStorage } from '../shared/jobs/jobTrackingStorage';

const AuthContext = createContext(null);

const clearPrivateClientState = () => {
  clearCache();
  clearDocumentCache();
  clearJobTrackingStorage();
};

export function AuthProvider({ children }) {
  // Every authentication request captures a session generation. Logout and
  // newer sign-ins advance it, while a sliding token refresh stays within the
  // same generation. An older /me response can therefore neither restore a
  // logged-out user nor clear a newer session after a late failure.
  const authGenerationRef = useRef(0);
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
    const generation = authGenerationRef.current + 1;
    authGenerationRef.current = generation;
    setAccessToken(accessToken);
    setLoading(true);
    const isCurrentRequest = () => (
      authGenerationRef.current === generation
      && Boolean(localStorage.getItem('taali_access_token'))
    );
    try {
      const { data: profile } = await authApi.me();
      if (!isCurrentRequest()) {
        const error = new Error('Authentication request was superseded.');
        error.name = 'AbortError';
        throw error;
      }
      localStorage.setItem('taali_user', JSON.stringify(profile));
      setUser(profile);
      return profile;
    } catch (error) {
      // Never leave a half-authenticated browser session when the profile
      // bootstrap fails after a successful token exchange. A stale request is
      // not allowed to mutate the session that superseded it.
      if (isCurrentRequest()) {
        clearAccessToken();
        localStorage.removeItem('taali_user');
        clearPrivateClientState();
        setUser(null);
      }
      throw error;
    } finally {
      if (authGenerationRef.current === generation) setLoading(false);
    }
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
    authGenerationRef.current += 1;
    clearAccessToken();
    localStorage.removeItem('taali_user');
    // Drop any cached per-account data (e.g. role workspaces) so the next user
    // to sign in on this tab can't briefly see the previous user's data.
    clearPrivateClientState();
    setUser(null);
    setLoading(false);
  }, []);

  // Listen for forced logout (401 interceptor)
  useEffect(() => {
    const handleLogout = () => {
      authGenerationRef.current += 1;
      clearPrivateClientState();
      setUser(null);
      setLoading(false);
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
      return undefined;
    }
    const generation = authGenerationRef.current + 1;
    authGenerationRef.current = generation;
    const isCurrentRequest = () => (
      authGenerationRef.current === generation
      // The sliding-session interceptor may rotate this token while /me is in
      // flight. Generation identifies the login session; token presence still
      // prevents a cleared session from being restored.
      && Boolean(localStorage.getItem('taali_access_token'))
    );
    setLoading(true);
    authApi.me()
      .then(({ data }) => {
        if (!isCurrentRequest()) return;
        setUser(data);
        localStorage.setItem('taali_user', JSON.stringify(data));
      })
      .catch(() => {
        if (!isCurrentRequest()) return;
        clearAccessToken();
        localStorage.removeItem('taali_user');
        clearPrivateClientState();
        setUser(null);
      })
      .finally(() => {
        if (authGenerationRef.current === generation) setLoading(false);
      });
    return () => {
      if (authGenerationRef.current === generation) authGenerationRef.current += 1;
    };
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
