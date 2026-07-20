const PRIVATE_JOB_STORAGE_PREFIXES = [
  'tali_tracked_',
  'tali_dismissed_',
];

export const clearJobTrackingStorage = () => {
  if (typeof localStorage === 'undefined') return;
  const privateKeys = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (PRIVATE_JOB_STORAGE_PREFIXES.some((prefix) => key?.startsWith(prefix))) {
      privateKeys.push(key);
    }
  }
  privateKeys.forEach((key) => localStorage.removeItem(key));
};
