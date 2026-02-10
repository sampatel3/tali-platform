import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { auth as authApi } from '../lib/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    const saved = localStorage.getItem('tali_user');
    return saved ? JSON.parse(saved) : null;
  });
  const [loading, setLoading] = useState(false);

  const isAuthenticated = !!user;

  const login = useCallback(async (email, password) => {
    const { data } = await authApi.login(email, password);
    localStorage.setItem('tali_access_token', data.access_token);

    // Fetch user profile
    const { data: profile } = await authApi.me();
    localStorage.setItem('tali_user', JSON.stringify(profile));
    setUser(profile);
    return profile;
  }, []);

  const register = useCallback(async (userData) => {
    const { data } = await authApi.register(userData);
    return data;
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('tali_access_token');
    localStorage.removeItem('tali_user');
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

  // Validate token on mount
  useEffect(() => {
    const token = localStorage.getItem('tali_access_token');
    if (token && !user) {
      setLoading(true);
      authApi.me()
        .then(({ data }) => {
          setUser(data);
          localStorage.setItem('tali_user', JSON.stringify(data));
        })
        .catch(() => {
          localStorage.removeItem('tali_access_token');
          localStorage.removeItem('tali_user');
          setUser(null);
        })
        .finally(() => setLoading(false));
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, isAuthenticated, loading, login, register, logout }}>
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
