import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const family = {
  owner: { id: 31, name: 'Data Platform Lead' },
  related: [{ id: 47, name: 'AI Engineer' }],
};

const cvGapRow = (overrides = {}) => ({
  id: 19,
  role_id: 31,
  role_name: 'Data Platform Lead',
  role_version: 3,
  kind: 'missing_cv',
  prompt: 'Two candidates have no readable CV.',
  role_family: family,
  cv_gap_rejection: {
    kind: 'missing_cv',
    owner_role_id: 31,
    application_ids: [81, 94],
    eligible_count: 2,
    has_more: false,
    expected_owner_role_version: 3,
    expected_role_family: family,
  },
  ...overrides,
});

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

  it('names the complete linked family before a CV-gap cohort rejection', async () => {
    mocks.get.mockResolvedValue({
      data: [cvGapRow()],
    });

    render(<AgentNeedsInputCard roleId={31} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reject — no CV' }));

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Rejects the shared ATS application across all linked roles: Data Platform Lead #31 (original) and AI Engineer #47 (related).',
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'confirming exactly 2 candidates',
    );
    expect(screen.getByRole('button', { name: 'Confirm reject' })).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it('submits the exact displayed cohort, owner version, and family then shows the receipt', async () => {
    mocks.get.mockResolvedValue({ data: [cvGapRow()] });
    mocks.post.mockResolvedValue({
      data: {
        job_run_id: 501,
        status: 'queued',
        accepted_count: 2,
        application_ids: [81, 94],
      },
    });

    render(<AgentNeedsInputCard roleId={31} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reject — no CV' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm reject' }));

    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledWith(
        '/agent-needs-input/19/reject-cv-gap',
        {
          application_ids: [81, 94],
          expected_owner_role_version: 3,
          expected_role_family: family,
        },
      );
    });
    expect(await screen.findByRole('status')).toHaveTextContent(
      'A rejection batch for exactly 2 candidates is queued as background job #501.',
    );
    expect(screen.queryByRole('button', { name: 'Confirm reject' })).not.toBeInTheDocument();
  });

  it('refreshes and requires a new confirmation after a structured conflict without retrying', async () => {
    const refreshed = cvGapRow({
      cv_gap_rejection: {
        ...cvGapRow().cv_gap_rejection,
        application_ids: [81, 94, 105],
        eligible_count: 3,
        expected_owner_role_version: 4,
      },
    });
    mocks.get
      .mockResolvedValueOnce({ data: [cvGapRow()] })
      .mockResolvedValueOnce({ data: [refreshed] });
    mocks.post.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: { code: 'CV_GAP_COHORT_CHANGED' } },
      },
    });

    render(<AgentNeedsInputCard roleId={31} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reject — no CV' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm reject' }));

    expect(await screen.findByText(/Review the refreshed count and family/)).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Confirm reject' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Reject — no CV' }));
    expect(screen.getByRole('alert')).toHaveTextContent('confirming exactly 3 candidates');
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
});
