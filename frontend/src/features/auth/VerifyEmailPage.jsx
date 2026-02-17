import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';

import { auth } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';
import { Button, Spinner } from '../../shared/ui/TaaliPrimitives';

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
    <div className="min-h-screen bg-[var(--taali-surface)] flex flex-col">
      <nav className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="border-2 border-[var(--taali-border)] p-8 text-center max-w-md w-full bg-[var(--taali-surface)]">
          {status === 'loading' && (
            <>
              <Spinner size={48} className="mx-auto mb-4" />
              <h2 className="text-2xl font-bold mb-2">Verifying your email...</h2>
              <p className="text-sm text-[var(--taali-muted)]">Please wait a moment.</p>
            </>
          )}
          {status === 'success' && (
            <>
              <CheckCircle size={48} className="mx-auto mb-4 text-[var(--taali-purple)]" />
              <h2 className="text-2xl font-bold mb-2">Email verified!</h2>
              <p className="text-sm text-[var(--taali-muted)] mb-6">{message}</p>
              <Button variant="primary" className="w-full" onClick={() => onNavigate('login')}>
                Sign In
              </Button>
            </>
          )}
          {status === 'error' && (
            <>
              <AlertTriangle size={48} className="mx-auto mb-4 text-[var(--taali-warning)]" />
              <h2 className="text-2xl font-bold mb-2">Verification failed</h2>
              <p className="text-sm text-[var(--taali-muted)] mb-6">{message}</p>
              <Button variant="primary" className="w-full" onClick={() => onNavigate('login')}>
                Go to Sign In
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
