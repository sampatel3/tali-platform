import React from 'react';
import { AlertTriangle, ChevronRight, CircleHelp, Clock } from 'lucide-react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

const HelpPillButton = ({ as: Component = 'button', children, className = '', ...props }) => (
  <Component
    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-medium text-[var(--mute)] transition-colors hover:bg-[var(--purple-soft)] hover:text-[var(--purple)] ${className}`.trim()}
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
    <div className="flex min-h-[68px] flex-wrap items-center justify-between gap-3 px-4 py-3 lg:px-8">
      <div className="min-w-0 flex items-center gap-4">
        <div className="flex items-center gap-3">
          <AssessmentBrandGlyph variant="compact-square" sizeClass="h-[30px] w-[30px]" markSizeClass="h-5 w-5" />
          <span className="hidden h-[22px] w-px bg-[var(--line)] sm:block" />
        </div>
        <div className="min-w-0 leading-tight">
          <div className="truncate font-display text-[17px] font-semibold tracking-[-0.01em] text-[var(--ink)]">
            {taskName}
          </div>
          <div className="mt-1 truncate font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">
            {metaLine || 'Candidate assessment'}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-3">
        <div className="hidden items-center rounded-full border border-[var(--line)] bg-[var(--bg-2)] p-[3px] shadow-[var(--shadow-sm)] lg:inline-flex">
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
            className="hidden items-center gap-2 rounded-full px-3.5 py-2 font-mono text-[12px] text-[color-mix(in_oklab,var(--amber)_60%,var(--ink))] md:inline-flex"
            style={{
              border: '1px solid color-mix(in oklab, var(--peach) 80%, var(--line))',
              background: 'color-mix(in oklab, var(--peach) 55%, transparent)',
            }}
          >
            <span>Claude</span>
            <span>{formatBudgetUsd(claudeBudget.remaining_usd)} of {formatUsd(claudeBudget.limit_usd)}</span>
          </div>
        ) : null}

        <div
          className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 font-mono text-[13.5px] font-medium ${
            timeUrgencyLevel === 'danger' || isTimeLow
              ? 'border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] text-[var(--taali-danger)]'
              : timeUrgencyLevel === 'warning'
                ? 'border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] text-[var(--taali-warning)]'
                : 'border-[var(--line)] bg-[var(--bg-2)] text-[var(--ink)]'
          }`}
        >
          <span className={`h-[7px] w-[7px] rounded-full ${timeUrgencyLevel === 'danger' || isTimeLow ? 'bg-[var(--taali-danger)]' : 'bg-[var(--purple)]'}`} />
          <Clock size={13} />
          <span>{formatTime(timeLeft)} left</span>
          {isTimerPaused ? <span className="text-[10px] uppercase tracking-[0.08em]">Paused</span> : null}
        </div>

        <button
          type="button"
          onClick={onSubmit}
          disabled={isTimerPaused}
          className="inline-flex items-center gap-2 rounded-full bg-[var(--ink)] px-4 py-2 text-[13px] font-medium text-[var(--bg)] transition-colors hover:bg-[var(--purple)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          Submit
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  </header>
);
