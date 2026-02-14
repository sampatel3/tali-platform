import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle, Loader2 } from 'lucide-react';

import { auth } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';

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
    <div className="min-h-screen bg-white flex flex-col">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="border-2 border-black p-8 text-center max-w-md w-full">
          {status === 'loading' && (
            <>
              <Loader2 size={48} className="mx-auto mb-4 animate-spin" style={{ color: '#9D00FF' }} />
              <h2 className="text-2xl font-bold mb-2">Verifying your email...</h2>
              <p className="font-mono text-sm text-gray-600">Please wait a moment.</p>
            </>
          )}
          {status === 'success' && (
            <>
              <CheckCircle size={48} className="mx-auto mb-4" style={{ color: '#9D00FF' }} />
              <h2 className="text-2xl font-bold mb-2">Email verified!</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">{message}</p>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={() => onNavigate('login')}
              >
                Sign In
              </button>
            </>
          )}
          {status === 'error' && (
            <>
              <AlertTriangle size={48} className="mx-auto mb-4 text-amber-500" />
              <h2 className="text-2xl font-bold mb-2">Verification failed</h2>
              <p className="font-mono text-sm text-gray-600 mb-6">{message}</p>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors"
                style={{ backgroundColor: '#9D00FF' }}
                onClick={() => onNavigate('login')}
              >
                Go to Sign In
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
