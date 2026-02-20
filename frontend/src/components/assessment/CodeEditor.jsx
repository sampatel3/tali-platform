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
      <div className={`${lightMode ? 'border-b border-gray-200 bg-white' : 'border-b border-white/10 bg-[#0f141d]'} px-3 py-2 flex items-center justify-between`}>
        <div className="flex items-center gap-2">
          <span className={`font-mono text-sm truncate max-w-[36ch] ${lightMode ? 'text-gray-900' : 'text-gray-100'}`}>{filename}</span>
          <span className={`font-mono text-[11px] uppercase tracking-wide ${lightMode ? 'text-gray-500' : 'text-gray-500'}`}>{language}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRun}
            disabled={disabled}
            className="border border-[var(--taali-purple)] px-3 py-1.5 font-mono text-xs font-bold flex items-center gap-1.5 text-white bg-[var(--taali-purple)] hover:bg-[#aa4dff] transition-colors disabled:opacity-50"
          >
            <Play size={12} /> Run
          </button>
          <button
            onClick={handleSave}
            disabled={disabled}
            className={`border px-3 py-1.5 font-mono text-xs font-bold flex items-center gap-1.5 transition-colors disabled:opacity-50 ${lightMode ? 'border-gray-300 bg-gray-100 text-gray-700 hover:bg-gray-200' : 'border-white/20 bg-[#131a25] text-gray-200 hover:bg-[#1a2432]'}`}
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
