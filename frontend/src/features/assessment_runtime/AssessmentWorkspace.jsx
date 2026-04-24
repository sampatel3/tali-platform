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
  <div className="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_380px] overflow-hidden">
    <div className={`${lightMode ? 'min-h-0 border-r border-[var(--line)] bg-[var(--bg-2)]' : 'min-h-0 border-r border-white/10 bg-[#0d1118]'} flex flex-col`}>
      <div className="flex-1 min-h-0 flex overflow-hidden">
        {hasRepoStructure && (
          <div className={`${collapsedSections.repoTree ? 'w-14' : 'w-[248px]'} ${lightMode ? 'border-r border-[var(--line)] bg-[color:color-mix(in_oklab,var(--bg)_75%,transparent)]' : 'border-r border-white/10 bg-[#0d121b]'} flex flex-col overflow-hidden transition-all duration-150`}>
            <button
              type="button"
              className={`px-3 py-3 border-b font-[var(--font-mono)] text-[10.5px] font-bold uppercase tracking-[0.12em] flex items-center gap-1.5 ${lightMode ? 'border-[var(--line)] text-[var(--mute)] hover:bg-[var(--bg)]' : 'border-white/10 text-gray-400 hover:bg-white/5'}`}
              onClick={() => toggleSection('repoTree')}
            >
              {collapsedSections.repoTree ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
              {!collapsedSections.repoTree && <span>Repository</span>}
            </button>
            {!collapsedSections.repoTree && (
              <div className="flex-1 overflow-y-auto py-2">
                {Object.entries(repoFileTree)
                  .sort(([a], [b]) => (a || '').localeCompare(b || ''))
                  .map(([dir, paths]) => (
                    <div key={dir || '(root)'} className="mb-1">
                      {dir ? (
                        <button
                          type="button"
                          className={`w-full px-3 py-1 font-[var(--font-mono)] text-[12px] flex items-center gap-0.5 text-left ${lightMode ? 'text-[var(--mute)] hover:bg-[var(--bg)]' : 'text-gray-500 hover:bg-white/5'}`}
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
                              className={`w-full text-left px-3 py-2 font-[var(--font-mono)] text-[12px] flex items-center gap-1.5 ${
                                isSelected
                                  ? (lightMode ? 'bg-[var(--purple-soft)] text-[var(--purple-2)] hover:bg-[var(--purple-soft)] font-medium' : 'bg-[#1a2440] text-indigo-200 hover:bg-[#202c4f]')
                                  : (lightMode ? 'text-[var(--ink-2)] hover:bg-[var(--bg)]' : 'text-gray-300 hover:bg-white/10')
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
        <div className={`flex-1 min-w-0 ${lightMode ? 'bg-[var(--bg-2)]' : 'bg-[#0f141d]'}`}>
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

    <div className={`min-h-0 flex flex-col ${lightMode ? 'bg-[color:color-mix(in_oklab,var(--bg)_60%,transparent)] text-[var(--ink)]' : 'bg-[#090c12] text-white'}`}>
      <div className={`border-b px-4 py-3 flex items-center justify-between gap-2 ${lightMode ? 'border-[var(--line)] bg-[color:color-mix(in_oklab,var(--bg)_60%,transparent)]' : 'border-white/10 bg-[#0f141d]'}`}>
        <span className="font-[var(--font-mono)] text-[10.5px] font-bold uppercase tracking-[0.12em] text-[var(--purple)]">
          Claude Chat
        </span>
        {showTerminal ? (
          <button
            type="button"
            onClick={onToggleTerminal}
            className={`rounded-full border px-3 py-1.5 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] ${lightMode ? 'border-[var(--line)] text-[var(--mute)] hover:border-[var(--purple)] hover:text-[var(--purple)]' : 'border-white/20 text-gray-300 hover:border-[var(--taali-purple)] hover:text-[var(--taali-purple)]'}`}
          >
            {terminalPanelOpen ? 'Hide Terminal' : 'Show Terminal'}
          </button>
        ) : null}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
        {output ? (
          <div className={`border p-3 rounded-[14px] ${lightMode ? 'border-amber-300 bg-amber-50' : 'border-amber-500/40 bg-amber-500/10'}`}>
            <div className={`mb-2 flex items-center gap-2 font-[var(--font-mono)] text-[10.5px] font-bold uppercase tracking-[0.1em] ${lightMode ? 'text-amber-700' : 'text-amber-300'}`}>
              <span>Run Output</span>
              {executing ? (
                <span className={`${lightMode ? 'text-amber-700' : 'text-yellow-300'} animate-pulse normal-case`}>executing...</span>
              ) : null}
            </div>
            <pre className={`whitespace-pre-wrap font-[var(--font-mono)] text-[12px] leading-6 ${lightMode ? 'text-amber-900' : 'text-amber-100/95'}`}>
              {output}
            </pre>
          </div>
        ) : null}

        {(claudeConversation || []).map((entry, index) => {
          const isUser = String(entry?.role || '').toLowerCase() === 'user';
          return (
            <div
              key={`${entry?.role || 'message'}-${index}`}
              className={`border rounded-[14px] p-3 ${isUser
                ? (lightMode ? 'ml-7 border-[var(--purple)]/35 bg-[var(--purple)] text-white' : 'ml-7 border-[var(--taali-purple)]/70 bg-[#180b27]')
                : (lightMode ? 'mr-7 border-[var(--line)] bg-[var(--bg-2)]' : 'mr-7 border-white/10 bg-[#0f141d]')
              }`}
            >
              <div className={`mb-2 font-[var(--font-mono)] text-[10.5px] font-bold uppercase tracking-[0.08em] ${isUser ? (lightMode ? 'text-white/70' : 'text-[var(--taali-purple)]') : (lightMode ? 'text-[var(--mute)]' : 'text-gray-300')}`}>
                {isUser ? 'You' : 'Claude'}
              </div>
              <p className={`whitespace-pre-wrap font-[var(--font-sans)] text-[13px] leading-6 ${isUser ? (lightMode ? 'text-white' : 'text-gray-100') : (lightMode ? 'text-[var(--ink-2)]' : 'text-gray-100')}`}>
                {String(entry?.content || '')}
              </p>
            </div>
          );
        })}

        {claudePromptSending ? (
          <div className={`mr-7 border rounded-[14px] p-3 ${lightMode ? 'border-[var(--line)] bg-[var(--bg-2)]' : 'border-white/10 bg-[#0f141d]'}`}>
            <div className={`font-[var(--font-mono)] text-[10.5px] font-bold uppercase mb-2 tracking-[0.08em] ${lightMode ? 'text-[var(--mute)]' : 'text-gray-400'}`}>Claude</div>
            <div className={`font-[var(--font-mono)] text-[12px] animate-pulse ${lightMode ? 'text-[var(--mute)]' : 'text-gray-300'}`}>Thinking...</div>
          </div>
        ) : null}

        {!output && !(claudeConversation || []).length ? (
          <div className={`border p-4 font-[var(--font-mono)] text-[12px] rounded-[14px] ${lightMode ? 'border-[var(--line)] bg-[var(--bg-2)] text-[var(--mute)]' : 'border-white/10 bg-[#0f141d] text-gray-400'}`}>
            Ask Claude for debugging, architecture, or test guidance.
          </div>
        ) : null}
      </div>

      <div className={`border-t p-4 ${lightMode ? 'border-[var(--line)] bg-[var(--bg-2)]' : 'border-white/10 bg-[#0c1119]'}`}>
        <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-3">
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
            className={`flex-1 border px-3 py-2 text-[13px] placeholder:text-gray-500 focus:outline-none focus:border-[var(--taali-purple)] disabled:opacity-60 ${lightMode ? 'border-[var(--line)] bg-transparent text-[var(--ink)]' : 'border-white/15 bg-[#090c12] text-gray-100'}`}
          />
          <button
            type="button"
            onClick={onClaudePromptSubmit}
            disabled={claudePromptDisabled || claudePromptSending || !String(claudePrompt || '').trim()}
            className="btn btn-primary btn-sm disabled:opacity-50"
          >
            {claudePromptSending ? 'Asking...' : 'Ask Claude'}
          </button>
        </div>
          <div className="mt-2 flex items-center justify-between gap-3">
            <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
              Enter to send
            </div>
            <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
              repo context included
            </div>
          </div>
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
