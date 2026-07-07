import React, { useEffect, useState } from 'react';

import { scorecards as scorecardsApi } from '../../shared/api';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Select,
  Spinner,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';

const RECS = [
  { value: 'strong_yes', label: 'Strong yes' },
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
  { value: 'strong_no', label: 'Strong no' },
  { value: 'no_decision', label: 'No decision' },
];
const recLabel = (v) => RECS.find((r) => r.value === v)?.label || v || '—';
const recVariant = (v) => (v === 'strong_yes' || v === 'yes' ? 'success' : v === 'no' || v === 'strong_no' ? 'danger' : 'muted');

export const ScorecardPanel = ({ applicationId }) => {
  const [cards, setCards] = useState(null);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState({ recommendation: '', overall_rating: '', notes: '' });
  const [saving, setSaving] = useState(false);

  const reload = () => Promise.all([
    scorecardsApi.list(applicationId),
    scorecardsApi.summary(applicationId),
  ]).then(([list, sum]) => { setCards(list); setSummary(sum); });

  useEffect(() => {
    let cancelled = false;
    reload().catch(() => { if (!cancelled) setError('Failed to load scorecards.'); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId]);

  const save = async (thenSubmit) => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        recommendation: draft.recommendation || null,
        overall_rating: draft.overall_rating === '' ? null : Number(draft.overall_rating),
        notes: draft.notes || null,
      };
      const card = await scorecardsApi.upsert(applicationId, payload);
      if (thenSubmit) await scorecardsApi.submit(card.id);
      await reload();
      if (thenSubmit) setDraft({ recommendation: '', overall_rating: '', notes: '' });
    } catch (err) {
      setError(err?.response?.status === 422 ? 'Add a recommendation before submitting.' : 'Could not save the scorecard.');
    } finally {
      setSaving(false);
    }
  };

  if (cards === null && !error) return <div className="flex justify-center py-10"><Spinner /></div>;

  return (
    <div className="space-y-4 py-2">
      {error ? (
        <Card className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">{error}</Card>
      ) : null}

      {summary && summary.submitted_count > 0 ? (
        <Card className="px-4 py-3">
          <div className="text-xs uppercase tracking-wide text-[var(--taali-muted)]">Panel summary</div>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-[var(--taali-text)]">
            <span><strong>{summary.submitted_count}</strong> submitted</span>
            {summary.mean_overall_rating != null ? <span>avg rating <strong>{summary.mean_overall_rating}</strong>/4</span> : null}
            <span className="flex flex-wrap gap-1.5">
              {Object.entries(summary.recommendations || {}).filter(([, n]) => n > 0).map(([k, n]) => (
                <Badge key={k} variant={recVariant(k)}>{recLabel(k)}: {n}</Badge>
              ))}
            </span>
          </div>
        </Card>
      ) : null}

      <Card className="px-4 py-4">
        <h3 className="text-sm font-semibold text-[var(--taali-text)]">Your scorecard</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Recommendation</span>
            <Select value={draft.recommendation} onChange={(e) => setDraft((d) => ({ ...d, recommendation: e.target.value }))}>
              <option value="">Select…</option>
              {RECS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Overall rating (1–4)</span>
            <Select value={draft.overall_rating} onChange={(e) => setDraft((d) => ({ ...d, overall_rating: e.target.value }))}>
              <option value="">—</option>
              {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
            </Select>
          </label>
        </div>
        <label className="mt-3 block">
          <span className="mb-1 block text-xs text-[var(--taali-muted)]">Notes</span>
          <Textarea value={draft.notes} onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))} className="min-h-[4rem]" />
        </label>
        <div className="mt-3 flex gap-2">
          <Button variant="secondary" disabled={saving} onClick={() => save(false)}>Save draft</Button>
          <Button variant="primary" disabled={saving || !draft.recommendation} onClick={() => save(true)}>Submit</Button>
        </div>
      </Card>

      {(cards || []).length === 0 ? (
        <EmptyState title="No scorecards yet" description="Submitted interviewer scorecards appear here." className="py-8" />
      ) : (
        <div className="space-y-2">
          {cards.map((c) => (
            <Card key={c.id} className="px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <Badge variant={recVariant(c.recommendation)}>{recLabel(c.recommendation)}</Badge>
                <span className="text-xs text-[var(--taali-muted)]">
                  {c.overall_rating != null ? `${c.overall_rating}/4 · ` : ''}{c.submitted_at ? 'Submitted' : 'Draft'}
                </span>
              </div>
              {c.notes ? <p className="mt-2 whitespace-pre-wrap text-sm text-[var(--taali-text)]">{c.notes}</p> : null}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};

export default ScorecardPanel;
