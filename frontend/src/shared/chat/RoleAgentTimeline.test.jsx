import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import { RoleAgentTimeline } from './RoleAgentTimeline';

vi.mock('../decisions/AgentDecisionTimelineCard', () => ({
  AgentDecisionTimelineCard: ({ item }) => (
    <div data-testid="decision-artifact">Decision {item.decision_id}</div>
  ),
}));

const message = (id, author, text) => ({
  id,
  kind: 'message',
  author,
  text,
  created_at: '2026-07-15T12:00:00Z',
});

const renderTimeline = (items, roleId = 1) => (
  <div data-testid="scroll-viewport">
    <RoleAgentTimeline items={items} roleId={roleId} roleName="AI Engineer" />
  </div>
);

test('announces only a newly appended assistant message while following the bottom', async () => {
  const history = [
    message('user-1', 'recruiter', 'Review this pool.'),
    message('agent-1', 'agent', 'I reviewed 42 candidates.'),
  ];
  const view = render(renderTimeline(history));
  const liveRegion = screen.getByRole('status');
  const viewport = screen.getByTestId('scroll-viewport');

  // Existing history establishes the baseline silently.
  expect(liveRegion).toBeEmptyDOMElement();
  Object.defineProperties(viewport, {
    scrollHeight: { configurable: true, value: 1000 },
    scrollTop: { configurable: true, value: 600, writable: true },
    clientHeight: { configurable: true, value: 400 },
  });
  fireEvent.scroll(viewport);

  view.rerender(renderTimeline([
    ...history,
    message('agent-2', 'agent', 'Three candidates clear every must-have.'),
  ]));

  await waitFor(() => {
    expect(screen.getByRole('status')).toHaveTextContent(
      'Agent: Three candidates clear every must-have.',
    );
  });
});

test('does not announce history for a new scope or messages arriving while browsing upward', () => {
  const history = [message('agent-1', 'agent', 'Persisted history.')];
  const view = render(renderTimeline(history));
  const viewport = screen.getByTestId('scroll-viewport');
  Object.defineProperties(viewport, {
    scrollHeight: { configurable: true, value: 1000 },
    scrollTop: { configurable: true, value: 100, writable: true },
    clientHeight: { configurable: true, value: 400 },
  });
  fireEvent.scroll(viewport);

  view.rerender(renderTimeline([
    ...history,
    message('agent-2', 'agent', 'An update arrived while you were reading.'),
  ]));
  expect(screen.getByRole('status')).toBeEmptyDOMElement();

  view.rerender(renderTimeline([
    message('agent-other-role', 'agent', 'Existing message for another role.'),
  ], 2));
  expect(screen.getByRole('status')).toBeEmptyDOMElement();
});

test('announces a newly appended needs-input request while following the bottom', async () => {
  const history = [message('agent-1', 'agent', 'I checked the shortlist.')];
  const view = render(renderTimeline(history));
  const viewport = screen.getByTestId('scroll-viewport');
  Object.defineProperties(viewport, {
    scrollHeight: { configurable: true, value: 700 },
    scrollTop: { configurable: true, value: 300, writable: true },
    clientHeight: { configurable: true, value: 400 },
  });
  fireEvent.scroll(viewport);

  view.rerender(renderTimeline([
    ...history,
    {
      id: 'needs-input-live',
      needs_input_id: 44,
      kind: 'needs_input',
      status: 'open',
      title: 'Choose an assessment task',
      prompt: 'Pick the task before I send invitations.',
      can_answer: false,
      can_dismiss: false,
    },
  ]));

  await waitFor(() => {
    expect(screen.getByRole('status')).toHaveTextContent(
      'Agent needs your input: Choose an assessment task',
    );
  });
});

test('attributes a needs-input request on the same assistant grid without repeating its prompt', () => {
  const prompt = 'Choose the assessment task before I invite candidates.';
  const { container } = render(renderTimeline([{
    id: 'needs-input-1',
    needs_input_id: 41,
    kind: 'needs_input',
    status: 'open',
    prompt,
    created_at: '2026-07-15T12:00:00Z',
    can_answer: false,
    can_dismiss: false,
  }]));

  const turn = container.querySelector('.tk-msg-assistant');
  expect(turn).not.toBeNull();
  expect(turn.querySelector('.tk-msg-author')).toHaveTextContent('Agent');
  expect(turn.querySelector('.tk-msg-author time')).not.toBeNull();
  expect(turn.querySelector('[data-needs-input-id="41"]')).not.toBeNull();
  expect(screen.getAllByText(prompt)).toHaveLength(1);
});

test('attributes a decision artifact on the same assistant grid', () => {
  const { container } = render(renderTimeline([{
    id: 'decision-1',
    decision_id: 77,
    kind: 'decision',
    created_at: '2026-07-15T12:00:00Z',
  }]));

  const artifact = screen.getByTestId('decision-artifact');
  const turn = artifact.closest('.tk-msg-assistant');
  expect(turn).not.toBeNull();
  expect(turn.querySelector('.tk-msg-author')).toHaveTextContent('Agent');
  expect(turn.querySelector('.tk-msg-author time')).not.toBeNull();
  expect(container.querySelectorAll('.tk-msg-author')).toHaveLength(1);
});
