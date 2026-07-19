import { listAllDecisionPages } from '../../shared/api/agentDecisionPagination';

export const PIPELINE_DECISION_PAGE_SIZE = 500;

const snapshotTimestamp = (decision) => {
  const value = Date.parse(decision?.created_at || '');
  return Number.isFinite(value) ? value : Number.NEGATIVE_INFINITY;
};

const preferSnapshot = (current, candidate) => {
  if (!current) return candidate;
  // A provider/local write is already underway.  Even if a legacy race left a
  // second pending row for the same application, the in-flight row must win so
  // the Pipeline cannot expose another action while that write is unresolved.
  const currentProcessing = current?.status === 'processing';
  const candidateProcessing = candidate?.status === 'processing';
  if (currentProcessing !== candidateProcessing) {
    return candidateProcessing ? candidate : current;
  }
  const currentCreatedAt = snapshotTimestamp(current);
  const candidateCreatedAt = snapshotTimestamp(candidate);
  if (candidateCreatedAt !== currentCreatedAt) {
    return candidateCreatedAt > currentCreatedAt ? candidate : current;
  }
  return Number(candidate?.id || 0) > Number(current?.id || 0) ? candidate : current;
};

/** Select one safe, deterministic live control per application. */
export const indexPipelineDecisionSnapshots = (snapshots = []) => snapshots.reduce(
  (byApplication, decision) => {
    const applicationId = Number(decision?.application_id);
    if (!Number.isFinite(applicationId)) return byApplication;
    byApplication[applicationId] = preferSnapshot(
      byApplication[applicationId],
      decision,
    );
    return byApplication;
  },
  {},
);

/** Load the complete role queue through its compact execution-only projection. */
export const loadPipelineDecisionSnapshots = (agentApi, roleId) => listAllDecisionPages(
  (params) => agentApi.listDecisionExecutionSnapshots(params),
  { role_id: roleId },
  PIPELINE_DECISION_PAGE_SIZE,
);
