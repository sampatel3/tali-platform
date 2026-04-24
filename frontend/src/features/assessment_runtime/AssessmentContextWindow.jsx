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
    <section className="mx-6 mt-4 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-5 py-5 shadow-[var(--shadow-sm)]">
      <div className="grid gap-4 xl:grid-cols-[auto_minmax(0,1fr)_auto] xl:items-start">
        <div className="inline-flex h-fit items-center rounded-full bg-[var(--purple-soft)] px-3 py-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.12em] text-[var(--purple)]">
          Task 01 / 01
        </div>

        <div className="min-w-0">
          <h2 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em] text-[var(--ink)]">
            Live assessment context
          </h2>
          <p className="mt-1 max-w-[920px] text-[13.5px] leading-7 text-[var(--ink-2)]">
            {taskContext || 'Task instructions will appear here when the assessment session starts.'}
          </p>
        </div>

        <div className="inline-flex h-fit gap-1 rounded-full border border-[var(--line)] bg-[var(--bg)] p-1">
          <button
            type="button"
            className={`rounded-full px-3 py-1.5 text-[12px] font-medium ${!collapsedSections.taskContext ? 'bg-[var(--ink)] text-[var(--bg)]' : 'text-[var(--mute)] hover:text-[var(--ink)]'}`}
            onClick={() => toggleSection('taskContext')}
          >
            Context
          </button>
          <button
            type="button"
            className={`rounded-full px-3 py-1.5 text-[12px] font-medium ${!collapsedSections.instructions ? 'bg-[var(--ink)] text-[var(--bg)]' : 'text-[var(--mute)] hover:text-[var(--ink)]'}`}
            onClick={() => toggleSection('instructions')}
          >
            Instructions
          </button>
          <button
            type="button"
            className="rounded-full px-3 py-1.5 text-[12px] font-medium text-[var(--mute)] hover:text-[var(--ink)]"
            onClick={() => toggleSection('contextWindow')}
          >
            {collapsedSections.contextWindow ? (
              <span className="inline-flex items-center gap-1.5"><ChevronRight size={13} /> Show details</span>
            ) : (
              <span className="inline-flex items-center gap-1.5"><ChevronDown size={13} /> Hide details</span>
            )}
          </button>
        </div>
      </div>

      {!collapsedSections.contextWindow ? (
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {!collapsedSections.taskContext ? (
            <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-4 py-4">
              <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">
                Task context
              </div>
              <div className="mt-3 max-h-[220px] overflow-y-auto pr-1">
                <p className="whitespace-pre-wrap text-[13.5px] leading-7 text-[var(--ink-2)]">
                  {taskContext || 'Task context has not been provided yet.'}
                </p>
              </div>
            </div>
          ) : null}

          {!collapsedSections.instructions ? (
            <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-4 py-4">
              <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">
                Working rules
              </div>
              <ul className="mt-3 list-disc space-y-2 pl-5 text-[13px] leading-6 text-[var(--ink-2)]">
                <li>Inspect the repo and task contract before making broad changes.</li>
                <li>
                  {terminalModeEnabled
                    ? 'Use Claude for focused help, then validate changes with the terminal and test loop.'
                    : 'Use the Claude chat panel for focused help, then validate changes before you submit.'}
                </li>
                <li>Keep your final code in the workspace before you press submit.</li>
              </ul>
              {cloneCommand ? (
                <div className="mt-4 rounded-[10px] border border-[var(--line)] bg-[var(--bg-2)] px-3 py-2 font-[var(--font-mono)] text-[11px] leading-5 text-[var(--mute)]">
                  Clone command: <code>{cloneCommand}</code>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};
