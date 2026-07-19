import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import useChatStream from './useChatStream';

describe('useChatStream transcript hydration', () => {
  it('prepends older pages in order and de-duplicates cursor overlap', () => {
    const { result } = renderHook(() => useChatStream());
    const latest = [
      { id: 'm_3', role: 'user', parts: [] },
      { id: 'm_4', role: 'assistant', parts: [] },
    ];

    act(() => result.current.setHistory(latest));
    act(() => result.current.prependHistory([
      { id: 'm_1', role: 'user', parts: [] },
      { id: 'm_2', role: 'assistant', parts: [] },
      { id: 'm_3', role: 'user', parts: [] },
    ]));

    expect(result.current.messages.map((message) => message.id)).toEqual([
      'm_1',
      'm_2',
      'm_3',
      'm_4',
    ]);
  });

  it('reconciles a page boundary against the existing recent page atomically', () => {
    const { result } = renderHook(() => useChatStream());
    act(() => result.current.setHistory([{ id: 'm_3', parts: ['recent'] }]));

    const reconcile = (messages) => messages.map((message) => ({
      ...message,
      reconciled: true,
    }));
    act(() => result.current.prependHistory(
      [{ id: 'm_2', parts: ['older'] }],
      reconcile,
    ));

    expect(result.current.messages).toEqual([
      { id: 'm_2', parts: ['older'], reconciled: true },
      { id: 'm_3', parts: ['recent'], reconciled: true },
    ]);
  });
});
