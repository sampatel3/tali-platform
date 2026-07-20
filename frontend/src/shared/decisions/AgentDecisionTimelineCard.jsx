import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { agent as agentApi, organizations as orgsApi } from '../api';
import { useToast } from '../../context/ToastContext';
import { OverrideModal } from '../../features/home/OverrideModal';
import { TeachModal } from '../../features/home/TeachModal';
import { AgentDecisionCard } from './AgentDecisionCard';
import { DECISION_ACTIONS } from './decisionActions';
import { isApprovalBlockingStale, isEngineOnlyStale } from './decisionStaleness';
import {
  asProcessingDecision,
  createApprovalReceiptOverlay,
  reconcileProcessingDecision,
} from './approvalReceipt';
import {
  APPROVAL_OUTCOME_UNKNOWN_MESSAGE,
  approveDecisionWithReconciliation,
  isApprovalOutcomeUnknownError,
} from './approvalReconciliation';
import './agentDecisionTimelineCard.css';

const isActionable = (decision) =>
  decision?.status === 'pending' || decision?.status === 'reverted_for_feedback';

const apiErrorMessage = (error, fallback) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    return detail.message || detail.detail || detail.code || fallback;
  }
  return error?.message || fallback;
};

const isDecisionStaleError = (error) => {
  const detail = error?.response?.data?.detail;
  const code = detail && typeof detail === 'object' ? detail.code : detail;
  return error?.response?.status === 409 && code === 'decision_stale';
};

export function normalizeTimelineDecision(timelineItem, detail, roleId, roleName) {
  const source = detail || timelineItem || {};
  const id = Number(detail?.id ?? timelineItem?.decision_id ?? timelineItem?.id);
  const decisionType = source.decision_type || timelineItem?.decision_type;
  return {
    ...(timelineItem || {}),
    ...(detail || {}),
    id,
    role_id: detail?.role_id ?? timelineItem?.role_id ?? roleId,
    role_name: detail?.role_name ?? timelineItem?.role_name ?? roleName,
    application_id: detail?.application_id ?? timelineItem?.application_id,
    decision_type: decisionType,
    // Pre-screen rejects deliberately have no score. The lightweight timeline
    // carries pre_screen_score_100, so never let that masquerade as a Tali score
    // while the canonical detail request is still loading.
    taali_score: detail
      ? detail.taali_score
      : decisionType === 'skip_assessment_reject'
        ? null
        : (timelineItem?.taali_score ?? timelineItem?.score ?? null),
    evidence: detail?.evidence ?? timelineItem?.evidence ?? {},
  };
}

// Shared action shell for a decision embedded in a chronological agent-chat
// thread. It deliberately delegates the visual vocabulary to AgentDecisionCard,
// the action vocabulary to DECISION_ACTIONS, and writes to the canonical agent
// API/OverrideModal/TeachModal used by the Home review queue.
export function AgentDecisionTimelineCard({
  item,
  detail,
  roleId,
  roleName,
  detailsLoading = false,
  detailsError = false,
  onRetryDetails,
  onChanged,
}) {
  const { showToast } = useToast() || { showToast: () => {} };
  const [busy, setBusy] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [teachFor, setTeachFor] = useState(null);
  const [alternativeFor, setAlternativeFor] = useState(null);
  const [workableStages, setWorkableStages] = useState([]);
  const [approvalReceipt, setApprovalReceipt] = useState(null);

  const canonicalDecision = useMemo(
    () => normalizeTimelineDecision(item, detail, roleId, roleName),
    [detail, item, roleId, roleName],
  );
  const receiptState = useMemo(
    () => reconcileProcessingDecision(canonicalDecision, approvalReceipt),
    [approvalReceipt, canonicalDecision],
  );
  const decision = receiptState.decision;
  useEffect(() => {
    if (approvalReceipt && !receiptState.overlay) setApprovalReceipt(null);
  }, [approvalReceipt, receiptState.overlay]);
  const actionable = isActionable(decision);
  // Resolved history is safe to render from the lightweight timeline. Pending
  // writes stay frozen unless the live canonical detail row is available.
  const detailsUnavailable = actionable && (!detail || detailsLoading || detailsError);

  const reconcile = useCallback(async () => {
    await Promise.resolve(onChanged?.());
  }, [onChanged]);

  const openAlternative = useCallback(async (target, alternative) => {
    if (!alternative) return;
    setBusy(true);
    try {
      let stages = [];
      if (alternative.requireStagePick && target.workable_job_id) {
        const response = await orgsApi.getWorkableStages({ shortcode: target.workable_job_id });
        stages = Array.isArray(response?.data?.stages) ? response.data.stages : [];
      }
      setWorkableStages(stages);
      setAlternativeFor({ decision: target, alternative });
    } catch (error) {
      // A failed stage lookup must not silently degrade into an internal-only
      // advance. Keep the modal closed and ask the recruiter to retry.
      showToast?.(apiErrorMessage(error, "Couldn’t load Workable stages. Try again."), 'error');
    } finally {
      setBusy(false);
    }
  }, [showToast]);

  const approve = useCallback(async (target) => {
    if (isApprovalBlockingStale(target)) {
      showToast?.("This decision’s inputs changed — re-evaluate before approving.", 'warning');
      return;
    }
    const primary = DECISION_ACTIONS[target.decision_type]?.primary;
    if (primary) {
      await openAlternative(target, primary);
      return;
    }
    setBusy(true);
    try {
      const { receipt, matchedDecision } = await approveDecisionWithReconciliation(
        agentApi,
        target,
        {},
        { force: isEngineOnlyStale(target) },
      );
      setApprovalReceipt(createApprovalReceiptOverlay(
        target,
        asProcessingDecision(target, matchedDecision || receipt?.data),
      ));
      showToast?.(
        target.decision_type === 'send_assessment' ? 'Sending assessment…'
          : target.decision_type === 'resend_assessment_invite' ? 'Resending invite…'
            : target.decision_type === 'reject' || target.decision_type === 'skip_assessment_reject' ? 'Rejecting…'
              : 'Approved.',
        'success',
      );
      await reconcile();
    } catch (error) {
      if (isApprovalOutcomeUnknownError(error)) {
        setApprovalReceipt(createApprovalReceiptOverlay(
          target,
          asProcessingDecision(target, {
            ...(error.observedDecision || {}),
            outcome_unknown: true,
          }),
        ));
        showToast?.(APPROVAL_OUTCOME_UNKNOWN_MESSAGE, 'warning');
        await reconcile();
        return;
      }
      showToast?.(
        isDecisionStaleError(error)
          ? "This decision’s inputs changed — re-evaluate to refresh it."
          : apiErrorMessage(error, "Couldn’t complete that decision."),
        isDecisionStaleError(error) ? 'warning' : 'error',
      );
      await reconcile();
    } finally {
      setBusy(false);
    }
  }, [openAlternative, reconcile, showToast]);

  const reEvaluate = useCallback(async (target) => {
    setBusy(true);
    try {
      await agentApi.reEvaluateDecision(target.id);
      showToast?.('Re-evaluating with fresh inputs…', 'success');
      await reconcile();
    } catch (error) {
      showToast?.(apiErrorMessage(error, 'Re-evaluate failed'), 'error');
    } finally {
      setBusy(false);
    }
  }, [reconcile, showToast]);

  const snooze = useCallback(async (target) => {
    setBusy(true);
    try {
      await agentApi.snoozeDecision(target.id);
      // The merged timeline is an audit projection and currently includes
      // snoozed pending rows. Hide it locally after the canonical write so the
      // action has the same immediate behaviour as the Home decision queue.
      setHidden(true);
      showToast?.('Snoozed for 1h.', 'success');
      await reconcile();
    } catch (error) {
      showToast?.(apiErrorMessage(error, 'Snooze failed'), 'error');
    } finally {
      setBusy(false);
    }
  }, [reconcile, showToast]);

  if (hidden) return null;

  return (
    <div className="agent-decision-timeline-card" data-decision-id={decision.id}>
      {detailsLoading && actionable ? (
        <div className="agent-decision-timeline-status" role="status">
          Refreshing live decision details…
        </div>
      ) : null}
      {(detailsError || (actionable && !detailsLoading && !detail)) ? (
        <div className="agent-decision-timeline-status is-error" role="alert">
          <span>Live decision details couldn’t be loaded. Actions are paused.</span>
          {onRetryDetails ? (
            <button type="button" className="taali-text-btn" onClick={onRetryDetails}>
              Try again
            </button>
          ) : null}
        </div>
      ) : null}
      <AgentDecisionCard
        decision={decision}
        busy={busy || detailsUnavailable}
        onApprove={approve}
        onAlternative={openAlternative}
        onReEvaluate={reEvaluate}
        onSnooze={snooze}
        onTeach={(target) => setTeachFor(target)}
      />

      {teachFor ? (
        <TeachModal
          decision={teachFor}
          onClose={() => setTeachFor(null)}
          onSubmitted={async () => {
            showToast?.('Feedback recorded. Decision returned to the queue.', 'success');
            await reconcile();
          }}
        />
      ) : null}

      {alternativeFor ? (
        <OverrideModal
          decision={alternativeFor.decision}
          alternative={alternativeFor.alternative}
          workableStages={workableStages}
          onClose={() => setAlternativeFor(null)}
          onSubmitted={async () => {
            showToast?.(
              `${alternativeFor.alternative.confirmLabel || 'Override'} dispatched.`,
              'success',
            );
            await reconcile();
          }}
        />
      ) : null}
    </div>
  );
}

export default AgentDecisionTimelineCard;
