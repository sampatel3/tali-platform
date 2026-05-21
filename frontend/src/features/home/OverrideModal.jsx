// Confirmation modal for both override AND primary-advance flows on a
// pending agent decision. Originally only handled destructive overrides
// (Reject / Skip & advance / Advance instead) where a free-text "why"
// is required so the agent's calibration loop has a teaching signal.
// Extended to also drive the recruiter-confirms-an-advance path, where
// a Workable stage `<select>` shows up and the "why" textarea becomes
// optional.
//
// The two modes are decided by ``alternative.mode``:
//   - "override"  → calls /override with override_action + note (default)
//   - "approve"   → calls /approve with note (no override_action)
//
// ``alternative.requireStagePick`` opts the modal into rendering the
// Workable stage `<select>`. The selected stage is sent as
// ``workable_target_stage`` on both endpoints.

import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, X } from 'lucide-react';

import { agent as agentApi } from '../../shared/api';

const _normalizeStages = (stages) => {
  if (!Array.isArray(stages)) return [];
  return stages
    .map((stage) => {
      if (typeof stage === 'string') {
        return { value: stage, label: stage };
      }
      if (stage && typeof stage === 'object') {
        const value = String(stage.slug || stage.name || stage.value || '').trim();
        const label = String(stage.name || stage.label || value).trim();
        return value ? { value, label } : null;
      }
      return null;
    })
    .filter(Boolean);
};

export const OverrideModal = ({
  decision,
  alternative,
  workableStages = [],
  onClose,
  onSubmitted,
}) => {
  const [reason, setReason] = useState('');
  const [targetStage, setTargetStage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const stageOptions = useMemo(() => _normalizeStages(workableStages), [workableStages]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !submitting) onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, submitting]);

  // Reset when a different alternative opens.
  useEffect(() => {
    setReason('');
    setTargetStage('');
    setError(null);
  }, [decision?.id, alternative?.action, alternative?.mode]);

  if (!decision || !alternative) return null;

  const mode = alternative.mode || 'override';
  const requireStagePick = Boolean(alternative.requireStagePick);
  const requireReason = mode === 'override';
  const stagePicked = !requireStagePick || Boolean(targetStage);
  const reasonOk = !requireReason || reason.trim().length > 0;
  const canSubmit = stagePicked && reasonOk && !submitting;
  const candidateName = decision.candidate_name || `Application #${decision.application_id}`;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        note: reason.trim() || null,
      };
      if (requireStagePick && targetStage) {
        payload.workable_target_stage = targetStage;
      }
      let res;
      if (mode === 'approve') {
        res = await agentApi.approveDecision(decision.id, payload);
      } else {
        res = await agentApi.overrideDecision(decision.id, {
          ...payload,
          override_action: alternative.action,
        });
      }
      onSubmitted?.(res?.data || null);
      onClose?.();
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || `${mode === 'approve' ? 'Approve' : 'Override'} failed`);
    } finally {
      setSubmitting(false);
    }
  };

  const KickerIcon = mode === 'approve' ? ArrowRight : AlertTriangle;

  return (
    <div className="rq-modal-backdrop" onClick={() => !submitting && onClose?.()}>
      <div className="rq-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="rq-modal-head">
          <div>
            <span className="kicker" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <KickerIcon size={11} aria-hidden="true" />
              {alternative.kicker || (mode === 'approve' ? 'ADVANCE' : 'OVERRIDE')}
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
          {requireStagePick ? (
            <div className="rq-modal-section">
              <label className="rq-modal-label" htmlFor="rq-target-stage">
                Move to which Workable stage? (required)
              </label>
              <select
                id="rq-target-stage"
                className="rq-modal-select"
                value={targetStage}
                onChange={(e) => setTargetStage(e.target.value)}
                disabled={submitting}
                autoFocus
              >
                <option value="">
                  {stageOptions.length === 0 ? 'Loading Workable stages…' : 'Pick a stage…'}
                </option>
                {stageOptions.map((stage) => (
                  <option key={stage.value} value={stage.value}>
                    {stage.label}
                  </option>
                ))}
              </select>
              {stageOptions.length === 0 ? (
                <span style={{ fontSize: 12, color: 'var(--mute)' }}>
                  No Workable stages found for this role. The candidate's internal stage will still update; nothing posts to Workable until stages load.
                </span>
              ) : null}
            </div>
          ) : null}

          <div className="rq-modal-section">
            <label className="rq-modal-label" htmlFor="rq-override-reason">
              {requireReason ? 'Why? (the agent learns from this — required)' : 'Note (optional)'}
            </label>
            <textarea
              id="rq-override-reason"
              className="rq-modal-textarea"
              rows={4}
              placeholder={alternative.placeholder || 'e.g. Internal referral — already pre-vetted by the hiring manager'}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={submitting}
              autoFocus={!requireStagePick}
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
