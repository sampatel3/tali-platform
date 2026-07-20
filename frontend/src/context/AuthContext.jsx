import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { auth as authApi } from '../shared/api/authClient';
import { clearAccessToken, setAccessToken } from '../shared/api/httpClient';
import {
  announceSessionBoundary,
  captureStoredSessionBoundary,
  initializeSessionBoundary,
  isStoredSessionBoundaryCurrent,
  SESSION_BOUNDARY_EVENT,
} from '../shared/auth/sessionBoundary';
import { clearJobTrackingStorage } from '../shared/jobs/jobTrackingStorage';

const AuthContext = createContext(null);

const supersededAuthenticationError = () => {
  const error = new Error('Authentication request was superseded.');
  error.name = 'AbortError';
  return error;
};

const publishLogout = () => {
  if (typeof window !== 'undefined') {
    announceSessionBoundary({ active: false });
    window.dispatchEvent(new Event('auth:logout'));
  }
};

export function AuthProvider({ children }) {
  // Authentication calls are ordered by invocation, not response time. A
  // logout or newer sign-in invalidates every older token/profile request, so
  // a late response cannot restore an ended session or replace a newer user.
  const authGenerationRef = useRef(0);
  const [user, setUser] = useState(() => {
    initializeSessionBoundary();
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

  const beginAuthentication = useCallback(() => {
    const boundary = captureStoredSessionBoundary();
    authGenerationRef.current += 1;
    setLoading(true);
    return {
      boundary,
      generation: authGenerationRef.current,
    };
  }, []);

  const isCurrentAuthentication = useCallback((attempt) => (
    Boolean(attempt)
    && authGenerationRef.current === attempt.generation
    && isStoredSessionBoundaryCurrent(attempt.boundary)
  ), []);

  const bootstrapProfile = useCallback(async (accessToken, attempt) => {
    if (!isCurrentAuthentication(attempt)) {
      throw supersededAuthenticationError();
    }
    // A successful credential exchange may replace an already-authenticated
    // account (notably /accept-invite). Publish the boundary before installing
    // the new token so this tab clears private caches and every other tab stops
    // using UI rendered for the prior account.
    announceSessionBoundary({ active: true });
    clearAccessToken();
    localStorage.removeItem('taali_user');
    clearJobTrackingStorage();
    setUser(null);
    const sessionAttempt = beginAuthentication();
    try {
      setAccessToken(accessToken);
      const { data: profile } = await authApi.me();
      if (
        !isCurrentAuthentication(sessionAttempt)
        || !localStorage.getItem('taali_access_token')
      ) {
        throw supersededAuthenticationError();
      }
      localStorage.setItem('taali_user', JSON.stringify(profile));
      setUser(profile);
      return profile;
    } catch (error) {
      // Roll back a half-created current session. A stale request must never
      // clear the token/profile belonging to the request that superseded it.
      if (isCurrentAuthentication(sessionAttempt)) publishLogout();
      throw error;
    } finally {
      if (authGenerationRef.current === sessionAttempt.generation) setLoading(false);
    }
  }, [beginAuthentication, isCurrentAuthentication]);

  const login = useCallback(async (email, password) => {
    const attempt = beginAuthentication();
    try {
      const { data } = await authApi.login(email, password);
      return await bootstrapProfile(data.access_token, attempt);
    } catch (error) {
      if (authGenerationRef.current === attempt.generation) setLoading(false);
      throw error;
    }
  }, [beginAuthentication, bootstrapProfile]);

  const acceptInvite = useCallback(async (token, password) => {
    const attempt = beginAuthentication();
    try {
      const { data } = await authApi.acceptInvite(token, password);
      return await bootstrapProfile(data.access_token, attempt);
    } catch (error) {
      if (authGenerationRef.current === attempt.generation) setLoading(false);
      throw error;
    }
  }, [beginAuthentication, bootstrapProfile]);

  const register = useCallback(async (userData) => {
    const { data } = await authApi.register(userData);
    return data;
  }, []);

  const logout = useCallback(() => {
    publishLogout();
  }, []);

  // One event terminates both explicit logouts and forced 401 logouts. Private
  // client caches subscribe to the same event and clear independently.
  useEffect(() => {
    const handleSessionBoundary = () => {
      // Do not mutate shared token/profile storage here: for an external
      // boundary, another tab may already be installing the next account.
      authGenerationRef.current += 1;
      setUser(null);
      setLoading(false);
    };
    const handleLogout = () => {
      authGenerationRef.current += 1;
      clearAccessToken();
      localStorage.removeItem('taali_user');
      clearJobTrackingStorage();
      setUser(null);
      setLoading(false);
    };
    window.addEventListener(SESSION_BOUNDARY_EVENT, handleSessionBoundary);
    window.addEventListener('auth:logout', handleLogout);
    return () => {
      window.removeEventListener(SESSION_BOUNDARY_EVENT, handleSessionBoundary);
      window.removeEventListener('auth:logout', handleLogout);
    };
  }, []);

  // Validate token on mount, even when a cached user exists.
  useEffect(() => {
    const token = localStorage.getItem('taali_access_token');
    if (!token) {
      authGenerationRef.current += 1;
      localStorage.removeItem('taali_user');
      setUser(null);
      setLoading(false);
      return undefined;
    }
    const attempt = beginAuthentication();
    authApi.me()
      .then(({ data }) => {
        if (
          !isCurrentAuthentication(attempt)
          || !localStorage.getItem('taali_access_token')
        ) return;
        setUser(data);
        localStorage.setItem('taali_user', JSON.stringify(data));
      })
      .catch(() => {
        if (isCurrentAuthentication(attempt)) publishLogout();
      })
      .finally(() => {
        if (authGenerationRef.current === attempt.generation) setLoading(false);
      });
    return () => {
      if (authGenerationRef.current === attempt.generation) {
        authGenerationRef.current += 1;
      }
    };
  }, [beginAuthentication, isCurrentAuthentication]);

  // If token disappears while state still has a user, force logout state sync.
  useEffect(() => {
    const token = localStorage.getItem('taali_access_token');
    if (!token && user) {
      setUser(null);
    }
  }, [user]);

  return (
    <AuthContext.Provider value={{
      user,
      isAuthenticated,
      loading,
      login,
      acceptInvite,
      register,
      logout,
    }}>
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
