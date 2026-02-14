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
}) => (
  <>
    {showTabWarning && (
      <div className="fixed top-4 right-4 z-50 border-2 border-red-500 bg-red-50 p-4 shadow-lg">
        <div className="font-mono text-sm text-red-700 font-bold">
          You have left the assessment tab.
        </div>
        <div className="font-mono text-xs text-red-600">
          This has been recorded.
        </div>
      </div>
    )}

    {proctoringEnabled && (
      <div className="border-b-2 border-black bg-yellow-50 p-2 text-center">
        <span className="font-mono text-xs text-yellow-800 font-bold">
          ⚠ This assessment is proctored — tab switches and browser focus are
          being recorded
        </span>
      </div>
    )}

    {isTimerPaused && (
      <div className="border-b-2 border-black bg-red-50 px-4 py-2 flex items-center justify-between gap-3">
        <div className="font-mono text-xs text-red-700">
          Assessment paused: Claude is currently unavailable{pauseReason ? ` (${pauseReason})` : ''}.
          {pauseMessage ? ` ${pauseMessage}` : ''}
        </div>
        <button
          type="button"
          className="border-2 border-black px-3 py-1 font-mono text-xs font-bold bg-white hover:bg-black hover:text-white disabled:opacity-60"
          onClick={onRetryClaude}
          disabled={retryingClaude}
        >
          {retryingClaude ? 'Retrying...' : 'Retry Claude'}
        </button>
      </div>
    )}

    {isClaudeBudgetExhausted && (
      <div className="border-b-2 border-black bg-amber-50 px-4 py-2">
        <div className="font-mono text-xs text-amber-800">
          Claude budget exhausted for this task
          {claudeBudget?.limit_usd ? ` (${formatUsd(claudeBudget.limit_usd)} cap reached)` : ''}.
          Continue coding and submit when ready.
        </div>
      </div>
    )}
  </>
);
