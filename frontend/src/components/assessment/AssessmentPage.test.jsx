import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import AssessmentPage from './AssessmentPage';

const mockClaude = vi.fn();
const mockSubmit = vi.fn();

vi.mock('../../lib/api', () => ({
  assessments: {
    start: vi.fn(),
    execute: vi.fn(),
    claude: (...args) => mockClaude(...args),
    submit: (...args) => mockSubmit(...args),
  },
}));

vi.mock('./CodeEditor', () => ({
  default: ({ initialCode }) => <div data-testid="code-editor">editor:{initialCode}</div>,
}));

vi.mock('./ClaudeChat', () => ({
  default: ({ onSendMessage, onPaste }) => (
    <div>
      <button type="button" onClick={() => onPaste?.()}>paste</button>
      <button
        type="button"
        onClick={async () => {
          await onSendMessage('Help me debug this', []);
        }}
      >
        send-claude
      </button>
    </div>
  ),
}));

describe('AssessmentPage tracking metadata', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockClaude.mockResolvedValue({ data: { response: 'ok' } });
    mockSubmit.mockResolvedValue({ data: { success: true } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('sends prompt metadata and submit tab_switch_count', async () => {
    const startData = {
      assessment_id: 10,
      token: 'tok',
      time_remaining: 1800,
      task: {
        name: 'Debug task',
        starter_code: 'print("hi")',
        duration_minutes: 30,
        proctoring_enabled: true,
      },
    };

    render(<AssessmentPage token="tok" startData={startData} />);

    await act(async () => {
      fireEvent.click(screen.getByText('paste'));
    });

    await act(async () => {
      fireEvent.click(screen.getByText('send-claude'));
    });

    await waitFor(() => expect(mockClaude).toHaveBeenCalledTimes(1));
    const claudeArgs = mockClaude.mock.calls[0];
    expect(claudeArgs[0]).toBe(10);
    expect(claudeArgs[1]).toBe('Help me debug this');
    expect(claudeArgs[3]).toBe('tok');
    expect(claudeArgs[4]).toMatchObject({
      code_context: 'print("hi")',
      paste_detected: true,
      browser_focused: true,
    });

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await screen.findByText('This has been recorded.');

    await act(async () => {
      fireEvent.click(screen.getByText('Submit'));
    });

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSubmit.mock.calls[0][3]).toMatchObject({ tab_switch_count: 1 });
  });


  it('renders task and repository context when provided', async () => {
    const startData = {
      assessment_id: 12,
      token: 'tok2',
      time_remaining: 1200,
      task: {
        name: 'History Backfill',
        description: 'Backfill account history for missing rows.',
        scenario: 'A migration left historical records incomplete.',
        starter_code: 'print("start")',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/backfill.py': 'def run():\n    return True',
            'README.md': '# task',
          },
        },
      },
    };

    render(<AssessmentPage token="tok2" startData={startData} />);

    expect(await screen.findByText('Task Context')).toBeInTheDocument();
    expect(screen.getByText(/migration left historical records incomplete/i)).toBeInTheDocument();
    expect(screen.getByText('Repository Context')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'src/backfill.py' })).toBeInTheDocument();
    expect(screen.getByText(/def run\(\):/)).toBeInTheDocument();
  });

  it('shows fallback context copy when task metadata is missing', async () => {
    const startData = {
      assessment_id: 13,
      token: 'tok3',
      time_remaining: 1200,
      task: {
        name: 'Untitled task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok3" startData={startData} />);

    expect(await screen.findByText('Task Context')).toBeInTheDocument();
    expect(screen.getByText('Task context has not been provided yet.')).toBeInTheDocument();
    expect(screen.getByText('Repository Context')).toBeInTheDocument();
    expect(screen.getByText('No repository files provided for this assessment.')).toBeInTheDocument();
  });

});
