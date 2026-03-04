import React from 'react';

import { formatScale100Score } from '../../lib/scoreDisplay';
import { Badge, Card, cx } from './TaaliPrimitives';

const renderScoreValue = (value, scale) => {
  if (value == null) return '—';
  if (typeof value === 'string') return value;
  return formatScale100Score(value, scale);
};

export const ScoreHeroCard = ({
  label = 'TAALI decision score',
  value,
  scale = '0-100',
  description = '',
  badgeLabel = '',
  badgeVariant = 'muted',
  className = '',
  valueClassName = '',
}) => (
  <Card className={cx('bg-[var(--taali-surface)] p-4', className)}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">{label}</div>
        <div className={cx('mt-2 taali-display text-5xl font-semibold leading-none text-[var(--taali-text)]', valueClassName)}>
          {renderScoreValue(value, scale)}
        </div>
        {description ? (
          <p className="mt-3 max-w-xl text-sm leading-6 text-[var(--taali-muted)]">{description}</p>
        ) : null}
      </div>
      {badgeLabel ? <Badge variant={badgeVariant}>{badgeLabel}</Badge> : null}
    </div>
  </Card>
);

export const ScoreMetricCard = ({
  label,
  value,
  scale = '0-100',
  sublabel = '',
  className = '',
  valueClassName = '',
}) => (
  <Card className={cx('bg-[var(--taali-surface)] px-3 py-3', className)}>
    <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">{label}</div>
    <div className={cx('mt-1.5 taali-display text-[1.55rem] font-semibold leading-none text-[var(--taali-text)]', valueClassName)}>
      {renderScoreValue(value, scale)}
    </div>
    {sublabel ? (
      <div className="mt-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">{sublabel}</div>
    ) : null}
  </Card>
);

export const InsightCard = ({
  label,
  title,
  description,
  className = '',
}) => (
  <Card className={cx('bg-[var(--taali-surface)] p-3', className)}>
    <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">{label}</div>
    <div className="mt-2 text-[1.05rem] font-semibold leading-7 text-[var(--taali-text)]">{title}</div>
    {description ? <p className="mt-1.5 text-sm leading-6 text-[var(--taali-muted)]">{description}</p> : null}
  </Card>
);
