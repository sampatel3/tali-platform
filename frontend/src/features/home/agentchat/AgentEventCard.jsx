import { ArrowUpRight, CircleAlert, CircleCheck, Info, TriangleAlert } from 'lucide-react';

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
    <article
      className="ac-card ac-card-event"
      data-severity={severity}
      data-testid="agent-event"
      aria-label={`${severityLabel} agent event: ${title}`}
    >
      <div className="ac-card-head ac-event-head">
        <Icon size={14} aria-hidden="true" />
        <span className="ac-event-severity">{severityLabel}</span>
        {card?.event_type ? <span className="ac-event-type">{humanize(card.event_type)}</span> : null}
      </div>
      <h3 className="ac-event-title">{title}</h3>
      {summary ? <p className="ac-event-summary">{summary}</p> : null}
      {(sourceLabel || occurredAt) ? (
        <div className="ac-event-meta">
          {sourceLabel ? (
            sourceHref ? (
              <a className="ac-event-source" href={sourceHref} aria-label={`Open ${sourceLabel}`}>
                {sourceLabel} <ArrowUpRight size={12} aria-hidden="true" />
              </a>
            ) : <span className="ac-event-source">{sourceLabel}</span>
          ) : null}
          {occurredAt ? <time dateTime={String(card.occurred_at)}>{occurredAt}</time> : null}
        </div>
      ) : null}
      {details.length > 0 ? (
        <details className="ac-event-details">
          <summary>Details</summary>
          <dl>
            {details.map((detail, index) => (
              <div key={`${detail.label}-${index}`}>
                <dt>{detail.label}</dt>
                <dd>{detail.value}</dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}
      {suggestions.length > 0 ? (
        <div className="ac-card-actions">
          {suggestions.map((suggestion, index) => (
            <button
              key={`${suggestion.label}-${index}`}
              type="button"
              className="ac-btn ac-btn-soft"
              aria-label={`${suggestion.label} — edit in composer`}
              title="Add to the composer for editing"
              onClick={() => onPrompt?.(suggestion.prompt)}
            >
              {suggestion.label}
            </button>
          ))}
        </div>
      ) : null}
    </article>
  );
}

export default AgentEventCard;
