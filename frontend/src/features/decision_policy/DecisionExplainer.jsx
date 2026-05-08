import React from 'react';

// Inline component embedded inside the existing AgentDecision panel.
// Reads ``evidence`` (the JSON blob agent_decisions.evidence carries)
// and surfaces the rule_path, sub-agent outputs, and policy revision
// id in a recruiter-readable shape. The parent panel passes evidence
// in directly — no API call here.
export default function DecisionExplainer({ evidence }) {
  if (!evidence || typeof evidence !== 'object') {
    return null;
  }
  const {
    policy_revision_id: policyRevisionId,
    rule_path: rulePath,
    sub_agent_outputs: subAgentOutputs,
    intent_overrode: intentOverrode,
    skipped_due_to_manual: skippedDueToManual,
  } = evidence;

  return (
    <section className="dp-decision-explainer">
      <header>
        <h4>How the policy reached this decision</h4>
        <div className="dp-badges">
          {policyRevisionId && (
            <span className="dp-badge">policy rev #{policyRevisionId}</span>
          )}
          {intentOverrode && (
            <span className="dp-badge dp-badge-info">intent overrode thresholds</span>
          )}
          {skippedDueToManual && (
            <span className="dp-badge dp-badge-warn">recruiter handled manually</span>
          )}
        </div>
      </header>

      {Array.isArray(rulePath) && rulePath.length > 0 && (
        <details open>
          <summary>Rule trace</summary>
          <ol className="dp-rule-trace">
            {rulePath.map((step, idx) => (
              <li key={idx}><code>{step}</code></li>
            ))}
          </ol>
        </details>
      )}

      {subAgentOutputs && (
        <details>
          <summary>Sub-agent outputs</summary>
          {Object.entries(subAgentOutputs).map(([name, payload]) => (
            <div key={name} className="dp-sub-output">
              <strong>{name}</strong>
              <pre>{JSON.stringify(payload, null, 2)}</pre>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
