// Static metadata for the per-role autonomy toggles rendered on the
// Agent settings tab in JobPipelinePage. Kept in its own module so the
// page file stays under the architecture-gate line cap.
//
// ``disabledIfKey`` references another rule's key; when that rule is
// on, this one is rendered disabled (and visibly muted) because the
// master toggle overrides it.

export const AUTONOMY_RULE_META = [
  {
    key: 'auto_reject',
    title: 'Auto-reject (all reasons)',
    sub: 'Every reject the agent queues executes immediately — pre-screen, role-fit, must-have failures, judgment calls. Off: every reject lands in the Decision Hub for one-click approval. Overrides the granular toggle below when on.',
  },
  {
    key: 'auto_reject_prescreen',
    title: 'Auto-reject pre-screen failures',
    sub: 'Only candidates whose pre-screen score is below the role’s reject threshold auto-execute. Judgment-based rejects (role-fit, must-have failures) still go to the Decision Hub. Use this to bulk-cull obvious rejects without giving up HITL on borderline calls.',
    disabledIfKey: 'auto_reject',
  },
  {
    key: 'auto_promote',
    title: 'Auto-promote',
    sub: 'Sending an assessment and advancing to interview happen without approval. Off: each invite/advance queues as a Decision Hub card.',
  },
];

export const AUTONOMY_LABEL_BY_KEY = AUTONOMY_RULE_META.reduce((acc, rule) => {
  acc[rule.key] = rule.title;
  return acc;
}, {});
