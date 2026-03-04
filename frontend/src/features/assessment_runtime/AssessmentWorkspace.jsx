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
    <div className="flex min-h-0 flex-col border-r border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-muted)]">
      <div className="flex-1 min-h-0 flex overflow-hidden">
        {hasRepoStructure && (
          <div className={`${collapsedSections.repoTree ? 'w-12' : 'w-56'} flex flex-col overflow-hidden border-r border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] transition-all duration-150`}>
            <button
              type="button"
              className="flex items-center gap-1.5 border-b border-[var(--taali-runtime-border)] px-2 py-2 font-mono text-[11px] font-bold uppercase tracking-wide text-[var(--taali-runtime-muted)] transition-colors hover:bg-[var(--taali-surface-hover)]"
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
                          className="flex w-full items-center gap-0.5 px-2 py-0.5 text-left font-mono text-xs text-[var(--taali-runtime-muted)] transition-colors hover:bg-[var(--taali-surface-hover)]"
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
                                  ? 'bg-[var(--taali-purple-soft)] text-[var(--taali-purple)]'
                                  : 'text-[var(--taali-runtime-text)] hover:bg-[var(--taali-surface-hover)]'
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
        <div className="min-w-0 flex-1 bg-[var(--taali-runtime-panel)]">
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

    <div className="min-h-0 flex flex-col bg-[var(--taali-runtime-panel-alt)] text-[var(--taali-runtime-text)]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] px-3 py-2">
        <span className="font-mono text-xs font-bold uppercase tracking-wide text-[var(--taali-purple)]">
          Claude Chat
        </span>
        {showTerminal ? (
          <button
            type="button"
            onClick={onToggleTerminal}
            className="rounded-[var(--taali-radius-control)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-2 py-1 font-mono text-[11px] text-[var(--taali-runtime-text)] transition-colors hover:border-[var(--taali-purple)] hover:text-[var(--taali-purple)]"
          >
            {terminalPanelOpen ? 'Hide Terminal' : 'Show Terminal'}
          </button>
        ) : null}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {output ? (
          <div className="rounded-[var(--taali-radius-control)] border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-2">
            <div className="mb-1 flex items-center gap-2 font-mono text-[11px] font-bold uppercase text-[var(--taali-warning)]">
              <span>Run Output</span>
              {executing ? (
                <span className="animate-pulse normal-case text-[var(--taali-warning)]">executing...</span>
              ) : null}
            </div>
            <pre className="whitespace-pre-wrap font-mono text-xs text-[var(--taali-runtime-text)]">
              {output}
            </pre>
          </div>
        ) : null}

        {(claudeConversation || []).map((entry, index) => {
          const isUser = String(entry?.role || '').toLowerCase() === 'user';
          return (
            <div
              key={`${entry?.role || 'message'}-${index}`}
              className={`rounded-[var(--taali-radius-control)] border p-2 ${isUser
                ? 'ml-7 border-[var(--taali-runtime-border)] bg-[linear-gradient(145deg,var(--taali-purple-soft),var(--taali-runtime-panel))] shadow-[var(--taali-shadow-soft)]'
                : 'mr-7 border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)]'
              }`}
            >
              <div className={`mb-1 font-mono text-[11px] font-bold uppercase ${isUser ? 'text-[var(--taali-purple)]' : 'text-[var(--taali-runtime-muted)]'}`}>
                {isUser ? 'You' : 'Claude'}
              </div>
              <p className="whitespace-pre-wrap font-mono text-xs text-[var(--taali-runtime-text)]">
                {String(entry?.content || '')}
              </p>
            </div>
          );
        })}

        {claudePromptSending ? (
          <div className="mr-7 rounded-[var(--taali-radius-control)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-2">
            <div className="mb-1 font-mono text-[11px] font-bold uppercase text-[var(--taali-runtime-muted)]">Claude</div>
            <div className="animate-pulse font-mono text-xs text-[var(--taali-runtime-muted)]">Thinking...</div>
          </div>
        ) : null}

        {!output && !(claudeConversation || []).length ? (
          <div className="rounded-[var(--taali-radius-control)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-3 font-mono text-xs text-[var(--taali-runtime-muted)]">
            Ask Claude for debugging, architecture, or test guidance.
          </div>
        ) : null}
      </div>

      <div className="border-t border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-3">
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
            className="flex-1 rounded-[var(--taali-radius-control)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-2.5 py-2 text-xs text-[var(--taali-runtime-text)] placeholder:text-[var(--taali-runtime-muted)] focus:border-[var(--taali-purple)] focus:outline-none disabled:opacity-60"
          />
          <button
            type="button"
            onClick={onClaudePromptSubmit}
            disabled={claudePromptDisabled || claudePromptSending || !String(claudePrompt || '').trim()}
            className="rounded-[var(--taali-radius-control)] border border-[var(--taali-purple)] bg-[var(--taali-purple)] px-3 py-1 text-xs font-bold text-white transition-colors hover:bg-[var(--taali-purple-hover)] disabled:opacity-50"
          >
            {claudePromptSending ? 'Asking...' : 'Ask Claude'}
          </button>
        </div>
      </div>

      {showTerminal && terminalPanelOpen ? (
        <div className="h-[40%] min-h-[220px] border-t border-[var(--taali-runtime-border)]">
          <AssessmentTerminal
            events={terminalEvents}
            connected={terminalConnected}
            disabled={isTimerPaused}
            onInput={onTerminalInput}
            onResize={onTerminalResize}
            onStop={onTerminalStop}
            stopping={terminalStopping}
            lightMode={lightMode}
          />
        </div>
      ) : null}
    </div>
  </div>
);
