import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  FileText,
  Folder,
  MessageSquare,
  Plus,
  TerminalSquare,
  X,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';

import { AssessmentClaudeChat } from './AssessmentClaudeChat';
import { useAgenticClaudeChat } from './featureFlags';

const LazyCodeEditor = lazy(() => import('../../components/assessment/CodeEditor'));
const LazyAssessmentTerminal = lazy(() =>
  import('./AssessmentTerminal').then((module) => ({ default: module.AssessmentTerminal }))
);

const CLAUDE_INTERNAL_TOOL_TAGS = new Set([
  'read_file',
  'read_many_files',
  'list_dir',
  'glob_search',
  'grep_search',
  'search_files',
  'run_command',
  'open_file',
]);

const CLAUDE_MARKDOWN_COMPONENTS = {
  p: ({ children }) => (
    <p className="whitespace-pre-line text-[0.84375rem] leading-6 text-[var(--ink-2)] [&:not(:first-child)]:mt-3">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="mt-3 list-disc space-y-2 pl-5 text-[0.84375rem] leading-6 text-[var(--ink-2)]">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-3 list-decimal space-y-2 pl-5 text-[0.84375rem] leading-6 text-[var(--ink-2)]">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1 marker:text-[var(--purple)]">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-[var(--ink)]">{children}</strong>,
  em: ({ children }) => <em className="italic text-[var(--ink-2)]">{children}</em>,
  code: ({ children, className, ...props }) => {
    const isBlock = typeof className === 'string' && className.length > 0;
    if (isBlock) {
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className="rounded-md bg-[var(--purple-soft)] px-1.5 py-0.5 font-mono text-[0.88em] text-[var(--purple-2)]" {...props}>
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="mt-3 overflow-x-auto rounded-[12px] border border-[var(--line)] bg-[var(--bg)] p-3 font-mono text-[0.75rem] leading-6 text-[var(--ink-2)]">
      {children}
    </pre>
  ),
};

function sanitizeClaudeMessage(content) {
  const raw = String(content || '').trim();
  if (!raw) return '';

  const toolNotes = [];
  const cleaned = raw
    .replace(/<([a-z_][a-z0-9_]*)>\s*([\s\S]*?)<\/\1>/gi, (fullMatch, rawTag, body) => {
      const tag = String(rawTag || '').trim().toLowerCase();
      if (!CLAUDE_INTERNAL_TOOL_TAGS.has(tag)) {
        return fullMatch;
      }
      const paths = Array.from(String(body || '').matchAll(/<path>\s*([\s\S]*?)\s*<\/path>/gi))
        .map((match) => String(match[1] || '').trim())
        .filter(Boolean);
      if (paths.length > 0) {
        const summary = paths.slice(0, 3).join(', ');
        toolNotes.push(`Reviewing: ${summary}${paths.length > 3 ? `, +${paths.length - 3} more` : ''}`);
      }
      return '';
    })
    .replace(/^\s*<\/?[a-z_][a-z0-9_]*>\s*$/gim, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  if (cleaned) return cleaned;
  if (toolNotes.length > 0) return toolNotes.join('\n');
  return raw;
}

class RuntimeSurfaceBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, errorKey: 0 };
    this.reset = this.reset.bind(this);
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error) {
    // eslint-disable-next-line no-console
    console.error('Runtime surface failed to load', error);
  }

  reset() {
    // Bump errorKey so children remount with a fresh attempt at lazy import.
    this.setState((s) => ({ hasError: false, errorKey: s.errorKey + 1 }));
  }

  render() {
    if (this.state.hasError) {
      const fallback = this.props.fallback;
      if (typeof fallback === 'function') return fallback({ retry: this.reset });
      return fallback;
    }
    return (
      <React.Fragment key={this.state.errorKey}>
        {this.props.children}
      </React.Fragment>
    );
  }
}

const EditorFallback = ({
  assessmentStarterCode,
  editorContent,
  onEditorChange,
  onExecute,
  onSave,
  onOpenTerminal,
  editorLanguage,
  editorFilename,
  isTimerPaused,
  saving = false,
  showTerminalAction = false,
  onRetry = null,
}) => (
  <div className="flex h-full flex-col bg-[var(--bg-2)]">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-3">
      <div className="min-w-0 flex items-center gap-2 text-[0.8125rem] text-[var(--ink-2)]">
        <FileText size={13} />
        <span className="truncate font-mono">{editorFilename}</span>
        <span className="rounded bg-[var(--bg-3)] px-2 py-0.5 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-[var(--mute)]">
          {editorLanguage}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onSave?.(editorContent ?? assessmentStarterCode ?? '')}
          disabled={isTimerPaused || saving}
          className="inline-flex items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3 py-1.5 text-[0.75rem] font-medium text-[var(--mute)] transition-colors hover:border-[var(--ink)] hover:text-[var(--ink)] disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
        {showTerminalAction ? (
          <button
            type="button"
            onClick={onOpenTerminal}
            disabled={isTimerPaused}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3 py-1.5 text-[0.75rem] font-medium text-[var(--ink-2)] transition-colors hover:border-[var(--purple)] hover:text-[var(--purple)] disabled:opacity-50"
          >
            <TerminalSquare size={12} />
            Run tests
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => onExecute?.(editorContent ?? assessmentStarterCode ?? '')}
          disabled={isTimerPaused}
          className="inline-flex items-center gap-1.5 rounded-full bg-[var(--purple)] px-3 py-1.5 text-[0.75rem] font-medium text-white transition-colors hover:bg-[var(--purple-2)] disabled:opacity-50"
        >
          Run
        </button>
      </div>
    </div>
    <div className="flex-1 overflow-hidden p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] px-3 py-2 font-mono text-[0.6875rem] text-[var(--taali-warning)]">
        <span>Advanced editor unavailable in this browser. Using a plain text fallback.</span>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-full border border-[var(--taali-warning-border)] bg-[var(--bg-2)] px-2.5 py-1 text-[0.6875rem] font-medium text-[var(--taali-warning)] transition-colors hover:border-[var(--purple)] hover:text-[var(--purple)]"
          >
            Try again
          </button>
        ) : null}
      </div>
      <textarea
        value={editorContent ?? assessmentStarterCode ?? ''}
        onChange={(event) => onEditorChange?.(event.target.value)}
        disabled={isTimerPaused}
        spellCheck={false}
        className="h-full min-h-[18rem] w-full resize-none rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4 font-mono text-[0.75rem] text-[var(--ink)] outline-none focus:border-[var(--purple)] disabled:opacity-60"
      />
    </div>
  </div>
);

const EditorLoadingFallback = () => (
  <div className="flex h-full items-center justify-center bg-[var(--bg-2)] p-6">
    <div className="rounded-[12px] border border-[var(--line)] bg-[var(--bg)] px-4 py-3 font-mono text-[0.75rem] text-[var(--mute)]">
      Loading editor...
    </div>
  </div>
);

const TerminalFallback = ({ onRetry = null } = {}) => (
  <div className="flex h-full flex-col bg-[var(--ink)] text-[var(--taali-inverse-text)]">
    <div className="border-b border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-[var(--purple-soft)]">Claude Code CLI</div>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-1 font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[color-mix(in_oklab,var(--taali-inverse-text)_70%,transparent)] transition-colors hover:border-[var(--purple)] hover:text-[var(--purple)]"
          >
            Retry
          </button>
        ) : null}
      </div>
      <div className="mt-1 text-[0.75rem] text-[color-mix(in_oklab,var(--taali-inverse-text)_70%,transparent)]">Terminal preview is unavailable in this browser.</div>
    </div>
    <div className="p-4 font-mono text-[0.75rem] leading-6 text-[color-mix(in_oklab,var(--taali-inverse-text)_70%,transparent)]">
      Continue with the editor and Claude chat, or switch browsers to open the live terminal.
    </div>
  </div>
);

const TerminalLoadingFallback = () => (
  <div className="flex h-full items-center justify-center bg-[var(--ink)] p-4">
    <div className="rounded-[12px] border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--ink)_82%,var(--purple))] px-4 py-3 font-mono text-[0.75rem] text-[color-mix(in_oklab,var(--taali-inverse-text)_70%,transparent)]">
      Loading terminal...
    </div>
  </div>
);

const DockToggleButton = ({ active = false, icon = null, onClick, children }) => (
  <button
    type="button"
    onClick={onClick}
    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[0.75rem] font-medium transition-colors ${
      active
        ? 'border-[var(--purple)] bg-[var(--purple-soft)] text-[var(--purple)]'
        : 'border-[var(--line)] bg-[var(--bg)] text-[var(--mute)] hover:border-[var(--ink)] hover:text-[var(--ink)]'
    }`}
  >
    {icon}
    {children}
  </button>
);

const RuntimeOutputPanel = ({ output, executing, onClose }) => (
  <div className="flex min-h-[13.75rem] flex-col overflow-hidden rounded-[16px] border border-[var(--line)] bg-[var(--ink)] text-[var(--taali-inverse-text)]">
    <div className="flex items-center justify-between gap-3 border-b border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] px-4 py-3">
      <div>
        <div className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-[var(--purple-soft)]">Run output</div>
        <div className="mt-1 text-[0.75rem] text-[color-mix(in_oklab,var(--taali-inverse-text)_60%,transparent)]">
          {executing ? 'Executing...' : 'Latest result'}
        </div>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="inline-flex items-center gap-1 rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-1.5 text-[0.6875rem] font-medium text-[color-mix(in_oklab,var(--taali-inverse-text)_70%,transparent)] transition-colors hover:border-[color-mix(in_oklab,var(--taali-inverse-text)_20%,transparent)] hover:text-[var(--taali-inverse-text)]"
      >
        Collapse
        <ChevronDown size={12} />
      </button>
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 font-mono text-[0.75rem] leading-7">
      {output ? (
        <pre className="whitespace-pre-wrap">{output}</pre>
      ) : (
        <div className="text-[color-mix(in_oklab,var(--taali-inverse-text)_60%,transparent)]">Run code from the editor to see stdout, stderr, and execution errors here.</div>
      )}
    </div>
  </div>
);

const TerminalDockPanel = ({
  terminalConnected,
  terminalRestarting,
  onRestartTerminal,
  onClose,
  children,
}) => (
  <div className="flex min-h-[13.75rem] flex-col overflow-hidden rounded-[16px] border border-[var(--line)] bg-[var(--ink)] text-[var(--taali-inverse-text)]">
    <div className="flex items-center justify-between gap-3 border-b border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] px-4 py-3">
      <div>
        <div className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-[var(--purple-soft)]">Terminal</div>
        <div className="mt-1 text-[0.75rem] text-[color-mix(in_oklab,var(--taali-inverse-text)_60%,transparent)]">
          {terminalConnected ? 'Connected to the live workspace' : 'Connecting to the live workspace'}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {typeof onRestartTerminal === 'function' ? (
          <button
            type="button"
            onClick={onRestartTerminal}
            disabled={terminalRestarting}
            className="rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-1.5 text-[0.6875rem] font-medium text-[color-mix(in_oklab,var(--taali-inverse-text)_70%,transparent)] transition-colors hover:border-[color-mix(in_oklab,var(--taali-inverse-text)_20%,transparent)] hover:text-[var(--taali-inverse-text)] disabled:opacity-50"
          >
            {terminalRestarting ? 'Restarting...' : 'Restart terminal'}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          className="inline-flex items-center gap-1 rounded-full border border-[color-mix(in_oklab,var(--taali-inverse-text)_10%,transparent)] bg-[color-mix(in_oklab,var(--taali-inverse-text)_5%,transparent)] px-3 py-1.5 text-[0.6875rem] font-medium text-[color-mix(in_oklab,var(--taali-inverse-text)_70%,transparent)] transition-colors hover:border-[color-mix(in_oklab,var(--taali-inverse-text)_20%,transparent)] hover:text-[var(--taali-inverse-text)]"
        >
          Collapse
          <ChevronDown size={12} />
        </button>
      </div>
    </div>
    <div className="min-h-0 flex-1">
      {children}
    </div>
  </div>
);

export const AssessmentWorkspace = ({
  className = '',
  hasRepoStructure,
  modifiedRepoPaths = [],
  repoFileTree,
  repoPanelCollapsed = false,
  onToggleRepoPanel,
  collapsedRepoDirs,
  toggleRepoDir,
  selectedRepoPath,
  onSelectRepoFile,
  onCreateRepoFile,
  creatingRepoFile = false,
  newRepoFilePath = '',
  onNewRepoFilePathChange,
  onCancelRepoFileCreate,
  assessmentStarterCode,
  editorContent,
  onEditorChange,
  onExecute,
  onSave,
  savingRepoFile = false,
  editorLanguage,
  editorFilename,
  isTimerPaused,
  showTerminal,
  assistantPanelCollapsed = false,
  onToggleAssistantPanel,
  terminalPanelOpen,
  onToggleTerminal,
  outputPanelOpen = false,
  onToggleOutput,
  terminalConnected,
  terminalEvents,
  onTerminalInput,
  onTerminalResize,
  onRestartTerminal,
  showRestartTerminal = true,
  terminalRestarting = false,
  output,
  executing,
  claudeConversation,
  claudePrompt,
  onClaudePromptChange,
  onClaudePromptSubmit,
  onClaudePromptPaste,
  claudePromptSending = false,
  claudePromptSlow = false,
  claudePromptDisabled = false,
  // Agentic chat (leaf C of the terminal-removal refactor): only used
  // when the runtime feature flag `__TAALI_AGENTIC_CHAT__` is on. The
  // legacy WebSocket-on-PTY chat path above stays wired so we can
  // toggle back without a rebuild while the backend route lands.
  assessmentId,
  assessmentToken,
  claudeBudget,
  onClaudeBudgetUpdate,
  selectedFilePath,
  codeContext,
  lightMode = false,
  branchName,
  // Existing transcript including the task_opener Claude wrote at /start
  // time. Threaded into AssessmentClaudeChat as initialAiPrompts so the
  // candidate sees the decision questions on first chat open (#37).
  initialAiPrompts = null,
}) => {
  const agenticChatEnabled = useAgenticClaudeChat();
  const modifiedPathSet = useMemo(
    () => new Set(Array.isArray(modifiedRepoPaths) ? modifiedRepoPaths : []),
    [modifiedRepoPaths],
  );
  const repoEntries = useMemo(
    () => Object.entries(repoFileTree || {}).sort(([a], [b]) => (a || '').localeCompare(b || '')),
    [repoFileTree],
  );
  const repoFileCount = useMemo(
    () => repoEntries.reduce((total, [, paths]) => total + paths.length, 0),
    [repoEntries],
  );
  // When the agentic Claude chat is on (the default post-#394), the legacy
  // PTY-backed terminal panel is dead weight — the new chat already does
  // everything the terminal did, via tool-use against the live sandbox. Drop
  // the bottom-dock Terminal tab entirely for that path. Keep the Output
  // panel (Run / Run tests) untouched.
  const terminalSurfaceEnabled = Boolean(showTerminal) && !agenticChatEnabled;
  const showOutputPanel = Boolean(outputPanelOpen || executing);
  const showTerminalPanel = Boolean(terminalSurfaceEnabled && terminalPanelOpen);
  const showDock = showOutputPanel || showTerminalPanel;

  // Resizable assistant panel. Default widened from 380→600 (2026-06-01)
  // to reflect the chat-first work model: candidates increasingly drive
  // assessments through the agent, so chat is the dominant surface and
  // the IDE pane is the context drawer. Doc-kind tasks (PM, Scrum
  // Master, etc.) push this further — chat IS the work; the editor is
  // for the deliverable. Drag-resize range correspondingly bumped:
  // 280 (light glance) → 900 (chat-as-half-the-viewport) covers both
  // candidates who want the editor bigger and those who want chat
  // bigger. Persist per browser via localStorage so the choice survives
  // reloads. When the panel is collapsed by the chevron button, we
  // still expose the small 76px rail, ignoring the saved width until
  // uncollapsed.
  const ASSISTANT_PANEL_MIN = 280;
  const ASSISTANT_PANEL_MAX = 900;
  const ASSISTANT_PANEL_DEFAULT = 600;
  const ASSISTANT_PANEL_STORAGE_KEY = 'taali.assessmentRuntime.assistantPanelWidth';
  const [assistantPanelWidth, setAssistantPanelWidth] = useState(() => {
    if (typeof window === 'undefined') return ASSISTANT_PANEL_DEFAULT;
    const raw = window.localStorage?.getItem(ASSISTANT_PANEL_STORAGE_KEY);
    const parsed = Number(raw);
    if (Number.isFinite(parsed) && parsed >= ASSISTANT_PANEL_MIN && parsed <= ASSISTANT_PANEL_MAX) {
      return parsed;
    }
    return ASSISTANT_PANEL_DEFAULT;
  });
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage?.setItem(ASSISTANT_PANEL_STORAGE_KEY, String(assistantPanelWidth));
    } catch {
      // localStorage may be disabled (private mode); the runtime width
      // still works in-session, just doesn't persist.
    }
  }, [assistantPanelWidth]);

  const resizingRef = useRef(null);
  const handleResizeStart = useCallback((event) => {
    if (assistantPanelCollapsed) return;
    event.preventDefault();
    resizingRef.current = {
      startX: event.clientX,
      startWidth: assistantPanelWidth,
    };
    const handleMove = (moveEvt) => {
      const ctx = resizingRef.current;
      if (!ctx) return;
      // Chat-centred layout (2026-06-01): the divider sits on the
      // chat's RIGHT edge (between chat and editor on the right).
      // Drag-RIGHT widens chat (chat extends into editor's space);
      // drag-LEFT shrinks chat. Sign flipped vs the old "chat on the
      // right" layout where the divider was on chat's left edge.
      const delta = moveEvt.clientX - ctx.startX;
      const next = Math.max(
        ASSISTANT_PANEL_MIN,
        Math.min(ASSISTANT_PANEL_MAX, ctx.startWidth + delta),
      );
      setAssistantPanelWidth(next);
    };
    const handleEnd = () => {
      resizingRef.current = null;
      window.removeEventListener('mousemove', handleMove);
      window.removeEventListener('mouseup', handleEnd);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleEnd);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [assistantPanelCollapsed, assistantPanelWidth]);

  // Chat-centred grid (2026-06-01). New layout order: [repo] [chat] [editor].
  // The editor pane is REVEALED only when ``selectedFilePath`` is set —
  // either via a doc-kind task auto-opening its primary_artifact, or via
  // the candidate clicking a file. When no file is selected the editor
  // column drops out of the grid entirely and chat expands to fill the
  // remaining space (1fr), which is the canonical "agent is the work"
  // shape. When a file IS selected, chat keeps its preferred fixed
  // pixel width and the editor takes 1fr on the right.
  const editorVisible = Boolean(selectedFilePath);
  const workspaceGridStyle = useMemo(() => {
    const repoTrack = hasRepoStructure
      ? (repoPanelCollapsed ? '72px' : '248px')
      : null;
    let chatTrack;
    if (assistantPanelCollapsed) {
      chatTrack = '76px';
    } else if (editorVisible) {
      chatTrack = `${assistantPanelWidth}px`;
    } else {
      chatTrack = 'minmax(0,1fr)';
    }
    const editorTrack = editorVisible ? 'minmax(0,1fr)' : null;
    const tracks = [repoTrack, chatTrack, editorTrack].filter(Boolean);
    return { '--workspace-grid': tracks.join(' ') };
  }, [
    assistantPanelCollapsed,
    assistantPanelWidth,
    editorVisible,
    hasRepoStructure,
    repoPanelCollapsed,
  ]);

  const handleOpenTerminal = () => {
    if (terminalSurfaceEnabled && !showTerminalPanel) {
      onToggleTerminal?.();
    }
  };

  return (
    <section
      className={`overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] shadow-[var(--shadow-md)] ${className}`.trim()}
    >
      {/* Cap the workspace at a viewport-relative height so the chat
          panel inside is bounded. Without this, the page itself grows
          as Claude's messages stack up and the candidate has to scroll
          the whole page instead of the chat scrolling internally
          (Sam, 2026-05-26). 220px reserves room for the top nav, the
          assessment header, and the task-context window above. The
          ``min-h-[38.75rem]`` keeps the previous floor for short windows
          (laptops at 800px tall stay usable). ``100dvh`` over ``vh``
          so mobile address-bar collapse doesn't leave a gap. */}
      {/* Workspace fills the viewport minus the chrome above (top nav,
          assessment header, brief panel). ``dvh`` keeps mobile
          address-bar collapse correct. */}
      <div className="flex min-h-[38.75rem] max-h-[calc(100dvh-180px)] flex-col">
        <div
          className="grid min-h-0 flex-1 xl:[grid-template-columns:var(--workspace-grid)]"
          style={workspaceGridStyle}
        >
          {hasRepoStructure ? (
            <aside
              className={`flex min-h-0 flex-col border-r border-[var(--line)] py-4 transition-[padding] duration-200 ${
                repoPanelCollapsed ? 'px-2' : 'px-4'
              }`}
              style={{ background: 'color-mix(in oklab, var(--bg) 75%, transparent)' }}
            >
              <div className={`flex items-center gap-2 pb-3 ${repoPanelCollapsed ? 'justify-center' : 'justify-between'}`}>
                {repoPanelCollapsed ? null : (
                  <span className="font-mono text-[0.65625rem] uppercase tracking-[0.12em] text-[var(--mute)]">Repository</span>
                )}
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => {
                      if (repoPanelCollapsed) {
                        onToggleRepoPanel?.();
                      }
                      onCreateRepoFile?.();
                    }}
                    className="inline-flex h-6 w-6 items-center justify-center rounded-md text-[var(--mute)] transition-colors hover:bg-[var(--bg-3)] hover:text-[var(--purple)]"
                    aria-label="New file"
                  >
                    <Plus size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => onToggleRepoPanel?.()}
                    className="inline-flex h-6 w-6 items-center justify-center rounded-md text-[var(--mute)] transition-colors hover:bg-[var(--bg-3)] hover:text-[var(--purple)]"
                    aria-label={repoPanelCollapsed ? 'Expand repository' : 'Collapse repository'}
                  >
                    {repoPanelCollapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
                  </button>
                </div>
              </div>

              {repoPanelCollapsed ? (
                <div className="flex flex-1 flex-col items-center gap-3 pt-3">
                  <div className="grid h-10 w-10 place-items-center rounded-[12px] border border-[var(--line)] bg-[var(--bg)] text-[var(--mute)]">
                    <Folder size={16} />
                  </div>
                  <div className="text-center">
                    <div className="font-mono text-[0.625rem] uppercase tracking-[0.12em] text-[var(--mute)]">Repo</div>
                    <div className="mt-1 text-[0.75rem] font-medium text-[var(--ink-2)]">{repoFileCount}</div>
                  </div>
                </div>
              ) : (
                <>
                  {creatingRepoFile ? (
                    <div className="mb-3 rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] p-3">
                      <input
                        type="text"
                        value={newRepoFilePath}
                        onChange={(event) => onNewRepoFilePathChange?.(event.target.value)}
                        placeholder="src/new_file.py"
                        className="w-full rounded-[10px] border border-[var(--line)] bg-[var(--bg)] px-3 py-2 font-mono text-[0.75rem] text-[var(--ink)] placeholder:text-[var(--mute)] focus:border-[var(--purple)] focus:outline-none"
                      />
                      <div className="mt-2 flex gap-2">
                        <button
                          type="button"
                          onClick={() => onCreateRepoFile?.(newRepoFilePath)}
                          className="flex-1 rounded-full bg-[var(--purple)] px-3 py-2 text-[0.75rem] font-medium text-white transition-colors hover:bg-[var(--purple-2)]"
                        >
                          Create
                        </button>
                        <button
                          type="button"
                          onClick={onCancelRepoFileCreate}
                          className="flex-1 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3 py-2 text-[0.75rem] font-medium text-[var(--ink-2)] transition-colors hover:border-[var(--ink)] hover:text-[var(--ink)]"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : null}

                  <div className="min-h-0 flex-1 overflow-y-auto font-mono text-[0.78125rem] leading-7">
                    {repoEntries.map(([dir, paths]) => (
                      <div key={dir || '(root)'} className="mb-1">
                        {dir ? (
                          <button
                            type="button"
                            className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[var(--mute)] transition-colors hover:bg-[var(--bg-3)]"
                            onClick={() => toggleRepoDir(dir)}
                          >
                            {collapsedRepoDirs[dir] ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
                            <Folder size={11} />
                            <span className="truncate">{dir}</span>
                          </button>
                        ) : null}
                        <div className={dir ? 'pl-4' : ''} hidden={Boolean(dir && collapsedRepoDirs[dir])}>
                          {paths.map((path) => {
                            const name = path.includes('/') ? path.slice(path.lastIndexOf('/') + 1) : path;
                            const isSelected = path === selectedRepoPath;
                            const isModified = modifiedPathSet.has(path);
                            return (
                              <button
                                key={path}
                                type="button"
                                className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left transition-colors ${
                                  isSelected
                                    ? 'bg-[var(--purple-soft)] text-[var(--purple-2)]'
                                    : 'text-[var(--ink-2)] hover:bg-[var(--bg-3)]'
                                }`}
                                onClick={() => onSelectRepoFile(path)}
                              >
                                <span className={`h-[0.3125rem] w-[0.3125rem] rounded-full ${isSelected ? 'bg-[var(--purple)]' : 'bg-[var(--mute-2)]/60'}`} />
                                <span className="truncate">{name}</span>
                                {isModified ? (
                                  <span className="ml-auto font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--amber)]">M</span>
                                ) : null}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-4 border-t border-[var(--line-2)] pt-3 font-mono text-[0.65625rem] leading-5 text-[var(--mute)]">
                    <div>Save syncs the selected file.</div>
                    <div>Branch · <span className="text-[var(--purple-2)]">{branchName || 'live workspace'}</span></div>
                  </div>
                </>
              )}
            </aside>
          ) : null}

          {/* Chat-centred layout: chat <aside> renders BEFORE the editor
              <main> so chat sits in the middle column of the
              [repo | chat | editor] grid. The editor only renders when
              a file is selected; chat itself is non-collapsible —
              Sam (2026-06-01): "chat shouldn't be collapsable, code
              should — chat is central." The previous chevron collapse
              for chat is gone. Editor pane has an explicit close (×)
              button that clears the file selection, dropping the
              editor column out of the grid entirely.
              Drag handle stays on chat's RIGHT edge (between chat and
              editor) and only mounts when the editor is visible. */}
          <aside
            className="relative min-h-0"
            style={{ background: 'color-mix(in oklab, var(--bg) 60%, transparent)' }}
          >
            {editorVisible ? (
              <div
                role="separator"
                aria-label="Resize Claude panel"
                aria-orientation="vertical"
                tabIndex={0}
                onMouseDown={handleResizeStart}
                onDoubleClick={() => setAssistantPanelWidth(ASSISTANT_PANEL_DEFAULT)}
                className="group absolute inset-y-0 right-0 z-10 hidden w-[0.4375rem] translate-x-[3px] cursor-col-resize xl:block"
                title="Drag to resize · double-click to reset"
              >
                <div className="pointer-events-none absolute left-1/2 top-1/2 h-12 w-[0.125rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--line-2)] transition-colors group-hover:bg-[var(--purple)]" />
              </div>
            ) : null}
            <div className="flex h-full min-h-[26.25rem] flex-col">
              <div className="flex items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <div className="grid h-6 w-6 place-items-center rounded-[7px] bg-[linear-gradient(135deg,var(--purple)_0%,var(--purple-soft)_100%)] font-mono text-[0.625rem] font-semibold text-[var(--taali-inverse-text)]">
                      C
                    </div>
                    <div className="min-w-0">
                      <div className="truncate text-[0.75rem] font-semibold text-[var(--ink)]">Claude</div>
                      <div className="truncate font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-[var(--mute)]">
                        {showTerminal ? 'Live repo assistant' : 'Chat guidance'}
                      </div>
                    </div>
                  </div>
                </div>

                {showTerminal ? (
                  <div className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.06em] ${
                    terminalConnected
                      ? 'border-[var(--line)] bg-[var(--bg)] text-[var(--green)]'
                      : 'border-[var(--line)] bg-[var(--bg)] text-[var(--mute)]'
                  }`}>
                    <span className={`h-[0.3125rem] w-[0.3125rem] rounded-full ${terminalConnected ? 'bg-[var(--green)]' : 'bg-[var(--mute-2)]'}`} />
                    {terminalConnected ? 'Terminal ready' : 'Terminal idle'}
                  </div>
                ) : null}
              </div>

              {agenticChatEnabled ? (
                <div className="min-h-0 flex-1 px-4 py-4">
                  <AssessmentClaudeChat
                    assessmentId={assessmentId}
                    token={assessmentToken}
                    selectedFilePath={selectedFilePath}
                    codeContext={codeContext}
                    claudeBudget={claudeBudget}
                    onBudgetUpdate={onClaudeBudgetUpdate}
                    disabled={claudePromptDisabled}
                    initialAiPrompts={initialAiPrompts}
                  />
                </div>
              ) : (
                <>
                  <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
                    <div className="space-y-4">
                      {(claudeConversation || []).map((entry, index) => {
                        const isUser = String(entry?.role || '').toLowerCase() === 'user';
                        const messageContent = sanitizeClaudeMessage(entry?.content || '');
                        const turnLabel = `turn ${index + 1}`;

                        return (
                          <div key={`${entry?.role || 'message'}-${index}`} className={`text-[0.84375rem] ${isUser ? 'text-right' : ''}`}>
                            <div className={`mb-2 flex gap-2 font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--mute)] ${isUser ? 'justify-end' : 'justify-start'}`}>
                              <span>{isUser ? 'You' : 'Claude'}</span>
                              <span>{turnLabel}</span>
                            </div>
                            <div className={`inline-block max-w-[92%] rounded-[14px] px-4 py-3 text-left ${
                              isUser
                                ? 'rounded-tr-[4px] bg-[var(--purple)] text-white'
                                : 'rounded-tl-[4px] border border-[var(--line)] bg-[var(--bg-2)] text-[var(--ink-2)]'
                            }`}>
                              {isUser ? (
                                <p className="whitespace-pre-wrap leading-6 text-inherit">{messageContent}</p>
                              ) : (
                                <ReactMarkdown components={CLAUDE_MARKDOWN_COMPONENTS}>
                                  {messageContent}
                                </ReactMarkdown>
                              )}
                            </div>
                          </div>
                        );
                      })}

                      {claudePromptSending ? (
                        <div className="text-[0.84375rem]">
                          <div className="mb-2 flex gap-2 font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--mute)]">
                            <span>Claude</span>
                            <span>drafting</span>
                          </div>
                          <div className="inline-block max-w-[92%] rounded-[14px] rounded-tl-[4px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 text-left">
                            {claudePromptSlow ? (
                              <div className="space-y-2">
                                <div className="font-medium text-[var(--ink)]">Still working in the live repo session...</div>
                                <div className="text-[0.8125rem] leading-6 text-[var(--mute)]">
                                  Open the terminal dock to inspect progress, or restart the terminal if it looks stuck.
                                </div>
                              </div>
                            ) : (
                              <div className="animate-pulse text-[var(--mute)]">Thinking...</div>
                            )}
                          </div>
                        </div>
                      ) : null}

                      {!(claudeConversation || []).length ? (
                        <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-4 text-[0.8125rem] leading-6 text-[var(--mute)]">
                          Ask Claude to inspect the live repo, explain a failure, or suggest the smallest safe patch path before you edit.
                          {showTerminal ? (
                            <div className="mt-2 inline-flex items-center gap-2 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-[var(--purple)]">
                              <TerminalSquare size={12} />
                              Terminal lives in the bottom dock.
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </div>

                  <div className="border-t border-[var(--line)] bg-[var(--bg-2)] px-4 py-4">
                    <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-3 py-3 transition-colors focus-within:border-[var(--purple)]">
                      <textarea
                        value={claudePrompt}
                        onChange={(event) => onClaudePromptChange?.(event.target.value)}
                        onPaste={() => onClaudePromptPaste?.()}
                        onKeyDown={(event) => {
                          if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                            event.preventDefault();
                            if (String(claudePrompt || '').trim()) {
                              onClaudePromptSubmit?.();
                            }
                          }
                        }}
                        placeholder="Ask Claude, attach files with @, run a tool with /…"
                        disabled={claudePromptDisabled || claudePromptSending}
                        className="min-h-[4rem] w-full resize-none border-0 bg-transparent text-[0.84375rem] leading-6 text-[var(--ink)] outline-none placeholder:text-[var(--mute)] disabled:opacity-60"
                      />
                      <div className="mt-2 flex items-center justify-between gap-3">
                        <div className="font-mono text-[0.65625rem] uppercase tracking-[0.06em] text-[var(--mute)]">
                          Cmd/Ctrl + Enter to send
                        </div>
                        <button
                          type="button"
                          onClick={onClaudePromptSubmit}
                          disabled={claudePromptDisabled || claudePromptSending || !String(claudePrompt || '').trim()}
                          className="inline-flex items-center gap-2 rounded-full bg-[var(--ink)] px-3 py-1.5 text-[0.78125rem] font-medium text-[var(--bg)] transition-colors hover:bg-[var(--purple)] disabled:opacity-50"
                        >
                          <MessageSquare size={13} />
                          Send
                        </button>
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
          </aside>

          {/* Editor pane — RIGHT column of the chat-centred grid.
              Mounts only when a file is selected (Sam: "code window
              appears on the right hand side if a user selects one of
              the repo files"). For doc-kind tasks the deliverable's
              primary_artifact pre-selects the file, so the editor is
              visible from session start. For code-kind tasks the
              candidate clicks a file in the repo tree to reveal the
              editor. */}
          {editorVisible ? (
            <main className="min-w-0 border-l border-[var(--line)] bg-[var(--bg-2)]">
              <div className="flex h-full min-h-[26.25rem] flex-col">
                {/* Editor header strip with close (×) — clears the file
                    selection so the editor column drops out of the
                    grid entirely and chat expands back to fill the
                    space. Sam (2026-06-01): "the code window should
                    be collapsable not the chat window — it should
                    completely collapse so not visible." */}
                <div className="flex items-center justify-between gap-3 border-b border-[var(--line)] px-4 py-2">
                  <div className="min-w-0 truncate font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--mute)]">
                    {editorFilename || selectedFilePath || 'Editor'}
                  </div>
                  <button
                    type="button"
                    onClick={() => onSelectRepoFile?.(null)}
                    className="inline-flex h-6 w-6 items-center justify-center rounded-md text-[var(--mute)] transition-colors hover:bg-[var(--bg-3)] hover:text-[var(--purple)]"
                    aria-label="Close editor"
                    title="Close editor"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="min-h-0 flex-1">
                  <RuntimeSurfaceBoundary
                    fallback={({ retry }) => (
                      <EditorFallback
                        assessmentStarterCode={assessmentStarterCode}
                        editorContent={editorContent}
                        onEditorChange={onEditorChange}
                        onExecute={onExecute}
                        onSave={onSave}
                        onOpenTerminal={handleOpenTerminal}
                        editorLanguage={editorLanguage}
                        editorFilename={editorFilename}
                        isTimerPaused={isTimerPaused}
                        saving={savingRepoFile}
                        showTerminalAction={showTerminal}
                        onRetry={retry}
                      />
                    )}
                  >
                    <Suspense fallback={<EditorLoadingFallback />}>
                      <LazyCodeEditor
                        initialCode={assessmentStarterCode}
                        value={editorContent}
                        onChange={onEditorChange}
                        onExecute={onExecute}
                        onSave={onSave}
                        onOpenTerminal={handleOpenTerminal}
                        saving={savingRepoFile}
                        language={editorLanguage}
                        filename={editorFilename}
                        disabled={isTimerPaused}
                        lightMode={lightMode}
                        showTerminalAction={showTerminal}
                      />
                    </Suspense>
                  </RuntimeSurfaceBoundary>
                </div>
              </div>
            </main>
          ) : null}
        </div>

        <div
          className="border-t border-[var(--line)] px-4 py-3 lg:px-5"
          style={{ background: 'color-mix(in oklab, var(--bg) 92%, transparent)' }}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-mono text-[0.65625rem] uppercase tracking-[0.12em] text-[var(--mute)]">Workspace dock</div>
              <div className="mt-1 text-[0.75rem] text-[var(--mute)]">
                {terminalSurfaceEnabled
                  ? 'Open the output pane after each run, or use the terminal for repo commands and tests.'
                  : 'Open the output pane to see results from Run and Run tests.'}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <DockToggleButton
                active={showOutputPanel}
                icon={showOutputPanel ? <ChevronUp size={12} /> : <FileText size={12} />}
                onClick={() => onToggleOutput?.()}
              >
                Output
              </DockToggleButton>
              {terminalSurfaceEnabled ? (
                <DockToggleButton
                  active={showTerminalPanel}
                  icon={showTerminalPanel ? <ChevronUp size={12} /> : <TerminalSquare size={12} />}
                  onClick={() => onToggleTerminal?.()}
                >
                  Terminal
                </DockToggleButton>
              ) : null}
            </div>
          </div>

          {showDock ? (
            <div className={`mt-3 grid gap-3 ${showOutputPanel && showTerminalPanel ? 'xl:grid-cols-2' : ''}`}>
              {showTerminalPanel ? (
                <TerminalDockPanel
                  terminalConnected={terminalConnected}
                  terminalRestarting={terminalRestarting}
                  onRestartTerminal={showRestartTerminal ? onRestartTerminal : undefined}
                  onClose={() => onToggleTerminal?.()}
                >
                  <RuntimeSurfaceBoundary fallback={({ retry }) => <TerminalFallback onRetry={retry} />}>
                    <Suspense fallback={<TerminalLoadingFallback />}>
                      <LazyAssessmentTerminal
                        events={terminalEvents}
                        connected={terminalConnected}
                        disabled={isTimerPaused}
                        onInput={onTerminalInput}
                        onResize={onTerminalResize}
                        lightMode={lightMode}
                      />
                    </Suspense>
                  </RuntimeSurfaceBoundary>
                </TerminalDockPanel>
              ) : null}

              {showOutputPanel ? (
                <RuntimeOutputPanel
                  output={output}
                  executing={executing}
                  onClose={() => onToggleOutput?.()}
                />
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
};
