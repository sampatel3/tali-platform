import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { agent as agentApi } from '../api';

const decisionIdOf = (item) => Number(item?.decision_id ?? item?.id);
const LIVE_DECISION_REFRESH_MS = 2500;

// The role-chat timeline deliberately keeps decision rows lightweight. Before
// rendering an actionable card, hydrate those rows from the canonical decision
// endpoint so live safety fields (staleness, Workable stage, score provenance,
// and rescore state) are present. One role-scoped request hydrates every card in
// the thread; we do not fan out one request per decision.
export function useRoleDecisionDetails(roleId, timeline) {
  const decisionRows = useMemo(
    () => (timeline || []).filter((item) => item?.kind === 'decision'),
    [timeline],
  );
  const decisionIdSignature = decisionRows
    .map(decisionIdOf)
    .filter(Number.isFinite)
    .join(',');
  const decisionIds = useMemo(
    () => decisionIdSignature
      .split(',')
      .filter(Boolean)
      .map(Number),
    [decisionIdSignature],
  );
  const signature = useMemo(
    () => decisionRows
      .map((item) => [decisionIdOf(item), item.status || '', item.resolved_at || ''].join(':'))
      .join('|'),
    [decisionRows],
  );

  const [snapshot, setSnapshot] = useState({
    roleId: null,
    signature: '',
    byId: {},
    loading: false,
    error: false,
  });
  const requestRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++requestRef.current;
    if (!roleId || decisionIds.length === 0) {
      setSnapshot({ roleId: roleId ?? null, signature, byId: {}, loading: false, error: false });
      return true;
    }

    setSnapshot((previous) => ({
      roleId,
      signature,
      byId: previous.roleId === roleId ? previous.byId : {},
      loading: true,
      error: false,
    }));

    try {
      const { data } = await agentApi.listDecisions({
        role_id: roleId,
        status: 'all',
        limit: 200,
      });
      if (requestRef.current !== requestId) return false;

      const requested = new Set(decisionIds);
      const byId = {};
      for (const decision of Array.isArray(data) ? data : []) {
        const id = Number(decision?.id);
        if (Number.isFinite(id) && requested.has(id)) byId[id] = decision;
      }
      setSnapshot({ roleId, signature, byId, loading: false, error: false });
      return true;
    } catch {
      if (requestRef.current === requestId) {
        setSnapshot((previous) => ({
          roleId,
          signature,
          byId: previous.roleId === roleId ? previous.byId : {},
          loading: false,
          error: true,
        }));
      }
      return false;
    }
  }, [decisionIds, roleId, signature]);

  useEffect(() => {
    void refresh();
    return () => { requestRef.current += 1; };
  }, [refresh]);

  // Treat a role/signature mismatch as loading immediately. That closes the
  // render-before-effect gap where a newly-arrived card could otherwise expose
  // an enabled action for one frame before its live details are fetched.
  const current = snapshot.roleId === roleId && snapshot.signature === signature;
  const hasInFlightDecision = current && Object.values(snapshot.byId).some(
    (decision) => decision?.status === 'processing' || Boolean(decision?.rescore_in_flight),
  );

  // Approvals/overrides run asynchronously and re-evaluation can launch a CV
  // re-score. Keep only those volatile cards live; otherwise an action that
  // finished in the worker would remain visually frozen until navigation or a
  // coincidental chat-timeline poll. The interval stops as soon as the fresh
  // canonical row is no longer processing/re-scoring.
  useEffect(() => {
    if (!hasInFlightDecision) return undefined;
    const poll = window.setInterval(() => { void refresh(); }, LIVE_DECISION_REFRESH_MS);
    return () => window.clearInterval(poll);
  }, [hasInFlightDecision, refresh]);

  return {
    byId: current ? snapshot.byId : {},
    loading: decisionIds.length > 0 && (!current || snapshot.loading),
    error: current ? snapshot.error : false,
    refresh,
  };
}

export default useRoleDecisionDetails;
