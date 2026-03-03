import React from 'react';

import { cx } from '../../shared/ui/TaaliPrimitives';
import { formatScale100Score } from '../../lib/scoreDisplay';
import { toCvScore100 } from './candidatesUiUtils';

const formatInnerValue = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return numeric.toFixed(1);
};

export function CandidateScoreRing({
  score,
  details = null,
  size = 76,
  strokeWidth = 8,
  label = 'Score',
  className = '',
  valueClassName = '',
}) {
  const normalized = toCvScore100(score, details);
  const radius = Math.max(1, (size - strokeWidth) / 2);
  const circumference = 2 * Math.PI * radius;
  const clamped = normalized == null ? 0 : Math.max(0, Math.min(100, normalized));
  const offset = circumference * (1 - (clamped / 100));
  const ringColor = normalized == null ? 'var(--taali-border)' : 'var(--taali-purple)';
  const ariaLabel = normalized == null
    ? `${label}: unavailable`
    : `${label}: ${formatScale100Score(normalized, '0-100')}`;

  return (
    <div
      role="img"
      aria-label={ariaLabel}
      className={cx('relative inline-flex items-center justify-center', className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--taali-border-muted)"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={ringColor}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className={cx('font-mono text-sm font-semibold text-[var(--taali-text)]', valueClassName)}>
          {formatInnerValue(normalized)}
        </span>
      </div>
    </div>
  );
}
