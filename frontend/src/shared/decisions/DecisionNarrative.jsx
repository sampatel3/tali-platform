import React, { useState } from 'react';

import './decisionNarrative.css';
import { normaliseDecisionText } from './decisionText';
import { explanationFactorTotal, ruleChipText, splitVerdict } from './decisionPresentation';

const normalise = normaliseDecisionText;

const statusLabel = (value) => {
  const status = normalise(value).toLowerCase();
  if (status === 'unknown') return 'Unverified';
  if (['not_met', 'not met', 'failed', 'fail', 'no'].includes(status)) return 'Not met';
  return 'Missing';
};

// Body longer than this clamps to 2 lines with a Show more toggle. A length
// heuristic — not a layout measurement — where ~180 chars is roughly two lines
// at the summary's width; shorter bodies never render a toggle they don't need.
const CLAMP_CHARS = 180;

// 2-line-clamped prose + Show more/less, shown only when the text is long enough
// to overflow. Used for the card-density agent reasoning.
const ClampBlock = ({ text, className }) => {
  const [expanded, setExpanded] = useState(false);
  const clampable = text.length > CLAMP_CHARS;
  return (
    <>
      <p className={`${className}${clampable && !expanded ? ' is-clamped' : ''}`}>{text}</p>
      {clampable ? (
        <button
          type="button"
          className="decision-narrative-toggle"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      ) : null}
    </>
  );
};

// Factor chips — small "✕ label" pills, status in the title tooltip. `max`
// chips render, the rest collapse into a "+N more" tail. `total` is the true
// blocker count (the API caps the factors list at 5), so the tail counts
// blockers the payload doesn't even carry.
const FactorChips = ({ factors, max, total = factors.length }) => {
  const shown = factors.slice(0, max);
  const extra = total - shown.length;
  return (
    <div className="decision-narrative-chips" aria-label="Decisive requirements">
      {shown.map((factor, index) => (
        <span
          key={`${normalise(factor.label)}-${index}`}
          className="decision-narrative-chip"
          title={statusLabel(factor.status)}
        >
          ✕ {normalise(factor.label)}
        </span>
      ))}
      {extra > 0 ? <span className="decision-narrative-chip-more">+{extra} more</span> : null}
    </div>
  );
};

// `density` picks the surface: 'card' (AgentDecisionCard / rail — chips + clamped
// summary, no boxed prose) or 'report' (candidate report — one merged FIT SUMMARY
// block). `compact` is the retired boolean prop: true maps to density='card'.
export const DecisionNarrative = ({ decision, density = 'report', compact = false, showPolicyReason = false }) => {
  if (!decision) return null;
  const resolvedDensity = compact ? 'card' : density;

  const explanation = decision.decision_explanation && typeof decision.decision_explanation === 'object'
    ? decision.decision_explanation
    : null;
  const decisionReason = normalise(explanation?.summary || decision.reasoning);
  const candidateSummary = normalise(decision.candidate_summary);
  const context = normalise(explanation?.context);
  const factors = Array.isArray(explanation?.factors)
    ? explanation.factors.filter((item) => item && normalise(item.label))
    : [];
  // Dedupe: a candidate summary that just restates the decision reason adds
  // nothing — drop it.
  const showCandidateSummary = candidateSummary
    && candidateSummary.toLowerCase() !== decisionReason.toLowerCase();
  const source = explanation?.source === 'policy' ? 'policy' : 'agent';
  const { verdict, body } = splitVerdict(candidateSummary);

  if (!decisionReason && !candidateSummary) return null;

  // Legacy cached payloads have no structured explanation — degrade to the
  // pre-redesign plain reasoning paragraph. Card density drops the candidate
  // summary (it lives in the candidate report); report density keeps it.
  if (!explanation) {
    if (resolvedDensity === 'card' && !decisionReason) return null;
    const showLegacySummary = resolvedDensity !== 'card' && showCandidateSummary;
    return (
      <div className={`decision-narrative is-${resolvedDensity}`}>
        {decisionReason ? (
          <section className="decision-narrative-block" aria-label="Why this decision">
            <div className="decision-narrative-kicker">
              {source === 'policy' ? 'WHY THE POLICY RECOMMENDS THIS' : 'WHY THE AGENT RECOMMENDS THIS'}
            </div>
            <p className="decision-narrative-primary">{decisionReason}</p>
          </section>
        ) : null}
        {showLegacySummary ? (
          <section className="decision-narrative-block decision-narrative-candidate" aria-label="Candidate summary">
            <div className="decision-narrative-kicker">CANDIDATE SUMMARY</div>
            <p className="decision-narrative-summary">{candidateSummary}</p>
          </section>
        ) : null}
      </div>
    );
  }

  if (resolvedDensity === 'card') {
    // Cards carry only the cause: must-have chips (policy) and the agent's own
    // reasoning. The candidate summary is dropped — it's in the candidate
    // report — so a policy card with no factors renders nothing here (the
    // chip + "why?" on the AgentDecisionCard kicker row carry the cause).
    const showMustHaveChips = source === 'policy'
      && explanation.rule === 'must_have_blocked'
      && factors.length > 0;
    const showAgentReason = source !== 'policy' && Boolean(decisionReason);
    // Pending cards carry the policy cause on the recommendation slab (chip +
    // "why?"), but resolved/processing cards have no slab — the caller sets
    // showPolicyReason so the cause still renders on history surfaces.
    const showPolicyReasonBlock = showPolicyReason && source === 'policy' && Boolean(decisionReason);
    if (!showMustHaveChips && !showAgentReason && !showPolicyReasonBlock) return null;
    return (
      <div className="decision-narrative is-card">
        {showMustHaveChips ? (
          <FactorChips factors={factors} max={3} total={explanationFactorTotal(explanation)} />
        ) : null}

        {showAgentReason || showPolicyReasonBlock ? (
          <section className="decision-narrative-block" aria-label="Why this decision">
            <div className="decision-narrative-kicker">
              {source === 'policy' ? 'WHY THE POLICY RECOMMENDS THIS' : 'WHY THE AGENT RECOMMENDS THIS'}
            </div>
            <ClampBlock text={context ? `${decisionReason} ${context}` : decisionReason} className="decision-narrative-primary" />
          </section>
        ) : null}
      </div>
    );
  }

  // report density — one merged FIT SUMMARY block: source · chip · decision type
  // heading, un-clamped summary, then the causal sentence as a quiet note.
  const sourceLabel = source === 'policy' ? 'Policy' : 'Agent';
  const chip = ruleChipText(decision);
  const decisionWords = normalise(String(decision.decision_type || '').replace(/_/g, ' '));
  const headParts = [`✦ ${sourceLabel}`, chip, decisionWords].filter(Boolean);
  const revisionId = explanation.policy_revision_id;
  const causal = context ? `${decisionReason} ${context}` : decisionReason;

  return (
    <div className="decision-narrative is-report">
      <section className="decision-narrative-block" aria-label="Fit summary">
        <div className="decision-narrative-kicker is-mute">FIT SUMMARY</div>
        <div className="decision-narrative-head">
          {verdict ? <span className="decision-narrative-pill">{verdict}</span> : null}
          {headParts.length ? (
            <span className="decision-narrative-rulechip">{headParts.join(' · ')}</span>
          ) : null}
        </div>
        {factors.length ? (
          <FactorChips factors={factors} max={5} total={explanationFactorTotal(explanation)} />
        ) : null}
        {showCandidateSummary ? <p className="decision-narrative-summary">{body}</p> : null}
        {causal ? (
          <div className="decision-narrative-note">
            <p className="decision-narrative-note-text">{causal}</p>
            {revisionId != null ? (
              <div className="decision-narrative-prov">policy revision #{revisionId}</div>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
};

export default DecisionNarrative;
