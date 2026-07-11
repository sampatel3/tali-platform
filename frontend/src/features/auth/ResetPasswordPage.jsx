import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { auth } from '../../shared/api';
import { PageLink } from '../../shared/ui/PageLink';
import { AuthShell, AuthField } from './AuthShell';
import { PasswordStrength } from './PasswordStrength';

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
    // bcrypt only hashes the first 72 bytes, so the backend rejects anything
    // longer. We encourage passphrases, so catch it here with a clear message
    // rather than letting a long one fail server-side.
    if (new TextEncoder().encode(password).length > 72) {
      setError('That passphrase is too long — please keep it to 72 characters or fewer.');
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
      const status = err.response?.status;
      const detail = err.response?.data?.detail;
      if (err.code === 'ERR_NETWORK' || err.message === 'Network Error' || (status && status >= 502)) {
        setError('Can\'t reach Taali right now — try again in a moment.');
      } else if (status === 400 && detail?.code === 'RESET_PASSWORD_INVALID_PASSWORD') {
        setError(detail.reason || 'That password can\'t be used — try a different one.');
      } else {
        setError('We couldn\'t update your password. The link may have expired — request a new one below.');
      }
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <AuthShell onNavigate={onNavigate} kicker="SET A NEW PASSWORD" title="Invalid link" sub="This reset link is missing or invalid. Request a new one from the login page.">
        <PageLink page="forgot-password" className="mc-auth-cta">
          Request new link →
        </PageLink>
      </AuthShell>
    );
  }

  if (success) {
    return (
      <AuthShell onNavigate={onNavigate} kicker="SET A NEW PASSWORD" title="Password updated" sub="You can now sign in with your new password.">
        <PageLink page="login" className="mc-auth-cta">
          Sign in →
        </PageLink>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      onNavigate={onNavigate}
      kicker="SET A NEW PASSWORD"
      title="Choose something memorable"
      sub="Use a passphrase, not a word. At least 8 characters; we won't make you add a symbol."
      topRight={(
        <span>
          Back to{' '}
          <PageLink
            page="login"
            style={{ background: 'none', border: 0, color: 'var(--purple)', fontWeight: 500, cursor: 'pointer', padding: 0, font: 'inherit', textDecoration: 'none' }}
          >
            sign in
          </PageLink>
        </span>
      )}
    >
      {error ? (
        <div className="mc-auth-error-card" role="alert">
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <AlertTriangle size={16} strokeWidth={1.8} style={{ color: 'var(--red)', flexShrink: 0 }} />
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{error}</span>
          </div>
        </div>
      ) : null}
      <form onSubmit={handleSubmit}>
        <AuthField
          label="New password"
          name="password"
          type="password"
          autoComplete="new-password"
          placeholder="••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          helper="At least 8 characters. Strong: a string of words you'll remember."
        />
        <PasswordStrength password={password} />
        <AuthField
          label="Confirm new password"
          name="confirm"
          type="password"
          autoComplete="new-password"
          placeholder="••••••••"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <button type="submit" className="mc-auth-cta" disabled={loading}>
          {loading ? 'Updating password...' : 'Update password →'}
        </button>
      </form>
    </AuthShell>
  );
};
