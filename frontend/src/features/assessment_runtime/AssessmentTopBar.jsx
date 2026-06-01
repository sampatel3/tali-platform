import React from 'react';
import { AlertTriangle, ChevronRight, CircleHelp, Clock } from 'lucide-react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

const HelpPillButton = ({ as: Component = 'button', children, className = '', ...props }) => (
  <Component
    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[0.75rem] font-medium text-[var(--mute)] transition-colors hover:bg-[var(--purple-soft)] hover:text-[var(--purple)] ${className}`.trim()}
    {...props}
  >
    {children}
  </Component>
);

export const AssessmentTopBar = ({
  taskName,
  metaLine = '',
  claudeBudget,
  formatUsd,
  formatBudgetUsd = formatUsd,
  isTimeLow,
  timeUrgencyLevel = 'normal',
  timeLeft,
  formatTime,
  isTimerPaused,
  onOpenGuide,
  reportIssueHref = 'mailto:support@taali.ai',
  onSubmit,
}) => (
  <header
    className="sticky top-0 z-30 border-b border-[var(--line)] backdrop-blur-[14px]"
    style={{ background: 'color-mix(in oklab, var(--bg) 88%, transparent)' }}
  >
    <div className="flex min-h-[4.25rem] flex-wrap items-center justify-between gap-3 px-4 py-3 lg:px-8">
      <div className="min-w-0 flex items-center gap-4">
        <div className="flex items-center gap-3">
          <AssessmentBrandGlyph variant="compact-square" sizeClass="h-[1.875rem] w-[1.875rem]" markSizeClass="h-5 w-5" />
          <span className="hidden h-[1.375rem] w-px bg-[var(--line)] sm:block" />
        </div>
        <div className="min-w-0 leading-tight">
          <div className="truncate font-display text-[1.0625rem] font-semibold tracking-[-0.01em] text-[var(--ink)]">
            {taskName}
          </div>
          <div className="mt-1 truncate font-mono text-[0.65625rem] uppercase tracking-[0.1em] text-[var(--mute)]">
            {metaLine || 'Candidate assessment'}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-3">
        <div className="hidden items-center rounded-full border border-[var(--line)] bg-[var(--bg-2)] p-[0.1875rem] shadow-[var(--shadow-sm)] lg:inline-flex">
          <HelpPillButton type="button" onClick={onOpenGuide}>
            <CircleHelp size={12} />
            Guide
          </HelpPillButton>
          <HelpPillButton as="a" href={reportIssueHref}>
            <AlertTriangle size={12} />
            Report
          </HelpPillButton>
        </div>

        {claudeBudget?.enabled ? (
          <div
            className="hidden items-center gap-2 rounded-full px-3.5 py-2 font-mono text-[0.75rem] text-[var(--purple)] md:inline-flex"
            style={{
              border: '1px solid color-mix(in oklab, var(--purple) 22%, var(--line))',
              background: 'var(--purple-soft)',
            }}
          >
            <span style={{ letterSpacing: '0.08em', textTransform: 'uppercase', fontSize: 10 }}>Claude</span>
            <span>{formatBudgetUsd(claudeBudget.remaining_usd)} of {formatUsd(claudeBudget.limit_usd)}</span>
          </div>
        ) : null}

        <div
          className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 font-mono text-[0.84375rem] font-medium ${
            timeUrgencyLevel === 'danger' || isTimeLow
              ? 'border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] text-[var(--taali-danger)]'
              : timeUrgencyLevel === 'warning'
                ? 'border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] text-[var(--taali-warning)]'
                : 'border-[var(--line)] bg-[var(--bg-2)] text-[var(--ink)]'
          }`}
        >
          <span className={`h-[0.4375rem] w-[0.4375rem] rounded-full ${timeUrgencyLevel === 'danger' || isTimeLow ? 'bg-[var(--taali-danger)]' : 'bg-[var(--purple)]'}`} />
          <Clock size={13} />
          <span>{formatTime(timeLeft)} left</span>
          {isTimerPaused ? <span className="text-[0.625rem] uppercase tracking-[0.08em]">Paused</span> : null}
        </div>

        <button
          type="button"
          onClick={onSubmit}
          disabled={isTimerPaused}
          className="inline-flex items-center gap-2 rounded-full bg-[var(--ink)] px-4 py-2 text-[0.8125rem] font-medium text-[var(--bg)] transition-colors hover:bg-[var(--purple)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          Submit
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  </header>
);
