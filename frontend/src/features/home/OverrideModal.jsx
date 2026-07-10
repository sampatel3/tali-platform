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
// ``alternative.requireStagePick`` opts the modal into rendering a row
// of chip-style stage buttons (mirrors the candidate-drawer pattern on
// Jobs). The selected stage is sent as ``workable_target_stage`` on both
// endpoints.

import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, X } from 'lucide-react';

import { agent as agentApi } from '../../shared/api';

export const normalizeWorkableStages = (stages) => {
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

// Workable's two pre-application stage kinds. You can't *advance* a candidate
// INTO "Sourced" or "Applied" — they sit before the funnel's hand-off — so an
// advance picker must never offer them. (A job whose Workable pipeline has only
// these two has no advance target at all; the caller then advances on Tali's
// internal stage and posts nothing to Workable.)
const PRE_HANDOVER_STAGE_KEYS = new Set(['sourced', 'applied']);
const stageKey = (raw) =>
  String(raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');

// The subset of a Workable job's stages a candidate can be ADVANCED into —
// everything except the pre-application stages. Use this for every advance /
// move-forward picker; use normalizeWorkableStages() for a plain full listing
// (e.g. settings), where excluding stages would be wrong.
export const advanceableWorkableStages = (stages) => {
  if (!Array.isArray(stages)) return [];
  return normalizeWorkableStages(
    stages.filter((stage) => {
      if (stage && typeof stage === 'object') {
        if (PRE_HANDOVER_STAGE_KEYS.has(stageKey(stage.kind))) return false;
        return !PRE_HANDOVER_STAGE_KEYS.has(
          stageKey(stage.slug || stage.value || stage.name),
        );
      }
      return !PRE_HANDOVER_STAGE_KEYS.has(stageKey(stage));
    }),
  );
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

  // Advance pickers only ever offer forward stages — never Sourced/Applied.
  const stageOptions = useMemo(() => advanceableWorkableStages(workableStages), [workableStages]);

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
  // `showStageSection` keeps the stage row (incl. the "no stages found"
  // notice) visible whenever the caller asked for a pick. But a pick can
  // only be *required* when there are stages to pick from — when
  // stageOptions is empty (load failed / no workable_job_id) the advance
  // proceeds on the internal stage, so don't gate confirm on an
  // impossible pick.
  const showStageSection = Boolean(alternative.requireStagePick);
  const requireStagePick = showStageSection && stageOptions.length > 0;
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
        res = await agentApi.approveDecision(decision.id, payload, {
          force: Boolean(decision.is_stale),
        });
      } else {
        res = await agentApi.overrideDecision(decision.id, {
          ...payload,
          override_action: alternative.action,
        });
      }
      onSubmitted?.(res?.data || null);
      onClose?.();
    } catch (err) {
      setError(typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : `${mode === 'approve' ? "Couldn't approve — try again." : "Couldn't override — try again."}`);
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
            <h3 className="home-title-md" style={{ margin: '6px 0 2px' }}>
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
          {showStageSection ? (
            <div className="rq-modal-section">
              <span className="rq-modal-label" id="rq-target-stage-label">
                Move to which Workable stage? (required)
              </span>
              {stageOptions.length === 0 ? (
                <span style={{ fontSize: 12, color: 'var(--mute)' }}>
                  This Workable job has no advance stages — only pre-application stages (Sourced / Applied) exist. The candidate advances on Taali's internal stage; nothing posts to Workable. Add interview/offer stages to the job in Workable to move them there.
                </span>
              ) : (
                <div
                  className="rq-modal-pills"
                  role="radiogroup"
                  aria-labelledby="rq-target-stage-label"
                >
                  {stageOptions.map((stage) => {
                    const isCurrent =
                      String(decision?.workable_stage || '').toLowerCase() ===
                      stage.value.toLowerCase();
                    const isOn = targetStage === stage.value;
                    return (
                      <button
                        key={stage.value}
                        type="button"
                        role="radio"
                        aria-checked={isOn}
                        className={`rq-modal-pill ${isOn ? 'on' : ''}`}
                        disabled={submitting || isCurrent}
                        onClick={() => setTargetStage(stage.value)}
                        title={isCurrent ? 'Candidate is already at this stage' : undefined}
                      >
                        <span>{stage.label}</span>
                        {isCurrent ? (
                          <span className="rq-modal-pill-sub">Current</span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              )}
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
