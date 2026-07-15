import { CircleAlert, CircleCheck, Info, TriangleAlert } from 'lucide-react';

import { ChatActivity } from '../../../shared/chat';
import { safeInternalRoute } from '../../../shared/chat/safeInternalRoute';

const EVENT_SEVERITIES = {
  info: { label: 'Info', Icon: Info },
  success: { label: 'Completed', Icon: CircleCheck },
  warning: { label: 'Warning', Icon: TriangleAlert },
  error: { label: 'Error', Icon: CircleAlert },
};

const humanize = (value, fallback = '') => {
  const text = String(value || '').trim().replace(/[_-]+/g, ' ');
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : fallback;
};

const formatEventTime = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
};

export function AgentEventCard({ card, onPrompt }) {
  const severity = Object.hasOwn(EVENT_SEVERITIES, card?.severity)
    ? card.severity
    : 'info';
  const { label: severityLabel, Icon } = EVENT_SEVERITIES[severity];
  const title = String(card?.title || '').trim() || humanize(card?.event_type, 'Agent update');
  const summary = String(card?.summary || '').trim();
  const details = (Array.isArray(card?.details) ? card.details : [])
    .map((detail) => ({
      label: String(detail?.label || '').trim(),
      value: detail?.value == null ? '' : String(detail.value).trim(),
    }))
    .filter((detail) => detail.label && detail.value);
  const sourceType = String(card?.source?.type || '').trim();
  const sourceId = card?.source?.id == null ? '' : String(card.source.id).trim();
  const providedSourceLabel = String(card?.source?.label || '').trim();
  const sourceLabel = sourceType && sourceId
    ? providedSourceLabel || `${humanize(sourceType)} #${sourceId}`
    : '';
  const sourceHref = sourceLabel ? safeInternalRoute(card?.source?.href) : null;
  const occurredAt = formatEventTime(card?.occurred_at);
  const suggestions = (Array.isArray(card?.suggestions) ? card.suggestions : [])
    .map((suggestion) => ({
      label: String(suggestion?.label || suggestion?.prompt || '').trim(),
      prompt: String(suggestion?.prompt || '').trim(),
    }))
    .filter((suggestion) => suggestion.label && suggestion.prompt);

  return (
    <ChatActivity
      data-severity={severity}
      data-testid="agent-event"
      aria-label={`${severityLabel} agent event: ${title}`}
      severity={severity}
      severityLabel={severityLabel}
      typeLabel={card?.event_type ? humanize(card.event_type) : null}
      title={title}
      summary={summary}
      icon={Icon}
      source={sourceLabel ? {
        label: sourceLabel,
        href: sourceHref,
        ariaLabel: `Open ${sourceLabel}`,
      } : null}
      timestamp={occurredAt ? {
        label: occurredAt,
        dateTime: String(card.occurred_at),
      } : null}
      details={details}
      actions={suggestions.map((suggestion) => ({
        label: suggestion.label,
        ariaLabel: `${suggestion.label} — edit in composer`,
        title: 'Add to the composer for editing',
        onClick: () => onPrompt?.(suggestion.prompt),
      }))}
    />
  );
}

export default AgentEventCard;
