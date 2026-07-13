import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RoleAgentSettingsTab } from './RoleAgentSettingsTab';

// Minimal props: the tab renders the autonomy toggles from the role record.
const baseProps = (roleOverrides = {}) => ({
  role: { id: 1, monthly_usd_budget_cents: 5000, ...roleOverrides },
  roleCriteria: [],
  workspaceCriteria: [],
  thresholdDraft: '',
  thresholdValue: 55,
  recruiterCriteria: [],
  activeApplications: [],
  belowThresholdCount: 0,
  usageBreakdown: null,
  thresholdMode: 'manual',
});

describe('RoleAgentSettingsTab auto-promote toggle', () => {
  it('surfaces the auto-promote toggle with plain auto-send + guard copy', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(screen.getByText('Auto-promote')).toBeInTheDocument();
    // Discoverability: the copy must say the agent sends assessments
    // automatically to candidates who pass screening.
    expect(
      screen.getByText(/sends assessments automatically to candidates who pass screening/i),
    ).toBeInTheDocument();
    // And it must surface the safety guard (held for review at the daily/budget cap).
    expect(screen.getByText(/daily send limit or budget cap/i)).toBeInTheDocument();
  });

  it('reflects the persisted auto_promote value and fires onAutonomyChange', () => {
    const onAutonomyChange = vi.fn();
    render(<RoleAgentSettingsTab {...baseProps({ auto_promote: true })} onAutonomyChange={onAutonomyChange} />);
    const toggle = screen.getByRole('button', { name: 'Auto-promote' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(toggle);
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_promote', false);
  });
});
