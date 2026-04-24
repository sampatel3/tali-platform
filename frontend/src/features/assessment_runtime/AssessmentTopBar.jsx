import React from 'react';
import { Clock, Moon, Sun } from 'lucide-react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

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
  <div className={`${lightMode ? 'sticky top-0 z-30 border-b border-[var(--line)] bg-[color:color-mix(in_oklab,var(--bg)_88%,transparent)] backdrop-blur' : 'sticky top-0 z-30 border-b border-white/10 bg-[#0c1016]/90 backdrop-blur'} px-6 py-3`}>
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="min-w-0 flex items-center gap-4">
        <AssessmentBrandGlyph sizeClass="h-8 w-8" markSizeClass="h-5 w-5" />
        <div className={`${lightMode ? 'h-6 w-px bg-[var(--line)]' : 'h-6 w-px bg-white/10'}`} />
        <div className="min-w-0">
          <div className={`truncate font-[var(--font-display)] text-[17px] font-semibold tracking-[-0.01em] ${lightMode ? 'text-[var(--ink)]' : 'text-gray-100'}`}>
            {taskName}
          </div>
          <div className={`mt-1 truncate font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] ${lightMode ? 'text-[var(--mute)]' : 'text-gray-500'}`}>
            {brandName} · {aiMode === 'claude_cli_terminal' ? 'Claude CLI' : 'Claude Chat'}
            {aiMode === 'claude_cli_terminal' ? ` · ${terminalCapabilities?.permission_mode || 'default'} permissions` : ''}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        {typeof onToggleTheme === 'function' ? (
          <button
            type="button"
            onClick={onToggleTheme}
            className={`inline-flex items-center gap-2 rounded-full px-3 py-2 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] ${lightMode ? 'border border-[var(--line)] bg-[var(--bg-2)] text-[var(--mute)] hover:text-[var(--ink)]' : 'border border-white/15 bg-[#111827] text-gray-300 hover:text-white'}`}
          >
            {lightMode ? <Moon size={12} /> : <Sun size={12} />}
            {lightMode ? 'Dark UI' : 'Light UI'}
          </button>
        ) : null}

        {claudeBudget?.enabled ? (
          <div className={`inline-flex items-center gap-2 rounded-full px-3 py-2 font-[var(--font-mono)] text-[12px] ${lightMode ? 'border border-[color:color-mix(in_oklab,var(--peach)_80%,var(--line))] bg-[color:color-mix(in_oklab,var(--peach)_55%,transparent)] text-[#8A4A13]' : 'border border-amber-500/30 bg-amber-500/10 text-amber-200'}`}>
            Claude {formatUsd(claudeBudget.remaining_usd)} of {formatUsd(claudeBudget.limit_usd)}
          </div>
        ) : null}

        <div
          className={`inline-flex items-center gap-2 rounded-full px-4 py-2 font-[var(--font-mono)] text-[13px] font-medium ${
            timeUrgencyLevel === 'danger' || isTimeLow
              ? (lightMode ? 'border border-red-300 bg-red-50 text-red-700' : 'border-red-500/60 bg-red-500/20 text-red-200')
              : timeUrgencyLevel === 'warning'
                ? (lightMode ? 'border border-amber-300 bg-amber-50 text-amber-700' : 'border-amber-500/60 bg-amber-500/20 text-amber-200')
                : (lightMode ? 'border border-[var(--line)] bg-[var(--bg-2)] text-[var(--ink)]' : 'border border-white/15 bg-[#111827] text-gray-200')
          }`}
        >
          <span className={`h-2 w-2 rounded-full ${lightMode ? 'bg-red-500' : 'bg-red-400'} animate-pulse`} />
          <Clock size={14} />
          <span>{formatTime(timeLeft)} left</span>
          {isTimerPaused ? <span className="text-[10px] uppercase tracking-[0.08em]">Paused</span> : null}
        </div>

        <button
          type="button"
          onClick={onSubmit}
          disabled={isTimerPaused}
          className="btn btn-primary btn-sm disabled:cursor-not-allowed disabled:opacity-50"
        >
          Submit
        </button>
      </div>
    </div>
  </div>
);
