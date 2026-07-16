import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { getErrorMessage } from '../candidates/candidatesUiUtils';
import {
  activationAutonomyPayload,
  hasActiveAssessmentTask,
} from './jobPipelineUtils';
import { roleVersionConflict, versionedRolePayload } from './roleConcurrency';
import { mergeRoleShell } from './roleShellMerge';

const TERMINAL_ACTIVATION_STATUSES = new Set(['blocked', 'cancelled', 'succeeded']);

export const blockedRoleReconfiguration = (role) => (
  String(role?.assessment_task_provisioning?.reconfiguration?.status || '').toLowerCase() === 'blocked'
);

export const activationTerminalFromRole = (role) => {
  const intent = role?.assessment_task_provisioning?.activation_intent || {};
  const status = String(intent.status || '').toLowerCase();
  if (role?.agentic_mode_enabled || status === 'succeeded') {
    return { status: 'succeeded', message: 'The Agent is on and the assessment policy is ready.' };
  }
  if (status === 'blocked') {
    return {
      status,
      message: intent.last_error || 'Turn-on needs input before assessment preparation can continue.',
    };
  }
  if (status === 'cancelled') {
    return {
      status,
      message: intent.cancel_reason || 'The saved Turn-on request was cancelled.',
    };
  }
  return null;
};

export function useRoleActivationFlow({
  canControlRoleAgent,
  handleRoleVersionConflict,
  numericRoleId,
  onTasksLoaded,
  refreshRoleAndTasks,
  refetchAgentStatus,
  role,
  roleTasks,
  roleTasksFetchKnown,
  rolesApi,
  setRole,
  showToast,
}) {
  const [activationPreflight, setActivationPreflight] = useState(null);
  const [activationReview, setActivationReview] = useState(null);
  const activeRoleIdRef = useRef(numericRoleId);
  const activationReviewOpen = Boolean(activationReview);
  const persistedActivationStatus = String(
    role?.assessment_task_provisioning?.activation_intent?.status || '',
  ).toLowerCase();
  const persistedActivationTaskId = role
    ?.assessment_task_provisioning?.activation_intent?.task_id ?? null;
  const persistedActivationPending = Number(role?.id) === numericRoleId
    && !role?.agentic_mode_enabled
    && ['pending', 'retry_wait'].includes(persistedActivationStatus);
  // A durable request outlives its dialog. Keep reconciling both a request made
  // in this render and one restored from the role record after navigation or a
  // page reload. A currently-submitting replacement request is the one case
  // where the older persisted intent must not race the authoritative PATCH.
  const activationPolling = Boolean(
    !activationReview?.activationSubmitting
    && (
      persistedActivationPending
      || (
        activationReview?.activationRequested
        && !activationReview?.terminalStatus
      )
    )
  );
  const ordinaryActivationAllowed = useMemo(() => (
    role?.role_kind === 'sister'
    || blockedRoleReconfiguration(role)
    || Boolean(role?.auto_skip_assessment)
    || (roleTasksFetchKnown && hasActiveAssessmentTask(roleTasks))
  ), [role, roleTasks, roleTasksFetchKnown]);

  useEffect(() => {
    activeRoleIdRef.current = numericRoleId;
    return () => {
      if (activeRoleIdRef.current === numericRoleId) activeRoleIdRef.current = null;
    };
  }, [numericRoleId]);

  useEffect(() => {
    if (
      !activationPolling
      || !Number.isFinite(numericRoleId)
    ) return undefined;
    let cancelled = false;
    let requestInFlight = false;
    let terminalReached = false;
    const readsRoleShell = typeof rolesApi.getShell === 'function';
    const refreshGeneratedAssessment = async () => {
      if (
        requestInFlight
        || terminalReached
        || (typeof document !== 'undefined' && document.visibilityState === 'hidden')
      ) return;
      requestInFlight = true;
      try {
        const [initialTasksResult, roleResult] = await Promise.allSettled([
          activationReviewOpen
            ? rolesApi.listTasks(numericRoleId)
            : Promise.resolve(null),
          readsRoleShell
            ? rolesApi.getShell(numericRoleId)
            : rolesApi.get(numericRoleId),
        ]);
        if (cancelled) return;
        const nextRole = roleResult.status === 'fulfilled' && roleResult.value?.data
          ? roleResult.value.data
          : null;
        const terminal = activationTerminalFromRole(nextRole);
        if (roleResult.status === 'fulfilled' && roleResult.value?.data) {
          setRole((current) => (
            readsRoleShell ? mergeRoleShell(current, nextRole) : nextRole
          ));
          if (terminal && TERMINAL_ACTIVATION_STATUSES.has(terminal.status)) {
            terminalReached = true;
            setActivationReview((current) => (current ? {
              ...current,
              activationSubmitting: false,
              activationRequested: terminal.status === 'succeeded',
              activationError: terminal.status === 'blocked' ? terminal.message : null,
              terminalStatus: terminal.status,
              terminalMessage: terminal.message,
            } : current));
            void refetchAgentStatus?.();
          }
        }
        if (activationReviewOpen && initialTasksResult.status === 'fulfilled') {
          const nextTasks = Array.isArray(initialTasksResult.value?.data)
            ? initialTasksResult.value.data
            : [];
          onTasksLoaded(nextTasks);
          const intentTaskId = Number(nextRole
            ? nextRole?.assessment_task_provisioning?.activation_intent?.task_id
            : persistedActivationTaskId);
          // A role may retain several generated tasks for audit/history. Only
          // the task named by this durable intent belongs in its review dialog.
          const generatedTask = Number.isFinite(intentTaskId) && intentTaskId > 0
            ? nextTasks.find((task) => (
                task?.generated && Number(task?.id) === intentTaskId
              )) || null
            : null;
          setActivationReview((current) => (
            current ? { ...current, draft: generatedTask } : current
          ));
        } else if (!activationReviewOpen && terminal?.status === 'succeeded') {
          // Terminal role truth must not wait behind the independently useful
          // task reconciliation. Keep this one-shot request alive across the
          // polling effect's terminal cleanup, but discard it after navigation
          // or unmount so it cannot write into another role.
          void rolesApi.listTasks(numericRoleId).then((response) => {
            if (activeRoleIdRef.current !== numericRoleId) return;
            onTasksLoaded(Array.isArray(response?.data) ? response.data : []);
          }).catch(() => {});
        }
      } finally {
        requestInFlight = false;
      }
    };
    void refreshGeneratedAssessment();
    const timer = window.setInterval(refreshGeneratedAssessment, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activationPolling, activationReviewOpen, numericRoleId, onTasksLoaded, persistedActivationTaskId,
    refetchAgentStatus, rolesApi, setRole]);

  const activateAgentWithAssessmentChoice = useCallback((monthlyBudgetCents, assessmentAction = null) => {
    if (!canControlRoleAgent || !Number.isFinite(numericRoleId)) return;
    const assessmentFields = assessmentAction === 'skip_assessment'
      ? { activation_assessment_action: assessmentAction, auto_skip_assessment: true }
      : assessmentAction
        ? { activation_assessment_action: assessmentAction }
        : {};
    setActivationReview(null);
    rolesApi.update(numericRoleId, versionedRolePayload(role, {
      agentic_mode_enabled: true,
      monthly_usd_budget_cents: monthlyBudgetCents,
      ...activationAutonomyPayload(role),
      ...assessmentFields,
    }))
      .then((response) => {
        if (response?.data) setRole(response.data);
        void refetchAgentStatus?.();
        if (!response?.data) void refreshRoleAndTasks?.();
      })
      .catch((error) => {
        void refetchAgentStatus?.();
        void refreshRoleAndTasks?.();
        if (!handleRoleVersionConflict(error)) {
          showToast(getErrorMessage(error, 'Failed to turn on agent mode.'), 'error');
        }
      });
  }, [canControlRoleAgent, handleRoleVersionConflict, numericRoleId, refreshRoleAndTasks,
    refetchAgentStatus, role, rolesApi, setRole, showToast]);

  const requestAgentActivationWhenReady = useCallback((monthlyBudgetCents, draft = null) => {
    if (!canControlRoleAgent || !Number.isFinite(numericRoleId)) return;
    setActivationReview({ monthlyBudgetCents, draft, activationSubmitting: true,
      activationRequested: false, activationError: null, terminalStatus: null, terminalMessage: null });
    rolesApi.update(numericRoleId, versionedRolePayload(role, {
      agentic_mode_enabled: true,
      monthly_usd_budget_cents: monthlyBudgetCents,
      ...activationAutonomyPayload(role),
      activation_assessment_action: 'approve_when_ready',
    }))
      .then((response) => {
        const nextRole = response?.data || null;
        if (nextRole) setRole(nextRole);
        const terminal = activationTerminalFromRole(nextRole);
        setActivationReview((current) => (current ? {
          ...current,
          activationSubmitting: false,
          activationRequested: terminal ? terminal.status === 'succeeded' : true,
          activationError: terminal?.status === 'blocked' ? terminal.message : null,
          terminalStatus: terminal?.status || null,
          terminalMessage: terminal?.message || null,
        } : current));
        void refetchAgentStatus?.();
      })
      .catch((error) => {
        const conflict = roleVersionConflict(error);
        const detail = conflict?.message || getErrorMessage(error, 'Failed to queue agent activation.');
        setActivationReview((current) => (current ? {
          ...current, activationSubmitting: false, activationRequested: false, activationError: detail,
        } : current));
        if (!handleRoleVersionConflict(error)) showToast(detail, 'error');
      });
  }, [canControlRoleAgent, handleRoleVersionConflict, numericRoleId,
    refetchAgentStatus, role, rolesApi, setRole, showToast]);

  const handleActivateAgent = useCallback((monthlyBudgetCents) => {
    if (!canControlRoleAgent) return;
    if (!Number.isFinite(monthlyBudgetCents) || monthlyBudgetCents <= 0) {
      showToast('Set a monthly cap greater than $0 before activating.', 'error');
      return;
    }
    setActivationPreflight({ monthlyBudgetCents });
  }, [canControlRoleAgent, showToast]);

  const confirmAgentActivation = useCallback((assessmentAction = null) => {
    if (!canControlRoleAgent) return;
    const monthlyBudgetCents = Number(activationPreflight?.monthlyBudgetCents);
    if (!Number.isFinite(monthlyBudgetCents) || monthlyBudgetCents <= 0) {
      setActivationPreflight(null);
      showToast('Set a monthly cap greater than $0 before activating.', 'error');
      return;
    }
    if (!assessmentAction && !ordinaryActivationAllowed) {
      showToast('Choose Generate assessment or Skip assessment before turning on.', 'error');
      return;
    }
    setActivationPreflight(null);
    if (role?.role_kind === 'sister') {
      activateAgentWithAssessmentChoice(monthlyBudgetCents, 'skip_assessment');
    } else if (assessmentAction === 'approve_when_ready') {
      requestAgentActivationWhenReady(monthlyBudgetCents);
    } else if (assessmentAction === 'skip_assessment') {
      activateAgentWithAssessmentChoice(monthlyBudgetCents, 'skip_assessment');
    } else {
      activateAgentWithAssessmentChoice(monthlyBudgetCents, null);
    }
  }, [activateAgentWithAssessmentChoice, activationPreflight, canControlRoleAgent,
    ordinaryActivationAllowed, requestAgentActivationWhenReady, role?.role_kind, showToast]);

  return {
    activationPreflight,
    activationReview,
    ordinaryActivationAllowed,
    setActivationPreflight,
    setActivationReview,
    activateAgentWithAssessmentChoice,
    requestAgentActivationWhenReady,
    handleActivateAgent,
    confirmAgentActivation,
  };
}

export default useRoleActivationFlow;
