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
import { freshAuthHeaders, turnUrl } from './api';

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

      try {
        const headers = await freshAuthHeaders();
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
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}: ${await resp.text().catch(() => '')}`);
        }
        if (!resp.body) throw new Error('No response body');

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let currentText = null; // index of an open text part we keep appending to

        const updateAssistant = (mutator) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? mutator(m) : m)),
          );
        };

        const ensureTextPart = () => {
          updateAssistant((m) => {
            const last = m.parts[m.parts.length - 1];
            if (last && last.type === 'text' && currentText === m.parts.length - 1) {
              return m;
            }
            currentText = m.parts.length;
            return { ...m, parts: [...m.parts, { type: 'text', text: '' }] };
          });
        };

        const closeTextPart = () => {
          currentText = null;
        };

        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { value, done } = await reader.read();
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
                ensureTextPart();
                const idx = currentText;
                updateAssistant((m) => {
                  const next = m.parts.slice();
                  const part = next[idx];
                  if (part && part.type === 'text') {
                    next[idx] = { ...part, text: part.text + String(payload) };
                  }
                  return { ...m, parts: next };
                });
                break;
              }
              case '2': {
                // server-side data — we use it for { conversation_id }
                const arr = Array.isArray(payload) ? payload : [payload];
                for (const item of arr) {
                  if (item && typeof item.conversation_id === 'number') {
                    onConversationId?.(item.conversation_id);
                  }
                }
                break;
              }
              case 'b': {
                // tool_call_streaming_start
                closeTextPart();
                updateAssistant((m) => ({
                  ...m,
                  parts: [
                    ...m.parts,
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
                  'error' in payload.result &&
                  Object.keys(payload.result).length <= 3;
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
                closeTextPart();
                break;
              }
              case '3': {
                // error
                setError(String(payload));
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
        if (err?.name !== 'AbortError') {
          setError(err?.message || String(err));
        }
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [conversationId, isStreaming, onConversationId],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  return { messages, isStreaming, error, send, stop, reset, clearError, setHistory };
};

export default useChatStream;
