import {
  captureLegacyJobTrackingStorage,
  clearSessionJobTrackingStorage,
  migrateLegacyJobTrackingStorage,
} from './sessionPrivateStorage';

const SESSION_BOUNDARY_STORAGE_KEY = 'taali_session_boundary';
const SESSION_MIGRATION_BOUNDARY_STORAGE_KEY = 'taali_session_migration_boundary';
const SESSION_BOUNDARY_EVENT = 'auth:session-boundary';

const SESSION_CREDENTIALS_PREFIX = 'taali_session_credentials:';
const SESSION_PROFILE_PREFIX = 'taali_session_profile:';
const SESSION_REVOKED_PREFIX = 'taali_session_revoked:';
const SESSION_MIGRATION_PREFIX = 'taali_session_migration:';
const LEGACY_TOKEN_KEY = 'taali_access_token';
const LEGACY_TOKEN_ISSUED_AT_KEY = 'taali_token_issued_at';

let lastSeenBoundary = null;
let initializedForThisTab = false;
let requestSessionActive = false;
let adoptableMigrationBoundary = null;

const storageAvailable = () => typeof window !== 'undefined';

const readStorageValue = (key) => {
  if (!storageAvailable()) return { ok: false, value: null };
  try {
    return { ok: true, value: window.localStorage.getItem(key) };
  } catch {
    return { ok: false, value: null };
  }
};

const getStorageValue = (key) => readStorageValue(key).value;

const setStorageValue = (key, value) => {
  if (!storageAvailable()) return false;
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
};

const removeStorageValue = (key) => {
  if (!storageAvailable()) return false;
  try {
    window.localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
};

const newBoundaryMarker = () => {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
};

const legacyTokenFingerprint = (token) => {
  let first = 2166136261;
  let second = 2246822519;
  for (let index = 0; index < token.length; index += 1) {
    const code = token.charCodeAt(index);
    first = Math.imul(first ^ code, 16777619);
    second = Math.imul(second ^ code, 3266489917);
  }
  return `${token.length}:${(first >>> 0).toString(36)}:${(second >>> 0).toString(36)}`;
};

const parseBoundaryValue = (
  rawValue,
  {
    storageKey = SESSION_BOUNDARY_STORAGE_KEY,
    legacyRawValue = null,
  } = {},
) => {
  if (!rawValue) return null;
  try {
    const parsed = JSON.parse(rawValue);
    if (parsed?.version === 2 && typeof parsed.marker === 'string' && parsed.marker) {
      const hasMigration = Object.prototype.hasOwnProperty.call(parsed, 'migration');
      if (!hasMigration) {
        return {
          marker: parsed.marker,
          rawValue,
          version: 2,
          migration: null,
          storageKey,
          legacyRawValue,
        };
      }
      const migration = parsed.migration;
      if (migration?.version === 1
        && typeof migration.tokenId === 'string'
        && migration.tokenId) {
        return {
          marker: parsed.marker,
          rawValue,
          version: 2,
          migration: { tokenId: migration.tokenId, version: 1 },
          storageKey,
          legacyRawValue,
        };
      }
      return {
        invalid: true,
        marker: parsed.marker,
        rawValue,
        version: 2,
        migration: null,
        storageKey,
        legacyRawValue,
      };
    }
    // A structured but unsupported value may belong to a future release.
    // Preserve it and fail closed.
    return {
      invalid: true,
      marker: typeof parsed?.marker === 'string' && parsed.marker
        ? parsed.marker
        : rawValue,
      rawValue,
      version: parsed?.version ?? 'unsupported',
      migration: null,
      storageKey,
      legacyRawValue,
    };
  } catch {
    // This key did not exist in the production version being upgraded. A raw
    // value is therefore corruption or an unknown writer, not a legacy format
    // we should reinterpret. Preserve it and fail closed.
    return {
      invalid: true,
      marker: rawValue,
      rawValue,
      version: 'invalid',
      migration: null,
      storageKey,
      legacyRawValue,
    };
  }
};

const encodeBoundaryValue = (marker, migration = null) => JSON.stringify({
  version: 2,
  marker,
  ...(migration ? { migration } : {}),
});

const credentialsKey = (marker) => `${SESSION_CREDENTIALS_PREFIX}${marker}`;
const profileKey = (marker) => `${SESSION_PROFILE_PREFIX}${marker}`;
const revokedKey = (marker) => `${SESSION_REVOKED_PREFIX}${marker}`;
const migrationKey = (marker) => `${SESSION_MIGRATION_PREFIX}${marker}`;

const readStoredBoundaryState = () => {
  const primary = readStorageValue(SESSION_BOUNDARY_STORAGE_KEY);
  if (!primary.ok) return null;
  const primaryState = parseBoundaryValue(primary.value);
  // Any primary pointer is authoritative. Explicit v2 transitions therefore
  // always outrank a one-time migration, and corrupt/future formats are
  // preserved rather than overwritten.
  if (primaryState) return primaryState;

  const migrationBoundary = readStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY);
  if (!migrationBoundary.ok) return null;
  if (migrationBoundary.value) {
    const migrationState = parseBoundaryValue(migrationBoundary.value, {
      storageKey: SESSION_MIGRATION_BOUNDARY_STORAGE_KEY,
      legacyRawValue: primary.value,
    });
    if (migrationState?.version === 2 && !migrationState.invalid) return migrationState;
    return {
      ...migrationState,
      invalid: true,
    };
  }
  return primaryState;
};

const readStoredBoundary = () => readStoredBoundaryState()?.marker || null;

const readJson = (key) => {
  const rawValue = getStorageValue(key);
  if (!rawValue) return null;
  try {
    return JSON.parse(rawValue);
  } catch {
    return null;
  }
};

const readCredentials = (marker) => {
  if (!marker) return null;
  const value = readJson(credentialsKey(marker));
  if (!value || value.version !== 1 || typeof value.token !== 'string' || !value.token) {
    return null;
  }
  return {
    token: value.token,
    issuedAt: Number(value.issuedAt) || 0,
    migrationTokenId: typeof value.migrationTokenId === 'string'
      ? value.migrationTokenId
      : null,
    migrationComplete: value.migrationComplete === true,
  };
};

const readProfile = (marker) => {
  if (!marker) return null;
  const value = readJson(profileKey(marker));
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
};

const readRevocation = (marker) => (
  marker ? getStorageValue(revokedKey(marker)) : null
);

const isRevoked = (marker) => Boolean(readRevocation(marker));

const boundaryHasActiveCredentials = (state) => {
  if (!state || state.version !== 2 || state.invalid || isRevoked(state.marker)) return false;
  const credentials = readCredentials(state.marker);
  if (!credentials) return false;
  if (!state.migration) return true;
  return credentials.migrationComplete
    && credentials.migrationTokenId === state.migration.tokenId;
};

const publishBoundaryEvent = () => {
  if (!storageAvailable()) return;
  try {
    window.dispatchEvent(new Event(SESSION_BOUNDARY_EVENT));
  } catch {
    // Event delivery is best-effort; storage ownership checks remain decisive.
  }
};

const removeSessionSecrets = (marker) => {
  if (!marker) return;
  removeStorageValue(credentialsKey(marker));
  removeStorageValue(profileKey(marker));
  removeStorageValue(migrationKey(marker));
};

const removeSessionArtifacts = (marker) => {
  if (!marker) return;
  removeSessionSecrets(marker);
  removeStorageValue(revokedKey(marker));
  clearSessionJobTrackingStorage(marker);
};

const currentPointerIs = (state) => (
  Boolean(state?.rawValue)
  && getStorageValue(state.storageKey || SESSION_BOUNDARY_STORAGE_KEY) === state.rawValue
  && (state.storageKey !== SESSION_MIGRATION_BOUNDARY_STORAGE_KEY
    || getStorageValue(SESSION_BOUNDARY_STORAGE_KEY) === state.legacyRawValue)
);

const discardSupersededMigration = (state) => {
  if (!state?.marker) return;
  removeSessionArtifacts(state.marker);
  const primaryState = parseBoundaryValue(getStorageValue(SESSION_BOUNDARY_STORAGE_KEY));
  // Once a supported explicit v2 transition exists, every migration pointer
  // is permanently shadowed. Best-effort removal avoids leaving even its inert
  // marker/fingerprint record behind; a concurrent migration cannot activate.
  if (primaryState?.version === 2
    && !primaryState.invalid
    && !primaryState.migration
    && getStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY) === state.rawValue) {
    removeStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY);
  }
};

const removeKnownMigrationBoundary = () => {
  const rawValue = getStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY);
  const state = parseBoundaryValue(rawValue, {
    storageKey: SESSION_MIGRATION_BOUNDARY_STORAGE_KEY,
  });
  if (state?.version === 2 && !state.invalid) {
    removeStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY);
  }
};

const readLegacyIssuedAt = () => {
  const result = readStorageValue(LEGACY_TOKEN_ISSUED_AT_KEY);
  if (!result.ok) return null;
  if (result.value == null || !String(result.value).trim()) return 0;
  const parsed = Number(result.value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const captureLegacyMigration = () => {
  const tokenBefore = readStorageValue(LEGACY_TOKEN_KEY);
  if (!tokenBefore.ok) return { ok: false };
  if (!tokenBefore.value) return { ok: true, token: null };

  const issuedAt = readLegacyIssuedAt();
  const jobs = captureLegacyJobTrackingStorage();
  const tokenAfter = readStorageValue(LEGACY_TOKEN_KEY);
  if (issuedAt == null
    || jobs == null
    || !tokenAfter.ok
    || tokenAfter.value !== tokenBefore.value) {
    return { ok: false };
  }
  return {
    ok: true,
    token: tokenBefore.value,
    tokenId: legacyTokenFingerprint(tokenBefore.value),
    issuedAt,
    jobs,
  };
};

const readVerifiedMigration = (state) => {
  if (!state?.migration) return null;
  const value = readJson(migrationKey(state.marker));
  if (!value
    || value.version !== 1
    || value.cutoverVerified !== true
    || typeof value.token !== 'string'
    || !value.token
    || value.tokenId !== state.migration.tokenId
    || legacyTokenFingerprint(value.token) !== value.tokenId
    || !value.jobs
    || typeof value.jobs !== 'object'
    || Array.isArray(value.jobs)) {
    return null;
  }
  return {
    token: value.token,
    tokenId: value.tokenId,
    issuedAt: Number(value.issuedAt) || 0,
    jobs: value.jobs,
  };
};

const finalizeLegacyMigration = (state) => {
  if (!state?.migration) return state;
  if (!currentPointerIs(state)) {
    discardSupersededMigration(state);
    return readStoredBoundaryState();
  }
  if (isRevoked(state.marker)) return state;
  if (boundaryHasActiveCredentials(state)) {
    removeStorageValue(migrationKey(state.marker));
    return state;
  }

  const migration = readVerifiedMigration(state);
  if (!migration) return state;
  if (!migrateLegacyJobTrackingStorage(state.marker, migration.jobs)) return state;
  if (!currentPointerIs(state)) {
    discardSupersededMigration(state);
    return readStoredBoundaryState();
  }

  // A peer may have completed the same immutable migration while this tab was
  // copying the scoped job snapshot. Its completed credential always wins.
  if (boundaryHasActiveCredentials(state)) {
    removeStorageValue(migrationKey(state.marker));
    return state;
  }

  const credentialsValue = JSON.stringify({
    version: 1,
    token: migration.token,
    issuedAt: migration.issuedAt,
    migrationTokenId: migration.tokenId,
    migrationComplete: true,
  });
  if (!setStorageValue(credentialsKey(state.marker), credentialsValue)) return state;
  if (!currentPointerIs(state)) {
    discardSupersededMigration(state);
    return readStoredBoundaryState();
  }
  if (isRevoked(state.marker)) {
    removeSessionSecrets(state.marker);
    clearSessionJobTrackingStorage(state.marker);
    return state;
  }
  removeStorageValue(migrationKey(state.marker));
  return state;
};

const reserveV2Boundary = (initialRawValue) => {
  const captured = captureLegacyMigration();
  if (!captured.ok) return readStoredBoundaryState();

  const marker = newBoundaryMarker();
  if (!captured.token) {
    const rawValue = encodeBoundaryValue(marker);
    if (getStorageValue(SESSION_BOUNDARY_STORAGE_KEY) !== initialRawValue
      || getStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY) != null
      || !setStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY, rawValue)) {
      return readStoredBoundaryState();
    }
    const state = parseBoundaryValue(rawValue, {
      storageKey: SESSION_MIGRATION_BOUNDARY_STORAGE_KEY,
      legacyRawValue: initialRawValue,
    });
    if (!currentPointerIs(state)) {
      discardSupersededMigration(state);
      return readStoredBoundaryState();
    }
    migrateLegacyJobTrackingStorage(marker, {});
    if (!currentPointerIs(state)) {
      discardSupersededMigration(state);
      return readStoredBoundaryState();
    }
    return state;
  }

  const migration = { version: 1, tokenId: captured.tokenId };
  const pendingValue = JSON.stringify({
    version: 1,
    token: captured.token,
    tokenId: captured.tokenId,
    issuedAt: captured.issuedAt,
    jobs: captured.jobs,
    cutoverVerified: false,
  });
  if (!setStorageValue(migrationKey(marker), pendingValue)) return readStoredBoundaryState();

  const tokenBeforeCutover = readStorageValue(LEGACY_TOKEN_KEY);
  if (getStorageValue(SESSION_BOUNDARY_STORAGE_KEY) !== initialRawValue
    || !tokenBeforeCutover.ok
    || tokenBeforeCutover.value !== captured.token) {
    removeSessionArtifacts(marker);
    return readStoredBoundaryState();
  }

  const rawValue = encodeBoundaryValue(marker, migration);
  if (getStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY) != null
    || !setStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY, rawValue)) {
    removeSessionArtifacts(marker);
    return readStoredBoundaryState();
  }
  const state = parseBoundaryValue(rawValue, {
    storageKey: SESSION_MIGRATION_BOUNDARY_STORAGE_KEY,
    legacyRawValue: initialRawValue,
  });
  const tokenAfterCutover = readStorageValue(LEGACY_TOKEN_KEY);
  if (!currentPointerIs(state)
    || !tokenAfterCutover.ok
    || tokenAfterCutover.value !== captured.token) {
    if (!currentPointerIs(state)) {
      discardSupersededMigration(state);
    } else {
      removeStorageValue(migrationKey(marker));
      clearSessionJobTrackingStorage(marker);
    }
    return readStoredBoundaryState();
  }

  // The immutable pointer is the cutover. Once the exact token is verified on
  // both sides of that write, all later legacy writes belong only to old code.
  const verifiedValue = JSON.stringify({
    version: 1,
    token: captured.token,
    tokenId: captured.tokenId,
    issuedAt: captured.issuedAt,
    jobs: captured.jobs,
    cutoverVerified: true,
  });
  if (!setStorageValue(migrationKey(marker), verifiedValue)
    || !currentPointerIs(state)
    || getStorageValue(migrationKey(marker)) !== verifiedValue) {
    if (!currentPointerIs(state)) {
      discardSupersededMigration(state);
    } else {
      removeStorageValue(migrationKey(marker));
      clearSessionJobTrackingStorage(marker);
    }
    return readStoredBoundaryState();
  }
  return state;
};

const initializeStoredBoundary = () => {
  const primary = readStorageValue(SESSION_BOUNDARY_STORAGE_KEY);
  const migrationBoundary = readStorageValue(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY);
  if (!primary.ok || !migrationBoundary.ok) return null;
  let state = readStoredBoundaryState();
  if (migrationBoundary.value == null && !state) {
    state = reserveV2Boundary(primary.value);
  }
  return state?.migration ? finalizeLegacyMigration(state) : state;
};

const invalidateForExternalBoundary = (marker) => {
  const previousMarker = lastSeenBoundary;
  lastSeenBoundary = marker || null;
  initializedForThisTab = true;
  requestSessionActive = false;
  adoptableMigrationBoundary = null;
  if (previousMarker && previousMarker !== marker) removeSessionArtifacts(previousMarker);
  publishBoundaryEvent();
};

export const initializeSessionBoundary = () => {
  if (!storageAvailable()) {
    initializedForThisTab = true;
    requestSessionActive = true;
    return null;
  }
  const state = initializeStoredBoundary();
  const marker = state?.marker || null;
  const active = boundaryHasActiveCredentials(state);
  lastSeenBoundary = marker;
  initializedForThisTab = true;
  requestSessionActive = active;
  adoptableMigrationBoundary = state?.migration && !active ? marker : null;
  return marker;
};

export const beginSessionTransition = () => {
  if (!storageAvailable()) {
    initializedForThisTab = true;
    requestSessionActive = false;
    return null;
  }
  const previousState = readStoredBoundaryState();
  const previousMarker = previousState?.marker || null;
  const previousOwnedSnapshot = getOwnedSessionSnapshot();
  const marker = newBoundaryMarker();
  const rawValue = encodeBoundaryValue(marker);
  if (!setStorageValue(SESSION_BOUNDARY_STORAGE_KEY, rawValue)) {
    const currentState = readStoredBoundaryState();
    const currentCredentials = readCredentials(previousMarker);
    if (previousOwnedSnapshot
      && currentState?.marker === previousOwnedSnapshot.boundary
      && currentCredentials?.token === previousOwnedSnapshot.token) {
      // A failed logout/login-start must not revive the prior session after a
      // reload. Clear only the exact scoped credential this tab still owns.
      removeSessionSecrets(previousMarker);
      clearSessionJobTrackingStorage(previousMarker);
    }
    lastSeenBoundary = null;
    initializedForThisTab = true;
    requestSessionActive = false;
    adoptableMigrationBoundary = null;
    publishBoundaryEvent();
    return null;
  }
  if (getStorageValue(SESSION_BOUNDARY_STORAGE_KEY) !== rawValue) {
    removeSessionArtifacts(marker);
    invalidateForExternalBoundary(readStoredBoundary());
    return marker;
  }
  if (!previousState?.invalid) removeSessionArtifacts(previousMarker);
  removeKnownMigrationBoundary();
  lastSeenBoundary = marker;
  initializedForThisTab = true;
  requestSessionActive = false;
  adoptableMigrationBoundary = null;
  publishBoundaryEvent();
  return marker;
};

export const activateSessionBoundary = (marker, token, issuedAt = Date.now()) => {
  if (!storageAvailable()) {
    lastSeenBoundary = marker || null;
    initializedForThisTab = true;
    requestSessionActive = Boolean(token);
    return Boolean(token);
  }
  const state = readStoredBoundaryState();
  if (!marker
    || !token
    || state?.marker !== marker
    || state.storageKey !== SESSION_BOUNDARY_STORAGE_KEY
    || state.migration
    || state.invalid) return false;
  const normalizedIssuedAt = Number(issuedAt) || Date.now();
  removeStorageValue(revokedKey(marker));
  if (!setStorageValue(credentialsKey(marker), JSON.stringify({
    version: 1,
    token,
    issuedAt: normalizedIssuedAt,
  }))) return false;
  const currentState = readStoredBoundaryState();
  if (currentState?.marker !== marker
    || currentState.storageKey !== SESSION_BOUNDARY_STORAGE_KEY
    || currentState.migration
    || currentState.invalid) {
    removeSessionArtifacts(marker);
    return false;
  }
  if (isRevoked(marker)) {
    removeSessionSecrets(marker);
    return false;
  }
  lastSeenBoundary = marker;
  initializedForThisTab = true;
  requestSessionActive = true;
  adoptableMigrationBoundary = null;
  return true;
};

export const updateSessionAccessToken = (
  marker,
  token,
  { expectedToken = null, issuedAt = Date.now() } = {},
) => {
  if (!storageAvailable() || !marker || !token) return false;
  const state = readStoredBoundaryState();
  if (state?.marker !== marker || !boundaryHasActiveCredentials(state)) return false;
  const current = readCredentials(marker);
  if (!current || (expectedToken && current.token !== expectedToken)) return false;
  const normalizedIssuedAt = Number(issuedAt) || Date.now();
  if (!setStorageValue(credentialsKey(marker), JSON.stringify({
    version: 1,
    token,
    issuedAt: normalizedIssuedAt,
    ...(current.migrationTokenId ? { migrationTokenId: current.migrationTokenId } : {}),
    ...(current.migrationComplete ? { migrationComplete: true } : {}),
  }))) return false;
  const currentState = readStoredBoundaryState();
  if (currentState?.marker !== marker) {
    removeSessionArtifacts(marker);
    return false;
  }
  if (isRevoked(marker)) {
    removeSessionSecrets(marker);
    return false;
  }
  return boundaryHasActiveCredentials(currentState);
};

export const storeSessionProfile = (marker, profile) => {
  if (!storageAvailable() || !marker || !profile) return false;
  const state = readStoredBoundaryState();
  if (state?.marker !== marker || !boundaryHasActiveCredentials(state)) return false;
  if (!setStorageValue(profileKey(marker), JSON.stringify(profile))) return false;
  const currentState = readStoredBoundaryState();
  if (currentState?.marker !== marker || !boundaryHasActiveCredentials(currentState)) {
    removeStorageValue(profileKey(marker));
    return false;
  }
  return true;
};

export const getStoredSessionSnapshot = () => {
  if (!storageAvailable()) return null;
  if (!initializedForThisTab) initializeSessionBoundary();
  const state = readStoredBoundaryState();
  if (!boundaryHasActiveCredentials(state)) return null;
  const credentials = readCredentials(state.marker);
  return {
    boundary: state.marker,
    token: credentials.token,
    issuedAt: credentials.issuedAt,
    profile: readProfile(state.marker),
  };
};

export const getOwnedSessionSnapshot = () => {
  if (!storageAvailable()) return null;
  if (!initializedForThisTab || !requestSessionActive || !lastSeenBoundary) return null;
  const state = readStoredBoundaryState();
  if (state?.marker !== lastSeenBoundary || !boundaryHasActiveCredentials(state)) return null;
  const credentials = readCredentials(lastSeenBoundary);
  return {
    boundary: lastSeenBoundary,
    token: credentials.token,
    issuedAt: credentials.issuedAt,
    profile: readProfile(lastSeenBoundary),
  };
};

export const isRequestSessionCurrent = () => {
  if (!storageAvailable()) return true;
  if (!initializedForThisTab) initializeSessionBoundary();
  const state = readStoredBoundaryState();
  if (!state?.marker || state.marker !== lastSeenBoundary) {
    invalidateForExternalBoundary(state?.marker || null);
    return false;
  }
  if (!requestSessionActive || !boundaryHasActiveCredentials(state)) {
    if (requestSessionActive) invalidateForExternalBoundary(state.marker);
    return false;
  }
  return true;
};

export const getCurrentSessionSnapshot = () => (
  isRequestSessionCurrent() ? getOwnedSessionSnapshot() : null
);

export const captureStoredSessionBoundary = () => {
  if (!storageAvailable()) return null;
  if (!initializedForThisTab) initializeSessionBoundary();
  return readStoredBoundary();
};

export const isStoredSessionBoundaryCurrent = (marker) => (
  !storageAvailable() || (Boolean(marker) && readStoredBoundary() === marker)
);

export const getCurrentSessionBoundary = () => (
  isRequestSessionCurrent() ? lastSeenBoundary : null
);

export const isSessionBoundaryCurrent = (marker) => (
  Boolean(marker) && getCurrentSessionSnapshot()?.boundary === marker
);

export const isStoredSessionBoundaryActive = (marker) => {
  if (!marker) return false;
  const state = readStoredBoundaryState();
  return state?.marker === marker && boundaryHasActiveCredentials(state);
};

export const revokeSessionBoundary = (marker, { expectedToken = null } = {}) => {
  if (!storageAvailable() || !marker || !expectedToken) return false;
  const ownedSnapshot = getCurrentSessionSnapshot();
  if (ownedSnapshot?.boundary !== marker || ownedSnapshot.token !== expectedToken) {
    if (readStoredBoundary() !== marker) removeSessionArtifacts(marker);
    return false;
  }

  const revocationValue = JSON.stringify({ version: 2, id: newBoundaryMarker() });
  if (!setStorageValue(revokedKey(marker), revocationValue)) {
    const currentState = readStoredBoundaryState();
    const currentCredentials = readCredentials(marker);
    if (currentState?.marker !== marker || currentCredentials?.token !== expectedToken) {
      if (currentState?.marker !== marker) removeSessionArtifacts(marker);
      return false;
    }
    // Quota/privacy-mode failures must not leave a known-expired session live
    // in this tab. Credential removal also invalidates peers when storage
    // remains readable even though the tombstone write itself failed.
    removeSessionSecrets(marker);
    clearSessionJobTrackingStorage(marker);
    requestSessionActive = false;
    adoptableMigrationBoundary = null;
    publishBoundaryEvent();
    return true;
  }
  const stateAfterTombstone = readStoredBoundaryState();
  const credentialsAfterTombstone = readCredentials(marker);
  const revocationStillOurs = readRevocation(marker) === revocationValue;
  const ownsCurrentBoundary = stateAfterTombstone?.marker === marker
    && revocationStillOurs
    && (!credentialsAfterTombstone || credentialsAfterTombstone.token === expectedToken);

  if (!ownsCurrentBoundary
    && stateAfterTombstone?.marker === marker
    && readRevocation(marker) === revocationValue) {
    removeStorageValue(revokedKey(marker));
  }
  if (!ownsCurrentBoundary && stateAfterTombstone?.marker !== marker) {
    removeSessionArtifacts(marker);
  }
  if (!ownsCurrentBoundary) return false;

  removeSessionSecrets(marker);
  clearSessionJobTrackingStorage(marker);
  if (lastSeenBoundary === marker) {
    requestSessionActive = false;
    adoptableMigrationBoundary = null;
    publishBoundaryEvent();
  }
  return true;
};

export const endSessionBoundary = () => beginSessionTransition();

if (storageAvailable()) {
  window.addEventListener('storage', (event) => {
    if (event.storageArea && event.storageArea !== window.localStorage) return;

    if (event.key === SESSION_BOUNDARY_STORAGE_KEY
      || event.key === SESSION_MIGRATION_BOUNDARY_STORAGE_KEY) {
      if (event.newValue !== getStorageValue(event.key)) return;
      const state = readStoredBoundaryState();
      if (state?.marker === lastSeenBoundary) {
        if (requestSessionActive && !boundaryHasActiveCredentials(state)) {
          invalidateForExternalBoundary(state.marker);
        } else if (state.migration
          && adoptableMigrationBoundary === state.marker
          && boundaryHasActiveCredentials(state)) {
          requestSessionActive = true;
          adoptableMigrationBoundary = null;
          publishBoundaryEvent();
        }
        return;
      }
      if (state?.migration) {
        const previousMarker = lastSeenBoundary;
        lastSeenBoundary = state.marker;
        initializedForThisTab = true;
        requestSessionActive = boundaryHasActiveCredentials(state);
        adoptableMigrationBoundary = requestSessionActive ? null : state.marker;
        if (previousMarker && previousMarker !== state.marker) {
          removeSessionArtifacts(previousMarker);
        }
        publishBoundaryEvent();
        return;
      }
      invalidateForExternalBoundary(state?.marker || null);
      return;
    }

    if (event.key?.startsWith(SESSION_CREDENTIALS_PREFIX)) {
      const marker = event.key.slice(SESSION_CREDENTIALS_PREFIX.length);
      if (marker !== lastSeenBoundary) return;
      const state = readStoredBoundaryState();
      if (requestSessionActive && !boundaryHasActiveCredentials(state)) {
        invalidateForExternalBoundary(marker);
        return;
      }
      if (marker !== adoptableMigrationBoundary
        || state?.marker !== marker
        || !state.migration
        || !boundaryHasActiveCredentials(state)) return;
      requestSessionActive = true;
      adoptableMigrationBoundary = null;
      publishBoundaryEvent();
      return;
    }

    if (!event.key?.startsWith(SESSION_REVOKED_PREFIX) || !event.newValue) return;
    const marker = event.key.slice(SESSION_REVOKED_PREFIX.length);
    if (marker !== lastSeenBoundary || marker !== readStoredBoundary()) return;
    if (event.newValue !== getStorageValue(event.key)) return;
    invalidateForExternalBoundary(marker);
  });
}

export {
  SESSION_BOUNDARY_EVENT,
  SESSION_BOUNDARY_STORAGE_KEY,
  SESSION_MIGRATION_BOUNDARY_STORAGE_KEY,
  SESSION_CREDENTIALS_PREFIX,
  SESSION_PROFILE_PREFIX,
  SESSION_REVOKED_PREFIX,
};
