// InterviewFeedbackSection — the structured feedback block on the candidate
// standing report's "Notes & timeline" tab. Recruiters record what happened in an
// interview: round, overall recommendation, a probe checklist auto-populated
// from the candidate interview kit, and free-text notes. Entries list newest-first
// with expandable detail; each is editable/deletable.
//
// Styling uses the shared Taali primitives plus the report's existing type and
// spacing tokens, so controls behave consistently with the rest of the app.
import React, { useEffect, useId, useMemo, useRef, useState } from 'react';

import { MotionDisclosure } from '../../shared/motion';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Select,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';

const ROUND_OPTIONS = [
  { value: 'screening', label: 'Screening' },
  { value: 'technical', label: 'Technical' },
  { value: 'final', label: 'Final' },
  { value: 'other', label: 'Other' },
];

const RECOMMENDATION_OPTIONS = [
  { value: 'strong_yes', label: 'Strong yes' },
  { value: 'yes', label: 'Yes' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'no', label: 'No' },
  { value: 'strong_no', label: 'Strong no' },
];

const RECOMMENDATION_LABEL = Object.fromEntries(
  RECOMMENDATION_OPTIONS.map((o) => [o.value, o.label]),
);

const ROUND_LABEL = Object.fromEntries(ROUND_OPTIONS.map((o) => [o.value, o.label]));

const PROBE_RESULTS = [
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'refuted', label: 'Refuted' },
  { value: 'not_probed', label: 'Not probed' },
];

const fmtRelative = (ts) => {
  if (!ts) return '';
  const diffMs = Date.now() - new Date(ts).getTime();
  if (Number.isNaN(diffMs)) return '';
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 14) return `${diffDay}d ago`;
  return new Date(ts).toLocaleDateString();
};

// Auto-populate the probe checklist from the interview kit — priority probes
// first, then knockout checks. De-dupe by criterion_id/text so the same
// requirement isn't listed twice.
const probesFromKit = (interviewKit) => {
  const rows = [
    ...(interviewKit?.priority_probes || []),
    ...(interviewKit?.knockout_checks || []),
  ];
  const seen = new Set();
  const out = [];
  for (const row of rows) {
    const key = String(row?.criterion_id || row?.criterion_text || '').trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push({
      criterion_id: row?.criterion_id || null,
      criterion_text: row?.criterion_text || '(unnamed criterion)',
    });
  }
  return out;
};

const emptyForm = (kitProbes) => ({
  interview_round: 'screening',
  interviewer_name: '',
  overall_recommendation: '',
  probe_results: kitProbes.map((p) => ({ ...p, result: 'not_probed' })),
  notes: '',
});

const FeedbackEntry = ({ entry, onEdit, onDelete, busy, readOnly }) => {
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const probes = Array.isArray(entry.probe_results) ? entry.probe_results : [];
  return (
    <Card className="p-4" data-kind="interview-feedback">
      <div className="mc-notes-card-head">
        <span className="mc-notes-card-who">
          {ROUND_LABEL[entry.interview_round] || entry.interview_round}
          {entry.interviewer_name ? (
            <span className="mc-notes-card-role"> · {entry.interviewer_name}</span>
          ) : null}
        </span>
        <span className="flex items-center gap-2">
          <Badge variant="purple">
            {RECOMMENDATION_LABEL[entry.overall_recommendation] || entry.overall_recommendation}
          </Badge>
          <span className="mc-notes-card-time">{fmtRelative(entry.created_at)}</span>
        </span>
      </div>
      {(entry.notes || probes.length) ? (
        <Button
          variant="ghost"
          size="sm"
          className="mt-2"
          aria-expanded={open}
          aria-controls={detailId}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? 'Hide detail' : 'Show detail'}
        </Button>
      ) : null}
      <MotionDisclosure open={open} id={detailId} className="mt-3">
        <div className="grid gap-3">
          {entry.notes ? <div className="mc-notes-card-body">{entry.notes}</div> : null}
          {probes.length ? (
            <div>
              <div className="mc-kicker mb-1">PROBE RESULTS</div>
              <div className="grid gap-1.5">
                {probes.map((p, i) => (
                  <div key={`${p.criterion_id || p.criterion_text || i}`} className="flex justify-between gap-3 text-sm">
                    <span>{p.criterion_text}</span>
                    <span className="whitespace-nowrap text-[var(--taali-muted)]">
                      {PROBE_RESULTS.find((r) => r.value === p.result)?.label || p.result}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </MotionDisclosure>
      {!readOnly ? (
        <div className="mt-3 flex gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={(event) => onEdit(entry, event.currentTarget)}
            disabled={busy}
          >
            Edit
          </Button>
          <Button variant="danger" size="sm" onClick={() => onDelete(entry)} disabled={busy}>
            Delete
          </Button>
        </div>
      ) : null}
    </Card>
  );
};

const InterviewFeedbackSection = ({ applicationId, interviewKit, rolesApi, initialFeedback, readOnly = false }) => {
  const kitProbes = useMemo(() => probesFromKit(interviewKit), [interviewKit]);
  const [entries, setEntries] = useState(Array.isArray(initialFeedback) ? initialFeedback : []);
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(() => emptyForm(kitProbes));
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const formId = useId();
  const recordButtonRef = useRef(null);
  const returnFocusRef = useRef(null);

  useEffect(() => {
    if (Array.isArray(initialFeedback)) setEntries(initialFeedback);
  }, [initialFeedback]);

  // Read-only on recruiter share links: the payload carries the entries but the
  // viewer is unauthenticated, so no record/edit/delete (and no API calls).
  const canSubmit = Boolean(!readOnly && applicationId && rolesApi?.createInterviewFeedback);

  const refresh = async () => {
    if (!applicationId || !rolesApi?.listInterviewFeedback) return;
    try {
      const resp = await rolesApi.listInterviewFeedback(applicationId);
      setEntries(Array.isArray(resp?.data) ? resp.data : []);
    } catch (err) {
      // Non-fatal: keep whatever we had, surface a soft error.
      setError('Could not refresh interview feedback.');
    }
  };

  const openCreate = (event) => {
    returnFocusRef.current = event?.currentTarget || null;
    setEditingId(null);
    setForm(emptyForm(kitProbes));
    setFormOpen(true);
    setError('');
  };

  const openEdit = (entry, trigger = null) => {
    returnFocusRef.current = trigger;
    setEditingId(entry.id);
    setForm({
      interview_round: entry.interview_round || 'screening',
      interviewer_name: entry.interviewer_name || '',
      overall_recommendation: entry.overall_recommendation || '',
      probe_results: Array.isArray(entry.probe_results) && entry.probe_results.length
        ? entry.probe_results.map((p) => ({
            criterion_id: p.criterion_id || null,
            criterion_text: p.criterion_text || '',
            result: p.result || 'not_probed',
          }))
        : kitProbes.map((p) => ({ ...p, result: 'not_probed' })),
      notes: entry.notes || '',
    });
    setFormOpen(true);
    setError('');
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditingId(null);
    setForm(emptyForm(kitProbes));
    window.requestAnimationFrame(() => {
      const previousTrigger = returnFocusRef.current;
      const focusTarget = previousTrigger?.isConnected ? previousTrigger : recordButtonRef.current;
      focusTarget?.focus();
    });
  };

  const setProbeResult = (index, result) => {
    setForm((prev) => {
      const probes = prev.probe_results.map((p, i) => (i === index ? { ...p, result } : p));
      return { ...prev, probe_results: probes };
    });
  };

  const handleSubmit = async () => {
    if (!form.overall_recommendation) {
      setError('Pick an overall recommendation.');
      return;
    }
    setSaving(true);
    setError('');
    const payload = {
      interview_round: form.interview_round,
      interviewer_name: form.interviewer_name.trim() || null,
      overall_recommendation: form.overall_recommendation,
      probe_results: form.probe_results.length ? form.probe_results : null,
      notes: form.notes.trim() || null,
    };
    try {
      if (editingId != null) {
        await rolesApi.updateInterviewFeedback(applicationId, editingId, payload);
      } else {
        await rolesApi.createInterviewFeedback(applicationId, payload);
      }
      await refresh();
      closeForm();
    } catch (err) {
      setError('Could not save interview feedback.');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (entry) => {
    if (!rolesApi?.deleteInterviewFeedback) return;
    if (!window.confirm('Delete this interview feedback entry? This cannot be undone.')) return;
    setBusy(true);
    try {
      await rolesApi.deleteInterviewFeedback(applicationId, entry.id);
      await refresh();
    } catch (err) {
      setError('Could not delete interview feedback.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mc-interview-feedback" data-section="interview-feedback" aria-labelledby="interview-feedback-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div id="interview-feedback-heading" className="mc-kicker">INTERVIEW FEEDBACK</div>
          <p className="mt-1 text-sm text-[var(--taali-muted)]">
            Record the recommendation and notes from each interview round.
          </p>
        </div>
        {!formOpen && canSubmit ? (
          <Button
            ref={recordButtonRef}
            variant="primary"
            size="sm"
            aria-expanded={false}
            aria-controls={formId}
            onClick={openCreate}
          >
            Record feedback
          </Button>
        ) : null}
      </div>
      <div className="mt-3 grid gap-2.5">
        {entries.length === 0 && !formOpen ? (
          <EmptyState
            title="No interview feedback yet"
            description={readOnly
              ? 'No feedback has been recorded for this candidate.'
              : 'After an interview, capture the recommendation, notes, and probe results here.'}
            className="py-7"
          />
        ) : (
          entries.map((entry) => (
            <FeedbackEntry
              key={entry.id}
              entry={entry}
              readOnly={readOnly}
              onEdit={openEdit}
              onDelete={handleDelete}
              busy={busy}
            />
          ))
        )}
      </div>

      {error ? (
        <div className="mt-2 text-sm text-[var(--taali-danger)]" role="alert">{error}</div>
      ) : null}

      <MotionDisclosure open={formOpen} id={formId}>
        <Card className="mt-3 grid gap-4 p-4">
          <div className="grid gap-2">
            <label className="text-xs font-semibold text-[var(--taali-muted)]" htmlFor="interview-feedback-round">Round</label>
            <Select
              id="interview-feedback-round"
              bare
              triggerClassName="max-w-[200px]"
              value={form.interview_round}
              onChange={(e) => setForm((prev) => ({ ...prev, interview_round: e.target.value }))}
              aria-label="Interview round"
            >
              {ROUND_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          </div>

          <div className="grid gap-2">
            <label className="text-xs font-semibold text-[var(--taali-muted)]" htmlFor="interview-feedback-interviewer">Interviewer</label>
            <Input
              id="interview-feedback-interviewer"
              type="text"
              value={form.interviewer_name}
              onChange={(e) => setForm((prev) => ({ ...prev, interviewer_name: e.target.value }))}
              placeholder="Interviewer name (optional)"
            />
          </div>

          <div className="grid gap-2">
            <div className="text-xs font-semibold text-[var(--taali-muted)]">Overall recommendation</div>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Overall recommendation">
              {RECOMMENDATION_OPTIONS.map((o) => {
                const selected = form.overall_recommendation === o.value;
                return (
                  <Button
                    key={o.value}
                    variant={selected ? 'primary' : 'secondary'}
                    size="sm"
                    aria-pressed={selected}
                    onClick={() => setForm((prev) => ({ ...prev, overall_recommendation: o.value }))}
                  >
                    {o.label}
                  </Button>
                );
              })}
            </div>
          </div>

          {form.probe_results.length ? (
            <div className="grid gap-2">
              <div className="text-xs font-semibold text-[var(--taali-muted)]">Probe results</div>
              <div className="grid gap-2">
                {form.probe_results.map((p, i) => (
                  <div key={`${p.criterion_id || p.criterion_text || i}`} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="min-w-0 flex-1">{p.criterion_text}</span>
                    <Select
                      bare
                      triggerClassName="max-w-[150px]"
                      value={p.result}
                      onChange={(e) => setProbeResult(i, e.target.value)}
                      aria-label={`Probe result for ${p.criterion_text}`}
                    >
                      {PROBE_RESULTS.map((r) => (
                        <option key={r.value} value={r.value}>{r.label}</option>
                      ))}
                    </Select>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="grid gap-2">
            <label className="text-xs font-semibold text-[var(--taali-muted)]" htmlFor="interview-feedback-notes">Notes</label>
            <Textarea
              id="interview-feedback-notes"
              value={form.notes}
              onChange={(e) => setForm((prev) => ({ ...prev, notes: e.target.value }))}
              placeholder="What happened, what stood out, what to dig into next…"
              rows={3}
            />
          </div>

          <div className="flex gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={handleSubmit}
              disabled={saving || !form.overall_recommendation}
              loading={saving}
              loadingLabel="Saving…"
            >
              {editingId != null ? 'Save changes' : 'Save feedback'}
            </Button>
            <Button variant="secondary" size="sm" onClick={closeForm} disabled={saving}>
              Cancel
            </Button>
          </div>
        </Card>
      </MotionDisclosure>
    </section>
  );
};

export { InterviewFeedbackSection };
