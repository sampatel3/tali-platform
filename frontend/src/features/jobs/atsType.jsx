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
//   - preferred : ats_provider + external_job_* (provider-neutral contract)
//   - workable  : source === 'workable' OR it carries a workable_job_id
//   - bullhorn  : source === 'bullhorn' OR it carries a bullhorn_job_order_id
//   - full_ats  : anything else (native Taali requisition)
export const roleAtsType = (role) => {
  if (role?.role_kind === 'sister' || role?.ats_owner_role_id) return 'sister';
  const provider = String(role?.ats_provider || '').toLowerCase();
  if (provider === 'workable' || provider === 'bullhorn') return provider;
  const source = String(role?.source || '').toLowerCase();
  if (source === 'workable' || role?.workable_job_id) return 'workable';
  if (source === 'bullhorn' || role?.bullhorn_job_order_id) return 'bullhorn';
  return 'full_ats';
};

export const roleAtsProvider = (role) => {
  // Sister/scoring views now carry the operational owner's provider on the
  // neutral contract. Prefer it before the legacy sister→Workable fallback so
  // a Bullhorn-owned candidate pool keeps Bullhorn labels and write routing.
  const explicit = String(role?.ats_provider || '').toLowerCase();
  if (explicit === 'workable' || explicit === 'bullhorn') return explicit;
  const type = roleAtsType(role);
  if (type === 'sister') return 'workable';
  return type === 'workable' || type === 'bullhorn' ? type : null;
};

export const atsProviderLabel = (provider) => {
  if (provider === 'workable') return 'Workable';
  if (provider === 'bullhorn') return 'Bullhorn';
  return 'Taali';
};

export const organizationAtsProvider = (organization) => {
  const active = String(organization?.active_ats || '').toLowerCase();
  // Once the server sends active_ats it is authoritative, including the
  // explicit `standalone` posture. Falling through from `standalone` to a stale
  // *_connected flag can expose a provider whose feature gate/credentials are
  // unavailable and makes Jobs immediately call endpoints that cannot work.
  if (active) {
    if (active === 'workable') {
      return organization?.workable_connected !== false ? 'workable' : null;
    }
    if (active === 'bullhorn') {
      return organization?.bullhorn_connected !== false ? 'bullhorn' : null;
    }
    return null;
  }
  // Match the server's established precedence for legacy payloads that do not
  // yet carry active_ats.
  if (organization?.workable_connected) return 'workable';
  if (organization?.bullhorn_connected) return 'bullhorn';
  return null;
};

// Shared pause/off copy for every role surface. An external ATS posting is not
// closed by Taali, while a native Taali page is still held so it cannot create
// unprocessed applications while the role agent is stopped.
export const agentIntakeLifecycleCopy = (role) => {
  const provider = roleAtsProvider(role);
  if (provider) {
    const label = atsProviderLabel(provider);
    return `Any Taali native job page remains viewable, but stops accepting applications until Resume or Turn on. ${label} intake is not closed by Taali and continues according to its provider-side publish state.`;
  }
  return 'The native job page remains viewable, but applications close until Resume or Turn on.';
};

export const roleExternalJobId = (role) => (
  role?.external_job_id
  ?? role?.workable_job_id
  ?? role?.bullhorn_job_order_id
  ?? null
);

export const roleExternalJobState = (role) => {
  const value = role?.external_job_state ?? role?.workable_job_state;
  const normalized = String(value || '').trim().toLowerCase();
  return normalized || null;
};

// `null` means the provider lifecycle is unknown. Do not reuse
// workable_job_live for Bullhorn: legacy role payloads serialized that field as
// true for every non-Workable role, which would falsely mark closed JobOrders
// as live.
export const roleExternalJobLive = (role) => {
  if (typeof role?.external_job_live === 'boolean') return role.external_job_live;
  if (roleAtsProvider(role) === 'workable' && typeof role?.workable_job_live === 'boolean') {
    return role.workable_job_live;
  }
  return null;
};

const NATIVE_JOB_STATUSES = new Set(['draft', 'open', 'filled', 'filled_external', 'cancelled']);

// Native roles created before job_status was persisted need one compatibility
// rule across the detail, catalogue, filters, and rollups. An explicitly empty
// role with no job spec is a draft; every other legacy native role is already
// in use and therefore reads as open.
export const effectiveNativeJobStatus = (role) => {
  const persisted = String(role?.job_status || '').trim().toLowerCase();
  if (NATIVE_JOB_STATUSES.has(persisted)) return persisted;
  const explicitlyEmpty = role?.job_spec_present === false
    && Number(role?.applications_count || 0) === 0
    && Number(role?.active_candidates_count || 0) === 0;
  return explicitlyEmpty ? 'draft' : 'open';
};

export const applicationAtsStage = (application, roleOrProvider = null) => {
  const provider = typeof roleOrProvider === 'string'
    ? roleOrProvider
    : roleAtsProvider(roleOrProvider) || String(application?.source || '').toLowerCase();
  if (provider === 'bullhorn') {
    return application?.external_stage_raw
      ?? application?.bullhorn_status
      ?? application?.external_stage_normalized
      ?? null;
  }
  if (provider === 'workable') {
    return application?.workable_stage
      ?? application?.external_stage_raw
      ?? application?.external_stage_normalized
      ?? null;
  }
  return null;
};

// The candidate-table stage column reflects who owns the pipeline: the external
// ATS's stage for synced roles, or the native Taali pipeline for full-ATS roles.
export const atsTypeColumnLabel = (role) => {
  switch (roleAtsType(role)) {
    case 'sister': return atsProviderLabel(roleAtsProvider(role));
    case 'workable': return 'Workable';
    case 'bullhorn': return 'Bullhorn';
    default: return 'Pipeline';
  }
};

// The single badge every surface renders so a job is unmistakably one mode.
export const AtsTypeTag = ({ role, size = 'md', className = '' }) => {
  switch (roleAtsType(role)) {
    case 'sister':
      return <FullAtsTag label={`Related · ${atsProviderLabel(roleAtsProvider(role))}`} size={size} className={className} />;
    case 'workable':
      return <WorkableTag label="Workable" size={size} className={className} />;
    case 'bullhorn':
      return <BullhornTag label="Bullhorn" size={size} className={className} />;
    default:
      return <FullAtsTag label="Full ATS" size={size} className={className} />;
  }
};
