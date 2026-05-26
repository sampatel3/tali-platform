import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FileSearch, MessageSquare, Wrench } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

import { assessments } from '../../shared/api';

const MESSAGE_BUFFER_LIMIT = 30;

// Tool-name -> small label/icon for the inline chip strip. Keeps the
// surface decorative without leaking raw tool internals to the
// candidate.
const TOOL_ICONS = {
  read_file: { icon: '📂', label: 'read_file' },
  read_many_files: { icon: '📂', label: 'read_files' },
  list_dir: { icon: '📁', label: 'list_dir' },
  glob_search: { icon: '🔎', label: 'glob_search' },
  grep_search: { icon: '🔎', label: 'grep_search' },
  search_files: { icon: '🔎', label: 'search_files' },
  run_command: { icon: '⚙️', label: 'run_command' },
  open_file: { icon: '📂', label: 'open_file' },
};

const MARKDOWN_COMPONENTS = {
  p: ({ children }) => (
    <p className="whitespace-pre-line text-[13.5px] leading-6 text-[var(--ink-2)] [&:not(:first-child)]:mt-3">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="mt-3 list-disc space-y-2 pl-5 text-[13.5px] leading-6 text-[var(--ink-2)]">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-3 list-decimal space-y-2 pl-5 text-[13.5px] leading-6 text-[var(--ink-2)]">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1 marker:text-[var(--purple)]">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-[var(--ink)]">{children}</strong>,
  em: ({ children }) => <em className="italic text-[var(--ink-2)]">{children}</em>,
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
      <code className="rounded-md bg-[var(--purple-soft)] px-1.5 py-0.5 font-mono text-[0.88em] text-[var(--purple-2)]" {...props}>
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="mt-3 overflow-x-auto rounded-[12px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-bg)] p-3 font-mono text-[12px] leading-6 text-[var(--ink-2)]">
      {children}
    </pre>
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

const summarizeToolCall = (call) => {
  if (!call || typeof call !== 'object') return null;
  const name = String(call.name || '').trim();
  if (!name) return null;
  const meta = TOOL_ICONS[name] || { icon: '🛠', label: name };
  const input = call.input && typeof call.input === 'object' ? call.input : {};
  const target = String(input.path || input.target || input.query || input.command || '').trim();
  return {
    icon: meta.icon,
    label: meta.label,
    target,
    ok: call.result_ok !== false,
  };
};

const ToolCallChip = ({ call }) => {
  const summary = summarizeToolCall(call);
  if (!summary) return null;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[10.5px] uppercase tracking-[0.06em] ${
        summary.ok
          ? 'border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-muted)] text-[var(--mute)]'
          : 'border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] text-[var(--red)]'
      }`}
    >
      <span aria-hidden="true">{summary.icon}</span>
      <span className="text-[var(--ink-2)]">{summary.label}</span>
      {summary.target ? (
        <span className="max-w-[160px] truncate normal-case tracking-normal text-[var(--mute)]">
          {summary.target}
        </span>
      ) : null}
    </span>
  );
};

const MessageRow = ({ entry }) => {
  const isUser = String(entry?.role || '').toLowerCase() === 'user';
  const content = String(entry?.content || '');
  const toolCalls = Array.isArray(entry?.toolCalls) ? entry.toolCalls : [];

  return (
    <div className={`text-[13.5px] ${isUser ? 'text-right' : ''}`}>
      <div
        className={`mb-2 flex gap-2 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)] ${
          isUser ? 'justify-end' : 'justify-start'
        }`}
      >
        <span>{isUser ? 'You' : 'Claude'}</span>
      </div>
      <div
        className={`inline-block max-w-[92%] rounded-[14px] px-4 py-3 text-left ${
          isUser
            ? 'rounded-tr-[4px] bg-[var(--purple)] text-white'
            : 'rounded-tl-[4px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] text-[var(--ink-2)]'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap leading-6 text-inherit">{content}</p>
        ) : (
          <ReactMarkdown components={MARKDOWN_COMPONENTS}>{content}</ReactMarkdown>
        )}
      </div>
      {!isUser && toolCalls.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {toolCalls.map((call, idx) => (
            <ToolCallChip key={`tool-${idx}-${call?.name || 'unknown'}`} call={call} />
          ))}
        </div>
      ) : null}
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
export const AssessmentClaudeChat = ({
  assessmentId,
  token,
  selectedFilePath,
  codeContext,
  claudeBudget,
  onBudgetUpdate,
  disabled = false,
}) => {
  const [messages, setMessages] = useState([]);
  const [pending, setPending] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [pasteDetected, setPasteDetected] = useState(false);

  const lastPromptAtRef = useRef(null);

  const isBudgetExhausted = Boolean(claudeBudget?.enabled && claudeBudget?.is_exhausted);

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
      const toolCalls = Array.isArray(payload.tool_calls_made) ? payload.tool_calls_made : [];

      if (payload.claude_budget && typeof payload.claude_budget === 'object') {
        try {
          onBudgetUpdate?.(payload.claude_budget);
        } catch {
          // Budget callback must never break the chat surface.
        }
      }

      setMessages((prev) => {
        const next = [...prev, { role: 'assistant', content: reply, toolCalls }];
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
      <div
        ref={listRef}
        className="min-h-0 flex-1 overflow-y-auto px-5 py-5"
        data-testid="assessment-claude-chat-messages"
      >
        <div className="space-y-4">
          {messages.length === 0 && !pending ? (
            <div className="rounded-[14px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-4 py-4 text-[13px] leading-6 text-[var(--mute)]">
              <div className="mb-2 inline-flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.08em] text-[var(--purple)]">
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
            <div className="text-[13.5px]" data-testid="assessment-claude-chat-pending">
              <div className="mb-2 flex gap-2 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
                <span>Claude</span>
                <span>working</span>
              </div>
              <div className="inline-block max-w-[92%] rounded-[14px] rounded-tl-[4px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-4 py-3 text-left">
                <div className="inline-flex items-center gap-2 text-[var(--mute)]">
                  <Wrench size={12} className="animate-pulse" />
                  <span className="animate-pulse">Claude is working...</span>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="border-t border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] px-4 py-4">
        <div className="rounded-[14px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-bg)] px-3 py-3 transition-colors focus-within:border-[var(--purple)]">
          <textarea
            value={inputValue}
            onChange={handleInputChange}
            onPaste={handlePaste}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled || pending || isBudgetExhausted}
            aria-label="Message Claude"
            className="min-h-[64px] w-full resize-none border-0 bg-transparent text-[13.5px] leading-6 text-[var(--ink)] outline-none placeholder:text-[var(--mute)] disabled:opacity-60"
          />
          <div className="mt-2 flex items-center justify-between gap-3">
            <div className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-[var(--mute)]">
              Cmd/Ctrl + Enter to send
            </div>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!canSend}
              className="inline-flex items-center gap-2 rounded-full bg-[var(--ink)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--bg)] transition-colors hover:bg-[var(--purple)] disabled:opacity-50"
              aria-label="Send message to Claude"
            >
              <MessageSquare size={13} />
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AssessmentClaudeChat;
