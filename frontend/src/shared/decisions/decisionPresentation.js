// Rule-chip + verdict derivation for the decision-narrative redesign.
//
// `ruleChipText` turns a decision's structured explanation into the short chip
// that rides the recommendation kicker ("72 ≥ 55", "2 must-haves missing",
// "Confidence 84%"). `splitVerdict` peels a "Verdict — body" candidate summary
// into a pill head + prose. Both degrade to null/whole-body on legacy payloads.

// Integer when whole, one decimal otherwise — mirrors how the scoring API sends
// role_fit_score / threshold, so the chip reads exactly like the source number.
const formatScore = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
};

// A related role owns its own membership, score, decision, and pipeline state.
// The provider application date is still useful evidence, but it must never be
// presented as proof that the roles share one candidate pool.
export const applicationDateContext = (decision) => {
  const ownerRoleId = decision?.role_family?.owner?.id;
  const decisionRoleId = decision?.role_id;
  const crossesRoleBoundary = ownerRoleId != null
    && decisionRoleId != null
    && String(ownerRoleId) !== String(decisionRoleId);
  const linkedAtsEvidence = decision?.evidence?.ats_transport_linked === true
    || decision?.evidence?.shared_ats_application === true
    || crossesRoleBoundary;

  return linkedAtsEvidence
    ? {
      label: 'Linked ATS application dated',
      title: 'When the linked ATS evidence application was submitted',
    }
    : {
      label: 'Applied',
      title: 'When this application was submitted — how fresh the candidate is',
    };
};

// True factor count. The API caps `factors` to the first 5 rows but sends the
// real count as `factors_total` — prefer it so a 7-blocker reject reads
// "7 must-haves missing", not "5". Legacy payloads without the key (or with a
// stale total below the visible rows) fall back to the visible length.
export const explanationFactorTotal = (explanation) => {
  const visible = Array.isArray(explanation?.factors) ? explanation.factors.length : 0;
  const total = Number(explanation?.factors_total);
  return Number.isFinite(total) && total >= visible ? total : visible;
};

export const ruleChipText = (decision) => {
  const explanation = decision?.decision_explanation;
  if (!explanation || typeof explanation !== 'object') return null;

  const source = explanation.source === 'policy' ? 'policy' : 'agent';

  // Agent judgment: the confidence IS the chip (deterministic rules never carry
  // model confidence). Guard null before coercing — Number(null) is 0, which
  // would render a fabricated "Confidence 0%" for decisions with no confidence.
  if (source !== 'policy') {
    if (decision?.confidence == null) return null;
    const confidence = Number(decision.confidence);
    return Number.isFinite(confidence) ? `Confidence ${Math.round(confidence * 100)}%` : null;
  }

  const rule = typeof explanation.rule === 'string' ? explanation.rule : '';
  const scoreCtx = explanation.score_context && typeof explanation.score_context === 'object'
    ? explanation.score_context
    : null;

  const scoreDecisive = Boolean(scoreCtx) && (
    scoreCtx.score_was_decisive
    || rule.includes('role_fit_score')
    || rule.includes('pre_screen_auto_reject_eligible')
  );
  // Legacy explanations can carry a score rule with null score/threshold —
  // Number(null) is 0, which would fabricate a "0 < 0" comparison. Missing
  // audit data degrades to no chip instead.
  if (scoreDecisive
    && scoreCtx.role_fit_score != null && Number.isFinite(Number(scoreCtx.role_fit_score))
    && scoreCtx.threshold != null && Number.isFinite(Number(scoreCtx.threshold))) {
    const score = formatScore(scoreCtx.role_fit_score);
    const threshold = formatScore(scoreCtx.threshold);
    return scoreCtx.threshold_passed ? `${score} ≥ ${threshold}` : `${score} < ${threshold}`;
  }

  if (rule === 'must_have_blocked') {
    const n = explanationFactorTotal(explanation);
    if (n === 0) return 'must-have rule';
    return `${n} must-have${n === 1 ? '' : 's'} missing`;
  }

  if (rule === 'knockout_screening') return 'knockout answer';

  return null;
};

// Split a candidate summary on the FIRST " — " (space-emdash-space). The head is
// a pill only when it's short enough to read as a verdict tag (≤ 40 chars);
// anything longer (or an empty tail, or no em-dash at all) stays whole body.
export const splitVerdict = (candidateSummary) => {
  const text = String(candidateSummary || '').replace(/\s+/g, ' ').trim();
  if (!text) return { verdict: null, body: '' };
  const idx = text.indexOf(' — ');
  if (idx === -1) return { verdict: null, body: text };
  const head = text.slice(0, idx).trim();
  const tail = text.slice(idx + 3).trim();
  if (!tail || head.length > 40) return { verdict: null, body: text };
  return { verdict: head, body: tail };
};
