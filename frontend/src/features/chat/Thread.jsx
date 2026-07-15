import React, { Suspense, lazy, useEffect, useRef } from 'react';
import { ChatMessage, ChatMarkdown, ThinkingDots } from '../../shared/chat';
import { MotionList, MotionListItem, motionSafeScrollBehavior } from '../../shared/motion';
import { Button } from '../../shared/ui/TaaliPrimitives';
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
  const databaseMatches = data?.database_matches ?? data?.total_matched;
  if (typeof databaseMatches !== 'number') return null;
  const returned = data.returned ?? data.applications?.length ?? 0;
  const deepChecked = Number(data.deep_checked || 0);
  return (
    <div className={['cp-search-coverage', data.capped ? 'is-capped' : ''].join(' ')}>
      <span>{returned} shown</span>
      <span>{databaseMatches} database matches</span>
      {deepChecked > 0 ? (
        <span>
          {deepChecked} deep-checked{data.capped ? ' · partial verification' : ''}
        </span>
      ) : (
        <span>full database search · no deep verification</span>
      )}
    </div>
  );
};

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
    return (
      <>
        {part.toolName === 'nl_search_candidates' ? (
          <SearchCoverage data={part.result} />
        ) : null}
        {Array.isArray(part.result.applications) ? (
          <CandidateGrid rows={part.result.applications} />
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
const Message = React.memo(({ msg, isStreaming }) => {
  if (msg.role === 'user') {
    const text = msg.parts.find((p) => p.type === 'text')?.text || '';
    return <ChatMessage role="user" text={text} time={msg.createdAt} />;
  }

  const isEmpty = !msg.parts.length;
  return (
    <ChatMessage role="assistant" time={msg.createdAt}>
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
});

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
          <MotionListItem key={m.id} index={i} className="tk-motion-row" layout={false}>
            <Message
              msg={m}
              isStreaming={isStreaming && i === messages.length - 1 && m.role === 'assistant'}
            />
          </MotionListItem>
        ))}
        {fr ? (
          <MotionListItem key="thread-error" className="tk-motion-row" layout={false}>
            <div className="cp-error">
              <div className="cp-error-title">{fr.title}</div>
              <div className="cp-error-detail">{fr.detail}</div>
              {onRetry ? (
                <Button size="xs" variant="secondary" className="cp-error-retry" onClick={onRetry}>
                  Try again
                </Button>
              ) : null}
            </div>
          </MotionListItem>
        ) : null}
      </MotionList>
      <div ref={endRef} />
    </>
  );
};

export default Thread;
