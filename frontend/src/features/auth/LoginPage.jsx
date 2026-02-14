import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Loader2, Mail } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { auth } from '../../shared/api';
import { BRAND } from '../../config/brand';
import { Logo } from '../../shared/ui/Branding';

export const LoginPage = ({ onNavigate }) => {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);

  const handleLogin = async () => {
    setError('');
    setNeedsVerification(false);
    setLoading(true);
    try {
      await login(email, password);
      onNavigate('dashboard');
    } catch (err) {
      const status = err.response?.status;
      const msg = err.response?.data?.detail || err.message || 'Login failed';
      if (status === 403 && typeof msg === 'string' && msg.toLowerCase().includes('verify')) {
        setNeedsVerification(true);
      }
      setError(typeof msg === 'string' ? msg : 'Invalid credentials');
    } finally {
      setLoading(false);
    }
  };

  const handleResendVerification = async () => {
    if (!email) return;
    setResending(true);
    try {
      await auth.resendVerification(email);
      setResent(true);
      setTimeout(() => setResent(false), 5000);
    } catch {
      // endpoint always returns 200
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="min-h-screen bg-white flex flex-col">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          {error && (
            <div className="border-2 border-red-500 bg-red-50 p-4 mb-6">
              <div className="flex items-center gap-2">
                <AlertTriangle size={18} className="text-red-500 flex-shrink-0" />
                <span className="font-mono text-sm text-red-700">{error}</span>
              </div>
              {needsVerification && (
                <button
                  className="mt-3 w-full border border-red-300 py-2 font-mono text-sm font-bold text-red-700 hover:bg-red-100 transition-colors flex items-center justify-center gap-2"
                  onClick={handleResendVerification}
                  disabled={resending}
                >
                  {resending ? <><Loader2 size={14} className="animate-spin" /> Sending...</> : resent ? <><CheckCircle size={14} /> Verification email sent!</> : <><Mail size={14} /> Resend verification email</>}
                </button>
              )}
            </div>
          )}
          <div className="border-2 border-black p-8">
            <h2 className="text-3xl font-bold mb-2">Sign In</h2>
            <p className="font-mono text-sm text-gray-600 mb-8">Access your {BRAND.name} dashboard</p>
            <div className="space-y-4">
              <div>
                <label className="block font-mono text-sm mb-1">Email</label>
                <input
                  type="email"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <label className="block font-mono text-sm mb-1">Password</label>
                <input
                  type="password"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
                />
              </div>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors mt-4 flex items-center justify-center gap-2"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={handleLogin}
                disabled={loading}
              >
                {loading ? <><Loader2 size={18} className="animate-spin" /> Signing in...</> : 'Sign In'}
              </button>
            </div>
            <div className="mt-6 text-center space-y-2">
              <button
                type="button"
                className="font-mono text-sm hover:underline"
                style={{ color: '#9D00FF' }}
                onClick={() => onNavigate('forgot-password')}
              >
                Forgot password?
              </button>
              <div>
                <span className="font-mono text-sm text-gray-500">No account? </span>
                <button
                  className="font-mono text-sm font-bold hover:underline"
                  style={{ color: '#9D00FF' }}
                  onClick={() => onNavigate('register')}
                >
                  Register
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
