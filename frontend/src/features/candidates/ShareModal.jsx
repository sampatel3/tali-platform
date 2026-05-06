import React, { useEffect, useMemo, useState } from 'react';
import { Copy, Link2, RefreshCw, X } from 'lucide-react';

import { roles as rolesApi } from '../../shared/api';

const buildShareUrl = (mode, token) => {
  if (!token) return '';
  if (typeof window === 'undefined') return '';
  if (mode === 'client') {
    return `${window.location.origin}/c/${token}?view=client&showcase=1`;
  }
  return `${window.location.origin}/candidates/${token}?view=interview&k=${token}`;
};

const EXPIRY_OPTIONS = [
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'single', label: 'Single view' },
];

// ShareModal — MVP single-link with rotate. Reuses the existing
// CandidateApplication.report_share_token. The expiry picker is rendered
// disabled with a "coming soon" hint until the multi-link backend ships
// (see plan for the share_links table).
export const ShareModal = ({ open, onClose, applicationId, initialToken }) => {
  const [token, setToken] = useState(initialToken || '');
  const [mode, setMode] = useState('client');
  const [expiry, setExpiry] = useState('30d');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copiedKey, setCopiedKey] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    setError('');
    setToken(initialToken || '');
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, initialToken, onClose]);

  useEffect(() => {
    if (!open || token || !applicationId) return;
    let cancelled = false;
    setBusy(true);
    setError('');
    rolesApi
      .getApplicationShareLink(applicationId)
      .then((res) => {
        if (cancelled) return;
        const next = res?.data?.token || res?.data?.share_token || '';
        if (next) setToken(next);
      })
      .catch((err) => {
        if (!cancelled) setError(err?.response?.data?.detail || 'Could not generate a share link.');
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, applicationId, token]);

  const handleRotate = async () => {
    if (!applicationId) return;
    setBusy(true);
    setError('');
    try {
      const res = await rolesApi.getApplicationShareLink(applicationId);
      const next = res?.data?.token || res?.data?.share_token || '';
      setToken(next);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not rotate the share link.');
    } finally {
      setBusy(false);
    }
  };

  const url = useMemo(() => buildShareUrl(mode, token), [mode, token]);

  const handleCopy = async (key, text) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey((curr) => (curr === key ? null : curr)), 2000);
    } catch {
      setError('Copy failed. Select the link and copy manually.');
    }
  };

  if (!open) return null;

  return (
    <div className="mc-share-overlay" role="dialog" aria-modal="true" aria-label="Share candidate report" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className="mc-share-card">
        <header className="mc-share-head">
          <div>
            <div className="mc-kicker">CLIENT-SHAREABLE LINK</div>
            <h2 className="mc-share-title">Share this candidate report</h2>
          </div>
          <button type="button" className="mc-icon-btn" onClick={onClose} aria-label="Close share modal">
            <X size={16} strokeWidth={1.7} />
          </button>
        </header>

        <div className="mc-share-body">
          <fieldset className="mc-share-modes" aria-label="View mode">
            <ModeToggle mode={mode} setMode={setMode} value="client" label="Client view" sub="Score, recommendation, and evidence — no prompts." />
            <ModeToggle mode={mode} setMode={setMode} value="recruiter" label="Recruiter view" sub="Full report with timeline, prompts, and AI usage." />
          </fieldset>

          <div className="mc-share-link">
            <Link2 size={14} strokeWidth={1.8} />
            <input className="mc-share-link-input" readOnly value={url} aria-label="Share link" />
            <button
              type="button"
              className="mc-share-link-btn"
              onClick={() => handleCopy('main', url)}
              disabled={!url || busy}
            >
              <Copy size={13} strokeWidth={1.8} />
              {copiedKey === 'main' ? 'Copied' : 'Copy'}
            </button>
            <button
              type="button"
              className="mc-share-link-btn is-ghost"
              onClick={handleRotate}
              disabled={busy || !applicationId}
              title="Rotate the link — the previous link stops working immediately"
            >
              <RefreshCw size={13} strokeWidth={1.8} />
              Rotate
            </button>
          </div>

          <div className="mc-share-expiry">
            <label className="mc-share-label">
              Expires in
              <select
                className="mc-share-select"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
                disabled
                aria-disabled="true"
                title="Multi-expiry support is rolling out — backend ticket pending."
              >
                {EXPIRY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </label>
            <p className="mc-share-help">
              For now every link stays live until you rotate. 24 h / 7 d / single-view options ship with
              the share-links backend.
            </p>
          </div>

          {error ? <div className="mc-share-error">{error}</div> : null}

          <p className="mc-share-foot">
            Recipients see only what the selected mode allows. Rotating the link revokes the previous one
            and invalidates anyone holding it.
          </p>
        </div>
      </div>
    </div>
  );
};

const ModeToggle = ({ mode, setMode, value, label, sub }) => (
  <button
    type="button"
    role="radio"
    aria-checked={mode === value}
    className={`mc-share-mode ${mode === value ? 'on' : ''}`.trim()}
    onClick={() => setMode(value)}
  >
    <span className="mc-share-mode-label">{label}</span>
    <span className="mc-share-mode-sub">{sub}</span>
  </button>
);

export default ShareModal;
