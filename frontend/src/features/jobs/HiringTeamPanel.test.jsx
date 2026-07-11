import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { hiringTeam as hiringTeamApi, team as teamApi } from '../../shared/api';
import { HiringTeamPanel } from './HiringTeamPanel';

vi.mock('../../shared/api', () => ({
  hiringTeam: { list: vi.fn(), set: vi.fn(), remove: vi.fn() },
  team: { list: vi.fn() },
}));

describe('HiringTeamPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    teamApi.list.mockResolvedValue({ data: [{ id: 9, full_name: 'Dana Lee', email: 'dana@x.test' }] });
  });

  it('lists members and adds one', async () => {
    hiringTeamApi.list
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ user_id: 9, team_role: 'interviewer', name: 'Dana Lee', email: 'dana@x.test' }]);
    hiringTeamApi.set.mockResolvedValue({ user_id: 9, team_role: 'interviewer' });

    render(<HiringTeamPanel roleId={4} />);
    expect(await screen.findByText('Add to hiring team')).toBeInTheDocument();

    // Open the member picker (a custom select trigger) and choose Dana.
    fireEvent.click(screen.getByText('Choose someone'));
    fireEvent.click(await screen.findByRole('option', { name: 'Dana Lee' }));

    fireEvent.click(screen.getByText('Add'));
    await waitFor(() => expect(hiringTeamApi.set).toHaveBeenCalledWith(4, 9, 'interviewer'));
    expect(await screen.findByText('Dana Lee')).toBeInTheDocument();
  });

  it('removes a member', async () => {
    hiringTeamApi.list
      .mockResolvedValueOnce([{ user_id: 9, team_role: 'recruiter', name: 'Dana Lee', email: 'dana@x.test' }])
      .mockResolvedValueOnce([]);
    hiringTeamApi.remove.mockResolvedValue({});

    render(<HiringTeamPanel roleId={4} />);
    expect(await screen.findByText('Dana Lee')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Remove'));
    await waitFor(() => expect(hiringTeamApi.remove).toHaveBeenCalledWith(4, 9));
  });
});
