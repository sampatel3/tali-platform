import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import CodeEditor from './CodeEditor';

const { fakeEditor, fakeModel } = vi.hoisted(() => {
  const model = {
    getLineCount: vi.fn(() => 2),
    getLineMaxColumn: vi.fn(() => 13),
    getValueInRange: vi.fn(() => 'const x = 1;\n'),
  };
  const editor = {
    getModel: vi.fn(() => model),
    getSelection: vi.fn(() => ({
      startLineNumber: 1,
      startColumn: 1,
      endLineNumber: 1,
      endColumn: 1,
      isEmpty: () => true,
    })),
    executeEdits: vi.fn(),
    pushUndoStop: vi.fn(),
    focus: vi.fn(),
    getValue: vi.fn(() => 'const x = 1;'),
  };
  return { fakeEditor: editor, fakeModel: model };
});

vi.mock('@monaco-editor/react', () => ({
  default: ({ onMount }) => {
    React.useEffect(() => {
      onMount(fakeEditor);
    }, [onMount]);
    return <textarea data-testid="mock-monaco-editor" aria-label="Code editor" />;
  },
}));

describe('CodeEditor protected workspace clipboard', () => {
  let workspaceSecurity;

  beforeEach(() => {
    vi.clearAllMocks();
    fakeModel.getValueInRange.mockReturnValue('const x = 1;\n');
    workspaceSecurity = {
      enabled: true,
      copy: vi.fn(() => true),
      paste: vi.fn(() => ({ text: 'workspaceValue', blocked: false })),
      announce: vi.fn(),
    };
  });

  it('routes Monaco copy to the in-memory workspace clipboard', () => {
    render(<CodeEditor value="const x = 1;" onChange={vi.fn()} workspaceSecurity={workspaceSecurity} />);

    fireEvent.copy(screen.getByTestId('mock-monaco-editor'));

    expect(workspaceSecurity.copy).toHaveBeenCalledWith('const x = 1;\n', {
      surface: 'editor',
      operation: 'copy',
      filePath: 'pipeline.py',
    });
  });

  it('inserts only workspace clipboard content through Monaco executeEdits', () => {
    render(<CodeEditor value="const x = 1;" onChange={vi.fn()} workspaceSecurity={workspaceSecurity} />);

    fireEvent.paste(screen.getByTestId('mock-monaco-editor'), {
      clipboardData: { getData: () => 'externalValue' },
    });

    expect(workspaceSecurity.paste).toHaveBeenCalledWith({
      surface: 'editor',
      externalCharacterCount: 13,
      filePath: 'pipeline.py',
    });
    expect(fakeEditor.executeEdits).toHaveBeenCalledWith('taali-workspace-paste', [expect.objectContaining({
      text: 'workspaceValue',
      forceMoveMarkers: true,
    })]);
  });

  it('cuts through an undoable Monaco edit without writing the OS clipboard', () => {
    render(<CodeEditor value="const x = 1;" onChange={vi.fn()} workspaceSecurity={workspaceSecurity} />);

    fireEvent.cut(screen.getByTestId('mock-monaco-editor'));

    expect(workspaceSecurity.copy).toHaveBeenCalledWith('const x = 1;\n', {
      surface: 'editor',
      operation: 'cut',
      filePath: 'pipeline.py',
    });
    expect(fakeEditor.executeEdits).toHaveBeenCalledWith('taali-workspace-cut', [expect.objectContaining({
      text: '',
      forceMoveMarkers: true,
    })]);
    expect(fakeEditor.pushUndoStop).toHaveBeenCalledTimes(2);
  });
});
