import React, { Suspense, lazy, useEffect, useRef } from 'react';
import { CircleAlert, Database } from 'lucide-react';

import { ChatActivity, ChatMessage, ChatMarkdown, ThinkingDots } from '../../shared/chat';
import {
  MotionChatItem,
  MotionDisclosure,
  MotionList,
  motionSafeScrollBehavior,
} from '../../shared/motion';
import ToolCallCard from './ToolCallCard';
import CandidateGrid from './CandidateGrid';
import ComparisonTable from './ComparisonTable';
import CandidateEvidenceCard from './CandidateEvidenceCard';
import { AssessmentQueueCard, RecruitingOverviewCard } from './OperationsCards';

// GraphView pulls in cytoscape (~455 kB) — lazy-load it so the ~455 kB
// graph vendor chunk only lands when a tool result actually carries a
// ``graph`` payload, instead of riding the chat path for every session.
const GraphView = lazy(() => import('./GraphView'));

export const SearchCoverage = ({ data }) => {
  const totalMatches = data?.total_matched ?? data?.retrieval_matches ?? data?.database_matches;
  if (typeof totalMatches !== 'number') return null;
  const postgresMatches = data?.database_matches;
  const returned = data.returned ?? data.applications?.length ?? 0;
  const deepChecked = Number(data.deep_checked || 0);
  const hasEvidenceSplit =
    typeof data.evidence_succeeded === 'number'
    || typeof data.evidence_failed === 'number';
  const evidenceSucceeded = Number(data.evidence_succeeded || 0);
  const evidenceFailed = Number(data.evidence_failed || 0);
  const warnings = Array.isArray(data.warnings)
    ? data.warnings
      .map((warning) => (typeof warning === 'string' ? warning : warning?.message))
      .filter(Boolean)
    : [];
  const isPartial = Boolean(
    data.capped || data.exhaustive === false || evidenceFailed > 0 || warnings.length
  );
  return (
    <ChatActivity
      severity={isPartial ? 'warning' : 'info'}
      severityLabel={isPartial ? 'Partial' : 'Covered'}
      typeLabel="Search coverage"
      title={`${returned} shown`}
      icon={Database}
      summary={(
        <span className="cp-search-coverage-summary">
          <span>
            {totalMatches} retrieval {totalMatches === 1 ? 'match' : 'matches'}
            {typeof postgresMatches === 'number' && postgresMatches !== totalMatches
              ? ` · ${postgresMatches} PostgreSQL`
              : ''}
          </span>
          {deepChecked > 0 ? (
            <span>
              {deepChecked} deep-checked{data.capped ? ' · partial verification' : ''}
            </span>
          ) : (
            <span>
              {data.exhaustive === false ? 'partial retrieval' : 'complete retrieval'}
              {' · no deep verification'}
            </span>
          )}
          {hasEvidenceSplit ? (
            <span>
              {evidenceSucceeded} evidence {evidenceSucceeded === 1 ? 'check' : 'checks'} completed
              {evidenceFailed > 0 ? ` · ${evidenceFailed} failed` : ''}
            </span>
          ) : null}
          {warnings.map((warning, index) => (
            <span className="cp-search-coverage-warning" key={`${warning}-${index}`}>
              {warning}
            </span>
          ))}
        </span>
      )}
      aria-label={`${isPartial ? 'Partial' : 'Complete'} candidate search coverage`}
    />
  );
};

export const ToolResultRender = ({ part }) => {
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
  if (part.toolName === 'get_recruiting_overview') {
    return <RecruitingOverviewCard data={part.result} />;
  }
  if (part.toolName === 'list_assessments') {
    return <AssessmentQueueCard data={part.result} />;
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
    const completedVerification = (
      part.result.exhaustive === true
      && part.result.capped !== true
      && Number(part.result.deep_checked || 0) > 0
      && Number(part.result.evidence_succeeded || 0)
        === Number(part.result.deep_checked || 0)
      && Number(part.result.evidence_failed || 0) === 0
      && Number(part.result.qualified || 0) === 0
    );
    let emptyMessage;
    if (completedVerification) {
      emptyMessage = {
        title: 'No candidates met the verified requirements',
        summary: 'Every retrieved candidate was checked against the requested evidence.',
      };
    } else if (part.result.is_exact_empty !== true) {
      emptyMessage = {
        title: 'No candidates retrieved',
        summary: 'Search coverage is partial, so this is not a confirmed zero.',
      };
    }
    return (
      <>
        <SearchCoverage data={part.result} />
        {Array.isArray(part.result.applications) ? (
          <CandidateGrid
            rows={part.result.applications}
            emptyMessage={emptyMessage}
          />
        ) : null}
        {part.result.graph ? (
          <Suspense fallback={null}>
            <GraphView graph={part.result.graph} />
          </Suspense>
        ) : null}
      </>
    );
  }
  return null;
};

// Memoized per message: useChatStream replaces the streaming message's
// object immutably on every SSE delta while every other message keeps its
// reference, so React.memo re-renders only the one message being streamed
// instead of re-parsing every ChatMarkdown in the thread on each token.
export const Message = React.memo(({ msg, isStreaming }) => {
  if (msg.role === 'user') {
    const text = msg.parts.find((p) => p.type === 'text')?.text || '';
    return <ChatMessage role="user" text={text} time={msg.createdAt} />;
  }

  const isEmpty = !msg.parts.length;
  return (
    <ChatMessage role="assistant" time={msg.createdAt} label="Taali">
      {isEmpty && isStreaming ? <ThinkingDots label="thinking…" /> : null}
      {msg.parts.map((part, idx) => {
        if (part.type === 'text') {
          if (!part.text) return null;
          return <ChatMarkdown key={idx}>{part.text}</ChatMarkdown>;
        }
        if (part.type === 'progress') {
          return <ThinkingDots key={idx} label={part.label} />;
        }
        if (part.type === 'tool_call') {
          return (
            <React.Fragment key={part.toolCallId || idx}>
              <ToolCallCard part={part} />
              <MotionDisclosure open={part.result != null} className="cp-tool-result">
                <ToolResultRender part={part} />
              </MotionDisclosure>
            </React.Fragment>
          );
        }
        return null;
      })}
    </ChatMessage>
  );
});

export const friendlyError = (raw) => {
  if (!raw) return null;
  const text = String(raw);
  if (/out of AI credits|does not have enough credits/i.test(text)) {
    return {
      title: 'AI credits needed',
      detail: 'Add credits in Settings → Billing to continue using Chat.',
      retryable: false,
    };
  }
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

// Nearest scrollable ancestor of the thread (``.cp-scroll``). We autoscroll
// only when the user is already pinned near the bottom — a recruiter who
// scrolls up to re-read an evidence card mid-stream keeps their position
// instead of being yanked back down on every SSE delta.
const NEAR_BOTTOM_PX = 80;
const scrollParentOf = (el) => {
  let node = el?.parentElement;
  while (node) {
    const oy = getComputedStyle(node).overflowY;
    if (oy === 'auto' || oy === 'scroll') return node;
    node = node.parentElement;
  }
  return null;
};

const Thread = ({ messages, isStreaming, error, onRetry }) => {
  const endRef = useRef(null);
  // Whether the user was pinned to the bottom *before* this render grew the
  // thread. Tracked on scroll so a mid-stream scroll-up is respected while
  // someone sitting at the bottom keeps following the answer as it streams.
  const pinnedRef = useRef(true);
  useEffect(() => {
    const scroller = scrollParentOf(endRef.current);
    if (!scroller) return undefined;
    const onScroll = () => {
      pinnedRef.current =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <
        NEAR_BOTTOM_PX;
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => scroller.removeEventListener('scroll', onScroll);
  }, []);
  useEffect(() => {
    if (pinnedRef.current) {
      endRef.current?.scrollIntoView({ behavior: motionSafeScrollBehavior('smooth'), block: 'end' });
    }
  }, [messages, isStreaming]);

  const fr = friendlyError(error);

  return (
    <>
      <MotionList className="cp-thread" aria-label="Search conversation" layout={false}>
        {messages.map((m, i) => (
          <MotionChatItem key={m.id} className="tk-motion-row">
            <Message
              msg={m}
              isStreaming={isStreaming && i === messages.length - 1 && m.role === 'assistant'}
            />
          </MotionChatItem>
        ))}
        {fr ? (
          <MotionChatItem key="thread-error" className="tk-motion-row">
            <ChatActivity
              role="alert"
              severity="error"
              severityLabel="Error"
              typeLabel="Conversation"
              title={fr.title}
              summary={fr.detail}
              icon={CircleAlert}
              actions={onRetry && fr.retryable !== false ? [{ label: 'Try again', onClick: onRetry }] : []}
            />
          </MotionChatItem>
        ) : null}
      </MotionList>
      <div ref={endRef} />
    </>
  );
};

export default Thread;
