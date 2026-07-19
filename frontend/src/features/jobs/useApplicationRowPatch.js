import { useCallback } from 'react';

/**
 * Refresh one ordinary-role application after a candidate mutation without
 * re-downloading the whole paginated roster. Role/load generations fence late
 * responses so navigation or a newer workspace load always wins.
 */
export function useApplicationRowPatch({
  currentRoleIdRef,
  loadRoleWorkspace,
  loadSeqRef,
  numericRoleId,
  roleKind,
  rolesApi,
  setRole,
  setRoleApplications,
}) {
  return useCallback(async (applicationId) => {
    // A related-role row is a source-application projection with an alternate
    // score; only a full projected-roster refresh can preserve that contract.
    if (roleKind === 'sister') {
      await loadRoleWorkspace();
      return;
    }
    const numericId = Number(applicationId);
    if (!Number.isFinite(numericId) || !rolesApi?.getApplication) return;
    const requestRoleId = numericRoleId;
    const requestLoadSeq = loadSeqRef.current;
    try {
      const [appRes, roleRes] = await Promise.all([
        rolesApi.getApplication(numericId),
        Number.isFinite(numericRoleId) && rolesApi?.get
          ? rolesApi.get(numericRoleId).catch(() => null)
          : Promise.resolve(null),
      ]);
      if (
        currentRoleIdRef.current !== requestRoleId
        || loadSeqRef.current !== requestLoadSeq
      ) return;
      const fresh = appRes?.data;
      if (fresh?.id) {
        setRoleApplications((apps) => {
          const exists = apps.some((app) => Number(app?.id) === numericId);
          return exists
            ? apps.map((app) => (Number(app?.id) === numericId ? fresh : app))
            : [...apps, fresh];
        });
      }
      // Only merge aggregates used by the funnel/KPIs; replacing the role
      // would revert newer optimistic agent, budget, or settings changes.
      const nextRole = roleRes?.data;
      if (nextRole) {
        setRole((current) => (current ? {
          ...current,
          stage_counts: nextRole.stage_counts ?? current.stage_counts,
          active_candidates_count: nextRole.active_candidates_count
            ?? current.active_candidates_count,
          pending_decisions_by_type: nextRole.pending_decisions_by_type
            ?? current.pending_decisions_by_type,
        } : current));
      }
    } catch {
      // Keep the last-known row until the next full workspace load.
    }
  }, [
    currentRoleIdRef,
    loadRoleWorkspace,
    loadSeqRef,
    numericRoleId,
    roleKind,
    rolesApi,
    setRole,
    setRoleApplications,
  ]);
}

export default useApplicationRowPatch;
