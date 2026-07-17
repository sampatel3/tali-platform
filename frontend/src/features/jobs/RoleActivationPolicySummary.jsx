import React from 'react';

import {
  hasActiveAssessmentTask,
  resolvedDeterministicReject,
  resolvedRoleAutomation,
  resolvedScoredReject,
} from './jobPipelineUtils';
import {
  roleFamilyOwner,
  roleFamilyReferences,
  roleReferenceLabel,
} from './RoleFamilyHeaderUi';

const roleSharesCandidatePool = (role) => (
  roleFamilyReferences(role).length > 1
  || role?.role_kind === 'sister'
  || Number(role?.sister_role_count || 0) > 0
);

export function RoleActivationPolicySummary({ role, roleTasks, roleTasksFetchKnown }) {
  const activeAssessment = hasActiveAssessmentTask(roleTasks);
  const sharedCandidatePool = roleSharesCandidatePool(role);

  if (role?.role_kind === 'sister') {
    const familyOwner = roleFamilyOwner(role);
    return (
      <div className="mc-agent-settings-card-help">
        <strong>Shared candidates</strong>
        <p style={{ margin: '8px 0 0' }}>
          This role shares candidates with {roleReferenceLabel(familyOwner) || 'the original role'}. The agent scores them separately for {role?.name || 'this related role'}.
        </p>
        <ul style={{ margin: '8px 0 0', paddingLeft: 20 }}>
          {activeAssessment ? (
            <li>
              {resolvedRoleAutomation(role, 'auto_send_assessment')
                ? (resolvedRoleAutomation(role, 'auto_resend_assessment')
                  ? 'Assessments: Invitations and retries send automatically.'
                  : 'Assessments: Invitations send automatically; you approve retries.')
                : (resolvedRoleAutomation(role, 'auto_resend_assessment')
                  ? 'Assessments: You approve invitations; retries send automatically.'
                  : 'Assessments: You approve invitations and retries.')}
            </li>
          ) : (
            <li>{roleTasksFetchKnown
              ? 'No active assessment is assigned, so candidates skip that step for now.'
              : 'The current assessment assignment is unavailable; Taali will not infer one.'}</li>
          )}
          <li>
            {resolvedRoleAutomation(role, 'auto_advance')
              ? 'Candidate decisions: Advances happen automatically across the original role and every related role. You approve rejections; an approved rejection applies across every role.'
              : 'Candidate decisions: You approve advances and rejections. Advancing or rejecting a candidate applies across the original role and every related role.'}
          </li>
        </ul>
      </div>
    );
  }

  const assessmentPolicy = String(
    role?.assessment_task_provisioning?.reconfiguration?.status || '',
  ).toLowerCase() === 'blocked'
    ? 'Turn on confirms the preserved assessment and resumes durable validation'
    : role?.auto_skip_assessment
      ? 'explicitly skipped for this role'
      : activeAssessment
        ? 'uses the active approved task already assigned to this role'
        : roleTasksFetchKnown
          ? 'no active task exists; choose Generate or Skip below'
          : 'the current task assignment is unavailable; choose Generate or Skip rather than inferring it';

  return (
    <div className="mc-agent-settings-card-help">
      <strong>Candidate-action safeguards</strong>
      <ul style={{ margin: '8px 0 0', paddingLeft: 20 }}>
        <li>Assessment invitations {resolvedRoleAutomation(role, 'auto_send_assessment') ? 'send automatically' : 'require your approval'}.</li>
        <li>Assessment retries {resolvedRoleAutomation(role, 'auto_resend_assessment') ? 'send automatically' : 'require your approval'}.</li>
        <li>
          Candidate advancement {resolvedRoleAutomation(role, 'auto_advance')
            ? (sharedCandidatePool ? 'runs automatically across all linked roles' : 'runs automatically')
            : (sharedCandidatePool ? 'requires your approval and advances all linked roles when approved' : 'requires your approval')}.
        </li>
        <li>
          Pre-screen failures {sharedCandidatePool
            ? 'require your approval because rejection affects all linked roles'
            : (resolvedDeterministicReject(role) ? 'reject automatically' : 'require your approval')}.
        </li>
        <li>
          Deterministic rejects after CV and role-fit scoring {sharedCandidatePool
            ? 'require your approval because rejection affects all linked roles'
            : (resolvedScoredReject(role) ? 'run automatically' : 'require your approval')}. Assessment-stage and LLM-only rejects require approval.
        </li>
        <li>Current assessment policy: {assessmentPolicy}.</li>
      </ul>
      <p style={{ margin: '8px 0 0' }}>
        Generate, validate, and approve a role-specific assessment, or explicitly skip the assessment stage. Turn on never guesses between those choices.
      </p>
    </div>
  );
}

export default RoleActivationPolicySummary;
