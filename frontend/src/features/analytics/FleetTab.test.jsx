import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../shared/api', () => ({
  agent: {
    panel: vi.fn(),
    orgActivity: vi.fn(),
  },
}));

import { agent as agentApi } from '../../shared/api';
import { FleetTab } from './FleetTab';

describe('FleetTab agent navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    agentApi.panel.mockResolvedValue({
      data: {
        kpis: {},
        pulse: {},
        agents: [{
          role_id: 77,
          name: 'Platform Engineer',
          running: true,
          budget_spent_cents: 100,
          budget_cap_cents: 5000,
          cycles_24h: 2,
          pending: 0,
          last_run_at: null,
          activity: { label: 'IDLE', text: 'idle' },
        }],
        recent_decisions: [],
      },
    });
    agentApi.orgActivity.mockResolvedValue({ data: { entries: [] } });
  });

  it('opens the role-scoped Agent settings from each fleet card', async () => {
    render(<MemoryRouter><FleetTab /></MemoryRouter>);

    expect(await screen.findByRole('link', { name: 'Open agent settings for Platform Engineer' }))
      .toHaveAttribute('href', '/jobs/77?view=role-fit');
  });
});
