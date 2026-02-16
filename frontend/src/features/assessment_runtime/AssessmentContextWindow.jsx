import React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

export const AssessmentContextWindow = ({
  collapsedSections,
  toggleSection,
  taskContext,
  aiMode,
  cloneCommand,
}) => {
  const terminalModeEnabled = aiMode === 'claude_cli_terminal';

  return (
    <div className="border-b-2 border-black bg-gray-50">
      <button
        type="button"
        className="w-full px-4 py-2.5 flex items-center justify-between font-mono text-xs font-bold text-gray-700 hover:bg-gray-100"
        onClick={() => toggleSection('contextWindow')}
      >
        <span>Context Window</span>
        {collapsedSections.contextWindow ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
      </button>

      {!collapsedSections.contextWindow && (
        <div className="p-4 border-t border-gray-200 max-h-[34vh] overflow-y-auto">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="border border-black bg-white">
              <button
                type="button"
                className="w-full px-3 py-2 flex items-center justify-between font-mono text-xs font-bold text-gray-700 hover:bg-gray-100"
                onClick={() => toggleSection('taskContext')}
              >
                <span>Task Context</span>
                {collapsedSections.taskContext ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              </button>
              {!collapsedSections.taskContext && (
                <div className="border-t border-gray-200 px-3 py-2">
                  <div className="max-h-32 overflow-y-auto pr-1">
                    <p className="font-mono text-sm text-gray-700 whitespace-pre-wrap">
                      {taskContext || 'Task context has not been provided yet.'}
                    </p>
                  </div>
                </div>
              )}
            </div>

            <div className="border border-black bg-white">
              <button
                type="button"
                className="w-full px-3 py-2 flex items-center justify-between font-mono text-xs font-bold text-gray-700 hover:bg-gray-100"
                onClick={() => toggleSection('instructions')}
              >
                <span>Instructions</span>
                {collapsedSections.instructions ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              </button>
              {!collapsedSections.instructions && (
                <div className="border-t border-gray-200 px-3 py-2">
                  <div className="max-h-32 overflow-y-auto pr-1">
                    <ul className="list-disc space-y-1 pl-4 font-mono text-xs text-gray-700">
                      <li>Read the task context and inspect repository files before editing.</li>
                      <li>
                        {terminalModeEnabled
                          ? 'Use Claude CLI in the terminal for guidance (example: claude "review this repo and suggest a fix plan").'
                          : 'Use the Claude chat panel for focused guidance as you work through the task.'}
                      </li>
                      <li>Run relevant validation commands or tests before submitting.</li>
                      <li>Summarize what you changed, why you changed it, and how you verified it.</li>
                    </ul>
                  </div>
                  {cloneCommand && (
                    <div className="font-mono text-[11px] text-gray-600 mt-2 break-all">
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
