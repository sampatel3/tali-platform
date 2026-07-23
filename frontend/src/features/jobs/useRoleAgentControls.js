import { useCallback, useState } from 'react';

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
  beginRoleOperation,
  captureRoleScope,
  commitRoleScope,
  finishRoleOperation,
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
  const [controlState, setControlState] = useState({ roleId: null, action: null });
  const controlAction = controlState.roleId === roleId ? controlState.action : null;

  const pauseAgent = useCallback(async () => {
    const actionScope = captureRoleScope?.(roleId);
    if (!canControlAgent || !actionScope || !beginRoleOperation?.(actionScope, 'agent-control')) return;
    setControlState({ roleId, action: 'pause' });
    try {
      const response = await mutateAgentStatus({
        optimistic: (current) => optimisticallyPauseRoleAgent(current),
        request: () => apiClient.agent.pause(roleId, roleExpectedVersion(role)),
      });
      commitRoleScope?.(actionScope, () => {
        if (response?.data) {
          setRole((current) => (current ? { ...current, ...response.data } : response.data));
        }
      });
    } catch (error) {
      commitRoleScope?.(actionScope, () => {
        if (!handleRoleVersionConflict(error)) {
          showToast(getErrorMessage(error, 'Failed to pause agent.'), 'error');
        }
      });
    } finally {
      finishRoleOperation?.(actionScope, 'agent-control');
      setControlState((current) => (
        current.roleId === roleId && current.action === 'pause'
          ? { roleId: null, action: null }
          : current
      ));
    }
  }, [
    beginRoleOperation,
    canControlAgent,
    captureRoleScope,
    commitRoleScope,
    finishRoleOperation,
    handleRoleVersionConflict,
    mutateAgentStatus,
    role,
    roleId,
    setRole,
    showToast,
  ]);

  const resumeAgent = useCallback(async () => {
    const actionScope = captureRoleScope?.(roleId);
    if (!canControlAgent || !actionScope || !beginRoleOperation?.(actionScope, 'agent-control')) return;
    const statusBeforeResume = agentStatus;
    setControlState({ roleId, action: 'resume' });
    try {
      const response = await mutateAgentStatus({
        optimistic: (current) => optimisticallyResumeRoleAgent(current),
        request: () => apiClient.agent.resume(roleId, roleExpectedVersion(role)),
      });
      commitRoleScope?.(actionScope, () => {
        if (response?.data) {
          setRole((current) => (current ? { ...current, ...response.data } : response.data));
        }
      });
      if (response?.data?.resumed === false && response?.data?.paused === true) {
        // A runtime/budget guard can truthfully accept Resume as a no-op.
        // Restore the viewed hold even if the reconciliation read failed.
        commitRoleScope?.(actionScope, () => {
          if (statusBeforeResume && setAgentStatus) {
            setAgentStatus(() => ({
              ...statusBeforeResume,
              paused: true,
              paused_reason: response.data.reason || statusBeforeResume.paused_reason,
            }));
          }
          const pauseCopy = getAgentPauseCopy(response.data.reason);
          showToast(`${pauseCopy.label}. Resolve the hold, then try Resume again.`, 'info');
        });
      }
    } catch (error) {
      commitRoleScope?.(actionScope, () => {
        void loadRoleWorkspace();
        if (!handleRoleVersionConflict(error)) {
          showToast(getErrorMessage(error, 'Failed to resume agent.'), 'error');
        }
      });
    } finally {
      finishRoleOperation?.(actionScope, 'agent-control');
      setControlState((current) => (
        current.roleId === roleId && current.action === 'resume'
          ? { roleId: null, action: null }
          : current
      ));
    }
  }, [
    agentStatus,
    beginRoleOperation,
    canControlAgent,
    captureRoleScope,
    commitRoleScope,
    finishRoleOperation,
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
