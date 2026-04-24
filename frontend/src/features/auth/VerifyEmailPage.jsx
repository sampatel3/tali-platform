import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';

import { auth } from '../../shared/api';
import { FlowLayout, AuthCard } from './AuthLayout';

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

  return (
    <FlowLayout>
      <AuthCard
        kicker="VERIFY EMAIL"
        title={status === 'success'
          ? <>Welcome to <em>Taali</em>.</>
          : status === 'error'
            ? <>Verification <em>failed</em>.</>
            : <>Verifying your <em>email</em>.</>}
        subtitle={status === 'loading' ? 'Please wait a moment.' : message}
        widthClassName="max-w-[560px]"
      >
        {status === 'success' ? (
          <div className="mb-6 rounded-[14px] border border-[color-mix(in_oklab,var(--green)_30%,var(--line))] bg-[color-mix(in_oklab,var(--green)_10%,var(--bg-2))] p-4">
            <div className="flex items-start gap-3">
              <div className="grid h-9 w-9 place-items-center rounded-[10px] bg-[var(--green)] text-white">
                <CheckCircle size={18} />
              </div>
              <div>
                <p className="text-[15px] font-semibold text-[var(--ink)]">Email verified</p>
                <p className="mt-1 text-[13px] leading-6 text-[var(--ink-2)]">Your account is active and ready to use.</p>
              </div>
            </div>
          </div>
        ) : null}
        {status === 'error' ? (
          <div className="mb-6 rounded-[14px] border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle size={18} className="mt-0.5 shrink-0 text-[var(--taali-warning)]" />
              <div>
                <p className="text-[15px] font-semibold text-[var(--ink)]">This link may have expired</p>
                <p className="mt-1 text-[13px] leading-6 text-[var(--ink-2)]">{message}</p>
              </div>
            </div>
          </div>
        ) : null}
        {status === 'loading' ? (
          <div className="space-y-3">
            <div className="h-3 w-1/3 rounded-full bg-[var(--line)]" />
            <div className="h-3 w-2/3 rounded-full bg-[var(--line)]" />
            <div className="h-3 w-1/2 rounded-full bg-[var(--line)]" />
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <button type="button" className="btn btn-purple btn-lg w-full justify-center" onClick={() => onNavigate('login')}>
              {status === 'success' ? 'Create your first role' : 'Go to sign in'} <span className="arrow">→</span>
            </button>
            {status === 'success' ? (
              <button type="button" className="btn btn-outline btn-lg w-full justify-center" onClick={() => onNavigate('demo')}>
                Take the 2-minute tour <span className="arrow">→</span>
              </button>
            ) : null}
          </div>
        )}
      </AuthCard>
    </FlowLayout>
  );
};
