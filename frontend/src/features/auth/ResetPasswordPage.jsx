import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Loader2 } from 'lucide-react';

import { auth } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';

export const ResetPasswordPage = ({ onNavigate, token }) => {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    if (!token) {
      setError('Invalid reset link. Request a new one.');
      return;
    }
    setLoading(true);
    try {
      await auth.resetPassword(token, password);
      setSuccess(true);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Reset failed');
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="min-h-screen bg-white flex flex-col">
        <nav className="border-b-2 border-black bg-white">
          <div className="max-w-7xl mx-auto px-6 py-4">
            <Logo onClick={() => onNavigate('landing')} />
          </div>
        </nav>
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="border-2 border-black p-8 text-center max-w-md">
            <AlertTriangle size={48} className="mx-auto mb-4 text-amber-500" />
            <h2 className="text-2xl font-bold mb-2">Invalid link</h2>
            <p className="font-mono text-sm text-gray-600 mb-6">This reset link is missing or invalid. Request a new one from the login page.</p>
            <button className="w-full border-2 border-black py-3 font-bold text-white" style={{ backgroundColor: '#9D00FF' }} onClick={() => onNavigate('forgot-password')}>Request new link</button>
          </div>
        </div>
      </div>
    );
  }

  if (success) {
    return (
      <div className="min-h-screen bg-white flex flex-col">
        <nav className="border-b-2 border-black bg-white">
          <div className="max-w-7xl mx-auto px-6 py-4">
            <Logo onClick={() => onNavigate('landing')} />
          </div>
        </nav>
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="border-2 border-black p-8 text-center max-w-md">
            <CheckCircle size={48} className="mx-auto mb-4" style={{ color: '#9D00FF' }} />
            <h2 className="text-2xl font-bold mb-2">Password reset</h2>
            <p className="font-mono text-sm text-gray-600 mb-6">You can now sign in with your new password.</p>
            <button className="w-full border-2 border-black py-3 font-bold text-white" style={{ backgroundColor: '#9D00FF' }} onClick={() => onNavigate('login')}>Sign In</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white flex flex-col">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <div className="border-2 border-black p-8">
            <h2 className="text-3xl font-bold mb-2">Set new password</h2>
            <p className="font-mono text-sm text-gray-600 mb-6">Enter your new password below.</p>
            {error && (
              <div className="border-2 border-red-500 bg-red-50 p-3 mb-4 flex items-center gap-2">
                <AlertTriangle size={18} className="text-red-500 flex-shrink-0" />
                <span className="font-mono text-sm text-red-700">{error}</span>
              </div>
            )}
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block font-mono text-sm mb-1">New password</label>
                <input
                  type="password"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <div>
                <label className="block font-mono text-sm mb-1">Confirm password</label>
                <input
                  type="password"
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none"
                  placeholder="••••••••"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </div>
              <button
                type="submit"
                className="w-full border-2 border-black py-3 font-bold text-white transition-colors flex items-center justify-center gap-2"
                style={{ backgroundColor: '#9D00FF' }}
                disabled={loading}
              >
                {loading ? <><Loader2 size={18} className="animate-spin" /> Resetting...</> : 'Reset password'}
              </button>
            </form>
            <div className="mt-6 text-center">
              <button type="button" className="font-mono text-sm hover:underline" style={{ color: '#9D00FF' }} onClick={() => onNavigate('login')}>Back to Sign In</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
