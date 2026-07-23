/**
 * DeckLinksPage — mint, audit and revoke per-prospect sales-deck links.
 *
 * Internal sales tooling, so it lives in `_dev` behind TokenGate alongside the
 * deck itself rather than in customer-facing Settings. TokenGate is obscurity
 * only; the real gate is server-side — every endpoint requires an owner JWT.
 *
 * Each row is one prospect: a distinct URL, when they opened it, and a Revoke
 * that kills that link without touching anyone else's.
 */

import { useCallback, useEffect, useState } from 'react';

import { deckLinks } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import getErrorMessage from '../../shared/getErrorMessage';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageContainer,
  PageHeader,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';

function formatWhen(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function DeckLinksPage() {
  const { showToast } = useToast();
  const [links, setLinks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [label, setLabel] = useState('');
  const [note, setNote] = useState('');
  const [creating, setCreating] = useState(false);
  const [confirmRevokeId, setConfirmRevokeId] = useState(null);

  const load = useCallback(async () => {
    try {
      const resp = await deckLinks.list();
      setLinks(resp.data?.links || []);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to load deck links.'), 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  const copy = async (url) => {
    // Kept separate from the mint call: a clipboard failure must never look
    // like a mint failure, or you end up with orphan links from retries.
    try {
      await navigator.clipboard?.writeText(url);
      showToast('Link copied to clipboard.', 'success');
    } catch {
      showToast(url, 'info');
    }
  };

  const handleCreate = async (event) => {
    event.preventDefault();
    const trimmed = label.trim();
    if (!trimmed) {
      showToast('Who is this link for?', 'warning');
      return;
    }
    setCreating(true);
    let created = null;
    try {
      const resp = await deckLinks.create({
        prospect_label: trimmed,
        note: note.trim() || undefined,
      });
      created = resp.data;
      setLinks((prev) => [created, ...prev]);
      setLabel('');
      setNote('');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to create the link.'), 'error');
    } finally {
      setCreating(false);
    }
    if (created?.url) await copy(created.url);
  };

  const handleRevoke = async (id) => {
    try {
      const resp = await deckLinks.revoke(id);
      setLinks((prev) => prev.map((l) => (l.id === id ? resp.data : l)));
      showToast('Link revoked. The others still work.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to revoke the link.'), 'error');
    } finally {
      setConfirmRevokeId(null);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Deck links"
        description="One link per prospect. See when they opened it, and revoke a single link without breaking the others."
      />

      <Card className="p-5">
        <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-3">
          <label className="flex-1 min-w-[220px] text-sm">
            <span className="mb-1 block text-[var(--taali-muted)]">Who is it for?</span>
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Venquis"
              maxLength={120}
            />
          </label>
          <label className="flex-1 min-w-[220px] text-sm">
            <span className="mb-1 block text-[var(--taali-muted)]">Note (optional)</span>
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Sent after the intro call"
              maxLength={500}
            />
          </label>
          <Button type="submit" loading={creating} loadingLabel="Creating…">
            Create link
          </Button>
        </form>
      </Card>

      <div className="mt-5">
        {loading ? (
          <Spinner />
        ) : links.length === 0 ? (
          <EmptyState
            title="No deck links yet"
            description="Create one per prospect so each open is attributable."
            className="py-6"
          />
        ) : (
          <div className="flex flex-col gap-2">
            {links.map((link) => (
              <Card
                key={link.id}
                className="p-4"
                style={{ opacity: link.is_revoked ? 0.55 : 1 }}
              >
                <div className="flex flex-wrap items-center gap-3">
                  <div className="min-w-[180px] flex-1">
                    <div className="flex items-center gap-2">
                      <strong>{link.prospect_label}</strong>
                      {link.is_revoked ? (
                        <Badge variant="danger">Revoked</Badge>
                      ) : link.view_count > 0 ? (
                        <Badge variant="success">
                          Opened {link.view_count}×
                        </Badge>
                      ) : (
                        <Badge variant="muted">Not opened yet</Badge>
                      )}
                    </div>
                    {link.note ? (
                      <div className="mt-1 text-xs text-[var(--taali-muted)]">{link.note}</div>
                    ) : null}
                    <div className="mt-1 font-mono text-xs text-[var(--taali-muted)] break-all">
                      {link.url}
                    </div>
                  </div>

                  <div className="text-xs text-[var(--taali-muted)]">
                    <div>Created {formatWhen(link.created_at)}</div>
                    <div>Last opened {formatWhen(link.last_viewed_at)}</div>
                  </div>

                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={() => copy(link.url)}
                      disabled={link.is_revoked}
                    >
                      Copy
                    </Button>
                    {link.is_revoked ? null : confirmRevokeId === link.id ? (
                      <>
                        <Button
                          variant="danger"
                          size="xs"
                          onClick={() => handleRevoke(link.id)}
                        >
                          Confirm
                        </Button>
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => setConfirmRevokeId(null)}
                        >
                          Cancel
                        </Button>
                      </>
                    ) : (
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => setConfirmRevokeId(link.id)}
                      >
                        Revoke
                      </Button>
                    )}
                  </div>
                </div>

                {link.opens?.length > 1 ? (
                  <div className="mt-2 border-t border-[var(--taali-border-soft)] pt-2 font-mono text-[11px] text-[var(--taali-muted)]">
                    {link.opens.slice(0, 8).map((o) => formatWhen(o)).join('  ·  ')}
                  </div>
                ) : null}
              </Card>
            ))}
          </div>
        )}
      </div>
    </PageContainer>
  );
}
