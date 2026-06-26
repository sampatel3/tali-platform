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
import { DECISION_ACTIONS, DEFAULT_ACTIONS } from '../../shared/decisions/decisionActions';
import '../../features/home/home.css';

// Human label for the agent's recommendation, reusing the SAME action
// vocabulary the card + queue use so the strip and the expanded card never
// disagree ("Send assessment", "Advance to next stage", "Approve", …).
const recommendationLabel = (decision) => {
  if (!decision) return '';
  const spec = DECISION_ACTIONS[decision.decision_type] || DEFAULT_ACTIONS;
  return spec.primaryLabel || 'Approve';
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

  // STATE 3 — nothing decided and nothing to decide yet (e.g. not scored).
  // Muted hint, no fabricated actions.
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
      <span>No agent decision yet — score this candidate to get a recommendation.</span>
    </div>
  );
};

export default CandidateDecisionStrip;
