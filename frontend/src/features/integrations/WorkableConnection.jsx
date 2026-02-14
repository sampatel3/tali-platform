import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle, Loader2 } from 'lucide-react';

import { organizations as orgsApi } from '../../shared/api';

export const ConnectWorkableButton = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleClick = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await orgsApi.getWorkableAuthorizeUrl();
      if (res.data?.url) window.location.href = res.data.url;
      else setError('Could not get authorization URL');
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to connect');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button
        type="button"
        onClick={handleClick}
        disabled={loading}
        className="flex items-center gap-2 px-4 py-2 font-mono text-sm font-bold border-2 border-black bg-black text-white hover:bg-gray-800 disabled:opacity-60"
      >
        {loading ? <Loader2 size={18} className="animate-spin" /> : null}
        {loading ? 'Redirecting…' : 'Connect Workable'}
      </button>
      {error && <p className="font-mono text-sm text-red-600 mt-2">{error}</p>}
    </div>
  );
};

export const WorkableCallbackPage = ({ code, onNavigate }) => {
  const [status, setStatus] = useState('connecting');
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!code) {
      setStatus('error');
      setMessage('Missing authorization code');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await orgsApi.connectWorkable(code);
        if (!cancelled) {
          setStatus('success');
          onNavigate('settings', { replace: true });
        }
      } catch (err) {
        if (!cancelled) {
          setStatus('error');
          setMessage(err?.response?.data?.detail || err.message || 'Connection failed');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, onNavigate]);

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="border-2 border-black p-8 max-w-md text-center">
        {status === 'connecting' && (
          <>
            <Loader2 size={32} className="animate-spin mx-auto mb-4" style={{ color: '#9D00FF' }} />
            <p className="font-mono text-sm">Connecting Workable…</p>
          </>
        )}
        {status === 'success' && (
          <>
            <CheckCircle size={32} className="mx-auto mb-4 text-green-600" />
            <p className="font-mono text-sm">Workable connected. Taking you to Settings…</p>
          </>
        )}
        {status === 'error' && (
          <>
            <AlertTriangle size={32} className="mx-auto mb-4 text-red-600" />
            <p className="font-mono text-sm text-red-600 mb-4">{message}</p>
            <button
              type="button"
              onClick={() => onNavigate('settings')}
              className="px-4 py-2 font-mono text-sm font-bold border-2 border-black hover:bg-gray-100"
            >
              Back to Settings
            </button>
          </>
        )}
      </div>
    </div>
  );
};
