// Canonical naming lexicon: agent decision_type → human label.
//
// The single source of truth for how a verdict is *named* across surfaces
// (report verdict band, home queue, pipeline, funnel). Distinct from
// decisionActions.js, which owns the *action* verbs — the button you click
// (e.g. an agent `reject` is approved with a button labelled "Approve",
// while its verdict is named "Reject" here).
//
//   LONG  — verdict phrasing for headlines ("Send assessment", "Reject").
//   SHORT — chip phrasing for dense rows ("Send", "Reject").
//
// These maps are currently duplicated across atoms.jsx, HomeEverything.jsx,
// metrics.js, JobPipelinePage.jsx and AgentsOverviewPanel.jsx. New surfaces
// import from here; the existing ones can be repointed in a follow-up.

// Values match the outcome wording shipped in CandidateDecisionStrip (#739) so
// the verdict band and the strip never disagree.
export const DECISION_LABELS_LONG = {
  send_assessment: 'Send assessment',
  resend_assessment_invite: 'Resend assessment invite',
  advance_to_interview: 'Advance to next stage',
  advance: 'Advance to next stage',
  reject: 'Reject',
  skip_assessment_reject: 'Reject',
  escalate_low_confidence: 'Needs your review',
};

export const DECISION_LABELS_SHORT = {
  send_assessment: 'Send',
  resend_assessment_invite: 'Resend',
  advance_to_interview: 'Advance',
  advance: 'Advance',
  reject: 'Reject',
  skip_assessment_reject: 'Pre-screen',
  escalate_low_confidence: 'Needs you',
};

// Verdict phrasing for a decision (headline use). Returns '' when there is no
// decision so callers can fall back to the report's recommendation label.
export const verdictLabel = (decision) => {
  if (!decision?.decision_type) return '';
  return DECISION_LABELS_LONG[decision.decision_type] || 'Needs your review';
};

export const verdictLabelShort = (decisionType) => (
  DECISION_LABELS_SHORT[decisionType] || 'Needs you'
);
