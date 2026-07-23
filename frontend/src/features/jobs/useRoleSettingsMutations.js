import { useCallback } from 'react';

import { getErrorMessage } from '../candidates/candidatesUiUtils';
import { roleAtsType } from './atsType';
import { versionedRolePayload } from './roleConcurrency';

const INACTIVE_JOB_STATUSES = new Set(['filled', 'filled_external', 'cancelled']);

/** Role-level settings writes sharing the job page's single route boundary. */
export const useRoleSettingsMutations = ({
  beginRoleOperation,
  captureRoleScope,
  clients,
  commitRoleScope,
  finishRoleOperation,
  handleRoleVersionConflict,
  isCurrentRoleScope,
  isRoleOperationPending,
  numericRoleId,
  role,
  rolesApi,
  setRole,
  setSuggestedThreshold,
  showToast,
}) => {
  const handleThresholdModeChange = useCallback(async (nextMode) => {
    const actionScope = captureRoleScope(numericRoleId);
    if (!actionScope || !['auto', 'manual'].includes(nextMode)) return;
    if (!beginRoleOperation(actionScope, 'threshold-mode')) return;
    setRole((current) => (current ? { ...current, auto_reject_threshold_mode: nextMode } : current));
    try {
      const response = await rolesApi.update(numericRoleId, versionedRolePayload(role, {
        auto_reject_threshold_mode: nextMode,
      }));
      if (!isCurrentRoleScope(actionScope)) return;
      if (response?.data) setRole(response.data);
      if (nextMode === 'auto') {
        try {
          const res = await rolesApi.suggestedAutoRejectThreshold(numericRoleId);
          commitRoleScope(actionScope, () => setSuggestedThreshold(res?.data || null));
        } catch { /* keep the last authoritative suggestion */ }
      }
      commitRoleScope(actionScope, () => showToast(
        nextMode === 'auto'
          ? 'Threshold mode set to auto — agent will pick the cut-off.'
          : 'Threshold mode set to manual.',
        'success',
      ));
    } catch (error) {
      commitRoleScope(actionScope, () => {
        if (!handleRoleVersionConflict(error)) {
          setRole((current) => (current ? {
            ...current,
            auto_reject_threshold_mode: nextMode === 'auto' ? 'manual' : 'auto',
          } : current));
          showToast(getErrorMessage(error, 'Failed to update threshold mode.'), 'error');
        }
      });
    } finally {
      finishRoleOperation(actionScope, 'threshold-mode');
    }
  }, [beginRoleOperation, captureRoleScope, commitRoleScope, finishRoleOperation, handleRoleVersionConflict, isCurrentRoleScope, numericRoleId, role, rolesApi, setRole, setSuggestedThreshold, showToast]);

  const handleSetJobStatus = useCallback(async (nextStatus) => {
    const actionScope = captureRoleScope(numericRoleId);
    const atsType = roleAtsType(role);
    if (!actionScope || !nextStatus || !['full_ats', 'sister'].includes(atsType)) return false;
    const previous = role?.job_status;
    if (nextStatus === previous || !beginRoleOperation(actionScope, 'job-status')) return false;
    setRole((current) => (current ? { ...current, job_status: nextStatus } : current));
    try {
      const res = await rolesApi.setJobStatus(
        numericRoleId,
        nextStatus,
        undefined,
        role?.version,
      );
      if (!isCurrentRoleScope(actionScope)) return true;
      if (res?.data) setRole(res.data);
      const successMessage = nextStatus === 'cancelled'
        ? 'Role archived.'
        : nextStatus === 'filled'
          ? 'Role marked as filled.'
          : nextStatus === 'filled_external'
            ? 'Role marked as filled externally.'
            : INACTIVE_JOB_STATUSES.has(String(previous || '').toLowerCase())
              ? 'Role reopened.'
              : 'Role opened.';
      showToast(successMessage, 'success');
      return true;
    } catch (error) {
      commitRoleScope(actionScope, () => {
        if (!handleRoleVersionConflict(error)) {
          setRole((current) => (current ? { ...current, job_status: previous } : current));
          showToast(getErrorMessage(error, 'Failed to update job status.'), 'error');
        }
      });
      return false;
    } finally {
      finishRoleOperation(actionScope, 'job-status');
    }
  }, [beginRoleOperation, captureRoleScope, commitRoleScope, finishRoleOperation, handleRoleVersionConflict, isCurrentRoleScope, numericRoleId, role, rolesApi, setRole, showToast]);

  const handleSetClient = useCallback(async (nextClientId) => {
    const actionScope = captureRoleScope(numericRoleId);
    if (!actionScope) return;
    const previousId = role?.client_id ?? null;
    const previousName = role?.client_name ?? null;
    if ((nextClientId ?? null) === previousId) return;
    const nextName = nextClientId == null
      ? null
      : (clients.find((client) => client.id === nextClientId)?.name ?? null);
    if (!beginRoleOperation(actionScope, 'client')) return;
    setRole((current) => (current ? {
      ...current,
      client_id: nextClientId ?? null,
      client_name: nextName,
    } : current));
    try {
      const res = await rolesApi.setClient(numericRoleId, nextClientId, role?.version);
      commitRoleScope(actionScope, () => {
        if (res?.data) setRole(res.data);
        showToast(nextClientId == null ? 'Hiring department cleared.' : 'Hiring department assigned.', 'success');
      });
    } catch (error) {
      commitRoleScope(actionScope, () => {
        if (!handleRoleVersionConflict(error)) {
          setRole((current) => (current ? {
            ...current,
            client_id: previousId,
            client_name: previousName,
          } : current));
          showToast(getErrorMessage(error, 'Failed to update hiring department.'), 'error');
        }
      });
    } finally {
      finishRoleOperation(actionScope, 'client');
    }
  }, [beginRoleOperation, captureRoleScope, clients, commitRoleScope, finishRoleOperation, handleRoleVersionConflict, numericRoleId, role?.client_id, role?.client_name, role?.version, rolesApi, setRole, showToast]);

  return {
    handleSetClient,
    handleSetJobStatus,
    handleThresholdModeChange,
    savingClient: isRoleOperationPending('client'),
    savingJobStatus: isRoleOperationPending('job-status'),
    savingThresholdMode: isRoleOperationPending('threshold-mode'),
  };
};

export default useRoleSettingsMutations;
