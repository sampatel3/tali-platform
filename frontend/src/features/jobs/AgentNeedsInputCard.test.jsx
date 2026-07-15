import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../../shared/api/httpClient', () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
  },
}));

import AgentNeedsInputCard from './AgentNeedsInputCard';

beforeEach(() => {
  mocks.get.mockReset();
  mocks.post.mockReset();
});

describe('AgentNeedsInputCard', () => {
  it('renders a missing task as a linked workflow without a bogus text answer', async () => {
    mocks.get.mockResolvedValue({
      data: [{
        id: 12,
        role_id: 4,
        role_name: 'Data Modeler',
        kind: 'task_assignment_missing',
        prompt: 'Pick an assessment task on the role page, then I will resume.',
        link_url: '/jobs/4?tab=agent-settings',
        link_label: 'Pick a task',
      }],
    });

    render(<AgentNeedsInputCard roleId={4} />);

    expect(await screen.findByRole('link', { name: 'Pick a task' })).toHaveAttribute(
      'href',
      '/jobs/4?tab=agent-settings',
    );
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
