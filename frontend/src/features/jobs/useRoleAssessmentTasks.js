import { useCallback, useEffect, useRef, useState } from 'react';

import { getErrorMessage } from '../candidates/candidatesUiUtils';
import { roleExpectedVersion } from './roleConcurrency';

const taskIds = (tasks) => (
  [...new Set((tasks || []).map((task) => Number(task?.id ?? task)).filter(Number.isFinite))]
);

const assignmentToast = (desired, agentEnabled) => {
  if (desired.length === 0) {
    return agentEnabled
      ? 'Assessment tasks cleared — this role will now skip the assessment stage.'
      : 'Assessment tasks cleared.';
  }
  if (desired.length === 1) return 'Assessment task assigned.';
  return `${desired.length}-task A/B set saved.`;
};

/** Owns the role's assessment membership mutation and its lazy task catalogue. */
export const useRoleAssessmentTasks = ({
  activeView,
  currentRoleIdRef,
  handleRoleVersionConflict,
  loadRoleWorkspace,
  numericRoleId,
  role,
  roleTasks,
  rolesApi,
  setRefreshTick,
  showToast,
  tasksApi,
}) => {
  const [allTasks, setAllTasks] = useState([]);
  const [savingRoleIds, setSavingRoleIds] = useState(() => new Set());
  const loadedAllTasksRef = useRef(false);
  const savingAssessmentTask = savingRoleIds.has(numericRoleId);

  const markRoleSaving = useCallback((roleId, saving) => {
    setSavingRoleIds((current) => {
      const next = new Set(current);
      if (saving) next.add(roleId);
      else next.delete(roleId);
      return next;
    });
  }, []);

  useEffect(() => {
    if (activeView !== 'role-fit' || loadedAllTasksRef.current || !tasksApi?.list) return undefined;
    let cancelled = false;
    const loadAllTasks = async () => {
      try {
        const response = await tasksApi.list();
        if (!cancelled) {
          setAllTasks(Array.isArray(response?.data) ? response.data : []);
          loadedAllTasksRef.current = true;
        }
      } catch {
        if (!cancelled) setAllTasks([]);
      }
    };
    void loadAllTasks();
    return () => { cancelled = true; };
  }, [activeView, tasksApi]);

  const assignAssessmentTasks = useCallback(async (nextTaskIds) => {
    if (!Number.isFinite(numericRoleId)) return false;
    const actionRoleId = numericRoleId;
    const isCurrentRole = () => currentRoleIdRef?.current === actionRoleId;
    markRoleSaving(actionRoleId, true);
    const desired = taskIds(nextTaskIds);
    const current = (roleTasks || []).map((task) => Number(task.id));
    try {
      let expectedVersion = roleExpectedVersion(role);
      if (rolesApi.addTask) {
        for (const id of desired) {
          if (!current.includes(id)) {
            const response = await rolesApi.addTask(numericRoleId, id, expectedVersion);
            expectedVersion = response?.data?.version ?? expectedVersion + 1;
          }
        }
      }
      if (rolesApi.removeTask) {
        for (const id of current) {
          if (!desired.includes(id)) {
            await rolesApi.removeTask(numericRoleId, id, expectedVersion);
            expectedVersion += 1;
          }
        }
      }
      if (!isCurrentRole()) return true;
      await loadRoleWorkspace();
      if (!isCurrentRole()) return true;
      setRefreshTick((value) => value + 1);
      showToast(assignmentToast(desired, role?.agentic_mode_enabled), 'success');
      return true;
    } catch (error) {
      if (isCurrentRole()) {
        if (!handleRoleVersionConflict(error)) {
          showToast(getErrorMessage(error, 'Failed to update assessment tasks.'), 'error');
        }
        await loadRoleWorkspace();
      }
      throw error;
    } finally {
      markRoleSaving(actionRoleId, false);
    }
  }, [currentRoleIdRef, handleRoleVersionConflict, loadRoleWorkspace, markRoleSaving, numericRoleId, role, roleTasks, rolesApi, setRefreshTick, showToast]);

  return { allTasks, assignAssessmentTasks, savingAssessmentTask };
};
