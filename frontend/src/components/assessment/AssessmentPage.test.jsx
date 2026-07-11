import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import AssessmentPage from '../../features/assessment_runtime/AssessmentPage';

const mockExecute = vi.fn();
const mockSubmit = vi.fn();
const mockSaveRepoFile = vi.fn();
const mockClaudeChat = vi.fn();

vi.mock('../../shared/api', () => ({
  assessments: {
    start: vi.fn(),
    execute: (...args) => mockExecute(...args),
    saveRepoFile: (...args) => mockSaveRepoFile(...args),
    claudeChat: (...args) => mockClaudeChat(...args),
    submit: (...args) => mockSubmit(...args),
    runtimeEvent: () => Promise.resolve({ data: { success: true } }),
  },
}));

vi.mock('../../components/assessment/CodeEditor', () => ({
  default: ({ initialCode, value, onExecute, onSave }) => {
    const code = value ?? initialCode;
    return (
      <div data-testid="code-editor">
        <div>editor:{code}</div>
        <button type="button" onClick={() => onExecute?.(code)}>Run</button>
        <button type="button" onClick={() => onSave?.(code)}>Save</button>
      </div>
    );
  },
}));

describe('AssessmentPage live agentic runtime', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockExecute.mockResolvedValue({ data: { success: true, stdout: '', stderr: '', error: null, results: [] } });
    mockSaveRepoFile.mockResolvedValue({ data: { success: true } });
    mockSubmit.mockResolvedValue({ data: { success: true } });
    mockClaudeChat.mockResolvedValue({ data: { success: true, content: 'ok' } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('sends submit tab_switch_count metadata', async () => {
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

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await screen.findByText('This has been recorded.');

    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]);
    });
    await screen.findByRole('dialog');
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));
    });

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSubmit.mock.calls[0][3]).toMatchObject({ tab_switch_count: 1 });
  });

  it('disables submission in demo task preview mode', async () => {
    const startData = {
      assessment_id: 26,
      token: null,
      time_remaining: 1800,
      task: {
        name: 'AWS Glue Pipeline Recovery',
        starter_code: 'print("hi")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage startData={startData} demoMode />);

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();

    // PR #707 made the demo / showcase preview read-only: a viewer (or the
    // pitch deck) must never be able to submit the walkthrough assessment and
    // flip the surface to the "Task submitted" screen. The top-bar Submit is
    // disabled + tooltipped, and handleSubmit no-ops in demoMode — so neither
    // Submit control can open the confirm dialog, hit the API, or reveal the
    // submitted screen.
    const submitButtons = screen.getAllByRole('button', { name: 'Submit' });
    const topBarSubmit = submitButtons[0];
    expect(topBarSubmit).toBeDisabled();
    expect(topBarSubmit).toHaveAttribute(
      'title',
      'Preview — submission is disabled in the demo',
    );

    // Clicking either Submit control is inert in demo mode.
    await act(async () => {
      submitButtons.forEach((button) => fireEvent.click(button));
    });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: /Task submitted/i }),
    ).not.toBeInTheDocument();
    expect(mockSubmit).not.toHaveBeenCalled();
  });


  it('renders task context and repository tree when provided', async () => {
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

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.getAllByText(/migration left historical records incomplete/i).length).toBeGreaterThan(0);
    expect(await screen.findByText('Repository')).toBeInTheDocument();
    expect(screen.getByText('src')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^backfill\.py$/i })).toBeInTheDocument();
    expect(screen.getByText('README.md')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Collapse repository/i }));
    });
    expect(screen.queryByRole('button', { name: /^backfill\.py$/i })).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Expand repository/i }));
    });
    expect(screen.getByRole('button', { name: /^backfill\.py$/i })).toBeInTheDocument();
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

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.getByText('Task context has not been provided yet.')).toBeInTheDocument();
    expect(screen.queryByText('Repository')).not.toBeInTheDocument();
  });


  it('shows working guidance and clone command in context window', async () => {
    const startData = {
      assessment_id: 14,
      token: 'tok4',
      time_remaining: 1200,
      clone_command: 'git clone --branch assessment/14 mock://repo',
      task: {
        name: 'Instruction task',
        starter_code: 'print("start")',
        duration_minutes: 30,
        rubric_categories: [
          { category: 'exploration', weight: 0.25 },
          { category: 'implementation_quality', weight: 0.35 },
        ],
        evaluation_rubric: {
          exploration: { weight: 0.25, criteria: { excellent: 'should never render' } },
        },
      },
    };

    render(<AssessmentPage token="tok4" startData={startData} />);

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.getByText(/Use Claude for scoped help, then validate the patch path yourself/i)).toBeInTheDocument();
    // Clone command + "Clone command available" chip were removed
    // 2026-05-26 (Sam: "hide it for candidates"). The repo URL is a
    // backend artifact — candidates work in-browser. The replacement
    // copy is the submission-clarity line.
    expect(screen.queryByText(/Clone command:/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Clone command available/i)).not.toBeInTheDocument();
    expect(screen.getByText(/code in the workspace when you submit/i)).toBeInTheDocument();
    expect(screen.queryByText('exploration')).not.toBeInTheDocument();
    expect(screen.queryByText(/should never render/i)).not.toBeInTheDocument();
  });

  it('keeps candidate rubric and inferred tests out of the runtime context window', async () => {
    const startData = {
      assessment_id: 15,
      token: 'tok5',
      time_remaining: 1200,
      task: {
        name: 'Collapsible sections',
        scenario: 'Investigate and patch the backfill job.',
        starter_code: 'print("start")',
        duration_minutes: 30,
        rubric_categories: [
          { category: 'implementation_quality', weight: 0.5 },
        ],
        repo_structure: {
          files: {
            'src/job.py': 'def run_job():\n    pass',
            'tests/test_job.py': 'def test_run_job():\n    assert True',
          },
        },
      },
    };

    render(<AssessmentPage token="tok5" startData={startData} />);

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.getAllByText(/Investigate and patch the backfill job/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Use Claude for scoped help, then validate the patch path yourself/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Rubric$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Tests$/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Candidate-safe rubric/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/What to validate/i)).not.toBeInTheDocument();
  });

  it('mounts the agentic Claude chat as the only assistant surface', async () => {
    const startData = {
      assessment_id: 21,
      token: 'tok-chat',
      time_remaining: 1200,
      task: {
        name: 'Chat-only task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok-chat" startData={startData} />);

    // The agentic HTTP chat is mounted; there is no terminal toggle or
    // terminal surface anymore.
    expect(await screen.findByTestId('assessment-claude-chat')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Terminal$/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId('assessment-terminal')).not.toBeInTheDocument();
    // Output dock toggle (the only remaining workspace dock surface) is present.
    expect(screen.getByText(/Workspace dock/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Output$/i })).toBeInTheDocument();
  });

  it('sends an agentic chat prompt over the HTTP claudeChat transport', async () => {
    const startData = {
      assessment_id: 27,
      token: 'tok-claude-repo',
      time_remaining: 1200,
      task: {
        name: 'Claude repo task',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/main.py': 'print("hi")',
            'README.md': '# Demo repo',
          },
        },
      },
    };

    mockClaudeChat.mockResolvedValueOnce({
      data: { content: 'Start with `src/main.py`, then read `README.md` for task context.' },
    });

    render(<AssessmentPage token="tok-claude-repo" startData={startData} />);

    // Select src/main.py so it is the file Claude receives as the active file.
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: /^main\.py$/i }));
    });

    const promptInput = await screen.findByPlaceholderText(/Ask Claude to inspect the repo/i);
    fireEvent.change(promptInput, { target: { value: 'What files matter?' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Send/i }));
    });

    await waitFor(() => expect(mockClaudeChat).toHaveBeenCalledTimes(1));
    expect(mockClaudeChat.mock.calls[0][0]).toBe(27);
    expect(mockClaudeChat.mock.calls[0][1]).toMatchObject({
      message: 'What files matter?',
      selected_file_path: 'src/main.py',
    });

    expect(await screen.findByText(/Start with/i)).toBeInTheDocument();
  });

  it('shows a clear success message when run finishes without stdout or stderr', async () => {
    const startData = {
      assessment_id: 22,
      token: 'tok-run-output',
      time_remaining: 1200,
      task: {
        name: 'Run output task',
        duration_minutes: 30,
        // The editor (and Run) now reveal only after a repo file is selected
        // (chat-centred workspace), so seed a single-file repo and open it.
        repo_structure: {
          files: {
            'src/main.py': 'answer = 42',
          },
        },
      },
    };

    mockExecute.mockResolvedValueOnce({
      data: {
        success: true,
        stdout: '',
        stderr: '',
        error: null,
        results: [],
      },
    });

    render(<AssessmentPage token="tok-run-output" startData={startData} />);

    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: /^main\.py$/i }));
    });
    const runButton = await screen.findByRole('button', { name: /^Run$/i });
    await act(async () => {
      fireEvent.click(runButton);
    });

    await waitFor(() => expect(mockExecute).toHaveBeenCalledTimes(1));
    expect(mockExecute.mock.calls[0][1]).toMatchObject({
      code: 'answer = 42',
      selected_file_path: 'src/main.py',
      repo_files: [{ path: 'src/main.py', content: 'answer = 42' }],
    });
    expect(await screen.findByText(/Code executed successfully\. No stdout\/stderr was produced\./i)).toBeInTheDocument();
  });

  it('creates a new repo file and saves it into the live workspace', async () => {
    const startData = {
      assessment_id: 28,
      token: 'tok-new-file',
      time_remaining: 1200,
      task: {
        name: 'New file task',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/main.py': 'print("hi")',
          },
        },
      },
    };

    render(<AssessmentPage token="tok-new-file" startData={startData} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /New File/i }));
    });
    fireEvent.change(screen.getByPlaceholderText('src/new_file.py'), {
      target: { value: 'src/new_file.py' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Create$/i }));
    });

    expect(screen.getByText('new_file.py')).toBeInTheDocument();

    const saveButton = await screen.findByRole('button', { name: /^Save$/i });
    await act(async () => {
      fireEvent.click(saveButton);
    });

    await waitFor(() => expect(mockSaveRepoFile).toHaveBeenCalledTimes(1));
    expect(mockSaveRepoFile.mock.calls[0][1]).toMatchObject({
      path: 'src/new_file.py',
      content: '',
    });
  });

  it('surfaces runtime execution errors in the output dock', async () => {
    const startData = {
      assessment_id: 26,
      token: 'tok-run-error',
      time_remaining: 1200,
      task: {
        name: 'Run error task',
        duration_minutes: 30,
        // Editor + Run reveal only after a repo file is opened (chat-centred
        // workspace), so seed a single-file repo and select it below.
        repo_structure: {
          files: {
            'src/main.py': 'broken(',
          },
        },
      },
    };

    mockExecute.mockResolvedValueOnce({
      data: {
        success: false,
        stdout: '',
        stderr: 'Traceback (most recent call last)',
        error: 'SyntaxError: unexpected EOF while parsing',
        results: [],
      },
    });

    render(<AssessmentPage token="tok-run-error" startData={startData} />);

    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: /^main\.py$/i }));
    });
    const runButton = await screen.findByRole('button', { name: /^Run$/i });
    await act(async () => {
      fireEvent.click(runButton);
    });

    await waitFor(() => expect(mockExecute).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/SyntaxError: unexpected EOF while parsing/i)).toBeInTheDocument();
    expect(screen.getByText(/Traceback \(most recent call last\)/i)).toBeInTheDocument();
  });

  it('shows budget exhausted alert when Claude budget is depleted', async () => {
    const startData = {
      assessment_id: 19,
      token: 'tok9',
      time_remaining: 1200,
      claude_budget: {
        enabled: true,
        limit_usd: 5,
        used_usd: 5,
        remaining_usd: 0,
        tokens_used: 5000,
        is_exhausted: true,
      },
      task: {
        name: 'Exhausted budget task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok9" startData={startData} />);

    expect(await screen.findByText(/Claude budget used up for this task/i)).toBeInTheDocument();
  });

  it('updates Claude credit display after an agentic chat response', async () => {
    const startData = {
      assessment_id: 23,
      token: 'tok-claude-budget',
      time_remaining: 1200,
      claude_budget: {
        enabled: true,
        limit_usd: 1,
        used_usd: 0,
        remaining_usd: 1,
        tokens_used: 0,
        is_exhausted: false,
      },
      task: {
        name: 'Budget update task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    mockClaudeChat.mockResolvedValueOnce({
      data: {
        content: 'Use stronger filtering.',
        claude_budget: {
          enabled: true,
          limit_usd: 1,
          used_usd: 0.25,
          remaining_usd: 0.75,
          tokens_used: 1000,
          is_exhausted: false,
        },
      },
    });

    render(<AssessmentPage token="tok-claude-budget" startData={startData} />);

    expect(await screen.findByText(/\$1\.00 of \$1\.00/i)).toBeInTheDocument();

    const promptInput = await screen.findByPlaceholderText(/Ask Claude to inspect the repo/i);
    fireEvent.change(promptInput, { target: { value: 'Help me debug' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Send/i }));
    });

    await waitFor(() => expect(mockClaudeChat).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/\$0\.75 of \$1\.00/i)).toBeInTheDocument();
  });

  it('defaults the assessment runtime to light mode', async () => {
    const startData = {
      assessment_id: 24,
      token: 'tok-theme',
      time_remaining: 1200,
      task: {
        name: 'Theme toggle task',
        starter_code: 'print("start")',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="tok-theme" startData={startData} />);

    expect((await screen.findAllByText('Theme toggle task')).length).toBeGreaterThan(0);
    expect(localStorage.getItem('taali_assessment_theme')).toBe('light');
  });

});
