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
      await mutation();
      await refetchOrgStatus({ force: true });
      void Promise.all([loadDecisions(), loadRoles()]);
    } catch (error) {
      await refetchOrgStatus({ force: true });
      showToast?.(
        Number(error?.response?.status) === 409
          ? workspaceControlConflictMessage(error)
          : 'Could not update the workspace agent — try again.',
        'error',
      );
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
