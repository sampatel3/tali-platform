import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle, Loader2 } from 'lucide-react';

import { organizations as orgsApi } from '../../shared/api';

const normalizeWorkableError = (input) => {
  const raw = (input || '').toString();
  const lower = raw.toLowerCase();
  // Keep infra/ops details (deploy, migration, railway) out of the
  // recruiter-facing callback page — same guard as the Settings copy.
  if (lower.includes('deploy') || lower.includes('migration') || lower.includes('endpoint not available') || lower.includes('railway')) {
    return 'This feature is temporarily unavailable. Please try again later or contact support.';
  }
  if (lower.includes('not configured')) {
    return 'Workable integration is not yet set up for this account. Please contact support to enable it.';
  }
  if (lower.includes('disabled for mvp')) {
    return 'Workable integration is not available on your current plan. Contact support to upgrade.';
  }
  if (lower.includes('oauth failed')) {
    return 'We couldn\'t connect to Workable. Try again, or contact support if it keeps failing.';
  }
  return raw || 'Workable connection failed.';
};

export const ConnectWorkableButton = ({ authorizeUrl = '', setupError = '', onClick = null }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleClick = async () => {
    if (onClick) {
      onClick();
      return;
    }
    setLoading(true);
    setError('');
    if (setupError) {
      setError(normalizeWorkableError(setupError));
      setLoading(false);
      return;
    }
    if (authorizeUrl) {
      window.location.href = authorizeUrl;
      return;
    }
    try {
      const res = await orgsApi.getWorkableAuthorizeUrl();
      if (res.data?.url) window.location.href = res.data.url;
      else setError('Couldn\'t start the Workable connection. Please try again.');
    } catch (err) {
      setError(normalizeWorkableError(err?.response?.data?.detail || err.message));
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
        className="btn btn-purple btn-sm"
      >
        {loading ? <Loader2 size={16} className="animate-spin" /> : null}
        {loading ? 'Redirecting…' : 'Connect Workable'}
      </button>
      {setupError && !error && <p className="settings-hint mt-2" style={{ color: 'var(--taali-danger)' }}>{normalizeWorkableError(setupError)}</p>}
      {error && <p className="settings-hint mt-2" style={{ color: 'var(--taali-danger)' }}>{error}</p>}
    </div>
  );
};

export const WorkableCallbackPage = ({
  code,
  error,
  errorDescription,
  onNavigate,
}) => {
  const [status, setStatus] = useState('connecting');
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (error) {
      setStatus('error');
      setMessage('Workable couldn\'t complete the connection. Please try again, or contact support if it keeps failing.');
      return;
    }
    if (!code) {
      setStatus('error');
      setMessage('Missing authorization code from Workable callback.');
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
          setMessage(normalizeWorkableError(err?.response?.data?.detail || err.message));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, error, errorDescription, onNavigate]);

  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ background: 'var(--taali-bg, var(--bg))' }}>
      <div
        className="p-8 max-w-md text-center"
        style={{
          background: 'var(--taali-card-bg, var(--surface))',
          border: '1px solid var(--taali-border-soft, var(--line))',
          borderRadius: 'var(--radius-card, 22px)',
          boxShadow: 'var(--taali-shadow-soft)',
        }}
      >
        {status === 'connecting' && (
          <>
            <Loader2 size={32} className="animate-spin mx-auto mb-4 text-[var(--taali-purple)]" />
            <p className="text-sm text-[var(--taali-text)]">Connecting Workable…</p>
          </>
        )}
        {status === 'success' && (
          <>
            <CheckCircle size={32} className="mx-auto mb-4 text-[var(--taali-purple)]" />
            <p className="text-sm text-[var(--taali-text)]">Workable connected. Taking you to Settings…</p>
          </>
        )}
        {status === 'error' && (
          <>
            <AlertTriangle size={32} className="mx-auto mb-4" style={{ color: 'var(--taali-danger)' }} />
            <p className="text-sm mb-4" style={{ color: 'var(--taali-danger)' }}>{message}</p>
            <button
              type="button"
              onClick={() => onNavigate('settings')}
              className="btn btn-outline btn-sm"
            >
              Back to Settings
            </button>
          </>
        )}
      </div>
    </div>
  );
};
