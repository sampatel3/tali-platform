import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

import { DECISION_ACTIONS } from '../../shared/decisions/decisionActions';
import { loadDecisionWorkableStages } from '../../shared/decisions/loadDecisionWorkableStages';
import { getErrorMessage } from '../candidates/candidatesUiUtils';
import {
  decisionAuthorityChangeKind,
  pipelineApprovalRequest,
  pipelineOverrideRequest,
} from './jobPipelineDecisionAuthority';
import { pendingDecisionMapsEqual } from './pendingDecisionCache';
import {
  indexPipelineDecisionSnapshots,
  loadPipelineDecisionSnapshots,
} from './pipelineDecisionSnapshots';
import { decisionRecommendsReject, roleFamilyReferences } from './RoleFamilyHeaderUi';

/** Role-scoped reads, dialogs and mutations for pipeline decision cards. */
export function usePipelineDecisionControls({
  agentApi,
  canControlRoleAgent,
  currentRoleIdRef,
  loadRoleWorkspaceRef,
  numericRoleId,
  organizationsApi,
  role,
  roleRenderGenerationRef,
  setRoleApplications,
  showToast,
}) {
  const [pendingAgentDecisions, setPendingAgentDecisions] = useState({});
  const [resolvingDecisionId, setResolvingDecisionId] = useState(null);
  const [decisionApprovalToConfirm, setDecisionApprovalToConfirm] = useState(null);
  const [decisionAdvanceToConfirm, setDecisionAdvanceToConfirm] = useState(null);
  const pendingDecisionFetchRef = useRef(null);

  const fetchPendingDecisions = useCallback(async ({ force = false } = {}) => {
    if (!Number.isFinite(numericRoleId) || currentRoleIdRef.current !== numericRoleId
      || (!force && pendingDecisionFetchRef.current?.roleId === numericRoleId)) return;
    const requestRoleId = numericRoleId;
    const requestGeneration = roleRenderGenerationRef.current.generation;
    const requestKey = Symbol('pending-decisions');
    pendingDecisionFetchRef.current = { roleId: requestRoleId, requestGeneration, requestKey };
    try {
      const res = await loadPipelineDecisionSnapshots(agentApi, requestRoleId);
      const list = Array.isArray(res?.data) ? res.data : [];
      const next = indexPipelineDecisionSnapshots(list);
      if (
        currentRoleIdRef.current !== requestRoleId
        || roleRenderGenerationRef.current.generation !== requestGeneration
        || pendingDecisionFetchRef.current?.requestKey !== requestKey
      ) return;
      setPendingAgentDecisions((previous) => (
        pendingDecisionMapsEqual(previous, next) ? previous : next
      ));
    } catch {
      // Keep the last authoritative snapshot until the next successful poll.
    } finally {
      if (pendingDecisionFetchRef.current?.requestKey === requestKey) {
        pendingDecisionFetchRef.current = null;
      }
    }
  }, [agentApi, currentRoleIdRef, numericRoleId, roleRenderGenerationRef]);

  useLayoutEffect(() => {
    pendingDecisionFetchRef.current = null;
    setPendingAgentDecisions({});
    setResolvingDecisionId(null);
    setDecisionApprovalToConfirm(null);
    setDecisionAdvanceToConfirm(null);
    return () => { pendingDecisionFetchRef.current = null; };
  }, [numericRoleId]);

  useEffect(() => {
    void fetchPendingDecisions();
    const handle = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void fetchPendingDecisions();
    }, 30_000);
    return () => window.clearInterval(handle);
  }, [fetchPendingDecisions]);

  const handleApproveDecision = useCallback(async (decisionOrId, { confirmed = false } = {}) => {
    const decision = typeof decisionOrId === 'object' ? decisionOrId : null;
    const decisionId = decision?.id || decisionOrId;
    if (!decisionId || !canControlRoleAgent) return;
    const requestRoleId = numericRoleId;
    if (!confirmed && decision?.decision_type === 'advance_to_interview' && decision?.workable_job_id) {
      setResolvingDecisionId(decisionId);
      try {
        const alternative = DECISION_ACTIONS.advance_to_interview.primary;
        const workableStages = await loadDecisionWorkableStages(
          organizationsApi,
          decision,
          alternative,
        );
        if (currentRoleIdRef.current !== requestRoleId) return;
        setDecisionAdvanceToConfirm({ decision, alternative, workableStages, requestRoleId });
      } catch (error) {
        if (currentRoleIdRef.current !== requestRoleId) return;
        showToast(getErrorMessage(error, "Couldn't load Workable stages. Try again."), 'error');
      } finally {
        if (currentRoleIdRef.current === requestRoleId) setResolvingDecisionId(null);
      }
      return;
    }

    const decisionFamily = decision?.role_family || role?.role_family;
    const linkedReferences = roleFamilyReferences({
      ...role,
      role_family: decisionFamily || role?.role_family,
    });
    const sharedPool = linkedReferences.length > 1
      || role?.role_kind === 'sister'
      || Number(role?.sister_role_count || 0) > 0;
    if (!confirmed && decisionRecommendsReject(decision) && sharedPool) {
      setDecisionApprovalToConfirm({
        ...(decision || {}),
        id: decisionId,
        role_family: decisionFamily,
      });
      return;
    }

    setResolvingDecisionId(decisionId);
    try {
      await agentApi.approveDecision(
        decisionId,
        pipelineApprovalRequest(decision, decisionFamily),
      );
      if (currentRoleIdRef.current !== requestRoleId) return;
      showToast('Recommendation approved.', 'success');
      setRoleApplications((applications) => applications.map((application) => (
        application?.pending_decision?.id === decisionId
          ? { ...application, pending_decision: null }
          : application
      )));
      await fetchPendingDecisions({ force: true });
    } catch (error) {
      if (currentRoleIdRef.current !== requestRoleId) return;
      const authorityChange = decisionAuthorityChangeKind(error);
      if (authorityChange) {
        showToast(authorityChange === 'family'
          ? 'Linked roles changed before approval. The latest role family and recommendation are being reloaded; review them before trying again.'
          : 'The recommendation changed before approval. The latest decision is being reloaded; review it before trying again.', 'warning');
        void fetchPendingDecisions({ force: true });
        void loadRoleWorkspaceRef.current?.();
      } else {
        showToast(getErrorMessage(error, 'Failed to approve recommendation.'), 'error');
      }
    } finally {
      if (currentRoleIdRef.current === requestRoleId) setResolvingDecisionId(null);
    }
  }, [agentApi, canControlRoleAgent, currentRoleIdRef, fetchPendingDecisions,
    loadRoleWorkspaceRef, numericRoleId, organizationsApi, role, setRoleApplications, showToast]);

  const handleOverrideDecision = useCallback(async (decision) => {
    const decisionId = decision?.id;
    if (!decisionId || !canControlRoleAgent) return;
    const requestRoleId = numericRoleId;
    setResolvingDecisionId(decisionId);
    try {
      await agentApi.overrideDecision(decisionId, pipelineOverrideRequest(decision));
      if (currentRoleIdRef.current !== requestRoleId) return;
      showToast(
        'Recommendation overridden — the candidate stays in your queue for manual review.',
        'info',
      );
      setRoleApplications((applications) => applications.map((application) => (
        application?.pending_decision?.id === decisionId
          ? { ...application, pending_decision: null }
          : application
      )));
      await fetchPendingDecisions({ force: true });
    } catch (error) {
      if (currentRoleIdRef.current !== requestRoleId) return;
      if (decisionAuthorityChangeKind(error)) {
        showToast(
          'The recommendation changed before override. The latest decision is being reloaded; review it before trying again.',
          'warning',
        );
        void fetchPendingDecisions({ force: true });
        void loadRoleWorkspaceRef.current?.();
      } else {
        showToast(getErrorMessage(error, 'Failed to override recommendation.'), 'error');
      }
    } finally {
      if (currentRoleIdRef.current === requestRoleId) setResolvingDecisionId(null);
    }
  }, [agentApi, canControlRoleAgent, currentRoleIdRef, fetchPendingDecisions,
    loadRoleWorkspaceRef, numericRoleId, setRoleApplications, showToast]);

  return {
    decisionAdvanceToConfirm,
    decisionApprovalToConfirm,
    fetchPendingDecisions,
    handleApproveDecision,
    handleOverrideDecision,
    pendingAgentDecisions,
    resolvingDecisionId,
    setDecisionAdvanceToConfirm,
    setDecisionApprovalToConfirm,
  };
}

export default usePipelineDecisionControls;
