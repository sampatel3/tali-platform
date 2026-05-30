// Bulk-approve soft guard.
//
// "Approve N visible" advances candidates irreversibly, so the confirmation
// modal surfaces each advancing role's pipeline standing — "X already advanced
// · approving Y more → Z total" — from role.stage_counts (/agent/roles/breakdown)
// and warns when the projected advanced total crosses ADVANCED_SOFT_CAP.

import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Open the modal, then let the per-role Workable-stage fetch settle so the
// trailing setState doesn't fire outside act() and warn.
const openBulkModalAndSettle = async () => {
  fireEvent.click(screen.getByRole('button', { name: /Approve \d+ visible/ }));
  const dialog = screen.getByRole('dialog');
  await within(dialog).findByText('Phone screen');
  return dialog;
};

vi.mock('../../shared/api', () => ({
  agent: {
    approveDecision: vi.fn(),
    bulkApproveDecisions: vi.fn(() => Promise.resolve({ data: { approved: 0, failures: [] } })),
    snoozeDecision: vi.fn(),
    reEvaluateDecision: vi.fn(),
  },
  organizations: {
    // Two Workable stages so the picker renders (and the standing line sits
    // above it). Resolves synchronously enough for the assertions.
    getWorkableStages: vi.fn(() =>
      Promise.resolve({ data: { stages: [{ slug: 'phone', name: 'Phone screen' }] } }),
    ),
  },
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

// Heavy children that fetch on mount — stub them out so the test stays unit-scoped.
vi.mock('./ActivityFeed', () => ({ ActivityFeed: () => null }));
vi.mock('../jobs/AgentNeedsInputCard', () => ({ default: () => null }));

import { HomeNow } from './HomeNow';

const advanceDecision = (id, roleId, roleName) => ({
  id,
  application_id: id * 10,
  candidate_name: `Cand ${id}`,
  decision_type: 'advance_to_interview',
  status: 'pending',
  role_id: roleId,
  role_name: roleName,
  workable_job_id: `wk-${roleId}`,
  created_at: '2026-05-30T00:00:00Z',
});

const renderQueue = (decisions, rolesBreakdown) =>
  render(
    <HomeNow
      decisions={decisions}
      pendingOrdered={decisions}
      selectedId={decisions[0]?.id}
      setSelectedId={() => {}}
      loading={false}
      filters={{ status: 'pending' }}
      setFilters={() => {}}
      rolesBreakdown={rolesBreakdown}
      reload={vi.fn()}
      onNavigate={() => {}}
    />,
  );

describe('HomeNow bulk-approve soft guard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the advanced standing line for each advancing role', async () => {
    const decisions = [
      advanceDecision(1, 7, 'Data Engineer'),
      advanceDecision(2, 7, 'Data Engineer'),
    ];
    const rolesBreakdown = [
      { role_id: 7, name: 'Data Engineer', short_name: 'Data Eng', pending: 2, stage_counts: { advanced: 5 } },
    ];
    renderQueue(decisions, rolesBreakdown);
    const dialog = await openBulkModalAndSettle();

    // 5 already + 2 approving → 7 total.
    expect(
      within(dialog).getByText(/5 already advanced · approving 2 more → 7 total/),
    ).toBeTruthy();
  });

  it('warns when the projected advanced total crosses the soft cap', async () => {
    const decisions = [
      advanceDecision(1, 7, 'Data Engineer'),
      advanceDecision(2, 7, 'Data Engineer'),
    ];
    // 24 already advanced + 2 = 26, over the cap of 25.
    const rolesBreakdown = [
      { role_id: 7, name: 'Data Engineer', short_name: 'Data Eng', pending: 2, stage_counts: { advanced: 24 } },
    ];
    renderQueue(decisions, rolesBreakdown);
    const dialog = await openBulkModalAndSettle();

    expect(within(dialog).getByRole('status')).toBeTruthy();
    expect(within(dialog).getByText(/past 25 advanced/)).toBeTruthy();
  });

  it('does not warn when the projected total stays within the cap', async () => {
    const decisions = [advanceDecision(1, 7, 'Data Engineer')];
    const rolesBreakdown = [
      { role_id: 7, name: 'Data Engineer', short_name: 'Data Eng', pending: 1, stage_counts: { advanced: 3 } },
    ];
    renderQueue(decisions, rolesBreakdown);
    const dialog = await openBulkModalAndSettle();

    expect(within(dialog).queryByRole('status')).toBeNull();
    expect(
      within(dialog).getByText(/3 already advanced · approving 1 more → 4 total/),
    ).toBeTruthy();
  });

  it('treats a missing stage_counts.advanced as zero already advanced', async () => {
    const decisions = [advanceDecision(1, 9, 'Backend Engineer')];
    const rolesBreakdown = [
      { role_id: 9, name: 'Backend Engineer', short_name: 'Backend', pending: 1 },
    ];
    renderQueue(decisions, rolesBreakdown);
    const dialog = await openBulkModalAndSettle();

    expect(
      within(dialog).getByText(/0 already advanced · approving 1 more → 1 total/),
    ).toBeTruthy();
  });
});
