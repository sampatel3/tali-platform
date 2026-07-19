import { useCallback } from 'react';

import { getErrorMessage } from '../candidates/candidatesUiUtils';
import { mergeRoleShell } from './roleShellMerge';

const captureTaskRequest = (request) => request.then(
  (response) => ({ response, error: null }),
  (error) => ({ response: null, error }),
);

export function useRoleTaskRefresh({
  currentRoleIdRef,
  currentRoleScopeRef,
  numericRoleId,
  scopeKey = numericRoleId,
  role,
  rolesApi,
  setAssessmentContextTasks,
  setAssessmentContextTasksFetchKnown,
  setAssessmentContextTasksLoadError,
  setRole,
  setRoleTasks,
  setRoleTasksFetchKnown,
  setRoleTasksLoadError,
  taskLoadSeqRef,
}) {
  const isCurrentRole = useCallback(() => (
    (!currentRoleIdRef || currentRoleIdRef.current === numericRoleId)
    && (!currentRoleScopeRef || currentRoleScopeRef.current === scopeKey)
  ), [currentRoleIdRef, currentRoleScopeRef, numericRoleId, scopeKey]);

  const refreshAssessmentTasks = useCallback(async (roleOverride = null) => {
    if (!Number.isFinite(numericRoleId) || !isCurrentRole()) return false;
    const targetRole = roleOverride?.id ? roleOverride : role;
    const taskSeq = (taskLoadSeqRef.current += 1);
    setRoleTasksFetchKnown(false);
    setRoleTasksLoadError('');
    setAssessmentContextTasksFetchKnown(false);
    setAssessmentContextTasksLoadError('');

    const isRelated = targetRole?.role_kind === 'sister';
    const ownerRoleId = Number(targetRole?.ats_owner_role_id);
    const hasOwnerRole = Number.isFinite(ownerRoleId) && ownerRoleId > 0;
    const missingOwnerError = {
      response: { data: { detail: 'This related role is not linked to an original role, so its assessment tasks cannot be confirmed.' } },
    };
    const [tasksResult, contextTasksResult] = await Promise.all([
      isRelated
        ? Promise.resolve({ response: { data: [] }, error: null })
        : captureTaskRequest(rolesApi.listTasks(numericRoleId)),
      isRelated
        ? (hasOwnerRole
          ? captureTaskRequest(rolesApi.listTasks(ownerRoleId))
          : Promise.resolve({ response: null, error: missingOwnerError }))
        : Promise.resolve(null),
    ]);
    if (taskSeq !== taskLoadSeqRef.current || !isCurrentRole()) return false;

    const tasksKnown = tasksResult.error == null;
    const nextTasks = tasksKnown && Array.isArray(tasksResult.response?.data)
      ? tasksResult.response.data
      : null;
    const contextKnown = contextTasksResult == null
      ? tasksKnown
      : contextTasksResult.error == null;
    const nextContextTasks = contextTasksResult == null
      ? nextTasks
      : (contextKnown && Array.isArray(contextTasksResult.response?.data)
        ? contextTasksResult.response.data
        : null);
    const contextError = contextTasksResult == null
      ? tasksResult.error
      : contextTasksResult.error;

    if (nextTasks != null) setRoleTasks(nextTasks);
    if (nextContextTasks != null) setAssessmentContextTasks(nextContextTasks);
    setRoleTasksFetchKnown(tasksKnown);
    setRoleTasksLoadError(tasksKnown
      ? ''
      : getErrorMessage(tasksResult.error, 'Assessment tasks could not be loaded.'));
    setAssessmentContextTasksFetchKnown(contextKnown);
    setAssessmentContextTasksLoadError(contextKnown
      ? ''
      : getErrorMessage(contextError, 'Assessment tasks could not be loaded.'));
    return tasksKnown && contextKnown;
  }, [isCurrentRole, numericRoleId, role, rolesApi, setAssessmentContextTasks,
    setAssessmentContextTasksFetchKnown, setAssessmentContextTasksLoadError, setRoleTasks,
    setRoleTasksFetchKnown, setRoleTasksLoadError, taskLoadSeqRef]);

  const refreshRoleAndTasks = useCallback(async () => {
    if (!isCurrentRole()) return role;
    let latestRole = role;
    try {
      const readsRoleShell = typeof rolesApi.getShell === 'function';
      const response = readsRoleShell
        ? await rolesApi.getShell(numericRoleId)
        : await rolesApi.get(numericRoleId);
      if (!isCurrentRole()) return latestRole;
      if (response?.data) {
        latestRole = readsRoleShell ? mergeRoleShell(latestRole, response.data) : response.data;
        setRole((current) => (
          readsRoleShell ? mergeRoleShell(current, response.data) : response.data
        ));
      }
    } catch {
      // The initiating mutation owns its error message. Preserve the last known
      // role while still attempting the independently useful task refresh.
    }
    if (!isCurrentRole()) return latestRole;
    await refreshAssessmentTasks(latestRole);
    return latestRole;
  }, [isCurrentRole, numericRoleId, refreshAssessmentTasks, role, rolesApi, setRole]);

  return { refreshAssessmentTasks, refreshRoleAndTasks };
}

export default useRoleTaskRefresh;
