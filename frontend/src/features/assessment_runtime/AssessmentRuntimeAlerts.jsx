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
  lightMode = false,
}) => (
  <>
    {showTabWarning && (
      <div className={`fixed top-4 right-4 z-50 border p-4 shadow-lg ${lightMode ? 'border-red-300 bg-red-50' : 'border-red-500/70 bg-[#2a1212]'}`}>
        <div className={`font-mono text-sm font-bold ${lightMode ? 'text-red-700' : 'text-red-200'}`}>
          You have left the assessment tab.
        </div>
        <div className={`font-mono text-xs ${lightMode ? 'text-red-600' : 'text-red-300'}`}>
          This has been recorded.
        </div>
      </div>
    )}

    {proctoringEnabled && (
      <div className={`border-b p-2 text-center ${lightMode ? 'border-amber-300 bg-amber-50' : 'border-amber-500/30 bg-amber-500/10'}`}>
        <span className={`font-mono text-xs font-bold ${lightMode ? 'text-amber-700' : 'text-amber-200'}`}>
          ⚠ This assessment is proctored — tab switches and browser focus are
          being recorded
        </span>
      </div>
    )}

    {timeMilestoneNotice?.message ? (
      <div
        className={`border-b px-4 py-2 ${
          timeMilestoneNotice.tone === 'danger'
            ? (lightMode ? 'border-red-300 bg-red-50' : 'border-red-500/40 bg-red-500/10')
            : timeMilestoneNotice.tone === 'warning'
              ? (lightMode ? 'border-amber-300 bg-amber-50' : 'border-amber-500/40 bg-amber-500/10')
              : (lightMode ? 'border-blue-300 bg-blue-50' : 'border-blue-500/40 bg-blue-500/10')
        }`}
      >
        <div
          className={`font-mono text-xs font-bold ${
            timeMilestoneNotice.tone === 'danger'
              ? (lightMode ? 'text-red-700' : 'text-red-200')
              : timeMilestoneNotice.tone === 'warning'
                ? (lightMode ? 'text-amber-700' : 'text-amber-200')
                : (lightMode ? 'text-blue-700' : 'text-blue-200')
          }`}
        >
          {timeMilestoneNotice.message}
        </div>
      </div>
    ) : null}

    {isTimerPaused && (
      <div className={`border-b px-4 py-2 flex items-center justify-between gap-3 ${lightMode ? 'border-red-300 bg-red-50' : 'border-red-500/40 bg-red-500/10'}`}>
        <div className={`font-mono text-xs ${lightMode ? 'text-red-700' : 'text-red-200'}`}>
          Assessment paused{pauseReason ? ` (${pauseReason})` : ''}.
          {pauseMessage ? ` ${pauseMessage}` : ''}
        </div>
        {onRetryClaude && (
          <button
            type="button"
            className={`border px-3 py-1 font-mono text-xs font-bold disabled:opacity-60 ${lightMode ? 'border-red-400 text-red-700 hover:bg-red-100' : 'border-red-400 text-red-100 hover:bg-red-500/20'}`}
            onClick={onRetryClaude}
            disabled={retryingClaude}
          >
            {retryingClaude ? 'Retrying...' : 'Retry Claude'}
          </button>
        )}
      </div>
    )}

    {isClaudeBudgetExhausted && (
      <div className={`border-b px-4 py-2 ${lightMode ? 'border-amber-300 bg-amber-50' : 'border-amber-500/40 bg-amber-500/10'}`}>
        <div className={`font-mono text-xs ${lightMode ? 'text-amber-700' : 'text-amber-200'}`}>
          Claude budget exhausted for this task
          {claudeBudget?.limit_usd ? ` (${formatUsd(claudeBudget.limit_usd)} cap reached)` : ''}.
          Continue coding and submit when ready.
        </div>
      </div>
    )}
  </>
);
