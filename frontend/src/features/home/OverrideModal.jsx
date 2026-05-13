// Confirmation modal for destructive alternative actions on a pending
// agent decision. Opens when the recruiter clicks any non-primary, non-
// Send-back button (Reject, Send assessment, Advance instead, Skip &
// advance, etc.). Captures the chosen override_action + a required
// free-text "why" — both go to ``agent_decisions.override_action`` /
// ``resolution_note`` and feed the agent's calibration loop.

import React, { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';

import { agent as agentApi } from '../../shared/api';

export const OverrideModal = ({ decision, alternative, onClose, onSubmitted }) => {
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !submitting) onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, submitting]);

  if (!decision || !alternative) return null;

  const canSubmit = reason.trim().length > 0 && !submitting;
  const candidateName = decision.candidate_name || `Application #${decision.application_id}`;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await agentApi.overrideDecision(decision.id, {
        override_action: alternative.action,
        note: reason.trim(),
      });
      onSubmitted?.(res?.data || null);
      onClose?.();
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Override failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rq-modal-backdrop" onClick={() => !submitting && onClose?.()}>
      <div className="rq-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="rq-modal-head">
          <div>
            <span className="kicker" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <AlertTriangle size={11} aria-hidden="true" />
              {alternative.kicker || 'OVERRIDE'}
            </span>
            <h3 style={{ margin: '6px 0 2px', fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 600, letterSpacing: '-.02em', color: 'var(--ink)' }}>
              {alternative.headline.replace('{name}', candidateName)}
            </h3>
            <p style={{ margin: 0, fontSize: 13, color: 'var(--mute)', maxWidth: 520, lineHeight: 1.5 }}>
              {alternative.body}
            </p>
          </div>
          <button type="button" className="rq-tinybtn" onClick={onClose} aria-label="Close" disabled={submitting}>
            <X size={12} strokeWidth={2.2} />
          </button>
        </div>

        <div className="rq-modal-body">
          <div className="rq-modal-section">
            <label className="rq-modal-label" htmlFor="rq-override-reason">
              Why? (the agent learns from this — required)
            </label>
            <textarea
              id="rq-override-reason"
              className="rq-modal-textarea"
              rows={4}
              placeholder={alternative.placeholder || 'e.g. Internal referral — already pre-vetted by the hiring manager'}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={submitting}
              autoFocus
            />
          </div>

          {error ? (
            <div style={{ color: 'var(--red)', fontSize: 12.5 }}>{error}</div>
          ) : null}
        </div>

        <div className="rq-modal-foot">
          <button type="button" className="rq-btn ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            type="button"
            className={`rq-btn ${alternative.confirmClass || 'rq-override'}`}
            onClick={submit}
            disabled={!canSubmit}
          >
            {submitting ? 'Submitting…' : (alternative.confirmLabel || 'Confirm')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default OverrideModal;
