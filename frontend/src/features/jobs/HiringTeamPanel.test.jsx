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
    teamApi.list.mockResolvedValue({ data: [{ id: 7, full_name: 'Dana R', email: 'dana@x.test' }] });
  });

  it('lists members and adds one', async () => {
    hiringTeamApi.list
      .mockResolvedValueOnce([]) // initial
      .mockResolvedValueOnce([{ user_id: 7, team_role: 'hiring_manager', name: 'Dana R', email: 'dana@x.test' }]);
    hiringTeamApi.set.mockResolvedValue({});

    render(<HiringTeamPanel roleId={1} />);

    expect(await screen.findByText(/No hiring team yet/)).toBeInTheDocument();

    // Two selects: [0] = user picker, [1] = team role (defaults to interviewer).
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: '7' } });
    fireEvent.click(screen.getByText('Add'));

    await waitFor(() => expect(hiringTeamApi.set).toHaveBeenCalledWith(1, { user_id: 7, team_role: 'interviewer' }));
    // The email only appears on the member card (the picker option shows the name),
    // so it's an unambiguous proof the member rendered after the reload.
    expect(await screen.findByText('dana@x.test')).toBeInTheDocument();
  });

  it('shows an error when loading fails', async () => {
    hiringTeamApi.list.mockRejectedValue(new Error('boom'));
    render(<HiringTeamPanel roleId={1} />);
    expect(await screen.findByText(/Failed to load the hiring team/)).toBeInTheDocument();
  });
});
