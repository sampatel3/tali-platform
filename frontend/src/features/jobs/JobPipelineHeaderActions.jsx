import { GitFork, MessageSquare, RefreshCw } from 'lucide-react';

import { Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  isRelatedRoleScoringActive,
  relatedRoleScoringActionLabel,
} from './relatedRoleScoringUi';

const ACTIVE_PROCESS_STATUSES = new Set([
  'pending',
  'queued',
  'starting',
  'running',
  'cancelling',
]);

export function JobPipelineHeaderActions({
  canEditJobSpec,
  canMutateRole = true,
  mutationDisabledReason = null,
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
  const sisterScoringState = String(sisterScoringStatus?.status || '').toLowerCase();
  const sisterScoringActive = isRelatedRoleScoringActive(sisterScoringStatus);

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
          disabled={!canMutateRole || sisterRescoring || sisterScoringActive}
          title={!canMutateRole ? mutationDisabledReason : undefined}
        >
          {sisterRescoring || sisterScoringState === 'running' || sisterScoringState === 'retrying'
            ? <Spinner size={12} />
            : (sisterScoringState === 'waiting' ? null : <RefreshCw size={12} />)}
          {relatedRoleScoringActionLabel(sisterScoringStatus)}
        </button>
      ) : (
        <>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={onOpenProcessDialog}
            disabled={!canMutateRole || processActive}
            title={!canMutateRole
              ? mutationDisabledReason
              : 'Fetch CVs, pre-screen, score, and update semantic search in one governed run'}
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
              disabled={!canMutateRole || startingRelatedRole}
              title={!canMutateRole
                ? mutationDisabledReason
                : `Create a separate scoring role over this ${externalProviderLabel} candidate pool`}
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
