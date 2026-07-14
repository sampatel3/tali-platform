import React from 'react';

import { formatStatusLabel } from '../candidates/candidatesUiUtils';
import {
  applicationAtsStage,
  atsProviderLabel,
  roleAtsProvider,
  roleAtsType,
} from './atsType';

export const ProviderStageCell = ({ application, role, stageLabel }) => {
  if (roleAtsType(role) === 'full_ats') {
    return <td><span className="stage-pill" title="Stage in the Taali pipeline">{stageLabel}</span></td>;
  }
  const provider = roleAtsProvider(role);
  const providerLabel = atsProviderLabel(provider);
  const externalStage = applicationAtsStage(application, role);
  if (provider === 'workable' && application?.workable_disqualified) {
    const title = externalStage
      ? `Disqualified in Workable (was: ${formatStatusLabel(externalStage)})`
      : 'Disqualified in Workable';
    return <td><span className="stage-pill is-disqualified" title={title}>Disqualified</span></td>;
  }
  if (application?.hiring_stage_context) {
    const title = externalStage
      ? `Raw ${providerLabel} stage: ${formatStatusLabel(externalStage)}`
      : undefined;
    return <td><span className="ctable-em" title={title}>—</span></td>;
  }
  return (
    <td>
      {externalStage ? (
        <span className="stage-pill" title={`Current stage in ${providerLabel}`}>
          {formatStatusLabel(externalStage)}
        </span>
      ) : <span className="ctable-em">—</span>}
    </td>
  );
};

export const HiringStageCell = ({ application }) => {
  const context = application?.hiring_stage_context || {};
  const stage = context.stage;
  const provider = context.provider || 'native';
  const rawProviderStage = application?.external_stage_raw
    || application?.workable_stage
    || application?.bullhorn_status;
  const title = rawProviderStage
    ? `${formatStatusLabel(provider)} · ${formatStatusLabel(rawProviderStage)}`
    : context.logistics_automation?.status === 'integration_required'
      ? 'Calendar integration required for autonomous logistics'
      : 'No downstream hiring stage recorded';
  const needsCalendar = context.logistics_automation?.status === 'integration_required'
    && context.logistics_automation?.required_integration === 'calendar';

  if (application?.application_outcome === 'rejected') {
    return <td><span className="stage-pill is-disqualified" title={title}>Rejected</span></td>;
  }
  if (stage) {
    return (
      <td>
        <div className="hiring-stage-stack">
          <span className="stage-pill" title={title}>{formatStatusLabel(stage)}</span>
          {needsCalendar ? (
            <a
              href="/settings/integrations"
              onClick={(event) => event.stopPropagation()}
            >
              Calendar setup required
            </a>
          ) : null}
        </div>
      </td>
    );
  }
  return (
    <td>
      <div className="hiring-stage-stack">
        <span className="ctable-em" title={title}>—</span>
        {needsCalendar ? (
          <a
            href="/settings/integrations"
            onClick={(event) => event.stopPropagation()}
          >
            Calendar setup required
          </a>
        ) : null}
      </div>
    </td>
  );
};

export default HiringStageCell;
