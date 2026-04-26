import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowRight,
  BriefcaseBusiness,
  Clock3,
  RefreshCw,
  Settings2,
} from 'lucide-react';

import {
  Button,
  Panel,
  cx,
} from './TaaliPrimitives';

const relativeFormatter = typeof Intl !== 'undefined'
  ? new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  : null;

const toDate = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const formatShortDate = (value) => {
  const parsed = toDate(value);
  if (!parsed) return '—';
  return parsed.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
};

export const formatRelativeDateTime = (value, { now = Date.now() } = {}) => {
  const parsed = toDate(value);
  if (!parsed) return '—';
  const deltaMs = parsed.getTime() - now;
  const deltaMinutes = Math.round(deltaMs / 60000);
  const absMinutes = Math.abs(deltaMinutes);

  if (absMinutes < 1) return 'just now';
  if (absMinutes < 60) {
    return relativeFormatter
      ? relativeFormatter.format(deltaMinutes, 'minute')
      : `${absMinutes}m`;
  }
  const deltaHours = Math.round(deltaMinutes / 60);
  const absHours = Math.abs(deltaHours);
  if (absHours < 24) {
    return relativeFormatter
      ? relativeFormatter.format(deltaHours, 'hour')
      : `${absHours}h`;
  }
  const deltaDays = Math.round(deltaHours / 24);
  return relativeFormatter
    ? relativeFormatter.format(deltaDays, 'day')
    : `${Math.abs(deltaDays)}d`;
};

const formatCountdown = (value, { now = Date.now() } = {}) => {
  const parsed = toDate(value);
  if (!parsed) return '—';
  const diffMs = parsed.getTime() - now;
  if (diffMs <= 0) return 'Due now';
  const totalMinutes = Math.round(diffMs / 60000);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    return `${days}d ${hours % 24}h`;
  }
  if (minutes === 0) return `${hours}h`;
  return `${hours}h ${minutes}m`;
};

const formatPercentScore = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return `${Math.round(numeric)}`;
};

export const WorkableLogo = ({ size = 36, className = '' }) => (
  <div
    className={cx(
      'inline-flex items-center justify-center rounded-[14px] text-[var(--taali-inverse-text)] shadow-[0_12px_30px_rgba(45,140,255,0.18)]',
      className
    )}
    style={{
      width: size,
      height: size,
      background: 'linear-gradient(135deg, var(--taali-workable) 0%, var(--taali-workable-dark) 100%)',
    }}
    aria-hidden
  >
    <span className="text-[0.92em] font-semibold tracking-[-0.04em]">W</span>
  </div>
);

const pulseTone = {
  healthy: {
    bg: 'var(--taali-success)',
    shadow: 'rgba(22, 163, 74, 0.28)',
  },
  stale: {
    bg: 'var(--taali-warning)',
    shadow: 'rgba(216, 138, 28, 0.28)',
  },
  error: {
    bg: 'var(--taali-danger)',
    shadow: 'rgba(180, 35, 48, 0.28)',
  },
};

export const SyncPulse = ({ status = 'healthy', className = '' }) => {
  const tone = pulseTone[status] || pulseTone.healthy;
  return (
    <span
      className={cx('relative inline-flex h-2.5 w-2.5 shrink-0 rounded-full', className)}
      style={{ backgroundColor: tone.bg, boxShadow: `0 0 0 4px ${tone.shadow}` }}
      aria-hidden
    />
  );
};

export const resolveSyncHealth = ({ status = '', lastSyncedAt = null }) => {
  const normalizedStatus = String(status || '').trim().toLowerCase();
  if (normalizedStatus === 'error' || normalizedStatus === 'failed') return 'error';
  const parsed = toDate(lastSyncedAt);
  if (!parsed) return 'stale';
  const ageMinutes = Math.abs(Date.now() - parsed.getTime()) / 60000;
  if (ageMinutes > 180) return 'stale';
  return 'healthy';
};

export const WorkableTag = ({
  label = 'Workable',
  size = 'md',
  className = '',
}) => (
  <span
    className={cx(
      'inline-flex items-center gap-1 rounded-full border px-3 py-1 font-medium tracking-[0.01em]',
      size === 'sm' ? 'text-[11px]' : 'text-[12px]',
      className
    )}
    style={{
      borderColor: 'color-mix(in oklab, var(--taali-workable) 28%, var(--taali-line))',
      background: 'linear-gradient(135deg, rgba(45,140,255,0.16) 0%, rgba(26,95,191,0.08) 100%)',
      color: 'var(--taali-workable-dark)',
    }}
  >
    <ArrowRight size={size === 'sm' ? 11 : 12} strokeWidth={2.2} />
    <span>{label}</span>
  </span>
);

export const WorkableTagSm = ({ className = '' }) => (
  <WorkableTag label="WK" size="sm" className={className} />
);

export const WorkableScorePip = ({ value, className = '' }) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-full px-2 py-1 font-mono text-[11px] font-semibold',
        className
      )}
      style={{
        backgroundColor: 'rgba(45, 140, 255, 0.1)',
        color: 'var(--taali-workable-dark)',
      }}
    >
      <span>WK</span>
      <b>{formatPercentScore(numeric)}</b>
    </span>
  );
};

export const WorkableSyncIndicator = ({
  lastSyncedAt,
  status = '',
  className = '',
  label = 'Synced',
}) => {
  const health = resolveSyncHealth({ status, lastSyncedAt });
  const relativeLabel = lastSyncedAt ? formatRelativeDateTime(lastSyncedAt) : 'Not synced';
  return (
    <span className={cx('inline-flex items-center gap-2 text-[11px] font-medium text-[var(--taali-muted)]', className)}>
      <SyncPulse status={health} />
      <span>{lastSyncedAt ? `${label} ${relativeLabel}` : relativeLabel}</span>
    </span>
  );
};

export const FilterChip = ({
  active = false,
  count = null,
  onClick,
  children,
  className = '',
  icon = null,
}) => (
  <button
    type="button"
    className={cx(
      'inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm font-medium transition-colors',
      active
        ? 'text-[var(--taali-purple-hover)]'
        : 'text-[var(--taali-muted)] hover:text-[var(--taali-text)]',
      className
    )}
    style={{
      borderColor: active ? 'var(--taali-purple)' : 'var(--taali-line)',
      background: active ? 'var(--taali-purple-soft)' : 'rgba(255, 255, 255, 0.82)',
    }}
    onClick={onClick}
  >
    {icon}
    <span>{children}</span>
    {count != null ? (
      <span
        className="inline-flex min-w-[1.45rem] items-center justify-center rounded-full px-1.5 py-0.5 text-[11px] font-semibold"
        style={{
          backgroundColor: active ? 'rgba(255,255,255,0.78)' : 'var(--taali-line-2)',
          color: active ? 'var(--taali-purple-hover)' : 'var(--taali-ink-2)',
        }}
      >
        {count}
      </span>
    ) : null}
  </button>
);

export const RecruiterStatStrip = ({ items = [], className = '' }) => {
  const visibleItems = items.filter((item) => item && (item.value != null || item.description));
  if (!visibleItems.length) return null;
  return (
    <div className={cx('grid gap-3 md:grid-cols-2 xl:grid-cols-4', className)}>
      {visibleItems.map((item) => (
        <div
          key={item.key || item.label}
          className="rounded-[22px] border px-4 py-4 shadow-[var(--taali-shadow-soft)]"
          style={{
            borderColor: 'var(--taali-line)',
            background: item.highlight
              ? 'linear-gradient(135deg, rgba(29,23,48,0.96) 0%, rgba(56,47,89,0.96) 100%)'
              : 'linear-gradient(180deg, rgba(255,255,255,0.96) 0%, rgba(248,245,255,0.92) 100%)',
            color: item.highlight ? 'white' : 'var(--taali-text)',
          }}
        >
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[inherit] opacity-70">
            {item.label}
          </div>
          <div className="mt-2 text-[1.75rem] font-semibold tracking-[-0.03em]">{item.value}</div>
          {item.description ? (
            <div className="mt-1 text-xs opacity-75">{item.description}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
};

export const RecruiterPageHero = ({
  eyebrow = '',
  title,
  subtitle,
  pills = [],
  actions = null,
  stats = [],
  className = '',
}) => (
  <Panel
    className={cx('overflow-hidden border p-0 shadow-[var(--taali-shadow-soft)]', className)}
    style={{
      borderColor: 'var(--taali-line)',
      background:
        'radial-gradient(circle at top left, rgba(127,57,251,0.14), transparent 28%), radial-gradient(circle at top right, rgba(45,140,255,0.14), transparent 24%), linear-gradient(155deg, rgba(255,255,255,0.98), rgba(246,241,255,0.94))',
    }}
  >
    <div className="grid gap-5 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:px-6">
      <div>
        {eyebrow ? (
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--taali-purple-hover)]">
            {eyebrow}
          </p>
        ) : null}
        <h1 className="mt-2 taali-display text-[2.5rem] font-semibold tracking-[-0.04em] text-[var(--taali-text)]">
          {title}
        </h1>
        {subtitle ? (
          <p className="mt-3 max-w-3xl text-sm leading-6 text-[var(--taali-muted)]">
            {subtitle}
          </p>
        ) : null}
        {pills.length ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {pills.map((pill) => (
              <span
                key={pill.key || pill.label}
                className="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[12px] font-medium"
                style={{
                  borderColor: pill.borderColor || 'var(--taali-line)',
                  background: pill.background || 'rgba(255,255,255,0.8)',
                  color: pill.color || 'var(--taali-ink-2)',
                }}
              >
                {pill.icon || null}
                <span>{pill.label}</span>
              </span>
            ))}
          </div>
        ) : null}
      </div>
      {actions ? (
        <div className="flex flex-wrap items-start justify-start gap-2 lg:justify-end">
          {actions}
        </div>
      ) : null}
    </div>
    {stats.length ? (
      <div className="border-t border-[var(--taali-line)] bg-[rgba(255,255,255,0.58)] px-5 py-4 lg:px-6">
        <RecruiterStatStrip items={stats} />
      </div>
    ) : null}
  </Panel>
);

export const CandidateAvatar = ({
  name,
  imageUrl,
  size = 40,
  className = '',
}) => {
  const initials = String(name || '?')
    .split(/\s+/)
    .map((part) => part[0] || '')
    .join('')
    .slice(0, 2)
    .toUpperCase();

  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        className={cx('rounded-full object-cover', className)}
        style={{ width: size, height: size }}
      />
    );
  }

  return (
    <div
      className={cx('inline-flex items-center justify-center rounded-full font-semibold text-[var(--taali-inverse-text)]', className)}
      style={{
        width: size,
        height: size,
        background: 'linear-gradient(135deg, var(--taali-purple) 0%, var(--taali-purple-hover) 100%)',
      }}
      aria-hidden
    >
      <span className="text-[0.78em] tracking-[0.02em]">{initials || '?'}</span>
    </div>
  );
};

const summaryValue = (summary = {}, keys = []) => {
  for (const key of keys) {
    const value = summary?.[key];
    if (value != null) return value;
  }
  return null;
};

export const WorkableSyncStrip = ({
  org = null,
  syncedRolesCount = 0,
  totalRolesCount = 0,
  syncing = false,
  onSyncNow,
  onManage,
  className = '',
}) => {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(interval);
  }, []);

  const workableConnected = Boolean(org?.workable_connected);
  const workableConfig = org?.workable_config || {};
  const summary = org?.workable_last_sync_summary || {};
  const lastSyncAt = org?.workable_last_sync_at || null;
  const intervalMinutes = Number(workableConfig.sync_interval_minutes || 0);
  const nextPullAt = useMemo(() => {
    const parsed = toDate(lastSyncAt);
    if (!parsed || !Number.isFinite(intervalMinutes) || intervalMinutes <= 0) return null;
    return new Date(parsed.getTime() + (intervalMinutes * 60000));
  }, [intervalMinutes, lastSyncAt]);
  const health = resolveSyncHealth({
    status: org?.workable_last_sync_status,
    lastSyncedAt: lastSyncAt,
  });

  if (!workableConnected) return null;

  const newCandidates = summaryValue(summary, ['new_candidates', 'candidates_upserted']) || 0;
  const openJobsSeen = summaryValue(summary, ['jobs_seen', 'jobs_processed']) || syncedRolesCount;
  const activeCandidatesSeen = summaryValue(summary, ['candidates_seen', 'active_candidates']) || 0;
  const errorCount = Array.isArray(summary?.errors) ? summary.errors.length : Number(summary?.errors || 0);

  return (
    <Panel
      className={cx('mb-4 overflow-hidden border p-0 shadow-[var(--taali-shadow-soft)]', className)}
      style={{
        borderColor: 'color-mix(in oklab, var(--taali-workable) 22%, var(--taali-line))',
        background:
          'linear-gradient(135deg, rgba(45,140,255,0.12) 0%, rgba(26,95,191,0.06) 28%, rgba(255,255,255,0.94) 72%)',
      }}
    >
      <div className="grid gap-4 px-5 py-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="flex items-start gap-3">
          <WorkableLogo size={40} />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[13.5px] font-semibold text-[var(--taali-ink-2)]">
                Synced from Workable
              </span>
              <SyncPulse status={health} />
            </div>
            <div className="mt-1 text-sm text-[var(--taali-text)]">
              {syncedRolesCount} of {totalRolesCount} roles synced
              {org?.workable_subdomain ? ` · ${org.workable_subdomain}.workable.com` : ''}
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-[var(--taali-muted)]">
              <span><b className="text-[var(--taali-ink-2)]">{newCandidates}</b> new candidates</span>
              <span><b className="text-[var(--taali-ink-2)]">{openJobsSeen}</b> open jobs</span>
              <span><b className="text-[var(--taali-ink-2)]">{activeCandidatesSeen}</b> active candidates</span>
              <span><b className="text-[var(--taali-ink-2)]">{errorCount}</b> errors</span>
              <span>
                <Clock3 size={11} className="mr-1 inline-flex" />
                Last pull <b className="text-[var(--taali-ink-2)]">{formatRelativeDateTime(lastSyncAt, { now })}</b>
              </span>
              <span>
                Next pull <b className="text-[var(--taali-ink-2)]">{formatCountdown(nextPullAt, { now })}</b>
              </span>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={onManage}
          >
            <Settings2 size={14} />
            Manage
          </Button>
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={onSyncNow}
            disabled={syncing}
          >
            <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Syncing…' : 'Sync now'}
          </Button>
        </div>
      </div>
    </Panel>
  );
};

export const RecruiterTableHeader = ({
  title,
  subtitle,
  right = null,
  className = '',
}) => (
  <div className={cx('flex flex-wrap items-end justify-between gap-3', className)}>
    <div>
      <div className="flex items-center gap-2 text-sm font-semibold text-[var(--taali-text)]">
        <BriefcaseBusiness size={16} className="text-[var(--taali-muted)]" />
        <span>{title}</span>
      </div>
      {subtitle ? (
        <p className="mt-1 text-xs text-[var(--taali-muted)]">{subtitle}</p>
      ) : null}
    </div>
    {right}
  </div>
);

export const buildWorkableHeroPill = (label = 'Synced from Workable') => ({
  key: 'workable',
  label,
  background: 'linear-gradient(135deg, rgba(45,140,255,0.14) 0%, rgba(26,95,191,0.08) 100%)',
  borderColor: 'color-mix(in oklab, var(--taali-workable) 24%, var(--taali-line))',
  color: 'var(--taali-workable-dark)',
});

export const buildStatusHeroPill = (label, tone = 'default') => {
  if (tone === 'success') {
    return {
      key: `${label}-success`,
      label,
      background: 'rgba(22, 163, 74, 0.12)',
      borderColor: 'var(--taali-success-border)',
      color: 'var(--taali-success)',
    };
  }
  if (tone === 'warning') {
    return {
      key: `${label}-warning`,
      label,
      background: 'rgba(216, 138, 28, 0.12)',
      borderColor: 'var(--taali-warning-border)',
      color: 'var(--taali-warning)',
    };
  }
  if (tone === 'danger') {
    return {
      key: `${label}-danger`,
      label,
      background: 'rgba(180, 35, 48, 0.1)',
      borderColor: 'var(--taali-danger-border)',
      color: 'var(--taali-danger)',
    };
  }
  return {
    key: `${label}-default`,
    label,
    background: 'rgba(255,255,255,0.82)',
    borderColor: 'var(--taali-line)',
    color: 'var(--taali-ink-2)',
  };
};

export const WorkableComparisonCard = ({
  workableRawScore = null,
  taaliScore = null,
  posted = false,
  postedAt = null,
  onPost = null,
  posting = false,
  workableProfileUrl = '',
  scorePrecedence = 'workable_first',
  className = '',
}) => {
  const showComparison = workableRawScore != null || taaliScore != null;
  if (!showComparison && !onPost && !posted) return null;

  const caption = workableRawScore != null && taaliScore != null
    ? (scorePrecedence === 'workable_first' ? 'Will overwrite Workable score' : 'Score comparison ready')
    : (taaliScore != null ? 'New Taali score ready' : 'Workable score only');

  return (
    <Panel className={cx('border p-4', className)} style={{ borderColor: 'color-mix(in oklab, var(--taali-workable) 24%, var(--taali-line))' }}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Workable comparison</div>
          <div className="mt-2 text-lg font-semibold text-[var(--taali-text)]">
            {posted ? 'Posted to Workable' : 'Ready to post'}
          </div>
          <p className="mt-1 text-sm text-[var(--taali-muted)]">
            {posted && postedAt
              ? `Posted ${formatRelativeDateTime(postedAt)}.`
              : 'Compare the synced Workable score with the current Taali score before posting recruiter notes back.'}
          </p>
        </div>
        {workableProfileUrl ? (
          <a
            href={workableProfileUrl}
            target="_blank"
            rel="noreferrer"
            className="text-xs font-medium text-[var(--taali-workable-dark)] underline underline-offset-4"
          >
            Open Workable profile
          </a>
        ) : null}
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-[16px] border border-[var(--taali-line)] bg-[rgba(45,140,255,0.08)] p-4">
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Workable raw</div>
          <div className="mt-2 text-[2rem] font-semibold tracking-[-0.04em] text-[var(--taali-workable-dark)]">
            {workableRawScore != null ? formatPercentScore(workableRawScore) : '—'}
          </div>
        </div>
        <div className="rounded-[16px] border border-[var(--taali-line)] bg-[var(--taali-surface-subtle)] p-4">
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">Taali score</div>
          <div className="mt-2 text-[2rem] font-semibold tracking-[-0.04em] text-[var(--taali-text)]">
            {taaliScore != null ? formatPercentScore(taaliScore) : '—'}
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <span className="font-mono text-[11px] text-[var(--taali-muted)]">{caption}</span>
        {posted ? (
          <span className="inline-flex items-center gap-2 rounded-full bg-[var(--taali-success-soft)] px-3 py-1 text-[11px] font-semibold text-[var(--taali-success)]">
            Posted {postedAt ? formatShortDate(postedAt) : ''}
          </span>
        ) : onPost ? (
          <Button type="button" variant="secondary" size="sm" onClick={onPost} disabled={posting}>
            {posting ? 'Posting…' : 'Post to Workable'}
          </Button>
        ) : null}
      </div>
    </Panel>
  );
};
