// "Send back & teach" modal — third action on every pending decision.
//
// Submitting POSTs /agent/feedback. For scope='org' the modal shows a
// purple-amber warning explaining a second admin must co-sign before the
// retune actually fires (see docs/HOME_HUB_DESIGN.md §7 — the cosign tray
// in the Hub's SIGNAL section is where the second admin acts).

import React, { useEffect, useMemo, useState } from 'react';
import { Brain, X } from 'lucide-react';

import { agent as agentApi } from '../../shared/api';

const FAILURE_MODES = [
  { id: 'rubric_mismatch', l: 'Rubric mismatch', d: "Score doesn't match the rubric" },
  { id: 'wrong_threshold', l: 'Wrong threshold', d: 'Threshold should be higher / lower' },
  { id: 'missing_signal', l: 'Missing signal', d: "Agent didn't see a key piece of evidence" },
  { id: 'over_confident', l: 'Over-confident', d: 'Agent was sure but wrong' },
  { id: 'policy_violation', l: 'Policy violation', d: "Doesn't match our hiring policy" },
  { id: 'other', l: 'Other', d: 'Free-form' },
];

const SCOPES = [
  { id: 'decision', l: 'Just this decision', d: 'Logged against this one decision' },
  { id: 'role', l: 'This role going forward', d: 'Tagged as a role-scoped correction' },
  { id: 'org', l: 'All roles in workspace', d: 'Tagged org-wide (requires a second admin to co-sign)' },
];

export const TeachModal = ({ decision, onClose, onSubmitted }) => {
  const [failureMode, setFailureMode] = useState('rubric_mismatch');
  const [correction, setCorrection] = useState('');
  const [scope, setScope] = useState('role');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !submitting) onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, submitting]);

  const isOrgScope = scope === 'org';
  const isCanSubmit = useMemo(
    () => correction.trim().length > 0 && !submitting,
    [correction, submitting],
  );

  if (!decision) return null;

  const submit = async () => {
    if (!isCanSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await agentApi.sendFeedback({
        decision_id: Number(decision.id),
        failure_mode: failureMode,
        correction_text: correction.trim(),
        scope,
        role_id: scope === 'role' ? Number(decision.role_id) : undefined,
      });
      onSubmitted?.(res?.data || null);
      onClose?.();
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to submit feedback');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rq-modal-backdrop" onClick={() => !submitting && onClose?.()}>
      <div className="rq-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="rq-modal-head">
          <div>
            <span className="kicker">TEACH THE AGENT</span>
            <h3 style={{ margin: '6px 0 2px', fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 600, letterSpacing: '-.02em', color: 'var(--ink)' }}>
              What did the agent get wrong?
            </h3>
            <p style={{ margin: 0, fontSize: 13, color: 'var(--mute)', maxWidth: 520, lineHeight: 1.5 }}>
              Your correction is logged against this decision. The decision goes back to the queue with your note attached so the next reviewer sees it.
            </p>
          </div>
          <button type="button" className="rq-tinybtn" onClick={onClose} aria-label="Close" disabled={submitting}>
            <X size={12} strokeWidth={2.2} />
          </button>
        </div>

        <div className="rq-modal-body">
          <div className="rq-modal-section">
            <label className="rq-modal-label">Failure mode</label>
            <div className="rq-modal-tags">
              {FAILURE_MODES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={`rq-modal-tag ${failureMode === t.id ? 'on' : ''}`.trim()}
                  onClick={() => setFailureMode(t.id)}
                >
                  <span className="l">{t.l}</span>
                  <span className="d">{t.d}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="rq-modal-section">
            <label className="rq-modal-label" htmlFor="rq-correction">
              Your correction (becomes a training example)
            </label>
            <textarea
              id="rq-correction"
              className="rq-modal-textarea"
              rows={4}
              placeholder="e.g. Score 88 should be ~78. Candidate's system design answer was strong on paper but didn't account for read-replica lag. The 'iteration' axis was over-credited."
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
              disabled={submitting}
            />
          </div>

          <div className="rq-modal-section">
            <label className="rq-modal-label">Apply to</label>
            <div className="rq-modal-radios">
              {SCOPES.map((r) => (
                <label key={r.id} className={`rq-modal-radio ${scope === r.id ? 'on' : ''}`.trim()}>
                  <input
                    type="radio"
                    name="scope"
                    checked={scope === r.id}
                    onChange={() => setScope(r.id)}
                    disabled={submitting}
                  />
                  <span className="l">{r.l}</span>
                  <span className="d">{r.d}</span>
                </label>
              ))}
            </div>
          </div>

          {isOrgScope ? (
            <div className="rq-modal-cosign-warning">
              <strong>Two-admin rule.</strong> Because this correction is tagged as org-wide, a second admin needs to co-sign before it's accepted. They'll see it in the Hub's <em>Signal</em> section.
            </div>
          ) : null}

          <div className="rq-modal-section rq-modal-impact">
            <span className="kicker mute">WHAT HAPPENS</span>
            <ul style={{ margin: '8px 0 0', padding: 0, listStyle: 'none', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}>
              <li>· Decision <span style={{ fontFamily: 'var(--font-mono)' }}>D-{decision.id}</span> goes back to <strong>Pending</strong> with your note attached.</li>
              <li>· Your correction is logged in the Signal section.</li>
              <li>· You can revert it within 1 hour.</li>
            </ul>
          </div>

          {error ? (
            <div style={{ color: 'var(--red)', fontSize: 12.5 }}>{error}</div>
          ) : null}
        </div>

        <div className="rq-modal-foot">
          <button type="button" className="rq-btn ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button type="button" className="rq-btn rq-teach" onClick={submit} disabled={!isCanSubmit}>
            <Brain size={13} strokeWidth={2} aria-hidden="true" />
            {submitting ? 'Submitting…' : 'Send back & teach'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default TeachModal;
