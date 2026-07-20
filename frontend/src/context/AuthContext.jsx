import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { auth as authApi } from '../shared/api/authClient';
import {
  activateSessionBoundary,
  beginSessionTransition,
  captureStoredSessionBoundary,
  endSessionBoundary,
  getCurrentSessionSnapshot,
  initializeSessionBoundary,
  isSessionBoundaryCurrent,
  isStoredSessionBoundaryCurrent,
  revokeSessionBoundary,
  SESSION_BOUNDARY_EVENT,
  storeSessionProfile,
} from '../shared/auth/sessionBoundary';

const AuthContext = createContext(null);

const supersededAuthenticationError = () => {
  const error = new Error('Authentication request was superseded.');
  error.name = 'AbortError';
  return error;
};

const requestAccessToken = (error, fallback = null) => {
  const header = String(error?.config?.headers?.Authorization || '');
  return header.startsWith('Bearer ') ? header.slice(7) : fallback;
};

const isInvalidCredentialResponse = (error) => Number(error?.response?.status || 0) === 401;

export function AuthProvider({ children }) {
  // Authentication calls are ordered by invocation, not response time. A
  // logout or newer sign-in invalidates every older token/profile request, so
  // a late response cannot restore an ended session or replace a newer user.
  const authGenerationRef = useRef(0);
  const initialSessionRef = useRef(null);
  if (!initialSessionRef.current) {
    const initializedBoundary = initializeSessionBoundary();
    const ownedSnapshot = getCurrentSessionSnapshot();
    initialSessionRef.current = {
      boundary: ownedSnapshot?.boundary || initializedBoundary,
      snapshot: ownedSnapshot,
    };
  }
  const [sessionBoundary, setSessionBoundary] = useState(initialSessionRef.current.boundary);
  const [user, setUser] = useState(initialSessionRef.current.snapshot?.profile || null);
  const [loading, setLoading] = useState(Boolean(initialSessionRef.current.snapshot?.token));
  const [validationEpoch, setValidationEpoch] = useState(0);

  const isAuthenticated = !!user;

  const beginAuthentication = useCallback(() => {
    // Reserve the cross-tab order before the network exchange. The marker has
    // no credential record yet, so a newly loaded tab cannot adopt the prior
    // account's token while this transition is incomplete.
    const boundary = beginSessionTransition();
    authGenerationRef.current += 1;
    setSessionBoundary(boundary);
    setUser(null);
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
    // The credential is written under this attempt's unique marker. A newer
    // tab can change the pointer at any moment, but an older completion can
    // then only create an unreachable record — never overwrite the winner.
    if (!activateSessionBoundary(attempt.boundary, accessToken)) {
      throw supersededAuthenticationError();
    }
    setSessionBoundary(attempt.boundary);
    try {
      const { data: profile } = await authApi.me();
      if (
        !isCurrentAuthentication(attempt)
        || !storeSessionProfile(attempt.boundary, profile)
      ) {
        throw supersededAuthenticationError();
      }
      setUser(profile);
      return profile;
    } catch (error) {
      // Revoke only this immutable marker. If another tab already owns a newer
      // session, its marker-scoped credentials remain untouched.
      if (isInvalidCredentialResponse(error)) {
        revokeSessionBoundary(attempt.boundary, {
          expectedToken: requestAccessToken(error, accessToken),
        });
      }
      throw error;
    } finally {
      if (authGenerationRef.current === attempt.generation) setLoading(false);
    }
  }, [isCurrentAuthentication]);

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
    endSessionBoundary();
  }, []);

  // Every transition (login start, explicit logout, external account switch,
  // or marker-scoped 401 revocation) invalidates private in-memory state.
  useEffect(() => {
    const handleSessionBoundary = () => {
      authGenerationRef.current += 1;
      const activeSnapshot = getCurrentSessionSnapshot();
      setSessionBoundary(activeSnapshot?.boundary || captureStoredSessionBoundary());
      setUser(null);
      setLoading(Boolean(activeSnapshot?.token));
      // A tab that mounted while the one-time legacy upgrade was pending has
      // already run its mount validation. Only the guarded migration
      // activation event has an owned token here; bumping this epoch reruns
      // `/me` even though the immutable boundary marker itself did not change.
      if (activeSnapshot?.token) setValidationEpoch((value) => value + 1);
    };
    window.addEventListener(SESSION_BOUNDARY_EVENT, handleSessionBoundary);
    return () => {
      window.removeEventListener(SESSION_BOUNDARY_EVENT, handleSessionBoundary);
    };
  }, []);

  // Validate token on mount, even when a cached user exists.
  useEffect(() => {
    const snapshot = getCurrentSessionSnapshot();
    if (!snapshot?.token) {
      authGenerationRef.current += 1;
      setUser(null);
      setLoading(false);
      return undefined;
    }
    const attempt = {
      boundary: snapshot.boundary,
      generation: authGenerationRef.current + 1,
    };
    authGenerationRef.current = attempt.generation;
    setLoading(true);
    authApi.me()
      .then(({ data }) => {
        if (
          !isCurrentAuthentication(attempt)
          || !storeSessionProfile(attempt.boundary, data)
        ) return;
        setUser(data);
      })
      .catch((error) => {
        if (isInvalidCredentialResponse(error)
          && isCurrentAuthentication(attempt)
          && isSessionBoundaryCurrent(attempt.boundary)) {
          revokeSessionBoundary(attempt.boundary, {
            expectedToken: requestAccessToken(error, snapshot.token),
          });
        }
      })
      .finally(() => {
        if (authGenerationRef.current === attempt.generation) setLoading(false);
      });
    return () => {
      if (authGenerationRef.current === attempt.generation) {
        authGenerationRef.current += 1;
      }
    };
  }, [isCurrentAuthentication, validationEpoch]);

  // If token disappears while state still has a user, force logout state sync.
  useEffect(() => {
    if (!getCurrentSessionSnapshot()?.token && user) {
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
      sessionBoundary,
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
