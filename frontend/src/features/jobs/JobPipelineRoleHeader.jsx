import { MessageSquare } from 'lucide-react';

import { AgentHeader } from '../../shared/layout/AgentHeader';
import { AtsTypeTag } from './atsType';
import { JobPipelineHeaderActions } from './JobPipelineHeaderActions';
import { resolvedRoleAutoSkipAssessment } from './jobPipelineUtils';
import {
  OriginalRoleButton,
  RoleFamilyHeaderNote,
  roleFamilyOwner,
  roleReferenceLabel,
} from './RoleFamilyHeaderUi';

export const JobPipelineRoleHeader = ({
  canEditJobSpec,
  canMutateRole,
  controlsDisabledReason,
  externalProvider,
  externalProviderLabel,
  navigate,
  onActivateAgent,
  onAgentSettings,
  onEditJobSpec,
  onOpenProcessDialog,
  onPauseAgent,
  onRescoreSister,
  onResumeAgent,
  onStartRelatedRole,
  onTurnOffAgent,
  processStatus,
  role,
  roleAgent,
  roleFactValues,
  rolePendingReviewTitle,
  roleTasks,
  sisterRescoring,
  sisterScoringStatus,
  startingRelatedRole,
}) => {
  const familyOwner = roleFamilyOwner(role);
  const familyRelatedLabels = (role?.role_family?.related || [])
    .filter((reference) => Number(reference?.id) !== Number(familyOwner?.id))
    .map(roleReferenceLabel)
    .filter(Boolean);
  const headerDisplayRole = role?.role_kind === 'sister'
    ? {
      ...role,
      role_kind: 'standard',
      ats_owner_role_id: null,
      ats_provider: externalProvider,
      source: externalProvider,
    }
    : role;
  const activeTasks = roleTasks.filter((task) => task?.is_active === true);
  const draftTasks = roleTasks.filter(
    (task) => task?.is_active === false && task?.generated,
  );
  const assessmentLabel = activeTasks.length
    ? activeTasks.map((task) => task.name).join(' · ')
    : draftTasks.length
      ? `${draftTasks[0].name} · draft`
      : resolvedRoleAutoSkipAssessment(role)
        ? 'Skipped'
        : 'Skipped until task assigned';

  return (
    <AgentHeader
      kicker={`ROLE · #${role?.id || '—'}`}
      title={(
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
          <span>{role?.name || 'Role'}<span className="ah-period">.</span></span>
          {role ? <AtsTypeTag role={headerDisplayRole} size="sm" /> : null}
        </span>
      )}
      subtitle={(
        <RoleFamilyHeaderNote
          role={role}
          providerLabel={externalProviderLabel}
        />
      )}
      period={false}
      breadcrumbs={[{ label: 'Jobs', page: 'jobs' }, { label: role?.name || 'Role' }]}
      actions={(
        <>
          <JobPipelineHeaderActions
            canEditJobSpec={canEditJobSpec}
            canMutateRole={canMutateRole}
            mutationDisabledReason={controlsDisabledReason}
            externalProvider={externalProvider}
            externalProviderLabel={externalProviderLabel}
            navigate={navigate}
            onEditJobSpec={onEditJobSpec}
            onOpenProcessDialog={onOpenProcessDialog}
            onRescoreSister={onRescoreSister}
            onStartRelatedRole={onStartRelatedRole}
            processStatus={processStatus}
            role={role}
            roleAgent={roleAgent}
            rolePendingReviewTitle={rolePendingReviewTitle}
            sisterRescoring={sisterRescoring}
            sisterScoringStatus={sisterScoringStatus}
            startingRelatedRole={startingRelatedRole}
          />
          {role?.role_kind === 'sister' && familyOwner?.id ? (
            <OriginalRoleButton
              owner={familyOwner}
              onOpen={() => navigate(`/jobs/${familyOwner.id}`)}
            />
          ) : null}
          {role?.role_kind === 'sister' && role?.id ? (
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={() => navigate(`/chat/agents/${role.id}`)}
              title="Open this job's agent chat"
            >
              <MessageSquare size={12} />
              Ask agent
            </button>
          ) : null}
        </>
      )}
      postTitle={(
        <div className="ah-facts">
          <div className="f"><span className="k">Location</span><span className="v">{roleFactValues.location}</span></div>
          <div className="f"><span className="k">Department</span><span className="v">{roleFactValues.department}</span></div>
          <div className="f"><span className="k">Employment</span><span className="v">{roleFactValues.employment}</span></div>
          {role?.role_kind === 'sister' ? (
            <div className="f"><span className="k">Original role</span><span className="v purple">{roleReferenceLabel(familyOwner) || role?.ats_owner_role_name || 'Original role'}</span></div>
          ) : (
            <div className="f">
              <span className="k">{activeTasks.length > 1 ? 'Tasks · A/B' : activeTasks.length ? 'Linked task' : 'Assessment'}</span>
              <span className="v purple">{assessmentLabel}</span>
            </div>
          )}
          {role?.role_kind !== 'sister' && Number(role?.sister_role_count || 0) > 0 ? (
            <div className="f">
              <span className="k">Related roles</span>
              <span className="v purple">
                {familyRelatedLabels.length > 0
                  ? familyRelatedLabels.join(' · ')
                  : `${role.sister_role_count} related role${role.sister_role_count === 1 ? '' : 's'}`}
              </span>
            </div>
          ) : null}
        </div>
      )}
      agent={roleAgent}
      onActivateAgent={onActivateAgent}
      onPauseAgent={onPauseAgent}
      onResumeAgent={onResumeAgent}
      onTurnOffAgent={onTurnOffAgent}
      onAgentSettings={onAgentSettings}
      controlsDisabledReason={controlsDisabledReason}
    />
  );
};
