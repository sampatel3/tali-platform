import React from 'react';
import { Clock } from 'lucide-react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

export const AssessmentTopBar = ({
  brandName,
  taskName,
  claudeBudget,
  aiMode,
  terminalCapabilities,
  formatUsd,
  isTimeLow,
  timeLeft,
  formatTime,
  isTimerPaused,
  onSubmit,
}) => (
  <div className="border-b-2 border-black bg-white px-4 py-3 flex items-center justify-between">
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-2">
        <AssessmentBrandGlyph />
        <span className="text-lg font-bold tracking-tight">{brandName}</span>
      </div>
      <span className="font-mono text-sm text-gray-500">|</span>
      <span className="font-mono text-sm font-bold">
        {taskName}
      </span>
      <span className="border border-black px-2 py-0.5 font-mono text-[11px] uppercase">
        AI Mode: {aiMode === 'claude_cli_terminal' ? 'Claude CLI' : 'Claude Chat'}
      </span>
      {aiMode === 'claude_cli_terminal' ? (
        <span className="font-mono text-[11px] text-gray-600">
          Permission: {terminalCapabilities?.permission_mode || 'default'}
        </span>
      ) : null}
    </div>
    <div className="flex items-center gap-4">
      {claudeBudget?.enabled && (
        <div className="border-2 border-black px-3 py-1.5 font-mono text-xs bg-amber-50">
          Claude left: {formatUsd(claudeBudget.remaining_usd)} / {formatUsd(claudeBudget.limit_usd)}
        </div>
      )}
      <div
        className={`flex items-center gap-2 border-2 border-black px-4 py-1.5 font-mono text-sm font-bold ${
          isTimeLow ? 'bg-red-500 text-white border-red-600' : 'bg-white'
        }`}
      >
        <Clock size={16} />
        <span>{formatTime(timeLeft)}</span>
        {isTimerPaused && <span className="text-xs uppercase">Paused</span>}
      </div>
      <button
        onClick={onSubmit}
        disabled={isTimerPaused}
        className="border-2 border-black px-6 py-1.5 font-mono text-sm font-bold text-white hover:bg-black transition-colors"
        style={{ backgroundColor: '#9D00FF' }}
      >
        Submit
      </button>
    </div>
  </div>
);
