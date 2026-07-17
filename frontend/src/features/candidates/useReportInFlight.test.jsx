import { useState } from 'react';
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useReportInFlight } from './useReportInFlight';

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function renderPollingHook(getApplication) {
  const loadAgentDecision = vi.fn();
  const loadStandingReport = vi.fn();
  const hook = renderHook(() => {
    const [evaluating, setEvaluating] = useState(true);
    useReportInFlight({
      rolesApi: { getApplication },
      numericApplicationId: 42,
      isShareRoute: false,
      activeTab: 'overview',
      application: { id: 42, cv_match_score: null },
      agentDecision: null,
      evaluating,
      setEvaluating,
      setApplication: vi.fn(),
      loadAgentDecision,
      loadStandingReport,
    });
    return { evaluating };
  });
  return { ...hook, loadAgentDecision, loadStandingReport };
}

describe('useReportInFlight polling', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits for a poll to finish before scheduling the next request', async () => {
    vi.useFakeTimers();
    const first = deferred();
    const getApplication = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue({ data: { id: 42, cv_match_score: null } });

    renderPollingHook(getApplication);

    act(() => { vi.advanceTimersByTime(4_000); });
    expect(getApplication).toHaveBeenCalledTimes(1);

    act(() => { vi.advanceTimersByTime(12_000); });
    expect(getApplication).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve({ data: { id: 42, cv_match_score: null } });
      await first.promise;
    });

    act(() => { vi.advanceTimersByTime(3_999); });
    expect(getApplication).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(getApplication).toHaveBeenCalledTimes(2);
  });

  it.each(['done', 'error', 'cancelled', 'unscorable', 'excluded'])(
    'stops polling and reloads authoritative state when scoring ends as %s without a score',
    async (scoreStatus) => {
      vi.useFakeTimers();
      const getApplication = vi.fn().mockResolvedValue({
        data: {
          id: 42,
          cv_match_score: null,
          score_status: scoreStatus,
          cv_match_details: scoreStatus === 'error' ? { error: 'provider_unavailable' } : null,
        },
      });
      const { result, loadAgentDecision, loadStandingReport } = renderPollingHook(getApplication);

      await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });

      expect(result.current.evaluating).toBe(false);
      expect(loadAgentDecision).toHaveBeenCalledOnce();
      expect(loadStandingReport).toHaveBeenCalledWith({ silent: true });

      await act(async () => { await vi.advanceTimersByTimeAsync(12_000); });
      expect(getApplication).toHaveBeenCalledOnce();
    },
  );

  it.each(['pending', 'running', 'retry_wait', 'stale'])(
    'keeps polling while scoring remains recoverable as %s',
    async (scoreStatus) => {
      vi.useFakeTimers();
      const getApplication = vi.fn().mockResolvedValue({
        data: { id: 42, cv_match_score: null, score_status: scoreStatus },
      });
      const { result, loadAgentDecision, loadStandingReport } = renderPollingHook(getApplication);

      await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });

      expect(result.current.evaluating).toBe(true);
      expect(loadAgentDecision).not.toHaveBeenCalled();
      expect(loadStandingReport).not.toHaveBeenCalled();

      await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
      expect(getApplication).toHaveBeenCalledTimes(2);
    },
  );
});

describe('useReportInFlight lazy CV text', () => {
  it('waits for the route-matching application before requesting or merging CV text', async () => {
    const getApplication = vi.fn().mockResolvedValue({
      data: { id: 42, cv_text: 'Current CV', cv_sections: { skills: ['React'] } },
    });
    const setApplication = vi.fn();
    const baseProps = {
      rolesApi: { getApplication },
      numericApplicationId: 42,
      isShareRoute: false,
      activeTab: 'cv',
      agentDecision: null,
      evaluating: false,
      setEvaluating: vi.fn(),
      setApplication,
      loadAgentDecision: vi.fn(),
      loadStandingReport: vi.fn(),
    };
    const { rerender } = renderHook(
      ({ application }) => useReportInFlight({ ...baseProps, application }),
      { initialProps: { application: { id: 41, cv_text: null } } },
    );

    expect(getApplication).not.toHaveBeenCalled();

    rerender({ application: { id: 42, cv_text: null } });
    await act(async () => {});

    expect(getApplication).toHaveBeenCalledOnce();
    expect(getApplication).toHaveBeenCalledWith(42, { params: { include_cv_text: true } });
    const merge = setApplication.mock.calls[0][0];
    expect(merge({ id: 42, cv_text: null })).toEqual({
      id: 42,
      cv_text: 'Current CV',
      cv_sections: { skills: ['React'] },
    });
    expect(merge({ id: 41, cv_text: null })).toEqual({ id: 41, cv_text: null });
  });
});
