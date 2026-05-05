import React, { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import ToolCallCard from './ToolCallCard';
import CandidateGrid from './CandidateGrid';
import ComparisonTable from './ComparisonTable';

const ToolResultRender = ({ part }) => {
  // Decide which custom renderer to show for this tool's payload.
  if (!part.result) return null;
  if (part.toolName === 'compare_applications') {
    return <ComparisonTable payload={part.result} />;
  }
  if (part.toolName === 'search_applications') {
    if (Array.isArray(part.result)) return <CandidateGrid rows={part.result} />;
  }
  if (
    part.toolName === 'nl_search_candidates' ||
    part.toolName === 'graph_search_candidates'
  ) {
    if (Array.isArray(part.result.applications)) {
      return <CandidateGrid rows={part.result.applications} />;
    }
  }
  return null;
};

const Message = ({ msg, isStreaming }) => {
  if (msg.role === 'user') {
    const text = msg.parts.find((p) => p.type === 'text')?.text || '';
    return <div className="cp-msg-user">{text}</div>;
  }

  const isEmpty = !msg.parts.length;
  return (
    <div className="cp-msg-assistant">
      {isEmpty && isStreaming ? (
        <div className="cp-thinking">
          <span className="cp-dots">
            <span /><span /><span />
          </span>
          thinking…
        </div>
      ) : null}
      {msg.parts.map((part, idx) => {
        if (part.type === 'text') {
          if (!part.text) return null;
          return (
            <div key={idx}>
              <ReactMarkdown>{part.text}</ReactMarkdown>
            </div>
          );
        }
        if (part.type === 'tool_call') {
          return (
            <React.Fragment key={part.toolCallId || idx}>
              <ToolCallCard part={part} />
              <ToolResultRender part={part} />
            </React.Fragment>
          );
        }
        return null;
      })}
    </div>
  );
};

const friendlyError = (raw) => {
  if (!raw) return null;
  const text = String(raw);
  // Anthropic credit-balance errors are common during dev — surface a
  // pointed message so the user knows where to go.
  if (/credit balance is too low/i.test(text) || /insufficient_quota/i.test(text)) {
    return {
      title: "Anthropic credit balance is empty",
      detail:
        "The chat backend hit Anthropic's API but the org's workspace has no credits. " +
        "Top up at https://console.anthropic.com → Plans & Billing, then try again.",
    };
  }
  if (/invalid_api_key|authentication_error/i.test(text)) {
    return {
      title: "Anthropic auth failed",
      detail:
        "The org's workspace API key was rejected. Re-provision via the Anthropic Console.",
    };
  }
  if (/rate_limit|429/i.test(text)) {
    return {
      title: "Rate limited by Anthropic",
      detail: "Wait a moment and retry. Heavy usage triggers a short cool-down.",
    };
  }
  return { title: "Something went wrong", detail: text };
};

const Thread = ({ messages, isStreaming, error }) => {
  const endRef = useRef(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, isStreaming]);

  const fr = friendlyError(error);

  return (
    <div className="cp-thread">
      {messages.map((m, i) => (
        <Message
          key={m.id}
          msg={m}
          isStreaming={isStreaming && i === messages.length - 1 && m.role === 'assistant'}
        />
      ))}
      {fr ? (
        <div className="cp-error">
          <div className="cp-error-title">{fr.title}</div>
          <div className="cp-error-detail">{fr.detail}</div>
        </div>
      ) : null}
      <div ref={endRef} />
    </div>
  );
};

export default Thread;
