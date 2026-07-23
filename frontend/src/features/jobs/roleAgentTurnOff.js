import { getErrorMessage } from '../candidates/candidatesUiUtils';
import { roleExpectedVersion, versionedRolePayload } from './roleConcurrency';

/**
 * Disable a role agent, then optionally discard its pending queue.
 *
 * The writes are deliberately phase-aware: once the role update succeeds the
 * agent is authoritatively off, even if the optional queue write later fails.
 */
export const runRoleAgentTurnOff = async ({
  agentApi,
  beginRoleOperation,
  captureRoleScope,
  commitRoleScope,
  fetchPendingDecisions,
  finishRoleOperation,
  handleRoleVersionConflict,
  isCurrentRoleScope,
  pendingDecisionCount,
  refetchAgentStatus,
  role,
  roleId,
  rolesApi,
  setAgentStatus,
  setRole,
  setTurnOffOpen,
  showToast,
  turnOffDiscard,
}) => {
  const actionScope = captureRoleScope(roleId);
  if (!actionScope || !beginRoleOperation(actionScope, 'agent-turn-off')) return;
  const alsoDiscard = turnOffDiscard && Number(pendingDecisionCount || 0) > 0;
  const previousRole = role;
  setTurnOffOpen(false);
  setRole((current) => (current ? { ...current, agentic_mode_enabled: false } : current));
  if (alsoDiscard && setAgentStatus) {
    setAgentStatus((current) => (current ? { ...current, pending_decisions: 0 } : current));
  }

  let disabledRoleResponse = null;
  try {
    disabledRoleResponse = await rolesApi.update(
      roleId,
      versionedRolePayload(role, { agentic_mode_enabled: false }),
    );
  } catch (error) {
    commitRoleScope(actionScope, () => {
      setRole(previousRole);
      void refetchAgentStatus?.();
      if (!handleRoleVersionConflict(error)) {
        showToast(getErrorMessage(error, 'Failed to turn off agent.'), 'error');
      }
    });
    finishRoleOperation(actionScope, 'agent-turn-off');
    return;
  }

  // Phase one is authoritative even if the optional queue discard fails.
  commitRoleScope(actionScope, () => {
    if (disabledRoleResponse?.data) setRole((current) => (current ? {
      ...current,
      ...disabledRoleResponse.data,
      stage_counts: current.stage_counts,
      pending_decisions_by_type: current.pending_decisions_by_type,
      active_candidates_count: current.active_candidates_count,
    } : disabledRoleResponse.data));
  });

  try {
    if (alsoDiscard) {
      await agentApi.discardPending(
        roleId,
        roleExpectedVersion(disabledRoleResponse?.data),
      );
    }
    commitRoleScope(actionScope, () => {
      void refetchAgentStatus?.();
      if (alsoDiscard) void fetchPendingDecisions();
    });
  } catch {
    commitRoleScope(actionScope, () => {
      void refetchAgentStatus?.();
      void fetchPendingDecisions();
      showToast(
        'Agent turned off, but pending decisions could not be discarded. They remain available for review.',
        'error',
      );
    });
  } finally {
    if (alsoDiscard && isCurrentRoleScope(actionScope)) {
      try {
        const response = await rolesApi.get(roleId);
        commitRoleScope(actionScope, () => {
          if (response?.data) setRole(response.data);
        });
      } catch {
        // The successful disable response above remains authoritative.
      }
    }
    finishRoleOperation(actionScope, 'agent-turn-off');
  }
};

export default runRoleAgentTurnOff;
