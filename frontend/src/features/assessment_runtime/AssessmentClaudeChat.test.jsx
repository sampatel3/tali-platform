import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { vi } from 'vitest';

import { AssessmentClaudeChat } from './AssessmentClaudeChat';

const mockClaudeChat = vi.fn();

vi.mock('../../shared/api', () => ({
  assessments: {
    claudeChat: (...args) => mockClaudeChat(...args),
  },
}));

const renderChat = (overrides = {}) => render(
  <AssessmentClaudeChat
    assessmentId={42}
    token="candidate-token"
    selectedFilePath="src/main.py"
    codeContext="print('hi')"
    claudeBudget={null}
    onBudgetUpdate={vi.fn()}
    disabled={false}
    {...overrides}
  />,
);

const typeAndSend = async (text) => {
  const textarea = screen.getByRole('textbox');
  await act(async () => {
    fireEvent.change(textarea, { target: { value: text } });
  });
  const sendBtn = screen.getByRole('button', { name: /send/i });
  await act(async () => {
    fireEvent.click(sendBtn);
  });
  return { textarea, sendBtn };
};

describe('AssessmentClaudeChat', () => {
  beforeEach(() => {
    mockClaudeChat.mockReset();
  });

  it('renders an empty conversation with the input visible and send disabled when empty', () => {
    renderChat();

    expect(screen.getByText(/Claude is ready/i)).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled();
  });

  it('shows optimistic user row and a pending row immediately after submit', async () => {
    let resolveCall;
    mockClaudeChat.mockImplementation(() => new Promise((resolve) => {
      resolveCall = resolve;
    }));

    renderChat();
    await typeAndSend('Why is this failing?');

    expect(screen.getByText('Why is this failing?')).toBeInTheDocument();
    const pendingRow = screen.getByTestId('assessment-claude-chat-pending');
    expect(pendingRow).toBeInTheDocument();
    // Working indicator is a live status line (elapsed seconds), not a static
    // "working" string.
    expect(pendingRow).toHaveTextContent('Working');
    expect(screen.getByTestId('assessment-claude-chat-pending-elapsed')).toHaveTextContent(/^\d+s$/);
    expect(mockClaudeChat).toHaveBeenCalledTimes(1);
    const [assessmentId, payload, token] = mockClaudeChat.mock.calls[0];
    expect(assessmentId).toBe(42);
    expect(token).toBe('candidate-token');
    expect(payload).toMatchObject({
      message: 'Why is this failing?',
      code_context: "print('hi')",
      selected_file_path: 'src/main.py',
      paste_detected: false,
    });
    expect(typeof payload.request_id).toBe('string');
    expect(payload.request_id.length).toBeGreaterThan(0);

    // unblock so the test cleanly tears down
    await act(async () => {
      resolveCall({ data: { content: 'done', tool_calls_made: [] } });
    });
  });

  it('replaces the pending row with the assistant content when the request resolves', async () => {
    mockClaudeChat.mockResolvedValue({
      data: {
        content: 'Try removing the duplicate import.',
        tool_calls_made: [],
      },
    });

    renderChat();
    await typeAndSend('Why is this failing?');

    await waitFor(() => {
      expect(screen.queryByTestId('assessment-claude-chat-pending')).not.toBeInTheDocument();
    });
    expect(screen.getByText(/Try removing the duplicate import/i)).toBeInTheDocument();
  });

  it('renders an error row when the request rejects and does not crash', async () => {
    mockClaudeChat.mockRejectedValue(new Error('network blew up'));

    renderChat();
    await typeAndSend('Help');

    await waitFor(() => {
      expect(screen.queryByTestId('assessment-claude-chat-pending')).not.toBeInTheDocument();
    });
    // Raw err.message is never surfaced — a friendly, distinct error row shows instead.
    expect(screen.queryByText(/network blew up/i)).not.toBeInTheDocument();
    expect(screen.getByText(/didn't go through/i)).toBeInTheDocument();
    // user row still there (scoped to the transcript so it doesn't collide
    // with the restored composer text below)
    const list = screen.getByTestId('assessment-claude-chat-messages');
    expect(within(list).getByText(/^Help$/)).toBeInTheDocument();
    // The failed message is restored into the composer so the candidate can
    // retry without retyping.
    expect(screen.getByRole('textbox')).toHaveValue('Help');
  });

  it('hides raw tool-call internals from the candidate (only the model text shows)', async () => {
    // Tool chips were removed 2026-05-26 — Sam called out that the
    // candidate doesn't need to see raw MCP/tool names like
    // ``mcp__sandbox__Bash ls -la``. We persist ``tool_calls_made``
    // in ``ai_prompts`` server-side for analytics; the candidate UI
    // shows only the assistant's narrative reply.
    mockClaudeChat.mockResolvedValue({
      data: {
        content: 'I checked the quality report.',
        tool_calls_made: [
          { name: 'read_file', input: { path: 'diagnostics/quality_report.md' }, result_ok: true },
          { name: 'grep_search', input: { query: 'TODO' }, result_ok: true },
        ],
      },
    });

    renderChat();
    await typeAndSend('Look at the diagnostics');

    await waitFor(() => {
      expect(screen.getByText(/I checked the quality report/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('read_file')).not.toBeInTheDocument();
    expect(screen.queryByText('diagnostics/quality_report.md')).not.toBeInTheDocument();
    expect(screen.queryByText('grep_search')).not.toBeInTheDocument();
  });

  it('caps the rolling buffer at the last 60 messages and marks trimmed history', async () => {
    // Each turn adds one user + one assistant row (2 messages). Send 31
    // turns → 62 messages → only the last 60 survive, and the candidate
    // sees an "Older messages are hidden" marker so nothing is dropped
    // silently.
    mockClaudeChat.mockImplementation(async (_id, payload) => ({
      data: { content: `reply-${payload.message}`, tool_calls_made: [] },
    }));

    renderChat();

    for (let i = 1; i <= 31; i += 1) {
      // sequentially submit
      // eslint-disable-next-line no-await-in-loop
      await typeAndSend(`msg-${i}`);
      // eslint-disable-next-line no-await-in-loop
      await waitFor(() => {
        expect(screen.getByText(`reply-msg-${i}`)).toBeInTheDocument();
      });
    }

    const list = screen.getByTestId('assessment-claude-chat-messages');
    // The earliest turn (msg-1 + its reply) is pruned; msg-2's row is the
    // oldest survivor.
    expect(within(list).queryByText('msg-1')).not.toBeInTheDocument();
    expect(within(list).queryByText('reply-msg-1')).not.toBeInTheDocument();
    expect(within(list).getByText('msg-2')).toBeInTheDocument();
    expect(within(list).getByText('reply-msg-2')).toBeInTheDocument();
    expect(within(list).getByText('msg-31')).toBeInTheDocument();
    expect(within(list).getByText('reply-msg-31')).toBeInTheDocument();
    // Trim marker is shown once older turns are dropped.
    expect(screen.getByTestId('assessment-claude-chat-history-trimmed')).toBeInTheDocument();
  });
});
