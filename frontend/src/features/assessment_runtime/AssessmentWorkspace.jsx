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
  terminalPanelOpen,
  onToggleTerminal,
  terminalConnected,
  terminalEvents,
  onTerminalInput,
  onTerminalResize,
  onTerminalStop,
  terminalStopping,
  output,
  executing,
  claudeConversation,
  claudePrompt,
  onClaudePromptChange,
  onClaudePromptSubmit,
  claudePromptSending = false,
  claudePromptDisabled = false,
  lightMode = false,
}) => (
  <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_420px] overflow-hidden">
    <div className={`${lightMode ? 'bg-gray-100 border-r border-gray-200' : 'min-h-0 border-r border-white/10 bg-[#0d1118]'} flex flex-col`}>
      <div className="flex-1 min-h-0 flex overflow-hidden">
        {hasRepoStructure && (
          <div className={`${collapsedSections.repoTree ? 'w-12' : 'w-56'} ${lightMode ? 'border-r border-gray-200 bg-gray-50' : 'border-r border-white/10 bg-[#0d121b]'} flex flex-col overflow-hidden transition-all duration-150`}>
            <button
              type="button"
              className={`px-2 py-2 border-b font-mono text-[11px] font-bold uppercase tracking-wide flex items-center gap-1.5 ${lightMode ? 'border-gray-200 text-gray-600 hover:bg-gray-100' : 'border-white/10 text-gray-400 hover:bg-white/5'}`}
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
                          className={`w-full px-2 py-0.5 font-mono text-xs flex items-center gap-0.5 text-left ${lightMode ? 'text-gray-500 hover:bg-gray-100' : 'text-gray-500 hover:bg-white/5'}`}
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
                              className={`w-full text-left px-2 py-1 font-mono text-xs flex items-center gap-1.5 ${
                                isSelected
                                  ? (lightMode ? 'bg-[#f3e6ff] text-[var(--taali-purple)] hover:bg-[#f3e6ff]' : 'bg-[#1a2440] text-indigo-200 hover:bg-[#202c4f]')
                                  : (lightMode ? 'text-gray-700 hover:bg-gray-100' : 'text-gray-300 hover:bg-white/10')
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
        <div className={`flex-1 min-w-0 ${lightMode ? 'bg-white' : 'bg-[#0f141d]'}`}>
          <CodeEditor
            initialCode={assessmentStarterCode}
            value={editorContent}
            onChange={onEditorChange}
            onExecute={onExecute}
            onSave={onSave}
            language={editorLanguage}
            filename={editorFilename}
            disabled={isTimerPaused}
            lightMode={lightMode}
          />
        </div>
      </div>
    </div>

    <div className={`min-h-0 flex flex-col ${lightMode ? 'bg-white text-gray-900' : 'bg-[#090c12] text-white'}`}>
      <div className={`border-b px-3 py-2 flex items-center justify-between gap-2 ${lightMode ? 'border-gray-200 bg-white' : 'border-white/10 bg-[#0f141d]'}`}>
        <span className="font-mono text-xs font-bold uppercase tracking-wide text-[var(--taali-purple)]">
          Claude Chat
        </span>
        {showTerminal ? (
          <button
            type="button"
            onClick={onToggleTerminal}
            className={`border px-2 py-1 font-mono text-[11px] ${lightMode ? 'border-gray-300 text-gray-700 hover:bg-gray-100' : 'border-white/20 text-gray-300 hover:border-[var(--taali-purple)] hover:text-[var(--taali-purple)]'}`}
          >
            {terminalPanelOpen ? 'Hide Terminal' : 'Show Terminal'}
          </button>
        ) : null}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {output ? (
          <div className={`border p-2 rounded-sm ${lightMode ? 'border-amber-300 bg-amber-50' : 'border-amber-500/40 bg-amber-500/10'}`}>
            <div className={`mb-1 flex items-center gap-2 font-mono text-[11px] font-bold uppercase ${lightMode ? 'text-amber-700' : 'text-amber-300'}`}>
              <span>Run Output</span>
              {executing ? (
                <span className={`${lightMode ? 'text-amber-700' : 'text-yellow-300'} animate-pulse normal-case`}>executing...</span>
              ) : null}
            </div>
            <pre className={`whitespace-pre-wrap font-mono text-xs ${lightMode ? 'text-amber-900' : 'text-amber-100/95'}`}>
              {output}
            </pre>
          </div>
        ) : null}

        {(claudeConversation || []).map((entry, index) => {
          const isUser = String(entry?.role || '').toLowerCase() === 'user';
          return (
            <div
              key={`${entry?.role || 'message'}-${index}`}
              className={`border rounded-sm p-2 ${isUser
                ? (lightMode ? 'ml-7 border-[var(--taali-purple)]/40 bg-[#f8f0ff]' : 'ml-7 border-[var(--taali-purple)]/70 bg-[#180b27]')
                : (lightMode ? 'mr-7 border-gray-200 bg-gray-50' : 'mr-7 border-white/10 bg-[#0f141d]')
              }`}
            >
              <div className={`mb-1 font-mono text-[11px] font-bold uppercase ${isUser ? 'text-[var(--taali-purple)]' : (lightMode ? 'text-gray-600' : 'text-gray-300')}`}>
                {isUser ? 'You' : 'Claude'}
              </div>
              <p className={`whitespace-pre-wrap font-mono text-xs ${lightMode ? 'text-gray-800' : 'text-gray-100'}`}>
                {String(entry?.content || '')}
              </p>
            </div>
          );
        })}

        {claudePromptSending ? (
          <div className={`mr-7 border rounded-sm p-2 ${lightMode ? 'border-gray-200 bg-gray-50' : 'border-white/10 bg-[#0f141d]'}`}>
            <div className={`font-mono text-[11px] font-bold uppercase mb-1 ${lightMode ? 'text-gray-600' : 'text-gray-400'}`}>Claude</div>
            <div className={`font-mono text-xs animate-pulse ${lightMode ? 'text-gray-600' : 'text-gray-300'}`}>Thinking...</div>
          </div>
        ) : null}

        {!output && !(claudeConversation || []).length ? (
          <div className={`border p-3 font-mono text-xs rounded-sm ${lightMode ? 'border-gray-200 bg-gray-50 text-gray-600' : 'border-white/10 bg-[#0f141d] text-gray-400'}`}>
            Ask Claude for debugging, architecture, or test guidance.
          </div>
        ) : null}
      </div>

      <div className={`border-t p-3 ${lightMode ? 'border-gray-200 bg-white' : 'border-white/10 bg-[#0c1119]'}`}>
        <div className="flex gap-2">
          <input
            type="text"
            value={claudePrompt}
            onChange={(event) => onClaudePromptChange?.(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                onClaudePromptSubmit?.();
              }
            }}
            placeholder="Ask Claude (Cursor-style): explain this bug in src/main.py"
            disabled={claudePromptDisabled || claudePromptSending}
            className={`flex-1 border px-2 py-1 text-xs placeholder:text-gray-500 focus:outline-none focus:border-[var(--taali-purple)] disabled:opacity-60 ${lightMode ? 'border-gray-300 bg-white text-gray-900' : 'border-white/15 bg-[#090c12] text-gray-100'}`}
          />
          <button
            type="button"
            onClick={onClaudePromptSubmit}
            disabled={claudePromptDisabled || claudePromptSending || !String(claudePrompt || '').trim()}
            className="border border-[var(--taali-purple)] bg-[var(--taali-purple)] px-3 py-1 text-xs font-bold text-white hover:bg-[#aa4dff] disabled:opacity-50"
          >
            {claudePromptSending ? 'Asking...' : 'Ask Claude'}
          </button>
        </div>
      </div>

      {showTerminal && terminalPanelOpen ? (
        <div className={`h-[40%] min-h-[220px] border-t ${lightMode ? 'border-gray-200' : 'border-white/10'}`}>
          <AssessmentTerminal
            events={terminalEvents}
            connected={terminalConnected}
            disabled={isTimerPaused}
            onInput={onTerminalInput}
            onResize={onTerminalResize}
            onStop={onTerminalStop}
            stopping={terminalStopping}
          />
        </div>
      ) : null}
    </div>
  </div>
);
