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

import '../../styles/09-standing-report.css';

import { ScoreRing } from '../../shared/ui/ScoreRing';
import { AgentLoop, Reveal } from '../../shared/motion';
import { ScoreProvenance } from './ScoreProvenance';
import {
  buildRejectConsequenceCopy,
  DECISION_ACTIONS,
  DEFAULT_ACTIONS,
  isRejectDecisionType,
  withRoleAwareRejectCopy,
} from '../../shared/decisions/decisionActions';
import { ruleChipText } from '../../shared/decisions/decisionPresentation';
import {
  isApprovalBlockingStale,
  isEngineOnlyStale,
} from '../../shared/decisions/decisionStaleness';
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
  // Candidate identity — the rail is the one surface that renders on EVERY
  // view of the report (recruiter app, recruiter share, client share), so the
  // name/meta live here rather than the app-only AgentHeader, which share
  // routes never render.
  candidateName = '',
  candidateInitials = '',
  candidateMeta = [],
  // Page-level actions (Open in Workable / share links) — recruiter-app
  // only; the page passes null on share routes and client views.
  footerActions = null,
  taaliScore,
  roleFitScore,
  assessmentScore,
  reqMet = 0,
  reqTotal = 0,
  experienceLabel = '',
  decision = null,
  application = null,
  flagCount = 0,
  provenance = null,
  // canDecide = recruiter app or recruiter share; false for external clients
  // and any share/interview view. Gates the entire decision apparatus.
  canDecide = false,
  busy = false,
  // Pre-screen escalation: a candidate the cheap Stage-1 gate filtered out (no
  // full cv_match score). The recruiter can pay to run the full v3 evaluation.
  // Rendered as a first-class rail action in the same dr-* vocabulary as the
  // decision buttons, replacing the old bespoke top-of-page banner.
  preScreenedOut = false,
  preScreenScore = null,
  preScreenReason = '',
  evaluating = false,
  runFullEvaluationBusy = false,
  onApprove,
  onAlternative,
  onTeach,
  onSnooze,
  onReEvaluate,
  onRunFullEvaluation,
}) => {
  const isActionable = Boolean(
    decision && (decision.status === 'pending' || decision.status === 'reverted_for_feedback'),
  );
  const isProcessing = decision?.status === 'processing';
  const outcomeUnknown = isProcessing && Boolean(decision?.outcome_unknown);
  // A re-score is running for this candidate (Re-evaluate on an old-engine
  // score, or a bulk re-score). Grey the rail + freeze actions until the fresh
  // score lands — mirrors the hub's AgentDecisionCard (PR 872). The report's
  // decision poll un-freezes it automatically.
  const rescoring = isActionable && Boolean(decision?.rescore_in_flight);
  // Changed inputs require re-evaluation. An unchanged score from an older
  // engine retains the deliberately bounded single-row approval path.
  const isStale = isActionable && Boolean(decision?.is_stale);
  const staleEngineOnly = isEngineOnlyStale(decision);
  const approvalBlockedByStaleness = isApprovalBlockingStale(decision);
  const frozen = busy || rescoring;
  const primaryTitle = staleEngineOnly
    ? 'Scored by an older version of Taali’s scoring — this approves the old score as-is. Re-evaluate first to refresh it.'
    : isStale
      ? 'Inputs changed since this was decided — re-evaluate before approving.'
      : undefined;
  const spec = isActionable ? (DECISION_ACTIONS[decision.decision_type] || DEFAULT_ACTIONS) : null;
  const PrimaryIcon = spec?.primaryIcon || Check;
  // Reject verdicts act on the ATS the instant they're approved — disqualify in
  // Workable + send the rejection email. Surface that consequence on the one-click
  // primary button (the copy previously lived only in the alt-reject modal). The
  // stale/old-engine warning still takes precedence in the tooltip.
  const isRejectDecision = isActionable && isRejectDecisionType(decision.decision_type);
  const rejectConsequenceCopy = buildRejectConsequenceCopy(
    decision?.role_family,
    decision?.role_id,
  );
  const alternatives = (spec?.alternatives || [])
    .map((alternative) => withRoleAwareRejectCopy(
      alternative,
      decision?.role_family,
      decision?.role_id,
    ));
  const primaryButtonTitle = primaryTitle ?? (isRejectDecision ? rejectConsequenceCopy : undefined);
  const decisionSource = decision?.decision_explanation?.source === 'policy' ? 'policy' : 'agent';
  // The rule chip (score / must-have / confidence) rides the kicker; the full
  // explanation lives in the report body, so the rail carries no prose.
  const railChip = ruleChipText(decision);
  const outcome = resolvedOutcomeLabel(application);
  const isScored = application?.cv_match_score != null;
  const postHandover = isPostHandoverWorkableStage(application?.workable_stage);

  const metaItems = (Array.isArray(candidateMeta) ? candidateMeta : []).filter(Boolean);

  return (
    <Reveal as="aside" className="dossier-rail" x={-16} y={0}>
      {candidateName ? (
        <div className="dr-id">
          <div className="dr-id-avatar" aria-hidden="true">{candidateInitials || 'C'}</div>
          <div className="dr-id-name">{candidateName}</div>
          {metaItems.length ? (
            <div className="dr-id-meta">
              {metaItems.map((item) => <span key={item}>{item}</span>)}
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="dr-score">
        {/* Unscored candidates have a null Taali score — read "—", not a
            genuine-looking 0/100 ring. ScoreRing's `display` override renders
            the centre text (and stays 0-arc). */}
        <ScoreRing
          score={taaliScore == null ? 0 : Number(taaliScore)}
          display={taaliScore == null ? '—' : null}
          label=""
          size={104}
          strokeWidth={9}
        />
        <div className="dr-score-label">Taali score</div>
      </div>

      {/* Pre-screen escalation — a first-class rail action, not a page banner.
          Shows the cheap-gate verdict as context (a dr-hint) and offers the
          full evaluation as a standard dr-btn, the same path the decision
          actions below use. Renders above any emitted skip_assessment_reject
          decision so the recruiter can accept the cheap reject OR pay for the
          full look. */}
      {canDecide && preScreenedOut ? (
        <div className="dr-prescreen" data-internal-only>
          <div className="dr-hint" role="status">
            <strong>
              Filtered out by pre-screen{preScreenScore != null ? ` · ${Math.round(preScreenScore)}/100` : ''}.
            </strong>{' '}
            {evaluating
              ? 'Running a full CV evaluation now — the report updates automatically when the score lands.'
              : (preScreenReason || 'Pre-screening found this CV unlikely to meet the role’s must-haves.')}
          </div>
          <button
            type="button"
            className="dr-btn dr-btn-wide"
            onClick={() => onRunFullEvaluation?.()}
            disabled={evaluating || runFullEvaluationBusy}
          >
            {evaluating ? (
              <><Sparkles size={14} strokeWidth={2} aria-hidden="true" className="rq-spin" /> Evaluating…</>
            ) : runFullEvaluationBusy ? (
              'Queuing…'
            ) : (
              <><Sparkles size={14} strokeWidth={2} aria-hidden="true" /> Run full evaluation</>
            )}
          </button>
        </div>
      ) : null}

      {canDecide && isActionable ? (
        <div data-internal-only className={rescoring ? 'is-rescoring' : undefined}>
          {/* Re-score in flight: one banner at the top; everything below is
              greyed (.is-rescoring) and the actions frozen so nothing is
              approved on a score that's being replaced. */}
          {rescoring ? (
            <div
              className="dr-hint"
              role="status"
              style={{ display: 'flex', alignItems: 'center', gap: 8 }}
            >
              <RefreshCw size={13} strokeWidth={2} aria-hidden="true" className="rq-spin" />
              <span>Re-scoring this candidate — the recommendation updates automatically when the new score lands.</span>
            </div>
          ) : null}
          <div className="dr-rec">
            <AgentLoop
              as="button"
              kind="flow"
              active={!frozen && !approvalBlockedByStaleness}
              type="button"
              className="dr-rec-btn"
              onClick={() => onApprove?.(decision)}
              disabled={frozen || approvalBlockedByStaleness}
              title={primaryButtonTitle}
            >
              <PrimaryIcon size={16} strokeWidth={2.2} aria-hidden="true" /> {spec.primaryLabel}
            </AgentLoop>
            {isRejectDecision ? (
              <div className="dr-rec-conf">{rejectConsequenceCopy}</div>
            ) : null}
            <div className="dr-rec-kl">
              <Sparkles size={14} strokeWidth={2.2} aria-hidden="true" /> {decisionSource === 'policy' ? 'Policy' : 'Agent'}
              {railChip ? <span className="dr-rec-chip">{railChip}</span> : null}
            </div>
          </div>

          {/* Changed inputs block the primary recommendation; old-engine-only
              staleness remains explicitly approvable for this single row. */}
          {isStale && !rescoring ? (
            <div className="dr-hint" role="alert">
              <span>
                {staleEngineOnly
                  ? 'This score came from an older version of Taali’s scoring. Re-evaluate to refresh it before approving.'
                  : 'Inputs changed since this was decided. Re-evaluate to refresh before approving.'}
              </span>
            </div>
          ) : null}

          {/* Reject recommended for a candidate already advanced in Workable:
              warn before the one-click approve. Advice, never a block. */}
          {postHandover
            && (decision.decision_type === 'reject' || decision.decision_type === 'skip_assessment_reject') ? (
              <div className="dr-hint" role="alert">
                <span>
                  <strong>Heads up —</strong> in <strong>{application.workable_stage}</strong> in
                  Workable. Approving this reject disqualifies them there.
                </span>
              </div>
            ) : null}

          {flagCount > 0 ? (
            <div className="dr-flags-chip">
              <Flag size={13} strokeWidth={2.2} aria-hidden="true" />
              {flagCount} flag{flagCount === 1 ? '' : 's'} · verify before deciding
            </div>
          ) : null}

          <div className="dr-actions">
            {alternatives.map((alt) => {
              const AltIcon = alt.icon || X;
              return (
                <button
                  key={alt.action}
                  type="button"
                  className="dr-btn dr-btn-counter"
                  onClick={() => onAlternative?.(decision, alt)}
                  disabled={frozen}
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
              disabled={frozen}
            >
              <Brain size={14} strokeWidth={2} aria-hidden="true" /> Teach
            </button>
            <button
              type="button"
              className="dr-btn"
              onClick={() => onSnooze?.(decision)}
              disabled={frozen}
            >
              <Clock size={14} strokeWidth={2} aria-hidden="true" /> Snooze
            </button>
            {onReEvaluate ? (
              <button
                type="button"
                className="dr-btn dr-btn-wide"
                onClick={() => onReEvaluate(decision)}
                disabled={frozen}
              >
                <RefreshCw size={14} strokeWidth={2} aria-hidden="true" /> Re-evaluate
              </button>
            ) : null}
          </div>
        </div>
      ) : canDecide && isProcessing ? (
        <div className="dr-decided" data-internal-only role="status">
          <div className="dr-rec-kl">
            <Sparkles size={14} strokeWidth={2.2} aria-hidden="true" /> Decision
          </div>
          <div className="dr-decided-outcome">{outcomeUnknown ? 'Checking status' : 'Processing'}</div>
          <div className="dr-rec-conf">
            {outcomeUnknown
              ? 'Outcome unconfirmed — actions stay read-only while Taali checks it'
              : 'Accepted — actions are read-only while Taali completes it'}
          </div>
        </div>
      ) : canDecide && outcome ? (
        // Resolved — surface the decision that was MADE with the same slab
        // treatment as the agent's recommendation (read-only: the outcome, not
        // an action). Mirrors the retired strip's STATE 2.
        <div className="dr-decided" data-internal-only>
          <div className="dr-rec-kl">
            <Check size={14} strokeWidth={2.2} aria-hidden="true" /> Decision
          </div>
          <div className="dr-decided-outcome">{outcome}</div>
          <div className="dr-rec-conf">Resolved — no agent action pending</div>
        </div>
      ) : canDecide && !preScreenedOut ? (
        // Honest "why no card" hint (mirrors the retired strip's STATE 3).
        // Suppressed when preScreenedOut — the pre-screen block above already
        // explains why there's no full score and offers the escalation.
        <div className="dr-hint" data-internal-only>
          {!isScored ? (
            <span>No agent decision yet — score this candidate to get a recommendation.</span>
          ) : postHandover ? (
            <span>
              In <strong>{application.workable_stage}</strong> in Workable — the recommendation
              will appear here shortly. Taali advises; acting on a reject here is always your call.
            </span>
          ) : (
            <span>Scored — the recommendation will appear here shortly.</span>
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

      {footerActions ? (
        <div className="dr-page-actions" data-internal-only>{footerActions}</div>
      ) : null}

      {provenance ? (
        <ScoreProvenance provenance={provenance} className="dr-prov" density="full" />
      ) : null}
    </Reveal>
  );
};

export default DecisionRail;
