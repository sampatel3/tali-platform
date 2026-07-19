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
});
