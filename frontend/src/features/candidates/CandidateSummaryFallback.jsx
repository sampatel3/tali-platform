import React from 'react';

import { normaliseDecisionText } from '../../shared/decisions/decisionText';

// Client/share views and decisions without a synthesis retain the holistic
// fallback; recruiter decision narratives never duplicate it.
export const CandidateSummaryFallback = ({
  agentDecision,
  isClientView,
  recruiterSummaryText,
}) => {
  const decisionSummary = normaliseDecisionText(agentDecision?.candidate_summary);
  const summary = normaliseDecisionText(recruiterSummaryText);
  if ((!isClientView && decisionSummary) || !summary) return null;

  return (
    <section className="mc-why" aria-label="Candidate summary">
      <div className="mc-kicker">CANDIDATE SUMMARY</div>
      <p className="mc-why-reason">{summary}</p>
    </section>
  );
};

export default CandidateSummaryFallback;
