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
import { clearJobTrackingStorage } from '../shared/jobs/jobTrackingStorage';

const AuthContext = createContext(null);

const supersededAuthenticationError = () => {
  const error = new Error('Authentication request was superseded.');
  error.name = 'AbortError';
  return error;
};

const publishLogout = () => {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('auth:logout'));
  }
};

export function AuthProvider({ children }) {
  // Authentication calls are ordered by invocation, not response time. A
  // logout or newer sign-in invalidates every older token/profile request, so
  // a late response cannot restore an ended session or replace a newer user.
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

  const beginAuthentication = useCallback(() => {
    authGenerationRef.current += 1;
    setLoading(true);
    return authGenerationRef.current;
  }, []);

  const bootstrapProfile = useCallback(async (accessToken, generation) => {
    if (authGenerationRef.current !== generation) {
      throw supersededAuthenticationError();
    }
    try {
      setAccessToken(accessToken);
      const { data: profile } = await authApi.me();
      if (
        authGenerationRef.current !== generation
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
      if (authGenerationRef.current === generation) publishLogout();
      throw error;
    } finally {
      if (authGenerationRef.current === generation) setLoading(false);
    }
  }, []);

  const login = useCallback(async (email, password) => {
    const generation = beginAuthentication();
    try {
      const { data } = await authApi.login(email, password);
      return await bootstrapProfile(data.access_token, generation);
    } catch (error) {
      if (authGenerationRef.current === generation) setLoading(false);
      throw error;
    }
  }, [beginAuthentication, bootstrapProfile]);

  const acceptInvite = useCallback(async (token, password) => {
    const generation = beginAuthentication();
    try {
      const { data } = await authApi.acceptInvite(token, password);
      return await bootstrapProfile(data.access_token, generation);
    } catch (error) {
      if (authGenerationRef.current === generation) setLoading(false);
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
    const handleLogout = () => {
      authGenerationRef.current += 1;
      clearAccessToken();
      localStorage.removeItem('taali_user');
      clearJobTrackingStorage();
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
      authGenerationRef.current += 1;
      localStorage.removeItem('taali_user');
      setUser(null);
      setLoading(false);
      return undefined;
    }
    const generation = beginAuthentication();
    authApi.me()
      .then(({ data }) => {
        if (
          authGenerationRef.current !== generation
          || !localStorage.getItem('taali_access_token')
        ) return;
        setUser(data);
        localStorage.setItem('taali_user', JSON.stringify(data));
      })
      .catch(() => {
        if (authGenerationRef.current === generation) publishLogout();
      })
      .finally(() => {
        if (authGenerationRef.current === generation) setLoading(false);
      });
    return () => {
      if (authGenerationRef.current === generation) {
        authGenerationRef.current += 1;
      }
    };
  }, [beginAuthentication]);

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
