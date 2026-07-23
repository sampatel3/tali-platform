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

export const roleActionTargetCopy = (role, roleFamily) => {
  const references = roleFamilyReferences({ ...role, role_family: roleFamily });
  const current = references.find((reference) => (
    role?.id != null && String(reference?.id) === String(role.id)
  ));
  return roleReferenceLabel(current) || roleReferenceLabel(role) || 'this role';
};

export const roleHasRelatedAtsLink = (role, roleFamily = role?.role_family) => (
  roleFamilyReferences({ ...role, role_family: roleFamily }).length > 1
  || role?.role_kind === 'sister'
  || Number(role?.sister_role_count || 0) > 0
);

export const decisionQueueKey = (roleId, applicationId) => {
  const application = Number(applicationId);
  if (!Number.isFinite(application)) return null;
  const role = Number(roleId);
  return `${Number.isFinite(role) ? role : 'unknown'}:${application}`;
};

const queueKeyForDecision = (decision, fallbackRoleId = null) => (
  decisionQueueKey(decision?.role_id ?? fallbackRoleId, decision?.application_id)
);

export const indexPendingDecisionsByApplication = (decisions, fallbackRoleId = null) => (
  (Array.isArray(decisions) ? decisions : []).reduce((indexed, decision) => {
    const key = queueKeyForDecision(decision, fallbackRoleId);
    if (!key) return indexed;
    const existing = indexed[key];
    // Only decisions for the same logical role/application compete for this
    // slot. Related roles can point at the same ATS evidence application while
    // retaining independent queues and lifecycle state.
    if (!existing || (isActionableDecision(existing) && !isActionableDecision(decision))) {
      indexed[key] = decision;
    }
    return indexed;
  }, {})
);

export const withDecisionReceipt = (decisions, overlay, fallbackRoleId = null) => {
  const key = queueKeyForDecision(overlay?.row, fallbackRoleId);
  return key
    ? { ...decisions, [key]: overlay.row }
    : decisions;
};

export const withRecordedDecisionReceipt = (receipts, overlay) => (
  overlay?.row?.id == null ? receipts : { ...receipts, [overlay.row.id]: overlay }
);

export const replaceRoleDecisionReceipts = (receiptsByRole, roleId, receipts) => {
  if (Object.keys(receipts).length > 0) receiptsByRole.set(roleId, receipts);
  else receiptsByRole.delete(roleId);
};

// Receipt reconciliation is shared with Home/Candidate: stale pending polls
// stay frozen, while an explicitly marked worker requeue, terminal row, or
// disappearance releases the local overlay. A different actionable
// sibling is not proof that the accepted row finished: the pending endpoint
// can omit processing rows behind its actionable-row limit.
export const mergeDecisionQueueReceipts = (decisions, receipts, fallbackRoleId = null) => {
  const merged = { ...decisions };
  const retained = {};
  Object.values(receipts || {}).forEach((overlay) => {
    const key = queueKeyForDecision(overlay?.row, fallbackRoleId);
    if (!key) return;
    const canonical = merged[key];
    const actionableSibling = canonical
      && Number(canonical?.id) !== Number(overlay?.row?.id)
      && isActionableDecision(canonical);
    if (actionableSibling) {
      merged[key] = overlay.row;
      retained[overlay.row.id] = overlay;
      return;
    }
    const reconciled = reconcileProcessingDecision(canonical, overlay);
    if (reconciled.overlay) {
      merged[key] = reconciled.decision;
      retained[overlay.row.id] = overlay;
    }
  });
  return { decisions: merged, receipts: retained };
};
