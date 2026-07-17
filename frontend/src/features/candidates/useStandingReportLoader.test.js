import { describe, expect, it, vi } from 'vitest';

import { loadRecruiterStandingReportData } from './useStandingReportLoader';

const decisionQuery = (roleId) => ({
  application_id: 42,
  role_id: roleId,
  status: 'current',
  limit: 1,
});

describe('loadRecruiterStandingReportData', () => {
  it('derives decision authority from the application role for legacy links', async () => {
    const rolesApi = {
      getApplication: vi.fn().mockResolvedValue({
        data: { id: 42, role_id: 8, application_events: [{ id: 'embedded' }] },
      }),
      listApplicationEvents: vi.fn().mockResolvedValue({ data: { items: [{ id: 'fresh' }] } }),
    };
    const agentApi = {
      listDecisions: vi.fn().mockResolvedValue({ data: [{ id: 81, role_id: 8 }] }),
    };

    const result = await loadRecruiterStandingReportData({
      agentApi,
      assessmentsApi: null,
      numericApplicationId: 42,
      rolesApi,
      viewRoleId: null,
    });

    expect(rolesApi.getApplication).toHaveBeenCalledWith(42, {});
    expect(agentApi.listDecisions).toHaveBeenCalledWith(decisionQuery(8));
    expect(result.decision).toEqual({ id: 81, role_id: 8 });
    expect(result.events).toEqual([{ id: 'fresh' }]);
  });

  it('loads a role projection and its decision with the same explicit role', async () => {
    const rolesApi = {
      getApplication: vi.fn().mockResolvedValue({ data: { id: 42, role_id: 135 } }),
    };
    const agentApi = {
      listDecisions: vi.fn().mockResolvedValue({ data: [{ id: 1351, role_id: 135 }] }),
    };

    const result = await loadRecruiterStandingReportData({
      agentApi,
      numericApplicationId: 42,
      rolesApi,
      viewRoleId: 135,
    });

    expect(rolesApi.getApplication).toHaveBeenCalledWith(42, {
      params: { view_role_id: 135 },
    });
    expect(agentApi.listDecisions).toHaveBeenCalledOnce();
    expect(agentApi.listDecisions).toHaveBeenCalledWith(decisionQuery(135));
    expect(result.decision).toEqual({ id: 1351, role_id: 135 });
  });

  it('reconciles a stale projection link to the role actually returned', async () => {
    const rolesApi = {
      getApplication: vi.fn().mockResolvedValue({ data: { id: 42, role_id: 31 } }),
    };
    const agentApi = {
      listDecisions: vi.fn(({ role_id: roleId }) => Promise.resolve({
        data: [{ id: roleId * 10, role_id: roleId }],
      })),
    };

    const result = await loadRecruiterStandingReportData({
      agentApi,
      numericApplicationId: 42,
      rolesApi,
      viewRoleId: 135,
    });

    expect(agentApi.listDecisions).toHaveBeenNthCalledWith(1, decisionQuery(135));
    expect(agentApi.listDecisions).toHaveBeenNthCalledWith(2, decisionQuery(31));
    expect(result.decision).toEqual({ id: 310, role_id: 31 });
  });

  it('fails an authoritative refresh when no decision role can be proven', async () => {
    const rolesApi = {
      getApplication: vi.fn().mockResolvedValue({ data: { id: 42, role_id: null } }),
    };

    await expect(loadRecruiterStandingReportData({
      agentApi: { listDecisions: vi.fn() },
      numericApplicationId: 42,
      requireDecision: true,
      rolesApi,
      viewRoleId: null,
    })).rejects.toThrow('Decision refresh unavailable');
  });

  it('fetches assessment detail only for a completed linked attempt', async () => {
    const rolesApi = {
      getApplication: vi.fn().mockResolvedValue({
        data: {
          id: 42,
          role_id: 8,
          valid_assessment_id: 912,
          valid_assessment_status: 'completed',
        },
      }),
    };
    const assessmentsApi = {
      get: vi.fn().mockResolvedValue({ data: { id: 912, status: 'completed' } }),
    };

    const result = await loadRecruiterStandingReportData({
      agentApi: { listDecisions: vi.fn().mockResolvedValue({ data: [] }) },
      assessmentsApi,
      numericApplicationId: 42,
      rolesApi,
    });

    expect(assessmentsApi.get).toHaveBeenCalledWith(912);
    expect(result.completedAssessment).toEqual({ id: 912, status: 'completed' });
  });
});
