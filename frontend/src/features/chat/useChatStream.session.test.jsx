import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const chatApi = vi.hoisted(() => ({
  freshAuth: vi.fn(),
  turnUrl: vi.fn(() => '/api/v1/taali-chat/turn'),
}));

vi.mock('./api', () => chatApi);

import useChatStream from './useChatStream';
import {
  activateSessionBoundary,
  beginSessionTransition,
  captureStoredSessionBoundary,
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
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'account-a-token');
    chatApi.freshAuth.mockReset().mockImplementation(async () => ({
      headers: { Authorization: 'Bearer account-a-token' },
      sessionBoundary: captureStoredSessionBoundary(),
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
    expect(result.current.messages).toEqual([]);
  });

  it('does not publish a delayed old-account error body after the boundary changes', async () => {
    const errorBody = deferred();
    const text = vi.fn(() => errorBody.promise);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, text }));
    const { result } = renderHook(() => useChatStream());
    let sendPromise;

    act(() => {
      sendPromise = result.current.send('Show private account data');
    });
    await waitFor(() => expect(text).toHaveBeenCalledTimes(1));
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, 'account-b-error-boundary');

    await act(async () => {
      errorBody.resolve('private account A error');
      await sendPromise;
    });

    expect(result.current.error).toBeNull();
    expect(JSON.stringify(result.current.messages)).not.toContain('private account A error');
  });

  it('does not publish later frames or callbacks from an old-account reader', async () => {
    const secondRead = deferred();
    const encoder = new TextEncoder();
    const read = vi.fn()
      .mockResolvedValueOnce({
        done: false,
        value: encoder.encode('0:"allowed"\n2:[{"conversation_id":12}]\n'),
      })
      .mockImplementationOnce(() => secondRead.promise);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => ({ read }) },
    }));
    const onConversationId = vi.fn();
    const { result } = renderHook(() => useChatStream({ onConversationId }));
    let sendPromise;

    act(() => {
      sendPromise = result.current.send('Stream private account data');
    });
    await waitFor(() => {
      expect(JSON.stringify(result.current.messages)).toContain('allowed');
      expect(onConversationId).toHaveBeenCalledWith(12);
    });
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, 'account-b-frame-boundary');

    await act(async () => {
      secondRead.resolve({
        done: false,
        value: encoder.encode('0:"private account A frame"\n2:[{"conversation_id":99}]\n'),
      });
      await sendPromise;
    });

    expect(result.current.messages).toEqual([]);
    expect(JSON.stringify(result.current.messages)).not.toContain('private account A frame');
    expect(onConversationId).toHaveBeenCalledTimes(1);
  });

  it('keeps text on both sides of a tool call in protocol order', async () => {
    const encoder = new TextEncoder();
    const frames = [
      '0:"Before tool"',
      'b:{"toolCallId":"tool-1","toolName":"search"}',
      '9:{"toolCallId":"tool-1","toolName":"search","args":{"q":"candidate"}}',
      'a:{"toolCallId":"tool-1","result":{"matches":2}}',
      '0:"After tool"',
      '',
    ].join('\n');
    const read = vi.fn()
      .mockResolvedValueOnce({ done: false, value: encoder.encode(frames) })
      .mockResolvedValueOnce({ done: true });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => ({ read }) },
    }));
    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send('Use the search tool');
    });

    expect(result.current.messages[1]?.parts).toEqual([
      { type: 'text', text: 'Before tool' },
      expect.objectContaining({
        type: 'tool_call',
        toolCallId: 'tool-1',
        toolName: 'search',
        args: { q: 'candidate' },
        result: { matches: 2 },
        status: 'complete',
      }),
      { type: 'text', text: 'After tool' },
    ]);
  });

  it('renders a typed candidate-search failure as an error tool result', async () => {
    const encoder = new TextEncoder();
    const failure = {
      code: 'candidate_search_unavailable',
      error: 'The verified search did not complete.',
      tool: 'find_top_candidates',
      retryable: true,
      search_completed: false,
      is_exact_empty: null,
      incident_id: 'incident-123',
    };
    const frames = [
      'b:{"toolCallId":"tool-search","toolName":"find_top_candidates"}',
      '9:{"toolCallId":"tool-search","toolName":"find_top_candidates","args":{"query":"PySpark"}}',
      `a:${JSON.stringify({ toolCallId: 'tool-search', result: failure })}`,
      '0:"I could not complete a verified candidate search."',
      '',
    ].join('\n');
    const read = vi.fn()
      .mockResolvedValueOnce({ done: false, value: encoder.encode(frames) })
      .mockResolvedValueOnce({ done: true });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => ({ read }) },
    }));
    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send('Find PySpark candidates');
    });

    expect(result.current.messages[1]?.parts).toEqual([
      expect.objectContaining({
        type: 'tool_call',
        toolCallId: 'tool-search',
        result: failure,
        status: 'error',
      }),
      { type: 'text', text: 'I could not complete a verified candidate search.' },
    ]);
  });

  it('does not let an old send clear a newer session stream controller', async () => {
    const accountAResponse = deferred();
    const accountBResponse = deferred();
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => accountAResponse.promise)
      .mockImplementationOnce(() => accountBResponse.promise);
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useChatStream());
    let accountASend;
    let accountBSend;

    act(() => { accountASend = result.current.send('Account A prompt'); });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    act(() => {
      const accountBBoundary = beginSessionTransition();
      activateSessionBoundary(accountBBoundary, 'account-b-token');
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    act(() => { accountBSend = result.current.send('Account B prompt'); });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    await act(async () => {
      accountAResponse.resolve({
        ok: true,
        body: { getReader: () => ({ read: vi.fn().mockResolvedValue({ done: true }) }) },
      });
      await accountASend;
    });
    expect(result.current.isStreaming).toBe(true);

    await act(async () => {
      accountBResponse.resolve({
        ok: true,
        body: { getReader: () => ({ read: vi.fn().mockResolvedValue({ done: true }) }) },
      });
      await accountBSend;
    });
    expect(result.current.isStreaming).toBe(false);
  });
});
