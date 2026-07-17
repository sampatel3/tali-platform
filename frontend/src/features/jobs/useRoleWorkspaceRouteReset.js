import { useLayoutEffect, useMemo, useRef } from 'react';

import { EMPTY_FETCH_PROGRESS, EMPTY_PRE_SCREEN_PROGRESS } from './jobPipelineUtils';

/** Commit-safe route identity for fencing work that may outlive one render. */
export function useRoleWorkspaceRouteIdentity(roleId) {
  const roleScopeKey = useMemo(() => ({ roleId }), [roleId]);
  const currentRoleIdRef = useRef(roleId);
  const currentRoleScopeRef = useRef(roleScopeKey);
  const roleRenderGenerationRef = useRef({ roleId, generation: 0 });
  useLayoutEffect(() => {
    if (!Object.is(roleRenderGenerationRef.current.roleId, roleId)) {
      roleRenderGenerationRef.current = {
        roleId,
        generation: roleRenderGenerationRef.current.generation + 1,
      };
    }
    currentRoleIdRef.current = roleId;
    currentRoleScopeRef.current = roleScopeKey;
    return () => {
      if (Object.is(currentRoleIdRef.current, roleId)) currentRoleIdRef.current = null;
      if (currentRoleScopeRef.current === roleScopeKey) currentRoleScopeRef.current = null;
    };
  }, [roleId, roleScopeKey]);
  return { currentRoleIdRef, currentRoleScopeRef, roleRenderGenerationRef, roleScopeKey };
}

/** Clear state that must never cross a /jobs/:roleId route boundary. */
export function useRoleWorkspaceRouteReset(roleId, generationRefs, setters) {
  const generationRefsRef = useRef(generationRefs);
  const settersRef = useRef(setters);
  generationRefsRef.current = generationRefs;
  settersRef.current = setters;

  useLayoutEffect(() => {
    const { loadSeqRef, taskLoadSeqRef, loadedRoleIdRef } = generationRefsRef.current;
    const state = settersRef.current;
    loadSeqRef.current += 1;
    taskLoadSeqRef.current += 1;
    loadedRoleIdRef.current = null;
    state.setRole(null);
    state.setUsageBreakdown(null);
    state.setRoleTasks([]);
    state.setRoleTasksFetchKnown(false);
    state.setRoleTasksLoadError('');
    state.setAssessmentContextTasks([]);
    state.setAssessmentContextTasksFetchKnown(false);
    state.setAssessmentContextTasksLoadError('');
    state.setRoleApplications([]);
    state.setWorkspaceCriteria([]);
    state.setFetchCvsProgress(EMPTY_FETCH_PROGRESS);
    state.setPreScreenProgress(EMPTY_PRE_SCREEN_PROGRESS);
    state.setSisterScoringStatus(null);
    state.setSisterRescoreToConfirm(null);
    state.setSisterRescoring(false);
    state.setSuggestedThreshold(null);
    state.setThresholdDraft('');
    state.setSelectedSourcedAppIds(new Set());
    state.setReachOutOpen(false);
    state.setFocusCampaignId(null);
    state.setProcessDialogOpen(false);
    state.setRelatedSpecChangeToConfirm(null);
    state.setPendingRoleView(null);
    state.setEditingSpec(false);
    state.setSpecEditorDirty(false);
    state.setJobSpecError('');
    state.setJobSpecConflict(null);
    state.setTurnOffOpen(false);
    state.setTurnOffDiscard(false);
    state.setLoadError('');
    state.setApplicationsLoadError('');
    state.setRoleDetailLoadError('');
    state.setLoading(true);
    state.setRoleDetailLoading(true);
    state.setApplicationsLoading(true);
  }, [roleId]);
}

export default useRoleWorkspaceRouteReset;
