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

  it('attributes a related-role ATS move and lets the backend project its confirmed stage', async () => {
    const application = { id: 45, source: 'bullhorn', application_outcome: 'open' };
    const rolesApi = {
      moveApplicationToAtsStage: vi.fn().mockResolvedValue({
        data: { ...application, ats_writeback_job_run_id: 904 },
      }),
      backgroundJobRun: vi.fn().mockResolvedValue({
        data: { id: 904, status: 'completed', counters: {} },
      }),
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

    expect(rolesApi.moveApplicationToAtsStage).toHaveBeenCalledWith(45, {
      target_stage: 'advanced',
      acting_role_id: 17,
    });
    expect(patchApplicationRow).toHaveBeenCalledWith(45);
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

  it('attributes a related-role rejection to that role hiring team', async () => {
    const application = { id: 46, source: 'bullhorn', application_outcome: 'open' };
    const rolesApi = {
      updateApplicationOutcome: vi.fn().mockResolvedValue({
        data: { ...application, application_outcome: 'rejected' },
      }),
    };
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
      patchApplicationRow: vi.fn(),
      showToast: vi.fn(),
      rolesApi,
      viewCandidateReport: vi.fn(),
    }));

    await act(async () => {
      await result.current.drawerProps.onReject(application);
    });

    expect(rolesApi.updateApplicationOutcome).toHaveBeenCalledWith(46, {
      application_outcome: 'rejected',
      reason: 'Recruiter reject from role view',
      acting_role_id: 17,
    });
  });
});
