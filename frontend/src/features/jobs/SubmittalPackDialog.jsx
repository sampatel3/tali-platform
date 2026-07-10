import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Dialog, Button } from '../../shared/ui/TaaliPrimitives';
import { roles as rolesApi } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { getErrorMessage } from '../candidates/candidatesUiUtils';

const EXPIRY_OPTIONS = [
  { key: '24h', label: '24 hours' },
  { key: '7d', label: '7 days' },
  { key: '30d', label: '30 days' },
];

const candidateName = (app) =>
  app?.candidate_name || app?.candidate_email || `Candidate #${app?.id || '—'}`;

// WS2 — curated multi-candidate client submittal. Freezes a client-safe
// snapshot of the selected candidates for one role behind a public link, with
// an optional per-candidate one-line note. Lists existing packs with revoke.
export default function SubmittalPackDialog({
  open,
  roleId,
  roleTitle,
  applications = [],
  onClose,
}) {
  const { showToast } = useToast();
  const [title, setTitle] = useState('');
  const [expiresIn, setExpiresIn] = useState('7d');
  const [notes, setNotes] = useState({});
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(null); // { token, url_path, expires_at }
  const [packs, setPacks] = useState([]);
  const [loadingPacks, setLoadingPacks] = useState(false);

  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  const createdUrl = created ? `${origin}${created.url_path}` : '';

  const loadPacks = useCallback(async () => {
    if (!roleId) return;
    setLoadingPacks(true);
    try {
      const res = await rolesApi.listSubmittalPacks(roleId);
      setPacks(res?.data?.packs || []);
    } catch (err) {
      // Non-fatal: the list is audit chrome, minting still works.
      setPacks([]);
    } finally {
      setLoadingPacks(false);
    }
  }, [roleId]);

  useEffect(() => {
    if (!open) return;
    setTitle(roleTitle || '');
    setExpiresIn('7d');
    setNotes({});
    setCreated(null);
    loadPacks();
  }, [open, roleTitle, loadPacks]);

  const canCreate = applications.length > 0 && applications.length <= 20 && !creating;

  const handleCreate = useCallback(async () => {
    if (!canCreate) return;
    setCreating(true);
    try {
      const noteMap = {};
      applications.forEach((app) => {
        const n = String(notes[app.id] || '').trim();
        if (n) noteMap[String(app.id)] = n;
      });
      const res = await rolesApi.createSubmittalPack(roleId, {
        applicationIds: applications.map((a) => a.id),
        title: String(title || '').trim() || roleTitle || null,
        notes: Object.keys(noteMap).length > 0 ? noteMap : null,
        expiresIn,
      });
      setCreated(res?.data || null);
      loadPacks();
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to create submittal pack.'), 'error');
    } finally {
      setCreating(false);
    }
  }, [applications, notes, roleId, title, roleTitle, expiresIn, canCreate, loadPacks, showToast]);

  const handleCopy = useCallback(async () => {
    if (!createdUrl) return;
    try {
      await navigator.clipboard.writeText(createdUrl);
      showToast('Link copied to clipboard.', 'success');
    } catch (err) {
      showToast('Copy failed — select and copy the link manually.', 'error');
    }
  }, [createdUrl, showToast]);

  const handleRevoke = useCallback(
    async (packId) => {
      try {
        await rolesApi.revokeSubmittalPack(packId);
        loadPacks();
      } catch (err) {
        showToast(getErrorMessage(err, 'Failed to revoke pack.'), 'error');
      }
    },
    [loadPacks, showToast],
  );

  const activePacks = useMemo(() => packs, [packs]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create submittal pack"
      description="Share a curated, client-safe shortlist for this role as one link. Recruiter notes below are visible to the client."
      footer={(
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Close</Button>
          {!created ? (
            <Button type="button" variant="purple" onClick={handleCreate} disabled={!canCreate}>
              {creating ? 'Creating…' : `Create link (${applications.length})`}
            </Button>
          ) : null}
        </div>
      )}
    >
      {created ? (
        <div className="space-y-3 text-sm">
          <p className="font-semibold text-[var(--taali-text)]">Submittal link ready</p>
          <div className="flex items-center gap-2">
            <input
              className="taali-input flex-1"
              readOnly
              value={createdUrl}
              onFocus={(e) => e.target.select()}
              aria-label="Submittal pack public URL"
            />
            <Button type="button" variant="secondary" size="sm" onClick={handleCopy}>Copy</Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              as="a"
              href={createdUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              View
            </Button>
          </div>
          <p className="text-[var(--taali-muted)]">
            Anyone with this link can view the shortlist until it expires or you revoke it.
          </p>
        </div>
      ) : (
        <div className="space-y-4 text-sm">
          {applications.length === 0 ? (
            <p className="text-[var(--taali-muted)]">
              Select candidates in the table first, then reopen this dialog.
            </p>
          ) : (
            <>
              <label className="block">
                <span className="mb-1 block font-medium">Title</span>
                <input
                  className="taali-input w-full"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder={roleTitle || 'Submittal title'}
                />
              </label>

              <label className="block">
                <span className="mb-1 block font-medium">Link expiry</span>
                <select
                  className="taali-input w-full"
                  value={expiresIn}
                  onChange={(e) => setExpiresIn(e.target.value)}
                >
                  {EXPIRY_OPTIONS.map((o) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
              </label>

              <div>
                <span className="mb-1 block font-medium">
                  Candidates ({applications.length})
                </span>
                <div className="space-y-2">
                  {applications.map((app) => (
                    <div key={app.id} className="rounded-lg border border-[var(--taali-border-soft)] p-2">
                      <div className="font-medium text-[var(--taali-text)]">{candidateName(app)}</div>
                      <input
                        className="taali-input mt-1 w-full"
                        value={notes[app.id] || ''}
                        onChange={(e) => setNotes((prev) => ({ ...prev, [app.id]: e.target.value }))}
                        placeholder="Optional one-line note (client-visible)"
                        maxLength={200}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {activePacks.length > 0 ? (
        <div className="mt-5 border-t border-[var(--taali-border-soft)] pt-3 text-sm">
          <p className="mb-2 font-medium">Existing packs</p>
          {loadingPacks ? (
            <p className="text-[var(--taali-muted)]">Loading…</p>
          ) : (
            <ul className="space-y-1">
              {activePacks.map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-2">
                  <span className="truncate">
                    {p.title || 'Submittal'} · {p.candidate_count} candidate{p.candidate_count === 1 ? '' : 's'}
                    {p.revoked ? ' · revoked' : p.expired ? ' · expired' : ''}
                    {typeof p.view_count === 'number' && p.view_count > 0 ? ` · ${p.view_count} view${p.view_count === 1 ? '' : 's'}` : ''}
                  </span>
                  {!p.revoked ? (
                    <Button type="button" variant="ghost" size="sm" onClick={() => handleRevoke(p.id)}>
                      Revoke
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </Dialog>
  );
}
