import React, { useId, useMemo, useState } from 'react';
import {
  Bell,
  Check,
  ChevronDown,
  CircleAlert,
  CircleHelp,
  Info,
  Sparkles,
  TriangleAlert,
  UserRoundCheck,
} from 'lucide-react';

import {
  MotionAttentionBadge,
  MotionChatItem,
  MotionDisclosure,
  MotionList,
  MotionTab,
  MotionTabs,
  m,
  motionTransition,
  useReducedMotionSync,
} from '../motion';
import { AgentPromptCard, agentPromptTitle } from './AgentPromptCard';
import { ChatMarkdown } from './ChatMarkdown';
import { pathForPage } from '../../app/routing';
import './AgentFeedTimeline.css';

const FEED_MESSAGE_KINDS = new Set(['event', 'proactive']);

const EVENT_PRESENTATION = {
  info: { label: 'Update', Icon: Info },
  success: { label: 'Completed', Icon: Check },
  warning: { label: 'Warning', Icon: TriangleAlert },
  error: { label: 'Error', Icon: CircleAlert },
};

const DECISION_LABELS = {
  advance: 'Advance recommended',
  reject: 'Reject recommended',
  skip_assessment_reject: 'Reject recommended',
  send_assessment: 'Assessment recommended',
  resend_assessment_invite: 'Resend assessment',
  pre_screen_reject: 'Pre-screen reject',
};

const ACTIONABLE_DECISION_STATUSES = new Set(['pending', 'reverted_for_feedback']);

const humanize = (value, fallback = '') => {
  const text = String(value || '').trim().replace(/[_-]+/g, ' ');
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : fallback;
};

const rowTime = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
};

const eventCardFor = (item) => (
  (item?.actions || []).find((card) => card?.type === 'agent_event') || null
);

const helperCardFor = (item) => (
  (item?.actions || []).find((card) => card?.type === 'helper_prompt') || null
);

const decisionLabel = (item) => {
  const value = item?.recommendation || item?.decision_type;
  return DECISION_LABELS[value] || humanize(value, 'Decision ready');
};

export const agentTimelineLane = (item) => {
  if (!item) return 'conversation';
  if (item.kind === 'decision' || item.kind === 'needs_input') return 'feed';
  if (item.kind === 'message' && item.author === 'agent' && FEED_MESSAGE_KINDS.has(item.message_kind)) {
    return 'feed';
  }
  return 'conversation';
};

export const splitAgentTimeline = (items = []) => items.reduce(
  (lanes, item) => {
    lanes[agentTimelineLane(item)].push(item);
    return lanes;
  },
  { conversation: [], feed: [] },
);

export const agentFeedItemMeta = (item) => {
  if (item?.kind === 'needs_input') {
    const open = item.status === 'open';
    return {
      title: agentPromptTitle(item),
      summary: String(item.prompt || '').trim(),
      severity: open ? 'warning' : 'success',
      statusLabel: open ? 'Needs you' : 'Resolved',
      category: open ? 'needs' : 'activity',
      attention: open,
      Icon: CircleHelp,
      time: rowTime(item.created_at),
    };
  }

  if (item?.kind === 'decision_summary') {
    const count = Number(item.count || 0);
    return {
      title: `${count} candidate ${count === 1 ? 'decision' : 'decisions'} ready`,
      summary: 'Open the review queue to work through candidate recommendations.',
      severity: 'info',
      statusLabel: 'Review queue',
      category: 'decisions',
      attention: false,
      Icon: UserRoundCheck,
      time: rowTime(item.created_at),
    };
  }

  if (item?.kind === 'decision') {
    const pending = item.status === 'pending' || item.status === 'reverted_for_feedback';
    return {
      title: `${item.candidate_name || 'Candidate'} · ${decisionLabel(item)}`,
      summary: String(item.reasoning || '').trim(),
      severity: pending ? 'info' : 'success',
      statusLabel: pending ? 'Review' : 'Resolved',
      category: 'decisions',
      attention: false,
      Icon: UserRoundCheck,
      time: rowTime(item.created_at),
      score: item.score ?? item.taali_score ?? null,
    };
  }

  const event = eventCardFor(item);
  if (event) {
    const severity = Object.hasOwn(EVENT_PRESENTATION, event.severity) ? event.severity : 'info';
    const presentation = EVENT_PRESENTATION[severity];
    return {
      title: String(event.title || '').trim() || humanize(event.event_type, 'Agent update'),
      summary: String(event.summary || item?.text || '').trim(),
      severity,
      statusLabel: presentation.label,
      category: severity === 'warning' || severity === 'error' ? 'issues' : 'activity',
      // Events are durable informational history and currently have no
      // acknowledged/resolved state. Counting them as attention would make old
      // failures live forever. Only explicit open recruiter requests badge.
      attention: false,
      Icon: presentation.Icon,
      time: rowTime(event.occurred_at || item?.created_at),
    };
  }

  const helper = helperCardFor(item);
  return {
    title: String(helper?.title || '').trim() || 'Agent suggestion',
    summary: String(helper?.summary || helper?.question || item?.text || '').trim(),
    severity: 'info',
    statusLabel: 'Suggestion',
    category: 'activity',
    attention: false,
    Icon: Sparkles,
    time: rowTime(item?.created_at),
  };
};

export const agentFeedAttentionCount = (items = []) => (
  items.reduce((count, item) => count + (agentFeedItemMeta(item).attention ? 1 : 0), 0)
);

const feedItemKey = (item, index) => (
  `${item?.kind || 'item'}:${item?.id ?? item?.needs_input_id ?? item?.decision_id ?? index}`
);

export function AgentStreamTabs({
  value,
  onChange,
  attentionCount = 0,
  chatPanelId,
  feedPanelId,
  className = '',
}) {
  return (
    <MotionTabs
      value={value}
      onValueChange={onChange}
      className={`tk-agent-stream-tabs${className ? ` ${className}` : ''}`}
      aria-label="Agent workspace"
    >
      <MotionTab
        value="chat"
        className="tk-agent-stream-tab"
        indicatorClassName="tk-agent-stream-tab-indicator"
        aria-controls={chatPanelId}
      >
        Chat
      </MotionTab>
      <MotionTab
        value="feed"
        className="tk-agent-stream-tab"
        indicatorClassName="tk-agent-stream-tab-indicator"
        aria-controls={feedPanelId}
      >
        Agent feed
        <MotionAttentionBadge
          value={attentionCount}
          className="tk-agent-stream-count"
          aria-label={`${attentionCount} agent feed ${attentionCount === 1 ? 'item needs' : 'items need'} attention`}
          format={(valueToFormat) => (valueToFormat > 99 ? '99+' : valueToFormat)}
        />
      </MotionTab>
    </MotionTabs>
  );
}

export function CandidateDecisionReference({ item, roleId }) {
  const decisionId = Number(item?.decision_id ?? item?.id);
  const scopedRoleId = item?.role_id ?? roleId;
  const actionable = ACTIONABLE_DECISION_STATUSES.has(String(item?.status || '').toLowerCase());
  const reviewHref = actionable && Number.isFinite(decisionId)
    ? `/home?${scopedRoleId ? `role=${encodeURIComponent(scopedRoleId)}&` : ''}pending=${decisionId}`
    : item?.application_id
      ? pathForPage('candidate-report', {
          candidateApplicationId: item.application_id,
          fromHome: true,
          viewRoleId: scopedRoleId,
        })
      : `/home?${scopedRoleId ? `role=${encodeURIComponent(scopedRoleId)}&` : ''}status=resolved`;
  const reviewLabel = actionable
    ? 'Review in queue'
    : item?.application_id ? 'Open candidate report' : 'Open decision history';
  const score = item?.score ?? item?.taali_score;

  return (
    <div className="tk-agent-decision-reference">
      {item?.reasoning ? <p>{item.reasoning}</p> : null}
      <div className="tk-agent-decision-reference-meta">
        {score != null ? <span>Taali {Math.round(Number(score))}</span> : null}
        <span>{decisionLabel(item)}</span>
        <span>{humanize(item?.status, 'Pending')}</span>
      </div>
      <a href={reviewHref}>{reviewLabel}</a>
    </div>
  );
}

function DecisionQueueReference({ item, roleId }) {
  const scopedRoleId = item?.role_id ?? roleId;
  const href = scopedRoleId ? `/home?role=${encodeURIComponent(scopedRoleId)}` : '/home';
  return (
    <div className="tk-agent-decision-reference tk-agent-decision-summary">
      <p>Candidate recommendations stay in the review workspace, where evidence and actions have room.</p>
      <a href={href}>Open review queue</a>
    </div>
  );
}

function AgentFeedRow({ item, children, showSummary = true }) {
  const reduced = useReducedMotionSync();
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const meta = agentFeedItemMeta(item);
  const Icon = meta.Icon || Bell;

  return (
    <article
      className="tk-agent-feed-row"
      data-severity={meta.severity}
      data-feed-category={meta.category}
      aria-label={`${meta.statusLabel}: ${meta.title}`}
    >
      <button
        type="button"
        className="tk-agent-feed-trigger"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="tk-agent-feed-icon" aria-hidden="true"><Icon size={14} /></span>
        <span className="tk-agent-feed-title">{meta.title}</span>
        {meta.score != null ? <span className="tk-agent-feed-score">{Math.round(Number(meta.score))}</span> : null}
        <span className="tk-agent-feed-status">{meta.statusLabel}</span>
        {meta.time ? <time className="tk-agent-feed-time" dateTime={item?.created_at}>{meta.time}</time> : null}
        <m.span
          className="tk-agent-feed-chevron"
          aria-hidden="true"
          animate={{ rotate: open ? 180 : 0 }}
          transition={reduced ? motionTransition.instant : motionTransition.fast}
        >
          <ChevronDown size={13} />
        </m.span>
      </button>
      <MotionDisclosure open={open} id={detailsId} className="tk-agent-feed-disclosure">
        <div className="tk-agent-feed-detail">
          {showSummary && meta.summary ? <p className="tk-agent-feed-summary">{meta.summary}</p> : null}
          {children}
        </div>
      </MotionDisclosure>
    </article>
  );
}

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'needs', label: 'Needs you' },
  { value: 'issues', label: 'Issues' },
  { value: 'decisions', label: 'Decisions' },
];

const suppressStructuredCopy = (item) => (
  (item?.message_kind === 'event' && Boolean(eventCardFor(item)))
  || (item?.message_kind === 'proactive' && Boolean(helperCardFor(item)))
);

export function AgentFeedTimeline({
  items = [],
  roleId,
  roleName,
  openQuestionPositions = new Map(),
  openQuestionCount = 0,
  onAnswer,
  onDismiss,
  onPrompt,
  onReply,
  renderAction,
  className = '',
}) {
  const [filter, setFilter] = useState('all');
  const reduced = useReducedMotionSync();
  const filterLayoutId = `agent-feed-filter-${useId().replace(/:/g, '')}`;
  const filtered = useMemo(() => {
    let matching;
    if (filter === 'all') {
      const pendingDecisions = items.filter(
        (item) => item.kind === 'decision'
          && ACTIONABLE_DECISION_STATUSES.has(String(item.status || '').toLowerCase()),
      );
      const latestDecision = pendingDecisions.reduce((latest, item) => (
        String(item.created_at || '') > String(latest?.created_at || '') ? item : latest
      ), null);
      matching = items.filter((item) => item.kind !== 'decision');
      if (pendingDecisions.length) {
        matching = [...matching, {
          kind: 'decision_summary',
          id: `decision-summary-${roleId || 'all'}`,
          count: pendingDecisions.length,
          role_id: roleId,
          created_at: latestDecision?.created_at,
        }];
      }
    } else {
      matching = items.filter((item) => agentFeedItemMeta(item).category === filter);
    }
    return [...matching]
      .sort((left, right) => String(left?.created_at || '').localeCompare(String(right?.created_at || '')))
      .reverse();
  }, [filter, items, roleId]);

  return (
    <div className={`tk-agent-feed${className ? ` ${className}` : ''}`}>
      <div className="tk-agent-feed-filters" role="group" aria-label="Filter agent feed">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            className="tk-agent-feed-filter"
            aria-pressed={filter === option.value}
            onClick={() => setFilter(option.value)}
          >
            {option.label}
            {filter === option.value ? (
              <m.span
                aria-hidden="true"
                className="tk-agent-feed-filter-indicator"
                layoutId={filterLayoutId}
                transition={reduced ? motionTransition.instant : motionTransition.layout}
              />
            ) : null}
          </button>
        ))}
      </div>

      {filtered.length ? (
        <MotionList className="tk-agent-feed-list" aria-label="Agent feed items" layout={false}>
          {filtered.map((item, index) => (
            <MotionChatItem key={feedItemKey(item, index)} className="tk-agent-feed-motion-row">
              <AgentFeedRow
                item={item}
                showSummary={item.kind === 'message' && !eventCardFor(item) && !helperCardFor(item)}
              >
                {item.kind === 'needs_input' ? (
                  <AgentPromptCard
                    item={item}
                    detailOnly
                    onAnswer={onAnswer}
                    onDismiss={onDismiss}
                    onPrompt={onPrompt}
                    onReply={onReply}
                    position={openQuestionPositions.get(item.needs_input_id ?? item.id)}
                    total={openQuestionCount}
                  />
                ) : item.kind === 'decision' ? (
                  <CandidateDecisionReference item={item} roleId={roleId} roleName={roleName} />
                ) : item.kind === 'decision_summary' ? (
                  <DecisionQueueReference item={item} roleId={roleId} />
                ) : (
                  <div className="tk-agent-feed-message-detail">
                    {!suppressStructuredCopy(item) && item.text ? <ChatMarkdown>{item.text}</ChatMarkdown> : null}
                    {(item.actions || []).map((card, actionIndex) => (
                      <React.Fragment key={card.id ?? `${card.type || 'action'}-${actionIndex}`}>
                        {renderAction?.(card, actionIndex, item, { detailOnly: true })}
                      </React.Fragment>
                    ))}
                  </div>
                )}
              </AgentFeedRow>
            </MotionChatItem>
          ))}
        </MotionList>
      ) : (
        <div className="tk-agent-feed-empty">
          <Bell size={18} aria-hidden="true" />
          <strong>No {filter === 'all' ? 'agent activity' : FILTERS.find((option) => option.value === filter)?.label.toLowerCase()}</strong>
          <span>Background work will appear here without interrupting chat.</span>
        </div>
      )}
    </div>
  );
}

export default AgentFeedTimeline;
