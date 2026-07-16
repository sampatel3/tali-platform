export const JOB_TRACKING_STORAGE_PREFIX = 'tali_tracked_';

export const jobTrackingScope = (user) => {
  const org = user?.organization_id ?? user?.organization?.id;
  const member = user?.id;
  if (org != null && member != null) return `org-${org}:user-${member}`;
  if (org != null) return `org-${org}`;
  if (member != null) return `user-${member}`;
  return 'anonymous';
};

export const scopedJobTrackingKey = (baseKey, scope) => `${baseKey}:${scope}`;

export const clearJobTrackingStorage = () => {
  if (typeof localStorage === 'undefined') return;
  const keys = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key?.startsWith(JOB_TRACKING_STORAGE_PREFIX)) keys.push(key);
  }
  keys.forEach((key) => localStorage.removeItem(key));
};
