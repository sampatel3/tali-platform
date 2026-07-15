import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { RoleAgentTimeline } from './RoleAgentTimeline';

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
