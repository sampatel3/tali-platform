const SESSION_STORAGE_PREFIX = 'taali.assessment.session.';
const RECOVERY_STORAGE_PREFIX = 'taali.assessment.session.recovery.';
const SESSION_KEY_PATTERN = /^[A-Za-z0-9_-]{32,}$/;
export const CANDIDATE_SESSION_RECOVERY_TTL_MS = 12 * 60 * 60 * 1000;

// The storage locator separates invite tokens without persisting them. It is
// not an authentication secret; the high-entropy candidate session key remains
// the server-bound credential, while this compact fingerprint is only a local
// lookup key for tab and expiring recovery storage.
const tokenFingerprint = (token) => {
  const value = String(token || '');
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193) >>> 0;
    second = Math.imul(second ^ (code + index), 0x85ebca6b) >>> 0;
  }
  return `${value.length.toString(36)}-${first.toString(36)}-${second.toString(36)}`;
};

const toBase64Url = (bytes) => {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return window.btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
};

const storageKeys = (token) => {
  const fingerprint = tokenFingerprint(token);
  return {
    session: `${SESSION_STORAGE_PREFIX}${fingerprint}`,
    recovery: `${RECOVERY_STORAGE_PREFIX}${fingerprint}`,
  };
};

const readStorage = (storage, key) => {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
};

const writeStorage = (storage, key, value) => {
  try {
    storage?.setItem(key, value);
    return true;
  } catch {
    return false;
  }
};

const removeStorage = (storage, key) => {
  try {
    storage?.removeItem(key);
  } catch {
    // Storage can be unavailable in hardened/private browsing modes.
  }
};

const readRecoveryRecord = (storage, key, now) => {
  const raw = readStorage(storage, key);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    const expiresAt = Number(parsed?.expires_at);
    if (
      parsed?.version !== 1
      || !SESSION_KEY_PATTERN.test(String(parsed?.session_key || ''))
      || !Number.isFinite(expiresAt)
      || expiresAt <= now
      // Do not accept a client-edited record that claims an unbounded life.
      || expiresAt > now + CANDIDATE_SESSION_RECOVERY_TTL_MS
    ) {
      removeStorage(storage, key);
      return null;
    }
    return parsed.session_key;
  } catch {
    removeStorage(storage, key);
    return null;
  }
};

const writeRecoveryRecord = (storage, key, sessionKey, now) => writeStorage(
  storage,
  key,
  JSON.stringify({
    version: 1,
    session_key: sessionKey,
    expires_at: now + CANDIDATE_SESSION_RECOVERY_TTL_MS,
  }),
);

export const getOrCreateCandidateSessionKey = (inviteToken) => {
  const token = String(inviteToken || '').trim();
  if (!token || typeof window === 'undefined') return null;

  const { session: sessionStorageKey, recovery: recoveryStorageKey } = storageKeys(token);
  const now = Date.now();
  const tabSessionKey = readStorage(window.sessionStorage, sessionStorageKey);
  if (SESSION_KEY_PATTERN.test(String(tabSessionKey || ''))) {
    // Backfill the expiring recovery record for sessions created before this
    // storage format existed, or when the prior record reached its TTL.
    if (!readRecoveryRecord(window.localStorage, recoveryStorageKey, now)) {
      writeRecoveryRecord(window.localStorage, recoveryStorageKey, tabSessionKey, now);
    }
    return tabSessionKey;
  }

  const recovered = readRecoveryRecord(window.localStorage, recoveryStorageKey, now);
  if (recovered) {
    writeStorage(window.sessionStorage, sessionStorageKey, recovered);
    return recovered;
  }

  if (!window.crypto?.getRandomValues) {
    throw new Error('Secure browser session generation is unavailable.');
  }
  const randomBytes = new Uint8Array(32);
  window.crypto.getRandomValues(randomBytes);
  const candidateSessionKey = toBase64Url(randomBytes);
  if (!SESSION_KEY_PATTERN.test(candidateSessionKey)) {
    throw new Error('Secure browser session generation failed.');
  }
  const persistedInTab = writeStorage(window.sessionStorage, sessionStorageKey, candidateSessionKey);
  const persistedForRecovery = writeRecoveryRecord(
    window.localStorage,
    recoveryStorageKey,
    candidateSessionKey,
    now,
  );
  if (!persistedInTab && !persistedForRecovery) {
    throw new Error('Secure browser session storage is unavailable.');
  }
  return candidateSessionKey;
};

export const clearCandidateSessionKey = (inviteToken) => {
  const token = String(inviteToken || '').trim();
  if (!token || typeof window === 'undefined') return;
  const keys = storageKeys(token);
  removeStorage(window.sessionStorage, keys.session);
  removeStorage(window.localStorage, keys.recovery);
};
