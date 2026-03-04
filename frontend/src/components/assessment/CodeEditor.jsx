import { useState, useRef, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import { Play, Save } from 'lucide-react';

export default function CodeEditor({
  initialCode = '',
  value: controlledValue,
  onChange: onControlledChange,
  onExecute,
  onSave,
  language = 'python',
  filename = 'pipeline.py',
  disabled = false,
  lightMode = false,
}) {
  const isControlled = controlledValue !== undefined;
  const [internalCode, setInternalCode] = useState(initialCode);
  const code = isControlled ? controlledValue : internalCode;
  const editorRef = useRef(null);

  useEffect(() => {
    if (isControlled && controlledValue !== code) {
      setInternalCode(controlledValue);
    }
  }, [isControlled, controlledValue]);

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
    if (disabled) return;
    const currentCode = editorRef.current?.getValue() || code;
    onSave?.(currentCode);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between border-b border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="max-w-[36ch] truncate font-mono text-sm text-[var(--taali-runtime-text)]">{filename}</span>
          <span className="font-mono text-[11px] uppercase tracking-wide text-[var(--taali-runtime-muted)]">{language}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleRun}
            disabled={disabled}
            className="flex items-center gap-1.5 rounded-[var(--taali-radius-control)] border border-[var(--taali-purple)] bg-[var(--taali-purple)] px-3 py-1.5 font-mono text-xs font-bold text-white transition-colors hover:bg-[var(--taali-purple-hover)] disabled:opacity-50"
          >
            <Play size={12} /> Run
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={disabled}
            className="flex items-center gap-1.5 rounded-[var(--taali-radius-control)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-3 py-1.5 font-mono text-xs font-bold text-[var(--taali-runtime-text)] transition-colors hover:border-[var(--taali-purple)] hover:text-[var(--taali-purple)] disabled:opacity-50"
          >
            <Save size={12} /> Save
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
            padding: { top: 12 },
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
