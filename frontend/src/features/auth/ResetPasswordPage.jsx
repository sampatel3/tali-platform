import React, { useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';

import { auth } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';
import { Button, Input, Spinner } from '../../shared/ui/TaaliPrimitives';

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

  const navLayout = (
    <nav className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
      <div className="max-w-7xl mx-auto px-6 py-4">
        <Logo onClick={() => onNavigate('landing')} />
      </div>
    </nav>
  );

  if (!token) {
    return (
      <div className="min-h-screen bg-[var(--taali-surface)] flex flex-col">
        {navLayout}
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="border-2 border-[var(--taali-border)] p-8 text-center max-w-md bg-[var(--taali-surface)]">
            <AlertTriangle size={48} className="mx-auto mb-4 text-[var(--taali-warning)]" />
            <h2 className="text-2xl font-bold mb-2">Invalid link</h2>
            <p className="text-sm text-[var(--taali-muted)] mb-6">This reset link is missing or invalid. Request a new one from the login page.</p>
            <Button variant="primary" className="w-full" onClick={() => onNavigate('forgot-password')}>Request new link</Button>
          </div>
        </div>
      </div>
    );
  }

  if (success) {
    return (
      <div className="min-h-screen bg-[var(--taali-surface)] flex flex-col">
        {navLayout}
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="border-2 border-[var(--taali-border)] p-8 text-center max-w-md bg-[var(--taali-surface)]">
            <CheckCircle size={48} className="mx-auto mb-4 text-[var(--taali-purple)]" />
            <h2 className="text-2xl font-bold mb-2">Password reset</h2>
            <p className="text-sm text-[var(--taali-muted)] mb-6">You can now sign in with your new password.</p>
            <Button variant="primary" className="w-full" onClick={() => onNavigate('login')}>Sign In</Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--taali-surface)] flex flex-col">
      {navLayout}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <div className="border-2 border-[var(--taali-border)] p-8 bg-[var(--taali-surface)]">
            <h2 className="text-3xl font-bold mb-2">Set new password</h2>
            <p className="text-sm text-[var(--taali-muted)] mb-6">Enter your new password below.</p>
            {error && (
              <div className="border-2 border-[var(--taali-danger)] bg-[var(--taali-danger-soft)] p-3 mb-4 flex items-center gap-2">
                <AlertTriangle size={18} className="text-[var(--taali-danger)] flex-shrink-0" />
                <span className="text-sm text-[var(--taali-text)]">{error}</span>
              </div>
            )}
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block font-mono text-sm mb-1">New password</label>
                <Input
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <div>
                <label className="block font-mono text-sm mb-1">Confirm password</label>
                <Input
                  type="password"
                  placeholder="••••••••"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </div>
              <Button type="submit" variant="primary" className="w-full" disabled={loading}>
                {loading ? <><Spinner size={18} /> Resetting...</> : 'Reset password'}
              </Button>
            </form>
            <div className="mt-6 text-center">
              <button type="button" className="text-sm hover:underline text-[var(--taali-purple)]" onClick={() => onNavigate('login')}>Back to Sign In</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
