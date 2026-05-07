import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';

import { auth } from '../../shared/api';
import { AuthShell } from './AuthShell';

export const VerifyEmailPage = ({ onNavigate, token }) => {
  const [status, setStatus] = useState('loading');
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!token) {
      setStatus('error');
      setMessage('Invalid verification link.');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await auth.verifyEmail(token);
        if (!cancelled) {
          setStatus('success');
          setMessage(res.data?.detail || 'Email verified successfully.');
        }
      } catch (err) {
        if (!cancelled) {
          setStatus('error');
          setMessage(err.response?.data?.detail || 'Verification failed. The link may have expired.');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const heroProps = status === 'success'
    ? { kicker: 'ONE STEP LEFT', title: 'Welcome to Taali', sub: 'Next step: create your first role, or import from Workable.' }
    : status === 'error'
      ? { kicker: 'VERIFY EMAIL', title: 'Verification failed', sub: message }
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
            <button type="button" className="mc-auth-cta" onClick={() => onNavigate('jobs')}>
              Create your first role →
            </button>
            <button type="button" className="mc-auth-cta mc-auth-cta-outline" onClick={() => onNavigate('settings-workable')}>
              Connect Workable →
            </button>
            <button type="button" className="mc-auth-cta mc-auth-cta-outline" onClick={() => onNavigate('showcase')}>
              Take the 2-min tour →
            </button>
          </div>
        </>
      ) : status === 'error' ? (
        <>
          <div className="mc-auth-error-card" role="alert">
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <AlertTriangle size={16} strokeWidth={1.8} style={{ color: 'var(--red)', flexShrink: 0, marginTop: 2 }} />
              <div>
                <div className="title">This link may have expired</div>
                <div className="body">{message}</div>
              </div>
            </div>
          </div>
          <button type="button" className="mc-auth-cta" onClick={() => onNavigate('login')}>
            Go to sign in →
          </button>
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
