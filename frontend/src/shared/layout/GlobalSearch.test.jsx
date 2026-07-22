import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GlobalSearch } from './GlobalSearch';

const listApplicationsGlobal = vi.fn();

vi.mock('../api', () => ({
  roles: {
    listApplicationsGlobal: (...args) => listApplicationsGlobal(...args),
    list: vi.fn().mockResolvedValue({ data: [] }),
  },
  tasks: {
    list: vi.fn().mockResolvedValue({ data: [] }),
  },
}));

describe('GlobalSearch logical application memberships', () => {
  let consoleError;

  beforeEach(() => {
    listApplicationsGlobal.mockReset().mockResolvedValue({
      data: {
        items: [
          {
            id: 42,
            logical_membership_id: '11:42',
            logical_role_id: 11,
            role_id: 11,
            role_name: 'ATS owner role',
            candidate_name: 'Shared Candidate',
          },
          {
            id: 42,
            logical_membership_id: '12:42',
            logical_role_id: 12,
            role_id: 12,
            role_name: 'Related search role',
            candidate_name: 'Shared Candidate',
          },
        ],
      },
    });
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
  });

  it('renders shared physical applications independently and opens the selected logical role', async () => {
    const onNavigate = vi.fn();
    render(<GlobalSearch onNavigate={onNavigate} />);

    fireEvent.focus(screen.getByRole('searchbox'));
    const relatedRole = await screen.findByText('Related search role');

    expect(screen.getAllByText('Shared Candidate')).toHaveLength(2);
    expect(consoleError.mock.calls.flat().join(' ')).not.toMatch(/same key/i);

    fireEvent.click(relatedRole.closest('button'));

    await waitFor(() => {
      expect(onNavigate).toHaveBeenCalledWith('candidate-report', {
        candidateApplicationId: 42,
        viewRoleId: 12,
      });
    });
  });
});
