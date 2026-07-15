import React, { useEffect, useRef, useState } from 'react';

import { AgentDecisionTimelineCard } from '../decisions/AgentDecisionTimelineCard';
import { MotionChatItem, MotionList } from '../motion';
import { AgentPromptCard } from './AgentPromptCard';
import { ChatMessage } from './ChatMessage';

const suppressStructuredMessageCopy = (item) => (
  (item?.message_kind === 'proactive'
    && (item.actions || []).some((card) => card.type === 'helper_prompt'))
  || (item?.message_kind === 'event'
    && (item.actions || []).some((card) => card.type === 'agent_event'))
);

const timelineItemIdentity = (item, index) => {
  const kind = item?.kind || 'item';
  const id = item?.id ?? item?.needs_input_id ?? item?.decision_id ?? item?.created_at ?? index;
  return `${kind}:${String(id)}`;
};

function IncomingAssistantAnnouncement({ items, scopeKey, agentLabel, threshold = 80 }) {
  const regionRef = useRef(null);
  const pinnedRef = useRef(true);
  const initializedRef = useRef(false);
  const knownIdsRef = useRef(new Set());
  const scopeRef = useRef(scopeKey);
  const [announcement, setAnnouncement] = useState(null);

  // The live region sits directly after the transcript in its scroll viewport.
  // Snapshot whether the reader was following the bottom before a new row
  // changes scrollHeight; readers browsing history use NewMessageNotice instead.
  useEffect(() => {
    const scroller = regionRef.current?.parentElement;
    if (!scroller) return undefined;
    const updatePinned = () => {
      const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      pinnedRef.current = distance <= threshold;
    };
    updatePinned();
    scroller.addEventListener('scroll', updatePinned, { passive: true });
    return () => scroller.removeEventListener('scroll', updatePinned);
  }, [scopeKey, threshold]);

  useEffect(() => {
    const nextIds = new Set(items.map(timelineItemIdentity));
    if (!initializedRef.current || scopeRef.current !== scopeKey) {
      initializedRef.current = true;
      scopeRef.current = scopeKey;
      knownIdsRef.current = nextIds;
      setAnnouncement(null);
      return;
    }

    const previousIds = knownIdsRef.current;
    const incoming = items.filter((item, index) => (
      !previousIds.has(timelineItemIdentity(item, index))
      && item?.kind === 'message'
      && item?.author === 'agent'
    ));
    knownIdsRef.current = nextIds;
    if (!incoming.length || !pinnedRef.current) return;

    const latest = incoming[incoming.length - 1];
    const text = String(latest.text || '').trim();
    setAnnouncement({
      key: timelineItemIdentity(latest, items.indexOf(latest)),
      text: text ? `${agentLabel}: ${text}` : `${agentLabel} sent a new update.`,
    });
  }, [agentLabel, items, scopeKey]);

  return (
    <span
      ref={regionRef}
      className="tk-chat-live-announcement"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      aria-relevant="additions text"
    >
      {announcement ? <span key={announcement.key}>{announcement.text}</span> : null}
    </span>
  );
}

/**
 * Pure renderer for a role agent's chronological thread. Home's compact dock
 * and Chat > Agents own their fetching, polling, empty states, and composers,
 * but share this mapping so messages, recruiter questions, and HITL decisions
 * cannot drift between the two surfaces.
 *
 * Domain-specific action cards stay injected through `renderAction`; this
 * keeps the shared renderer independent of either feature directory while the
 * card implementations complete their own shared-chat migration.
 */
export function RoleAgentTimeline({
  items = [],
  className,
  label = 'Agent conversation',
  roleId,
  roleName,
  openQuestionPositions = new Map(),
  openQuestionCount = 0,
  onAnswer,
  onDismiss,
  onPrompt,
  onReply,
  decisionDetails = {},
  decisionDetailsLoading = false,
  decisionDetailsError = false,
  onRetryDecisionDetails,
  onDecisionChanged,
  renderAction,
  agentLabel = 'Agent',
  before = null,
  after = null,
}) {
  return (
    <>
      <MotionList className={className} aria-label={label} layout={false}>
        {before}
        {items.map((item) => {
        let content;

        if (item.kind === 'needs_input') {
          const requestId = item.needs_input_id ?? item.id;
          content = (
            <AgentPromptCard
              item={item}
              onAnswer={onAnswer}
              onDismiss={onDismiss}
              onPrompt={onPrompt}
              onReply={onReply}
              position={openQuestionPositions.get(requestId)}
              total={openQuestionCount}
            />
          );
        } else if (item.kind === 'decision') {
          const decisionId = Number(item.decision_id);
          content = (
            <AgentDecisionTimelineCard
              item={item}
              detail={decisionDetails[decisionId]}
              roleId={roleId}
              roleName={roleName}
              detailsLoading={decisionDetailsLoading}
              detailsError={decisionDetailsError}
              onRetryDetails={onRetryDecisionDetails}
              onChanged={onDecisionChanged}
            />
          );
        } else {
          const isAgent = item.author === 'agent';
          content = isAgent ? (
            <ChatMessage
              role="assistant"
              time={item.created_at}
              label={agentLabel}
              text={suppressStructuredMessageCopy(item) ? undefined : item.text}
            >
              {(item.actions || []).map((card, index) => (
                <React.Fragment key={card.id ?? `${card.type || 'action'}-${index}`}>
                  {renderAction?.(card, index, item)}
                </React.Fragment>
              ))}
            </ChatMessage>
          ) : (
            <ChatMessage role="user" text={item.text} time={item.created_at} />
          );
        }

        return (
          <MotionChatItem key={item.id} className="tk-motion-row">
            {content}
          </MotionChatItem>
        );
        })}
        {after}
      </MotionList>
      <IncomingAssistantAnnouncement
        items={items}
        scopeKey={roleId ?? roleName ?? label}
        agentLabel={agentLabel}
      />
    </>
  );
}

export default RoleAgentTimeline;
