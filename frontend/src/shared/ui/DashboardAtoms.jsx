import React from 'react';
import { AlertCircle, Check, Clock, Timer, XCircle } from 'lucide-react';
import { Badge } from './TaaliPrimitives';
import { PageLink } from './PageLink';

// StatsCard accepts one of three click contracts, in priority order:
//   - `to`: a raw href → renders as <Link>, supports ctrl/cmd+click for new tab
//   - `page` + `options`: routed page id → renders as <Link> via PageLink
//   - `onClick`: legacy callback → renders as <button>
// If none are passed, renders as a static <div>.
export const StatsCard = ({ icon: Icon, label, value, subValue, change, onClick, to, page, options }) => {
  const className = [
    'rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] p-4 shadow-[var(--taali-shadow-soft)] transition-transform duration-200 hover:-translate-y-0.5 hover:shadow-[var(--taali-shadow-strong)] text-left',
    (to || page || onClick) ? 'cursor-pointer block' : 'cursor-default',
  ].join(' ');
  const style = { background: 'var(--taali-card-bg)' };
  const body = (
    <>
      <Icon size={24} className="mb-3 text-[var(--taali-purple)]" />
      <div className="text-xs uppercase tracking-[0.1em] text-[var(--taali-muted)] mb-1.5">{label}</div>
      <div className="taali-display text-3xl font-semibold mb-1 text-[var(--taali-text)]">{value}</div>
      {subValue ? (
        <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">{subValue}</div>
      ) : null}
      <div className="font-mono text-[11px] leading-4 text-[var(--taali-muted)]">{change}</div>
    </>
  );

  if (to || page) {
    return (
      <PageLink to={to} page={page} options={options} className={className} style={style} onClick={onClick}>
        {body}
      </PageLink>
    );
  }
  if (onClick) {
    return (
      <button type="button" className={className} style={style} onClick={onClick}>
        {body}
      </button>
    );
  }
  return (
    <div className={className} style={style}>
      {body}
    </div>
  );
};

export const StatusBadge = ({ status }) => {
  const normalized = String(status || '').toLowerCase();

  if (normalized === 'completed') {
    return (
      <Badge variant="purple" className="inline-flex gap-1">
        <Check size={12} /> Completed
      </Badge>
    );
  }

  if (normalized === 'completed_due_to_timeout') {
    return (
      <Badge variant="warning" className="inline-flex gap-1">
        <Clock size={12} /> Timed Out
      </Badge>
    );
  }

  if (normalized === 'in_progress') {
    return (
      <Badge variant="warning" className="inline-flex gap-1">
        <Timer size={12} /> In Progress
      </Badge>
    );
  }

  if (normalized === 'expired') {
    return (
      <Badge variant="danger" className="inline-flex gap-1">
        <XCircle size={12} /> Expired
      </Badge>
    );
  }

  return (
    <Badge variant="muted" className="inline-flex gap-1">
      <AlertCircle size={12} /> Invited
    </Badge>
  );
};
