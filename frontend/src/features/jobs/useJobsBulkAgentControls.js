import { useCallback, useRef, useState } from 'react';

import * as apiClient from '../../shared/api';
import {
  resolveWorkspaceControlVersion,
  workspaceControlConflictMessage,
} from '../../shared/workspaceAgentControl';

// Keeps the Jobs page's owner-only bulk pause/resume transaction cohesive:
// one in-flight writer, truthful mutation errors, best-effort reconciliation,
// and partial-resume feedback. The caller still owns authorization and the
// surrounding page refresh.
export const useJobsBulkAgentControls = ({
  isShowcase,
  loadJobsHub,
  refetchAgentStatus,
  workspaceControlVersion,
  agentApi = apiClient.agent,
}) => {
  const busyRef = useRef(false);
  const [action, setAction] = useState(null);
  const [message, setMessage] = useState(null);

  const run = useCallback(async (actionName, mutation, failureMessage) => {
    if (isShowcase || busyRef.current) return;
    busyRef.current = true;
    setAction(actionName);
    setMessage(null);
    try {
      let response;
      try {
        response = await mutation();
      } catch (error) {
        try {
          await refetchAgentStatus({ force: true });
        } catch {
          // Reconciliation is best-effort; preserve the mutation error.
        }
        setMessage({
          tone: 'error',
          text: Number(error?.response?.status) === 409
            ? workspaceControlConflictMessage(error)
            : failureMessage,
        });
        return;
      }

      let statusRefreshed = true;
      try {
        statusRefreshed = await refetchAgentStatus({ force: true }) != null;
      } catch {
        statusRefreshed = false;
      }
      void loadJobsHub();
      const affected = Math.max(0, Number(response?.data?.affected) || 0);
      const skipped = Math.max(0, Number(response?.data?.skipped) || 0);
      if (skipped > 0) {
        setMessage({
          tone: 'warning',
          text: `${affected} role${affected === 1 ? '' : 's'} resumed; ${skipped} need${skipped === 1 ? 's' : ''} attention. Review role budgets and status, then retry.`,
        });
      } else if (!statusRefreshed) {
        setMessage({
          tone: 'info',
          text: 'The workspace change was saved, but the latest status could not be refreshed yet.',
        });
      }
    } finally {
      busyRef.current = false;
      setAction(null);
    }
  }, [isShowcase, loadJobsHub, refetchAgentStatus]);

  const pause = useCallback(
    () => run(
      'pause',
      async () => agentApi.pauseAll(await resolveWorkspaceControlVersion(
        workspaceControlVersion,
        refetchAgentStatus,
      )),
      'Could not pause running agents.',
    ),
    [agentApi, refetchAgentStatus, run, workspaceControlVersion],
  );
  const resume = useCallback(
    () => run(
      'resume',
      async () => agentApi.resumeAll(await resolveWorkspaceControlVersion(
        workspaceControlVersion,
        refetchAgentStatus,
      )),
      'Could not resume eligible paused agents.',
    ),
    [agentApi, refetchAgentStatus, run, workspaceControlVersion],
  );
  const dismissMessage = useCallback(() => setMessage(null), []);

  return { action, message, pause, resume, dismissMessage };
};

export default useJobsBulkAgentControls;
