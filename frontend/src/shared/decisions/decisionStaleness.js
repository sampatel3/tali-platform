export const decisionStalenessReasons = (decision) => (
  Array.isArray(decision?.staleness_reasons) ? decision.staleness_reasons : []
);

export const isEngineOnlyStale = (decision) => {
  if (!decision?.is_stale) return false;
  const reasons = decisionStalenessReasons(decision);
  return reasons.length > 0 && reasons.every((reason) => reason === 'engine_outdated');
};

// Unknown/empty stale reasons fail closed. Only the explicitly bounded
// old-engine case may approve the unchanged historical score as-is.
export const isApprovalBlockingStale = (decision) => (
  Boolean(decision?.is_stale) && !isEngineOnlyStale(decision)
);
