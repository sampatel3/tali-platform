import React, { useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';

import { auth } from '../../shared/api';
import { FlowLayout, AuthCard } from './AuthLayout';

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
      setError('Password must be at least 8 characters long.');
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
      <FlowLayout>
        <AuthCard kicker="RESET PASSWORD" title={<>Invalid <em>link</em>.</>} subtitle="This reset link is missing or invalid. Request a new one from the login page." widthClassName="max-w-[560px]">
          <button type="button" className="btn btn-purple btn-lg w-full justify-center" onClick={() => onNavigate('forgot-password')}>
            Request new link <span className="arrow">→</span>
          </button>
        </AuthCard>
      </FlowLayout>
    );
  }

  if (success) {
    return (
      <FlowLayout>
        <AuthCard kicker="RESET PASSWORD" title={<>Password <em>updated</em>.</>} subtitle="You can now sign in with your new password." widthClassName="max-w-[560px]">
          <button type="button" className="btn btn-purple btn-lg w-full justify-center" onClick={() => onNavigate('login')}>
            Sign in <span className="arrow">→</span>
          </button>
        </AuthCard>
      </FlowLayout>
    );
  }

  return (
    <FlowLayout>
      <AuthCard kicker="RESET PASSWORD" title={<>Set a <em>new</em> password.</>} subtitle="Use a strong password you haven’t used before." widthClassName="max-w-[560px]">
        {error ? (
          <div className="mb-4 flex items-center gap-2 rounded-[14px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-3">
            <AlertTriangle size={18} className="shrink-0 text-[var(--taali-danger)]" />
            <span className="text-sm text-[var(--ink)]">{error}</span>
          </div>
        ) : null}
        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="field">
            <span className="k">New password</span>
            <input type="password" placeholder="••••••••" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label className="field">
            <span className="k">Confirm new password</span>
            <input type="password" placeholder="••••••••" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </label>
          <button type="submit" className="btn btn-purple btn-lg w-full justify-center" disabled={loading}>
            {loading ? 'Updating password...' : <>Update password <span className="arrow">→</span></>}
          </button>
        </form>
        <div className="mt-5 text-center text-[13.5px] text-[var(--mute)]">
          Back to{' '}
          <button type="button" className="font-medium text-[var(--purple)] hover:underline" onClick={() => onNavigate('login')}>
            sign in
          </button>
        </div>
      </AuthCard>
    </FlowLayout>
  );
};
