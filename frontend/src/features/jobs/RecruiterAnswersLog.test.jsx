import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../shared/api/httpClient', () => ({
  default: { get: mocks.get },
}));

import RecruiterAnswersLog from './RecruiterAnswersLog';

beforeEach(() => {
  mocks.get.mockReset();
});

describe('RecruiterAnswersLog', () => {
  it('loads resolved questions for the role and renders structured answers', async () => {
    mocks.get.mockResolvedValue({
      data: [{
        id: 8,
        kind: 'threshold_ambiguous',
        prompt: 'Which score should the agent use?',
        response: { value: '65' },
        resolved_at: '2026-07-16T09:00:00Z',
      }],
    });

    render(<RecruiterAnswersLog roleId={4} />);

    expect(await screen.findByText('Score threshold')).toBeInTheDocument();
    expect(screen.getByText('Which score should the agent use?')).toBeInTheDocument();
    expect(screen.getByText('65')).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith('/agent-needs-input', {
      params: { role_id: 4, status: 'resolved', limit: 25 },
    });
  });

  it('stays out of the layout when the role has no resolved questions', async () => {
    mocks.get.mockResolvedValue({ data: [] });

    render(<RecruiterAnswersLog roleId={4} />);

    await waitFor(() => {
      expect(screen.queryByTestId('recruiter-answers-log')).not.toBeInTheDocument();
    });
  });

  it('keeps a truthful retry message visible when loading fails', async () => {
    mocks.get.mockRejectedValue(new Error('offline'));

    render(<RecruiterAnswersLog roleId={4} />);

    expect(await screen.findByText("Couldn't load the Q&A history — try again.")).toBeInTheDocument();
  });
});
