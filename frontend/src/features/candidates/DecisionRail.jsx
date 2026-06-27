// DecisionRail — the candidate report's sticky left "decision rail".
//
// Consolidates what used to be three separate surfaces (the four-ring hero, the
// VerdictBand, and the full-width CandidateDecisionStrip) into ONE column:
//   · one canonical Taali score ring
//   · the agent's recommendation as a bold slab + the SAME approve / alternative
//     / teach / snooze / re-evaluate actions as the home queue — reusing the
//     page's handlers verbatim (no re-implemented decision logic; the modal
//     wiring stays in the page, identical to AgentDecisionCard's action bar)
//   · a "flags to verify" chip (the integrity warning count)
//   · the remaining scores demoted to facts, and the score provenance
//
// Pure-ish: every action is handed back up via props. Renders safely with no
// decision and no integrity (external client + demo views) — the recommendation
// slab, the action buttons and the flags chip are all gated on `canDecide` AND a
// present pending decision, so the ring + facts + provenance are all a client or
// the demo ever sees. This is the single decision surface on the report.
import React from 'react';
import { Brain, Check, Clock, Flag, RefreshCw, Sparkles, X } from 'lucide-react';

import { ScoreRing } from '../../shared/ui/ScoreRing';
import { ScoreProvenance } from './ScoreProvenance';
import { DECISION_ACTIONS, DEFAULT_ACTIONS } from '../../shared/decisions/decisionActions';
import { isPostHandoverWorkableStage } from '../../shared/metrics';

const fmt = (v) => (v == null || Number.isNaN(Number(v)) ? '—' : Math.round(Number(v)));

// Read-only outcome chip for a resolved application (mirrors the retired
// CandidateDecisionStrip STATE 2). Returns null when not terminal/advanced.
const resolvedOutcomeLabel = (application) => {
  const outcome = String(application?.application_outcome || '').toLowerCase();
  if (outcome === 'rejected' || outcome === 'declined') return 'Rejected';
  if (outcome === 'hired') return 'Hired';
  if (String(application?.pipeline_stage || '').toLowerCase() === 'advanced') return 'Advanced';
  return null;
};

export const DecisionRail = ({
  taaliScore,
  roleFitScore,
  assessmentScore,
  reqMet = 0,
  reqTotal = 0,
  experienceLabel = '',
  decision = null,
  application = null,
  integrity = null,
  provenance = null,
  // canDecide = recruiter app or recruiter share; false for external clients
  // and any share/interview view. Gates the entire decision apparatus.
  canDecide = false,
  busy = false,
  onApprove,
  onAlternative,
  onTeach,
  onSnooze,
  onReEvaluate,
}) => {
  const isActionable = Boolean(
    decision && (decision.status === 'pending' || decision.status === 'reverted_for_feedback'),
  );
  const spec = isActionable ? (DECISION_ACTIONS[decision.decision_type] || DEFAULT_ACTIONS) : null;
  const PrimaryIcon = spec?.primaryIcon || Check;
  const confPct = decision?.confidence != null && !Number.isNaN(Number(decision.confidence))
    ? Math.round(Number(decision.confidence) * 100)
    : null;
  const warnCount = Array.isArray(integrity?.warnings) ? integrity.warnings.length : 0;
  const outcome = resolvedOutcomeLabel(application);
  const isScored = application?.cv_match_score != null;
  const postHandover = isPostHandoverWorkableStage(application?.workable_stage);

  return (
    <aside className="dossier-rail">
      <div className="dr-score">
        <ScoreRing score={Number(taaliScore) || 0} label="TAALI" size={104} strokeWidth={9} />
      </div>

      {canDecide && isActionable ? (
        <div data-internal-only>
          <div className="dr-rec">
            <div className="dr-rec-kl">
              <Sparkles size={14} strokeWidth={2.2} aria-hidden="true" /> Agent recommends
            </div>
            <button
              type="button"
              className="dr-rec-btn"
              onClick={() => onApprove?.(decision)}
              disabled={busy}
            >
              <PrimaryIcon size={16} strokeWidth={2.2} aria-hidden="true" /> {spec.primaryLabel}
            </button>
            {confPct != null ? <div className="dr-rec-conf">Confidence {confPct}%</div> : null}
          </div>

          {warnCount > 0 ? (
            <div className="dr-flags-chip">
              <Flag size={13} strokeWidth={2.2} aria-hidden="true" />
              {warnCount} flag{warnCount === 1 ? '' : 's'} · verify before deciding
            </div>
          ) : null}

          <div className="dr-actions">
            {(spec.alternatives || []).map((alt) => {
              const AltIcon = alt.icon || X;
              return (
                <button
                  key={alt.action}
                  type="button"
                  className="dr-btn dr-btn-counter"
                  onClick={() => onAlternative?.(decision, alt)}
                  disabled={busy}
                  title={alt.body}
                >
                  <AltIcon size={14} strokeWidth={2} aria-hidden="true" /> {alt.label}
                </button>
              );
            })}
            <button
              type="button"
              className="dr-btn"
              onClick={() => onTeach?.(decision)}
              disabled={busy}
            >
              <Brain size={14} strokeWidth={2} aria-hidden="true" /> Teach
            </button>
            <button
              type="button"
              className="dr-btn"
              onClick={() => onSnooze?.(decision)}
              disabled={busy}
            >
              <Clock size={14} strokeWidth={2} aria-hidden="true" /> Snooze
            </button>
            {onReEvaluate ? (
              <button
                type="button"
                className="dr-btn dr-btn-wide"
                onClick={() => onReEvaluate(decision)}
                disabled={busy}
              >
                <RefreshCw size={14} strokeWidth={2} aria-hidden="true" /> Re-evaluate
              </button>
            ) : null}
          </div>
        </div>
      ) : canDecide ? (
        // No actionable decision — the resolved chip or the honest "why no card"
        // hint (mirrors the retired strip's STATE 2 / STATE 3).
        <div className="dr-hint" data-internal-only>
          {outcome ? (
            <span>
              <strong>Decision: {outcome}.</strong> Resolved — no agent action is pending.
            </span>
          ) : !isScored ? (
            <span>No agent decision yet — score this candidate to get a recommendation.</span>
          ) : postHandover ? (
            <span>
              In <strong>{application.workable_stage}</strong> in Workable — Taali defers to you on
              candidates you&apos;re interviewing.
            </span>
          ) : (
            <span>Scored — the deterministic decision will appear here once it&apos;s computed.</span>
          )}
        </div>
      ) : null}

      <div className="dr-facts">
        <div className="dr-fact"><span>Role fit</span><b>{fmt(roleFitScore)}</b></div>
        <div className="dr-fact"><span>Assessment</span><b>{fmt(assessmentScore)}</b></div>
        {reqTotal ? (
          <div className="dr-fact">
            <span>Requirements</span><b>{reqMet} of {reqTotal}</b>
          </div>
        ) : null}
        {experienceLabel ? (
          <div className="dr-fact"><span>Experience</span><b>{experienceLabel}</b></div>
        ) : null}
      </div>

      {provenance ? (
        <ScoreProvenance provenance={provenance} className="dr-prov" density="full" />
      ) : null}
    </aside>
  );
};

export default DecisionRail;
