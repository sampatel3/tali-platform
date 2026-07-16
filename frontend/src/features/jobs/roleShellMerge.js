// The role shell deliberately omits expensive detail and aggregate state, but
// RoleResponse fills those omissions with null/empty/default values. Merge only
// fields the shell reads authoritatively so a poll cannot erase an already
// loaded job specification, interview pack, requisition, or pipeline counts.
const ROLE_SHELL_AUTHORITATIVE_FIELDS = [
  'id',
  'version',
  'organization_id',
  'name',
  'source',
  'role_kind',
  'ats_owner_role_id',
  'ats_owner_role_name',
  'effective_workable_job_id',
  'ats_provider',
  'external_job_id',
  'external_job_state',
  'external_job_live',
  'workable_job_id',
  'job_status',
  'workable_job_state',
  'workable_job_live',
  'auto_reject_threshold_mode',
  'workable_actor_member_id',
  'starred_for_auto_sync',
  'agentic_mode_enabled',
  'agent_action_allowlist',
  'agent_token_budget_per_cycle',
  'agent_decision_budget_per_cycle',
  'auto_reject',
  'auto_reject_pre_screen',
  'auto_promote',
  'auto_send_assessment',
  'auto_resend_assessment',
  'auto_advance',
  'auto_skip_assessment',
  'agent_effective_policy',
  'monthly_usd_budget_cents',
  'score_threshold',
  'agent_paused_at',
  'agent_paused_reason',
  'agent_last_run_at',
  'agent_bootstrap_status',
  'agent_bootstrap_error',
  'agent_bootstrap_started_at',
  'agent_bootstrap_completed_at',
  'assessment_task_provisioning',
];

export function mergeRoleShell(currentRole, shellRole) {
  if (!shellRole) return currentRole ?? null;
  if (!currentRole || Number(currentRole.id) !== Number(shellRole.id)) return shellRole;

  const merged = { ...currentRole };
  ROLE_SHELL_AUTHORITATIVE_FIELDS.forEach((field) => {
    if (Object.prototype.hasOwnProperty.call(shellRole, field)) merged[field] = shellRole[field];
  });
  return merged;
}

export default mergeRoleShell;
