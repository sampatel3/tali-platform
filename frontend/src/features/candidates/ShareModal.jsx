import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Copy, Link2, RefreshCw, Trash2, X } from 'lucide-react';

import { roles as rolesApi } from '../../shared/api';

const buildShareUrl = (mode, token) => {
  if (!token) return '';
  if (typeof window === 'undefined') return '';
  // HANDOFF v2 §3 — recipients land on /share/:token, the public
  // route on the backend that gates by expiry / view-count and tells
  // the frontend which mode to render in.
  return `${window.location.origin}/share/${token}`;
};

const EXPIRY_OPTIONS = [
  { value: '7d', label: 'In 7 days' },
  { value: '24h', label: 'In 24 hours' },
  { value: '30d', label: 'In 30 days' },
  { value: 'single-view', label: 'Single view, then expires' },
];

const MODE_OPTIONS = [
  {
    value: 'client',
    label: 'Client view',
    sub: 'Score, recommendation, and evidence — no prompts.',
  },
  {
    value: 'recruiter',
    label: 'Recruiter view',
    sub: 'Full report with timeline, prompts, and AI usage.',
  },
];

const formatExpiryLabel = (link) => {
  if (link?.revoked) return 'Revoked';
  if (link?.expired) return 'Expired';
  if (link?.single_view_consumed) return 'Used';
  if (link?.expiry_preset) {
    const match = EXPIRY_OPTIONS.find((opt) => opt.value === link.expiry_preset);
    if (match) return match.label;
  }
  if (link?.expires_at) {
    const date = new Date(link.expires_at);
    if (!Number.isNaN(date.getTime())) return `Until ${date.toLocaleString()}`;
  }
  return '—';
};

// ShareModal — HANDOFF v2 §3 multi-link contract.
// Mints a new share link per recruiter click, lists all existing links
// (active + revoked + expired) with revoke + copy controls. Single-
// view mode invalidates after the first GET against /share/:token.
//
// `initialMode` lets the caller pre-select 'interview' (internal panel)
// or 'client' (external) when the modal opens — e.g. the candidate
// header has separate "Share internally" + "Share with client" buttons
// that open the same modal but on the right tab.
export const ShareModal = ({ open, onClose, applicationId, initialMode = 'client' }) => {
  const [mode, setMode] = useState(initialMode);
  const [expiry, setExpiry] = useState('7d');

  // Re-sync mode when the modal opens with a different initial mode
  // (e.g. user closes the client tab, then clicks "Share internally").
  useEffect(() => {
    if (open) setMode(initialMode);
  }, [open, initialMode]);
  const [links, setLinks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const [copiedKey, setCopiedKey] = useState(null);
  const [busyLinkId, setBusyLinkId] = useState(null);

  const refreshLinks = useCallback(async () => {
    if (!applicationId) return;
    setError('');
    setLoading(true);
    try {
      const res = await rolesApi.listApplicationShareLinks(applicationId);
      const next = Array.isArray(res?.data?.links) ? res.data.links : [];
      setLinks(next);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not load share links.');
    } finally {
      setLoading(false);
    }
  }, [applicationId]);

  useEffect(() => {
    if (!open) return undefined;
    setError('');
    setCopiedKey(null);
    void refreshLinks();
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, refreshLinks, onClose]);

  const handleCreate = useCallback(async () => {
    if (!applicationId || creating) return;
    setError('');
    setCreating(true);
    try {
      const res = await rolesApi.createApplicationShareLink(applicationId, { mode, expiry });
      const link = res?.data;
      if (link?.id) {
        setLinks((prev) => [link, ...prev.filter((row) => row.id !== link.id)]);
        const url = buildShareUrl(link.mode, link.token);
        if (url) {
          try {
            await navigator.clipboard.writeText(url);
            setCopiedKey(`row-${link.id}`);
            setTimeout(() => setCopiedKey((curr) => (curr === `row-${link.id}` ? null : curr)), 2000);
          } catch {
            // Copy failure is non-fatal — recipient can copy from the row.
          }
        }
      } else {
        await refreshLinks();
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not create share link.');
    } finally {
      setCreating(false);
    }
  }, [applicationId, mode, expiry, creating, refreshLinks]);

  const handleRevoke = useCallback(async (linkId) => {
    setError('');
    setBusyLinkId(linkId);
    try {
      const res = await rolesApi.revokeShareLink(linkId);
      const updated = res?.data;
      if (updated?.id) {
        setLinks((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
      } else {
        await refreshLinks();
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not revoke share link.');
    } finally {
      setBusyLinkId(null);
    }
  }, [refreshLinks]);

  const handleCopy = useCallback(async (key, text) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey((curr) => (curr === key ? null : curr)), 2000);
    } catch {
      setError('Copy failed. Select the link and copy manually.');
    }
  }, []);

  const activeLinks = useMemo(() => links.filter((row) => row?.active), [links]);
  const inactiveLinks = useMemo(() => links.filter((row) => !row?.active), [links]);

  if (!open) return null;

  return (
    <div className="mc-share-overlay" role="dialog" aria-modal="true" aria-label="Share candidate report" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className="mc-share-card">
        <header className="mc-share-head">
          <div>
            <div className="mc-kicker">CANDIDATE REPORT · SHARE LINKS</div>
            <h2 className="mc-share-title">Share this candidate report</h2>
          </div>
          <button type="button" className="mc-icon-btn" onClick={onClose} aria-label="Close share modal">
            <X size={16} strokeWidth={1.7} />
          </button>
        </header>

        <div className="mc-share-body">
          {/* Create-link panel */}
          <fieldset className="mc-share-modes" aria-label="View mode">
            {MODE_OPTIONS.map((opt) => (
              <ModeToggle
                key={opt.value}
                mode={mode}
                setMode={setMode}
                value={opt.value}
                label={opt.label}
                sub={opt.sub}
              />
            ))}
          </fieldset>

          <div className="mc-share-expiry">
            <label className="mc-share-label">
              Expires in
              <select
                className="mc-share-select"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
              >
                {EXPIRY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn btn-purple btn-sm"
              onClick={handleCreate}
              disabled={creating || !applicationId}
            >
              <Link2 size={13} strokeWidth={1.8} />
              {creating ? 'Creating…' : 'Create link'}
            </button>
          </div>

          {error ? <div className="mc-share-error">{error}</div> : null}

          {/* Active links — HANDOFF v2 §3: visible in the report footer
              with revoke + new-link controls. */}
          <div className="mc-share-links">
            <div className="mc-share-links-head">
              <span className="mc-kicker is-mute">ACTIVE LINKS</span>
              <span className="mc-share-links-count">
                {loading ? 'Loading…' : `${activeLinks.length} active`}
              </span>
            </div>
            {activeLinks.length === 0 && !loading ? (
              <p className="mc-share-empty">No active links yet. Create one above.</p>
            ) : (
              <ul className="mc-share-link-list">
                {activeLinks.map((link) => {
                  const url = buildShareUrl(link.mode, link.token);
                  const copyKey = `row-${link.id}`;
                  return (
                    <li key={link.id} className="mc-share-link-row">
                      <div className="mc-share-link-meta">
                        <span className={`mc-share-link-mode ${link.mode}`}>{link.mode}</span>
                        <span className="mc-share-link-expiry">{formatExpiryLabel(link)}</span>
                        {link.view_count > 0 ? (
                          <span className="mc-share-link-views">{link.view_count} view{link.view_count === 1 ? '' : 's'}</span>
                        ) : null}
                      </div>
                      <input
                        readOnly
                        value={url}
                        className="mc-share-link-input"
                        aria-label={`${link.mode} share link`}
                      />
                      <button
                        type="button"
                        className="mc-share-link-btn"
                        onClick={() => handleCopy(copyKey, url)}
                        disabled={!url}
                      >
                        <Copy size={13} strokeWidth={1.8} />
                        {copiedKey === copyKey ? 'Copied' : 'Copy'}
                      </button>
                      <button
                        type="button"
                        className="mc-share-link-btn is-ghost"
                        onClick={() => handleRevoke(link.id)}
                        disabled={busyLinkId === link.id}
                        title="Revoke this link — it stops working immediately"
                      >
                        <Trash2 size={13} strokeWidth={1.8} />
                        {busyLinkId === link.id ? 'Revoking…' : 'Revoke'}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Audit history — revoked / expired / single-view-consumed.
              Surfaced so recruiters can see who shared what and when. */}
          {inactiveLinks.length > 0 ? (
            <div className="mc-share-links is-history">
              <div className="mc-share-links-head">
                <span className="mc-kicker is-mute">HISTORY</span>
                <button
                  type="button"
                  className="mc-share-link-btn is-ghost"
                  onClick={() => void refreshLinks()}
                  disabled={loading}
                >
                  <RefreshCw size={13} strokeWidth={1.8} className={loading ? 'animate-spin' : ''} />
                  Refresh
                </button>
              </div>
              <ul className="mc-share-link-list">
                {inactiveLinks.map((link) => (
                  <li key={link.id} className="mc-share-link-row is-inactive">
                    <div className="mc-share-link-meta">
                      <span className={`mc-share-link-mode ${link.mode}`}>{link.mode}</span>
                      <span className="mc-share-link-expiry">{formatExpiryLabel(link)}</span>
                      {link.view_count > 0 ? (
                        <span className="mc-share-link-views">{link.view_count} view{link.view_count === 1 ? '' : 's'}</span>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <p className="mc-share-foot">
            Recipients see only what the selected mode allows. Revoking a link invalidates anyone
            holding it; single-view links auto-revoke after the first open.
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
