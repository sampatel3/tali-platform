const conflictCurrent = (error) => error?.response?.data?.detail?.current || null;

export const workspaceControlVersion = (value) => {
  const version = Number(value);
  return Number.isInteger(version) && version >= 1 ? version : null;
};

// A cached org-status payload can briefly predate the workspace-control
// contract during a rolling deployment. Do not strand the button disabled in
// that state: acknowledge the click, fetch the authoritative status once, and
// then issue the concurrency-safe command with the refreshed version.
export const resolveWorkspaceControlVersion = async (value, refetch) => {
  const current = workspaceControlVersion(value);
  if (current != null) return current;

  const refreshed = typeof refetch === 'function'
    ? await refetch({ force: true })
    : null;
  const resolved = workspaceControlVersion(refreshed?.workspace_control_version);
  if (resolved != null) return resolved;

  throw new Error('Workspace controls could not be refreshed.');
};

export const workspaceControlConflictMessage = (error) => {
  const current = conflictCurrent(error);
  const changedBy = current?.changed_by;
  const actorName = String(changedBy?.name || '').trim();
  const actor = changedBy?.is_current_user
    ? (actorName ? `${actorName} (you)` : 'you')
    : (actorName || 'another team member');
  const action = changedBy?.action === 'paused'
    ? 'paused'
    : (changedBy?.action === 'resumed' ? 'resumed' : 'changed');

  return `The workspace agent was ${action} by ${actor} in another session. The latest state is shown — review it and try again.`;
};
