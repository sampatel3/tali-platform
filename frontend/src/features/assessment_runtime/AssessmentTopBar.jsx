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
  lightMode = false,
  onToggleTheme,
  onSubmit,
}) => (
  <div className={`${lightMode ? 'border-b border-gray-200 bg-white' : 'border-b border-white/10 bg-[#0c1016]'} px-4 py-2.5`}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0 flex items-center gap-3">
        <AssessmentBrandGlyph sizeClass="w-7 h-7" markSizeClass="w-5 h-5" />
        <div className="min-w-0">
          <div className={`font-mono text-[10px] uppercase tracking-[0.2em] ${lightMode ? 'text-gray-500' : 'text-gray-500'}`}>
            {brandName}
          </div>
          <div className={`font-mono text-sm truncate ${lightMode ? 'text-gray-900' : 'text-gray-100'}`}>
            {taskName}
          </div>
        </div>
        <span className={`hidden md:inline-flex border px-2 py-1 font-mono text-[10px] uppercase tracking-wide ${lightMode ? 'border-gray-300 bg-gray-50 text-gray-700' : 'border-white/15 bg-[#111827] text-gray-300'}`}>
          AI: {aiMode === 'claude_cli_terminal' ? 'Claude CLI' : 'Claude Chat'}
        </span>
        {aiMode === 'claude_cli_terminal' ? (
          <span className={`hidden lg:inline-flex border px-2 py-1 font-mono text-[10px] uppercase tracking-wide ${lightMode ? 'border-gray-300 bg-gray-50 text-gray-600' : 'border-white/10 bg-[#0f172a] text-gray-400'}`}>
            Permission: {terminalCapabilities?.permission_mode || 'default'}
          </span>
        ) : null}
      </div>

      <div className="flex items-center gap-2 sm:gap-3">
        <button
          type="button"
          onClick={onToggleTheme}
          className={`border px-2 py-1 font-mono text-[11px] ${lightMode ? 'border-gray-300 bg-gray-50 text-gray-700 hover:bg-gray-100' : 'border-white/20 bg-[#111827] text-gray-300 hover:border-[var(--taali-purple)] hover:text-[var(--taali-purple)]'}`}
        >
          {lightMode ? 'Dark UI' : 'Light UI'}
        </button>
        {claudeBudget?.enabled && (
          <div className={`hidden sm:block border px-3 py-1.5 font-mono text-xs ${lightMode ? 'border-amber-300 bg-amber-50 text-amber-700' : 'border-amber-500/40 bg-amber-500/10 text-amber-200'}`}>
            Claude Credit: {formatUsd(claudeBudget.remaining_usd)} left of {formatUsd(claudeBudget.limit_usd)}
          </div>
        )}
        <div
          className={`flex items-center gap-2 border px-3 py-1.5 font-mono text-xs font-bold ${isTimeLow
            ? (lightMode ? 'border-red-300 bg-red-50 text-red-700' : 'border-red-500/60 bg-red-500/20 text-red-200')
            : (lightMode ? 'border-gray-300 bg-gray-50 text-gray-700' : 'border-white/15 bg-[#111827] text-gray-200')
          }`}
        >
          <Clock size={14} />
          <span>{formatTime(timeLeft)}</span>
          {isTimerPaused && <span className="text-[10px] uppercase tracking-wide">Paused</span>}
        </div>
        <button
          onClick={onSubmit}
          disabled={isTimerPaused}
          className="border border-[var(--taali-purple)] bg-[var(--taali-purple)] px-4 py-1.5 font-mono text-xs font-bold text-white transition-colors hover:bg-[#aa4dff] disabled:cursor-not-allowed disabled:opacity-50"
        >
          Submit
        </button>
      </div>
    </div>
  </div>
);
