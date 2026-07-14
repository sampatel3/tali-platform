import React, { useCallback, useEffect, useState } from 'react';
import { ChevronDown, Copy, ExternalLink, Globe, Linkedin, Mail, Rss } from 'lucide-react';

import { roles as rolesApi } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { Spinner } from '../../shared/ui/TaaliPrimitives';

// "Live since 12 Jun 2026" — a plain, recruiter-facing date (no time). Falls
// back to nothing when the page carries no published_at.
function publishedSince(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

// "Distribute this role" panel on the published-role view. Everything here
// produces copy-paste / one-click-out artefacts that point at the role's
// EXISTING public job page — there is NO LinkedIn API, scraping, or automation.
//
//  - LinkedIn post: an editable, copy-paste-ready draft.
//  - Share buttons: open LinkedIn's share composer, an email draft, or copy the
//    raw apply link.
//  - Careers feed URL: the org's XML feed for boards like Indeed / Google Jobs.
//
// Only meaningful once the role is published (has a public job page). Until
// then the panel shows a gentle "publish to distribute" note.

function useCopy() {
  const { showToast } = useToast();
  return useCallback(
    async (value, label = 'Copied to clipboard.') => {
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        showToast(label, 'success');
      } catch (err) {
        showToast('Copy failed — select and copy the text manually.', 'error');
      }
    },
    [showToast],
  );
}

function FeedRow({ url }) {
  const copy = useCopy();
  if (!url) return null;
  return (
    <div className="src-string">
      <div className="src-string-head">
        <span className="src-string-label">Careers feed</span>
        <span className="src-string-hint">for Indeed / Google Jobs</span>
        <button type="button" className="btn btn-outline btn-sm src-copy" onClick={() => copy(url, 'Feed URL copied.')}>
          <Copy size={12} /> Copy
        </button>
      </div>
      <code className="src-string-value">{url}</code>
    </div>
  );
}

// Status view: is this job live, where can it go, and since when. Makes
// "published vs not" unmistakable before the copy-paste artefacts.
function DistStatus({ data }) {
  const share = data?.share_urls || {};
  const since = publishedSince(data?.published_at);
  const channels = [
    { key: 'linkedin', Icon: Linkedin, label: 'LinkedIn', on: Boolean(share.linkedin) },
    { key: 'email', Icon: Mail, label: 'Email', on: Boolean(share.email) },
    { key: 'apply', Icon: Globe, label: 'Public apply page', on: Boolean(share.apply_url || data?.apply_url) },
    { key: 'feed', Icon: Rss, label: 'Indeed / Google Jobs feed', on: Boolean(data?.feed_url) },
  ];
  return (
    <div className="dist-status">
      <div className="dist-status-head">
        <span className="dist-live-pill"><span className="dot" /> Live</span>
        {since ? <span className="dist-status-since">Live since {since}</span> : null}
      </div>
      <div className="dist-status-line">
        This role is published — candidates can apply on your public job page, and it&apos;s
        carried in your careers feed for the job boards to pull.
      </div>
      <div className="dist-channels">
        {channels.map(({ key, Icon, label, on }) => (
          <span key={key} className={`dist-channel ${on ? '' : 'off'}`}>
            <Icon size={11} /> {label}
          </span>
        ))}
      </div>
    </div>
  );
}

function Artefacts({ data }) {
  const copy = useCopy();
  const [post, setPost] = useState(data?.linkedin_post || '');

  useEffect(() => {
    setPost(data?.linkedin_post || '');
  }, [data]);

  const share = data?.share_urls || {};

  return (
    <div className="src-results">
      <DistStatus data={data} />
      <div className="src-tool">
        <div className="src-tool-head">
          <span className="src-tool-title">LinkedIn post</span>
          <button type="button" className="btn btn-primary btn-sm src-copy" onClick={() => copy(post, 'Post copied.')}>
            <Copy size={12} /> Copy post
          </button>
        </div>
        <textarea
          className="taali-input src-profile dist-post"
          rows={8}
          value={post}
          onChange={(e) => setPost(e.target.value)}
          aria-label="LinkedIn post draft"
        />
      </div>

      <div className="src-tool">
        <div className="src-tool-head">
          <span className="src-tool-title">Share</span>
        </div>
        <div className="dist-share">
          {share.linkedin ? (
            <a className="btn btn-outline btn-sm" href={share.linkedin} target="_blank" rel="noreferrer">
              <ExternalLink size={12} /> Open in LinkedIn
            </a>
          ) : null}
          {share.email ? (
            <a className="btn btn-outline btn-sm" href={share.email}>
              <Mail size={12} /> Email
            </a>
          ) : null}
          {share.apply_url ? (
            <button type="button" className="btn btn-outline btn-sm" onClick={() => copy(share.apply_url, 'Apply link copied.')}>
              <Copy size={12} /> Copy apply link
            </button>
          ) : null}
        </div>
      </div>

      <FeedRow url={data?.feed_url} />
    </div>
  );
}

export function DistributeRolePanel({ roleId, defaultOpen = false }) {
  const { showToast } = useToast();
  const [open, setOpen] = useState(defaultOpen);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const { data: body } = await rolesApi.distribution(roleId);
      setData(body);
    } catch (err) {
      setError(true);
      showToast('Could not load distribution options.', 'error');
    } finally {
      // Mark the fetch as attempted regardless of outcome so a failure shows a
      // Retry affordance instead of the effect re-firing load() every render.
      setLoaded(true);
      setLoading(false);
    }
  }, [roleId, showToast]);

  // Fetch once, the first time the panel is opened. On failure `loaded` still
  // flips true (in finally), so this does not auto-retry — the user retries.
  useEffect(() => {
    if (open && !loaded && !loading) load();
  }, [open, loaded, loading, load]);

  const published = data?.published === true;

  return (
    <div className="role-sec src-panel">
      <button
        type="button"
        className={`src-panel-toggle ${open ? 'open' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div className="role-sec-title">
          <span className="marker">DX</span>
          Distribute this role
        </div>
        <ChevronDown className="caret" size={12} />
      </button>

      {open ? (
        <div className="src-panel-body">
          <p className="src-help">
            Copy-paste and one-click helpers to post this role out to LinkedIn and the job boards.
            Everything points at your public job page — nothing is posted or automated for you.
          </p>

          {loading ? (
            <div className="src-warn">
              <Spinner size={12} className="!text-current" /> Loading…
            </div>
          ) : null}

          {!loading && error ? (
            <div className="src-warn">
              Could not load distribution options.{' '}
              <button type="button" className="taali-text-btn src-retry-link" onClick={() => load()}>
                Retry
              </button>
            </div>
          ) : null}

          {!error && loaded && !published ? (
            <div className="src-warn">Publish this role to distribute it — a public job page is created on publish.</div>
          ) : null}

          {!error && loaded && published ? <Artefacts data={data} /> : null}
        </div>
      ) : null}
    </div>
  );
}

export default DistributeRolePanel;
