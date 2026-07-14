import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

// Mock the API module so the dock's getTimeline/sendMessage hit our stubs.
const mocks = vi.hoisted(() => ({
  getTimeline: vi.fn(),
  sendMessage: vi.fn(),
  answerNeedsInput: vi.fn().mockResolvedValue({ data: {} }),
  dismissNeedsInput: vi.fn().mockResolvedValue({ data: {} }),
}));
vi.mock('../../../shared/api', () => ({
  agentChat: {
    getTimeline: mocks.getTimeline,
    sendMessage: mocks.sendMessage,
    answerNeedsInput: mocks.answerNeedsInput,
    dismissNeedsInput: mocks.dismissNeedsInput,
    markRead: vi.fn().mockResolvedValue({ data: {} }),
    listConversations: vi.fn().mockResolvedValue({ data: { agents: [] } }),
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
  // A decision — Option C keeps these in the main feed, so the dock must NOT show it.
  {
    kind: 'decision', id: 'd1', decision_id: 5, decision_type: 'reject', candidate_name: 'Tom Hale',
    score: 38, status: 'pending', reasoning: 'below cut-off', created_at: '2026-06-03T08:00:00Z',
  },
];

const renderDock = (props = {}) =>
  render(
    <ToastProvider>
      <AgentChatDock roleId={1} roleName="Data Eng" onReload={vi.fn()} {...props} />
    </ToastProvider>
  );

beforeEach(() => {
  mocks.getTimeline.mockReset();
  mocks.sendMessage.mockReset();
});

describe('AgentChatDock', () => {
  it('renders chat + impact card + question, and hides decisions', async () => {
    mocks.getTimeline.mockResolvedValue({ data: { timeline: TIMELINE } });
    renderDock();

    expect(await screen.findByText('Updated and re-screening.')).toBeInTheDocument();
    // Impact card from the agent message.
    expect(screen.getByText('Salary ≤ AED 25,000')).toBeInTheDocument();
    expect(screen.getByText(/re-screening 47/)).toBeInTheDocument();
    // The agent's question + its options.
    expect(screen.getByText('Marcus or Lena?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Marcus' })).toBeInTheDocument();
    // Decisions live in the feed, not the dock.
    expect(screen.queryByText('Tom Hale')).not.toBeInTheDocument();
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
