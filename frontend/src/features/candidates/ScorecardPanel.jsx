import React, { useEffect, useState } from 'react';

import {
  Badge,
  Button,
  Card,
  EmptyState,
  Select,
  Spinner,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';

// Recommendation vocabulary for a scorecard. ``no_decision`` is an explicit
// abstention — it can't be submitted as a decision and never enters the mean.
const RECS = [
  { value: 'strong_yes', label: 'Strong yes' },
  { value: 'yes', label: 'Yes' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'no', label: 'No' },
  { value: 'strong_no', label: 'Strong no' },
  { value: 'no_decision', label: 'No decision' },
];
const recLabel = (v) => RECS.find((r) => r.value === v)?.label || v || '—';
const recVariant = (v) =>
  v === 'strong_yes' || v === 'yes'
    ? 'success'
    : v === 'no' || v === 'strong_no'
      ? 'danger'
      : 'muted';

/**
 * Interviewer scorecards for one application. Each interviewer drafts and
 * submits their OWN card (the backend keys the upsert on the caller), and the
 * panel summary tallies the SUBMITTED cards. Read-only on share links (no
 * authenticated caller to file feedback under).
 */
export const ScorecardPanel = ({
  applicationId,
  rolesApi,
  readOnly = false,
  interviews = [],
}) => {
  const [cards, setCards] = useState(null);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState({
    overall_recommendation: '',
    overall_rating: '',
    notes: '',
  });
  const [saving, setSaving] = useState(false);
  const [drafting, setDrafting] = useState(false);
  // True once the agent has drafted the card into the editor, so the UI can
  // flag "review and submit" — the human still owns the verdict.
  const [aiDrafted, setAiDrafted] = useState(false);

  // A transcript is required to draft from. The endpoint auto-picks the latest
  // transcript-bearing interview; the button is disabled with a reason when
  // none is linked.
  const hasTranscript = (interviews || []).some(
    (iv) => (iv?.transcript_text || '').trim().length > 0,
  );

  const draftFromTranscript = async () => {
    setDrafting(true);
    setError(null);
    try {
      const { data: card } = await rolesApi.draftScorecardFromTranscript(applicationId, {});
      setDraft({
        overall_recommendation: card.overall_recommendation === 'no_decision'
          ? ''
          : card.overall_recommendation || '',
        overall_rating: card.overall_rating == null ? '' : String(card.overall_rating),
        notes: card.notes || '',
      });
      setAiDrafted(true);
      await reload();
    } catch (err) {
      setError(
        err?.response?.status === 400
          ? 'No interview transcript is linked yet — link one to draft from.'
          : err?.response?.status === 409
            ? 'You already submitted this scorecard; it can’t be redrafted.'
            : 'Could not draft the scorecard from the transcript.',
      );
    } finally {
      setDrafting(false);
    }
  };

  const reload = () =>
    Promise.all([
      rolesApi.listScorecards(applicationId),
      rolesApi.getScorecardSummary(applicationId),
    ]).then(([list, sum]) => {
      setCards(list.data);
      setSummary(sum.data);
    });

  useEffect(() => {
    let cancelled = false;
    if (!applicationId || !rolesApi?.listScorecards) return undefined;
    reload().catch(() => {
      if (!cancelled) setError('Could not load scorecards.');
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId]);

  const save = async (thenSubmit) => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        overall_recommendation: draft.overall_recommendation || null,
        overall_rating: draft.overall_rating === '' ? null : Number(draft.overall_rating),
        notes: draft.notes || null,
      };
      const { data: card } = await rolesApi.upsertScorecard(applicationId, payload);
      if (thenSubmit) await rolesApi.submitScorecard(applicationId, card.id);
      await reload();
      if (thenSubmit) {
        setDraft({ overall_recommendation: '', overall_rating: '', notes: '' });
      }
    } catch (err) {
      setError(
        err?.response?.status === 422
          ? 'Add a recommendation before submitting.'
          : 'Could not save the scorecard.',
      );
    } finally {
      setSaving(false);
    }
  };

  if (cards === null && !error) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-4 py-2">
      {error ? (
        <Card className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">
          {error}
        </Card>
      ) : null}

      {summary && summary.submitted_count > 0 ? (
        <Card className="px-4 py-3">
          <div className="text-xs uppercase tracking-wide text-[var(--taali-muted)]">
            Panel summary
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-[var(--taali-text)]">
            <span>
              <strong>{summary.submitted_count}</strong> submitted
            </span>
            {summary.mean_overall_rating != null ? (
              <span>
                avg rating <strong>{summary.mean_overall_rating}</strong>/4
              </span>
            ) : null}
            <span className="flex flex-wrap gap-1.5">
              {Object.entries(summary.recommendations || {})
                .filter(([, n]) => n > 0)
                .map(([k, n]) => (
                  <Badge key={k} variant={recVariant(k)}>
                    {recLabel(k)}: {n}
                  </Badge>
                ))}
            </span>
          </div>
        </Card>
      ) : null}

      {!readOnly ? (
        <Card className="px-4 py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-[var(--taali-text)]">Your scorecard</h3>
            <Button
              variant="secondary"
              disabled={drafting || !hasTranscript}
              title={
                hasTranscript
                  ? 'Let the agent draft this scorecard from the interview transcript'
                  : 'Link an interview transcript to enable an agent draft'
              }
              onClick={draftFromTranscript}
            >
              {drafting ? 'Drafting…' : 'Draft from transcript'}
            </Button>
          </div>
          {aiDrafted ? (
            <div className="mt-3 rounded-md border border-[var(--taali-accent-border,var(--taali-border))] bg-[var(--taali-accent-soft,var(--taali-surface-2))] px-3 py-2 text-xs text-[var(--taali-text)]">
              Drafted from the interview transcript — review, edit, and submit. You own the
              final verdict; the agent never submits.
            </div>
          ) : null}
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs text-[var(--taali-muted)]">Recommendation</span>
              <Select
                value={draft.overall_recommendation}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, overall_recommendation: e.target.value }))
                }
              >
                <option value="">Select…</option>
                {RECS.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-[var(--taali-muted)]">
                Overall rating (1–4)
              </span>
              <Select
                value={draft.overall_rating}
                onChange={(e) => setDraft((d) => ({ ...d, overall_rating: e.target.value }))}
              >
                <option value="">—</option>
                {[1, 2, 3, 4].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </Select>
            </label>
          </div>
          <label className="mt-3 block">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Notes</span>
            <Textarea
              value={draft.notes}
              onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))}
              className="min-h-[4rem]"
            />
          </label>
          <div className="mt-3 flex gap-2">
            <Button variant="secondary" disabled={saving} onClick={() => save(false)}>
              Save draft
            </Button>
            <Button
              variant="primary"
              disabled={saving || !draft.overall_recommendation || draft.overall_recommendation === 'no_decision'}
              onClick={() => save(true)}
            >
              Submit
            </Button>
          </div>
        </Card>
      ) : null}

      {(cards || []).length === 0 ? (
        <EmptyState
          title="No scorecards yet"
          description="Interviewer scorecards appear here once recorded."
          className="py-8"
        />
      ) : (
        <div className="space-y-2">
          {cards.map((c) => (
            <Card key={c.id} className="px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <Badge variant={recVariant(c.overall_recommendation)}>
                  {recLabel(c.overall_recommendation)}
                </Badge>
                <span className="text-xs text-[var(--taali-muted)]">
                  {c.overall_rating != null ? `${c.overall_rating}/4 · ` : ''}
                  {c.submitted_at ? 'Submitted' : 'Draft'}
                </span>
              </div>
              {c.interviewer_name ? (
                <div className="mt-1 text-xs text-[var(--taali-muted)]">{c.interviewer_name}</div>
              ) : null}
              {c.notes ? (
                <p className="mt-2 whitespace-pre-wrap text-sm text-[var(--taali-text)]">
                  {c.notes}
                </p>
              ) : null}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};

export default ScorecardPanel;
