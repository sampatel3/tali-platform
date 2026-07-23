import { useEffect, useRef, useState } from 'react';
import Editor from '@monaco-editor/react';
import { FileText, Play, Save } from 'lucide-react';

// Points the Monaco loader at our bundled copy. Must be imported before the
// editor mounts; see monacoSetup.js for why we do not use the CDN default.
import './monacoSetup';

// Monaco ships in the same lazy chunk as this component, so by the time we
// render it is already in memory and mounting takes a tick. If onMount has not
// fired well past that, something is wrong at a layer we cannot see — rethrow
// into the workspace error boundary, which swaps in the plain-textarea editor.
// A candidate mid-assessment gets a usable editor and a retry rather than an
// indefinite "Loading editor...".
const MOUNT_TIMEOUT_MS = 15000;

export default function CodeEditor({
  initialCode = '',
  value: controlledValue,
  onChange: onControlledChange,
  onExecute,
  onSave,
  language = 'python',
  filename = 'pipeline.py',
  disabled = false,
  actionsDisabled = false,
  saving = false,
  lightMode = false,
  workspaceSecurity = null,
}) {
  const isControlled = controlledValue !== undefined;
  const [internalCode, setInternalCode] = useState(initialCode);
  const code = isControlled ? controlledValue : internalCode;
  const editorRef = useRef(null);
  const [mounted, setMounted] = useState(false);
  const [mountTimedOut, setMountTimedOut] = useState(false);

  useEffect(() => {
    if (isControlled && controlledValue !== code) {
      setInternalCode(controlledValue);
    }
  }, [isControlled, controlledValue, code]);

  useEffect(() => {
    if (mounted) return undefined;
    const timer = setTimeout(() => setMountTimedOut(true), MOUNT_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [mounted]);

  if (mountTimedOut) {
    throw new Error('Monaco editor failed to mount');
  }

  const handleEditorDidMount = (editor) => {
    editorRef.current = editor;
    setMounted(true);
  };

  const protectedSelection = () => {
    const editor = editorRef.current;
    const model = editor?.getModel?.();
    const selection = editor?.getSelection?.();
    if (!editor || !model || !selection) return null;

    const selectionIsEmpty = typeof selection.isEmpty === 'function'
      ? selection.isEmpty()
      : selection.startLineNumber === selection.endLineNumber
        && selection.startColumn === selection.endColumn;
    if (!selectionIsEmpty) {
      return {
        range: selection,
        text: String(model.getValueInRange?.(selection) || ''),
      };
    }

    // Match Monaco's familiar empty-selection Copy/Cut behavior by using the
    // current line. A plain range object is accepted by executeEdits and keeps
    // this component independent of Monaco's Range constructor.
    const line = Number(selection.startLineNumber || 1);
    const lineCount = Number(model.getLineCount?.() || line);
    const range = line < lineCount
      ? {
          startLineNumber: line,
          startColumn: 1,
          endLineNumber: line + 1,
          endColumn: 1,
        }
      : {
          startLineNumber: line,
          startColumn: 1,
          endLineNumber: line,
          endColumn: Number(model.getLineMaxColumn?.(line) || 1),
        };
    return {
      range,
      text: String(model.getValueInRange?.(range) || ''),
    };
  };

  const handleProtectedCopy = (event) => {
    if (!workspaceSecurity?.enabled) return;
    event.preventDefault();
    event.stopPropagation();
    const selected = protectedSelection();
    workspaceSecurity.copy?.(selected?.text || '', {
      surface: 'editor',
      operation: 'copy',
      filePath: filename,
    });
  };

  const handleProtectedCut = (event) => {
    if (!workspaceSecurity?.enabled) return;
    event.preventDefault();
    event.stopPropagation();
    if (disabled) {
      workspaceSecurity.announce?.('The editor is read-only while this assessment is paused.');
      return;
    }
    const selected = protectedSelection();
    if (!selected?.text || !selected.range) {
      workspaceSecurity.announce?.('Select workspace code before cutting.');
      return;
    }
    if (!workspaceSecurity.copy?.(selected.text, {
      surface: 'editor',
      operation: 'cut',
      filePath: filename,
    })) return;
    editorRef.current?.pushUndoStop?.();
    editorRef.current?.executeEdits?.('taali-workspace-cut', [{
      range: selected.range,
      text: '',
      forceMoveMarkers: true,
    }]);
    editorRef.current?.pushUndoStop?.();
    editorRef.current?.focus?.();
  };

  const handleProtectedPaste = (event) => {
    if (!workspaceSecurity?.enabled) return;
    event.preventDefault();
    event.stopPropagation();
    if (disabled) {
      workspaceSecurity.announce?.('The editor is read-only while this assessment is paused.');
      return;
    }
    const externalCharacterCount = String(event.clipboardData?.getData?.('text/plain') || '').length;
    const { text } = workspaceSecurity.paste?.({
      surface: 'editor',
      externalCharacterCount,
      filePath: filename,
    }) || {};
    const editor = editorRef.current;
    const selection = editor?.getSelection?.();
    if (!text || !editor || !selection) return;
    editor.pushUndoStop?.();
    editor.executeEdits?.('taali-workspace-paste', [{
      range: selection,
      text,
      forceMoveMarkers: true,
    }]);
    editor.pushUndoStop?.();
    editor.focus?.();
  };

  const handleChange = (newValue) => {
    if (isControlled) {
      onControlledChange?.(newValue ?? '');
    } else {
      setInternalCode(newValue ?? '');
    }
  };

  const handleRun = () => {
    if (disabled || actionsDisabled) return;
    const currentCode = editorRef.current?.getValue() || code;
    onExecute?.(currentCode);
  };

  const handleSave = () => {
    if (disabled || actionsDisabled || saving) return;
    const currentCode = editorRef.current?.getValue() || code;
    onSave?.(currentCode);
  };

  return (
    <div className="flex h-full flex-col bg-[var(--bg-2)]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-3">
        <div className="min-w-0 flex items-center gap-2 text-[0.8125rem] text-[var(--ink-2)]">
          <FileText size={13} />
          <span className="truncate font-mono">{filename}</span>
          <span className="rounded bg-[var(--bg-3)] px-2 py-0.5 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-[var(--mute)]">
            {language}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={disabled || actionsDisabled || saving}
            className="taali-btn taali-btn-secondary taali-btn-xs"
          >
            <Save size={12} />
            {saving ? 'Saving...' : 'Save'}
          </button>
          <button
            type="button"
            onClick={handleRun}
            disabled={disabled || actionsDisabled}
            className="taali-btn taali-btn-primary taali-btn-xs"
          >
            <Play size={12} fill="currentColor" />
            Run
          </button>
        </div>
      </div>

      <div
        className="flex-1 overflow-hidden"
        data-workspace-surface="editor"
        onCopyCapture={handleProtectedCopy}
        onCutCapture={handleProtectedCut}
        onPasteCapture={handleProtectedPaste}
      >
        <Editor
          height="100%"
          language={language}
          value={code}
          theme={lightMode ? 'vs-light' : 'vs-dark'}
          onChange={handleChange}
          onMount={handleEditorDidMount}
          options={{
            minimap: { enabled: false },
            fontSize: 14,
            tabSize: 4,
            scrollBeyondLastLine: false,
            automaticLayout: true,
            padding: { top: 14 },
            lineNumbers: 'on',
            renderLineHighlight: 'line',
            cursorBlinking: 'smooth',
            wordWrap: 'on',
            readOnly: disabled,
            dragAndDrop: !workspaceSecurity?.enabled,
          }}
        />
      </div>
    </div>
  );
}
