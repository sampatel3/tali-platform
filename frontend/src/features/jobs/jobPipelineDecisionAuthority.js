import {
  expectedRoleFamilyForReject,
  isDecisionChangedError,
  isRoleFamilyChangedError,
} from '../../shared/decisions/decisionActions';

const expectedDecisionType = (decision) => {
  const decisionType = String(decision?.decision_type || '').trim();
  return decisionType ? { expected_decision_type: decisionType } : {};
};

export const pipelineApprovalRequest = (decision, roleFamily) => {
  const expectedFamily = expectedRoleFamilyForReject(
    decision?.decision_type,
    roleFamily,
  );
  return {
    ...expectedDecisionType(decision),
    ...(expectedFamily ? { expected_role_family: expectedFamily } : {}),
  };
};

export const pipelineOverrideRequest = (decision) => ({
  override_action: 'manual_review',
  ...expectedDecisionType(decision),
});

export const decisionAuthorityChangeKind = (error) => {
  if (isRoleFamilyChangedError(error)) return 'family';
  if (isDecisionChangedError(error)) return 'decision';
  return null;
};
