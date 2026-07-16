import { forwardRef, useId } from 'react';

import './ChatArtifact.css';

const statusValue = (status) => {
  if (!status) return null;
  if (typeof status === 'string') return { label: status, tone: 'info' };
  return {
    label: status.label,
    detail: status.detail,
    tone: status.tone || 'info',
  };
};

/**
 * Shared outer anatomy for inspectable work produced in chat.
 *
 * Domain renderers own their body (candidate evidence, comparisons, graphs,
 * task drafts, and so on). This component owns the visual relationship to the
 * conversation: one calm shell, one header hierarchy, one status language,
 * and density-aware body/footer spacing.
 */
export const ChatArtifact = forwardRef(function ChatArtifact({
  as: Component = 'section',
  eyebrow,
  title,
  summary,
  meta,
  status,
  icon: Icon,
  children,
  footer,
  flush = false,
  className = '',
  bodyClassName = '',
  ...props
}, ref) {
  const headingId = useId();
  const resolvedStatus = statusValue(status);
  const hasHeader = eyebrow || title || summary || meta || resolvedStatus || Icon;

  return (
    <Component
      ref={ref}
      className={`tk-artifact${className ? ` ${className}` : ''}`}
      data-artifact-status={resolvedStatus?.tone || undefined}
      aria-labelledby={title ? headingId : undefined}
      {...props}
    >
      {hasHeader ? (
        <header className="tk-artifact-head">
          <div className="tk-artifact-heading">
            {Icon ? (
              <span className="tk-artifact-icon" aria-hidden="true">
                <Icon size={15} />
              </span>
            ) : null}
            <div className="tk-artifact-heading-copy">
              {eyebrow ? <span className="tk-artifact-eyebrow">{eyebrow}</span> : null}
              {title ? <h3 id={headingId} className="tk-artifact-title">{title}</h3> : null}
              {summary ? <div className="tk-artifact-summary">{summary}</div> : null}
              {meta ? <div className="tk-artifact-meta">{meta}</div> : null}
            </div>
          </div>

          {resolvedStatus?.label ? (
            <span className="tk-artifact-status">
              <span className="tk-artifact-status-dot" aria-hidden="true" />
              {resolvedStatus.label}
            </span>
          ) : null}

          {resolvedStatus?.detail ? (
            <div className="tk-artifact-status-detail">{resolvedStatus.detail}</div>
          ) : null}
        </header>
      ) : null}

      {children ? (
        <div className={`tk-artifact-body${flush ? ' is-flush' : ''}${bodyClassName ? ` ${bodyClassName}` : ''}`}>
          {children}
        </div>
      ) : null}

      {footer ? <footer className="tk-artifact-foot">{footer}</footer> : null}
    </Component>
  );
});

export default ChatArtifact;
