import { GitFork, MessageSquare, RefreshCw } from 'lucide-react';

import { Spinner } from '../../shared/ui/TaaliPrimitives';

const ACTIVE_PROCESS_STATUSES = new Set([
  'pending',
  'queued',
  'starting',
  'running',
  'cancelling',
]);

export function JobPipelineHeaderActions({
  canEditJobSpec,
  externalProvider,
  externalProviderLabel,
  navigate,
  onEditJobSpec,
  onOpenProcessDialog,
  onRescoreSister,
  onStartRelatedRole,
  processStatus,
  role,
  roleAgent,
  rolePendingReviewTitle,
  sisterRescoring,
  sisterScoringStatus,
  startingRelatedRole,
}) {
  const isSister = role?.role_kind === 'sister';
  const processActive = ACTIVE_PROCESS_STATUSES.has(String(processStatus || '').toLowerCase());

  return (
    <>
      {(roleAgent?.pending || 0) > 0 ? (
        <button
          type="button"
          className="btn btn-outline btn-sm"
          title={rolePendingReviewTitle}
          aria-label={`${rolePendingReviewTitle}. Open the Home review queue.`}
          onClick={() => {
            const params = new URLSearchParams({
              role: String(role?.id || ''),
              status: 'pending',
            });
            navigate(`/home?${params.toString()}`);
          }}
        >
          Review {roleAgent.pending} {roleAgent.pending === 1 ? 'item' : 'items'} →
        </button>
      ) : null}
      {isSister ? (
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={onRescoreSister}
          disabled={sisterRescoring || sisterScoringStatus?.status === 'running'}
        >
          {sisterRescoring || sisterScoringStatus?.status === 'running'
            ? <Spinner size={12} />
            : <RefreshCw size={12} />}
          {sisterScoringStatus?.status === 'running'
            ? `Scoring ${sisterScoringStatus.progress_percent || 0}%`
            : 'Re-score roster'}
        </button>
      ) : (
        <>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={onOpenProcessDialog}
            disabled={processActive}
            title="Fetch CVs, pre-screen, score, and update semantic search in one governed run"
          >
            <RefreshCw size={12} />
            {processActive ? 'Processing…' : 'Process candidates'}
          </button>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => navigate(`/chat/agents/${role.id}`)}
            title="Open this job's agent chat"
          >
            <MessageSquare size={12} />
            Ask agent
          </button>
          {externalProvider ? (
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={onStartRelatedRole}
              disabled={startingRelatedRole}
              title={`Create a separate scoring role over this ${externalProviderLabel} candidate pool`}
            >
              {startingRelatedRole ? <Spinner size={12} /> : <GitFork size={12} />}
              {startingRelatedRole ? 'Opening draft…' : 'Create related role'}
            </button>
          ) : null}
        </>
      )}
      {canEditJobSpec ? (
        <button type="button" className="btn btn-outline btn-sm" onClick={onEditJobSpec}>
          Edit job spec
        </button>
      ) : null}
    </>
  );
}

export default JobPipelineHeaderActions;
