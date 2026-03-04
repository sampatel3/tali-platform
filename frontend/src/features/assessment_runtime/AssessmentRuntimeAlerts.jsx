import React from 'react';

export const AssessmentRuntimeAlerts = ({
  showTabWarning,
  proctoringEnabled,
  isTimerPaused,
  pauseReason,
  pauseMessage,
  onRetryClaude,
  retryingClaude,
  isClaudeBudgetExhausted,
  claudeBudget,
  formatUsd,
  timeMilestoneNotice = null,
}) => (
  <>
    {showTabWarning && (
      <div className="fixed right-4 top-4 z-50 rounded-[var(--taali-radius-card)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 shadow-[var(--taali-shadow-strong)]">
        <div className="font-mono text-sm font-bold text-[var(--taali-danger)]">
          You have left the assessment tab.
        </div>
        <div className="font-mono text-xs text-[var(--taali-danger)]">
          This has been recorded.
        </div>
      </div>
    )}

    {proctoringEnabled && (
      <div className="border-b border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-2 text-center">
        <span className="font-mono text-xs font-bold text-[var(--taali-warning)]">
          ⚠ This assessment is proctored — tab switches and browser focus are being recorded
        </span>
      </div>
    )}

    {timeMilestoneNotice?.message ? (
      <div
        className={`border-b px-4 py-2 ${
          timeMilestoneNotice.tone === 'danger'
            ? 'border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)]'
            : timeMilestoneNotice.tone === 'warning'
              ? 'border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)]'
              : 'border-[var(--taali-info-border)] bg-[var(--taali-info-soft)]'
        }`}
      >
        <div
          className={`font-mono text-xs font-bold ${
            timeMilestoneNotice.tone === 'danger'
              ? 'text-[var(--taali-danger)]'
              : timeMilestoneNotice.tone === 'warning'
                ? 'text-[var(--taali-warning)]'
                : 'text-[var(--taali-info)]'
          }`}
        >
          {timeMilestoneNotice.message}
        </div>
      </div>
    ) : null}

    {isTimerPaused && (
      <div className="flex items-center justify-between gap-3 border-b border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-4 py-2">
        <div className="font-mono text-xs text-[var(--taali-danger)]">
          Assessment paused{pauseReason ? ` (${pauseReason})` : ''}.
          {pauseMessage ? ` ${pauseMessage}` : ''}
        </div>
        {onRetryClaude && (
          <button
            type="button"
            className="rounded-[var(--taali-radius-control)] border border-[var(--taali-danger)] px-3 py-1 font-mono text-xs font-bold text-[var(--taali-danger)] transition-colors hover:bg-[var(--taali-danger-soft)] disabled:opacity-60"
            onClick={onRetryClaude}
            disabled={retryingClaude}
          >
            {retryingClaude ? 'Retrying...' : 'Retry Claude'}
          </button>
        )}
      </div>
    )}

    {isClaudeBudgetExhausted && (
      <div className="border-b border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] px-4 py-2">
        <div className="font-mono text-xs text-[var(--taali-warning)]">
          Claude budget exhausted for this task
          {claudeBudget?.limit_usd ? ` (${formatUsd(claudeBudget.limit_usd)} cap reached)` : ''}.
          Continue coding and submit when ready.
        </div>
      </div>
    )}
  </>
);
