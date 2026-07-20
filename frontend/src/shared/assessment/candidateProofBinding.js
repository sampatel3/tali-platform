const PROOF_DATABASE_NAME = 'taali-assessment-proof';
const PROOF_DATABASE_VERSION = 1;
const PROOF_STORE_NAME = 'candidate-proof-keys';
const ACTIVE_RUNTIME_STORAGE_KEY = 'taali.assessment.runtime.recovery.v1';
const PROOF_ALGORITHM = Object.freeze({ name: 'ECDSA', namedCurve: 'P-256' });
const PROOF_HASH = 'SHA-256';
const KEY_ID_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const KEY_COORDINATE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const TOKEN_RECOVERY_TTL_MS = 12 * 60 * 60 * 1000;

const bindingPromises = new Map();

export class CandidateProofUnavailableError extends Error {
  constructor(message = 'This browser cannot securely bind the assessment to this device. Use a current browser in a normal browsing window, then reopen the invite link.') {
    super(message);
    this.name = 'CandidateProofUnavailableError';
    this.code = 'CANDIDATE_PROOF_UNAVAILABLE';
  }
}

const browserCrypto = () => {
  const cryptoApi = globalThis.crypto || globalThis.window?.crypto;
  if (!cryptoApi?.subtle || !cryptoApi?.getRandomValues) {
    throw new CandidateProofUnavailableError('Secure browser cryptography is unavailable. Use a current version of Chrome, Edge, Firefox, or Safari, then reopen the invite link.');
  }
  return cryptoApi;
};

const browserIndexedDb = () => {
  const indexedDbApi = globalThis.indexedDB || globalThis.window?.indexedDB;
  if (!indexedDbApi?.open) {
    throw new CandidateProofUnavailableError('Secure browser key storage is unavailable. Leave private browsing mode or use a current browser, then reopen the invite link.');
  }
  return indexedDbApi;
};

export const toBase64Url = (bytes) => {
  let binary = '';
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  const encode = globalThis.btoa || globalThis.window?.btoa;
  if (typeof encode !== 'function') {
    throw new CandidateProofUnavailableError();
  }
  return encode(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
};

const utf8 = (value) => new TextEncoder().encode(String(value));

const sha256 = async (value) => browserCrypto().subtle.digest(
  PROOF_HASH,
  value instanceof Uint8Array ? value : utf8(value),
);

const toLowerHex = (bytes) => Array.from(new Uint8Array(bytes))
  .map((byte) => byte.toString(16).padStart(2, '0'))
  .join('');

const tokenBindingId = async (inviteToken) => (
  `v1.${toBase64Url(await sha256(`taali-assessment:${inviteToken}`))}`
);

const keyIdForPublicJwk = async (publicJwk) => {
  const material = `${publicJwk.crv}.${publicJwk.x}.${publicJwk.y}`;
  return toBase64Url(await sha256(material));
};

const openProofDatabase = () => new Promise((resolve, reject) => {
  let request;
  try {
    request = browserIndexedDb().open(PROOF_DATABASE_NAME, PROOF_DATABASE_VERSION);
  } catch (error) {
    reject(new CandidateProofUnavailableError());
    return;
  }
  request.onupgradeneeded = () => {
    const database = request.result;
    if (!database.objectStoreNames.contains(PROOF_STORE_NAME)) {
      database.createObjectStore(PROOF_STORE_NAME, { keyPath: 'id' });
    }
  };
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(new CandidateProofUnavailableError());
  request.onblocked = () => reject(new CandidateProofUnavailableError('Secure browser key storage is blocked. Close other assessment tabs and reopen the invite link.'));
});

const proofStoreRequest = async (mode, operation) => {
  const database = await openProofDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(PROOF_STORE_NAME, mode);
      const store = transaction.objectStore(PROOF_STORE_NAME);
      let request;
      try {
        request = operation(store);
      } catch (error) {
        reject(error);
        return;
      }
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('IndexedDB request failed'));
      transaction.onabort = () => reject(transaction.error || new Error('IndexedDB transaction aborted'));
    });
  } finally {
    database.close();
  }
};

const readProofRecord = (id) => proofStoreRequest('readonly', (store) => store.get(id));
const addProofRecord = (record) => proofStoreRequest('readwrite', (store) => store.add(record));
const deleteProofRecord = (id) => proofStoreRequest('readwrite', (store) => store.delete(id));

const isValidPublicJwk = (jwk) => (
  jwk?.kty === 'EC'
  && jwk?.crv === 'P-256'
  && KEY_COORDINATE_PATTERN.test(String(jwk.x || ''))
  && KEY_COORDINATE_PATTERN.test(String(jwk.y || ''))
  && !Object.prototype.hasOwnProperty.call(jwk, 'd')
);

const isValidPrivateSigningKey = (key) => (
  key?.type === 'private'
  && key?.extractable === false
  && key?.algorithm?.name === 'ECDSA'
  && key?.algorithm?.namedCurve === 'P-256'
  && Array.isArray(key?.usages)
  && key.usages.includes('sign')
);

const validateProofRecord = async (record, id) => {
  if (
    record?.version !== 1
    || record?.id !== id
    || !isValidPrivateSigningKey(record.privateKey)
    || !isValidPublicJwk(record.publicJwk)
    || !KEY_ID_PATTERN.test(String(record.keyId || ''))
  ) {
    throw new CandidateProofUnavailableError('The secure assessment key in this browser is invalid. Reopen the original invite link in the browser where you started the assessment.');
  }
  const expectedKeyId = await keyIdForPublicJwk(record.publicJwk);
  if (expectedKeyId !== record.keyId) {
    throw new CandidateProofUnavailableError('The secure assessment key in this browser is invalid. Reopen the original invite link in the browser where you started the assessment.');
  }
  return record;
};

const generateProofRecord = async (id) => {
  const cryptoApi = browserCrypto();
  let keyPair;
  try {
    // WebCrypto keeps the private key non-extractable. For asymmetric key
    // generation, browsers still expose the public key for export so it can
    // be registered with the server; private key material is never exported.
    keyPair = await cryptoApi.subtle.generateKey(PROOF_ALGORITHM, false, ['sign', 'verify']);
  } catch (error) {
    throw new CandidateProofUnavailableError();
  }
  if (!isValidPrivateSigningKey(keyPair?.privateKey)) {
    throw new CandidateProofUnavailableError();
  }

  let publicJwk;
  try {
    publicJwk = await cryptoApi.subtle.exportKey('jwk', keyPair.publicKey);
  } catch (error) {
    throw new CandidateProofUnavailableError();
  }
  if (!isValidPublicJwk(publicJwk)) {
    throw new CandidateProofUnavailableError();
  }

  // Keep only the public fields the backend accepts. In particular, never
  // persist or transmit a private `d` member even if a browser is non-compliant.
  const safePublicJwk = {
    kty: 'EC',
    crv: 'P-256',
    x: publicJwk.x,
    y: publicJwk.y,
  };
  return {
    version: 1,
    id,
    privateKey: keyPair.privateKey,
    publicJwk: safePublicJwk,
    keyId: await keyIdForPublicJwk(safePublicJwk),
    createdAt: Date.now(),
  };
};

const createOrReadProofRecord = async (id) => {
  let existing;
  try {
    existing = await readProofRecord(id);
  } catch (error) {
    throw new CandidateProofUnavailableError();
  }
  if (existing) return validateProofRecord(existing, id);

  const generated = await generateProofRecord(id);
  try {
    await addProofRecord(generated);
    return generated;
  } catch (error) {
    // Two tabs may generate concurrently. `add` deliberately does not
    // overwrite: the first durable key wins and every other tab reloads it.
    if (error?.name !== 'ConstraintError') {
      throw new CandidateProofUnavailableError();
    }
    const winner = await readProofRecord(id);
    return validateProofRecord(winner, id);
  }
};

export const getOrCreateCandidateProofBinding = async (inviteToken) => {
  const token = String(inviteToken || '').trim();
  if (!token) throw new CandidateProofUnavailableError('The assessment invite is missing. Reopen the original invite link.');
  let pending = bindingPromises.get(token);
  if (!pending) {
    pending = tokenBindingId(token)
      .then(createOrReadProofRecord)
      .catch((error) => {
        bindingPromises.delete(token);
        throw error instanceof CandidateProofUnavailableError
          ? error
          : new CandidateProofUnavailableError();
      });
    bindingPromises.set(token, pending);
  }
  const record = await pending;
  return {
    keyId: record.keyId,
    publicJwk: { ...record.publicJwk },
  };
};

const serializeProofBody = (body) => {
  if (body === undefined || body === null) return '';
  if (typeof body === 'string') return body;
  try {
    return JSON.stringify(body);
  } catch (error) {
    throw new CandidateProofUnavailableError('The assessment request could not be signed securely.');
  }
};

export const buildCandidateProofCanonicalMessage = async ({
  method,
  pathAndQuery,
  body,
  timestamp,
  nonce,
}) => {
  const bodyHash = toLowerHex(await sha256(serializeProofBody(body)));
  return [
    'v1',
    String(method || '').toUpperCase(),
    String(pathAndQuery || ''),
    bodyHash,
    String(timestamp),
    String(nonce),
  ].join('\n');
};

export const createCandidateProofHeaders = async (
  inviteToken,
  { method, pathAndQuery, body },
) => {
  const token = String(inviteToken || '').trim();
  const binding = await getOrCreateCandidateProofBinding(token);
  const id = await tokenBindingId(token);
  const record = await validateProofRecord(await readProofRecord(id), id);
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonceBytes = new Uint8Array(18);
  browserCrypto().getRandomValues(nonceBytes);
  const nonce = toBase64Url(nonceBytes);
  const canonicalMessage = await buildCandidateProofCanonicalMessage({
    method,
    pathAndQuery,
    body,
    timestamp,
    nonce,
  });

  let signature;
  try {
    signature = await browserCrypto().subtle.sign(
      { name: 'ECDSA', hash: PROOF_HASH },
      record.privateKey,
      utf8(canonicalMessage),
    );
  } catch (error) {
    throw new CandidateProofUnavailableError('The assessment request could not be signed by this browser. Reopen the original invite link in the browser where you started.');
  }
  const signatureBytes = new Uint8Array(signature);
  if (signatureBytes.byteLength !== 64) {
    throw new CandidateProofUnavailableError('This browser returned an unsupported assessment signature format. Use a current browser and reopen the invite link.');
  }
  return {
    'X-Assessment-Key-Id': binding.keyId,
    'X-Assessment-Proof-Timestamp': timestamp,
    'X-Assessment-Proof-Nonce': nonce,
    'X-Assessment-Proof': toBase64Url(signatureBytes),
  };
};

export const rememberCandidateRuntime = (inviteToken, assessmentId) => {
  const token = String(inviteToken || '').trim();
  const id = Number(assessmentId);
  if (!token || !Number.isInteger(id) || id <= 0 || typeof window === 'undefined') {
    throw new CandidateProofUnavailableError('The assessment session could not be retained securely for refresh.');
  }
  try {
    // Tab-scoped only: the invite token is never written to localStorage.
    window.sessionStorage.setItem(ACTIVE_RUNTIME_STORAGE_KEY, JSON.stringify({
      version: 1,
      assessment_id: id,
      invite_token: token,
      expires_at: Date.now() + TOKEN_RECOVERY_TTL_MS,
    }));
  } catch (error) {
    throw new CandidateProofUnavailableError('Tab recovery storage is unavailable. Leave private browsing mode and reopen the invite link.');
  }
};

const readRuntimeRecovery = () => {
  if (typeof window === 'undefined') return null;
  let raw;
  try {
    raw = window.sessionStorage.getItem(ACTIVE_RUNTIME_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    const expiresAt = Number(parsed?.expires_at);
    const assessmentId = Number(parsed?.assessment_id);
    if (
      parsed?.version !== 1
      || !String(parsed?.invite_token || '').trim()
      || !Number.isInteger(assessmentId)
      || assessmentId <= 0
      || !Number.isFinite(expiresAt)
      || expiresAt <= Date.now()
      || expiresAt > Date.now() + TOKEN_RECOVERY_TTL_MS
    ) {
      window.sessionStorage.removeItem(ACTIVE_RUNTIME_STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    try {
      window.sessionStorage.removeItem(ACTIVE_RUNTIME_STORAGE_KEY);
    } catch {
      // A storage failure already means recovery is unavailable.
    }
    return null;
  }
};

export const recoverCandidateRuntimeToken = (assessmentId = null) => {
  const recovery = readRuntimeRecovery();
  if (!recovery) return null;
  if (assessmentId !== null && Number(assessmentId) !== Number(recovery.assessment_id)) return null;
  return recovery.invite_token;
};

export const clearCandidateRuntimeRecovery = (inviteToken = null) => {
  if (typeof window === 'undefined') return;
  const recovery = readRuntimeRecovery();
  if (inviteToken && recovery?.invite_token !== String(inviteToken)) return;
  try {
    window.sessionStorage.removeItem(ACTIVE_RUNTIME_STORAGE_KEY);
  } catch {
    // Best-effort cleanup after the server has accepted final submission.
  }
};

export const clearCandidateProofBinding = async (inviteToken) => {
  const token = String(inviteToken || '').trim();
  if (!token) return;
  bindingPromises.delete(token);
  try {
    await deleteProofRecord(await tokenBindingId(token));
  } catch {
    // Final submission is already authoritative. A storage cleanup failure
    // must not make the submitted screen look unsuccessful.
  }
};

export const scrubCandidateInviteTokenFromUrl = () => {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  if (url.pathname === '/assessment/live' && url.searchParams.has('token')) {
    url.searchParams.delete('token');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  }
};

export const __resetCandidateProofBindingForTests = () => {
  bindingPromises.clear();
};
