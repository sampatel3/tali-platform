import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import CodeEditor from './CodeEditor';

const { fakeEditor, fakeModel, monacoMock } = vi.hoisted(() => {
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
  // Flipped to false to simulate a Monaco runtime that loads but never mounts.
  return { fakeEditor: editor, fakeModel: model, monacoMock: { mounts: true } };
});

// The real module pulls the bundled Monaco runtime in, which needs browser
// APIs jsdom does not implement. The editor itself is mocked below, so the
// loader wiring has nothing to do here.
vi.mock('./monacoSetup', () => ({ default: {} }));

vi.mock('@monaco-editor/react', () => ({
  default: ({ onMount }) => {
    React.useEffect(() => {
      if (monacoMock.mounts) onMount(fakeEditor);
    }, [onMount]);
    return <textarea data-testid="mock-monaco-editor" aria-label="Code editor" />;
  },
}));

class CatchBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? <p>plain text fallback</p> : this.props.children;
  }
}

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

describe('CodeEditor load failure', () => {
  beforeEach(() => {
    monacoMock.mounts = true;
  });

  afterEach(() => {
    monacoMock.mounts = true;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('escalates to the workspace fallback instead of loading forever', () => {
    // A candidate sits on "Loading editor..." with no way forward if the
    // runtime never mounts, so the component gives up and lets the workspace
    // boundary swap in the plain-textarea editor.
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    monacoMock.mounts = false;

    // React rethrows a boundary-caught error at window so jsdom can report it.
    // Throwing is the point of this test, so mark it handled: an unexplained
    // Monaco stack trace in an otherwise green run sends whoever reads the
    // output next off debugging something that is working as designed.
    const swallowExpectedThrow = (event) => event.preventDefault();
    window.addEventListener('error', swallowExpectedThrow);

    try {
      render(
        <CatchBoundary>
          <CodeEditor value="x = 1" onChange={vi.fn()} />
        </CatchBoundary>,
      );

      expect(screen.queryByText('plain text fallback')).toBeNull();

      act(() => {
        vi.advanceTimersByTime(15000);
      });

      expect(screen.getByText('plain text fallback')).toBeTruthy();
    } finally {
      window.removeEventListener('error', swallowExpectedThrow);
    }
  });

  it('leaves a mounted editor alone once the timeout would have fired', () => {
    vi.useFakeTimers();

    render(
      <CatchBoundary>
        <CodeEditor value="x = 1" onChange={vi.fn()} />
      </CatchBoundary>,
    );

    act(() => {
      vi.advanceTimersByTime(60000);
    });

    expect(screen.getByTestId('mock-monaco-editor')).toBeTruthy();
  });
});
