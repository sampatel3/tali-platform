const conflictCurrent = (error) => error?.response?.data?.detail?.current || null;

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
