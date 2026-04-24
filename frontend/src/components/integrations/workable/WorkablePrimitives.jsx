import React from 'react';
import { ArrowRight } from 'lucide-react';

import { deriveSyncHealth, formatRelativeTime } from '../../../lib/workableUi';

const workableGradient = 'linear-gradient(135deg, var(--workable) 0%, var(--workable-dark) 100%)';

const cx = (...parts) => parts.filter(Boolean).join(' ');

export const WorkableLogo = ({ size = 30, className = '' }) => (
  <span
    aria-hidden="true"
    className={cx('grid place-items-center rounded-[10px] text-white shadow-[0_4px_12px_rgba(45,140,255,.25)]', className)}
    style={{
      width: size,
      height: size,
      background: workableGradient,
      fontSize: size >= 40 ? 18 : 13,
      fontWeight: 700,
    }}
  >
    W
  </span>
);

export const SyncPulse = ({ status = 'healthy', className = '' }) => {
  const background = status === 'error'
    ? 'var(--red)'
    : status === 'stale'
      ? 'var(--amber)'
      : 'var(--green)';

  return (
    <span
      aria-hidden="true"
      className={cx('inline-block rounded-full', className)}
      style={{
        width: 8,
        height: 8,
        background,
        boxShadow: `0 0 0 3px color-mix(in oklab, ${background} 22%, transparent)`,
        animation: 'pulse 2s infinite',
      }}
    />
  );
};

export const WorkableTag = ({ size = 'md', label = 'WORKABLE', className = '' }) => {
  const padding = size === 'sm' ? '3px 8px' : '5px 10px';
  const fontSize = size === 'sm' ? 9.5 : 10.5;

  return (
    <span
      className={cx('inline-flex items-center gap-[5px] rounded-[6px] font-[var(--font-mono)] font-semibold tracking-[0.08em] text-white', className)}
      style={{ padding, fontSize, background: workableGradient }}
    >
      <ArrowRight size={size === 'sm' ? 9 : 11} strokeWidth={3} />
      {label}
    </span>
  );
};

export const WorkableTagSm = ({ className = '' }) => (
  <WorkableTag size="sm" label="WK" className={className} />
);

export const WorkableScorePip = ({ value, className = '' }) => {
  if (!Number.isFinite(Number(value))) return null;

  return (
    <span className={cx('font-[var(--font-mono)] text-[10px] font-medium text-[var(--workable)]', className)}>
      WK <b className="font-semibold">{Math.round(Number(value))}</b>
    </span>
  );
};

export const WorkableSyncIndicator = ({ lastSyncedAt, status = null, className = '' }) => {
  const tone = deriveSyncHealth({
    lastSyncStatus: status,
    lastSyncAt: lastSyncedAt,
  });

  return (
    <span className={cx('inline-flex items-center gap-[5px] font-[var(--font-mono)] text-[10.5px] text-[var(--mute)]', className)}>
      <SyncPulse status={tone} className="!h-[6px] !w-[6px]" />
      {lastSyncedAt ? `Synced ${formatRelativeTime(lastSyncedAt)}` : 'Sync pending'}
    </span>
  );
};
