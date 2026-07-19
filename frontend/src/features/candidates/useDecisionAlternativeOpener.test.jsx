import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  useAgentDecisionReader,
  useDecisionAlternativeOpener,
  useRouteOperationFence,
} from './useDecisionAlternativeOpener';

const deferred = () => {
  let resolve;
  const promise = new Promise((next) => { resolve = next; });
  return { promise, resolve };
};

const setup = (organizationsApi) => {
  const props = {
    organizationsApi,
    setAlternativeFor: vi.fn(),
    setBusy: vi.fn(),
    showToast: vi.fn(),
  };
  const { result } = renderHook(() => useDecisionAlternativeOpener(props));
  return { open: result.current, props };
};

describe('useDecisionAlternativeOpener', () => {
  it('opens a linked Workable action with freshly loaded stages', async () => {
    const stages = [{ slug: 'interview', name: 'Interview' }];
    const { open, props } = setup({
      getWorkableStages: vi.fn().mockResolvedValue({ data: { stages } }),
    });
    const decision = { id: 7, workable_job_id: 'role-shortcode' };
    const alternative = { requireStagePick: true };

    await act(async () => open(decision, alternative));

    expect(props.setAlternativeFor).toHaveBeenCalledWith({
      decision,
      alternative,
      workableStages: stages,
    });
    expect(props.setBusy.mock.calls).toEqual([[true], [false]]);
  });

  it('synchronously single-flights a route operation and invalidates it on identity change', () => {
    const { result, rerender } = renderHook(
      ({ identity }) => useRouteOperationFence(identity),
      { initialProps: { identity: 'candidate-42' } },
    );
    let first;
    act(() => { first = result.current.begin('decision', 'candidate-42'); });
    expect(first).not.toBeNull();
    expect(result.current.begin('decision', 'candidate-42')).toBeNull();
    expect(result.current.commit(first, vi.fn())).toBe(true);

    rerender({ identity: 'candidate-43' });
    expect(result.current.isCurrent(first)).toBe(false);
    expect(result.current.begin('decision', 'candidate-42')).toBeNull();
    const second = result.current.begin('decision', 'candidate-43');
    const completed = vi.fn();
    expect(result.current.finish(second, completed)).toBe(true);
    expect(completed).toHaveBeenCalledTimes(1);
  });

  it('single-flights a decision read and rejects a superseded generation', async () => {
    const first = deferred();
    const freshDecision = { id: 9, status: 'current' };
    const agentApi = {
      listDecisions: vi.fn()
        .mockReturnValueOnce(first.promise)
        .mockResolvedValueOnce({ data: [freshDecision] }),
    };
    const setAgentDecision = vi.fn();
    const { result } = renderHook(() => useAgentDecisionReader({
      agentApi,
      applicationId: 42,
      identity: 'candidate-42',
      isShareRoute: false,
      setAgentDecision,
    }));

    let firstLoad;
    let duplicateLoad;
    act(() => {
      firstLoad = result.current.load();
      duplicateLoad = result.current.load();
    });
    expect(duplicateLoad).toBe(firstLoad);
    expect(agentApi.listDecisions).toHaveBeenCalledTimes(1);

    act(() => { result.current.begin(); });
    await act(async () => {
      first.resolve({ data: [{ id: 7, status: 'current' }] });
      await firstLoad;
    });
    expect(setAgentDecision).not.toHaveBeenCalled();

    await act(async () => result.current.load());
    expect(agentApi.listDecisions).toHaveBeenCalledTimes(2);
    expect(setAgentDecision).toHaveBeenCalledWith(freshDecision);
  });

  it('drops a stage lookup when the authoritative decision changes on the same route', async () => {
    const stages = deferred();
    const originalDecision = { id: 7, workable_job_id: 'role-shortcode' };
    let currentDecision = originalDecision;
    const props = {
      organizationsApi: { getWorkableStages: vi.fn().mockReturnValue(stages.promise) },
      isCurrent: (decision) => !decision || decision === currentDecision,
      setAlternativeFor: vi.fn(),
      setBusy: vi.fn(),
      showToast: vi.fn(),
    };
    const { result } = renderHook(() => useDecisionAlternativeOpener(props));

    let pending;
    act(() => {
      pending = result.current(originalDecision, { requireStagePick: true });
    });
    currentDecision = { ...originalDecision, id: 8 };
    await act(async () => {
      stages.resolve({ data: { stages: [{ slug: 'interview', name: 'Interview' }] } });
      await pending;
    });

    expect(props.setAlternativeFor).not.toHaveBeenCalled();
    expect(props.setBusy.mock.calls).toEqual([[true], [false]]);
  });

  it('keeps the modal closed when the stage lookup fails', async () => {
    const { open, props } = setup({
      getWorkableStages: vi.fn().mockRejectedValue(new Error('network down')),
    });

    await act(async () => open(
      { id: 7, workable_job_id: 'role-shortcode' },
      { requireStagePick: true },
    ));

    expect(props.setAlternativeFor).not.toHaveBeenCalled();
    expect(props.showToast).toHaveBeenCalledWith(
      "Couldn't load Workable stages. Try again.",
      'error',
    );
    expect(props.setBusy.mock.calls).toEqual([[true], [false]]);
  });
});
