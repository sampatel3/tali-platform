import React from 'react';
import { Clock } from 'lucide-react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';
import { ThemeModeToggle } from '../../shared/ui/ThemeModeToggle';

export const AssessmentTopBar = ({
  brandName,
  taskName,
  claudeBudget,
  aiMode,
  terminalCapabilities,
  formatUsd,
  isTimeLow,
  timeUrgencyLevel = 'normal',
  timeLeft,
  formatTime,
  isTimerPaused,
  lightMode = false,
  onToggleTheme,
  onSubmit,
}) => (
  <div className="border-b border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] px-4 py-3 backdrop-blur-sm">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0 flex items-center gap-3">
        <AssessmentBrandGlyph sizeClass="w-7 h-7" markSizeClass="w-5 h-5" />
        <div className="min-w-0">
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--taali-runtime-muted)]">
            {brandName}
          </div>
          <div className="truncate font-mono text-sm text-[var(--taali-runtime-text)]">
            {taskName}
          </div>
        </div>
        <span className="hidden rounded-full border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-[var(--taali-runtime-muted)] md:inline-flex">
          AI: {aiMode === 'claude_cli_terminal' ? 'Claude CLI' : 'Claude Chat'}
        </span>
        {aiMode === 'claude_cli_terminal' ? (
          <span className="hidden rounded-full border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-[var(--taali-runtime-muted)] lg:inline-flex">
            Permission: {terminalCapabilities?.permission_mode || 'default'}
          </span>
        ) : null}
      </div>

      <div className="flex items-center gap-2 sm:gap-3">
        <ThemeModeToggle
          value={lightMode ? 'light' : 'dark'}
          onChange={(nextValue) => {
            const shouldBeLight = nextValue === 'light';
            if (shouldBeLight !== lightMode) {
              onToggleTheme?.();
            }
          }}
          ariaLabel={`Assessment runtime theme. Current mode is ${lightMode ? 'light' : 'dark'}.`}
          title={`Switch to ${lightMode ? 'dark' : 'light'} UI`}
          className="shrink-0"
        />
        {claudeBudget?.enabled && (
          <div className="hidden rounded-full border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] px-3 py-1.5 font-mono text-xs text-[var(--taali-warning)] sm:block">
            Claude Credit: {formatUsd(claudeBudget.remaining_usd)} left of {formatUsd(claudeBudget.limit_usd)}
          </div>
        )}
        <div
          className={`flex items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-xs font-bold ${
            timeUrgencyLevel === 'danger' || isTimeLow
              ? 'border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] text-[var(--taali-danger)]'
              : timeUrgencyLevel === 'warning'
                ? 'border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] text-[var(--taali-warning)]'
                : 'border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] text-[var(--taali-runtime-text)]'
          }`}
        >
          <Clock size={14} />
          <span>{formatTime(timeLeft)}</span>
          {isTimerPaused && <span className="text-[10px] uppercase tracking-wide">Paused</span>}
        </div>
        <button
          onClick={onSubmit}
          disabled={isTimerPaused}
          className="rounded-[var(--taali-radius-control)] border border-[var(--taali-purple)] bg-[var(--taali-purple)] px-4 py-1.5 font-mono text-xs font-bold text-white transition-colors hover:bg-[var(--taali-purple-hover)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          Submit
        </button>
      </div>
    </div>
  </div>
);
