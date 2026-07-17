import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link2 } from 'lucide-react';

import { formatCount } from '../../shared/metrics';
import {
  isRelatedRolePaidAuthorizationError,
  relatedRoleRecoveryAuthorization,
  relatedRoleRescoreAuthorization,
} from '../../shared/relatedRoles/paidWorkAuthorization';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import { getErrorMessage } from '../candidates/candidatesUiUtils';

const ACTIVE_SCORING_STATES = new Set(['running', 'waiting', 'retrying']);

export const isRelatedRoleScoringActive = (status) => (
  ACTIVE_SCORING_STATES.has(String(status?.status || '').toLowerCase())
);

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
        // Keep the last authoritative snapshot visible and keep trying. A
        // transient network/API failure must not erase progress or silently
        // disable polling for the rest of the page lifetime.
        if (!cancelled) timer = window.setTimeout(poll, 15_000);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [enabled, onStatus, refreshKey, roleId, rolesApi]);
};

export const useRelatedRoleRecoveryScope = (
  enabled,
  roleId,
  agentApi,
  refreshKey,
) => {
  const [state, setState] = useState({ scope: null, loading: false, error: false });
  useEffect(() => {
    if (!enabled || !agentApi?.relatedRoleRecoveryScope) {
      setState({ scope: null, loading: false, error: false });
      return undefined;
    }
    let cancelled = false;
    let retryTimer = null;
    const load = async () => {
      // A changed role/workspace version invalidates the prior proof
      // immediately. Never leave a stale-but-enabled recovery button visible
      // while its replacement snapshot is loading.
      setState({ scope: null, loading: true, error: false });
      try {
        const response = await agentApi.relatedRoleRecoveryScope(roleId);
        if (cancelled) return;
        setState({ scope: response?.data || null, loading: false, error: false });
      } catch {
        if (cancelled) return;
        setState({ scope: null, loading: false, error: true });
        // Recovery remains unavailable without an exact proof, but a transient
        // request failure must not strand the owner until a full page reload.
        retryTimer = window.setTimeout(load, 15_000);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, [agentApi, enabled, refreshKey, roleId]);
  return state;
};

export { relatedRoleRecoveryAuthorization, relatedRoleRescoreAuthorization };

export const useRelatedRoleRescoreApproval = ({
  approval,
  canControlRoleAgent,
  loadRoleWorkspace,
  numericRoleId,
  rolesApi,
  setApproval,
  setPollingVersion,
  setRescoring,
  setStatus,
  showToast,
}) => useCallback(async () => {
  if (!Number.isFinite(numericRoleId) || !approval || !canControlRoleAgent) return;
  setApproval(null);
  setRescoring(true);
  try {
    const res = await rolesApi.rescoreSister(numericRoleId, approval.request);
    setStatus(res?.data || null);
    setPollingVersion((value) => value + 1);
    showToast('Re-scoring queued for the coupled candidate roster.', 'success');
    window.setTimeout(() => { void loadRoleWorkspace(); }, 1000);
  } catch (error) {
    if (isRelatedRolePaidAuthorizationError(error)) {
      const [, statusResult] = await Promise.all([
        loadRoleWorkspace(),
        rolesApi.sisterScoringStatus(numericRoleId).catch(() => null),
      ]);
      if (statusResult?.data) setStatus(statusResult.data);
      setPollingVersion((value) => value + 1);
      showToast(
        'The related role or scoreable roster changed. Preview refreshed — review the current count and cost before confirming again.',
        'warning',
      );
    } else {
      showToast(getErrorMessage(error, 'Failed to queue the related-role re-score.'), 'error');
    }
  } finally {
    setRescoring(false);
  }
}, [approval, canControlRoleAgent, loadRoleWorkspace, numericRoleId, rolesApi,
  setApproval, setPollingVersion, setRescoring, setStatus, showToast]);

export const useEffectiveRelatedAgentResume = ({
  agentStatus,
  canResumeWorkspace = false,
  onResumeRole,
  recoverRelatedRole,
  recoveryScope,
  refetchAgentStatus,
  reloadRole,
  role,
  setPollingVersion,
  showToast,
}) => {
  const busyRef = useRef(false);
  return useCallback(async () => {
    // Ordinary role pauses remain role-scoped. Legacy overlay recovery has a
    // separate endpoint that preserves every unrelated role's effective hold.
    if (!agentStatus?.workspace_paused) return onResumeRole?.();
    if (!canResumeWorkspace) {
      showToast?.('Only a workspace owner can recover this related role.', 'error');
      return false;
    }
    if (busyRef.current) return false;
    busyRef.current = true;
    try {
      const authority = relatedRoleRecoveryAuthorization(
        role,
        recoveryScope,
      );
      if (!authority || !Number.isFinite(Number(role?.id))) {
        showToast?.('Related-role recovery scope is still loading. Try again.', 'error');
        return false;
      }

      let response;
      try {
        response = await recoverRelatedRole(Number(role.id), authority);
      } catch (error) {
        if (isRelatedRolePaidAuthorizationError(error)) {
          await Promise.allSettled([refetchAgentStatus?.(), reloadRole?.()]);
          setPollingVersion?.((value) => value + 1);
          showToast?.(
            'The related role, family, cohort, or legacy hold changed. Review the refreshed scope and recover again.',
            'warning',
          );
          return false;
        }
        showToast?.(
          getErrorMessage(
            error,
            'Could not recover this related role from the legacy workspace hold.',
          ),
          'error',
        );
        return false;
      }

      try {
        await refetchAgentStatus?.();
      } catch {
        // The targeted mutation succeeded; the next poll remains authoritative.
      }
      setPollingVersion?.((value) => value + 1);
      try {
        const reload = reloadRole?.();
        if (reload?.catch) void reload.catch(() => {});
      } catch {
        // The mutation succeeded; a presentation refresh must not report it as
        // a failed resume or suppress the next scoring poll.
      }

      const result = response?.data || response;
      showToast?.(
        result?.resumed === false
          ? 'The legacy workspace hold was cleared. This related role keeps its existing pause, and every unrelated role remains paused.'
          : 'This related role was recovered. Every unrelated role remains paused.',
        'success',
      );
      return response;
    } finally {
      busyRef.current = false;
    }
  }, [
    agentStatus,
    canResumeWorkspace,
    onResumeRole,
    recoverRelatedRole,
    recoveryScope,
    refetchAgentStatus,
    reloadRole,
    role,
    setPollingVersion,
    showToast,
  ]);
};

export const relatedRoleScoringActionLabel = (status) => {
  const progress = Number(status?.progress_percent || 0);
  switch (String(status?.status || '').toLowerCase()) {
    case 'running': return `Scoring ${progress}%`;
    case 'waiting': return `Waiting ${progress}%`;
    case 'retrying': return `Retrying ${progress}%`;
    case 'stale': return 'Re-score roster';
    default: return 'Re-score roster';
  }
};

export const shouldRefreshRelatedRoleWorkspace = (previousStatus, currentStatus) => {
  const previous = String(previousStatus || '').toLowerCase();
  const current = String(currentStatus || '').toLowerCase();
  if (!current) return false;
  const previousActive = ACTIVE_SCORING_STATES.has(previous);
  const currentActive = ACTIVE_SCORING_STATES.has(current);
  return (previousActive && !currentActive)
    || (previous === 'running' && current !== 'running');
};

const waitCopy = (reason) => {
  switch (reason) {
    case 'workspace_paused':
      return 'A legacy workspace-wide agent hold is blocking scoring. A workspace owner can recover only this related role while every unrelated role stays paused.';
    case 'agent_off':
      return 'This related role’s Agent is off. Turn it on to continue scoring automatically.';
    case 'agent_paused':
      return 'This related role’s Agent is paused. Resume it to continue scoring automatically.';
    case 'job_not_open':
      return 'The original role is not open. Reopen it before related-role scoring can continue.';
    case 'ats_job_not_live':
      return 'The original ATS job is no longer live. Related-role scoring is held until it is reopened.';
    case 'temporary_retry':
      return 'Scoring is waiting to retry after a temporary service issue.';
    default:
      return 'Scoring is waiting for this related role’s Agent to allow model-backed work.';
  }
};

const scoringNotice = (status) => {
  if (!status) return null;
  const counts = status?.counts || {};
  const total = Math.max(0, Number(status?.cohort_total ?? status?.total ?? 0));
  const unscorable = Math.max(0, Number(status?.cohort_unscorable ?? counts?.unscorable ?? 0));
  const excluded = Math.max(0, Number(status?.cohort_excluded ?? counts?.excluded ?? 0));
  const scoreable = Math.max(0, Number(status?.cohort_scoreable
    ?? status?.scoreable_total ?? (total - unscorable - excluded)));
  const scored = Math.max(0, Number(status?.scored ?? counts?.done ?? 0));
  const staleScored = Math.max(0, Number(status?.stale_scored ?? 0));
  const errors = Math.max(0, Number(counts?.error || 0));
  const progress = Math.max(0, Number(status?.progress_percent || 0));
  const estimatedCost = Math.max(0, Number(status?.estimated_rescore_cost_usd || 0));
  const scoreSummary = `${formatCount(scored)} of ${formatCount(scoreable)} scoreable candidates have a related-role score`;
  const unavailableSummary = unscorable > 0
    ? ` ${formatCount(unscorable)} ${unscorable === 1 ? 'candidate has' : 'candidates have'} no usable CV text.`
    : '';
  const excludedSummary = excluded > 0
    ? ` ${formatCount(excluded)} ${excluded === 1 ? 'candidate is' : 'candidates are'} already closed or disqualified in the shared ATS application.`
    : '';
  switch (String(status?.status || '').toLowerCase()) {
    case 'running':
      return { title: `Related-role scoring in progress · ${progress}%`, body: `${scoreSummary}.${unavailableSummary}${excludedSummary}` };
    case 'waiting':
    case 'retrying':
      return {
        title: `Related-role scoring is waiting · ${progress}%`,
        body: `${waitCopy(status?.waiting_reason)} ${scoreSummary}.${unavailableSummary}${excludedSummary}`,
      };
    case 'error':
      return {
        title: 'Related-role scoring needs attention',
        body: `${formatCount(errors)} ${errors === 1 ? 'candidate could' : 'candidates could'} not be scored. ${scoreSummary}.${unavailableSummary}${excludedSummary}`,
      };
    case 'stale':
      return {
        title: 'Related-role scores need re-score approval',
        body: `${scoreSummary}. ${formatCount(staleScored)} previous ${staleScored === 1 ? 'score remains' : 'scores remain'} visible, but ${staleScored === 1 ? 'it is' : 'they are'} stale because the job specification changed.${estimatedCost > 0 ? ` Estimated model cost: $${estimatedCost.toFixed(2)}.` : ''} No model spend starts until Re-score roster is explicitly approved.${unavailableSummary}${excludedSummary}`,
      };
    case 'completed':
      return { title: 'Related-role scoring complete', body: `${scoreSummary}.${unavailableSummary}${excludedSummary}` };
    default:
      return null;
  }
};

export const buildRelatedRolePipelineStats = ({
  status,
  rosterFallback,
  belowThresholdCount,
  thresholdValue,
  budget,
  monthlyBudgetCents,
}) => {
  const counts = status?.counts || {};
  const total = Math.max(0, Number(status?.cohort_total ?? status?.total ?? rosterFallback));
  const unscorable = Math.max(0, Number(status?.cohort_unscorable ?? counts?.unscorable ?? 0));
  const excluded = Math.max(0, Number(status?.cohort_excluded ?? counts?.excluded ?? 0));
  const errors = Math.max(0, Number(counts?.error || 0));
  const scored = Math.max(0, Number(status?.scored ?? counts?.done ?? 0));
  const staleScored = Math.max(0, Number(status?.stale_scored ?? 0));
  const queuedOrStale = Math.max(
    0,
    Number(counts?.pending || 0)
      + Number(counts?.running || 0)
      + Number(counts?.retry_wait || 0)
      + Number(counts?.stale || 0),
  );
  const scoreable = Math.max(0, Number(status?.cohort_scoreable
    ?? status?.scoreable_total ?? (total - unscorable - excluded)));
  // A newly synced source application can be in the live cohort briefly
  // before its SisterRoleEvaluation row is materialized. Do not hide that
  // candidate from the Awaiting score tile during the gap.
  const awaitingScore = Math.max(queuedOrStale, scoreable - scored - errors);
  const waiting = String(status?.status || '').toLowerCase() === 'waiting';
  const stale = String(status?.status || '').toLowerCase() === 'stale';
  return [
    {
      key: 'shared',
      label: 'Shared candidates',
      value: formatCount(total),
      sub: staleScored > 0
        ? `${formatCount(scored)} current related scores · ${formatCount(staleScored)} stale snapshots visible`
        : `${formatCount(scored)} related scores complete`,
    },
    {
      key: 'unscored',
      label: 'Awaiting score',
      value: formatCount(awaitingScore),
      sub: stale
        ? 're-score approval required'
        : waiting ? 'scoring is waiting' : (awaitingScore > 0 ? 'related-role scoring queue' : 'queue clear'),
    },
    {
      key: 'below-threshold',
      label: 'Below threshold',
      value: formatCount(belowThresholdCount),
      sub: thresholdValue != null ? `flagged at < ${thresholdValue}` : 'set a reject threshold',
    },
    {
      key: 'not-scored',
      label: 'Cannot score',
      value: formatCount(unscorable + errors + excluded),
      sub: [
        unscorable > 0 ? `${formatCount(unscorable)} without CV text` : null,
        errors > 0 ? `${formatCount(errors)} errors` : null,
        excluded > 0 ? `${formatCount(excluded)} ATS-closed` : null,
      ].filter(Boolean).join(' · ') || 'none',
    },
    {
      key: 'spend',
      label: 'Role budget · MTD',
      value: budget.value,
      unit: monthlyBudgetCents > 0 ? budget.unit : null,
      bar: monthlyBudgetCents > 0 ? budget : null,
      sub: budget.sub,
    },
  ];
};

export const RelatedRoleContextBanner = ({
  role,
  providerLabel,
  status,
  agentStatus,
  canResumeWorkspace = false,
  recoveryScopeError = false,
  recoveryScopeLoading = false,
  recoveryScopeReady = false,
  recoveryScope = null,
  onResumeWorkspace,
  onOpenOriginal,
}) => {
  const notice = scoringNotice(recoveryScope ? {
    ...status,
    cohort_total: recoveryScope.cohort_total,
    cohort_scoreable: recoveryScope.cohort_scoreable,
    cohort_unscorable: recoveryScope.cohort_unscorable,
    cohort_excluded: recoveryScope.cohort_excluded,
  } : status);
  return (
    <div className="mx-auto mt-4 flex max-w-[1440px] flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-4 py-3 text-sm">
      <div className="flex items-start gap-2">
        <Link2 size={15} className="text-[var(--taali-purple)]" />
        <div>
          <div><strong>Related role · independent Taali pipeline</strong></div>
          <div className="mt-1 text-[var(--taali-muted)]">
            This role independently scores and progresses the candidates attached to{' '}
            <strong className="text-[var(--taali-text)]">{role.ats_owner_role_name || `the original ${providerLabel} role`}</strong>.
            {' '}The {providerLabel} application is shared: rejecting in any linked role rejects the candidate in the original and every related role. Advancing keeps this role&apos;s own funnel and writes through the shared application.
          </div>
          {notice ? (
            <div className="mt-1 text-[var(--taali-muted)]" role="status">
              <strong className="text-[var(--taali-text)]">{notice.title}.</strong>{' '}
              {notice.body}
            </div>
          ) : null}
          {agentStatus?.workspace_paused && recoveryScopeReady ? (
            <div className="mt-1 text-[var(--taali-muted)]" role="status">
              Exact recovery scope checked: {formatCount(recoveryScope?.cohort_scoreable)} scoreable of{' '}
              {formatCount(recoveryScope?.cohort_total)} shared candidates. Taali re-checks this scope before resuming.
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {agentStatus?.workspace_paused ? (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={onResumeWorkspace}
            disabled={!canResumeWorkspace || !recoveryScopeReady}
            aria-busy={canResumeWorkspace && recoveryScopeLoading ? 'true' : undefined}
            title={!canResumeWorkspace
              ? 'Only workspace owners can recover this related role.'
              : recoveryScopeLoading
                ? 'Checking the exact role family and candidate cohort before recovery.'
                : recoveryScopeError
                  ? 'The exact recovery scope could not be loaded. Taali will retry automatically.'
                  : !recoveryScopeReady
                    ? 'Waiting for the exact recovery scope.'
                    : undefined}
          >
            Recover this related role
          </button>
        ) : null}
        <button type="button" className="btn btn-outline btn-sm" onClick={onOpenOriginal}>
          Open original role
        </button>
      </div>
    </div>
  );
};

export const RelatedRolePipelineLabel = ({ providerLabel }) => (
  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--taali-muted)]">
    Related-role Taali pipeline · independent stages · shared {providerLabel} application
  </div>
);

export const RelatedRoleScoringInlineStatus = ({ status }) => {
  const state = String(status?.status || '').toLowerCase();
  if (state === 'stale') {
    return (
      <span className="inline-flex items-center gap-2 text-sm text-[var(--taali-muted)]" role="status">
        Related-role scores are stale · re-score approval required
      </span>
    );
  }
  if (!isRelatedRoleScoringActive(status)) return null;
  const progress = Number(status?.progress_percent || 0);
  return (
    <span className="inline-flex items-center gap-2 text-sm text-[var(--taali-muted)]">
      {state === 'running' || state === 'retrying' ? <Spinner size={12} /> : null}
      {state === 'waiting'
        ? `Related-role scoring waiting at ${progress}%`
        : `Related-role scoring ${progress}%`}
    </span>
  );
};
