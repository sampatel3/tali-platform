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
    actions: [{ type: 'constraint_change', action: 'updated', criterion: { text: 'Salary ≤ £25,000' }, rescreening_count: 47 }],
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
    expect(screen.getByText('Salary ≤ £25,000')).toBeInTheDocument();
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
    expect(await screen.findByText(/Tell the agent what to change/)).toBeInTheDocument();

    // Use a hint chip to send a canned message.
    fireEvent.click(screen.getByText('what if I drop the cut-off to 60?'));

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
});

describe('AgentSidebar', () => {
  const AGENTS = [
    { role_id: 1, role_name: 'Data Eng', agent_enabled: true, attention: 3, last_message_preview: 'cut-off is 64' },
    { role_id: 2, role_name: 'GenAI Engineer', agent_enabled: true, attention: 0, last_message_preview: 'queue clear' },
  ];

  it('lists agents with badges and fires onSelect', () => {
    const onSelect = vi.fn();
    render(<AgentSidebar agents={AGENTS} activeRoleId={1} onSelect={onSelect} />);

    expect(screen.getByText('Data Eng')).toBeInTheDocument();
    expect(screen.getByText('GenAI Engineer')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument(); // attention badge

    fireEvent.click(screen.getByText('GenAI Engineer'));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it('shows an empty state when there are no agents', () => {
    render(<AgentSidebar agents={[]} activeRoleId={null} onSelect={vi.fn()} />);
    expect(screen.getByText(/No active agents yet/)).toBeInTheDocument();
  });
});
