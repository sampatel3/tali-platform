import React from 'react';

import { Badge, Card, cx } from '../../shared/ui/TaaliPrimitives';

const variantConfig = {
  page: {
    padding: 'p-4',
    columns: 'md:grid-cols-[auto_minmax(0,1fr)_minmax(0,1.1fr)]',
    timelineColumns: 'md:grid-cols-3',
  },
  sheet: {
    padding: 'p-4',
    columns: 'grid-cols-1',
    timelineColumns: 'grid-cols-1',
  },
  preview: {
    padding: 'p-3',
    columns: 'md:grid-cols-[auto_minmax(0,1fr)_minmax(0,1.1fr)]',
    timelineColumns: 'md:grid-cols-3',
  },
};

const SectionLabel = ({ children }) => (
  <div className="text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-[var(--taali-muted)]">
    {children}
  </div>
);

const UNVERIFIED_TITLE = 'Employer name not found in the CV text — auto-extracted, treat as unverified.';

const UnverifiedTag = () => (
  <span
    className="shrink-0 rounded-full border border-[var(--taali-border-subtle)] px-1.5 py-px text-[0.5625rem] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]"
    title={UNVERIFIED_TITLE}
  >
    Unverified
  </span>
);

const TimelineRow = ({ entry }) => (
  <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-subtle)] bg-[var(--taali-surface)] px-3 py-2">
    <div className="flex items-center gap-1.5">
      <span className="truncate text-sm font-semibold text-[var(--taali-text)]" title={entry.company}>
        {entry.company || '—'}
      </span>
      {entry.company && entry.companyUnverified ? <UnverifiedTag /> : null}
    </div>
    {entry.role ? (
      <div className="mt-0.5 truncate text-xs text-[var(--taali-muted)]" title={entry.role}>
        {entry.role}
      </div>
    ) : null}
    {entry.range ? (
      <div className="mt-1 font-mono text-[0.6875rem] text-[var(--taali-muted)]">{entry.range}</div>
    ) : null}
  </div>
);

export function CandidateSnapshotCard({ snapshot, variant = 'page', className = '' }) {
  if (!snapshot) return null;
  const { yearsLabel, topSkills = [], timeline = [] } = snapshot;
  if (!yearsLabel && !topSkills.length && !timeline.length) return null;

  const config = variantConfig[variant] || variantConfig.page;

  return (
    <Card className={cx(config.padding, className)}>
      <div className={cx('grid gap-4', config.columns)}>
        {yearsLabel ? (
          <div className="flex min-w-[8.75rem] flex-col gap-1">
            <SectionLabel>Experience</SectionLabel>
            <div className="taali-display text-2xl font-semibold text-[var(--taali-text)]">
              {yearsLabel}
            </div>
          </div>
        ) : null}

        {topSkills.length ? (
          <div className="flex min-w-0 flex-col gap-2">
            <SectionLabel>Tech stack</SectionLabel>
            <div className="flex flex-wrap gap-1.5">
              {topSkills.map((skill) => (
                <Badge key={skill} variant="purple" className="text-[0.6875rem]">{skill}</Badge>
              ))}
            </div>
          </div>
        ) : null}

        {timeline.length ? (
          <div className="flex min-w-0 flex-col gap-2">
            <SectionLabel>Recent roles</SectionLabel>
            <div className={cx('grid gap-2', config.timelineColumns)}>
              {timeline.map((entry, idx) => (
                <TimelineRow key={`${entry.company || 'role'}-${idx}`} entry={entry} />
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export default CandidateSnapshotCard;
