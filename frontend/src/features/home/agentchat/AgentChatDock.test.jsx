import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

// Mock the API module so the dock's getTimeline/sendMessage hit our stubs.
const mocks = vi.hoisted(() => ({
  getTimeline: vi.fn(),
  sendMessage: vi.fn(),
  answerNeedsInput: vi.fn().mockResolvedValue({ data: {} }),
  dismissNeedsInput: vi.fn().mockResolvedValue({ data: {} }),
  markRead: vi.fn(),
  listDecisions: vi.fn(),
  approveDecision: vi.fn(),
  overrideDecision: vi.fn(),
  reEvaluateDecision: vi.fn(),
  snoozeDecision: vi.fn(),
  sendFeedback: vi.fn(),
  getWorkableStages: vi.fn(),
}));
vi.mock('../../../shared/api', () => ({
  agentChat: {
    getTimeline: mocks.getTimeline,
    sendMessage: mocks.sendMessage,
    answerNeedsInput: mocks.answerNeedsInput,
    dismissNeedsInput: mocks.dismissNeedsInput,
    markRead: mocks.markRead,
    listConversations: vi.fn().mockResolvedValue({ data: { agents: [] } }),
  },
  agent: {
    listDecisions: mocks.listDecisions,
    approveDecision: mocks.approveDecision,
    overrideDecision: mocks.overrideDecision,
    reEvaluateDecision: mocks.reEvaluateDecision,
    snoozeDecision: mocks.snoozeDecision,
    sendFeedback: mocks.sendFeedback,
  },
  organizations: {
    getWorkableStages: mocks.getWorkableStages,
  },
}));

import { ToastProvider } from '../../../context/ToastContext';
import { AgentChatDock } from './AgentChatDock';
import { AgentSidebar } from './AgentSidebar';

const TIMELINE = [
  { kind: 'message', id: 'm1', author: 'recruiter', text: 'cap salary at 25k', created_at: '2026-06-03T09:00:00Z' },
  {
    kind: 'message', id: 'm2', author: 'agent', text: 'Updated and re-screening.',
    created_at: '2026-06-03T09:00:05Z',
    actions: [{ type: 'constraint_change', action: 'updated', criterion: { text: 'Salary ≤ AED 25,000' }, rescreening_count: 47 }],
  },
  {
    kind: 'needs_input', id: 'q1', needs_input_id: 9, question_kind: 'candidate_tie_break',
    prompt: 'Marcus or Lena?', options: [{ value: 'marcus', label: 'Marcus' }, { value: 'lena', label: 'Lena' }],
    status: 'open', created_at: '2026-06-03T09:01:00Z',
  },
  {
    kind: 'decision', id: 'd1', decision_id: 5, application_id: 55, decision_type: 'reject', candidate_name: 'Tom Hale',
    score: 38, status: 'pending', reasoning: 'below cut-off', created_at: '2026-06-03T08:00:00Z',
  },
];

const FULL_DECISION = {
  id: 5,
  role_id: 1,
  role_name: 'Data Eng',
  application_id: 55,
  decision_type: 'reject',
  recommendation: 'reject',
  candidate_name: 'Tom Hale',
  candidate_email: 'tom@example.com',
  taali_score: 38,
  confidence: 0.88,
  status: 'pending',
  reasoning: 'Below the role cut-off.',
  evidence: {},
  requirements: [],
  is_stale: false,
  staleness_reasons: [],
};

const renderDock = (props = {}) =>
  render(
    <ToastProvider>
      <AgentChatDock roleId={1} roleName="Data Eng" onReload={vi.fn()} {...props} />
    </ToastProvider>
  );

beforeEach(() => {
  mocks.getTimeline.mockReset();
  mocks.sendMessage.mockReset();
  mocks.answerNeedsInput.mockReset();
  mocks.markRead.mockReset();
  mocks.listDecisions.mockReset();
  mocks.approveDecision.mockReset();
  mocks.overrideDecision.mockReset();
  mocks.reEvaluateDecision.mockReset();
  mocks.snoozeDecision.mockReset();
  mocks.sendFeedback.mockReset();
  mocks.getWorkableStages.mockReset();
  mocks.listDecisions.mockResolvedValue({ data: [FULL_DECISION] });
  mocks.approveDecision.mockResolvedValue({ data: { ...FULL_DECISION, status: 'processing' } });
  mocks.overrideDecision.mockResolvedValue({ data: {} });
  mocks.reEvaluateDecision.mockResolvedValue({ data: {} });
  mocks.snoozeDecision.mockResolvedValue({ data: {} });
  mocks.sendFeedback.mockResolvedValue({ data: {} });
  mocks.getWorkableStages.mockResolvedValue({ data: { stages: [] } });
  mocks.answerNeedsInput.mockResolvedValue({ data: {} });
  mocks.markRead.mockResolvedValue({ data: {} });
});

describe('AgentChatDock', () => {
  it('answers a free-form request through reply mode and restores the saved draft', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'needs_input',
          id: 'request-32',
          needs_input_id: 32,
          status: 'open',
          prompt: 'What should I optimise for?',
          input_mode: 'string',
          can_answer: true,
          can_dismiss: false,
        }],
        agent_working: false,
      },
    });
    renderDock();

    const composer = await screen.findByRole('textbox', { name: 'Chat message' });
    fireEvent.change(composer, { target: { value: 'Keep this role draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reply in chat' }));

    const answerBox = screen.getByRole('textbox', { name: 'Answer the agent' });
    expect(answerBox).toHaveFocus();
    expect(answerBox).toHaveValue('');
    fireEvent.change(answerBox, { target: { value: 'Optimise for quality' } });
    fireEvent.keyDown(answerBox, { key: 'Enter' });

    await waitFor(() => {
      expect(mocks.answerNeedsInput).toHaveBeenCalledWith(32, { value: 'Optimise for quality' });
    });
    expect(mocks.sendMessage).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox', { name: 'Chat message' }))
      .toHaveValue('Keep this role draft');
  });

  it('puts a helper quick reply in the composer without sending it', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'helper-message',
          author: 'agent',
          message_kind: 'proactive',
          text: 'Five candidates are close to the cut-off.\n\nWould you like to review them?',
          actions: [{
            type: 'helper_prompt',
            title: 'Review the close calls',
            summary: 'Five candidates are close to the cut-off.',
            question: 'Would you like to review them?',
            suggestions: [{ label: 'Show me', prompt: 'Show me the candidates just below the cut-off.' }],
          }],
        }],
      },
    });
    renderDock();

    fireEvent.click(await screen.findByRole('button', { name: 'Show me' }));

    expect(screen.getAllByText('Five candidates are close to the cut-off.')).toHaveLength(1);
    expect(screen.getAllByText('Would you like to review them?')).toHaveLength(1);
    const composer = screen.getByPlaceholderText(/Message the Data Eng agent/);
    expect(composer).toHaveValue(
      'Show me the candidates just below the cut-off.',
    );
    expect(composer).toHaveFocus();
    expect(screen.getByText('Added to composer')).toHaveAttribute('aria-live', 'polite');
    expect(mocks.sendMessage).not.toHaveBeenCalled();
  });

  it('keeps distinct assistant prose when a helper card comes from an interactive chat turn', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'interactive-helper',
          author: 'agent',
          message_kind: 'chat',
          text: 'I checked the current scores before suggesting this.',
          actions: [{
            type: 'helper_prompt',
            title: 'Review the close calls',
            summary: 'Five candidates are close to the cut-off.',
            question: 'Would you like to review them?',
            suggestions: [{ label: 'Show me', prompt: 'Show me the candidates just below the cut-off.' }],
          }],
        }],
      },
    });
    renderDock();

    expect(await screen.findByText('I checked the current scores before suggesting this.')).toBeInTheDocument();
    expect(screen.getByText('Five candidates are close to the cut-off.')).toBeInTheDocument();
  });

  it('renders a dedicated agent event once and only prefills its follow-up prompt', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'event-message',
          author: 'agent',
          message_kind: 'event',
          text: 'The scheduled review did not finish.\n\nNo candidate state changed.',
          actions: [{
            type: 'agent_event',
            event_type: 'run_failed',
            severity: 'error',
            title: 'The scheduled review did not finish',
            summary: 'No candidate state changed.',
            details: [{ label: 'Reason', value: 'Provider timeout' }],
            source: { type: 'agent_run', id: 91 },
            occurred_at: '2026-07-15T08:30:00Z',
            suggestions: [{ label: 'Investigate', prompt: 'Investigate the failed scheduled review.' }],
          }],
        }],
      },
    });
    renderDock();

    expect(await screen.findByRole('article', {
      name: 'Error agent event: The scheduled review did not finish',
    })).toBeInTheDocument();
    expect(screen.getAllByText('The scheduled review did not finish')).toHaveLength(1);
    expect(screen.getAllByText('No candidate state changed.')).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: /Investigate/ }));
    expect(screen.getByPlaceholderText(/Message the Data Eng agent/)).toHaveValue(
      'Investigate the failed scheduled review.',
    );
    expect(mocks.sendMessage).not.toHaveBeenCalled();
  });

  it('keeps assistant prose when an event card is part of a normal chat reply', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'chat-with-event',
          author: 'agent',
          message_kind: 'chat',
          text: 'I also checked the previous run before answering.',
          actions: [{
            type: 'agent_event',
            event_type: 'run_completed',
            severity: 'success',
            title: 'The review completed',
            summary: 'Twelve candidates were checked.',
          }],
        }],
      },
    });
    renderDock();

    expect(await screen.findByText('I also checked the previous run before answering.')).toBeInTheDocument();
    expect(screen.getByText('The review completed')).toBeInTheDocument();
  });

  it('acknowledges the selected thread only after it has visibly settled', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: { timeline: [{ kind: 'message', id: 'ready', author: 'agent', text: 'Ready to help.' }] },
    });
    renderDock();

    expect(await screen.findByText('Ready to help.')).toBeInTheDocument();
    expect(mocks.markRead).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.markRead).toHaveBeenCalledWith(1), { timeout: 1600 });
  });

  it('clears the previous role while the newly selected role is loading', async () => {
    let resolveSecondRole;
    mocks.getTimeline.mockImplementation((roleId) => {
      if (roleId === 1) {
        return Promise.resolve({
          data: { timeline: [{ kind: 'message', id: 'role-one', author: 'agent', text: 'Role one advice' }] },
        });
      }
      return new Promise((resolve) => { resolveSecondRole = resolve; });
    });
    const view = renderDock();
    expect(await screen.findByText('Role one advice')).toBeInTheDocument();

    view.rerender(
      <ToastProvider>
        <AgentChatDock roleId={2} roleName="Platform Eng" onReload={vi.fn()} />
      </ToastProvider>,
    );

    await waitFor(() => expect(screen.queryByText('Role one advice')).not.toBeInTheDocument());
    expect(screen.getByText('Loading the conversation…')).toBeInTheDocument();
    await act(async () => {
      resolveSecondRole({ data: { timeline: [], agent_working: false } });
    });
  });

  it('replaces a failed initial load with a retry control', async () => {
    mocks.getTimeline
      .mockRejectedValueOnce(new Error('timeout'))
      .mockResolvedValueOnce({
        data: {
          timeline: [{ kind: 'message', id: 'recovered', author: 'agent', text: 'Back online.' }],
          agent_working: false,
        },
      });
    renderDock();

    const retry = await screen.findByRole('button', { name: 'Try again' });
    expect(screen.queryByText('Loading the conversation…')).not.toBeInTheDocument();
    fireEvent.click(retry);

    expect(await screen.findByText('Back online.')).toBeInTheDocument();
    expect(mocks.getTimeline).toHaveBeenCalledTimes(2);
  });

  it('renders chat, impact, question, and decision items in timeline order', async () => {
    mocks.getTimeline.mockResolvedValue({ data: { timeline: TIMELINE } });
    renderDock();

    expect(await screen.findByText('Updated and re-screening.')).toBeInTheDocument();
    // Impact card from the agent message.
    expect(screen.getByText('Salary ≤ AED 25,000')).toBeInTheDocument();
    expect(screen.getByText(/re-screening 47/)).toBeInTheDocument();
    // The agent's question + its options.
    expect(screen.getByText('Marcus or Lena?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Marcus' })).toBeInTheDocument();
    const questionShortcut = screen.getByRole('button', { name: '1 question needs your input' });
    expect(questionShortcut).toBeInTheDocument();
    const questionCard = screen.getByRole('article', { name: 'Choose who to prioritise' });
    questionCard.scrollIntoView = vi.fn();
    const focusQuestion = vi.spyOn(questionCard, 'focus');
    fireEvent.click(questionShortcut);
    expect(questionCard.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' });
    expect(focusQuestion).toHaveBeenCalledWith({ preventScroll: true });
    // HITL decisions now stay in the same role thread.
    expect(await screen.findByText('Tom Hale')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Candidate report' })).toHaveAttribute('href', '/candidates/55?from=home');
  });

  it('approves an embedded decision through the canonical decision API', async () => {
    mocks.getTimeline.mockResolvedValue({ data: { timeline: [TIMELINE[3]] } });
    renderDock();

    const approve = await screen.findByRole('button', { name: 'Reject' });
    await waitFor(() => expect(approve).toBeEnabled());
    fireEvent.click(approve);

    await waitFor(() => {
      expect(mocks.approveDecision).toHaveBeenCalledWith(5, {}, { force: false });
    });
  });

  it('sends a message and renders the agent reply', async () => {
    mocks.getTimeline.mockResolvedValue({ data: { timeline: [] } });
    mocks.sendMessage.mockResolvedValue({
      data: { timeline: [{ kind: 'message', id: 'a1', author: 'agent', text: 'Cut-off is now 60.', created_at: '2026-06-03T09:02:00Z' }] },
    });
    renderDock();

    // Empty state first.
    expect(await screen.findByText(/What should this agent do/)).toBeInTheDocument();

    // Type into the shared composer and press Enter to send. The placeholder
    // names the active role, matching the home-preview ("Message the {role} agent…").
    const ta = screen.getByPlaceholderText(/Message the Data Eng agent/);
    fireEvent.change(ta, { target: { value: 'what if I drop the cut-off to 60?' } });
    fireEvent.keyDown(ta, { key: 'Enter' });

    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalledWith(1, 'what if I drop the cut-off to 60?'));
    expect(await screen.findByText('Cut-off is now 60.')).toBeInTheDocument();
  });

  it('preserves an unpinned reader and surfaces a new agent update without announcing history', async () => {
    const history = {
      kind: 'message',
      id: 'history-agent',
      author: 'agent',
      text: 'Existing history',
      created_at: '2026-07-15T08:00:00Z',
    };
    mocks.getTimeline.mockResolvedValue({ data: { timeline: [history], agent_working: false } });
    mocks.sendMessage.mockResolvedValue({
      data: {
        timeline: [
          history,
          { kind: 'message', id: 'user-2', author: 'recruiter', text: 'Any change?' },
          { kind: 'message', id: 'agent-2', author: 'agent', text: 'Three candidates moved forward.' },
        ],
        agent_working: false,
      },
    });
    renderDock();

    expect(await screen.findByText('Existing history')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'New agent update' })).not.toBeInTheDocument();
    const updateStatus = document.querySelector('.tk-new-update-status');
    expect(updateStatus).toBeEmptyDOMElement();

    const stream = document.querySelector('.ac-stream');
    Object.defineProperties(stream, {
      scrollHeight: { configurable: true, value: 1200 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 200 },
    });
    fireEvent.scroll(stream);

    const composer = screen.getByPlaceholderText(/Message the Data Eng agent/);
    fireEvent.change(composer, { target: { value: 'Any change?' } });
    fireEvent.keyDown(composer, { key: 'Enter' });

    expect(await screen.findByText('Three candidates moved forward.')).toBeInTheDocument();
    const notice = await screen.findByRole('button', { name: 'New agent update' });
    expect(stream.scrollTop).toBe(200);
    expect(updateStatus).toHaveTextContent('New agent update');
    expect(notice).toHaveAttribute('aria-controls', stream.id);

    fireEvent.click(notice);
    expect(stream.scrollTop).toBe(1200);
    expect(updateStatus).toBeEmptyDOMElement();
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'New agent update' })).not.toBeInTheDocument();
    });
  });

  it('answers an agent question via an option button', async () => {
    mocks.getTimeline.mockResolvedValue({ data: { timeline: [TIMELINE[2]] } });
    renderDock();

    fireEvent.click(await screen.findByRole('button', { name: 'Marcus' }));
    await waitFor(() =>
      expect(mocks.answerNeedsInput).toHaveBeenCalledWith(9, { value: 'marcus', label: 'Marcus' })
    );
  });

  it('shows Turn-on-owned assessment drafts as automatic progress, not another approval step', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'auto-task',
          author: 'agent',
          text: 'Turn on is saved.',
          actions: [{
            type: 'draft_task_review',
            automatic_activation: true,
            activation_status: 'pending',
            reject_questions: [],
            drafts: [{
              task_id: 17,
              name: 'Platform reliability exercise',
              decisions: [],
              rubric: [],
              repo_file_count: 3,
            }],
          }],
        }],
      },
    });

    renderDock();

    expect(await screen.findByText(/being validated for Turn on/i)).toBeInTheDocument();
    expect(screen.getByText(/no second click is needed/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Approve$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Reject & revise/i })).not.toBeInTheDocument();
  });

  it('bulk mode: composer fans out to onSendBulk, not the single-role send', async () => {
    mocks.getTimeline.mockResolvedValue({ data: { timeline: [] } });
    const onSendBulk = vi.fn();
    renderDock({
      bulkSelectedRoles: [
        { role_id: 1, role_name: 'Data Eng' },
        { role_id: 2, role_name: 'GenAI Engineer' },
      ],
      onSendBulk,
    });

    expect(await screen.findByText('Messaging 2 agents')).toBeInTheDocument();
    expect(screen.getByText(/One message →/)).toBeInTheDocument();

    const ta = screen.getByPlaceholderText(/Message 2 agents at once/);
    fireEvent.change(ta, { target: { value: 'Salary is now AED 30k' } });
    fireEvent.keyDown(ta, { key: 'Enter' });

    expect(onSendBulk).toHaveBeenCalledWith('Salary is now AED 30k');
    expect(mocks.sendMessage).not.toHaveBeenCalled();
  });
});

describe('AgentSidebar', () => {
  const AGENTS = [
    // Badge = unread (2) + open questions (1) = 3, and must EXCLUDE the 50 pending
    // decisions (those are the feed's queue, not a chat notification).
    { role_id: 1, role_name: 'Data Eng', agent_enabled: true, unread_messages: 2, open_questions: 1, pending_decisions: 50, last_message_preview: 'cut-off is 64' },
    { role_id: 2, role_name: 'GenAI Engineer', agent_enabled: true, unread_messages: 0, open_questions: 0, pending_decisions: 9, last_message_preview: 'queue clear' },
  ];

  it('shows two separate indicators: questions and pending decisions', () => {
    const onSelect = vi.fn();
    render(<AgentSidebar agents={AGENTS} activeRoleId={1} onSelect={onSelect} />);

    expect(screen.getByText('Data Eng')).toBeInTheDocument();
    expect(screen.getByText('GenAI Engineer')).toBeInTheDocument();
    // Questions indicator: 2 unread + 1 question = 3.
    expect(screen.getByText('3')).toBeInTheDocument();
    // Decisions indicator: "50 pending", shown separately (NOT summed into the
    // questions badge). Reads "{n} pending" per the home-preview `.abadge`.
    expect(screen.getByText('50 pending')).toBeInTheDocument();
    expect(screen.queryByText('53')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('GenAI Engineer'));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it('announces role scope and multi-select state as pressed choices', () => {
    const { rerender } = render(
      <AgentSidebar
        agents={AGENTS}
        activeRoleId={1}
        onSelect={vi.fn()}
        bulkMode={false}
        bulkSelected={new Set()}
        onToggleBulkMode={vi.fn()}
        onToggleSelected={vi.fn()}
      />
    );

    expect(screen.getByRole('button', { name: /All roles/i })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: /Data Eng/i })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /GenAI Engineer/i })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: /Select/i })).toHaveAttribute('aria-pressed', 'false');

    rerender(
      <AgentSidebar
        agents={AGENTS}
        activeRoleId={1}
        onSelect={vi.fn()}
        bulkMode
        bulkSelected={new Set([2])}
        onToggleBulkMode={vi.fn()}
        onToggleSelected={vi.fn()}
      />
    );

    expect(screen.getByRole('button', { name: /Cancel/i })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /Data Eng/i })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: /GenAI Engineer/i })).toHaveAttribute('aria-pressed', 'true');
  });

  it('orders running agents above paused ones in the on/paused section', () => {
    // Backend hands them back interleaved (here: pending-count desc). Running
    // agents must float to the top; within running and within paused the
    // original relative order is preserved (stable sort).
    const agents = [
      { role_id: 1, role_name: 'Paused A', group: 'on_paused', agent_enabled: true, agent_paused: true, pending_decisions: 147 },
      { role_id: 2, role_name: 'Running A', group: 'on_paused', agent_enabled: true, agent_paused: false, pending_decisions: 130 },
      { role_id: 3, role_name: 'Paused B', group: 'on_paused', agent_enabled: true, agent_paused: true, pending_decisions: 107 },
      { role_id: 4, role_name: 'Running B', group: 'on_paused', agent_enabled: true, agent_paused: false, pending_decisions: 39 },
    ];
    render(<AgentSidebar agents={agents} activeRoleId={null} onSelect={vi.fn()} />);
    const names = Array.from(document.querySelectorAll('.ac-agent-role')).map((el) => el.textContent);
    expect(names).toEqual(['Running A', 'Running B', 'Paused A', 'Paused B']);
  });

  it('shows an empty state when there are no agents', () => {
    render(<AgentSidebar agents={[]} activeRoleId={null} onSelect={vi.fn()} />);
    expect(screen.getByText(/No live roles yet/)).toBeInTheDocument();
  });

  it('multi-select: toggle enters select mode and row clicks pick roles', () => {
    const onToggleBulkMode = vi.fn();
    const onToggleSelected = vi.fn();
    const onSelect = vi.fn();
    const { rerender } = render(
      <AgentSidebar
        agents={AGENTS}
        activeRoleId={1}
        onSelect={onSelect}
        bulkMode={false}
        bulkSelected={new Set()}
        onToggleBulkMode={onToggleBulkMode}
        onToggleSelected={onToggleSelected}
      />
    );
    // The "Select" toggle is present and enters bulk mode.
    fireEvent.click(screen.getByText(/Select/));
    expect(onToggleBulkMode).toHaveBeenCalled();

    // In bulk mode a row click picks the role instead of opening its chat.
    rerender(
      <AgentSidebar
        agents={AGENTS}
        activeRoleId={1}
        onSelect={onSelect}
        bulkMode
        bulkSelected={new Set()}
        onToggleBulkMode={onToggleBulkMode}
        onToggleSelected={onToggleSelected}
      />
    );
    fireEvent.click(screen.getByText('Data Eng'));
    expect(onToggleSelected).toHaveBeenCalledWith(1);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
