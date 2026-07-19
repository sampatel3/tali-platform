import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { auth } from '../../shared/api';
import { PageLink } from '../../shared/ui/PageLink';
import { AuthShell, AuthField } from './AuthShell';
import { PasswordStrength } from './PasswordStrength';

// Typed accept-invite failures from the backend. INVITE_ALREADY_ACCEPTED and
// INVITE_SSO_REQUIRED both resolve on the sign-in page, so those get a
// "go to sign in" affordance rather than telling the user to chase their admin.
const INVITE_ERRORS = {
  INVITE_TOKEN_INVALID: 'This invite link is invalid or has expired. Ask your workspace admin to resend the invite.',
  INVITE_ALREADY_ACCEPTED: 'This invite was already accepted. Sign in instead.',
  INVITE_REVOKED: 'This invite is no longer active. Ask your workspace admin for a new one.',
  INVITE_SSO_REQUIRED: 'Your workspace requires single sign-on. Use "Sign in with SSO" on the sign-in page.',
};

// Errors whose fix lives on /login — the error card adds a sign-in link.
const SIGN_IN_ERRORS = new Set(['INVITE_ALREADY_ACCEPTED', 'INVITE_SSO_REQUIRED']);

export const AcceptInvitePage = ({ onNavigate, token }) => {
  const { completeLogin } = useAuth();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Set when the error resolves on the sign-in page (already accepted /
  // SSO-enforced) — the error card then offers a "go to sign in" link.
  const [offerSignIn, setOfferSignIn] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setOfferSignIn(false);
    if (password.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }
    // bcrypt only hashes the first 72 bytes, so the backend rejects anything
    // longer. Catch it here with a clear message rather than a server-side 422.
    if (new TextEncoder().encode(password).length > 72) {
      setError('That passphrase is too long — please keep it to 72 UTF-8 bytes or fewer.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    if (!token) {
      setError('This invite link is invalid or has expired. Ask your workspace admin to resend the invite.');
      return;
    }
    setLoading(true);
    try {
      const { data } = await auth.acceptInvite(token, password);
      // Same completion path as sign-in: store the token, load the profile.
      await completeLogin(data.access_token);
      onNavigate('home');
    } catch (err) {
      const status = err.response?.status;
      const detail = err.response?.data?.detail;
      if (err.code === 'ERR_NETWORK' || err.message === 'Network Error' || (status && status >= 502)) {
        setError('Can\'t reach Taali right now — try again in a moment.');
      } else if (status === 400 && typeof detail === 'string' && INVITE_ERRORS[detail]) {
        setError(INVITE_ERRORS[detail]);
        setOfferSignIn(SIGN_IN_ERRORS.has(detail));
      } else if (status === 422) {
        // The backend returns the specific reason (too common, contains email,
        // too short/long) as a plain string in detail — show it verbatim.
        setError(
          typeof detail === 'string' && detail.trim()
            ? detail.trim()
            : 'That password can\'t be used — choose at least 8 characters.',
        );
      } else {
        setError('We couldn\'t set up your account. The invite may have expired — ask your workspace admin to resend it.');
      }
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <AuthShell
        onNavigate={onNavigate}
        kicker="JOIN YOUR TEAM"
        title="Invite link missing"
        sub="This invite link is missing or invalid. Ask your workspace admin to resend the invite."
      >
        <PageLink page="login" className="mc-auth-cta">
          Go to sign in →
        </PageLink>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      onNavigate={onNavigate}
      kicker="JOIN YOUR TEAM"
      title="Set a password to get started"
      sub="You've been invited to join your team on Taali. Set a password to get started."
      topRight={(
        <span>
          Already set up?{' '}
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
          {offerSignIn ? (
            <PageLink page="login" className="mc-auth-cta mc-auth-cta-outline" style={{ marginTop: 12, height: 36, fontSize: 13 }}>
              Go to sign in →
            </PageLink>
          ) : null}
        </div>
      ) : null}
      <form onSubmit={handleSubmit}>
        <AuthField
          label="Password"
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
          label="Confirm password"
          name="confirm"
          type="password"
          autoComplete="new-password"
          placeholder="••••••••"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <button type="submit" className="mc-auth-cta" disabled={loading}>
          {loading ? 'Setting up your account...' : 'Set password & continue →'}
        </button>
      </form>
    </AuthShell>
  );
};
