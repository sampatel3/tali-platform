// InterviewFeedbackSection — the "Interview feedback" block on the candidate
// standing report's "Interview prep" tab. Recruiters record what happened in
// an interview: round, overall recommendation (a 5-chip selector), optional
// 1–5 ratings on the 5-Ds axes, a probe checklist auto-populated from the
// candidate interview kit, and free-text notes. Entries list newest-first
// with expandable detail; each is editable/deletable.
//
// Styling reuses the standing-report design system (mc-* classes, purple
// tokens). Recommendation badges use purple-scale intensity — never
// red/amber/green — so the surface stays on-scheme.
import React, { useEffect, useMemo, useState } from 'react';

import { Select } from '../../shared/ui/TaaliPrimitives';
import { FLUENCY_4D_AXES } from '../../shared/assessment/fluency4d';

const ROUND_OPTIONS = [
  { value: 'screening', label: 'Screening' },
  { value: 'technical', label: 'Technical' },
  { value: 'final', label: 'Final' },
  { value: 'other', label: 'Other' },
];

// Strongest-positive → strongest-negative for the chip row. `intensity` drives
// a purple-scale mix so a stronger verdict reads darker (no traffic-light hues).
const RECOMMENDATION_OPTIONS = [
  { value: 'strong_yes', label: 'Strong yes', intensity: 100 },
  { value: 'yes', label: 'Yes', intensity: 72 },
  { value: 'neutral', label: 'Neutral', intensity: 44 },
  { value: 'no', label: 'No', intensity: 72 },
  { value: 'strong_no', label: 'Strong no', intensity: 100 },
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

const badgeStyle = (recommendation) => {
  const opt = RECOMMENDATION_OPTIONS.find((o) => o.value === recommendation);
  const intensity = opt ? opt.intensity : 44;
  return {
    background: `color-mix(in oklab, var(--purple) ${intensity}%, transparent)`,
    color: intensity >= 60 ? 'var(--bg)' : 'var(--ink)',
    borderRadius: 999,
    padding: '2px 10px',
    fontSize: 12,
    fontWeight: 600,
    whiteSpace: 'nowrap',
  };
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
  dimension_ratings: {},
  probe_results: kitProbes.map((p) => ({ ...p, result: 'not_probed' })),
  notes: '',
});

const FeedbackEntry = ({ entry, onEdit, onDelete, busy, readOnly }) => {
  const [open, setOpen] = useState(false);
  const probes = Array.isArray(entry.probe_results) ? entry.probe_results : [];
  const ratings = entry.dimension_ratings && typeof entry.dimension_ratings === 'object'
    ? entry.dimension_ratings
    : {};
  const ratingRows = FLUENCY_4D_AXES
    .filter((axis) => ratings[axis.key] != null)
    .map((axis) => ({ label: axis.label, score: ratings[axis.key] }));

  return (
    <div className="mc-notes-card" data-kind="interview-feedback">
      <div className="mc-notes-card-head">
        <span className="mc-notes-card-who">
          {ROUND_LABEL[entry.interview_round] || entry.interview_round}
          {entry.interviewer_name ? (
            <span className="mc-notes-card-role"> · {entry.interviewer_name}</span>
          ) : null}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={badgeStyle(entry.overall_recommendation)}>
            {RECOMMENDATION_LABEL[entry.overall_recommendation] || entry.overall_recommendation}
          </span>
          <span className="mc-notes-card-time">{fmtRelative(entry.created_at)}</span>
        </span>
      </div>
      {(entry.notes || probes.length || ratingRows.length) ? (
        <button
          type="button"
          className="btn btn-outline btn-sm"
          style={{ marginTop: 8 }}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? 'Hide detail' : 'Show detail'}
        </button>
      ) : null}
      {open ? (
        <div style={{ marginTop: 10, display: 'grid', gap: 10 }}>
          {entry.notes ? <div className="mc-notes-card-body">{entry.notes}</div> : null}
          {ratingRows.length ? (
            <div>
              <div className="mc-kicker" style={{ marginBottom: 4 }}>5-DS RATINGS</div>
              <div style={{ display: 'grid', gap: 4 }}>
                {ratingRows.map((r) => (
                  <div key={r.label} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span>{r.label}</span>
                    <span style={{ color: 'var(--purple)', fontWeight: 600 }}>{r.score}/5</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {probes.length ? (
            <div>
              <div className="mc-kicker" style={{ marginBottom: 4 }}>PROBE RESULTS</div>
              <div style={{ display: 'grid', gap: 4 }}>
                {probes.map((p, i) => (
                  <div key={`${p.criterion_id || p.criterion_text || i}`} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                    <span>{p.criterion_text}</span>
                    <span style={{ color: 'var(--mute)', whiteSpace: 'nowrap' }}>
                      {PROBE_RESULTS.find((r) => r.value === p.result)?.label || p.result}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
      {!readOnly ? (
        <div className="mc-notes-input-actions" style={{ marginTop: 8, gap: 8, display: 'flex' }}>
          <button type="button" className="btn btn-outline btn-sm" onClick={() => onEdit(entry)} disabled={busy}>
            Edit
          </button>
          <button type="button" className="btn btn-outline btn-sm" onClick={() => onDelete(entry)} disabled={busy}>
            Delete
          </button>
        </div>
      ) : null}
    </div>
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

  const openCreate = () => {
    setEditingId(null);
    setForm(emptyForm(kitProbes));
    setFormOpen(true);
    setError('');
  };

  const openEdit = (entry) => {
    setEditingId(entry.id);
    setForm({
      interview_round: entry.interview_round || 'screening',
      interviewer_name: entry.interviewer_name || '',
      overall_recommendation: entry.overall_recommendation || '',
      dimension_ratings: entry.dimension_ratings || {},
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
  };

  const setRating = (axisKey, value) => {
    setForm((prev) => {
      const next = { ...prev.dimension_ratings };
      if (value === '') delete next[axisKey];
      else next[axisKey] = Number(value);
      return { ...prev, dimension_ratings: next };
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
      dimension_ratings: Object.keys(form.dimension_ratings).length ? form.dimension_ratings : null,
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
    <div className="mc-prep-stage" data-section="interview-feedback">
      <div className="mc-kicker">INTERVIEW FEEDBACK</div>
      <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
        {entries.length === 0 ? (
          <div className="mc-notes-empty">
            {readOnly
              ? 'No interview feedback recorded.'
              : 'No interview feedback recorded yet. After an interview, record the round, recommendation, and how the probes landed — it feeds the score↔outcome calibration.'}
          </div>
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
        <div className="mc-notes-empty" style={{ color: 'var(--purple)', marginTop: 8 }}>{error}</div>
      ) : null}

      {!formOpen && canSubmit ? (
        <div className="mc-notes-input-actions" style={{ marginTop: 12 }}>
          <button type="button" className="btn btn-purple btn-sm" onClick={openCreate}>
            Record feedback
          </button>
        </div>
      ) : null}

      {formOpen ? (
        <div className="mc-notes-input" style={{ marginTop: 12, display: 'grid', gap: 12 }}>
          <div style={{ display: 'grid', gap: 8 }}>
            <div className="mc-kicker">ROUND</div>
            <Select
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

          <div style={{ display: 'grid', gap: 8 }}>
            <div className="mc-kicker">INTERVIEWER</div>
            <input
              type="text"
              className="taali-input"
              value={form.interviewer_name}
              onChange={(e) => setForm((prev) => ({ ...prev, interviewer_name: e.target.value }))}
              placeholder="Interviewer name (optional)"
            />
          </div>

          <div style={{ display: 'grid', gap: 8 }}>
            <div className="mc-kicker">OVERALL RECOMMENDATION</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }} role="group" aria-label="Overall recommendation">
              {RECOMMENDATION_OPTIONS.map((o) => {
                const selected = form.overall_recommendation === o.value;
                return (
                  <button
                    key={o.value}
                    type="button"
                    className="btn btn-sm"
                    aria-pressed={selected}
                    onClick={() => setForm((prev) => ({ ...prev, overall_recommendation: o.value }))}
                    style={selected
                      ? badgeStyle(o.value)
                      : { border: '1px solid var(--line)', borderRadius: 999, padding: '2px 10px', background: 'var(--bg)' }}
                  >
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div style={{ display: 'grid', gap: 8 }}>
            <div className="mc-kicker">5-DS RATINGS (OPTIONAL)</div>
            <div style={{ display: 'grid', gap: 6 }}>
              {FLUENCY_4D_AXES.map((axis) => (
                <div key={axis.key} style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
                  <span>{axis.label}</span>
                  <Select
                    bare
                    triggerClassName="max-w-[110px]"
                    value={form.dimension_ratings[axis.key] != null ? String(form.dimension_ratings[axis.key]) : ''}
                    onChange={(e) => setRating(axis.key, e.target.value)}
                    aria-label={`${axis.label} rating`}
                  >
                    <option value="">—</option>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <option key={n} value={String(n)}>{n}/5</option>
                    ))}
                  </Select>
                </div>
              ))}
            </div>
          </div>

          {form.probe_results.length ? (
            <div style={{ display: 'grid', gap: 8 }}>
              <div className="mc-kicker">PROBE CHECKLIST</div>
              <div style={{ display: 'grid', gap: 6 }}>
                {form.probe_results.map((p, i) => (
                  <div key={`${p.criterion_id || p.criterion_text || i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
                    <span style={{ flex: 1 }}>{p.criterion_text}</span>
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

          <div style={{ display: 'grid', gap: 8 }}>
            <div className="mc-kicker">NOTES</div>
            <textarea
              value={form.notes}
              onChange={(e) => setForm((prev) => ({ ...prev, notes: e.target.value }))}
              placeholder="What happened, what stood out, what to dig into next…"
              rows={3}
            />
          </div>

          <div className="mc-notes-input-actions" style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="btn btn-purple btn-sm"
              onClick={handleSubmit}
              disabled={saving || !form.overall_recommendation}
            >
              {saving ? 'Saving…' : editingId != null ? 'Save changes' : 'Save feedback'}
            </button>
            <button type="button" className="btn btn-outline btn-sm" onClick={closeForm} disabled={saving}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export { InterviewFeedbackSection };
