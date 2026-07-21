// useChatStream — minimal AI SDK v3 Data Stream Protocol parser.
//
// Calls POST /api/v1/taali-chat/turn with a bearer token, reads the SSE
// response, and builds up a message log shaped like:
//
//   {
//     id: 'msg-...',
//     role: 'user' | 'assistant',
//     parts: [
//       { type: 'text', text: '...' },
//       { type: 'tool_call', toolCallId, toolName, args, status: 'streaming'|'complete'|'error' },
//       { type: 'tool_result', toolCallId, result }
//     ]
//   }
//
// The component renders parts in order. Tool call cards correlate by
// `toolCallId` between the call and its later result.

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  isSessionBoundaryCurrent,
  isStoredSessionBoundaryActive,
  SESSION_BOUNDARY_EVENT,
} from '../../shared/auth/sessionBoundary';
import { freshAuth, turnUrl } from './api';

const newId = () => `m_${Math.random().toString(36).slice(2, 9)}`;

const parseLine = (line) => {
  if (!line) return null;
  const colon = line.indexOf(':');
  if (colon < 1) return null;
  const prefix = line.slice(0, colon);
  const json = line.slice(colon + 1);
  try {
    return { prefix, payload: JSON.parse(json) };
  } catch {
    return null;
  }
};

const useChatStream = ({ conversationId, onConversationId } = {}) => {
  const [messages, setMessages] = useState([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const reset = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  // Clears just the error banner without touching the message log — used
  // when switching to a different conversation (so conv A's error frame
  // doesn't render over conv B) or when the user hits "Try again".
  const clearError = useCallback(() => {
    setError(null);
  }, []);

  // Replaces the message log with rows hydrated from the persistence
  // API. We intentionally do NOT clear the ``error`` state here — that
  // would race with an in-flight stream's error frame (caller is
  // responsible for resetting it via ``reset()`` / ``clearError()`` when
  // switching conversations).
  const setHistory = useCallback((history) => {
    setMessages(history);
  }, []);

  const send = useCallback(
    async (userText) => {
      const text = (userText || '').trim();
      if (!text || isStreaming) return;

      // Append the user message and a placeholder assistant message we
      // mutate as frames arrive.
      const userMsg = {
        id: newId(),
        role: 'user',
        parts: [{ type: 'text', text }],
      };
      const assistantId = newId();
      const assistantMsg = { id: assistantId, role: 'assistant', parts: [] };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsStreaming(true);
      setError(null);

      const controller = new AbortController();
      abortRef.current = controller;
      let streamBoundary = null;

      try {
        const { headers, sessionBoundary } = await freshAuth();
        streamBoundary = sessionBoundary;
        const assertCurrentSession = () => {
          if (isSessionBoundaryCurrent(sessionBoundary)) return;
          controller.abort();
          const sessionError = new Error('Session changed in another tab. Please sign in again.');
          sessionError.name = 'AbortError';
          throw sessionError;
        };
        assertCurrentSession();
        const resp = await fetch(turnUrl(), {
          method: 'POST',
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            ...headers,
          },
          body: JSON.stringify({
            message: text,
            conversation_id: conversationId ?? null,
          }),
        });
        assertCurrentSession();
        if (!resp.ok) {
          const detail = await resp.text().catch(() => '');
          assertCurrentSession();
          throw new Error(`HTTP ${resp.status}: ${detail}`);
        }
        if (!resp.body) throw new Error('No response body');

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        const updateAssistant = (mutator) => {
          setMessages((prev) =>
            (isStoredSessionBoundaryActive(streamBoundary)
              ? prev.map((m) => (m.id === assistantId ? mutator(m) : m))
              : prev),
          );
        };

        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { value, done } = await reader.read();
          assertCurrentSession();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let nlIdx;
          while ((nlIdx = buffer.indexOf('\n')) !== -1) {
            const line = buffer.slice(0, nlIdx);
            buffer = buffer.slice(nlIdx + 1);
            const frame = parseLine(line);
            if (!frame) continue;
            const { prefix, payload } = frame;

            switch (prefix) {
              case '0': {
                // text-delta — payload is a plain string
                updateAssistant((m) => {
                  const next = m.parts.filter((part) => part.type !== 'progress');
                  const idx = next.length - 1;
                  if (idx < 0 || next[idx]?.type !== 'text') {
                    next.push({ type: 'text', text: '' });
                  }
                  const textIndex = next.length - 1;
                  next[textIndex] = {
                    ...next[textIndex],
                    text: next[textIndex].text + String(payload),
                  };
                  return { ...m, parts: next };
                });
                break;
              }
              case '2': {
                // server-side data — we use it for { conversation_id }
                const arr = Array.isArray(payload) ? payload : [payload];
                for (const item of arr) {
                  if (item && typeof item.conversation_id === 'number') {
                    if (isSessionBoundaryCurrent(streamBoundary)) {
                      onConversationId?.(item.conversation_id);
                    }
                  }
                  if (item?.progress && typeof item.progress.label === 'string') {
                    updateAssistant((m) => ({
                      ...m,
                      parts: [
                        ...m.parts.filter((part) => part.type !== 'progress'),
                        { type: 'progress', ...item.progress },
                      ],
                    }));
                  }
                }
                break;
              }
              case 'b': {
                // tool_call_streaming_start
                updateAssistant((m) => ({
                  ...m,
                  parts: [
                    ...m.parts.filter((part) => part.type !== 'progress'),
                    {
                      type: 'tool_call',
                      toolCallId: payload.toolCallId,
                      toolName: payload.toolName,
                      args: null,
                      argsText: '',
                      status: 'streaming',
                    },
                  ],
                }));
                break;
              }
              case 'c': {
                // tool_call_delta — argsTextDelta
                updateAssistant((m) => {
                  const next = m.parts.slice();
                  const idx = next.findIndex(
                    (p) => p.type === 'tool_call' && p.toolCallId === payload.toolCallId,
                  );
                  if (idx >= 0) {
                    const p = next[idx];
                    next[idx] = {
                      ...p,
                      argsText: (p.argsText || '') + (payload.argsTextDelta || ''),
                    };
                  }
                  return { ...m, parts: next };
                });
                break;
              }
              case '9': {
                // tool_call (complete)
                updateAssistant((m) => {
                  const next = m.parts.slice();
                  const idx = next.findIndex(
                    (p) => p.type === 'tool_call' && p.toolCallId === payload.toolCallId,
                  );
                  if (idx >= 0) {
                    next[idx] = {
                      ...next[idx],
                      args: payload.args || {},
                      status: 'awaiting_result',
                    };
                  } else {
                    next.push({
                      type: 'tool_call',
                      toolCallId: payload.toolCallId,
                      toolName: payload.toolName,
                      args: payload.args || {},
                      status: 'awaiting_result',
                    });
                  }
                  return { ...m, parts: next };
                });
                break;
              }
              case 'a': {
                // tool_result
                const isError =
                  payload.result &&
                  typeof payload.result === 'object' &&
                  (
                    'error' in payload.result ||
                    payload.result.code === 'candidate_search_unavailable' ||
                    payload.result.search_completed === false
                  );
                updateAssistant((m) => {
                  const next = m.parts.slice();
                  const idx = next.findIndex(
                    (p) => p.type === 'tool_call' && p.toolCallId === payload.toolCallId,
                  );
                  if (idx >= 0) {
                    next[idx] = {
                      ...next[idx],
                      result: payload.result,
                      status: isError ? 'error' : 'complete',
                    };
                  }
                  return { ...m, parts: next };
                });
                break;
              }
              case '3': {
                // error
                if (isSessionBoundaryCurrent(streamBoundary)) setError(String(payload));
                break;
              }
              case 'd':
              case 'e':
              case 'f':
              default:
                // finish-message / finish-step / start-step / unknown — no UI side effect
                break;
            }
          }
        }
      } catch (err) {
        const cancelled = err?.name === 'AbortError' || err?.code === 'ERR_CANCELED';
        if (!cancelled && streamBoundary && isSessionBoundaryCurrent(streamBoundary)) {
          setError(err?.message || String(err));
        }
      } finally {
        // A boundary reset may already have allowed a new-session stream to
        // start. Only the controller that owns this send may clear the ref or
        // its streaming flag when its old promise finally settles.
        if (abortRef.current === controller) {
          setIsStreaming(false);
          abortRef.current = null;
        }
      }
    },
    [conversationId, isStreaming, onConversationId],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  useEffect(
    () => {
      const resetForSessionChange = () => {
        abortRef.current?.abort();
        abortRef.current = null;
        setMessages([]);
        setError(null);
        setIsStreaming(false);
      };
      window.addEventListener(SESSION_BOUNDARY_EVENT, resetForSessionChange);
      return () => {
        window.removeEventListener(SESSION_BOUNDARY_EVENT, resetForSessionChange);
        abortRef.current?.abort();
      };
    },
    [],
  );

  return { messages, isStreaming, error, send, stop, reset, clearError, setHistory };
};

export default useChatStream;
