import React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

export const AssessmentContextWindow = ({
  collapsedSections,
  toggleSection,
  taskContext,
  aiMode,
  cloneCommand,
  lightMode = false,
}) => {
  const terminalModeEnabled = aiMode === 'claude_cli_terminal';

  return (
    <div className="border-b border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)]">
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-2.5 font-mono text-xs font-bold uppercase tracking-wide text-[var(--taali-runtime-text)] transition-colors hover:bg-[var(--taali-surface-hover)]"
        onClick={() => toggleSection('contextWindow')}
      >
        <span>Context Window</span>
        {collapsedSections.contextWindow ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
      </button>

      {!collapsedSections.contextWindow && (
        <div className="max-h-[34vh] overflow-y-auto border-t border-[var(--taali-runtime-border)] p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)]">
              <button
                type="button"
                className="flex w-full items-center justify-between px-3 py-2 font-mono text-xs font-bold uppercase tracking-wide text-[var(--taali-runtime-text)] transition-colors hover:bg-[var(--taali-surface-hover)]"
                onClick={() => toggleSection('taskContext')}
              >
                <span>Task Context</span>
                {collapsedSections.taskContext ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              </button>
              {!collapsedSections.taskContext && (
                <div className="border-t border-[var(--taali-runtime-border)] px-3 py-2">
                  <div className="max-h-32 overflow-y-auto pr-1">
                    <p className="whitespace-pre-wrap font-mono text-sm text-[var(--taali-runtime-text)]">
                      {taskContext || 'Task context has not been provided yet.'}
                    </p>
                  </div>
                </div>
              )}
            </div>

            <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)]">
              <button
                type="button"
                className="flex w-full items-center justify-between px-3 py-2 font-mono text-xs font-bold uppercase tracking-wide text-[var(--taali-runtime-text)] transition-colors hover:bg-[var(--taali-surface-hover)]"
                onClick={() => toggleSection('instructions')}
              >
                <span>Instructions</span>
                {collapsedSections.instructions ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              </button>
              {!collapsedSections.instructions && (
                <div className="border-t border-[var(--taali-runtime-border)] px-3 py-2">
                  <div className="max-h-32 overflow-y-auto pr-1">
                    <ul className="list-disc space-y-1 pl-4 font-mono text-xs text-[var(--taali-runtime-text)]">
                      <li>Read the task context and inspect repository files before editing.</li>
                      <li>
                        {terminalModeEnabled
                          ? 'Use the Ask Claude box for Cursor-style help with repo context, or use terminal commands if needed.'
                          : 'Use the Claude chat panel for focused guidance as you work through the task.'}
                      </li>
                      <li>Run relevant validation commands or tests before submitting.</li>
                      <li>Summarize what you changed, why you changed it, and how you verified it.</li>
                    </ul>
                  </div>
                  {cloneCommand && (
                    <div className="mt-2 break-all font-mono text-[11px] text-[var(--taali-runtime-muted)]">
                      Workspace clone command: <code>{cloneCommand}</code>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
