const JOB_TRACKING_PREFIX = 'taali_session_jobs:';
const LEGACY_JOB_TRACKING_KEYS = [
  'tali_tracked_batch_roles',
  'tali_tracked_fetch_roles',
  'tali_tracked_pre_screen_roles',
  'tali_tracked_process_roles',
];
export const clearSessionJobTrackingStorage = (boundary) => {
  if (typeof localStorage === 'undefined' || !boundary) return;
  try {
    const prefix = `${JOB_TRACKING_PREFIX}${encodeURIComponent(boundary)}:`;
    const keys = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key?.startsWith(prefix)) keys.push(key);
    }
    keys.forEach((key) => localStorage.removeItem(key));
  } catch {
    // Storage can be unavailable in privacy modes. Session revocation still
    // fails closed because its in-memory ownership flag is cleared separately.
  }
};

const parseIds = (rawValue) => {
  try {
    const parsed = rawValue ? JSON.parse(rawValue) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

export const captureLegacyJobTrackingStorage = () => {
  if (typeof localStorage === 'undefined') return null;
  try {
    return Object.fromEntries(LEGACY_JOB_TRACKING_KEYS.map((key) => (
      [key, parseIds(localStorage.getItem(key))]
    )));
  } catch {
    return null;
  }
};

// Seal the one-time legacy session upgrade from a snapshot captured before
// the v2 pointer was reserved. Every known key receives a scoped value,
// including empty sentinels. Shared legacy keys are deliberately untouched:
// later old-bundle writes remain available to that bundle but invisible to v2.
export const migrateLegacyJobTrackingStorage = (boundary, captured = {}) => {
  if (typeof localStorage === 'undefined' || !boundary) return false;
  try {
    for (const baseKey of LEGACY_JOB_TRACKING_KEYS) {
      const scopedKey = `${JOB_TRACKING_PREFIX}${encodeURIComponent(boundary)}:${baseKey}`;
      if (localStorage.getItem(scopedKey) == null) {
        const ids = Array.isArray(captured[baseKey]) ? captured[baseKey] : [];
        localStorage.setItem(scopedKey, JSON.stringify(ids));
      }
    }
    return true;
  } catch {
    return false;
  }
};

export { JOB_TRACKING_PREFIX };
