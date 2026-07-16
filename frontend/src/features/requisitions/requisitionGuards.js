import { conflictActorLabel, roleVersionConflict } from '../jobs/roleConcurrency';
import { requisitionApi } from './api';

export const errorDetail = (error, fallback) => {
  const detail = error?.response?.data?.detail;
  return typeof detail === 'string' && detail.trim() ? detail : fallback;
};

const humanizeGapKey = (key) => String(key || '')
  .replace(/_/g, ' ')
  .replace(/\b\w/g, (character) => character.toUpperCase())
  .trim();

export const requisitionGapLabels = (gaps) => {
  const labels = (Array.isArray(gaps) ? gaps : [])
    .map((gap) => String(gap?.label || humanizeGapKey(gap?.key) || '').trim())
    .filter(Boolean);
  return [...new Set(labels)];
};

export const requisitionPublishBlockedMessage = (gaps, { relatedRole = false } = {}) => {
  const labels = requisitionGapLabels(gaps);
  if (labels.length === 0) return '';
  const action = relatedRole ? 'create and score candidates' : 'publish this job';
  return `Complete the required Brief fields before you can ${action}: ${labels.join(', ')}.`;
};

export const requisitionRoleConflictMessage = (error, { latestLoaded = true } = {}) => {
  const conflict = roleVersionConflict(error);
  if (!conflict) return null;
  const actor = conflictActorLabel(conflict.changedBy);
  const prefix = `${conflict.message || 'This job changed before your update was saved.'}${actor ? ` Changed by ${actor}.` : ''}`;
  return latestLoaded
    ? `${prefix} Latest requisition loaded — review and try again.`
    : `${prefix} The latest requisition could not be loaded; reload this page before retrying.`;
};

export const reloadRequisitionAfterRoleConflict = async (
  briefId,
  error,
  fetchBrief = requisitionApi.get,
) => {
  if (!roleVersionConflict(error)) return null;
  try {
    const latestBrief = await fetchBrief(briefId);
    if (!latestBrief || Number(latestBrief.id) !== Number(briefId)) {
      throw new Error('Conflict refresh returned the wrong requisition');
    }
    return {
      brief: latestBrief,
      message: requisitionRoleConflictMessage(error, { latestLoaded: true }),
    };
  } catch {
    // Never adopt only the conflict's Role.version. Without the authoritative
    // RoleBrief, a retry could pass OCC with stale requisition fields.
    return {
      brief: null,
      message: requisitionRoleConflictMessage(error, { latestLoaded: false }),
    };
  }
};
