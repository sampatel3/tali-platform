import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  AgentFeedTimeline,
  AgentStreamTabs,
  agentFeedAttentionCount,
  agentTimelineLane,
  splitAgentTimeline,
} from './AgentFeedTimeline';

const eventItem = {
  kind: 'message',
  id: 'event-1',
  author: 'agent',
  message_kind: 'event',
  created_at: '2026-07-15T19:30:00Z',
  actions: [{
    type: 'agent_event',
    severity: 'error',
    event_type: 'run_failed',
    title: 'Agent run failed',
    summary: 'No candidate records changed.',
  }],
};

const needItem = {
  kind: 'needs_input',
  id: 'needs-2',
  needs_input_id: 2,
  status: 'open',
  question_kind: 'task_assignment_missing',
  prompt: 'Choose a task before the agent sends assessments.',
  can_answer: false,
  can_dismiss: false,
};

const decisionItem = {
  kind: 'decision',
  id: 'decision-5',
  decision_id: 5,
  role_id: 1,
  candidate_name: 'Tom Hale',
  recommendation: 'reject',
  score: 38,
  status: 'pending',
  reasoning: 'Below the role cut-off.',
};

describe('agent timeline lanes', () => {
  it.each([
    [{ kind: 'message', author: 'recruiter', message_kind: 'chat' }, 'conversation'],
    [{ kind: 'message', author: 'agent', message_kind: 'chat' }, 'conversation'],
    [{ kind: 'message', author: 'agent', message_kind: 'action' }, 'conversation'],
    [{ kind: 'message', author: 'agent', message_kind: 'event' }, 'feed'],
    [{ kind: 'message', author: 'agent', message_kind: 'proactive' }, 'feed'],
    [{ kind: 'needs_input' }, 'feed'],
    [{ kind: 'decision' }, 'feed'],
  ])('classifies %j as %s', (item, expected) => {
    expect(agentTimelineLane(item)).toBe(expected);
  });

  it('partitions one durable timeline without changing item identity', () => {
    const chat = { kind: 'message', id: 'chat-1', author: 'agent', message_kind: 'chat' };
    const lanes = splitAgentTimeline([chat, eventItem, needItem, decisionItem]);
    expect(lanes.conversation).toEqual([chat]);
    expect(lanes.feed).toEqual([eventItem, needItem, decisionItem]);
    // Durable operational events have no resolve/ack state, so only the open
    // recruiter request is honestly countable as attention.
    expect(agentFeedAttentionCount(lanes.feed)).toBe(1);
  });
});

describe('AgentFeedTimeline', () => {
  it('keeps an error to one line until the recruiter expands it', () => {
    render(
      <AgentFeedTimeline
        items={[eventItem]}
        renderAction={() => (
          <div>
            <span>No candidate records changed.</span>
            <span>Recovery actions and source</span>
          </div>
        )}
      />,
    );

    const trigger = screen.getByText('Agent run failed').closest('button');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('No candidate records changed.')).not.toBeInTheDocument();
    expect(screen.queryByText('Recovery actions and source')).not.toBeInTheDocument();

    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('No candidate records changed.')).toBeInTheDocument();
    expect(screen.getByText('Recovery actions and source')).toBeInTheDocument();
  });

  it('uses a compact candidate reference instead of the workspace decision card', () => {
    render(<AgentFeedTimeline items={[decisionItem]} roleId={1} />);

    expect(screen.getByText('1 candidate decision ready')).toBeInTheDocument();
    expect(screen.queryByText('Tom Hale · Reject recommended')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }));
    const trigger = screen.getByText('Tom Hale · Reject recommended').closest('button');
    expect(screen.queryByText('Below the role cut-off.')).not.toBeInTheDocument();
    expect(document.querySelector('.rq-hybrid-detail')).not.toBeInTheDocument();

    fireEvent.click(trigger);

    expect(screen.getByText('Below the role cut-off.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Review in queue' })).toHaveAttribute(
      'href',
      '/home?role=1&pending=5',
    );
    expect(document.querySelector('.rq-hybrid-detail')).not.toBeInTheDocument();
  });

  it('filters needs, issues, and decisions without expanding history', () => {
    render(<AgentFeedTimeline items={[eventItem, needItem, decisionItem]} roleId={1} />);

    fireEvent.click(screen.getByRole('button', { name: 'Issues' }));
    expect(screen.getByText('Agent run failed')).toBeInTheDocument();
    expect(screen.queryByText('Choose an assessment task')).not.toBeInTheDocument();
    expect(screen.queryByText('Tom Hale · Reject recommended')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }));
    expect(screen.getByText('Tom Hale · Reject recommended')).toBeInTheDocument();
    expect(screen.queryByText('Agent run failed')).not.toBeInTheDocument();
  });

  it('aggregates a large candidate queue in All and only lists people in Decisions', () => {
    const decisions = Array.from({ length: 27 }, (_, index) => ({
      ...decisionItem,
      id: `decision-${index + 1}`,
      decision_id: index + 1,
      candidate_name: `Candidate ${index + 1}`,
      created_at: `2026-07-15T${String(index % 24).padStart(2, '0')}:00:00Z`,
    }));
    render(<AgentFeedTimeline items={decisions} roleId={1} />);

    expect(screen.getByText('27 candidate decisions ready')).toBeInTheDocument();
    expect(screen.queryByText('Candidate 1 · Reject recommended')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }));
    expect(screen.getByText('Candidate 1 · Reject recommended')).toBeInTheDocument();
    expect(screen.getByText('Candidate 27 · Reject recommended')).toBeInTheDocument();
  });

  it('keeps resolved requests out of Needs you and sends resolved decisions to the candidate report', () => {
    const resolvedNeed = { ...needItem, id: 'needs-resolved', needs_input_id: 3, status: 'answered' };
    const resolvedDecision = {
      ...decisionItem,
      id: 'decision-resolved',
      decision_id: 8,
      application_id: 88,
      status: 'approved',
    };
    render(<AgentFeedTimeline items={[resolvedNeed, resolvedDecision]} roleId={1} />);

    fireEvent.click(screen.getByRole('button', { name: 'Needs you' }));
    expect(screen.queryByText('Choose an assessment task')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }));
    fireEvent.click(screen.getByText('Tom Hale · Reject recommended').closest('button'));
    expect(screen.getByRole('link', { name: 'Open candidate report' })).toHaveAttribute(
      'href',
      '/candidates/88?from=home&view_role_id=1',
    );
  });
});

describe('AgentStreamTabs', () => {
  it('uses native tab semantics and labels the badge as attention, not unread', () => {
    const onChange = vi.fn();
    render(
      <AgentStreamTabs
        value="chat"
        onChange={onChange}
        attentionCount={2}
        chatPanelId="chat-panel"
        feedPanelId="feed-panel"
      />,
    );

    expect(screen.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('2 agent feed items need attention')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Agent feed/ }));
    expect(onChange).toHaveBeenCalledWith('feed');
  });
});
