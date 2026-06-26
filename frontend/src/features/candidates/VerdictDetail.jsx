// VerdictDetail — the "why" beneath the verdict band:
//   1. the agent's reasoning + the deterministic DECISION TRACE built from the
//      decision's evidence (policy_basis + rule_path), and
//   2. INTEGRITY & CORROBORATION as a FIRST-CLASS section listing the
//      claims-to-verify — previously a small block buried inside the CV-match
//      card, below the scores it qualifies.
// Recruiter-only; the page gates rendering on !isClientView.
import React from 'react';
import { AlertTriangle, ShieldCheck } from 'lucide-react';

// Strip the engine's rule prefixes for display ("rule:fired:role_fit…" → the
// bare expression; "point:send_assessment" → "send assessment").
const prettyTraceStep = (step) => {
  const s = String(step || '');
  if (s.startsWith('rule:fired:')) return s.slice(11);
  if (s.startsWith('rule:skipped:')) return s.slice(13).replace(/_/g, ' ');
  if (s.startsWith('point:')) return s.slice(6).replace(/_/g, ' ');
  return s.replace(/_/g, ' ');
};

export const VerdictDetail = ({ decision = null, integrity = null, claimsToVerify = null }) => {
  const reason = decision?.reasoning || '';
  const evidence = decision?.evidence || null;
  const policyBasis = evidence?.policy_basis || '';
  const rulePath = Array.isArray(evidence?.rule_path) ? evidence.rule_path : [];

  // Only surface claims that still need verifying (drop the corroborated ones).
  const claims = Array.isArray(claimsToVerify)
    ? claimsToVerify.filter((c) => String(c?.corroboration || '').toLowerCase() !== 'corroborated')
    : [];
  const warnings = Array.isArray(integrity?.warnings) ? integrity.warnings : [];
  const showIntegrity = Boolean(integrity) || claims.length > 0;
  const showWhy = Boolean(reason) || rulePath.length > 0;

  if (!showWhy && !showIntegrity) return null;

  return (
    <>
      {showWhy ? (
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
      ) : null}

      {showIntegrity ? (
        <section className="mc-integrity" aria-label="Integrity and corroboration">
          <div className="mc-kicker">INTEGRITY &amp; CORROBORATION</div>
          {claims.length ? (
            <ul className="mc-integrity-list">
              {claims.map((claim, index) => (
                <li key={`claim-${index}`} className="mc-integrity-item">
                  <AlertTriangle size={14} strokeWidth={2.2} className="mc-integrity-ic" aria-hidden="true" />
                  <span className="mc-integrity-body">
                    <span className="mc-integrity-claim">{claim?.claim_text || 'Unverified claim'}</span>
                    {claim?.reasoning ? <span className="mc-integrity-why"> — {claim.reasoning}</span> : null}
                  </span>
                  <span className="mc-integrity-tag">
                    {String(claim?.corroboration || 'unverified').replace(/_/g, ' ')}
                  </span>
                </li>
              ))}
            </ul>
          ) : warnings.length ? (
            <ul className="mc-integrity-list">
              {warnings.map((warning, index) => (
                <li key={`warn-${index}`} className="mc-integrity-item">
                  <AlertTriangle size={14} strokeWidth={2.2} className="mc-integrity-ic" aria-hidden="true" />
                  <span className="mc-integrity-body">{warning}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="mc-integrity-clean">
              <ShieldCheck size={14} strokeWidth={2.2} aria-hidden="true" />
              No integrity concerns — claims corroborate the CV.
            </div>
          )}
        </section>
      ) : null}
    </>
  );
};

export default VerdictDetail;
