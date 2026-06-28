// VerdictDetail — "why this verdict": the agent's plain-English reasoning.
// The deterministic rule-path trace box (policy_basis + the cryptic rule list)
// was removed — recruiters read the reason; the rule list added noise without
// value. Integrity lives in the Flags section. Recruiter-only; the page gates
// rendering on !isClientView. Renders nothing without a decision.
import React from 'react';

export const VerdictDetail = ({ decision = null }) => {
  const reason = decision?.reasoning || '';
  if (!reason) return null;

  return (
    <section className="mc-why" aria-label="Why this verdict">
      <div className="mc-kicker">WHY THIS VERDICT</div>
      <p className="mc-why-reason">{reason}</p>
    </section>
  );
};

export default VerdictDetail;
