import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  buildRelatedRolePipelineStats,
  RelatedRoleContextBanner,
  RelatedRoleScoringInlineStatus,
  relatedRoleScoringActionLabel,
  shouldRefreshRelatedRoleWorkspace,
  useEffectiveRelatedAgentResume,
  useRelatedRoleRecoveryScope,
  useRelatedRoleScoringPolling,
} from './relatedRoleScoringUi';

afterEach(() => {
  vi.useRealTimers();
});

const makeResumeProps = (overrides = {}) => ({
  agentStatus: {
    workspace_paused: true,
    workspace_control_version: 7,
  },
  canResumeWorkspace: true,
  onResumeRole: vi.fn(),
  role: {
    id: 47,
    version: 7,
    role_family: {
      owner: { id: 31, name: 'Original engineering role' },
      related: [{ id: 47, name: 'Security view' }],
    },
  },
  recoveryScope: {
    role_id: 47,
    role_version: 7,
    workspace_paused: true,
    workspace_control_version: 7,
    role_family: {
      owner: { id: 31, name: 'Original engineering role' },
      related: [{ id: 47, name: 'Security view' }],
    },
    cohort_total: 9,
    cohort_scoreable: 7,
    cohort_unscorable: 2,
    cohort_excluded: 0,
    cohort_fingerprint: 'a'.repeat(64),
  },
  refetchAgentStatus: vi.fn().mockResolvedValue({
    workspace_paused: true,
    workspace_control_version: 7,
  }),
  recoverRelatedRole: vi.fn().mockResolvedValue({
    data: { role_id: 47, resumed: true, preserved_paused_count: 2 },
  }),
  reloadRole: vi.fn().mockResolvedValue(undefined),
  setPollingVersion: vi.fn(),
  showToast: vi.fn(),
  ...overrides,
});

const setupResume = (overrides) => {
  const props = makeResumeProps(overrides);
  const hook = renderHook(() => useEffectiveRelatedAgentResume(props));
  return { ...hook, props };
};

describe('related-role scoring workspace refresh', () => {
  it('refreshes after scored work pauses or any active state becomes terminal', () => {
    expect(shouldRefreshRelatedRoleWorkspace('running', 'waiting')).toBe(true);
    expect(shouldRefreshRelatedRoleWorkspace('running', 'retrying')).toBe(true);
    expect(shouldRefreshRelatedRoleWorkspace('waiting', 'completed')).toBe(true);
    expect(shouldRefreshRelatedRoleWorkspace('retrying', 'error')).toBe(true);
    expect(shouldRefreshRelatedRoleWorkspace('waiting', 'running')).toBe(false);
    expect(shouldRefreshRelatedRoleWorkspace('retrying', 'running')).toBe(false);
    expect(shouldRefreshRelatedRoleWorkspace(null, 'completed')).toBe(false);
    expect(shouldRefreshRelatedRoleWorkspace('waiting', null)).toBe(false);
  });

  it('keeps the last status and retries after a transient polling failure', async () => {
    vi.useFakeTimers();
    const running = { status: 'running', progress_percent: 25 };
    const completed = { status: 'completed', progress_percent: 100 };
    const rolesApi = {
      sisterScoringStatus: vi.fn()
        .mockResolvedValueOnce({ data: running })
        .mockRejectedValueOnce(new Error('temporary network failure'))
        .mockResolvedValueOnce({ data: completed }),
    };
    const onStatus = vi.fn();
    const { unmount } = renderHook(() => useRelatedRoleScoringPolling(
      true,
      47,
      rolesApi,
      0,
      onStatus,
    ));

    await act(async () => { await Promise.resolve(); });
    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenLastCalledWith(running);

    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(rolesApi.sisterScoringStatus).toHaveBeenCalledTimes(2);
    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus).not.toHaveBeenCalledWith(null);

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(rolesApi.sisterScoringStatus).toHaveBeenCalledTimes(3);
    expect(onStatus).toHaveBeenLastCalledWith(completed);
    unmount();
  });

  it('keeps exact CV-scope work out of normal progress polling', async () => {
    const rolesApi = {
      sisterScoringStatus: vi.fn().mockResolvedValue({
        data: { status: 'completed', progress_percent: 100 },
      }),
    };
    const agentApi = { relatedRoleRecoveryScope: vi.fn() };
    const onStatus = vi.fn();

    renderHook(() => useRelatedRoleScoringPolling(true, 47, rolesApi, 0, onStatus));
    await act(async () => { await Promise.resolve(); });

    expect(rolesApi.sisterScoringStatus).toHaveBeenCalledOnce();
    expect(agentApi.relatedRoleRecoveryScope).not.toHaveBeenCalled();
  });

  it('loads the exact recovery scope once only when the owner recovery control renders', async () => {
    const scope = makeResumeProps().recoveryScope;
    const agentApi = {
      relatedRoleRecoveryScope: vi.fn().mockResolvedValue({ data: scope }),
    };
    const { result, rerender } = renderHook(
      ({ enabled, refreshKey }) => useRelatedRoleRecoveryScope(
        enabled,
        47,
        agentApi,
        refreshKey,
      ),
      { initialProps: { enabled: false, refreshKey: '7:7' } },
    );

    expect(agentApi.relatedRoleRecoveryScope).not.toHaveBeenCalled();
    rerender({ enabled: true, refreshKey: '7:7' });
    await act(async () => { await Promise.resolve(); });

    expect(agentApi.relatedRoleRecoveryScope).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({ scope, loading: false, error: false });
    rerender({ enabled: true, refreshKey: '7:7' });
    await act(async () => { await Promise.resolve(); });
    expect(agentApi.relatedRoleRecoveryScope).toHaveBeenCalledTimes(1);
  });

  it('surfaces stale scores as an explicit, paid re-score approval state', () => {
    const status = {
      status: 'stale',
      total: 10,
      scoreable_total: 8,
      scored: 0,
      stale_scored: 8,
      visible_scored: 8,
      estimated_rescore_cost_usd: 0.66,
      counts: { done: 0, stale: 8, unscorable: 2 },
    };
    render(
      <>
        <RelatedRoleContextBanner
          role={{ ats_owner_role_name: 'Original engineering role' }}
          providerLabel="Workable"
          status={status}
          agentStatus={{ workspace_paused: false }}
          onResumeWorkspace={vi.fn()}
          onOpenOriginal={vi.fn()}
        />
        <RelatedRoleScoringInlineStatus status={status} />
      </>,
    );

    expect(screen.getByText(/Related-role scores need re-score approval/i)).toBeInTheDocument();
    expect(screen.getByText(/Estimated model cost: \$0\.66/i)).toBeInTheDocument();
    expect(screen.getByText(/0 of 8 scoreable candidates have a related-role score/i))
      .toBeInTheDocument();
    expect(screen.getByText(/8 previous scores remain visible, but they are stale/i))
      .toBeInTheDocument();
    expect(screen.getByText(/No model spend starts until Re-score roster is explicitly approved/i))
      .toBeInTheDocument();
    expect(screen.getByText(/scores are stale · re-score approval required/i)).toBeInTheDocument();
    expect(relatedRoleScoringActionLabel(status)).toBe('Re-score roster');

    const awaiting = buildRelatedRolePipelineStats({
      status,
      rosterFallback: 10,
      belowThresholdCount: 1,
      thresholdValue: 60,
      budget: { value: '$0', sub: 'of $50 cap' },
      monthlyBudgetCents: 5000,
    }).find((tile) => tile.key === 'unscored');
    expect(awaiting.value).toBe('8');
    expect(awaiting.sub).toBe('re-score approval required');
    const shared = buildRelatedRolePipelineStats({
      status,
      rosterFallback: 10,
      belowThresholdCount: 1,
      thresholdValue: 60,
      budget: { value: '$0', sub: 'of $50 cap' },
      monthlyBudgetCents: 5000,
    }).find((tile) => tile.key === 'shared');
    expect(shared.sub).toBe('0 current related scores · 8 stale snapshots visible');
  });

  it('uses the live source cohort rather than the smaller evaluation-row count', () => {
    const tiles = buildRelatedRolePipelineStats({
      status: {
        status: 'running',
        total: 1,
        cohort_total: 3,
        cohort_scoreable: 2,
        cohort_unscorable: 0,
        cohort_excluded: 1,
        counts: { pending: 1, done: 0 },
      },
      rosterFallback: 1,
      belowThresholdCount: 0,
      thresholdValue: 60,
      budget: { value: '$0', sub: 'of $50 cap' },
      monthlyBudgetCents: 5000,
    });

    expect(tiles.find((tile) => tile.key === 'shared')?.value).toBe('3');
    expect(tiles.find((tile) => tile.key === 'unscored')?.value).toBe('2');
    expect(tiles.find((tile) => tile.key === 'not-scored')?.sub).toBe('1 ATS-closed');
  });
});

describe('related-role legacy workspace recovery', () => {
  it('keeps targeted recovery owner-only and promises unrelated roles stay paused', () => {
    const onResumeWorkspace = vi.fn();
    const view = render(
      <RelatedRoleContextBanner
        role={{ ats_owner_role_name: 'Original engineering role' }}
        providerLabel="Workable"
        status={{ status: 'waiting', waiting_reason: 'workspace_paused' }}
        agentStatus={{ workspace_paused: true }}
        canResumeWorkspace={false}
        onResumeWorkspace={onResumeWorkspace}
        onOpenOriginal={vi.fn()}
      />,
    );

    expect(screen.getByText(/legacy workspace-wide agent hold is blocking scoring/i))
      .toBeInTheDocument();
    expect(screen.getByText(/every unrelated role stays paused/i)).toBeInTheDocument();
    const resume = screen.getByRole('button', { name: 'Recover this related role' });
    expect(resume).toBeDisabled();
    expect(resume).toHaveAttribute(
      'title',
      'Only workspace owners can recover this related role.',
    );
    fireEvent.click(resume);
    expect(onResumeWorkspace).not.toHaveBeenCalled();

    view.rerender(
      <RelatedRoleContextBanner
        role={{ ats_owner_role_name: 'Original engineering role' }}
        providerLabel="Workable"
        status={{ status: 'waiting', waiting_reason: 'workspace_paused' }}
        agentStatus={{ workspace_paused: true }}
        canResumeWorkspace
        recoveryScope={makeResumeProps().recoveryScope}
        recoveryScopeReady
        onResumeWorkspace={onResumeWorkspace}
        onOpenOriginal={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Recover this related role' }))
      .toBeEnabled();
    expect(screen.getByText(/Exact recovery scope checked: 7 scoreable of 9 shared candidates/i))
      .toBeInTheDocument();
  });

  it('leaves ordinary role resume available to role-authorized members', async () => {
    const onResumeRole = vi.fn().mockResolvedValue('role resumed');
    const { result, props } = setupResume({
      agentStatus: { workspace_paused: false },
      canResumeWorkspace: false,
      onResumeRole,
    });

    let outcome;
    await act(async () => {
      outcome = await result.current();
    });

    expect(outcome).toBe('role resumed');
    expect(onResumeRole).toHaveBeenCalledOnce();
    expect(props.recoverRelatedRole).not.toHaveBeenCalled();
  });

  it('guards targeted recovery even when invoked outside the disabled UI', async () => {
    const { result, props } = setupResume({ canResumeWorkspace: false });

    await act(async () => {
      expect(await result.current()).toBe(false);
    });

    expect(props.refetchAgentStatus).not.toHaveBeenCalled();
    expect(props.recoverRelatedRole).not.toHaveBeenCalled();
    expect(props.showToast).toHaveBeenCalledWith(
      'Only a workspace owner can recover this related role.',
      'error',
    );
  });

  it('sends the exact viewed role, family, version, and cohort to the targeted endpoint', async () => {
    const { result, props } = setupResume();

    await act(async () => {
      await result.current();
    });

    expect(props.recoverRelatedRole).toHaveBeenCalledWith(47, {
      expected_version: 7,
      expected_workspace_control_version: 7,
      expected_role_family: props.recoveryScope.role_family,
      cohort_fingerprint: 'a'.repeat(64),
      approved_max_candidates_total: 9,
      approved_max_scoreable_count: 7,
    });
    expect(props.refetchAgentStatus).toHaveBeenCalledOnce();
    expect(props.setPollingVersion).toHaveBeenCalledWith(expect.any(Function));
    expect(props.reloadRole).toHaveBeenCalledOnce();
    expect(props.showToast).toHaveBeenCalledWith(
      'This related role was recovered. Every unrelated role remains paused.',
      'success',
    );
  });

  it('reports that a distinct target pause was preserved', async () => {
    const { result, props } = setupResume({
      recoverRelatedRole: vi.fn().mockResolvedValue({
        data: { role_id: 47, resumed: false, preserved_paused_count: 2 },
      }),
    });

    await act(async () => {
      await result.current();
    });

    expect(props.showToast).toHaveBeenCalledWith(
      expect.stringMatching(/keeps its existing pause/i),
      'success',
    );
  });

  it('refreshes on scope drift and never retries the targeted mutation', async () => {
    const { result, props } = setupResume({
      recoverRelatedRole: vi.fn().mockRejectedValue({
        response: {
          status: 409,
          data: { detail: { code: 'RELATED_ROLE_RECOVERY_SCOPE_CHANGED' } },
        },
      }),
    });

    await act(async () => {
      expect(await result.current()).toBe(false);
    });

    expect(props.recoverRelatedRole).toHaveBeenCalledTimes(1);
    expect(props.refetchAgentStatus).toHaveBeenCalledOnce();
    expect(props.setPollingVersion).toHaveBeenCalledWith(expect.any(Function));
    expect(props.reloadRole).toHaveBeenCalledOnce();
    expect(props.showToast).toHaveBeenCalledWith(
      'The related role, family, cohort, or legacy hold changed. Review the refreshed scope and recover again.',
      'warning',
    );
  });

  it('fails closed when the viewed cohort proof is unavailable', async () => {
    const { result, props } = setupResume({
      recoveryScope: {
        role_id: 47,
        role_version: 7,
        workspace_paused: true,
        workspace_control_version: 7,
        role_family: makeResumeProps().role.role_family,
        cohort_total: 8,
        cohort_scoreable: 6,
      },
    });

    await act(async () => {
      expect(await result.current()).toBe(false);
    });

    expect(props.recoverRelatedRole).not.toHaveBeenCalled();
    expect(props.showToast).toHaveBeenCalledWith(
      'Related-role recovery scope is still loading. Try again.',
      'error',
    );
  });
});
