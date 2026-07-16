import { useId, useState } from 'react';
import { ChevronDown } from 'lucide-react';

import {
  m,
  MotionDisclosure,
  motionTransition,
  useReducedMotionSync,
} from '../motion';
import './ChatActivity.css';

/**
 * Flat, transcript-native activity row for durable agent/tool history.
 *
 * This is intentionally not a notification card: severity lives in the rail
 * marker and visible text, while the conversation stays on one calm surface.
 * Callers own the action semantics. In Agent Chat, event suggestions only add
 * editable text to the composer; this component never executes them itself.
 */
export function ChatActivity({
  severity = 'info',
  severityLabel = 'Info',
  typeLabel,
  title,
  summary,
  icon: Icon,
  source,
  timestamp,
  details = [],
  disclosureLabel = 'Details',
  disclosureAriaLabel,
  actions = [],
  detailOnly = false,
  ...articleProps
}) {
  const reduced = useReducedMotionSync();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsId = useId();

  return (
    <article
      className={`tk-activity${detailOnly ? ' is-detail-only' : ''}`}
      data-severity={severity}
      aria-label={detailOnly ? title : undefined}
      {...articleProps}
    >
      {!detailOnly ? (
        <span className="tk-activity-rail" aria-hidden="true">
          {Icon ? <Icon size={13} /> : <span className="tk-activity-dot" />}
        </span>
      ) : null}

      <div className="tk-activity-main">
        {!detailOnly ? (
          <>
            <div className="tk-activity-kicker">
              <span className="tk-activity-severity">{severityLabel}</span>
              {typeLabel ? <span className="tk-activity-type">{typeLabel}</span> : null}
            </div>

            <div className="tk-activity-title-row">
              <h3 className="tk-activity-title">{title}</h3>
              {timestamp?.label ? (
                <time className="tk-activity-time" dateTime={timestamp.dateTime || undefined}>
                  {timestamp.label}
                </time>
              ) : null}
            </div>
          </>
        ) : null}

        {summary ? <p className="tk-activity-summary">{summary}</p> : null}

        {source?.label ? (
          <div className="tk-activity-meta">
            {source.href ? (
              <a href={source.href} aria-label={source.ariaLabel || `Open ${source.label}`}>
                {source.label}
              </a>
            ) : <span>{source.label}</span>}
          </div>
        ) : null}

        {actions.length > 0 ? (
          <div className="tk-activity-actions" role="group" aria-label={`Follow up on ${title}`}>
            {actions.map((action, index) => (
              <m.button
                key={`${action.label}-${index}`}
                type="button"
                aria-label={action.ariaLabel}
                title={action.title}
                onClick={action.onClick}
                whileTap={reduced ? undefined : { scale: 0.98 }}
                transition={reduced ? motionTransition.instant : motionTransition.fast}
              >
                {action.label}
              </m.button>
            ))}
          </div>
        ) : null}

        {details.length > 0 ? (
          <div className="tk-activity-details">
            <button
              type="button"
              className="tk-activity-details-trigger"
              aria-label={disclosureAriaLabel}
              aria-expanded={detailsOpen}
              aria-controls={detailsId}
              onClick={() => setDetailsOpen((open) => !open)}
            >
              <span>{disclosureLabel}</span>
              <m.span
                className="tk-activity-details-chevron"
                aria-hidden="true"
                animate={{ rotate: detailsOpen ? 180 : 0 }}
                transition={reduced ? motionTransition.instant : motionTransition.fast}
              >
                <ChevronDown size={12} />
              </m.span>
            </button>
            <MotionDisclosure open={detailsOpen} id={detailsId}>
              <dl>
                {details.map((detail, index) => (
                  <div key={`${detail.label}-${index}`}>
                    <dt>{detail.label}</dt>
                    <dd>{detail.value}</dd>
                  </div>
                ))}
              </dl>
            </MotionDisclosure>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export default ChatActivity;
