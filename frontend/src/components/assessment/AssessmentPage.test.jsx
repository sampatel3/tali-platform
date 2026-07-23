import { useEffect, useRef, useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
  useSearchParams,
} from 'react-router-dom';
import { vi } from 'vitest';

import { AssessmentLiveRoute } from '../../app/AssessmentRoutes';
import AssessmentPage from '../../features/assessment_runtime/AssessmentPage';
import { recoverCandidateRuntimeToken } from '../../shared/assessment/candidateProofBinding';

const mockExecute = vi.fn();
const mockStart = vi.fn();
const mockSubmit = vi.fn();
const mockSaveRepoFile = vi.fn();
const mockGetRepoFile = vi.fn();
const mockClaudeChat = vi.fn();
const mockRuntimeEvent = vi.fn();
const mockKeepalive = vi.fn();

vi.mock('../../shared/api', () => ({
  assessments: {
    start: (...args) => mockStart(...args),
    execute: (...args) => mockExecute(...args),
    saveRepoFile: (...args) => mockSaveRepoFile(...args),
    getRepoFile: (...args) => mockGetRepoFile(...args),
    claudeChat: (...args) => mockClaudeChat(...args),
    submit: (...args) => mockSubmit(...args),
    runtimeEvent: (...args) => mockRuntimeEvent(...args),
    keepalive: (...args) => mockKeepalive(...args),
  },
}));

vi.mock('../../components/assessment/CodeEditor', () => ({
  default: ({ initialCode, value, onChange, onExecute, onSave, disabled }) => {
    const code = value ?? initialCode;
    return (
      <div data-testid="code-editor">
        <div>editor:{code}</div>
        <textarea
          aria-label="Mock code editor"
          value={code}
          disabled={disabled}
          onChange={(event) => onChange?.(event.target.value)}
        />
        <button type="button" disabled={disabled} onClick={() => onExecute?.(code)}>Run</button>
        <button type="button" disabled={disabled} onClick={() => onSave?.(code)}>Save</button>
      </div>
    );
  },
}));

const AppShellStartDataRaceHarness = ({ staleStartData }) => {
  const [startData, setStartData] = useState(staleStartData);
  const [searchParams] = useSearchParams();
  const activeToken = searchParams.get('token');
  const priorTokenRef = useRef(activeToken);
  const navigate = useNavigate();

  // AppShell currently clears its prior started payload in a passive effect.
  // The route must reject a mismatched payload synchronously, before this runs.
  useEffect(() => {
    if (priorTokenRef.current !== activeToken) {
      setStartData(null);
      priorTokenRef.current = activeToken;
    }
  }, [activeToken]);

  return (
    <>
      <button type="button" onClick={() => navigate('/assessment/live?token=token-b')}>
        Switch to assessment B
      </button>
      <AssessmentLiveRoute startData={startData} />
    </>
  );
};

describe('AssessmentPage live agentic runtime', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    window.localStorage.clear();
    mockExecute.mockResolvedValue({ data: { success: true, stdout: '', stderr: '', error: null, results: [] } });
    mockStart.mockResolvedValue({ data: {} });
    mockSaveRepoFile.mockResolvedValue({ data: { success: true, revision: 'b'.repeat(64) } });
    mockGetRepoFile.mockImplementation((assessmentId, path) => Promise.resolve({
      data: { path, content: '', revision: 'a'.repeat(64) },
    }));
    mockSubmit.mockResolvedValue({ data: { success: true } });
    mockClaudeChat.mockResolvedValue({ data: { success: true, content: 'ok' } });
    mockRuntimeEvent.mockResolvedValue({ data: { recorded: true } });
    mockKeepalive.mockResolvedValue({ data: { success: true, time_remaining: 1200 } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('never initializes token B with token A start data during the AppShell clear race', async () => {
    const staleScenario = 'STALE A SCENARIO MUST NEVER RENDER';
    const freshScenario = 'Fresh B scenario loaded from the B start API';
    const staleStartData = {
      assessment_id: 401,
      token: 'token-a',
      time_remaining: 1200,
      task: {
        name: 'Stale assessment A',
        scenario: staleScenario,
        duration_minutes: 30,
      },
    };
    mockStart.mockResolvedValueOnce({
      data: {
        assessment_id: 402,
        time_remaining: 1200,
        task: {
          name: 'Fresh assessment B',
          scenario: freshScenario,
          duration_minutes: 30,
        },
      },
    });
    render(
      <MemoryRouter initialEntries={['/assessment/live?token=token-a']}>
        <Routes>
          <Route
            path="/assessment/live"
            element={<AppShellStartDataRaceHarness staleStartData={staleStartData} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect((await screen.findAllByText(staleScenario)).length).toBeGreaterThan(0);
    await waitFor(() => expect(mockRuntimeEvent).toHaveBeenCalledWith(
      401,
      'runtime_loaded',
      'token-a',
      {},
      expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
    ));
    mockRuntimeEvent.mockClear();
    const staleFrames = [];
    const observer = new MutationObserver(() => {
      if (document.body.textContent?.includes(staleScenario)) {
        staleFrames.push(document.body.textContent);
      }
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    try {
      fireEvent.click(screen.getByRole('button', { name: 'Switch to assessment B' }));
      expect((await screen.findAllByText(freshScenario)).length).toBeGreaterThan(0);
      await waitFor(() => expect(mockRuntimeEvent).toHaveBeenCalledWith(
        402,
        'runtime_loaded',
        'token-b',
        {},
        expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
      ));
    } finally {
      observer.disconnect();
    }

    expect(mockStart).toHaveBeenCalledWith('token-b', {
      candidate_session_key: expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
    });
    expect(staleFrames).toEqual([]);
    expect(screen.queryByText(staleScenario)).not.toBeInTheDocument();
    expect(mockRuntimeEvent.mock.calls.some((call) => (
      call[0] === 401 || call[2] === 'token-a'
    ))).toBe(false);
  });

  it('ignores a late token A start response after token B is already active', async () => {
    let resolveTokenA;
    const tokenAStart = new Promise((resolve) => {
      resolveTokenA = resolve;
    });
    mockStart.mockImplementation((token) => {
      if (token === 'token-a') return tokenAStart;
      return Promise.resolve({
        data: {
          assessment_id: 412,
          time_remaining: 1200,
          task: {
            name: 'Assessment B',
            scenario: 'CURRENT B SCENARIO',
            duration_minutes: 30,
          },
        },
      });
    });
    window.history.replaceState(null, '', '/assessment/live?token=token-a');
    const replaceStateSpy = vi.spyOn(window.history, 'replaceState');

    try {
      const view = render(<AssessmentPage token="token-a" />);
      await waitFor(() => expect(mockStart).toHaveBeenCalledWith('token-a', {
        candidate_session_key: expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
      }));

      window.history.pushState(null, '', '/assessment/live?token=token-b');
      view.rerender(<AssessmentPage token="token-b" />);

      expect((await screen.findAllByText('CURRENT B SCENARIO')).length).toBeGreaterThan(0);
      await waitFor(() => expect(recoverCandidateRuntimeToken()).toBe('token-b'));
      expect(window.location.search).toBe('');
      replaceStateSpy.mockClear();
      mockRuntimeEvent.mockClear();

      await act(async () => {
        resolveTokenA({
          data: {
            assessment_id: 411,
            time_remaining: 1200,
            task: {
              name: 'Assessment A',
              scenario: 'STALE A RESPONSE',
              duration_minutes: 30,
            },
          },
        });
      });

      expect(recoverCandidateRuntimeToken()).toBe('token-b');
      expect(replaceStateSpy).not.toHaveBeenCalled();
      expect(screen.queryByText('STALE A RESPONSE')).not.toBeInTheDocument();
      expect(mockRuntimeEvent.mock.calls.some((call) => (
        call[0] === 411 || call[2] === 'token-a'
      ))).toBe(false);
    } finally {
      replaceStateSpy.mockRestore();
      window.history.replaceState(null, '', '/');
    }
  });

  it('drops start data without an identifying token before loading an explicit route token', async () => {
    const ambiguousScenario = 'AMBIGUOUS START DATA MUST NEVER RENDER';
    const freshScenario = 'Explicit token B loaded from its own start API';
    mockStart.mockResolvedValueOnce({
      data: {
        assessment_id: 404,
        time_remaining: 1200,
        task: {
          name: 'Explicit assessment B',
          scenario: freshScenario,
          duration_minutes: 30,
        },
      },
    });

    render(
      <MemoryRouter initialEntries={['/assessment/live?token=token-b']}>
        <Routes>
          <Route
            path="/assessment/live"
            element={(
              <AssessmentLiveRoute startData={{
                assessment_id: 403,
                time_remaining: 1200,
                task: {
                  name: 'Ambiguous prior assessment',
                  scenario: ambiguousScenario,
                  duration_minutes: 30,
                },
              }} />
            )}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect((await screen.findAllByText(freshScenario)).length).toBeGreaterThan(0);
    expect(mockStart).toHaveBeenCalledWith('token-b', {
      candidate_session_key: expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
    });
    expect(screen.queryByText(ambiguousScenario)).not.toBeInTheDocument();
    await waitFor(() => expect(mockRuntimeEvent).toHaveBeenCalledWith(
      404,
      'runtime_loaded',
      'token-b',
      {},
      expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
    ));
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

  it('tells the candidate the workspace records advisory signals with proctoring off', async () => {
    render(<AssessmentPage token="disclosure-token" startData={{
      assessment_id: 17,
      token: 'disclosure-token',
      time_remaining: 1800,
      task: { name: 'Debug task', starter_code: '', duration_minutes: 30 },
    }} />);

    // The workspace-control layer emits copy_attempt/visibility_hidden/etc even
    // with proctoring off, so the footer must not claim transcript-only.
    const disclosure = await screen.findByTestId('assessment-recording-disclosure');
    expect(disclosure).toHaveTextContent(/keep the assessment fair/i);
    expect(disclosure).toHaveTextContent(/when the tab loses focus/i);
    expect(disclosure).toHaveTextContent(/do not record your screen, camera, or microphone/i);
    expect(screen.getByText('Transcript + activity metrics')).toBeInTheDocument();
    expect(screen.queryByText('Session transcript only')).not.toBeInTheDocument();
  });

  it('keeps keyboard focus inside the submit confirmation and closes on Escape', async () => {
    render(<AssessmentPage token="dialog-token" startData={{
      assessment_id: 11,
      token: 'dialog-token',
      time_remaining: 1800,
      task: { name: 'Debug task', starter_code: '', duration_minutes: 30 },
    }} />);

    fireEvent.click((await screen.findAllByRole('button', { name: 'Submit' }))[0]);
    const dialog = await screen.findByRole('dialog');
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    const confirm = screen.getAllByRole('button', { name: 'Submit' }).at(-1);
    expect(cancel).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(confirm).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(cancel).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(dialog).not.toBeInTheDocument();
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

  it('keeps brief-to-Claude copy and paste inside the protected workspace', async () => {
    const startData = {
      assessment_id: 55,
      token: 'workspace-token',
      time_remaining: 1800,
      task: {
        name: 'Stateful repair task',
        scenario: 'Trace the failing contract before changing the implementation.',
        starter_code: '',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="workspace-token" startData={startData} />);

    expect(await screen.findByTestId('assessment-workspace-security-banner')).toBeInTheDocument();
    expect(screen.getByTestId('assessment-workspace-marker')).toHaveTextContent(/^WS-A1J-[A-Z0-9]{4}$/);

    const scenario = screen.getAllByText('Trace the failing contract before changing the implementation.').at(-1);
    const range = document.createRange();
    range.selectNodeContents(scenario);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    fireEvent.copy(scenario);

    const composer = screen.getByRole('textbox', { name: 'Chat message' });
    await act(async () => {
      fireEvent.paste(composer, {
        clipboardData: { getData: () => 'outside clipboard content' },
      });
    });

    expect(composer).toHaveValue('Trace the failing contract before changing the implementation.');
    expect(mockRuntimeEvent).toHaveBeenCalledWith(55, 'copy_attempt', 'workspace-token', {
      source: 'brief',
      length: 62,
    }, expect.any(String));
    expect(mockRuntimeEvent).toHaveBeenCalledWith(55, 'internal_paste', 'workspace-token', {
      source: 'claude',
      length: 62,
    }, expect.any(String));
  });

  it('blocks print and drag/drop with advisory, content-free telemetry', async () => {
    const startData = {
      assessment_id: 56,
      token: 'workspace-token-2',
      time_remaining: 1800,
      task: {
        name: 'Stateful repair task',
        starter_code: '',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="workspace-token-2" startData={startData} />);
    const banner = await screen.findByTestId('assessment-workspace-security-banner');

    fireEvent.keyDown(banner, { key: 'p', ctrlKey: true });
    fireEvent.drop(banner, {
      dataTransfer: { files: [new File(['secret'], 'external.py')] },
    });

    expect(mockRuntimeEvent).toHaveBeenCalledWith(56, 'print_attempt', 'workspace-token-2', {
      source: 'keyboard',
      length: 0,
    }, expect.any(String));
    expect(mockRuntimeEvent).toHaveBeenCalledWith(56, 'drag_drop_blocked', 'workspace-token-2', {
      source: 'workspace',
      length: 1,
    }, expect.any(String));
    expect(JSON.stringify(mockRuntimeEvent.mock.calls)).not.toContain('secret');
  });

  it('honors an approved external-clipboard accommodation flag', async () => {
    const startData = {
      assessment_id: 57,
      token: 'accommodation-token',
      time_remaining: 1800,
      allow_external_clipboard: true,
      task: {
        name: 'Accessible repair task',
        starter_code: '',
        duration_minutes: 30,
      },
    };

    render(<AssessmentPage token="accommodation-token" startData={startData} />);

    expect(await screen.findByText('Assessment brief')).toBeInTheDocument();
    expect(screen.queryByTestId('assessment-workspace-security-banner')).not.toBeInTheDocument();
    expect(screen.queryByTestId('assessment-workspace-watermark')).not.toBeInTheDocument();
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

  it('renders a manifest immediately and lazy-loads selected files once, including empty files', async () => {
    let resolveMainFile;
    mockGetRepoFile.mockReturnValueOnce(new Promise((resolve) => {
      resolveMainFile = resolve;
    }));

    const startData = {
      assessment_id: 120,
      time_remaining: 1200,
      task: {
        name: 'Lazy repository task',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/main.py': '',
            'src/empty.py': '',
          },
        },
      },
    };

    render(<AssessmentPage token="lazy-token" startData={startData} />);

    expect(await screen.findByRole('button', { name: /^main\.py$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^empty\.py$/i })).toBeInTheDocument();
    expect(mockGetRepoFile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /^main\.py$/i }));
    expect(await screen.findByText('Loading file')).toBeInTheDocument();
    expect(screen.queryByTestId('code-editor')).not.toBeInTheDocument();
    expect(mockGetRepoFile).toHaveBeenCalledWith(
      120,
      'src/main.py',
      'lazy-token',
      expect.stringMatching(/^[A-Za-z0-9_-]{32,}$/),
    );

    await act(async () => {
      resolveMainFile({ data: { path: 'src/main.py', content: 'print("from sandbox")' } });
    });
    expect(await screen.findByRole('textbox', { name: 'Mock code editor' })).toHaveValue('print("from sandbox")');

    fireEvent.click(screen.getByRole('button', { name: /^empty\.py$/i }));
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Mock code editor' })).toHaveValue(''));
    expect(screen.getByTestId('code-editor')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^main\.py$/i }));
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Mock code editor' })).toHaveValue('print("from sandbox")'));
    expect(mockGetRepoFile).toHaveBeenCalledTimes(2);
  });

  it('lazy-loads an initially selected manifest file', async () => {
    mockGetRepoFile.mockImplementationOnce((assessmentId, path) => Promise.resolve({
      data: { path, content: '# decision log' },
    }));
    const startData = {
      assessment_id: 121,
      initial_selected_repo_path: 'DECISION.md',
      time_remaining: 1200,
      task: {
        name: 'Initial deliverable task',
        duration_minutes: 30,
        repo_structure: { files: { 'DECISION.md': '' } },
      },
    };

    render(<AssessmentPage token="initial-file-token" startData={startData} />);

    expect(await screen.findByRole('textbox', { name: 'Mock code editor' })).toHaveValue('# decision log');
    expect(mockGetRepoFile).toHaveBeenCalledTimes(1);
    expect(mockGetRepoFile.mock.calls[0].slice(0, 3)).toEqual([
      121,
      'DECISION.md',
      'initial-file-token',
    ]);
  });

  it('preserves edits across files, syncs only changed files, and submits no repository export', async () => {
    const fileContents = {
      'src/one.py': 'one = 1',
      'src/two.py': 'two = 2',
    };
    mockGetRepoFile.mockImplementation((assessmentId, path) => Promise.resolve({
      data: {
        path,
        content: fileContents[path],
        revision: (path.endsWith('one.py') ? '1' : '2').repeat(64),
      },
    }));
    const startData = {
      assessment_id: 122,
      time_remaining: 1200,
      task: {
        name: 'Multi-file repair',
        duration_minutes: 30,
        repo_structure: {
          files: {
            'src/one.py': '',
            'src/two.py': '',
          },
        },
      },
    };

    render(<AssessmentPage token="submit-sandbox-token" startData={startData} />);

    fireEvent.click(await screen.findByRole('button', { name: /^one\.py$/i }));
    const editor = await screen.findByRole('textbox', { name: 'Mock code editor' });
    fireEvent.change(editor, { target: { value: 'one = 10' } });

    fireEvent.click(screen.getByRole('button', { name: /^two\.py$/i }));
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Mock code editor' })).toHaveValue('two = 2'));
    fireEvent.change(screen.getByRole('textbox', { name: 'Mock code editor' }), {
      target: { value: 'two = 20' },
    });

    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]);
    });
    await screen.findByRole('dialog');
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));
    });

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSaveRepoFile.mock.calls.map((call) => call[1])).toEqual([
      { path: 'src/one.py', content: 'one = 10', base_revision: '1'.repeat(64) },
      { path: 'src/two.py', content: 'two = 20', base_revision: '2'.repeat(64) },
    ]);
    expect(mockSaveRepoFile.mock.calls.every((call) => (
      /^[A-Za-z0-9_-]{32,}$/.test(call[3])
    ))).toBe(true);
    expect(mockSubmit.mock.calls[0][1]).toMatchObject({
      final_code: 'two = 20',
      selected_file_path: 'src/two.py',
    });
    expect(mockSubmit.mock.calls[0][1]).not.toHaveProperty('repo_files');
    expect(mockGetRepoFile).toHaveBeenCalledTimes(2);
    await waitFor(() => {
      expect(window.sessionStorage.length).toBe(0);
      expect(
        Array.from({ length: window.localStorage.length }, (_, index) => window.localStorage.key(index))
          .filter((key) => key?.startsWith('taali.assessment.session.recovery.')),
      ).toEqual([]);
    });
  });

  it('autosaves a dirty file with its revision and warns before leaving', async () => {
    const revision = 'a'.repeat(64);
    mockGetRepoFile.mockResolvedValueOnce({
      data: { path: 'src/main.py', content: 'value = 1', revision },
    });
    const startData = {
      assessment_id: 123,
      initial_selected_repo_path: 'src/main.py',
      time_remaining: 1200,
      task: {
        name: 'Autosave task',
        duration_minutes: 30,
        repo_structure: { files: { 'src/main.py': '' } },
      },
    };

    render(<AssessmentPage token="autosave-token" startData={startData} />);
    const editor = await screen.findByRole('textbox', { name: 'Mock code editor' });
    fireEvent.change(editor, { target: { value: 'value = 2' } });

    expect(screen.getByTestId('assessment-save-state')).toHaveTextContent('Autosave pending');
    const beforeUnload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(beforeUnload);
    expect(beforeUnload.defaultPrevented).toBe(true);

    await waitFor(() => expect(mockSaveRepoFile).toHaveBeenCalledTimes(1), { timeout: 3000 });
    expect(mockSaveRepoFile.mock.calls[0][1]).toEqual({
      path: 'src/main.py',
      content: 'value = 2',
      base_revision: revision,
    });
    await waitFor(() => expect(screen.getByTestId('assessment-save-state')).toHaveTextContent(/Saved/i));
  });

  it('flushes before Claude, locks the editor, then refreshes changed files', async () => {
    const initialRevision = 'a'.repeat(64);
    const savedRevision = 'b'.repeat(64);
    const claudeRevision = 'c'.repeat(64);
    mockGetRepoFile
      .mockResolvedValueOnce({
        data: { path: 'src/main.py', content: 'value = 1', revision: initialRevision },
      })
      .mockResolvedValueOnce({
        data: { path: 'src/main.py', content: 'value = 3', revision: claudeRevision },
      });
    mockSaveRepoFile.mockResolvedValueOnce({ data: { success: true, revision: savedRevision } });
    let resolveClaude;
    mockClaudeChat.mockImplementationOnce(() => new Promise((resolve) => {
      resolveClaude = resolve;
    }));
    const startData = {
      assessment_id: 124,
      initial_selected_repo_path: 'src/main.py',
      time_remaining: 1200,
      task: {
        name: 'Claude refresh task',
        duration_minutes: 30,
        repo_structure: { files: { 'src/main.py': '' } },
      },
    };

    render(<AssessmentPage token="claude-refresh-token" startData={startData} />);
    const editor = await screen.findByRole('textbox', { name: 'Mock code editor' });
    fireEvent.change(editor, { target: { value: 'value = 2' } });
    const prompt = screen.getByRole('textbox', { name: 'Chat message' });
    fireEvent.change(prompt, { target: { value: 'Finish this change' } });
    fireEvent.click(screen.getByRole('button', { name: /Send/i }));

    await waitFor(() => expect(mockClaudeChat).toHaveBeenCalledTimes(1));
    expect(mockSaveRepoFile.mock.calls[0][1]).toEqual({
      path: 'src/main.py',
      content: 'value = 2',
      base_revision: initialRevision,
    });
    expect(editor).toBeDisabled();

    await act(async () => {
      resolveClaude({
        data: {
          content: 'Updated the implementation.',
          changed_paths: [{ path: 'src/main.py', revision: claudeRevision }],
        },
      });
    });

    await waitFor(() => expect(editor).toHaveValue('value = 3'));
    expect(editor).not.toBeDisabled();
    expect(mockGetRepoFile).toHaveBeenCalledTimes(2);
  });

  it('removes a file when Claude reports a null revision', async () => {
    mockGetRepoFile.mockResolvedValueOnce({
      data: { path: 'src/old.py', content: 'legacy = true', revision: 'a'.repeat(64) },
    });
    mockClaudeChat.mockResolvedValueOnce({
      data: {
        content: 'Removed the obsolete file.',
        changed_paths: [{ path: 'src/old.py', revision: null }],
      },
    });
    render(<AssessmentPage token="delete-token" startData={{
      assessment_id: 126,
      initial_selected_repo_path: 'src/old.py',
      time_remaining: 1200,
      task: {
        name: 'Delete obsolete file',
        duration_minutes: 30,
        repo_structure: { files: { 'src/old.py': '' } },
      },
    }} />);

    await screen.findByRole('textbox', { name: 'Mock code editor' });
    fireEvent.change(screen.getByRole('textbox', { name: 'Chat message' }), {
      target: { value: 'Remove it' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send/i }));

    await waitFor(() => expect(screen.queryByRole('button', { name: /^old\.py$/i })).not.toBeInTheDocument());
    expect(screen.queryByRole('textbox', { name: 'Mock code editor' })).not.toBeInTheDocument();
    expect(mockGetRepoFile).toHaveBeenCalledTimes(1);
  });

  it('locks the editor while the immutable submission is being frozen', async () => {
    let resolveSubmit;
    mockSubmit.mockImplementationOnce(() => new Promise((resolve) => {
      resolveSubmit = resolve;
    }));
    mockGetRepoFile.mockResolvedValueOnce({
      data: { path: 'src/main.py', content: 'value = 1', revision: 'a'.repeat(64) },
    });
    render(<AssessmentPage token="freeze-token" startData={{
      assessment_id: 127,
      initial_selected_repo_path: 'src/main.py',
      time_remaining: 1200,
      task: {
        name: 'Freeze task',
        duration_minutes: 30,
        repo_structure: { files: { 'src/main.py': '' } },
      },
    }} />);

    const editor = await screen.findByRole('textbox', { name: 'Mock code editor' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]);
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(editor).toBeDisabled();
    await act(async () => resolveSubmit({ data: { success: true } }));
  });

  it('starts the deadline submission five seconds early and freezes the latest edit', async () => {
    const warmup = render(<AssessmentPage token="deadline-warmup-token" startData={{
      assessment_id: 999,
      initial_selected_repo_path: 'src/main.py',
      time_remaining: 60,
      task: {
        name: 'Deadline warmup',
        duration_minutes: 1,
        repo_structure: { files: { 'src/main.py': '' } },
      },
    }} />);
    await screen.findByRole('textbox', { name: 'Mock code editor' });
    warmup.unmount();
    vi.clearAllMocks();
    vi.useFakeTimers();

    const revision = 'a'.repeat(64);
    let resolveSubmit;
    mockSubmit.mockImplementationOnce(() => new Promise((resolve) => {
      resolveSubmit = resolve;
    }));
    mockGetRepoFile.mockResolvedValueOnce({
      data: { path: 'src/main.py', content: 'value = 1', revision },
    });
    render(<AssessmentPage token="deadline-token" startData={{
      assessment_id: 128,
      initial_selected_repo_path: 'src/main.py',
      time_remaining: 6,
      task: {
        name: 'Deadline task',
        duration_minutes: 1,
        repo_structure: { files: { 'src/main.py': '' } },
      },
    }} />);

    await act(async () => {});
    const editor = screen.getByRole('textbox', { name: 'Mock code editor' });
    fireEvent.change(editor, { target: { value: 'value = 2' } });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(mockSaveRepoFile).toHaveBeenCalledTimes(1);
    expect(mockSaveRepoFile.mock.calls[0][1]).toEqual({
      path: 'src/main.py',
      content: 'value = 2',
      base_revision: revision,
    });
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(mockSubmit.mock.calls[0][1]).toMatchObject({
      final_code: 'value = 2',
      selected_file_path: 'src/main.py',
    });
    expect(mockSubmit.mock.calls[0][1]).not.toHaveProperty('repo_files');
    expect(editor).toBeDisabled();
    expect(screen.getByTestId('assessment-submit-status')).toHaveTextContent(/Finalizing your latest work/i);

    await act(async () => {
      resolveSubmit({ data: { success: true, grading_status: 'pending' } });
    });
    expect(screen.getByRole('heading', { name: /Task submitted/i })).toBeInTheDocument();
  });

  it.each([
    ['the timeout finalizer reports a conflict', {
      status: 409,
      data: { detail: 'Assessment time expired and was auto-submitted' },
    }],
    ['another terminal request makes the active-row lookup disappear', {
      status: 404,
      data: { detail: 'Active assessment not found' },
    }],
  ])('retrieves the durable receipt when %s during a slow multi-file flush', async (_label, response) => {
    const warmup = render(<AssessmentPage token="deadline-multi-warmup" startData={{
      assessment_id: 1000,
      initial_selected_repo_path: 'src/one.py',
      time_remaining: 60,
      task: {
        name: 'Deadline multi-file warmup',
        duration_minutes: 1,
        repo_structure: { files: { 'src/one.py': '' } },
      },
    }} />);
    await screen.findByRole('textbox', { name: 'Mock code editor' });
    warmup.unmount();
    vi.clearAllMocks();
    vi.useFakeTimers();

    mockGetRepoFile.mockImplementation((assessmentId, path) => Promise.resolve({
      data: {
        path,
        content: path.endsWith('one.py') ? 'one = 1' : 'two = 2',
        revision: (path.endsWith('one.py') ? '1' : '2').repeat(64),
      },
    }));
    let resolveFirstSave;
    mockSaveRepoFile
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveFirstSave = resolve;
      }))
      .mockRejectedValueOnce(Object.assign(new Error('workspace frozen'), {
        response,
      }));
    mockSubmit.mockResolvedValueOnce({ data: {
      success: true,
      grading_status: 'pending',
      artifact_gate: { status: 'satisfied' },
    } });

    render(<AssessmentPage token="deadline-multi-token" startData={{
      assessment_id: 131,
      initial_selected_repo_path: 'src/one.py',
      time_remaining: 6,
      task: {
        name: 'Deadline multi-file task',
        duration_minutes: 1,
        repo_structure: {
          files: {
            'src/one.py': '',
            'src/two.py': '',
          },
        },
      },
    }} />);

    await act(async () => {});
    const editor = screen.getByRole('textbox', { name: 'Mock code editor' });
    fireEvent.change(editor, { target: { value: 'one = 10' } });
    fireEvent.click(screen.getByRole('button', { name: /^two\.py$/i }));
    await act(async () => {});
    fireEvent.change(screen.getByRole('textbox', { name: 'Mock code editor' }), {
      target: { value: 'two = 20' },
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(mockSaveRepoFile).toHaveBeenCalledTimes(1);
    expect(mockSubmit).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(mockSaveRepoFile).toHaveBeenCalledTimes(1);
    expect(mockSubmit).not.toHaveBeenCalled();

    await act(async () => {
      resolveFirstSave({ data: { success: true, revision: '3'.repeat(64) } });
    });

    expect(mockSaveRepoFile).toHaveBeenCalledTimes(2);
    expect(mockSaveRepoFile.mock.calls.map((call) => call[1])).toEqual([
      { path: 'src/one.py', content: 'one = 10', base_revision: '1'.repeat(64) },
      { path: 'src/two.py', content: 'two = 20', base_revision: '2'.repeat(64) },
    ]);
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('heading', { name: /Task submitted/i })).toBeInTheDocument();
    expect(screen.getByText(/snapshot was already locked.*not included/i)).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(mockSubmit).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['network failure', new Error('network unavailable'), /save your changes/i],
    ['revision conflict', Object.assign(new Error('revision conflict'), {
      response: {
        status: 409,
        data: { detail: {
          code: 'FILE_REVISION_CONFLICT',
          message: 'This file changed in the workspace. Review it before overwriting.',
        } },
      },
    }), /file changed in the workspace/i],
    ['live workspace lease', Object.assign(new Error('workspace busy'), {
      response: {
        status: 409,
        data: { detail: 'Another workspace operation is still in progress. Please retry shortly.' },
      },
    }), /workspace operation is still in progress/i],
    ['unrelated missing resource', Object.assign(new Error('missing resource'), {
      response: {
        status: 404,
        data: { detail: 'Repository file not found' },
      },
    }), /repository file not found/i],
  ])('does not submit past a dirty-file %s', async (_label, saveError, expectedMessage) => {
    mockGetRepoFile.mockResolvedValueOnce({
      data: { path: 'src/main.py', content: 'value = 1', revision: 'a'.repeat(64) },
    });
    mockSaveRepoFile.mockRejectedValueOnce(saveError);
    render(<AssessmentPage token="save-failure-token" startData={{
      assessment_id: 132,
      initial_selected_repo_path: 'src/main.py',
      time_remaining: 1200,
      task: {
        name: 'Save failure task',
        duration_minutes: 30,
        repo_structure: { files: { 'src/main.py': '' } },
      },
    }} />);

    const editor = await screen.findByRole('textbox', { name: 'Mock code editor' });
    fireEvent.change(editor, { target: { value: 'value = 2' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]);
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));

    await waitFor(() => expect(mockSaveRepoFile).toHaveBeenCalledTimes(1));
    expect(mockSubmit).not.toHaveBeenCalled();
    await waitFor(() => expect(editor).not.toBeDisabled());
    expect(editor).toHaveValue('value = 2');
    expect(screen.getByTestId('assessment-submit-error')).toHaveTextContent(expectedMessage);
  });

  it('keeps the workspace open when an exact terminal 404 has no durable receipt', async () => {
    mockGetRepoFile.mockResolvedValueOnce({
      data: { path: 'src/main.py', content: 'value = 1', revision: 'a'.repeat(64) },
    });
    mockSaveRepoFile.mockRejectedValueOnce(Object.assign(new Error('active row gone'), {
      response: {
        status: 404,
        data: { detail: 'Active assessment not found' },
      },
    }));
    mockSubmit.mockRejectedValueOnce(Object.assign(new Error('receipt unavailable'), {
      response: {
        status: 409,
        data: { detail: 'Submission receipt is not available' },
      },
    }));
    render(<AssessmentPage token="missing-receipt-token" startData={{
      assessment_id: 133,
      initial_selected_repo_path: 'src/main.py',
      time_remaining: 1200,
      task: {
        name: 'Missing receipt task',
        duration_minutes: 30,
        repo_structure: { files: { 'src/main.py': '' } },
      },
    }} />);

    const editor = await screen.findByRole('textbox', { name: 'Mock code editor' });
    fireEvent.change(editor, { target: { value: 'value = 2' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]);
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(editor).not.toBeDisabled());
    expect(editor).toHaveValue('value = 2');
    expect(screen.queryByRole('heading', { name: /Task submitted/i })).not.toBeInTheDocument();
    expect(screen.getByTestId('assessment-submit-error')).toHaveTextContent(/receipt is not available/i);
  });

  it('retries a lost deadline response once at zero and accepts the idempotent receipt', async () => {
    vi.useFakeTimers();
    mockSubmit
      .mockRejectedValueOnce(new Error('response lost after submission'))
      .mockResolvedValueOnce({ data: { success: true, grading_status: 'pending' } });
    render(<AssessmentPage token="deadline-retry-token" startData={{
      assessment_id: 129,
      time_remaining: 6,
      task: {
        name: 'Deadline retry task',
        starter_code: 'value = 2',
        duration_minutes: 1,
      },
    }} />);

    expect(screen.getByText('00:06 left')).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('assessment-submit-error')).toHaveTextContent(/connection/i);

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(mockSubmit).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('heading', { name: /Task submitted/i })).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(mockSubmit).toHaveBeenCalledTimes(2);
  });

  it('waits for an in-flight safety attempt and never loops after the zero retry fails', async () => {
    vi.useFakeTimers();
    let rejectSafetySubmit;
    mockSubmit
      .mockImplementationOnce(() => new Promise((resolve, reject) => {
        rejectSafetySubmit = reject;
      }))
      .mockRejectedValueOnce(new Error('retry response unavailable'));
    render(<AssessmentPage token="deadline-bounded-token" startData={{
      assessment_id: 130,
      time_remaining: 6,
      task: {
        name: 'Bounded deadline task',
        starter_code: 'value = 3',
        duration_minutes: 1,
      },
    }} />);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(mockSubmit).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(mockSubmit).toHaveBeenCalledTimes(1);

    await act(async () => {
      rejectSafetySubmit(new Error('response lost after submission'));
    });
    expect(mockSubmit).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('assessment-submit-error')).toHaveTextContent(/connection/i);

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(mockSubmit).toHaveBeenCalledTimes(2);
  });

  it('keeps the visible timer running when submission fails', async () => {
    vi.useFakeTimers();
    let rejectSubmit;
    mockSubmit.mockImplementationOnce(() => new Promise((resolve, reject) => {
      rejectSubmit = reject;
    }));
    const startData = {
      assessment_id: 125,
      token: 'timer-token',
      time_remaining: 10,
      task: { name: 'Timer task', duration_minutes: 1 },
    };

    render(<AssessmentPage token="timer-token" startData={startData} />);
    expect(screen.getByText('00:10 left')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]);
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' }).at(-1));
    await act(async () => {});

    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByText('00:09 left')).toBeInTheDocument();
    await act(async () => {
      rejectSubmit(new Error('network unavailable'));
    });
    expect(screen.getByTestId('assessment-submit-error')).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByText('00:08 left')).toBeInTheDocument();
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
    mockGetRepoFile.mockImplementationOnce((assessmentId, path) => Promise.resolve({
      data: { path, content: 'answer = 42', revision: 'd'.repeat(64) },
    }));

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
      base_revision: 'd'.repeat(64),
    });
    expect(mockExecute.mock.calls[0][1]).not.toHaveProperty('repo_files');
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
    mockGetRepoFile.mockImplementationOnce((assessmentId, path) => Promise.resolve({
      data: { path, content: 'broken(' },
    }));

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
