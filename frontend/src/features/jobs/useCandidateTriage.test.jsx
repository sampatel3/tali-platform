import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  organizations: {
    getBullhornStageMap: vi.fn(),
    getWorkableStages: vi.fn(),
  },
}));

import * as apiClient from '../../shared/api';
import {
  buildBullhornAtsStageOptions,
  useCandidateTriage,
} from './useCandidateTriage';

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

describe('buildBullhornAtsStageOptions', () => {
  it('uses the server-resolved remote label while keeping Taali intent as the value', () => {
    expect(buildBullhornAtsStageOptions({
      resolved_write_targets: {
        invited: 'Screening call',
        in_assessment: null,
        review: 'Client Review',
        advanced: 'Interview Scheduled',
      },
      mappings: [
        { remote_status: 'Placed', taali_stage: 'advanced', is_reject: false },
      ],
    })).toEqual([
      { slug: 'invited', name: 'Screening call', kind: 'invited' },
      { slug: 'review', name: 'Client Review', kind: 'review' },
      { slug: 'advanced', name: 'Interview Scheduled', kind: 'advanced' },
    ]);
  });

  it('does not guess an ambiguous mapping on legacy stage-map payloads', () => {
    expect(buildBullhornAtsStageOptions({
      mappings: [
        { remote_status: 'Phone screen', taali_stage: 'invited', is_reject: false },
        { remote_status: 'Recruiter screen', taali_stage: 'invited', is_reject: false },
        { remote_status: 'Manager review', taali_stage: 'review', is_reject: false },
        { remote_status: 'Interview', taali_stage: 'advanced', is_reject: false },
        { remote_status: 'Rejected', taali_stage: 'review', is_reject: true },
      ],
    })).toEqual([
      { slug: 'review', name: 'Manager review', kind: 'review' },
      { slug: 'advanced', name: 'Mapped Bullhorn advance', kind: 'advanced' },
    ]);
  });
});

describe('useCandidateTriage Bullhorn hand-back', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiClient.organizations.getWorkableStages.mockResolvedValue({
      data: { stages: [] },
    });
    apiClient.organizations.getBullhornStageMap.mockResolvedValue({
      data: {
        resolved_write_targets: {
          invited: null,
          in_assessment: null,
          review: null,
          advanced: 'Interview Scheduled',
        },
      },
    });
  });

  it.each([
    ['no ATS provider', { id: 10 }],
    [
      'Workable without an external job ID',
      { id: 10, ats_provider: 'workable', external_job_id: null },
    ],
  ])('clears an in-flight stage spinner when the next role has %s', async (_label, nextRole) => {
    let resolveStages;
    apiClient.organizations.getWorkableStages.mockReturnValue(new Promise((resolve) => {
      resolveStages = resolve;
    }));
    const baseProps = {
      roleApplications: [],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow: vi.fn(),
      showToast: vi.fn(),
      rolesApi: {},
      viewCandidateReport: vi.fn(),
    };
    const { result, rerender } = renderHook(
      ({ role }) => useCandidateTriage({ ...baseProps, role }),
      {
        initialProps: {
          role: {
            id: 9,
            ats_provider: 'workable',
            external_job_id: 'WORKABLE-9',
          },
        },
      },
    );

    await waitFor(() => expect(result.current.drawerProps.loadingAtsStages).toBe(true));

    rerender({ role: nextRole });

    await waitFor(() => expect(result.current.drawerProps.loadingAtsStages).toBe(false));
    expect(result.current.drawerProps.atsStages).toEqual([]);

    await act(async () => {
      resolveStages({ data: { stages: [{ slug: 'review', name: 'Review' }] } });
    });
    expect(result.current.drawerProps.loadingAtsStages).toBe(false);
    expect(result.current.drawerProps.atsStages).toEqual([]);
  });

  it('refuses direct assessment sends from a related-role handler', async () => {
    const application = { id: 40, source: 'workable', application_outcome: 'open' };
    const rolesApi = { createAssessment: vi.fn() };
    const patchApplicationRow = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 17, role_kind: 'sister' },
      roleApplications: [application],
      roleTasks: [{ id: 5, name: 'Shared owner task', is_active: true }],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onSendAssessment(application, '5');
    });

    expect(rolesApi.createAssessment).not.toHaveBeenCalled();
    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenLastCalledWith(
      expect.stringMatching(/score-only.*no invite was sent/i),
      'info',
    );
  });

  it('creates the first assessment with the selected task', async () => {
    const application = { id: 51, application_outcome: 'open', score_summary: {} };
    const rolesApi = {
      createAssessment: vi.fn().mockResolvedValue({ data: {} }),
      retakeAssessment: vi.fn(),
    };
    const patchApplicationRow = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, role_kind: 'standard' },
      roleApplications: [application],
      roleTasks: [{ id: 5, name: 'Backend take-home', is_active: true }],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast: vi.fn(),
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    let sent;
    await act(async () => {
      sent = await result.current.drawerProps.onSendAssessment(application, '5');
    });

    expect(sent).toBe(true);
    expect(rolesApi.createAssessment).toHaveBeenCalledWith(51, { task_id: 5 });
    expect(rolesApi.retakeAssessment).not.toHaveBeenCalled();
    expect(patchApplicationRow).toHaveBeenCalledWith(51);
  });

  it('does not let an assessment completion from the previous role patch or unlock the current role', async () => {
    const oldSend = deferred();
    const currentSend = deferred();
    const oldApplication = { id: 51, application_outcome: 'open', score_summary: {} };
    const currentApplication = { id: 52, application_outcome: 'open', score_summary: {} };
    const rolesApi = {
      createAssessment: vi.fn((applicationId) => (
        applicationId === oldApplication.id ? oldSend.promise : currentSend.promise
      )),
      retakeAssessment: vi.fn(),
    };
    const patchApplicationRow = vi.fn().mockResolvedValue(undefined);
    const showToast = vi.fn();
    const baseProps = {
      roleTasks: [{ id: 5, name: 'Backend take-home', is_active: true }],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    };
    const { result, rerender } = renderHook(
      ({ scopeKey, role, roleApplications }) => useCandidateTriage({
        ...baseProps, scopeKey, role, roleApplications,
      }),
      {
        initialProps: {
          scopeKey: 9,
          role: { id: 9, role_kind: 'standard' },
          roleApplications: [oldApplication],
        },
      },
    );

    let oldResult;
    act(() => {
      oldResult = result.current.drawerProps.onSendAssessment(oldApplication, '5');
    });
    await waitFor(() => expect(result.current.drawerProps.assessmentBusy).toBe(true));

    rerender({
      scopeKey: 10,
      role: { id: 10, role_kind: 'standard' },
      roleApplications: [currentApplication],
    });
    let currentResult;
    act(() => {
      currentResult = result.current.drawerProps.onSendAssessment(currentApplication, '5');
    });
    await waitFor(() => expect(result.current.drawerProps.assessmentBusy).toBe(true));

    await act(async () => {
      oldSend.resolve({ data: {} });
      await oldResult;
    });

    expect(result.current.drawerProps.assessmentBusy).toBe(true);
    expect(patchApplicationRow).not.toHaveBeenCalledWith(oldApplication.id);
    expect(showToast).not.toHaveBeenCalledWith('Assessment invite sent.', 'success');

    await act(async () => {
      currentSend.resolve({ data: {} });
      await currentResult;
    });
    expect(result.current.drawerProps.assessmentBusy).toBe(false);
    expect(patchApplicationRow).toHaveBeenCalledTimes(1);
    expect(patchApplicationRow).toHaveBeenCalledWith(currentApplication.id);
    expect(showToast).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['inactive', { id: 5, name: 'Retired task', is_active: false }],
    ['unconfirmed', { id: 5, name: 'Malformed task' }],
  ])('refuses a direct send through an %s task link', async (_label, task) => {
    const application = { id: 55, application_outcome: 'open', score_summary: {} };
    const rolesApi = { createAssessment: vi.fn(), retakeAssessment: vi.fn() };
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, role_kind: 'standard' },
      roleApplications: [application],
      roleTasks: [task],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow: vi.fn(),
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    let sent;
    await act(async () => {
      sent = await result.current.drawerProps.onSendAssessment(application, '5');
    });

    expect(sent).toBe(false);
    expect(rolesApi.createAssessment).not.toHaveBeenCalled();
    expect(rolesApi.retakeAssessment).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/inactive or no longer linked/i), 'error');
  });

  it('refuses a stale direct send while task availability is unconfirmed', async () => {
    const application = { id: 56, application_outcome: 'open', score_summary: {} };
    const rolesApi = { createAssessment: vi.fn() };
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, role_kind: 'standard' },
      roleApplications: [application],
      roleTasks: [{ id: 5, name: 'Last known task', is_active: true }],
      roleTasksFetchKnown: false,
      roleTasksLoadError: 'Current task assignment could not be loaded.',
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow: vi.fn(),
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onSendAssessment(application, '5');
    });

    expect(rolesApi.createAssessment).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith('Current task assignment could not be loaded.', 'error');
  });

  it('uses the retake endpoint and preserves an optional replacement reason', async () => {
    const application = {
      id: 52,
      application_outcome: 'open',
      valid_assessment_id: 901,
      score_summary: {},
    };
    const rolesApi = {
      createAssessment: vi.fn(),
      retakeAssessment: vi.fn().mockResolvedValue({ data: {} }),
    };
    const patchApplicationRow = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, role_kind: 'standard' },
      roleApplications: [application],
      roleTasks: [{ id: 6, name: 'Systems exercise', is_active: true }],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast: vi.fn(),
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    let sent;
    await act(async () => {
      sent = await result.current.drawerProps.onSendAssessment(
        application,
        '6',
        { voidReason: 'Candidate lost connectivity' },
      );
    });

    expect(sent).toBe(true);
    expect(rolesApi.retakeAssessment).toHaveBeenCalledWith(52, {
      task_id: 6,
      void_reason: 'Candidate lost connectivity',
    });
    expect(rolesApi.createAssessment).not.toHaveBeenCalled();
    expect(patchApplicationRow).toHaveBeenCalledWith(52);
  });

  it('returns false and leaves reconciliation untouched when a retake fails', async () => {
    const application = {
      id: 53,
      application_outcome: 'open',
      score_summary: { assessment_id: 902 },
    };
    const conflict = new Error('already replaced');
    conflict.response = { status: 409, data: { detail: 'Assessment changed; refresh and retry.' } };
    const rolesApi = {
      createAssessment: vi.fn(),
      retakeAssessment: vi.fn().mockRejectedValue(conflict),
    };
    const patchApplicationRow = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, role_kind: 'standard' },
      roleApplications: [application],
      roleTasks: [{ id: 6, name: 'Systems exercise', is_active: true }],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    let sent;
    await act(async () => {
      sent = await result.current.drawerProps.onSendAssessment(application, '6');
    });

    expect(sent).toBe(false);
    expect(rolesApi.createAssessment).not.toHaveBeenCalled();
    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenLastCalledWith(
      'Assessment changed; refresh and retry.',
      'error',
    );
  });

  it('fails closed for every candidate mutation while capability is unavailable', async () => {
    const application = { id: 54, application_outcome: 'open' };
    const rolesApi = {
      createAssessment: vi.fn(),
      updateApplicationOutcome: vi.fn(),
      updateApplicationStage: vi.fn(),
      moveApplicationToAtsStage: vi.fn(),
    };
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, role_kind: 'standard' },
      roleApplications: [application],
      roleTasks: [{ id: 5, name: 'Backend take-home', is_active: true }],
      canMutate: false,
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow: vi.fn(),
      showToast: vi.fn(),
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onSendAssessment(application, '5');
      await result.current.drawerProps.onReject(application);
      await result.current.drawerProps.onMoveStage(application, 'review');
      await result.current.drawerProps.onMoveToAtsStage(application, 'advanced');
    });

    expect(rolesApi.createAssessment).not.toHaveBeenCalled();
    expect(rolesApi.updateApplicationOutcome).not.toHaveBeenCalled();
    expect(rolesApi.updateApplicationStage).not.toHaveBeenCalled();
    expect(rolesApi.moveApplicationToAtsStage).not.toHaveBeenCalled();
  });

  it('attributes a shared-application rejection to the acting related role', async () => {
    const application = {
      id: 55, source: 'manual', application_outcome: 'open', version: 3,
    };
    const roleFamily = {
      owner: { id: 9, name: 'Platform Engineer' },
      related: [{ id: 17, name: 'Related Platform Engineer' }],
    };
    const rolesApi = {
      updateApplicationOutcome: vi.fn().mockResolvedValue({
        data: { ...application, application_outcome: 'rejected' },
      }),
    };
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 17, role_kind: 'sister', role_family: roleFamily },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow: vi.fn().mockResolvedValue(undefined),
      showToast: vi.fn(),
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onReject(application);
    });

    expect(rolesApi.updateApplicationOutcome).toHaveBeenCalledWith(55, {
      application_outcome: 'rejected',
      reason: 'Recruiter reject from role view',
      expected_version: 3,
      acting_role_id: 17,
      expected_role_family: roleFamily,
    });
  });

  it('reloads a changed role family without closing or mutating the drawer row', async () => {
    const application = { id: 56, source: 'manual', application_outcome: 'open' };
    const roleFamily = {
      owner: { id: 9, name: 'Platform Engineer' },
      related: [{ id: 17, name: 'Related Platform Engineer' }],
    };
    const rolesApi = {
      updateApplicationOutcome: vi.fn().mockRejectedValue({
        response: {
          status: 409,
          data: { detail: { code: 'ROLE_FAMILY_CHANGED' } },
        },
      }),
    };
    const loadRoleWorkspace = vi.fn().mockResolvedValue(undefined);
    const patchApplicationRow = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 17, role_kind: 'sister', role_family: roleFamily },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace,
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    act(() => {
      result.current.handleRowClick({
        defaultPrevented: false,
        preventDefault: vi.fn(),
      }, application);
    });
    expect(result.current.triageApplication).toEqual(application);

    let rejected;
    await act(async () => {
      rejected = await result.current.drawerProps.onReject(application);
    });

    expect(rejected).toBe(false);
    expect(rolesApi.updateApplicationOutcome).toHaveBeenCalledWith(56, {
      application_outcome: 'rejected',
      reason: 'Recruiter reject from role view',
      acting_role_id: 17,
      expected_role_family: roleFamily,
    });
    expect(loadRoleWorkspace).toHaveBeenCalledOnce();
    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(result.current.triageApplication).toEqual(application);
    expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining('review it before trying again'),
      'warning',
    );
  });

  it('forwards the complete role family to the HITL drawer', () => {
    const roleFamily = {
      owner: { id: 31, name: 'Data Platform Lead' },
      related: [{ id: 47, name: 'AI Engineer' }],
    };
    const { result } = renderHook(() => useCandidateTriage({
      role: {
        id: 47,
        role_kind: 'sister',
        sister_role_count: 1,
        role_family: roleFamily,
      },
      roleApplications: [],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow: vi.fn(),
      showToast: vi.fn(),
      rolesApi: {},
      viewCandidateReport: vi.fn(),
    }));

    expect(result.current.drawerProps).toEqual(expect.objectContaining({
      isRelatedRole: true,
      hasRelatedRoles: true,
      roleFamily,
    }));
  });

  it('posts the selected Taali intent rather than Bullhorn free text', async () => {
    const application = { id: 41, source: 'bullhorn' };
    const rolesApi = {
      moveApplicationToAtsStage: vi.fn().mockResolvedValue({
        data: {
          ...application,
          ats_writeback_status: 'queued',
          ats_writeback_job_run_id: 901,
        },
      }),
      backgroundJobRun: vi.fn().mockResolvedValue({
        data: { id: 901, status: 'completed', counters: {} },
      }),
    };
    const patchApplicationRow = vi.fn().mockResolvedValue(undefined);
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, ats_provider: 'bullhorn', external_job_id: 'BH-900' },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await waitFor(() => {
      expect(result.current.drawerProps.atsStages).toEqual([
        { slug: 'advanced', name: 'Interview Scheduled', kind: 'advanced' },
      ]);
    });

    const selectedOption = result.current.drawerProps.atsStages[0];
    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(
        application,
        selectedOption.slug,
        selectedOption.name,
      );
    });

    expect(rolesApi.moveApplicationToAtsStage).toHaveBeenCalledWith(41, {
      target_stage: 'advanced',
    });
    expect(rolesApi.moveApplicationToAtsStage).not.toHaveBeenCalledWith(
      41,
      { target_stage: 'Interview Scheduled' },
    );
    expect(rolesApi.backgroundJobRun).toHaveBeenCalledWith(901);
    expect(patchApplicationRow).toHaveBeenCalledWith(41);
    expect(showToast).toHaveBeenNthCalledWith(
      1,
      'Bullhorn move queued. Waiting for confirmation…',
      'info',
    );
    expect(showToast).toHaveBeenLastCalledWith(
      'Moved in Bullhorn: Interview Scheduled.',
      'success',
    );
  });

  it('attributes a related-role advance and refreshes its durable worker transition', async () => {
    const application = { id: 45, source: 'bullhorn', application_outcome: 'open' };
    const rolesApi = {
      moveRelatedApplicationToAtsStage: vi.fn().mockResolvedValue({
        data: {
          ...application,
          ats_writeback_job_run_id: 904,
          ats_related_transition_protocol: 1,
          ats_related_stage_managed: true,
        },
      }),
      backgroundJobRun: vi.fn().mockResolvedValue({
        data: { id: 904, status: 'completed', counters: {} },
      }),
      updateRelatedApplicationStage: vi.fn().mockResolvedValue({ data: {} }),
    };
    const patchApplicationRow = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useCandidateTriage({
      role: {
        id: 17,
        role_kind: 'sister',
        ats_provider: 'bullhorn',
        external_job_id: 'BH-900',
      },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast: vi.fn(),
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(
        application,
        'advanced',
        'Interview Scheduled',
      );
    });

    expect(rolesApi.moveRelatedApplicationToAtsStage).toHaveBeenCalledWith(
      17,
      45,
      {
        target_stage: 'advanced',
        acting_role_id: 17,
      },
    );
    expect(rolesApi.updateRelatedApplicationStage).not.toHaveBeenCalled();
    expect(patchApplicationRow).toHaveBeenCalledWith(45);
  });

  it('fails before provider mutation when the managed related endpoint is unavailable', async () => {
    const application = { id: 46, source: 'workable', application_outcome: 'open' };
    const rolesApi = {
      // These old endpoints may exist and return 200 while silently ignoring
      // acting_role_id. The related flow must not call either one.
      moveApplicationToAtsStage: vi.fn(),
      moveApplicationToWorkableStage: vi.fn(),
    };
    const patchApplicationRow = vi.fn().mockResolvedValue(undefined);
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: {
        id: 18,
        role_kind: 'sister',
        ats_provider: 'workable',
        external_job_id: 'workable-owner-job',
      },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(
        application,
        'final-interview',
        'Final interview',
      );
    });

    expect(rolesApi.moveApplicationToAtsStage).not.toHaveBeenCalled();
    expect(rolesApi.moveApplicationToWorkableStage).not.toHaveBeenCalled();
    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenLastCalledWith(
      expect.stringContaining('No provider update was sent'),
      'error',
    );
  });

  it('does not fall back when a rolling-deploy instance lacks the managed route', async () => {
    const application = { id: 47, source: 'workable', application_outcome: 'open' };
    const routeMissing = new Error('managed route is not deployed');
    routeMissing.response = {
      status: 404,
      data: { detail: 'Managed related-role ATS moves are not available on this instance.' },
    };
    const rolesApi = {
      moveRelatedApplicationToAtsStage: vi.fn().mockRejectedValue(routeMissing),
      moveApplicationToAtsStage: vi.fn(),
      moveApplicationToWorkableStage: vi.fn(),
    };
    const patchApplicationRow = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 19, role_kind: 'sister', ats_provider: 'workable' },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(
        application,
        'final-interview',
        'Final interview',
      );
    });

    expect(rolesApi.moveRelatedApplicationToAtsStage).toHaveBeenCalledOnce();
    expect(rolesApi.moveApplicationToAtsStage).not.toHaveBeenCalled();
    expect(rolesApi.moveApplicationToWorkableStage).not.toHaveBeenCalled();
    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenLastCalledWith(
      'Managed related-role ATS moves are not available on this instance.',
      'error',
    );
  });

  it('requires the managed receipt and never performs a browser stage write', async () => {
    const application = { id: 48, source: 'bullhorn', application_outcome: 'open' };
    const rolesApi = {
      moveRelatedApplicationToAtsStage: vi.fn().mockResolvedValue({
        data: { ...application, ats_writeback_job_run_id: 906 },
      }),
      backgroundJobRun: vi.fn(),
      updateRelatedApplicationStage: vi.fn(),
    };
    const patchApplicationRow = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 20, role_kind: 'sister', ats_provider: 'bullhorn' },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(application, 'advanced', 'Interview');
    });

    expect(rolesApi.backgroundJobRun).not.toHaveBeenCalled();
    expect(rolesApi.updateRelatedApplicationStage).not.toHaveBeenCalled();
    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenLastCalledWith(
      expect.stringContaining('Check background jobs'),
      'error',
    );
  });

  it('leaves a managed related move server-owned when the response has no run id', async () => {
    const application = { id: 49, source: 'workable', application_outcome: 'open' };
    const rolesApi = {
      moveRelatedApplicationToAtsStage: vi.fn().mockResolvedValue({
        data: {
          ...application,
          ats_related_transition_protocol: 1,
          ats_related_stage_managed: true,
        },
      }),
      backgroundJobRun: vi.fn(),
      updateRelatedApplicationStage: vi.fn(),
    };
    const patchApplicationRow = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 21, role_kind: 'sister', ats_provider: 'workable' },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(
        application,
        'final-interview',
        'Final interview',
      );
    });

    expect(rolesApi.backgroundJobRun).not.toHaveBeenCalled();
    expect(rolesApi.updateRelatedApplicationStage).not.toHaveBeenCalled();
    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenLastCalledWith(
      'Workable move is still queued. The stage will update after the provider confirms it.',
      'info',
    );
  });

  it('keeps the standard-role Workable compatibility fallback', async () => {
    const application = { id: 50, source: 'workable', application_outcome: 'open' };
    const routeMissing = new Error('provider-neutral route is not deployed');
    routeMissing.response = { status: 404 };
    const rolesApi = {
      moveApplicationToAtsStage: vi.fn().mockRejectedValue(routeMissing),
      moveApplicationToWorkableStage: vi.fn().mockResolvedValue({
        data: { ...application, ats_writeback_job_run_id: 907 },
      }),
      backgroundJobRun: vi.fn().mockResolvedValue({
        data: { id: 907, status: 'completed', counters: {} },
      }),
    };
    const patchApplicationRow = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 22, role_kind: 'standard', ats_provider: 'workable' },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast: vi.fn(),
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(
        application,
        'final-interview',
        'Final interview',
      );
    });

    expect(rolesApi.moveApplicationToWorkableStage).toHaveBeenCalledWith(50, {
      target_stage: 'final-interview',
    });
    expect(rolesApi.backgroundJobRun).toHaveBeenCalledWith(907);
    expect(patchApplicationRow).toHaveBeenCalledWith(50);
  });

  it('does not patch or report success when the queued provider write fails', async () => {
    const application = { id: 42, source: 'bullhorn' };
    const rolesApi = {
      moveApplicationToAtsStage: vi.fn().mockResolvedValue({
        data: { ...application, ats_writeback_status: 'queued', ats_writeback_job_run_id: 902 },
      }),
      backgroundJobRun: vi.fn().mockResolvedValue({
        data: { id: 902, status: 'failed', error: 'Bullhorn status mapping was removed.' },
      }),
    };
    const patchApplicationRow = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, ats_provider: 'bullhorn', external_job_id: 'BH-900' },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(application, 'advanced', 'Interview');
    });

    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenLastCalledWith(
      'Bullhorn status mapping was removed.',
      'error',
    );
  });

  it('does not poll a fabricated run zero when an older response omits the run id', async () => {
    const application = { id: 43, source: 'bullhorn' };
    const rolesApi = {
      moveApplicationToAtsStage: vi.fn().mockResolvedValue({
        data: { ...application, ats_writeback_status: 'queued' },
      }),
      backgroundJobRun: vi.fn(),
    };
    const patchApplicationRow = vi.fn();
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, ats_provider: 'bullhorn', external_job_id: 'BH-900' },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onMoveToAtsStage(application, 'advanced', 'Interview');
    });

    expect(rolesApi.backgroundJobRun).not.toHaveBeenCalled();
    expect(patchApplicationRow).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenLastCalledWith(
      'Bullhorn move is still queued. The stage will update after the provider confirms it.',
      'info',
    );
  });

  it('tracks a queued Bullhorn reject through provider confirmation', async () => {
    const application = { id: 44, source: 'bullhorn', application_outcome: 'open' };
    const rolesApi = {
      updateApplicationOutcome: vi.fn().mockResolvedValue({
        data: {
          ...application,
          application_outcome: 'rejected',
          ats_writeback_status: 'queued',
          ats_writeback_job_run_id: 903,
        },
      }),
      backgroundJobRun: vi.fn().mockResolvedValue({
        data: { id: 903, status: 'completed', counters: {} },
      }),
    };
    const patchApplicationRow = vi.fn().mockResolvedValue(undefined);
    const showToast = vi.fn();
    const { result } = renderHook(() => useCandidateTriage({
      role: { id: 9, ats_provider: 'bullhorn', external_job_id: 'BH-900' },
      roleApplications: [application],
      roleTasks: [],
      loadRoleWorkspace: vi.fn(),
      patchApplicationRow,
      showToast,
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onReject(application);
    });

    expect(rolesApi.updateApplicationOutcome).toHaveBeenCalledWith(44, {
      application_outcome: 'rejected',
      reason: 'Recruiter reject from role view',
    });
    expect(patchApplicationRow).toHaveBeenCalledWith(44);
    expect(patchApplicationRow).toHaveBeenCalledTimes(2);
    expect(rolesApi.backgroundJobRun).toHaveBeenCalledWith(903);
    expect(showToast).toHaveBeenNthCalledWith(
      1,
      'Candidate rejected in Taali. Waiting for Bullhorn confirmation…',
      'info',
    );
    expect(showToast).toHaveBeenLastCalledWith(
      'Candidate rejected in Bullhorn.',
      'success',
    );
  });
});
