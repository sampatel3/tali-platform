import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useRoleBriefControls } from './useRoleBriefControls';

const clientList = vi.hoisted(() => vi.fn());

vi.mock('../clients/api', () => ({
  clientApi: { list: clientList },
}));

const baseRole = {
  id: 101,
  version: 7,
  job_status: 'open',
  client_id: 501,
  client_name: 'Platform',
};

const setup = (overrides = {}) => {
  const rolesApi = {
    setJobStatus: vi.fn().mockResolvedValue({
      data: { ...baseRole, version: 8, job_status: 'filled' },
    }),
    setClient: vi.fn().mockResolvedValue({
      data: { ...baseRole, version: 8, client_id: 502, client_name: 'Research' },
    }),
  };
  const props = {
    canControlRole: true,
    handleRoleVersionConflict: vi.fn().mockReturnValue(false),
    role: baseRole,
    roleId: 101,
    rolesApi,
    setRole: vi.fn(),
    showToast: vi.fn(),
    ...overrides,
  };
  return { ...renderHook(() => useRoleBriefControls(props)), props };
};

describe('useRoleBriefControls', () => {
  beforeEach(() => {
    clientList.mockReset().mockResolvedValue([
      { id: 501, name: 'Platform' },
      { id: 502, name: 'Research' },
    ]);
  });

  it('preserves the versioned optimistic job-status mutation', async () => {
    const { result, props } = setup();

    await act(async () => {
      await result.current.setJobStatus('filled');
    });

    expect(props.rolesApi.setJobStatus).toHaveBeenCalledWith(101, 'filled', undefined, 7);
    expect(props.setRole.mock.calls[0][0](baseRole)).toEqual({
      ...baseRole,
      job_status: 'filled',
    });
    expect(props.setRole).toHaveBeenLastCalledWith({
      ...baseRole,
      version: 8,
      job_status: 'filled',
    });
    expect(props.showToast).toHaveBeenCalledWith('Job status updated.', 'success');
    expect(result.current.savingJobStatus).toBe(false);
  });

  it('loads departments and preserves the optimistic client mutation', async () => {
    const { result, props } = setup();
    await waitFor(() => expect(result.current.clients).toHaveLength(2));

    await act(async () => {
      await result.current.setClient(502);
    });

    expect(props.rolesApi.setClient).toHaveBeenCalledWith(101, 502, 7);
    expect(props.setRole.mock.calls[0][0](baseRole)).toEqual({
      ...baseRole,
      client_id: 502,
      client_name: 'Research',
    });
    expect(props.showToast).toHaveBeenCalledWith('Hiring department assigned.', 'success');
    expect(result.current.savingClient).toBe(false);
  });

  it('keeps both mutations inert when role control is unavailable', async () => {
    const { result, props } = setup({ canControlRole: false });

    await act(async () => {
      await result.current.setJobStatus('filled');
      await result.current.setClient(502);
    });

    expect(props.rolesApi.setJobStatus).not.toHaveBeenCalled();
    expect(props.rolesApi.setClient).not.toHaveBeenCalled();
    expect(props.setRole).not.toHaveBeenCalled();
  });
});
