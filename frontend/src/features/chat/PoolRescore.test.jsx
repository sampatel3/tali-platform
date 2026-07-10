import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, afterEach, test, expect } from 'vitest';

import PoolRescore from './PoolRescore';
import { roles as rolesApi } from '../../shared/api/rolesClient';

vi.mock('../../shared/api/rolesClient', () => ({
  roles: { startPoolRescore: vi.fn(), getPoolRescore: vi.fn() },
}));

afterEach(() => {
  vi.restoreAllMocks();
  rolesApi.startPoolRescore.mockReset();
  rolesApi.getPoolRescore.mockReset();
});

const candidates = [
  { application_id: 1, candidate_name: 'Ada' },
  { application_id: 2, candidate_name: 'Linus' },
];

test('shows the re-score button with the cost estimate', () => {
  render(<PoolRescore requirementText="banking domain" candidates={candidates} />);
  // 2 candidates × $0.09 = $0.18
  expect(screen.getByText(/Re-score top 2 against this requirement · est \$0\.18/)).toBeInTheDocument();
});

test('confirm → start → poll → renders true scores ranked', async () => {
  rolesApi.startPoolRescore.mockResolvedValue({ data: { job_id: 7 } });
  rolesApi.getPoolRescore.mockResolvedValue({
    data: {
      status: 'done',
      results: [
        { application_id: 1, role_fit_score: 81.4 },
        { application_id: 2, role_fit_score: 90.2 },
      ],
    },
  });

  render(<PoolRescore requirementText="banking domain" candidates={candidates} />);
  // Open the in-app confirm dialog, then confirm.
  fireEvent.click(screen.getByText(/Re-score top 2/));
  fireEvent.click(screen.getByRole('button', { name: 'Re-score' }));

  await waitFor(() => expect(screen.getByText('True fit vs your requirement')).toBeInTheDocument());
  expect(rolesApi.startPoolRescore).toHaveBeenCalledWith('banking domain', [1, 2]);
  // ranked desc: Linus (90) above Ada (81)
  const names = screen.getAllByText(/Ada|Linus/).map((n) => n.textContent);
  expect(names[0]).toBe('Linus');
  expect(screen.getByText('90')).toBeInTheDocument();
  expect(screen.getByText('81')).toBeInTheDocument();
});

test('does nothing if the user cancels the confirm', () => {
  render(<PoolRescore requirementText="banking domain" candidates={candidates} />);
  fireEvent.click(screen.getByText(/Re-score top 2/));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(rolesApi.startPoolRescore).not.toHaveBeenCalled();
});
