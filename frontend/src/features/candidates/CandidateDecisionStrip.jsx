// CandidateDecisionStrip — the agent-decision surface on the candidate
// standing report header. Mirrors the /home review queue: the SAME
// Approve / Override / Teach controls, reusing <AgentDecisionCard> verbatim
// for the expanded view.
//
// Three states (recruiter-view only — the page gates rendering):
//   1. PENDING decision present → a compact one-line summary (the agent's
//      recommendation + action label) with a "Review & decide" toggle that
//      expands the full <AgentDecisionCard> with its action bar wired to the
//      modals.
//   2. NO pending decision but the application is RESOLVED (rejected / hired,
//      or advanced past review) → a read-only outcome chip, no actions.
//   3. NO decision and not resolved (e.g. not scored yet) → a muted hint to
//      score the candidate. No fabricated actions.
//
// The page owns the data + modal state (parity with HomeNow owning the queue
// state); this component owns only the local collapse/expand toggle and hands
// every action back up via props.
import React, { useState } from 'react';
import { ChevronDown, ChevronUp, Sparkles } from 'lucide-react';

import { AgentDecisionCard } from '../../shared/decisions/AgentDecisionCard';
import { isPostHandoverWorkableStage } from '../../shared/metrics';
import '../../features/home/home.css';

// Human label for what the agent DECIDED — the outcome, not the button you
// press to confirm it. (For a reject, the primary button is labelled "Approve"
// = approve the rejection, which read as "Agent recommends Approve" on a reject
// card. Show the decision instead: "Agent recommends Reject".)
const DECISION_OUTCOME_LABEL = {
  reject: 'Reject',
  skip_assessment_reject: 'Reject',
  send_assessment: 'Send assessment',
  advance_to_interview: 'Advance to next stage',
  resend_assessment_invite: 'Resend assessment invite',
};
const recommendationLabel = (decision) => {
  if (!decision) return '';
  return DECISION_OUTCOME_LABEL[decision.decision_type] || 'Review';
};

// Map a resolved application to a read-only outcome chip. Returns null when
// the application isn't in a terminal/advanced state, so the caller can fall
// through to the "no decision yet" hint.
const resolvedOutcomeLabel = (application) => {
  const outcome = String(application?.application_outcome || '').toLowerCase();
  if (outcome === 'rejected' || outcome === 'declined') return 'Rejected';
  if (outcome === 'hired') return 'Hired';
  const stage = String(application?.pipeline_stage || '').toLowerCase();
  if (stage === 'advanced') return 'Advanced';
  return null;
};

export const CandidateDecisionStrip = ({
  decision,
  application,
  recommendation,
  busy,
  onApprove,
  onAlternative,
  onTeach,
  onSnooze,
  onReEvaluate,
  onNavigate,
}) => {
  const [expanded, setExpanded] = useState(false);

  // STATE 1 — a live pending (or returned-for-feedback) decision the recruiter
  // can act on. Collapsed = one compact plum line; expanded = the full card.
  if (decision) {
    const summary = recommendationLabel(decision);
    return (
      <div
        data-internal-only
        className="cand-decision-strip"
        style={{
          marginTop: 4,
          marginBottom: 14,
          borderRadius: 12,
          border: '1px solid color-mix(in oklab, var(--purple) 30%, var(--line))',
          background: 'var(--purple-soft)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '10px 14px',
            flexWrap: 'wrap',
          }}
        >
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 7,
              color: 'var(--purple)',
              fontWeight: 600,
              fontSize: '0.8125rem',
            }}
          >
            <Sparkles size={15} strokeWidth={2.2} aria-hidden="true" />
            Agent recommends
          </span>
          <span style={{ fontSize: '0.875rem', color: 'var(--ink)', fontWeight: 600 }}>
            {summary}
          </span>
          {decision.is_stale ? (
            <span
              style={{
                fontSize: '0.6875rem',
                fontWeight: 600,
                letterSpacing: '.04em',
                textTransform: 'uppercase',
                color: 'var(--purple)',
                border: '1px solid color-mix(in oklab, var(--purple) 35%, var(--line))',
                borderRadius: 999,
                padding: '2px 8px',
              }}
            >
              Inputs changed
            </span>
          ) : null}
          <button
            type="button"
            className="btn btn-purple btn-sm"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            {expanded ? 'Hide' : 'Review & decide'}
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>

        {expanded ? (
          <div
            style={{
              borderTop: '1px solid color-mix(in oklab, var(--purple) 20%, var(--line))',
              background: 'var(--bg)',
              padding: 14,
            }}
          >
            <AgentDecisionCard
              decision={decision}
              busy={busy}
              onApprove={onApprove}
              onAlternative={onAlternative}
              onReEvaluate={onReEvaluate}
              onSnooze={onSnooze}
              onTeach={onTeach}
              onNavigate={onNavigate}
            />
          </div>
        ) : null}
      </div>
    );
  }

  // STATE 2 — no pending decision, but the application has reached a terminal /
  // advanced state. Read-only chip; the decision was already made.
  const outcome = resolvedOutcomeLabel(application);
  if (outcome) {
    return (
      <div
        data-internal-only
        className="cand-decision-strip"
        style={{
          marginTop: 4,
          marginBottom: 14,
          padding: '10px 14px',
          borderRadius: 12,
          border: '1px solid var(--line)',
          background: 'var(--bg-2)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          fontSize: '0.8125rem',
          color: 'var(--ink-2)',
        }}
      >
        <Sparkles size={15} strokeWidth={2.2} aria-hidden="true" style={{ color: 'var(--mute)' }} />
        <span>
          <strong style={{ color: 'var(--ink)' }}>Decision: {outcome}.</strong>{' '}
          This candidate has been resolved — no agent action is pending.
        </span>
      </div>
    );
  }

  // STATE 3 — no agent decision card. The deterministic verdict is emitted
  // automatically the moment a candidate is scored (ensure_deterministic_decision,
  // decoupled from the agent), so "scored + no card" has exactly two honest
  // causes — never "score this candidate" for an already-scored one:
  //  (a) NOT scored → genuinely nothing to decide; prompt to score.
  //  (b) POST-HANDOVER → the policy DELIBERATELY abstains (won't auto-decide
  //      someone a human is interviewing in Workable). Surface Taali's read +
  //      say it's deferred to the recruiter.
  //  (c) scored, not post-handover, still no card → the rare just-scored /
  //      genuine-abstention tail; the verdict will materialise.
  const isScored = application?.cv_match_score != null;
  const recLabel = recommendation?.label && recommendation.label !== 'Continue review'
    ? recommendation.label
    : '';
  const postHandover = isPostHandoverWorkableStage(application?.workable_stage);
  const read = recLabel ? (
    <>Taali&apos;s read: <strong style={{ color: 'var(--ink)' }}>{recLabel}</strong>. </>
  ) : null;
  return (
    <div
      data-internal-only
      className="cand-decision-strip"
      style={{
        marginTop: 4,
        marginBottom: 14,
        padding: '10px 14px',
        borderRadius: 12,
        border: '1px dashed var(--line)',
        background: 'transparent',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        fontSize: '0.8125rem',
        color: 'var(--mute)',
      }}
    >
      <Sparkles size={15} strokeWidth={2.2} aria-hidden="true" />
      {!isScored ? (
        <span>No agent decision yet — score this candidate to get a recommendation.</span>
      ) : postHandover ? (
        <span>
          {read}
          In <strong style={{ color: 'var(--ink)' }}>{application.workable_stage}</strong> in
          Workable — Taali defers to you on candidates you&apos;re interviewing and won&apos;t
          auto-decide them.
        </span>
      ) : (
        <span>{read}Scored — the deterministic decision will appear here once it&apos;s computed.</span>
      )}
    </div>
  );
};

export default CandidateDecisionStrip;
