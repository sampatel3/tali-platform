import {
  expectedRoleFamilySnapshot,
  isDecisionChangedError,
  isRoleFamilyChangedError,
} from '../../shared/decisions/decisionActions';

const expectedDecisionType = (decision) => {
  const decisionType = String(decision?.decision_type || '').trim();
  return decisionType ? { expected_decision_type: decisionType } : {};
};

export const pipelineApprovalRequest = (decision, roleFamily) => {
  // Both linked advances and rejects act on one shared application. Carry the
  // exact family the recruiter reviewed even where an older server only
  // required it for rejects, so newer authority checks can fence either path.
  const expectedFamily = expectedRoleFamilySnapshot(roleFamily);
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
