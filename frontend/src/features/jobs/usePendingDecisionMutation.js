import { useCallback } from 'react';

import { asProcessingDecision } from '../../shared/decisions/approvalReceipt';
import {
  APPROVAL_OUTCOME_UNKNOWN_MESSAGE,
  isApprovalOutcomeUnknownError,
  mutateDecisionWithReconciliation,
} from '../../shared/decisions/approvalReconciliation';
import { getErrorMessage } from '../candidates/candidatesUiUtils';

/**
 * Runs a Job pipeline decision mutation through the same fail-closed receipt
 * and reconciliation path. Keeping this outside the page prevents approve and
 * override from drifting while leaving the page focused on orchestration.
 */
export const usePendingDecisionMutation = ({
  agentApi,
  currentRoleIdRef,
  decisionFetchSequenceRef,
  fetchPendingDecisions,
  freezePendingDecision,
  numericRoleId,
  setDecisionResolving,
  showToast,
}) => useCallback(async (
  decision,
  mutate,
  { successMessage, successTone = 'success', failureMessage },
) => {
  const decisionId = decision?.id;
  const actionRoleId = numericRoleId;
  if (!decisionId || !setDecisionResolving(decisionId, true)) return;
  decisionFetchSequenceRef.current += 1;
  try {
    const { receipt, matchedDecision } = await mutateDecisionWithReconciliation(
      agentApi,
      decision,
      mutate,
    );
    if (!freezePendingDecision(
      decision,
      asProcessingDecision(decision, matchedDecision || receipt?.data),
      actionRoleId,
    )) return;
    showToast(successMessage, successTone);
    await fetchPendingDecisions();
  } catch (error) {
    if (isApprovalOutcomeUnknownError(error)) {
      // Store the receipt even if the recruiter navigated away. Returning to
      // the role must not expose a retry while the first outcome is unknown.
      freezePendingDecision(
        decision,
        asProcessingDecision(decision, error.observedDecision),
        actionRoleId,
      );
      if (currentRoleIdRef.current === actionRoleId) {
        showToast(APPROVAL_OUTCOME_UNKNOWN_MESSAGE, 'error');
      }
    } else if (currentRoleIdRef.current === actionRoleId) {
      showToast(getErrorMessage(error, failureMessage), 'error');
    }
  } finally {
    // Decision IDs are global. Keep the lock across A → B → A route hops, then
    // release that exact ID wherever the request eventually settles.
    setDecisionResolving(decisionId, false);
  }
}, [
  agentApi,
  currentRoleIdRef,
  decisionFetchSequenceRef,
  fetchPendingDecisions,
  freezePendingDecision,
  numericRoleId,
  setDecisionResolving,
  showToast,
]);

export default usePendingDecisionMutation;
