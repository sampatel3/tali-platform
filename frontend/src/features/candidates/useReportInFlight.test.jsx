import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useReportInFlight } from './useReportInFlight';

describe('useReportInFlight role-scoped polling', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it.each([
    ['the viewed related role', 135, 135],
    ['the reconciled application role', 31, 135],
  ])('polls %s instead of the canonical application', async (_label, applicationRoleId, viewRoleId) => {
    const getApplication = vi.fn().mockResolvedValue({ data: { cv_match_score: null } });

    const { unmount } = renderHook(() => useReportInFlight({
      rolesApi: { getApplication },
      numericApplicationId: 77,
      viewRoleId,
      isShareRoute: false,
      activeTab: 'overview',
      application: { id: 77, role_id: applicationRoleId, cv_match_score: null },
      agentDecision: null,
      evaluating: true,
      setEvaluating: vi.fn(),
      setApplication: vi.fn(),
      loadAgentDecision: vi.fn(),
      loadStandingReport: vi.fn(),
    }));

    await act(async () => vi.advanceTimersByTimeAsync(4000));

    expect(getApplication).toHaveBeenCalledWith(77, {
      params: { view_role_id: applicationRoleId },
    });
    unmount();
  });

  it('polls a processing approval through the decision endpoint only', async () => {
    const getApplication = vi.fn();
    const loadAgentDecision = vi.fn().mockResolvedValue(undefined);
    const loadStandingReport = vi.fn();

    const { rerender, unmount } = renderHook(({ status }) => useReportInFlight({
      rolesApi: { getApplication },
      numericApplicationId: 77,
      viewRoleId: 31,
      isShareRoute: false,
      activeTab: 'overview',
      application: { id: 77, role_id: 31, cv_match_score: 68 },
      agentDecision: { id: 42, application_id: 77, status },
      evaluating: false,
      setEvaluating: vi.fn(),
      setApplication: vi.fn(),
      loadAgentDecision,
      loadStandingReport,
    }), { initialProps: { status: 'processing' } });

    await act(async () => vi.advanceTimersByTimeAsync(4000));

    expect(loadAgentDecision).toHaveBeenCalledOnce();
    expect(getApplication).not.toHaveBeenCalled();

    rerender({ status: 'approved' });
    expect(loadStandingReport).toHaveBeenCalledWith({ silent: true });
    unmount();
  });

  it('does not overlap decision polls while the previous request is unresolved', async () => {
    let resolveDecisionPoll;
    const loadAgentDecision = vi.fn().mockImplementation(() => new Promise((resolve) => {
      resolveDecisionPoll = resolve;
    }));

    const { unmount } = renderHook(() => useReportInFlight({
      rolesApi: { getApplication: vi.fn() },
      numericApplicationId: 77,
      viewRoleId: 31,
      isShareRoute: false,
      activeTab: 'overview',
      application: { id: 77, role_id: 31, cv_match_score: 68 },
      agentDecision: { id: 42, application_id: 77, status: 'processing' },
      evaluating: false,
      setEvaluating: vi.fn(),
      setApplication: vi.fn(),
      loadAgentDecision,
      loadStandingReport: vi.fn(),
    }));

    await act(async () => vi.advanceTimersByTimeAsync(12_000));
    expect(loadAgentDecision).toHaveBeenCalledOnce();

    await act(async () => {
      resolveDecisionPoll();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(loadAgentDecision).toHaveBeenCalledTimes(2);
    unmount();
  });

  it('does not treat navigation away from a rescoring candidate as completion', () => {
    const loadStandingReport = vi.fn();
    const shared = {
      rolesApi: { getApplication: vi.fn() },
      viewRoleId: 31,
      isShareRoute: false,
      activeTab: 'overview',
      evaluating: false,
      setEvaluating: vi.fn(),
      setApplication: vi.fn(),
      loadAgentDecision: vi.fn(),
      loadStandingReport,
    };
    const { rerender, unmount } = renderHook(({ applicationId, decision }) => useReportInFlight({
      ...shared,
      numericApplicationId: applicationId,
      application: { id: applicationId, role_id: 31, cv_match_score: 68 },
      agentDecision: decision,
    }), {
      initialProps: {
        applicationId: 77,
        decision: { id: 42, status: 'pending', rescore_in_flight: true },
      },
    });

    rerender({ applicationId: 88, decision: null });
    expect(loadStandingReport).not.toHaveBeenCalled();
    unmount();
  });

  it('reloads CV text when the same application is viewed in another logical role', async () => {
    vi.useRealTimers();
    const getApplication = vi.fn()
      .mockResolvedValueOnce({ data: { cv_text: 'Role A CV' } })
      .mockResolvedValueOnce({ data: { cv_text: 'Role B CV' } });
    const setApplication = vi.fn();
    const shared = {
      rolesApi: { getApplication },
      numericApplicationId: 77,
      isShareRoute: false,
      activeTab: 'cv',
      agentDecision: null,
      evaluating: false,
      setEvaluating: vi.fn(),
      setApplication,
      loadAgentDecision: vi.fn(),
      loadStandingReport: vi.fn(),
    };
    const { rerender, unmount } = renderHook(({ roleId }) => useReportInFlight({
      ...shared,
      viewRoleId: roleId,
      application: { id: 77, role_id: roleId, cv_match_score: 68 },
    }), { initialProps: { roleId: 31 } });

    await act(async () => { await Promise.resolve(); });
    expect(getApplication).toHaveBeenNthCalledWith(1, 77, {
      params: { include_cv_text: true, view_role_id: 31 },
    });

    rerender({ roleId: 135 });
    await act(async () => { await Promise.resolve(); });

    expect(getApplication).toHaveBeenNthCalledWith(2, 77, {
      params: { include_cv_text: true, view_role_id: 135 },
    });
    expect(setApplication).toHaveBeenCalledTimes(2);
    unmount();
  });
});
