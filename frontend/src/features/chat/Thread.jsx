import React, { useEffect, useRef } from 'react';
import { ChatMessage, ChatMarkdown, ThinkingDots } from '../../shared/chat';
import ToolCallCard from './ToolCallCard';
import CandidateGrid from './CandidateGrid';
import ComparisonTable from './ComparisonTable';
import GraphView from './GraphView';
import CandidateEvidenceCard from './CandidateEvidenceCard';

const ToolResultRender = ({ part }) => {
  // Decide which custom renderer(s) to show for this tool's payload. A
  // graph_search_candidates result can render BOTH a candidate grid (the
  // hydrated applications) and an inline graph (the underlying nodes +
  // edges from Graphiti) — they're complementary views of the same hit.
  if (!part.result) return null;
  if (
    part.toolName === 'find_top_candidates' ||
    part.toolName === 'screen_pool_against_requirement'
  ) {
    return <CandidateEvidenceCard data={part.result} />;
  }
  if (part.toolName === 'compare_applications') {
    return <ComparisonTable payload={part.result} />;
  }
  if (part.toolName === 'search_applications') {
    if (Array.isArray(part.result)) return <CandidateGrid rows={part.result} />;
  }
  // Both search tools share the same payload shape: ``applications`` (the
  // candidate grid) plus an optional ``graph`` (the inline subgraph from
  // Graphiti). nl_search_candidates returns the matched candidates'
  // subgraph; graph_search_candidates returns the query-anchored
  // subgraph. Render the same way for both.
  if (
    part.toolName === 'nl_search_candidates' ||
    part.toolName === 'graph_search_candidates'
  ) {
    return (
      <>
        {Array.isArray(part.result.applications) ? (
          <CandidateGrid rows={part.result.applications} />
        ) : null}
        {part.result.graph ? <GraphView graph={part.result.graph} /> : null}
      </>
    );
  }
  return null;
};

const Message = ({ msg, isStreaming }) => {
  if (msg.role === 'user') {
    const text = msg.parts.find((p) => p.type === 'text')?.text || '';
    return <ChatMessage role="user" text={text} />;
  }

  const isEmpty = !msg.parts.length;
  return (
    <ChatMessage role="assistant">
      {/* search-preview tags each assistant turn with a mono "TAALI" kicker. */}
      <div className="cp-who">Taali</div>
      {isEmpty && isStreaming ? <ThinkingDots label="thinking…" /> : null}
      {msg.parts.map((part, idx) => {
        if (part.type === 'text') {
          if (!part.text) return null;
          return <ChatMarkdown key={idx}>{part.text}</ChatMarkdown>;
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
    </ChatMessage>
  );
};

const friendlyError = (raw) => {
  if (!raw) return null;
  const text = String(raw);
  // Anthropic credit-balance errors are common during dev — surface a
  // pointed message so the user knows where to go.
  if (/credit balance is too low/i.test(text) || /insufficient_quota/i.test(text)) {
    return {
      title: "Taali's AI is unavailable",
      detail:
        "The AI service can't take requests right now. Try again shortly — contact support if it keeps happening.",
    };
  }
  if (/invalid_api_key|authentication_error/i.test(text)) {
    return {
      title: "Couldn't reach Taali's AI",
      detail:
        "There's a configuration problem with AI access for your workspace. Contact support.",
    };
  }
  if (/rate_limit|429/i.test(text)) {
    return {
      title: "Too many requests",
      detail: "Wait a moment and try again.",
    };
  }
  return { title: "Something went wrong", detail: "Please try again. If it keeps happening, contact support." };
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
