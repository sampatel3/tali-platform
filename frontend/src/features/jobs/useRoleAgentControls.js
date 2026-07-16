import { useCallback, useRef, useState } from 'react';

import * as apiClient from '../../shared/api';
import { getAgentPauseCopy } from '../../shared/agentPauseCopy';
import { getErrorMessage } from '../candidates/candidatesUiUtils';
import {
  optimisticallyPauseRoleAgent,
  optimisticallyResumeRoleAgent,
} from './roleAgentStatusOptimism';
import { roleExpectedVersion } from './roleConcurrency';

// Owns the single-writer interaction around role Pause/Resume. Keeping the
// mutex, optimistic status and authoritative reconciliation together prevents
// a rapid opposite click or an older poll from issuing a second command with
// the same viewed role version.
export const useRoleAgentControls = ({
  roleId,
  role,
  agentStatus,
  canControlAgent,
  mutateAgentStatus,
  setAgentStatus,
  setRole,
  loadRoleWorkspace,
  handleRoleVersionConflict,
  showToast,
}) => {
  const busyRef = useRef(false);
  const [controlAction, setControlAction] = useState(null);

  const pauseAgent = useCallback(async () => {
    if (!canControlAgent || !Number.isFinite(roleId) || busyRef.current) return;
    busyRef.current = true;
    setControlAction('pause');
    try {
      const response = await mutateAgentStatus({
        optimistic: (current) => optimisticallyPauseRoleAgent(current),
        request: () => apiClient.agent.pause(roleId, roleExpectedVersion(role)),
      });
      if (response?.data) {
        setRole((current) => (current ? { ...current, ...response.data } : response.data));
      }
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to pause agent.'), 'error');
      }
    } finally {
      busyRef.current = false;
      setControlAction(null);
    }
  }, [
    canControlAgent,
    handleRoleVersionConflict,
    mutateAgentStatus,
    role,
    roleId,
    setRole,
    showToast,
  ]);

  const resumeAgent = useCallback(async () => {
    if (!canControlAgent || !Number.isFinite(roleId) || busyRef.current) return;
    const statusBeforeResume = agentStatus;
    busyRef.current = true;
    setControlAction('resume');
    try {
      const response = await mutateAgentStatus({
        optimistic: (current) => optimisticallyResumeRoleAgent(current),
        request: () => apiClient.agent.resume(roleId, roleExpectedVersion(role)),
      });
      if (response?.data) {
        setRole((current) => (current ? { ...current, ...response.data } : response.data));
      }
      if (response?.data?.resumed === false && response?.data?.paused === true) {
        // A runtime/budget guard can truthfully accept Resume as a no-op.
        // Restore the viewed hold even if the reconciliation read failed.
        if (statusBeforeResume && setAgentStatus) {
          setAgentStatus(() => ({
            ...statusBeforeResume,
            paused: true,
            paused_reason: response.data.reason || statusBeforeResume.paused_reason,
          }));
        }
        const pauseCopy = getAgentPauseCopy(response.data.reason);
        showToast(`${pauseCopy.label}. Resolve the hold, then try Resume again.`, 'info');
      }
    } catch (error) {
      void loadRoleWorkspace();
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to resume agent.'), 'error');
      }
    } finally {
      busyRef.current = false;
      setControlAction(null);
    }
  }, [
    agentStatus,
    canControlAgent,
    handleRoleVersionConflict,
    loadRoleWorkspace,
    mutateAgentStatus,
    role,
    roleId,
    setAgentStatus,
    setRole,
    showToast,
  ]);

  return { controlAction, pauseAgent, resumeAgent };
};

export default useRoleAgentControls;
