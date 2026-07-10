import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';

import { auth } from '../../shared/api';
import { PageLink } from '../../shared/ui/PageLink';
import { AuthShell } from './AuthShell';

export const VerifyEmailPage = ({ onNavigate, token }) => {
  // 'already' distinguishes a refresh/second click on a link that already
  // did its job — that's a success state, not a failure.
  const [status, setStatus] = useState('loading');

  useEffect(() => {
    if (!token) {
      setStatus('error');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await auth.verifyEmail(token);
        if (!cancelled) {
          setStatus('success');
        }
      } catch (err) {
        if (cancelled) return;
        // fastapi-users returns typed codes, never render them raw.
        // An already-verified account (e.g. a second click) is a success.
        const code = err.response?.data?.detail;
        if (code === 'VERIFY_USER_ALREADY_VERIFIED') {
          setStatus('already');
        } else {
          setStatus('error');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const heroProps = status === 'success'
    ? { kicker: 'ONE STEP LEFT', title: 'Welcome to Taali', sub: 'Next step: create your first role, or import from Workable.' }
    : status === 'already'
      ? { kicker: 'VERIFY EMAIL', title: 'You\'re already verified', sub: 'Your email is confirmed — sign in below.' }
      : status === 'error'
        ? { kicker: 'VERIFY EMAIL', title: 'This link didn\'t work', sub: 'It has expired or was already used.' }
        : { kicker: 'VERIFY EMAIL', title: 'Verifying your email', sub: 'Please wait a moment.' };

  return (
    <AuthShell {...heroProps} onNavigate={onNavigate}>
      {status === 'success' ? (
        <>
          <div className="mc-auth-success-card" style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: 16 }}>
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: 'var(--green)',
                color: '#fff',
                display: 'grid',
                placeItems: 'center',
                flexShrink: 0,
              }}
            >
              <CheckCircle size={16} strokeWidth={1.8} />
            </div>
            <div>
              <p style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>Email verified.</p>
              <p style={{ margin: '4px 0 0', fontSize: 13, lineHeight: 1.5, color: 'var(--ink-2)' }}>
                Your account is active. Your workspace is ready.
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 18 }}>
            <PageLink page="jobs" className="mc-auth-cta">
              Create your first role →
            </PageLink>
            <PageLink page="settings-workable" className="mc-auth-cta mc-auth-cta-outline">
              Connect Workable →
            </PageLink>
            <PageLink page="showcase" className="mc-auth-cta mc-auth-cta-outline">
              Take the 2-min tour →
            </PageLink>
          </div>
        </>
      ) : status === 'already' ? (
        <>
          <div className="mc-auth-success-card" style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: 16 }}>
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: 'var(--green)',
                color: '#fff',
                display: 'grid',
                placeItems: 'center',
                flexShrink: 0,
              }}
            >
              <CheckCircle size={16} strokeWidth={1.8} />
            </div>
            <div>
              <p style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>You&apos;re already verified.</p>
              <p style={{ margin: '4px 0 0', fontSize: 13, lineHeight: 1.5, color: 'var(--ink-2)' }}>
                Sign in below to reach your workspace.
              </p>
            </div>
          </div>
          <PageLink page="login" className="mc-auth-cta" style={{ marginTop: 18 }}>
            Go to sign in →
          </PageLink>
        </>
      ) : status === 'error' ? (
        <>
          <div className="mc-auth-error-card" role="alert">
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <AlertTriangle size={16} strokeWidth={1.8} style={{ color: 'var(--red)', flexShrink: 0, marginTop: 2 }} />
              <div>
                <div className="title">This link has expired or was already used</div>
                <div className="body">Request a fresh verification email, or sign in if your account is already active.</div>
              </div>
            </div>
          </div>
          <PageLink page="login" className="mc-auth-cta">
            Go to sign in →
          </PageLink>
        </>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ height: 12, width: '33%', borderRadius: 999, background: 'var(--line)' }} />
          <div style={{ height: 12, width: '67%', borderRadius: 999, background: 'var(--line)' }} />
          <div style={{ height: 12, width: '50%', borderRadius: 999, background: 'var(--line)' }} />
        </div>
      )}
    </AuthShell>
  );
};
