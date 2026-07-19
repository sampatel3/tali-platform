import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { vi } from 'vitest';

import { HomeNow } from './HomeNow';

// Sourced tracker (Phase 3b) guards:
//  - toggling the "Sourced" chip fetches pipeline_stage=sourced and renders the
//    prospects as a read-only tracker, grouped by role;
//  - it carries no score chip and no decision action — sourced leads have no
//    verdict and are separate from the pending-decision queue;
//  - the toolbar chip surfaces the sourced count from the role breakdown.

const listApplicationsGlobal = vi.fn();
const listDecisions = vi.fn().mockResolvedValue({ data: [] });
const getWorkableStages = vi.fn().mockResolvedValue({ data: { stages: [] } });

vi.mock('../../shared/api', () => ({
  agent: {
    approveDecision: vi.fn().mockResolvedValue({ data: {} }),
    bulkApproveDecisions: vi.fn().mockResolvedValue({ data: {} }),
    bulkOverrideDecisions: vi.fn().mockResolvedValue({ data: {} }),
    overrideDecision: vi.fn().mockResolvedValue({ data: {} }),
    snoozeDecision: vi.fn().mockResolvedValue({ data: {} }),
    reEvaluateDecision: vi.fn().mockResolvedValue({ data: {} }),
    listDecisions: (...a) => listDecisions(...a),
  },
  organizations: {
    getWorkableStages: (...a) => getWorkableStages(...a),
  },
  roles: {
    listApplicationsGlobal: (...a) => listApplicationsGlobal(...a),
  },
}));

const mkAdvance = (id, name) => ({
  id,
  decision_type: 'advance_to_interview',
  status: 'pending',
  candidate_name: name,
  candidate_email: `${name.split(' ')[0].toLowerCase()}@example.com`,
  application_id: id * 10,
  role_id: 53,
  role_name: 'Data Engineer',
  workable_job_id: 'de-shortcode',
  created_at: '2026-06-07T10:00:00Z',
  applied_at: '2026-06-01T10:00:00Z',
  reasoning: 'Strong fit.',
  taali_score: 80,
});

const mkSourced = (id, name, roleName, source = 'sourced') => ({
  id,
  candidate_name: name,
  candidate_email: `${name.split(' ')[0].toLowerCase()}@example.com`,
  role_id: roleName === 'Data Engineer' ? 53 : 71,
  role_name: roleName,
  pipeline_stage: 'sourced',
  application_outcome: 'open',
  source,
  created_at: '2026-06-10T10:00:00Z',
});

const rolesBreakdown = [
  { role_id: 53, name: 'Data Engineer', stage_counts: { sourced: 2, applied: 5 } },
  { role_id: 71, name: 'Platform Lead', stage_counts: { sourced: 1, applied: 3 } },
];

const renderHome = (overrides = {}) => render(
  <HomeNow
    decisions={[mkAdvance(1, 'Miguel Parracho')]}
    pendingOrdered={[mkAdvance(1, 'Miguel Parracho')]}
    selectedId={1}
    setSelectedId={vi.fn()}
    loading={false}
    filters={{ status: 'pending', role_id: null, type: null, q: null, view: null }}
    setFilters={vi.fn()}
    rolesBreakdown={rolesBreakdown}
    reload={vi.fn().mockResolvedValue(undefined)}
    onNavigate={vi.fn()}
    questionsInDock
    {...overrides}
  />,
);

const settleHomeMount = async () => {
  await act(async () => {
    await Promise.all([
      listDecisions.mock.results.at(-1)?.value,
      getWorkableStages.mock.results.at(-1)?.value,
    ].filter(Boolean));
  });
};

describe('HomeNow — Sourced tracker', () => {
  beforeEach(() => {
    listApplicationsGlobal.mockReset();
    listDecisions.mockReset().mockResolvedValue({ data: [] });
    getWorkableStages.mockReset().mockResolvedValue({ data: { stages: [] } });
  });

  it('shows the sourced count on the toolbar chip from the role breakdown', async () => {
    listApplicationsGlobal.mockResolvedValue({ data: { items: [] } });
    renderHome();
    await settleHomeMount();
    // 2 (Data Engineer) + 1 (Platform Lead) summed across roles.
    const chip = screen.getByRole('button', { name: /^Sourced/ });
    expect(within(chip).getByText('3')).toBeInTheDocument();
  });

  it('shows a selected related-role funnel when its assessment count is completed', async () => {
    listApplicationsGlobal.mockResolvedValue({ data: { items: [] } });
    const { container } = renderHome({
      filters: { status: 'pending', role_id: 135, type: null, q: null, view: null },
      rolesBreakdown: [{
        role_id: 135,
        name: 'AI Engineer · Platform',
        stage_counts: {
          sourced: 0,
          applied: 0,
          scored: 0,
          invited: 0,
          completed: 5,
          advanced: 0,
          rejected: 0,
        },
      }],
    });
    await settleHomeMount();

    const funnel = container.querySelector('.funnel-board');
    expect(funnel).not.toBeNull();
    const invitedCell = within(funnel).getByText('Invited').closest('.fb-st');
    expect(within(invitedCell).getByText('5')).toBeInTheDocument();
  });

  it('the Sourced chip toggles the view (calls setFilters with view=sourced)', async () => {
    listApplicationsGlobal.mockResolvedValue({ data: { items: [] } });
    const setFilters = vi.fn();
    renderHome({ setFilters });
    await settleHomeMount();
    fireEvent.click(screen.getByRole('button', { name: /^Sourced/ }));
    // setFilters is called with an updater — apply it to see the produced view.
    const updater = setFilters.mock.calls.at(-1)[0];
    expect(updater({ view: null })).toMatchObject({ view: 'sourced' });
  });

  it('fetches pipeline_stage=sourced and renders prospects grouped by role, as a tracker', async () => {
    listApplicationsGlobal.mockResolvedValue({
      data: {
        items: [
          mkSourced(101, 'Ada Sourced', 'Data Engineer'),
          mkSourced(102, 'Grace Prospect', 'Platform Lead', 'workable'),
        ],
      },
    });
    renderHome({ filters: { status: 'pending', role_id: null, type: null, q: null, view: 'sourced' } });

    // Fetch is the sourced-stage filter, not the invited assessment_status one.
    await waitFor(() => expect(listApplicationsGlobal).toHaveBeenCalled());
    expect(listApplicationsGlobal).toHaveBeenCalledWith(
      expect.objectContaining({ pipeline_stage: 'sourced' }),
    );

    // Rows render with name + when-sourced + channel; grouped under role labels.
    expect(await screen.findByText('Ada Sourced')).toBeInTheDocument();
    expect(screen.getByText('Grace Prospect')).toBeInTheDocument();
    expect(screen.getByText(/added manually/)).toBeInTheDocument();
    expect(screen.getByText(/via Workable/)).toBeInTheDocument();
    expect(screen.getAllByText(/Sourced .+ ago/).length).toBeGreaterThan(0);
  });

  it('is separate from the decision queue — no decision actions and no queue rows in the sourced view', async () => {
    listApplicationsGlobal.mockResolvedValue({
      data: { items: [mkSourced(101, 'Ada Sourced', 'Data Engineer')] },
    });
    renderHome({ filters: { status: 'pending', role_id: null, type: null, q: null, view: 'sourced' } });

    await screen.findByText('Ada Sourced');
    // The pending decision's candidate (queue) is NOT shown in the sourced view.
    expect(screen.queryByText('Miguel Parracho')).not.toBeInTheDocument();
    // No bulk-approve / decision action for sourced leads (they have no verdict).
    expect(screen.queryByRole('button', { name: /Approve \d+ visible/i })).not.toBeInTheDocument();
    // The tracker link keeps the role context as well as the application id.
    const link = screen.getByRole('link', { name: 'Ada Sourced' });
    expect(link).toHaveAttribute('href', '/candidates/101?from=home&view_role_id=53');
  });
});
