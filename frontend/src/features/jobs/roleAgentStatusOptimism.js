const optimisticActor = Object.freeze({ is_current_user: true });

// Role controls remain writable beneath a workspace hold. Keep effective
// workspace fields untouched while updating the local state that should take
// effect once the workspace resumes.
export const optimisticallyPauseRoleAgent = (status, changedAt = new Date().toISOString()) => {
  if (!status) return status;
  const next = {
    ...status,
    role_paused_at: changedAt,
    role_paused_reason: 'paused by recruiter',
    role_paused_by: optimisticActor,
  };
  if (status.workspace_paused) return next;
  return {
    ...next,
    paused: true,
    pause_scope: 'role',
    paused_at: changedAt,
    paused_reason: 'paused by recruiter',
    paused_by: optimisticActor,
  };
};

export const optimisticallyResumeRoleAgent = (status) => {
  if (!status) return status;
  const next = {
    ...status,
    role_paused_at: null,
    role_paused_reason: null,
    role_paused_by: null,
  };
  if (status.workspace_paused) return next;
  return {
    ...next,
    paused: false,
    pause_scope: null,
    paused_at: null,
    paused_reason: null,
    paused_by: null,
  };
};
