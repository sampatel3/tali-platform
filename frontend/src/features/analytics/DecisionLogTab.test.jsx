import {
  act, fireEvent, render, screen, waitFor,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const listDecisions = vi.fn();
const authState = { user: { id: 7 } };

vi.mock('../../shared/api', () => ({
  agent: { listDecisions: (...args) => listDecisions(...args) },
}));
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => authState,
}));

import { DecisionLogTab } from './DecisionLogTab';

const row = (id, overrides = {}) => ({
  id,
  application_id: id,
  role_id: 7,
  role_name: 'Platform Engineer',
  candidate_name: `Candidate ${id}`,
  decision_type: 'advance_to_interview',
  status: 'approved',
  created_at: `2026-07-16T12:${String(id % 60).padStart(2, '0')}:00Z`,
  ...overrides,
});

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('DecisionLogTab pagination', () => {
  it('does not attribute a teammate resolver to the current viewer', async () => {
    listDecisions.mockResolvedValueOnce({
      data: [row(11, { resolved_by_user_id: 42 })],
    });

    render(<DecisionLogTab />);

    expect(await screen.findByText('Recruiter')).toBeInTheDocument();
    expect(screen.getByText('approved by recruiter')).toBeInTheDocument();
    expect(screen.queryByText('You')).not.toBeInTheDocument();
  });

  it('identifies only the current viewer as the resolver', async () => {
    listDecisions.mockResolvedValueOnce({
      data: [row(12, { resolved_by_user_id: 7 })],
    });

    render(<DecisionLogTab />);

    expect(await screen.findByText('You')).toBeInTheDocument();
    expect(screen.getByText('approved by you')).toBeInTheDocument();
  });

  it('loads earlier rows with a stable cursor instead of truncating the log', async () => {
    const firstPage = Array.from({ length: 101 }, (_, index) => row(300 - index));
    listDecisions
      .mockResolvedValueOnce({ data: firstPage })
      .mockResolvedValueOnce({ data: [row(200), row(199)] });

    render(<DecisionLogTab roleId="7" />);

    expect(await screen.findByText('Candidate 300')).toBeInTheDocument();
    expect(screen.queryByText('Candidate 200')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load earlier decisions' }));

    expect(await screen.findByText('Candidate 200')).toBeInTheDocument();
    const cursorRow = firstPage[99];
    expect(listDecisions).toHaveBeenNthCalledWith(2, {
      status: 'all',
      role_id: '7',
      limit: 101,
      before_created_at: cursorRow.created_at,
      before_id: cursorRow.id,
    });
    expect(screen.queryByRole('button', { name: 'Load earlier decisions' })).not.toBeInTheDocument();
  });

  it('starts only one load-earlier request when clicks arrive in the same render', async () => {
    const firstPage = Array.from({ length: 101 }, (_, index) => row(500 - index));
    const earlierPage = deferred();
    listDecisions
      .mockResolvedValueOnce({ data: firstPage })
      .mockReturnValue(earlierPage.promise);

    render(<DecisionLogTab roleId="7" />);
    const loadEarlier = await screen.findByRole('button', { name: 'Load earlier decisions' });
    act(() => {
      loadEarlier.click();
      loadEarlier.click();
    });

    expect(listDecisions).toHaveBeenCalledTimes(2);
    await act(async () => {
      earlierPage.resolve({ data: [] });
      await earlierPage.promise;
    });
  });

  it('does not append or apply pagination state from an old role scope', async () => {
    const firstRolePage = Array.from({ length: 101 }, (_, index) => row(700 - index));
    const staleEarlierPage = deferred();
    const secondRolePage = Array.from({ length: 101 }, (_, index) => row(900 - index, {
      candidate_name: `Role 8 Candidate ${900 - index}`,
      role_id: 8,
      role_name: 'Product Engineer',
    }));
    listDecisions
      .mockResolvedValueOnce({ data: firstRolePage })
      .mockReturnValueOnce(staleEarlierPage.promise)
      .mockResolvedValueOnce({ data: secondRolePage });

    const { rerender } = render(<DecisionLogTab roleId="7" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Load earlier decisions' }));
    rerender(<DecisionLogTab roleId="8" />);

    expect(await screen.findByText('Role 8 Candidate 900')).toBeInTheDocument();
    await act(async () => {
      staleEarlierPage.resolve({ data: [row(111, { candidate_name: 'Stale role 7 decision' })] });
      await staleEarlierPage.promise;
    });

    expect(screen.queryByText('Stale role 7 decision')).not.toBeInTheDocument();
    expect(screen.getByText('Role 8 Candidate 900')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Load earlier decisions' })).toBeInTheDocument();
  });

  it('applies action filters on the server before paginating', async () => {
    listDecisions
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({ data: [row(8, { decision_type: 'skip_assessment_reject' })] });

    render(<DecisionLogTab />);
    await waitFor(() => expect(listDecisions).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByLabelText('Filter decisions'));
    fireEvent.click(screen.getByRole('option', { name: 'Rejects' }));

    expect(await screen.findByText('Candidate 8')).toBeInTheDocument();
    expect(listDecisions).toHaveBeenLastCalledWith({
      status: 'all',
      type: 'all_rejects',
      limit: 101,
    });
  });

  it('shows a retryable error instead of disguising a failed request as an empty log', async () => {
    listDecisions
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ data: [row(9)] });

    render(<DecisionLogTab />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Decisions could not be loaded');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('Candidate 9')).toBeInTheDocument();
  });
});
