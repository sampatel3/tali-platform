import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Loader2 } from 'lucide-react';

import { auth } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';

export const ForgotPasswordPage = ({ onNavigate }) => {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!email.trim()) {
      setError('Enter your email address');
      return;
    }
    setLoading(true);
    try {
      await auth.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Request failed');
    } finally {
      setLoading(false);
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
          {sent ? (
            <div className="border-2 border-black p-8 text-center">
              <CheckCircle size={48} className="mx-auto mb-4" style={{ color: '#9D00FF' }} />
              <h2 className="text-2xl font-bold mb-2">Check your email</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">
                If an account exists for that email, we sent a link to reset your password.
              </p>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white transition-colors"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={() => onNavigate('login')}
              >
                Back to Sign In
              </button>
            </div>
          ) : (
            <div className="border-2 border-black p-8">
              <h2 className="text-3xl font-bold mb-2">Forgot password?</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">Enter your email and we&apos;ll send a reset link.</p>
              {error && (
                <div className="border-2 border-red-500 bg-red-50 p-3 mb-4 flex items-center gap-2">
                  <AlertTriangle size={18} className="text-red-500 flex-shrink-0" />
                  <span className="font-mono text-sm text-red-700">{error}</span>
                </div>
              )}
              <form onSubmit={handleSubmit} className="space-y-4">
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
                <button
                  type="submit"
                  className="w-full border-2 border-black py-3 font-bold text-white transition-colors flex items-center justify-center gap-2"
                  style={{ backgroundColor: '#9D00FF' }}
                  disabled={loading}
                >
                  {loading ? <><Loader2 size={18} className="animate-spin" /> Sending...</> : 'Send reset link'}
                </button>
              </form>
              <div className="mt-6 text-center">
                <button
                  type="button"
                  className="font-mono text-sm hover:underline"
                  style={{ color: '#9D00FF' }}
                  onClick={() => onNavigate('login')}
                >
                  Back to Sign In
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
