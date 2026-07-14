import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { MotionSystemProvider } from '../../shared/motion';
import { OutcomesTab } from './OutcomesTab';

const renderTab = (props) =>
  render(
    <MotionSystemProvider>
      <OutcomesTab summary={null} breakdown={null} trend={null} rolesBreakdown={[]} {...props} />
    </MotionSystemProvider>,
  );

const costData = {
  window: { label: 'Last 30 days' },
  billed_spend_cents: 300000,
  counts: { pre_screened: 100, scored: 40, advanced: 6, hired: 0 },
  per_outcome: {
    pre_screen: { cost_cents: 2, count: 100 },
    score: { cost_cents: 30, count: 40 },
    advanced: { cost_cents: 500, count: 6 },
    hired: { cost_cents: null, count: 0 },
  },
};

describe('OutcomesTab cost-per-outcome', () => {
  it('renders the four unit-cost tiles with counts', () => {
    renderTab({ cost: costData });
    expect(screen.getByText('Cost per outcome')).toBeInTheDocument();
    expect(screen.getByText('Per pre-screen')).toBeInTheDocument();
    expect(screen.getByText('Per score')).toBeInTheDocument();
    expect(screen.getByText('Per advanced')).toBeInTheDocument();
    expect(screen.getByText('Per hire')).toBeInTheDocument();
    // Counts surfaced under each tile.
    expect(screen.getByText('100 pre-screened')).toBeInTheDocument();
    expect(screen.getByText('6 advanced')).toBeInTheDocument();
  });

  it('shows a dash for an outcome with zero count (no divide-by-zero)', () => {
    renderTab({ cost: costData });
    // Hired has count 0 → per-unit renders "—" inside that tile (not a crash).
    const hiredTile = screen.getByText('0 hired').closest('.an-cpo-cell');
    expect(within(hiredTile).getByText('—')).toBeInTheDocument();
  });

  it('renders an empty state when there is no billed spend yet', () => {
    renderTab({
      cost: {
        billed_spend_cents: 0,
        counts: { pre_screened: 0, scored: 0, advanced: 0, hired: 0 },
        per_outcome: {
          pre_screen: { cost_cents: null, count: 0 },
          score: { cost_cents: null, count: 0 },
          advanced: { cost_cents: null, count: 0 },
          hired: { cost_cents: null, count: 0 },
        },
      },
    });
    expect(screen.getByText(/No billed agent spend in this window yet/i)).toBeInTheDocument();
  });

  it('renders an empty state when the cost feed is absent', () => {
    renderTab({ cost: null });
    expect(screen.getByText(/No billed agent spend in this window yet/i)).toBeInTheDocument();
  });
});
