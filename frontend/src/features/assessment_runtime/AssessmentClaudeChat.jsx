import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, FileSearch, MessageSquare, Wrench } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

import { assessments } from '../../shared/api';

const MESSAGE_BUFFER_LIMIT = 30;

// Compact token-count formatter (12000 → "12.0k", 8 → "8"). Used in
// the token-tracker pill so the candidate sees activity without a
// big four-digit number stealing focus.
const formatTokenCount = (n) => {
  const safe = Math.max(0, Number(n) || 0);
  if (safe < 1000) return String(Math.round(safe));
  return `${(safe / 1000).toFixed(safe < 10_000 ? 1 : 0)}k`;
};

const formatCostUsd = (usd) => {
  const safe = Math.max(0, Number(usd) || 0);
  // Always two decimals so the pill width stays stable as the number
  // ticks up; <$0.01 still shows $0.00 (we don't want $0.005, etc.).
  return `$${safe.toFixed(2)}`;
};

// Markdown styling sized like Claude Desktop — meaningfully larger than
// the surrounding UI chrome (which is 85% via the body zoom). Headings,
// lists, and code blocks all get explicit components so prose from
// Claude reads as the primary content instead of cramped sidekick text.
// Sam (2026-06-01): "make the claude chat a bit more bolder, similar
// to claude desktop ... check the text formatting in the claude text."
const MARKDOWN_COMPONENTS = {
  p: ({ children }) => (
    <p className="whitespace-pre-line text-[15px] leading-[1.65] text-[var(--ink)] [&:not(:first-child)]:mt-3">
      {children}
    </p>
  ),
  h1: ({ children }) => (
    <h1 className="mt-4 text-[18px] font-semibold leading-tight text-[var(--ink)] [&:first-child]:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-4 text-[16.5px] font-semibold leading-tight text-[var(--ink)] [&:first-child]:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-3.5 text-[15px] font-semibold leading-tight text-[var(--ink)] [&:first-child]:mt-0">
      {children}
    </h3>
  ),
  ul: ({ children }) => (
    <ul className="mt-3 list-disc space-y-1.5 pl-5 text-[15px] leading-[1.65] text-[var(--ink)] marker:text-[var(--purple)]">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-[15px] leading-[1.65] text-[var(--ink)] marker:text-[var(--purple)]">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1">{children}</li>,
  strong: ({ children }) => (
    <strong className="font-semibold text-[var(--ink)]">{children}</strong>
  ),
  em: ({ children }) => <em className="italic text-[var(--ink)]">{children}</em>,
  blockquote: ({ children }) => (
    <blockquote className="mt-3 border-l-2 border-[var(--purple)] bg-[var(--purple-soft)] px-3 py-2 text-[14.5px] leading-[1.6] text-[var(--ink-2)]">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-t border-[var(--line-2)]" />,
  code: ({ children, className, ...props }) => {
    const isBlock = typeof className === 'string' && className.length > 0;
    if (isBlock) {
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code
        className="rounded-md bg-[var(--purple-soft)] px-1.5 py-0.5 font-mono text-[0.9em] text-[var(--purple-2)]"
        {...props}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="mt-3 overflow-x-auto rounded-[12px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-bg)] p-3.5 font-mono text-[13px] leading-[1.6] text-[var(--ink-2)]">
      {children}
    </pre>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-[var(--purple-2)] underline decoration-[var(--purple)] decoration-dotted underline-offset-[3px] hover:decoration-solid"
    >
      {children}
    </a>
  ),
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
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message.trim()) {
    return detail.message;
  }
  if (typeof err?.message === 'string' && err.message.trim()) return err.message;
  return 'Claude prompt failed.';
};

const MessageRow = ({ entry }) => {
  const isUser = String(entry?.role || '').toLowerCase() === 'user';
  const content = String(entry?.content || '');

  return (
    <div className={`text-[15px] ${isUser ? 'text-right' : ''}`}>
      <div
        className={`mb-1.5 flex gap-2 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)] ${
          isUser ? 'justify-end' : 'justify-start'
        }`}
      >
        <span>{isUser ? 'You' : 'Claude'}</span>
      </div>
      <div
        className={`inline-block max-w-[94%] rounded-[16px] px-5 py-3.5 text-left shadow-sm ${
          isUser
            ? 'rounded-tr-[4px] bg-[var(--purple)] text-white shadow-[0_1px_3px_color-mix(in_oklab,var(--purple)_30%,transparent)]'
            : 'rounded-tl-[4px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] text-[var(--ink)]'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-[15px] leading-[1.6] text-inherit">{content}</p>
        ) : (
          <ReactMarkdown components={MARKDOWN_COMPONENTS}>{content}</ReactMarkdown>
        )}
      </div>
    </div>
  );
};

/**
 * HTTP-based Claude chat for the candidate-facing assessment runtime.
 *
 * Leaf C of the terminal-removal refactor — replaces the WebSocket-on-
 * PTY chat surface with a plain HTTP request/response loop against the
 * agentic backend route at
 *   POST /api/v1/assessments/{id}/claude/chat
 * mounted behind the `__TAALI_AGENTIC_CHAT__` runtime flag.
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
// Critical for interrogative mode (#422): the ``task_opener`` lives in
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
}) => {
  const [messages, setMessages] = useState(() => hydrateMessagesFromAiPrompts(initialAiPrompts));
  const [pending, setPending] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [pasteDetected, setPasteDetected] = useState(false);

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

  const trimmedInput = inputValue.trim();
  const canSend = trimmedInput.length > 0 && !pending && !disabled && !isBudgetExhausted && Boolean(assessmentId) && Boolean(token);

  const handleInputChange = useCallback((event) => {
    const next = event.target.value;
    setInputValue(next);
    if (!String(next || '').trim()) {
      setPasteDetected(false);
    }
  }, []);

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

    setInputValue('');
    setPasteDetected(false);
    setPending(true);
    setMessages((prev) => {
      const next = [...prev, { role: 'user', content: message }];
      return next.slice(-MESSAGE_BUFFER_LIMIT);
    });

    try {
      const res = await assessments.claudeChat(assessmentId, requestPayload, token);
      const payload = res?.data || {};
      const reply = String(payload.content || '').trim() || 'No response from Claude.';

      if (payload.claude_budget && typeof payload.claude_budget === 'object') {
        try {
          onBudgetUpdate?.(payload.claude_budget);
        } catch {
          // Budget callback must never break the chat surface.
        }
      }

      setMessages((prev) => {
        const next = [...prev, { role: 'assistant', content: reply }];
        return next.slice(-MESSAGE_BUFFER_LIMIT);
      });
    } catch (err) {
      const errorText = errorMessageFromException(err);
      setMessages((prev) => {
        const next = [...prev, { role: 'assistant', content: `[Error] ${errorText}` }];
        return next.slice(-MESSAGE_BUFFER_LIMIT);
      });
    } finally {
      setPending(false);
    }
  }, [
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

  const handleKeyDown = useCallback((event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      if (canSend) {
        handleSubmit();
      }
    }
  }, [canSend, handleSubmit]);

  const placeholder = useMemo(() => {
    if (isBudgetExhausted) return 'Claude budget exhausted for this assessment.';
    if (disabled) return 'Claude is unavailable right now.';
    return 'Ask Claude to inspect the repo, explain a failure, or suggest a patch path…';
  }, [disabled, isBudgetExhausted]);

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
      {/* Token tracker. Always visible so the candidate has a persistent
          read on accumulating spend; pulses briefly after each Claude
          response so they SEE the system ticking. Data comes from the
          same claude_budget snapshot the workspace top-bar uses. */}
      <div
        className={`flex items-center justify-between gap-3 border-b border-[var(--taali-runtime-border)] px-5 py-2.5 font-mono text-[11px] transition-colors duration-700 ${
          trackerHighlight
            ? 'bg-[var(--purple-soft)] text-[var(--purple)]'
            : 'text-[var(--mute)]'
        }`}
        data-testid="assessment-claude-chat-token-tracker"
      >
        <div className="flex items-center gap-2">
          <Activity size={12} className={trackerHighlight ? 'animate-pulse' : ''} />
          <span style={{ letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Session
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span data-testid="token-tracker-tokens">
            {formatTokenCount(tokensUsed)} tokens
          </span>
          <span aria-hidden="true">·</span>
          <span data-testid="token-tracker-usd">
            {formatCostUsd(usedUsd)}
          </span>
        </div>
      </div>

      <div
        ref={listRef}
        className="min-h-0 flex-1 overflow-y-auto px-5 py-5"
        data-testid="assessment-claude-chat-messages"
      >
        <div className="space-y-5">
          {messages.length === 0 && !pending ? (
            <div className="rounded-[16px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-5 py-5 text-[15px] leading-[1.6] text-[var(--ink-2)]">
              <div className="mb-3 inline-flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">
                <FileSearch size={14} />
                Claude is ready
              </div>
              Ask Claude to inspect the repo, explain a failure, or suggest the smallest safe patch path before you edit.
            </div>
          ) : null}

          {messages.map((entry, index) => (
            <MessageRow key={`msg-${index}`} entry={entry} />
          ))}

          {pending ? (
            <div className="text-[15px]" data-testid="assessment-claude-chat-pending">
              <div className="mb-1.5 flex gap-2 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">
                <span>Claude</span>
                <span>working</span>
              </div>
              <div className="inline-block max-w-[94%] rounded-[16px] rounded-tl-[4px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-5 py-3.5 text-left">
                <div className="inline-flex items-center gap-2.5 text-[var(--mute)]">
                  <Wrench size={14} className="animate-pulse" />
                  <span className="animate-pulse">Claude is working...</span>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {/* Input box sized like Claude Desktop: ~100px min height so the
          composer is a deliberate surface (not a sidekick line),
          chunky padding, and a prominent send button. Hover-state on
          the container changes the border color to telegraph "drop
          your prompt here." Sam (2026-06-01) wanted the chat to feel
          more robust visually — this is the load-bearing part. */}
      <div className="border-t border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] px-4 py-4">
        <div className="rounded-[18px] border-[1.5px] border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-bg)] px-4 py-3.5 shadow-sm transition-colors focus-within:border-[var(--purple)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklab,var(--purple)_15%,transparent)]">
          <textarea
            value={inputValue}
            onChange={handleInputChange}
            onPaste={handlePaste}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled || pending || isBudgetExhausted}
            aria-label="Message Claude"
            className="min-h-[100px] w-full resize-none border-0 bg-transparent text-[15px] leading-[1.6] text-[var(--ink)] outline-none placeholder:text-[var(--mute)] disabled:opacity-60"
          />
          <div className="mt-3 flex items-center justify-between gap-3">
            <div className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
              Cmd/Ctrl + Enter to send
            </div>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!canSend}
              className="inline-flex items-center gap-2 rounded-full bg-[var(--ink)] px-4 py-2 text-[13px] font-semibold text-[var(--bg)] transition-colors hover:bg-[var(--purple)] disabled:opacity-50"
              aria-label="Send message to Claude"
            >
              <MessageSquare size={14} />
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AssessmentClaudeChat;
