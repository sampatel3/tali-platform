import { getOwnedSessionSnapshot } from '../auth/sessionBoundary';
import {
  clearSessionJobTrackingStorage,
  JOB_TRACKING_PREFIX,
} from '../auth/sessionPrivateStorage';

const scopedKey = (baseKey, boundary) => (
  `${JOB_TRACKING_PREFIX}${encodeURIComponent(boundary)}:${baseKey}`
);

const parseIds = (raw) => {
  try {
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

export const isJobTrackingSessionCurrent = (boundary) => (
  Boolean(boundary) && getOwnedSessionSnapshot()?.boundary === boundary
);

export const loadJobTrackingIds = (baseKey, boundary) => {
  if (typeof localStorage === 'undefined' || !isJobTrackingSessionCurrent(boundary)) return [];
  try {
    const scoped = localStorage.getItem(scopedKey(baseKey, boundary));
    return scoped == null ? [] : parseIds(scoped);
  } catch {
    return [];
  }
};

export const persistJobTrackingIds = (baseKey, boundary, ids) => {
  if (typeof localStorage === 'undefined' || !isJobTrackingSessionCurrent(boundary)) return false;
  try {
    localStorage.setItem(scopedKey(baseKey, boundary), JSON.stringify([...ids]));
    return true;
  } catch {
    return false;
  }
};

export const clearJobTrackingStorage = clearSessionJobTrackingStorage;
