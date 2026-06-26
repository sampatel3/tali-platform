// VerdictDetail — "why this verdict": the agent's reasoning plus the
// deterministic DECISION TRACE built from the decision's evidence
// (policy_basis + rule_path, with the fired rule highlighted). Integrity lives
// in the shared IntegrityFlags readout on the band, not here. Recruiter-only;
// the page gates rendering on !isClientView. Renders nothing without a decision.
import React from 'react';

// Strip the engine's rule prefixes for display ("rule:fired:role_fit…" → the
// bare expression; "point:send_assessment" → "send assessment").
const prettyTraceStep = (step) => {
  const s = String(step || '');
  if (s.startsWith('rule:fired:')) return s.slice(11);
  if (s.startsWith('rule:skipped:')) return s.slice(13).replace(/_/g, ' ');
  if (s.startsWith('point:')) return s.slice(6).replace(/_/g, ' ');
  return s.replace(/_/g, ' ');
};

export const VerdictDetail = ({ decision = null }) => {
  const reason = decision?.reasoning || '';
  const evidence = decision?.evidence || null;
  const policyBasis = evidence?.policy_basis || '';
  const rulePath = Array.isArray(evidence?.rule_path) ? evidence.rule_path : [];

  if (!reason && !rulePath.length) return null;

  return (
    <section className="mc-why" aria-label="Why this verdict">
      <div className="mc-kicker">WHY THIS VERDICT</div>
      {reason ? <p className="mc-why-reason">{reason}</p> : null}
      {rulePath.length ? (
        <div className="mc-trace">
          {policyBasis ? <div className="mc-trace-basis">{policyBasis}</div> : null}
          <ul className="mc-trace-list">
            {rulePath.map((step, index) => {
              const fired = String(step).startsWith('rule:fired');
              return (
                <li key={`trace-${index}`} className={`mc-trace-step ${fired ? 'is-fired' : ''}`}>
                  <span className="mc-trace-dot" aria-hidden="true" />
                  {prettyTraceStep(step)}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
};

export default VerdictDetail;
