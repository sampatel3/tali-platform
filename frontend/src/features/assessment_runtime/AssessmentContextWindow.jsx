import React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

export const AssessmentContextWindow = ({
  collapsedSections,
  toggleSection,
  taskContext,
  rubricCategories,
  cloneCommand,
  repoFiles,
  selectedRepoPath,
  selectedRepoContent,
  onSelectRepoFile,
}) => (
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
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
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
              onClick={() => toggleSection('rubric')}
            >
              <span>How you'll be assessed</span>
              {collapsedSections.rubric ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
            </button>
            {!collapsedSections.rubric && (
              <div className="border-t border-gray-200 px-3 py-2">
                <div className="max-h-32 overflow-y-auto pr-1">
                  {rubricCategories.length === 0 ? (
                    <p className="font-mono text-xs text-gray-600">Rubric categories will be shown when available.</p>
                  ) : (
                    <ul className="font-mono text-xs text-gray-700 space-y-1">
                      {rubricCategories.map((item) => (
                        <li key={item.category} className="flex justify-between gap-3">
                          <span className="truncate">{String(item.category || '').replace(/_/g, ' ')}</span>
                          <span>{Math.round((Number(item.weight || 0) * 100))}%</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {cloneCommand && (
                  <div className="font-mono text-[11px] text-gray-600 mt-2 break-all">
                    Workspace clone command: <code>{cloneCommand}</code>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="border border-black bg-white">
            <button
              type="button"
              className="w-full px-3 py-2 flex items-center justify-between font-mono text-xs font-bold text-gray-700 hover:bg-gray-100"
              onClick={() => toggleSection('repoContext')}
            >
              <span>Repository Context</span>
              {collapsedSections.repoContext ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
            </button>
            {!collapsedSections.repoContext && (
              <div className="border-t border-gray-200 px-3 py-2">
                {repoFiles.length === 0 ? (
                  <p className="font-mono text-xs text-gray-600">No repository files provided for this assessment.</p>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-2 mb-2 max-h-16 overflow-auto pr-1">
                      {repoFiles.map((file) => (
                        <button
                          key={file.path}
                          type="button"
                          className={`border px-2 py-1 font-mono text-xs ${selectedRepoPath === file.path ? 'border-black bg-black text-white' : 'border-gray-400 bg-white'}`}
                          onClick={() => onSelectRepoFile(file.path)}
                        >
                          {file.path}
                        </button>
                      ))}
                    </div>
                    <pre className="bg-black text-gray-200 p-2 text-xs overflow-auto max-h-32 border-2 border-black">
                      {selectedRepoContent || 'No file content available.'}
                    </pre>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    )}
  </div>
);
