import { useCallback, useRef, useState } from 'react';

import { agent as agentApi } from '../../shared/api';
import {
  resolveWorkspaceControlVersion,
  workspaceControlConflictMessage,
} from '../../shared/workspaceAgentControl';

// Serializes workspace pause/resume mutations and reconciles the shared status
// before the slower decision and role refreshes continue in the background.
export function useWorkspaceAgentControl({
  loadDecisions,
  loadRoles,
  refetchOrgStatus,
  showToast,
  workspaceControlVersion,
}) {
  const busyRef = useRef(false);
  const [action, setAction] = useState(null);

  const run = useCallback(async (actionName, mutation) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setAction(actionName);
    try {
      let response;
      try {
        response = await mutation();
      } catch (error) {
        try {
          await refetchOrgStatus({ force: true });
        } catch {
          // Keep the original mutation failure: reconciliation is best-effort
          // and must never swallow a collaborator conflict or provider error.
        }
        showToast?.(
          Number(error?.response?.status) === 409
            ? workspaceControlConflictMessage(error)
            : 'Could not update the workspace agent — try again.',
          'error',
        );
        return;
      }

      let statusRefreshed = true;
      try {
        const refreshedStatus = await refetchOrgStatus({ force: true });
        statusRefreshed = refreshedStatus != null;
      } catch {
        statusRefreshed = false;
      }
      void Promise.all([loadDecisions(), loadRoles()]);
      const affected = Math.max(0, Number(response?.data?.affected) || 0);
      const skipped = Math.max(0, Number(response?.data?.skipped) || 0);
      if (skipped > 0) {
        showToast?.(
          `${affected} role${affected === 1 ? '' : 's'} resumed; ${skipped} need${skipped === 1 ? 's' : ''} attention. Review role budgets and status, then retry.`,
          'warning',
        );
      } else if (!statusRefreshed) {
        showToast?.(
          'The workspace change was saved, but the latest status could not be refreshed yet.',
          'info',
        );
      }
    } finally {
      busyRef.current = false;
      setAction(null);
    }
  }, [loadDecisions, loadRoles, refetchOrgStatus, showToast]);

  const pause = useCallback(
    () => run('pause', async () => agentApi.pauseAll(
      await resolveWorkspaceControlVersion(workspaceControlVersion, refetchOrgStatus),
    )),
    [refetchOrgStatus, run, workspaceControlVersion],
  );
  const resume = useCallback(
    () => run('resume', async () => agentApi.resumeAll(
      await resolveWorkspaceControlVersion(workspaceControlVersion, refetchOrgStatus),
    )),
    [refetchOrgStatus, run, workspaceControlVersion],
  );

  return { action, pause, resume };
}

export default useWorkspaceAgentControl;
