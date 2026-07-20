import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const chatApi = vi.hoisted(() => ({
  freshAuth: vi.fn(),
  turnUrl: vi.fn(() => '/api/v1/taali-chat/turn'),
}));

vi.mock('./api', () => chatApi);

import useChatStream from './useChatStream';
import {
  initializeSessionBoundary,
  SESSION_BOUNDARY_STORAGE_KEY,
} from '../../shared/auth/sessionBoundary';

const deferred = () => {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return { promise, resolve };
};

describe('useChatStream session isolation', () => {
  beforeEach(() => {
    localStorage.clear();
    initializeSessionBoundary();
    chatApi.freshAuth.mockReset().mockImplementation(async () => ({
      headers: { Authorization: 'Bearer account-a-token' },
      sessionBoundary: localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY),
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('does not publish an old-account stream response after the boundary changes', async () => {
    const response = deferred();
    const getReader = vi.fn();
    const fetchMock = vi.fn(() => response.promise);
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useChatStream());
    let sendPromise;

    act(() => {
      sendPromise = result.current.send('Show private account data');
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, 'account-b-stream-boundary');

    await act(async () => {
      response.resolve({
        ok: true,
        body: { getReader },
      });
      await sendPromise;
    });

    expect(getReader).not.toHaveBeenCalled();
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.messages[1]?.parts).toEqual([]);
  });
});
