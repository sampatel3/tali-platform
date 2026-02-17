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
      {/* Header bar */}
      <div className="border-b-2 border-black bg-white px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-bold">{filename}</span>
          <span className="font-mono text-xs text-gray-400">{language}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRun}
            disabled={disabled}
            className="border-2 border-[var(--taali-border)] px-4 py-1.5 font-mono text-sm font-bold flex items-center gap-2 text-[var(--taali-surface)] bg-[var(--taali-purple)] hover:opacity-90 transition-colors"
          >
            <Play size={14} /> Run Code
          </button>
          <button
            onClick={handleSave}
            disabled={disabled}
            className="border-2 border-black px-4 py-1.5 font-mono text-sm font-bold flex items-center gap-2 bg-white hover:bg-black hover:text-white transition-colors"
          >
            <Save size={14} /> Save
          </button>
        </div>
      </div>

      {/* Monaco Editor */}
      <div className="flex-1 overflow-hidden">
        <Editor
          height="100%"
          language={language}
          value={code}
          theme="vs-dark"
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
