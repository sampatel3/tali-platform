import { useEffect, useRef, useState } from 'react';
import Editor from '@monaco-editor/react';
import { FileText, Play, Save, TerminalSquare } from 'lucide-react';

export default function CodeEditor({
  initialCode = '',
  value: controlledValue,
  onChange: onControlledChange,
  onExecute,
  onSave,
  onOpenTerminal,
  language = 'python',
  filename = 'pipeline.py',
  disabled = false,
  saving = false,
  lightMode = false,
  showTerminalAction = false,
}) {
  const isControlled = controlledValue !== undefined;
  const [internalCode, setInternalCode] = useState(initialCode);
  const code = isControlled ? controlledValue : internalCode;
  const editorRef = useRef(null);

  useEffect(() => {
    if (isControlled && controlledValue !== code) {
      setInternalCode(controlledValue);
    }
  }, [isControlled, controlledValue, code]);

  const handleEditorDidMount = (editor) => {
    editorRef.current = editor;
  };

  const handleChange = (newValue) => {
    if (isControlled) {
      onControlledChange?.(newValue ?? '');
    } else {
      setInternalCode(newValue ?? '');
    }
  };

  const handleRun = () => {
    if (disabled) return;
    const currentCode = editorRef.current?.getValue() || code;
    onExecute?.(currentCode);
  };

  const handleSave = () => {
    if (disabled || saving) return;
    const currentCode = editorRef.current?.getValue() || code;
    onSave?.(currentCode);
  };

  return (
    <div className="flex h-full flex-col bg-[var(--bg-2)]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-3">
        <div className="min-w-0 flex items-center gap-2 text-[13px] text-[var(--ink-2)]">
          <FileText size={13} />
          <span className="truncate font-mono">{filename}</span>
          <span className="rounded bg-[var(--bg-3)] px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--mute)]">
            {language}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={disabled || saving}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3 py-1.5 text-[12px] font-medium text-[var(--mute)] transition-colors hover:border-[var(--ink)] hover:text-[var(--ink)] disabled:opacity-50"
          >
            <Save size={12} />
            {saving ? 'Saving...' : 'Save'}
          </button>
          {showTerminalAction ? (
            <button
              type="button"
              onClick={onOpenTerminal}
              disabled={disabled}
              className="inline-flex items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3 py-1.5 text-[12px] font-medium text-[var(--ink-2)] transition-colors hover:border-[var(--purple)] hover:text-[var(--purple)] disabled:opacity-50"
            >
              <TerminalSquare size={12} />
              Run tests
            </button>
          ) : null}
          <button
            type="button"
            onClick={handleRun}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-full bg-[var(--purple)] px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-[var(--purple-2)] disabled:opacity-50"
          >
            <Play size={12} fill="currentColor" />
            Run
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
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
          }}
        />
      </div>
    </div>
  );
}
