import React from 'react';

import {
  WorkableTag,
  BullhornTag,
  FullAtsTag,
} from '../../shared/ui/RecruiterDesignPrimitives';

// ONE source of truth for how a job is run. A role is either synced from an
// external ATS (Workable / Bullhorn) or Taali runs it as its own full ATS.
// This is per-job — an org can have some of each — and is derived from fields
// already on the role payload (no backend change):
//   - workable  : source === 'workable' OR it carries a workable_job_id
//   - bullhorn  : source === 'bullhorn' OR it carries a bullhorn_job_order_id
//   - full_ats  : anything else (native Taali requisition)
export const roleAtsType = (role) => {
  const source = String(role?.source || '').toLowerCase();
  if (source === 'workable' || role?.workable_job_id) return 'workable';
  if (source === 'bullhorn' || role?.bullhorn_job_order_id) return 'bullhorn';
  return 'full_ats';
};

// The candidate-table stage column reflects who owns the pipeline: the external
// ATS's stage for synced roles, or the native Taali pipeline for full-ATS roles.
export const atsTypeColumnLabel = (role) => {
  switch (roleAtsType(role)) {
    case 'workable': return 'Workable';
    case 'bullhorn': return 'Bullhorn';
    default: return 'Pipeline';
  }
};

// The single badge every surface renders so a job is unmistakably one mode.
export const AtsTypeTag = ({ role, size = 'md', className = '' }) => {
  switch (roleAtsType(role)) {
    case 'workable':
      return <WorkableTag label="Workable" size={size} className={className} />;
    case 'bullhorn':
      return <BullhornTag label="Bullhorn" size={size} className={className} />;
    default:
      return <FullAtsTag label="Full ATS" size={size} className={className} />;
  }
};
