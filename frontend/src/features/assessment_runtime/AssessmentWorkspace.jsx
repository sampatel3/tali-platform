import React from 'react';
import { ChevronDown, ChevronRight, FileText, Folder } from 'lucide-react';

import CodeEditor from '../../components/assessment/CodeEditor';
import { AssessmentTerminal } from './AssessmentTerminal';

export const AssessmentWorkspace = ({
  hasRepoStructure,
  collapsedSections,
  toggleSection,
  repoFileTree,
  collapsedRepoDirs,
  toggleRepoDir,
  selectedRepoPath,
  onSelectRepoFile,
  assessmentStarterCode,
  editorContent,
  onEditorChange,
  onExecute,
  onSave,
  editorLanguage,
  editorFilename,
  isTimerPaused,
  showTerminal,
  terminalConnected,
  terminalEvents,
  onTerminalInput,
  onTerminalResize,
  onTerminalStop,
  terminalStopping,
  output,
  executing,
}) => (
  <div className="flex-1 flex overflow-hidden">
    <div className="w-[65%] border-r-2 border-black flex flex-col">
      <div className="flex-1 flex overflow-hidden">
        {hasRepoStructure && (
          <div className={`${collapsedSections.repoTree ? 'w-10' : 'w-52'} border-r-2 border-black bg-gray-50 flex flex-col overflow-hidden transition-all duration-150`}>
            <button
              type="button"
              className="px-2 py-2 border-b border-gray-200 font-mono text-xs font-bold text-gray-600 flex items-center gap-1.5 hover:bg-gray-100"
              onClick={() => toggleSection('repoTree')}
            >
              {collapsedSections.repoTree ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
              {!collapsedSections.repoTree && <span>Repository</span>}
            </button>
            {!collapsedSections.repoTree && (
              <div className="flex-1 overflow-y-auto py-1">
                {Object.entries(repoFileTree)
                  .sort(([a], [b]) => (a || '').localeCompare(b || ''))
                  .map(([dir, paths]) => (
                    <div key={dir || '(root)'} className="mb-1">
                      {dir ? (
                        <button
                          type="button"
                          className="w-full px-2 py-0.5 font-mono text-xs text-gray-500 flex items-center gap-0.5 hover:bg-gray-100 text-left"
                          onClick={() => toggleRepoDir(dir)}
                        >
                          {collapsedRepoDirs[dir] ? <ChevronRight size={10} /> : <ChevronDown size={10} />}
                          <Folder size={10} />
                          <span>{dir}/</span>
                        </button>
                      ) : null}
                      <div className={dir ? 'pl-3' : ''} hidden={Boolean(dir && collapsedRepoDirs[dir])}>
                        {paths.map((path) => {
                          const name = path.includes('/') ? path.slice(path.lastIndexOf('/') + 1) : path;
                          const isSelected = path === selectedRepoPath;
                          return (
                            <button
                              key={path}
                              type="button"
                              className={`w-full text-left px-2 py-1 font-mono text-xs flex items-center gap-1.5 hover:bg-gray-200 ${
                                isSelected ? 'bg-black text-white hover:bg-gray-800' : 'text-gray-800'
                              }`}
                              onClick={() => onSelectRepoFile(path)}
                            >
                              <FileText size={10} />
                              <span className="truncate">{name}</span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <CodeEditor
            initialCode={assessmentStarterCode}
            value={editorContent}
            onChange={onEditorChange}
            onExecute={onExecute}
            onSave={onSave}
            language={editorLanguage}
            filename={editorFilename}
            disabled={isTimerPaused}
          />
        </div>
      </div>
    </div>

    <div className="w-[35%] flex flex-col">
      <div className="h-[60%] border-b-2 border-black">
        {showTerminal ? (
          <AssessmentTerminal
            events={terminalEvents}
            connected={terminalConnected}
            disabled={isTimerPaused}
            onInput={onTerminalInput}
            onResize={onTerminalResize}
            onStop={onTerminalStop}
            stopping={terminalStopping}
          />
        ) : (
          <div className="h-full bg-gray-50 border-l-2 border-black p-4 flex items-center justify-center">
            <div className="border-2 border-red-400 bg-red-50 p-4 max-w-sm text-center">
              <div className="font-mono text-sm font-bold text-red-700 mb-1">Terminal runtime unavailable</div>
              <div className="font-mono text-xs text-red-700">
                This assessment requires Claude CLI terminal mode. Contact support if this persists.
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="h-[40%] bg-black text-white p-4 font-mono text-sm overflow-y-auto">
        <div className="flex items-center gap-2 mb-3">
          <span className="font-bold" style={{ color: '#9D00FF' }}>
            Output:
          </span>
          {executing && (
            <span className="text-yellow-400 animate-pulse text-xs">
              executing...
            </span>
          )}
        </div>
        <pre className="whitespace-pre-wrap text-gray-300">
          {output || 'Run your code to see output here.'}
        </pre>
      </div>
    </div>
  </div>
);
