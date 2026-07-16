import { render, waitFor } from '@testing-library/react';

import { AgentPromptCard } from './AgentPromptCard';

const request = {
  id: 'needs-threshold',
  needs_input_id: 'needs-threshold',
  kind: 'needs_input',
  question_kind: 'threshold_ambiguous',
  title: 'Set the screening threshold',
  prompt: 'What screening threshold should I use?',
  status: 'open',
  response_schema: { properties: { value: { type: 'integer' } } },
};

test('does not announce a resolved request loaded from persisted history', () => {
  const { container } = render(
    <AgentPromptCard
      item={{ ...request, status: 'answered', response: { value: 70 } }}
      onAnswer={() => true}
      onDismiss={() => true}
    />,
  );

  const receipt = container.querySelector('.tk-agent-prompt-receipt');
  expect(receipt).toBeInTheDocument();
  expect(receipt).not.toHaveAttribute('role');
  expect(receipt).not.toHaveAttribute('aria-live');
});

test('announces a request that resolves while its open card is mounted', async () => {
  const view = render(
    <AgentPromptCard item={request} onAnswer={() => true} onDismiss={() => true} />,
  );

  view.rerender(
    <AgentPromptCard
      item={{ ...request, status: 'answered', response: { value: 70 } }}
      onAnswer={() => true}
      onDismiss={() => true}
    />,
  );

  await waitFor(() => {
    const receipt = view.container.querySelector('.tk-agent-prompt-receipt');
    expect(receipt).toHaveAttribute('role', 'status');
    expect(receipt).toHaveAttribute('aria-live', 'polite');
  });
});
