import React from 'react';

import './decisionNarrative.css';
import { normaliseDecisionText } from './decisionText';

const normalise = normaliseDecisionText;

const statusLabel = (value) => {
  const status = normalise(value).toLowerCase();
  if (status === 'unknown') return 'Unverified';
  if (['not_met', 'not met', 'failed', 'fail', 'no'].includes(status)) return 'Not met';
  return 'Missing';
};

export const DecisionNarrative = ({ decision, compact = false }) => {
  if (!decision) return null;

  const explanation = decision.decision_explanation && typeof decision.decision_explanation === 'object'
    ? decision.decision_explanation
    : null;
  const decisionReason = normalise(explanation?.summary || decision.reasoning);
  const candidateSummary = normalise(decision.candidate_summary);
  const context = normalise(explanation?.context);
  const factors = Array.isArray(explanation?.factors)
    ? explanation.factors.filter((item) => item && normalise(item.label)).slice(0, compact ? 3 : 5)
    : [];
  const showCandidateSummary = candidateSummary
    && candidateSummary.toLowerCase() !== decisionReason.toLowerCase();
  const source = explanation?.source === 'policy' ? 'policy' : 'agent';

  if (!decisionReason && !candidateSummary) return null;

  return (
    <div className={`decision-narrative${compact ? ' is-compact' : ''}`}>
      {decisionReason ? (
        <section className="decision-narrative-block decision-narrative-decision" aria-label="Why this decision">
          <div className="decision-narrative-kicker">
            {source === 'policy' ? 'WHY THE POLICY RECOMMENDS THIS' : 'WHY THE AGENT RECOMMENDS THIS'}
          </div>
          <p className="decision-narrative-primary">{decisionReason}</p>
          {factors.length ? (
            <ul className="decision-narrative-factors" aria-label="Decisive requirements">
              {factors.map((factor, index) => (
                <li key={`${normalise(factor.label)}-${index}`}>
                  <span>{normalise(factor.label)}</span>
                  <b>{statusLabel(factor.status)}</b>
                </li>
              ))}
            </ul>
          ) : null}
          {context ? <p className="decision-narrative-context">{context}</p> : null}
        </section>
      ) : null}

      {showCandidateSummary ? (
        <section className="decision-narrative-block decision-narrative-candidate" aria-label="Candidate summary">
          <div className="decision-narrative-kicker">CANDIDATE SUMMARY</div>
          <p className="decision-narrative-summary">{candidateSummary}</p>
        </section>
      ) : null}
    </div>
  );
};

export default DecisionNarrative;
