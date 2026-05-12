import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy, Link2, X } from 'lucide-react';

import { roles as rolesApi } from '../../shared/api';

// HANDOFF v2 §3 — share-link contract.
// Recipients open ``/c/{appId}?view=<interview|client>&k=<token>`` which
// the SPA already routes to ``CandidateStandingReportPage``; that page
// resolves the token via ``GET /api/v1/applications/share/{token}``,
// which now also accepts ``share_links`` tokens (not just the legacy
// single-token column).
const buildShareUrl = (applicationId, mode, token) => {
  if (!applicationId || !token) return '';
  if (typeof window === 'undefined') return '';
  const view = mode === 'client' ? 'client' : 'interview';
  return `${window.location.origin}/c/${applicationId}?view=${view}&k=${token}`;
};

const INTERNAL_EXPIRY_DAYS = 7;
const CLIENT_DAYS_MIN = 1;
const CLIENT_DAYS_MAX = 14;
const CLIENT_DAYS_DEFAULT = 7;

const clampClientDays = (value) => {
  const num = Number(value);
  if (!Number.isFinite(num)) return CLIENT_DAYS_DEFAULT;
  return Math.max(CLIENT_DAYS_MIN, Math.min(CLIENT_DAYS_MAX, Math.round(num)));
};

const TITLE = {
  internal: 'Internal share link',
  client: 'Client share link',
};

const SUBTITLE = {
  internal: `Full report for hiring panel. Expires quietly in ${INTERNAL_EXPIRY_DAYS} days.`,
  client: 'Score, recommendation, and evidence — no recruiter prompts.',
};

// One-shot share dialog. ``kind === 'internal'`` mints a recruiter-mode
// link with a fixed 7-day expiry the moment the dialog opens.
// ``kind === 'client'`` shows a 1–14 day picker first, mints on submit.
// No history / revoke surfaces — recruiters mint new links instead.
export const ShareModal = ({ open, onClose, applicationId, kind = 'internal' }) => {
  const [days, setDays] = useState(CLIENT_DAYS_DEFAULT);
  const [link, setLink] = useState(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  // Track which (kind, applicationId) combo we've auto-created for, so
  // re-opens with a different kind retrigger the auto-create instead of
  // reusing the stale internal link.
  const autoCreatedKeyRef = useRef(null);

  const createLink = useCallback(async (overrides = {}) => {
    if (!applicationId) return null;
    setError('');
    setCreating(true);
    try {
      const payload = kind === 'internal'
        ? { mode: 'recruiter', expiry_days: INTERNAL_EXPIRY_DAYS }
        : { mode: 'client', expiry_days: clampClientDays(overrides.days ?? days) };
      const res = await rolesApi.createApplicationShareLink(applicationId, payload);
      const next = res?.data || null;
      if (next?.token) {
        setLink(next);
        const url = buildShareUrl(applicationId, next.mode, next.token);
        if (url) {
          try {
            await navigator.clipboard.writeText(url);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          } catch {
            // Copy failure is non-fatal — recipient can copy from the row.
          }
        }
      }
      return next;
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not create share link.');
      return null;
    } finally {
      setCreating(false);
    }
  }, [applicationId, days, kind]);

  useEffect(() => {
    if (!open) {
      autoCreatedKeyRef.current = null;
      setLink(null);
      setError('');
      setCopied(false);
      setDays(CLIENT_DAYS_DEFAULT);
      return undefined;
    }
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || kind !== 'internal' || !applicationId) return;
    const key = `${kind}:${applicationId}`;
    if (autoCreatedKeyRef.current === key) return;
    autoCreatedKeyRef.current = key;
    void createLink();
  }, [open, kind, applicationId, createLink]);

  const handleCopy = useCallback(async (text) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('Copy failed. Select the link and copy manually.');
    }
  }, []);

  if (!open) return null;

  const url = link ? buildShareUrl(applicationId, link.mode, link.token) : '';

  return (
    <div
      className="mc-share-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={TITLE[kind]}
      onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}
    >
      <div className="mc-share-card mc-share-card-sm">
        <header className="mc-share-head">
          <div>
            <div className="mc-kicker">CANDIDATE REPORT · SHARE</div>
            <h2 className="mc-share-title">{TITLE[kind]}</h2>
          </div>
          <button type="button" className="mc-icon-btn" onClick={onClose} aria-label="Close share dialog">
            <X size={16} strokeWidth={1.7} />
          </button>
        </header>

        <div className="mc-share-body">
          <p className="mc-share-foot">{SUBTITLE[kind]}</p>

          {kind === 'client' && !link ? (
            <div className="mc-share-expiry">
              <label className="mc-share-label">
                Expires in
                <input
                  type="number"
                  min={CLIENT_DAYS_MIN}
                  max={CLIENT_DAYS_MAX}
                  value={days}
                  onChange={(e) => setDays(clampClientDays(e.target.value))}
                  className="mc-share-select"
                  style={{ width: 70 }}
                />
                days
              </label>
              <button
                type="button"
                className="btn btn-purple btn-sm"
                onClick={() => void createLink({ days })}
                disabled={creating || !applicationId}
              >
                <Link2 size={13} strokeWidth={1.8} />
                {creating ? 'Creating…' : 'Generate link'}
              </button>
            </div>
          ) : null}

          {error ? <div className="mc-share-error">{error}</div> : null}

          {link ? (
            <div className="mc-share-link">
              <Link2 size={14} strokeWidth={1.8} />
              <input
                readOnly
                value={url}
                className="mc-share-link-input"
                aria-label={`${link.mode} share link`}
                onFocus={(e) => e.target.select()}
              />
              <button
                type="button"
                className="mc-share-link-btn"
                onClick={() => handleCopy(url)}
                disabled={!url}
              >
                {copied ? <Check size={13} strokeWidth={1.8} /> : <Copy size={13} strokeWidth={1.8} />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
          ) : null}

          {kind === 'internal' && creating ? (
            <p className="mc-share-help">Creating link…</p>
          ) : null}
        </div>
      </div>
    </div>
  );
};

export default ShareModal;
