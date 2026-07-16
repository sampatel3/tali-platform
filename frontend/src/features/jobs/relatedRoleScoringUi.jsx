import { useEffect } from 'react';

const ACTIVE_SCORING_STATES = new Set(['running', 'waiting', 'retrying']);

const isRelatedRoleScoringActive = (status) => (
  ACTIVE_SCORING_STATES.has(String(status?.status || '').toLowerCase())
);

// Related-role scoring remains an operational background process. The job
// page consumes its completion transition to refresh normal role data, without
// introducing a second related-only page layout or vocabulary.
export const useRelatedRoleScoringPolling = (
  enabled,
  roleId,
  rolesApi,
  refreshKey,
  onStatus,
) => {
  useEffect(() => {
    if (!enabled || !rolesApi?.sisterScoringStatus) {
      onStatus(null);
      return undefined;
    }
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      try {
        const res = await rolesApi.sisterScoringStatus(roleId);
        if (cancelled) return;
        const next = res?.data || null;
        onStatus(next);
        if (isRelatedRoleScoringActive(next)) {
          timer = window.setTimeout(poll, next?.status === 'running' ? 3000 : 15_000);
        }
      } catch {
        if (!cancelled) onStatus(null);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [enabled, onStatus, refreshKey, roleId, rolesApi]);
};
