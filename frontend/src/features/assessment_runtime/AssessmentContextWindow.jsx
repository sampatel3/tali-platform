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
    <div className={`${lightMode ? 'border-b border-gray-200 bg-gray-50' : 'border-b border-white/10 bg-[#0f131b]'}`}>
      <button
        type="button"
        className={`w-full px-4 py-2.5 flex items-center justify-between font-mono text-xs font-bold uppercase tracking-wide ${lightMode ? 'text-gray-700 hover:bg-gray-100' : 'text-gray-300 hover:bg-white/5'}`}
        onClick={() => toggleSection('contextWindow')}
      >
        <span>Context Window</span>
        {collapsedSections.contextWindow ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
      </button>

      {!collapsedSections.contextWindow && (
        <div className={`p-4 border-t max-h-[34vh] overflow-y-auto ${lightMode ? 'border-gray-200' : 'border-white/10'}`}>
          <div className="grid gap-3 md:grid-cols-2">
            <div className={`${lightMode ? 'border border-gray-200 bg-white' : 'border border-white/12 bg-[#111726]'}`}>
              <button
                type="button"
                className={`w-full px-3 py-2 flex items-center justify-between font-mono text-xs font-bold uppercase tracking-wide ${lightMode ? 'text-gray-700 hover:bg-gray-50' : 'text-gray-300 hover:bg-white/5'}`}
                onClick={() => toggleSection('taskContext')}
              >
                <span>Task Context</span>
                {collapsedSections.taskContext ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              </button>
              {!collapsedSections.taskContext && (
                <div className={`border-t px-3 py-2 ${lightMode ? 'border-gray-200' : 'border-white/10'}`}>
                  <div className="max-h-32 overflow-y-auto pr-1">
                    <p className={`font-mono text-sm whitespace-pre-wrap ${lightMode ? 'text-gray-700' : 'text-gray-200'}`}>
                      {taskContext || 'Task context has not been provided yet.'}
                    </p>
                  </div>
                </div>
              )}
            </div>

            <div className={`${lightMode ? 'border border-gray-200 bg-white' : 'border border-white/12 bg-[#111726]'}`}>
              <button
                type="button"
                className={`w-full px-3 py-2 flex items-center justify-between font-mono text-xs font-bold uppercase tracking-wide ${lightMode ? 'text-gray-700 hover:bg-gray-50' : 'text-gray-300 hover:bg-white/5'}`}
                onClick={() => toggleSection('instructions')}
              >
                <span>Instructions</span>
                {collapsedSections.instructions ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              </button>
              {!collapsedSections.instructions && (
                <div className={`border-t px-3 py-2 ${lightMode ? 'border-gray-200' : 'border-white/10'}`}>
                  <div className="max-h-32 overflow-y-auto pr-1">
                    <ul className={`list-disc space-y-1 pl-4 font-mono text-xs ${lightMode ? 'text-gray-700' : 'text-gray-300'}`}>
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
                    <div className={`font-mono text-[11px] mt-2 break-all ${lightMode ? 'text-gray-600' : 'text-gray-400'}`}>
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
