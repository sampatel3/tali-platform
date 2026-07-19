import { isRejectDecisionType } from '../../shared/decisions/decisionActions';
import { reconcileProcessingDecision } from '../../shared/decisions/approvalReceipt';
import { roleFamilyReferences, roleReferenceLabel } from './RoleFamilyHeaderUi';

export const isActionableDecision = (decision) => (
  !decision?.status
  || decision.status === 'pending'
  || decision.status === 'reverted_for_feedback'
);

export const decisionRecommendsReject = (decision) => (
  isRejectDecisionType(decision?.decision_type)
  || String(decision?.recommendation || decision?.action || '')
    .trim().toLowerCase().includes('reject')
);

export const decisionRecommendsAdvance = (decision) => (
  String(decision?.decision_type || '').trim().toLowerCase() === 'advance_to_interview'
  || String(decision?.recommendation || decision?.action || '')
    .trim().toLowerCase().includes('advance')
);

export const linkedRoleTargetCopy = (role, roleFamily) => {
  const references = roleFamilyReferences({ ...role, role_family: roleFamily });
  const labels = references.map(roleReferenceLabel).filter(Boolean);
  return labels.length > 1
    ? labels.join(', ')
    : 'the original and every related role in this shared candidate pool';
};

export const roleSharesCandidatePool = (role, roleFamily = role?.role_family) => (
  roleFamilyReferences({ ...role, role_family: roleFamily }).length > 1
  || role?.role_kind === 'sister'
  || Number(role?.sister_role_count || 0) > 0
);

export const indexPendingDecisionsByApplication = (decisions) => (
  (Array.isArray(decisions) ? decisions : []).reduce((indexed, decision) => {
    const applicationId = Number(decision?.application_id);
    if (!Number.isFinite(applicationId)) return indexed;
    const existing = indexed[applicationId];
    // Multiple related-role decisions can share one application. If any of
    // them is already processing, fail closed for the whole candidate: exposing
    // a sibling pending action could race a still-running ATS side effect.
    if (!existing || (isActionableDecision(existing) && !isActionableDecision(decision))) {
      indexed[applicationId] = decision;
    }
    return indexed;
  }, {})
);

export const pendingDecisionMapsEqual = (previous, next) => {
  const previousKeys = Object.keys(previous);
  const nextKeys = Object.keys(next);
  return previousKeys.length === nextKeys.length
    && nextKeys.every((key) => (
      previous[key]?.id === next[key]?.id
      && previous[key]?.status === next[key]?.status
    ));
};

export const withDecisionReceipt = (decisions, overlay) => {
  const applicationId = Number(overlay?.row?.application_id);
  return Number.isFinite(applicationId)
    ? { ...decisions, [applicationId]: overlay.row }
    : decisions;
};

export const withRecordedDecisionReceipt = (receipts, overlay) => (
  overlay?.row?.id == null ? receipts : { ...receipts, [overlay.row.id]: overlay }
);

// Receipt reconciliation is shared with Home/Candidate: stale pending polls
// stay frozen, while an explicitly marked worker requeue, processing/terminal
// row, or disappearance releases the local overlay. A different actionable
// sibling is not proof that the accepted row finished: the pending endpoint
// can omit processing rows behind its actionable-row limit.
export const mergeDecisionQueueReceipts = (decisions, receipts) => {
  const merged = { ...decisions };
  const retained = {};
  Object.values(receipts || {}).forEach((overlay) => {
    const applicationId = Number(overlay?.row?.application_id);
    const canonical = merged[applicationId];
    const actionableSibling = canonical
      && Number(canonical?.id) !== Number(overlay?.row?.id)
      && isActionableDecision(canonical);
    if (actionableSibling) {
      merged[applicationId] = overlay.row;
      retained[overlay.row.id] = overlay;
      return;
    }
    const reconciled = reconcileProcessingDecision(canonical, overlay);
    if (reconciled.overlay) {
      merged[applicationId] = reconciled.decision;
      retained[overlay.row.id] = overlay;
    }
  });
  return { decisions: merged, receipts: retained };
};
