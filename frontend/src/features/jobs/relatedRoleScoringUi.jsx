import React, { useCallback, useEffect } from 'react';
import { Link2 } from 'lucide-react';

import { formatCount } from '../../shared/metrics';
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

export const useEffectiveRelatedAgentResume = ({
  agentStatus,
  onResumeRole,
  refetchAgentStatus,
  resumeWorkspace,
  reloadRole,
  setPollingVersion,
  showToast,
}) => useCallback(async () => {
  if (!agentStatus?.workspace_paused) return onResumeRole();
  try {
    const refreshed = await refetchAgentStatus?.();
    const version = Number(
      refreshed?.workspace_control_version
      ?? refreshed?.data?.workspace_control_version
      ?? agentStatus?.workspace_control_version,
    );
    if (!Number.isFinite(version)) throw new Error('Workspace control state is still loading. Try again.');
    await resumeWorkspace(version);
    showToast('Workspace Agent resumed. Related-role scoring will continue automatically.', 'success');
    await refetchAgentStatus?.();
    setPollingVersion((value) => value + 1);
    void reloadRole();
  } catch (error) {
    showToast(getErrorMessage(error, 'Only a workspace owner can resume the workspace Agent.'), 'error');
  }
}, [agentStatus, onResumeRole, refetchAgentStatus, reloadRole, resumeWorkspace, setPollingVersion, showToast]);

export const relatedRoleScoringActionLabel = (status) => {
  const progress = Number(status?.progress_percent || 0);
  switch (String(status?.status || '').toLowerCase()) {
    case 'running': return `Scoring ${progress}%`;
    case 'waiting': return `Waiting ${progress}%`;
    case 'retrying': return `Retrying ${progress}%`;
    default: return 'Re-score roster';
  }
};

const waitCopy = (reason) => {
  switch (reason) {
    case 'workspace_paused':
      return 'The workspace Agent is paused. Resume it to continue scoring automatically.';
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
  const total = Math.max(0, Number(status?.total || 0));
  const unscorable = Math.max(0, Number(counts?.unscorable || 0));
  const excluded = Math.max(0, Number(counts?.excluded || 0));
  const scoreable = Math.max(0, Number(status?.scoreable_total ?? (total - unscorable - excluded)));
  const scored = Math.max(0, Number(status?.scored ?? counts?.done ?? 0));
  const errors = Math.max(0, Number(counts?.error || 0));
  const progress = Math.max(0, Number(status?.progress_percent || 0));
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
  const total = Math.max(0, Number(status?.total ?? rosterFallback));
  const unscorable = Math.max(0, Number(counts?.unscorable || 0));
  const excluded = Math.max(0, Number(counts?.excluded || 0));
  const errors = Math.max(0, Number(counts?.error || 0));
  const scored = Math.max(0, Number(status?.scored ?? counts?.done ?? 0));
  const awaitingScore = Math.max(
    0,
    Number(counts?.pending || 0)
      + Number(counts?.running || 0)
      + Number(counts?.retry_wait || 0),
  );
  const waiting = String(status?.status || '').toLowerCase() === 'waiting';
  return [
    { key: 'shared', label: 'Shared candidates', value: formatCount(total), sub: `${formatCount(scored)} related scores complete` },
    {
      key: 'unscored',
      label: 'Awaiting score',
      value: formatCount(awaitingScore),
      sub: waiting ? 'scoring is waiting' : (awaitingScore > 0 ? 'related-role scoring queue' : 'queue clear'),
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
  onResumeWorkspace,
  onOpenOriginal,
}) => {
  const notice = scoringNotice(status);
  return (
    <div className="mx-auto mt-4 flex max-w-[1440px] flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-4 py-3 text-sm">
      <div className="flex items-start gap-2">
        <Link2 size={15} className="text-[var(--taali-purple)]" />
        <div>
          <div><strong>Related role · independent Taali pipeline</strong></div>
          <div className="mt-1 text-[var(--taali-text-muted)]">
            This role independently scores and progresses the candidates attached to{' '}
            <strong className="text-[var(--taali-text)]">{role.ats_owner_role_name || `the original ${providerLabel} role`}</strong>.
            {' '}The {providerLabel} application is shared: rejecting in any linked role rejects the candidate in the original and every related role. Advancing keeps this role&apos;s own funnel and writes through the shared application.
          </div>
          {notice ? (
            <div className="mt-1 text-[var(--taali-text-muted)]" role="status">
              <strong className="text-[var(--taali-text)]">{notice.title}.</strong>{' '}
              {notice.body}
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {agentStatus?.workspace_paused ? (
          <button type="button" className="btn btn-primary btn-sm" onClick={onResumeWorkspace}>
            Resume workspace Agent
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
  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--taali-text-muted)]">
    Related-role Taali pipeline · independent stages · shared {providerLabel} application
  </div>
);

export const RelatedRoleScoringInlineStatus = ({ status }) => {
  if (!isRelatedRoleScoringActive(status)) return null;
  const state = String(status?.status || '').toLowerCase();
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
