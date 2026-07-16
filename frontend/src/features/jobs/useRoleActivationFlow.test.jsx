import { useState } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useRoleActivationFlow } from './useRoleActivationFlow';

const baseRole = {
  id: 101,
  version: 7,
  agentic_mode_enabled: false,
  auto_promote: false,
  auto_send_assessment: null,
  auto_resend_assessment: null,
  auto_advance: null,
};

const makeProps = ({ updateRole, polledRole } = {}) => ({
  canControlRoleAgent: true,
  handleRoleVersionConflict: vi.fn(() => false),
  refreshRoleAndTasks: vi.fn(),
  numericRoleId: 101,
  onTasksLoaded: vi.fn(),
  refetchAgentStatus: vi.fn(),
  role: baseRole,
  roleTasks: [],
  roleTasksFetchKnown: true,
  rolesApi: {
    update: vi.fn().mockResolvedValue({
      data: updateRole || {
        ...baseRole,
        assessment_task_provisioning: {
          activation_intent: { status: 'pending' },
        },
      },
    }),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn().mockResolvedValue({ data: polledRole || baseRole }),
  },
  setRole: vi.fn(),
  showToast: vi.fn(),
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useRoleActivationFlow durable polling', () => {
  it.each([
    {
      name: 'succeeded',
      polledRole: {
        ...baseRole,
        agentic_mode_enabled: true,
        assessment_task_provisioning: {
          activation_intent: { status: 'succeeded' },
        },
      },
      expectedMessage: /assessment policy is ready/i,
    },
    {
      name: 'blocked',
      polledRole: {
        ...baseRole,
        assessment_task_provisioning: {
          activation_intent: {
            status: 'blocked',
            last_error: 'The task needs a clearer acceptance criterion.',
          },
        },
      },
      expectedMessage: /clearer acceptance criterion/i,
    },
    {
      name: 'cancelled',
      polledRole: {
        ...baseRole,
        assessment_task_provisioning: {
          activation_intent: {
            status: 'cancelled',
            cancel_reason: 'Cancelled by the recruiter.',
          },
        },
      },
      expectedMessage: /cancelled by the recruiter/i,
    },
  ])('stops its timer and exposes a $name terminal state', async ({
    name,
    polledRole,
    expectedMessage,
  }) => {
    const setIntervalSpy = vi.spyOn(window, 'setInterval').mockReturnValue(91);
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
    const props = makeProps({ polledRole });
    const { result } = renderHook(() => useRoleActivationFlow(props));

    await act(async () => {
      result.current.requestAgentActivationWhenReady(5000);
    });

    await waitFor(() => {
      expect(result.current.activationReview?.terminalStatus).toBe(name);
    });
    expect(result.current.activationReview?.terminalMessage).toMatch(expectedMessage);
    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 5000);
    expect(clearIntervalSpy).toHaveBeenCalledWith(91);
  });

  it('does not poll before the activation request has been saved', async () => {
    let resolveUpdate;
    const props = makeProps();
    props.rolesApi.update.mockReturnValue(new Promise((resolve) => {
      resolveUpdate = resolve;
    }));
    const { result } = renderHook(() => useRoleActivationFlow(props));

    act(() => {
      result.current.requestAgentActivationWhenReady(5000);
    });

    expect(result.current.activationReview?.activationSubmitting).toBe(true);
    expect(props.rolesApi.get).not.toHaveBeenCalled();
    expect(props.rolesApi.listTasks).not.toHaveBeenCalled();

    await act(async () => {
      resolveUpdate({
        data: {
          ...baseRole,
          assessment_task_provisioning: {
            activation_intent: { status: 'pending' },
          },
        },
      });
    });

    await waitFor(() => expect(props.rolesApi.get).toHaveBeenCalledTimes(1));
  });

  it('reconciles a persisted pending request on page load through the role shell', async () => {
    const setIntervalSpy = vi.spyOn(window, 'setInterval');
    const pendingRole = {
      ...baseRole,
      assessment_task_provisioning: {
        activation_intent: { status: 'retry_wait', task_id: 22 },
      },
    };
    const succeededRole = {
      ...pendingRole,
      agentic_mode_enabled: true,
      assessment_task_provisioning: {
        activation_intent: { status: 'succeeded', task_id: 22 },
      },
    };
    const props = makeProps();
    props.role = pendingRole;
    props.rolesApi.getShell = vi.fn().mockResolvedValue({ data: succeededRole });
    const { result } = renderHook(() => useRoleActivationFlow(props));

    await waitFor(() => expect(props.setRole).toHaveBeenCalled());
    const applyShell = props.setRole.mock.calls.at(-1)[0];
    expect(applyShell(pendingRole)).toEqual(succeededRole);
    expect(props.rolesApi.getShell).toHaveBeenCalledWith(101);
    expect(props.rolesApi.get).not.toHaveBeenCalled();
    expect(props.rolesApi.listTasks).toHaveBeenCalledTimes(1);
    expect(props.rolesApi.listTasks).toHaveBeenCalledWith(101);
    expect(props.onTasksLoaded).toHaveBeenCalledWith([]);
    expect(props.refetchAgentStatus).toHaveBeenCalled();
    expect(result.current.activationReview).toBeNull();

    const scheduledPoll = setIntervalSpy.mock.calls.find(([, delay]) => delay === 5000)?.[0];
    expect(scheduledPoll).toEqual(expect.any(Function));
    await act(async () => scheduledPoll());
    expect(props.rolesApi.getShell).toHaveBeenCalledTimes(1);
    expect(props.rolesApi.listTasks).toHaveBeenCalledTimes(1);
  });

  it('publishes terminal success before a delayed closed-dialog task refresh resolves', async () => {
    let resolveTasks;
    const pendingRole = {
      ...baseRole,
      assessment_task_provisioning: {
        activation_intent: { status: 'pending', task_id: 22 },
      },
    };
    const succeededRole = {
      ...pendingRole,
      agentic_mode_enabled: true,
      assessment_task_provisioning: {
        activation_intent: { status: 'succeeded', task_id: 22 },
      },
    };
    const props = makeProps();
    props.rolesApi.getShell = vi.fn().mockResolvedValue({ data: succeededRole });
    props.rolesApi.listTasks.mockReturnValue(new Promise((resolve) => {
      resolveTasks = resolve;
    }));
    const useStatefulActivationFlow = () => {
      const [currentRole, setCurrentRole] = useState(pendingRole);
      const flow = useRoleActivationFlow({ ...props, role: currentRole, setRole: setCurrentRole });
      return { currentRole, flow };
    };
    const { result } = renderHook(useStatefulActivationFlow);

    await waitFor(() => expect(result.current.currentRole.agentic_mode_enabled).toBe(true));
    expect(result.current.currentRole.assessment_task_provisioning.activation_intent.status)
      .toBe('succeeded');
    expect(props.refetchAgentStatus).toHaveBeenCalledTimes(1);
    expect(props.rolesApi.listTasks).toHaveBeenCalledTimes(1);
    expect(props.onTasksLoaded).not.toHaveBeenCalled();

    await act(async () => {
      resolveTasks({ data: [{ id: 22, name: 'Generated assessment', generated: true }] });
    });
    await waitFor(() => expect(props.onTasksLoaded).toHaveBeenCalledWith([
      { id: 22, name: 'Generated assessment', generated: true },
    ]));
  });

  it.each(['blocked', 'cancelled'])(
    'keeps a closed persisted %s reconciliation shell-only',
    async (terminalStatus) => {
      const pendingRole = {
        ...baseRole,
        assessment_task_provisioning: {
          activation_intent: { status: 'pending', task_id: 22 },
        },
      };
      const terminalRole = {
        ...pendingRole,
        assessment_task_provisioning: {
          activation_intent: { status: terminalStatus, task_id: 22 },
        },
      };
      const props = makeProps();
      props.role = pendingRole;
      props.rolesApi.getShell = vi.fn().mockResolvedValue({ data: terminalRole });

      renderHook(() => useRoleActivationFlow(props));

      await waitFor(() => expect(props.setRole).toHaveBeenCalled());
      expect(props.rolesApi.getShell).toHaveBeenCalledTimes(1);
      expect(props.rolesApi.listTasks).not.toHaveBeenCalled();
      expect(props.onTasksLoaded).not.toHaveBeenCalled();
      expect(props.refetchAgentStatus).toHaveBeenCalledTimes(1);
    },
  );

  it('keeps reconciling a saved request after its review dialog closes', async () => {
    let resolvePoll;
    const pendingRole = {
      ...baseRole,
      assessment_task_provisioning: {
        activation_intent: { status: 'pending', task_id: 22 },
      },
    };
    const succeededRole = {
      ...pendingRole,
      agentic_mode_enabled: true,
      assessment_task_provisioning: {
        activation_intent: { status: 'succeeded', task_id: 22 },
      },
    };
    const props = makeProps({ updateRole: pendingRole });
    props.rolesApi.get.mockReturnValue(new Promise((resolve) => {
      resolvePoll = resolve;
    }));
    const useStatefulActivationFlow = () => {
      const [currentRole, setCurrentRole] = useState(baseRole);
      const flow = useRoleActivationFlow({ ...props, role: currentRole, setRole: setCurrentRole });
      return { currentRole, flow };
    };
    const { result } = renderHook(useStatefulActivationFlow);

    act(() => result.current.flow.requestAgentActivationWhenReady(5000));
    await waitFor(() => expect(result.current.flow.activationReview?.activationRequested).toBe(true));
    expect(result.current.currentRole).toEqual(pendingRole);

    act(() => result.current.flow.setActivationReview(null));
    expect(result.current.flow.activationReview).toBeNull();

    await act(async () => {
      resolvePoll({ data: succeededRole });
    });
    await waitFor(() => expect(result.current.currentRole).toEqual(succeededRole));
    expect(props.refetchAgentStatus).toHaveBeenCalled();
  });

  it('shows only the generated task named by the latest activation intent', async () => {
    const pendingRole = {
      ...baseRole,
      assessment_task_provisioning: {
        activation_intent: { status: 'pending', task_id: 22 },
      },
    };
    const props = makeProps({ updateRole: pendingRole, polledRole: pendingRole });
    props.rolesApi.listTasks.mockResolvedValue({
      data: [
        { id: 11, name: 'Old generated task', generated: true },
        { id: 22, name: 'Current generated task', generated: true },
      ],
    });
    const { result } = renderHook(() => useRoleActivationFlow(props));

    act(() => result.current.requestAgentActivationWhenReady(5000));

    await waitFor(() => expect(result.current.activationReview?.draft?.id).toBe(22));
    expect(result.current.activationReview?.draft?.name).toBe('Current generated task');
  });

  it('clears an older draft when the latest intent has not selected a task', async () => {
    const updateRole = {
      ...baseRole,
      assessment_task_provisioning: {
        activation_intent: { status: 'pending', task_id: 22 },
      },
    };
    const polledRole = {
      ...baseRole,
      assessment_task_provisioning: {
        activation_intent: { status: 'pending', task_id: null },
      },
    };
    const props = makeProps({ updateRole, polledRole });
    props.rolesApi.listTasks.mockResolvedValue({
      data: [{ id: 22, name: 'Older generated task', generated: true }],
    });
    const { result } = renderHook(() => useRoleActivationFlow(props));

    act(() => result.current.requestAgentActivationWhenReady(
      5000,
      { id: 22, name: 'Older generated task', generated: true },
    ));

    await waitFor(() => expect(result.current.activationReview?.activationRequested).toBe(true));
    await waitFor(() => expect(result.current.activationReview?.draft).toBeNull());
  });

  it('uses the persisted intent task when the role poll fails', async () => {
    const pendingRole = {
      ...baseRole,
      assessment_task_provisioning: {
        activation_intent: { status: 'pending', task_id: 22 },
      },
    };
    const props = makeProps({ updateRole: pendingRole });
    props.rolesApi.get.mockRejectedValue(new Error('transient role read failure'));
    props.rolesApi.listTasks.mockResolvedValue({
      data: [
        { id: 11, name: 'Old generated task', generated: true },
        { id: 22, name: 'Current generated task', generated: true },
      ],
    });
    const useStatefulActivationFlow = () => {
      const [currentRole, setCurrentRole] = useState(baseRole);
      const flow = useRoleActivationFlow({ ...props, role: currentRole, setRole: setCurrentRole });
      return flow;
    };
    const { result } = renderHook(useStatefulActivationFlow);

    act(() => result.current.requestAgentActivationWhenReady(5000));

    await waitFor(() => expect(result.current.activationReview?.draft?.id).toBe(22));
  });
});
