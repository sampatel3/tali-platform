import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, FileSearch } from 'lucide-react';

import { assessments } from '../../shared/api';
import { getErrorMessage } from '../../shared/getErrorMessage';
import { ChatComposer, ChatMarkdown } from '../../shared/chat';
import { MotionLoop } from '../../shared/motion';
import { Spinner } from '../../shared/ui/TaaliPrimitives';

const MESSAGE_BUFFER_LIMIT = 60;

const formatCostUsd = (usd) => {
  const safe = Math.max(0, Number(usd) || 0);
  // Always two decimals so the pill width stays stable as the number
  // ticks up; <$0.01 still shows $0.00 (we don't want $0.005, etc.).
  return `$${safe.toFixed(2)}`;
};

const generateRequestId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID();
    } catch {
      // Fall through to the fallback path on environments where
      // randomUUID throws (older Safari in non-secure contexts, etc.).
    }
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
};

const errorMessageFromException = (err) => {
  // A stalled connection trips the per-request timeout in assessmentsClient
  // (axios raises ECONNABORTED). Keep the candidate-facing "Claude" wording
  // here (assessments say Claude by design) rather than the shared generic
  // timeout line.
  if (err?.code === 'ECONNABORTED' || err?.code === 'ETIMEDOUT') {
    return 'Claude took too long — try again.';
  }
  return getErrorMessage(err, "Your message didn't go through. Check your connection and try again.");
};

const MessageRow = ({ entry }) => {
  const role = String(entry?.role || '').toLowerCase();
  const isUser = role === 'user';
  const isError = role === 'error';
  const content = String(entry?.content || '');

  if (isError) {
    return (
      <div className="text-[0.875rem]">
        <div className="mb-1.5 flex gap-2 font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--taali-danger)]">
          <span>Message not sent</span>
        </div>
        <div className="inline-block max-w-[94%] rounded-[12px] rounded-tl-[4px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-4 py-2.5 text-left">
          <p className="whitespace-pre-wrap text-[0.875rem] leading-[1.55] text-[var(--taali-danger)]">{content}</p>
        </div>
      </div>
    );
  }

  return (
    <div className={`text-[0.875rem] ${isUser ? 'text-right' : ''}`}>
      <div
        className={`mb-1.5 flex gap-2 font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--mute)] ${
          isUser ? 'justify-end' : 'justify-start'
        }`}
      >
        <span>{isUser ? 'You' : 'Claude'}</span>
      </div>
      <div
        className={`inline-block max-w-[94%] rounded-[12px] px-4 py-2.5 text-left ${
          isUser
            ? 'rounded-tr-[4px] bg-[var(--purple)] text-[var(--taali-on-accent)]'
            : 'rounded-tl-[4px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] text-[var(--ink)]'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-[0.875rem] leading-[1.55] text-inherit">{content}</p>
        ) : (
          <ChatMarkdown>{content}</ChatMarkdown>
        )}
      </div>
    </div>
  );
};

/**
 * HTTP-based Claude chat for the candidate-facing assessment runtime.
 *
 * The only candidate-facing assistant surface — a plain HTTP
 * request/response loop against the agentic backend route at
 *   POST /api/v1/assessments/{id}/claude/chat
 * (the legacy WebSocket-on-PTY terminal chat was removed).
 *
 * Props:
 *   - assessmentId: numeric/string assessment id
 *   - token: candidate assessment token (X-Assessment-Token)
 *   - selectedFilePath: optional file path currently open in the editor
 *   - codeContext: optional current editor buffer contents
 *   - claudeBudget: { enabled, is_exhausted, remaining_usd, limit_usd, ... }
 *   - onBudgetUpdate(snapshot): called with `claude_budget` from the response
 *   - disabled: parent-driven disable (timer paused, submitted, etc.)
 */
// Hydrate the chat component's ``messages`` state from existing
// ``assessment.ai_prompts``. Each backend record yields one user
// message (skipped if empty — covers the ``opener`` case where Claude
// asked unprompted at /start) and one assistant message.
//
// Critical for interrogative mode (issue 422): the ``task_opener`` lives in
// ``ai_prompts[0]`` with ``message=""``, ``response=<opener text>``.
// Without this preload the candidate opens chat and sees a blank panel
// instead of Claude's decision questions (assessment 81, 2026-05-26).
const hydrateMessagesFromAiPrompts = (aiPrompts) => {
  if (!Array.isArray(aiPrompts)) return [];
  const out = [];
  for (const entry of aiPrompts) {
    if (!entry || typeof entry !== 'object') continue;
    const userMsg = String(entry.message || '').trim();
    if (userMsg) {
      out.push({ role: 'user', content: userMsg });
    }
    const assistantMsg = String(entry.response || '').trim();
    if (assistantMsg) {
      out.push({ role: 'assistant', content: assistantMsg });
    }
  }
  return out;
};

export const AssessmentClaudeChat = ({
  assessmentId,
  token,
  selectedFilePath,
  codeContext,
  claudeBudget,
  onBudgetUpdate,
  disabled = false,
  initialAiPrompts = null,
  // Read-only demo mode: the transcript is pre-seeded and sending is fully
  // disabled (no backend assessment behind it). The candidate-facing live
  // runtime never sets this.
  locked = false,
}) => {
  const [messages, setMessages] = useState(() => hydrateMessagesFromAiPrompts(initialAiPrompts));
  const [pending, setPending] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [pasteDetected, setPasteDetected] = useState(false);
  // Flips true the first time the transcript overflows MESSAGE_BUFFER_LIMIT
  // and older turns are dropped, so we can tell the candidate their earlier
  // history was trimmed rather than silently losing it.
  const [historyTrimmed, setHistoryTrimmed] = useState(false);

  // Append a row and keep only the last MESSAGE_BUFFER_LIMIT turns. Flags
  // historyTrimmed whenever the append actually drops an older message.
  const appendMessage = useCallback((row) => {
    setMessages((prev) => {
      const next = [...prev, row];
      if (next.length > MESSAGE_BUFFER_LIMIT) {
        setHistoryTrimmed(true);
        return next.slice(-MESSAGE_BUFFER_LIMIT);
      }
      return next;
    });
  }, []);

  const lastPromptAtRef = useRef(null);

  const isBudgetExhausted = Boolean(claudeBudget?.enabled && claudeBudget?.is_exhausted);

  // Token tracker: persistent pill showing tokens used + USD spent.
  // Highlights briefly when the value ticks up so the candidate sees
  // confirmation that their message landed and was costed. Sam wanted
  // this as a "shows the system is working" signal — same data the
  // workspace top-bar budget chip uses, just framed as accumulation
  // (used) rather than depletion (remaining).
  const tokensUsed = Number(claudeBudget?.tokens_used || 0);
  const usedUsd = Number(claudeBudget?.used_usd || 0);
  const [trackerHighlight, setTrackerHighlight] = useState(false);
  const prevTokensRef = useRef(tokensUsed);
  useEffect(() => {
    if (tokensUsed > prevTokensRef.current) {
      setTrackerHighlight(true);
      const t = setTimeout(() => setTrackerHighlight(false), 900);
      prevTokensRef.current = tokensUsed;
      return () => clearTimeout(t);
    }
    prevTokensRef.current = tokensUsed;
    return undefined;
  }, [tokensUsed]);

  // Live "working" status line — mirrors Claude Code's indicator (elapsed time
  // ticking + a token count) instead of a static "Claude is working". The
  // seconds tick is the moment-to-moment "it's alive" signal while we wait on
  // the turn; the token count is the session total (it jumps when the turn
  // lands — the per-turn usage isn't streamed on this path).
  const [elapsedSec, setElapsedSec] = useState(0);
  useEffect(() => {
    if (!pending) {
      setElapsedSec(0);
      return undefined;
    }
    const startedAt = Date.now();
    setElapsedSec(0);
    const id = setInterval(() => {
      setElapsedSec(Math.max(0, Math.round((Date.now() - startedAt) / 1000)));
    }, 1000);
    return () => clearInterval(id);
  }, [pending]);

  const handlePaste = useCallback(() => {
    setPasteDetected(true);
  }, []);

  const handleSubmit = useCallback(async () => {
    const message = inputValue.trim();
    if (!message || pending || disabled || isBudgetExhausted) return;
    if (!assessmentId || !token) return;

    const nowMs = Date.now();
    const timeSinceLastPromptMs = lastPromptAtRef.current != null
      ? Math.max(0, nowMs - lastPromptAtRef.current)
      : null;
    lastPromptAtRef.current = nowMs;

    const requestPayload = {
      message,
      code_context: codeContext || null,
      selected_file_path: selectedFilePath || null,
      paste_detected: pasteDetected,
      browser_focused: typeof document !== 'undefined' ? document.visibilityState === 'visible' : true,
      time_since_last_prompt_ms: timeSinceLastPromptMs,
      request_id: generateRequestId(),
    };

    // Remember the paste signal for this send so a failure can restore the
    // exact same anti-cheat context when the candidate retries.
    const pasteDetectedForSend = pasteDetected;
    setInputValue('');
    setPasteDetected(false);
    setPending(true);
    appendMessage({ role: 'user', content: message });

    try {
      const res = await assessments.claudeChat(assessmentId, requestPayload, token);
      const payload = res?.data || {};
      const reply = String(payload.content || '').trim() || 'No response — try asking again.';

      if (payload.claude_budget && typeof payload.claude_budget === 'object') {
        try {
          onBudgetUpdate?.(payload.claude_budget);
        } catch {
          // Budget callback must never break the chat surface.
        }
      }

      appendMessage({ role: 'assistant', content: reply });
    } catch (err) {
      const errorText = errorMessageFromException(err);
      appendMessage({ role: 'error', content: errorText });
      // Restore the message into the composer so the candidate can retry
      // without retyping. This is a programmatic set (not an onPaste), so it
      // does NOT retrip paste detection — we re-apply the original paste flag
      // explicitly so the retry carries the same signal.
      setInputValue((current) => (current.trim() ? current : message));
      setPasteDetected(pasteDetectedForSend);
    } finally {
      setPending(false);
    }
  }, [
    appendMessage,
    assessmentId,
    codeContext,
    disabled,
    inputValue,
    isBudgetExhausted,
    onBudgetUpdate,
    pasteDetected,
    pending,
    selectedFilePath,
    token,
  ]);


  const placeholder = useMemo(() => {
    if (locked) return 'Read-only demo — this transcript is from a real candidate session. Book a demo to try it live.';
    if (isBudgetExhausted) return 'Your Claude budget for this assessment is used up.';
    if (disabled) return 'Claude is unavailable right now.';
    return 'Ask Claude to inspect the repo, explain a failure, or suggest a patch path…';
  }, [disabled, isBudgetExhausted, locked]);

  // Auto-scroll the message list as new turns arrive.
  const listRef = useRef(null);
  useEffect(() => {
    const el = listRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, pending]);

  return (
    <div
      className="flex h-full min-h-0 flex-col rounded-[var(--radius-lg)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)]"
      data-testid="assessment-claude-chat"
    >
      {/* Activity tracker. Always visible so the candidate has a persistent
          read on accumulating spend; pulses briefly after each response so
          they SEE the system ticking. Data comes from the same claude_budget
          snapshot the workspace top-bar uses. */}
      <div
        className={`flex items-center justify-between gap-3 border-b border-[var(--taali-runtime-border)] px-5 py-2.5 font-mono text-[0.6875rem] transition-colors duration-700 ${
          trackerHighlight
            ? 'bg-[var(--purple-soft)] text-[var(--purple)]'
            : 'text-[var(--mute)]'
        }`}
        data-testid="assessment-claude-chat-activity-tracker"
      >
        <div className="flex items-center gap-2">
          <MotionLoop active={trackerHighlight} kind="pulse" className="inline-flex">
            <Activity size={12} />
          </MotionLoop>
          <span style={{ letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Session
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span data-testid="activity-tracker-usd">
            {formatCostUsd(usedUsd)}
          </span>
        </div>
      </div>

      <div
        ref={listRef}
        className="min-h-0 flex-1 overflow-y-auto px-5 py-5"
        data-testid="assessment-claude-chat-messages"
      >
        <div className="space-y-3">
          {historyTrimmed ? (
            <div
              className="text-center font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--mute)]"
              data-testid="assessment-claude-chat-history-trimmed"
            >
              Older messages are hidden
            </div>
          ) : null}

          {messages.length === 0 && !pending ? (
            <div className="rounded-[12px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-4 py-3.5 text-[0.84375rem] leading-[1.55] text-[var(--ink-2)]">
              <div className="mb-2 flex items-center gap-2 font-mono text-[0.65625rem] uppercase tracking-[0.1em] text-[var(--purple)]">
                <FileSearch size={12} />
                Claude is ready
              </div>
              Ask Claude to inspect the repo, explain a failure, or suggest the smallest safe patch path before you edit.
            </div>
          ) : null}

          {messages.map((entry, index) => (
            <MessageRow key={`msg-${index}`} entry={entry} />
          ))}

          {pending ? (
            <div className="text-[0.875rem]" data-testid="assessment-claude-chat-pending">
              <div className="mb-1.5 flex gap-2 font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--mute)]">
                <span>Claude</span>
                <span>working</span>
              </div>
              <div className="inline-block max-w-[94%] rounded-[12px] rounded-tl-[4px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-4 py-2.5 text-left">
                <div className="inline-flex items-center gap-2 font-mono text-[0.8125rem] text-[var(--mute)]">
                  <Spinner size={13} className="!text-current" />
                  <span>Working</span>
                  <span aria-hidden="true">·</span>
                  <span
                    data-testid="assessment-claude-chat-pending-elapsed"
                    className="tabular-nums"
                  >
                    {elapsedSec}s
                  </span>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {/* Input box — the shared chat composer (⌘/Ctrl+Enter to send so a plain
          Enter is a newline; candidates often write multi-line prompts). Paste
          detection (the anti-cheat signal) is preserved via onPaste. */}
      <div className="border-t border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] px-3 py-3">
        <ChatComposer
          value={inputValue}
          onChange={(v) => {
            setInputValue(v);
            if (!String(v || '').trim()) setPasteDetected(false);
          }}
          onSubmit={() => handleSubmit()}
          onPaste={handlePaste}
          placeholder={placeholder}
          submitMode="cmd"
          busy={disabled || pending || isBudgetExhausted || locked || !assessmentId || !token}
        />
      </div>
    </div>
  );
};

export default AssessmentClaudeChat;
