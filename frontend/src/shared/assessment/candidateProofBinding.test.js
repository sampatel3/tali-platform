import { webcrypto } from 'node:crypto';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const createMemoryIndexedDb = () => {
  const records = new Map();
  let storeCreated = false;

  const makeRequest = (operation) => {
    const request = {};
    queueMicrotask(() => {
      try {
        request.result = operation();
        request.onsuccess?.({ target: request });
      } catch (error) {
        request.error = error;
        request.onerror?.({ target: request });
      }
    });
    return request;
  };

  const store = {
    get: (id) => makeRequest(() => records.get(id)),
    add: (record) => makeRequest(() => {
      if (records.has(record.id)) {
        throw new DOMException('Key already exists', 'ConstraintError');
      }
      records.set(record.id, record);
      return record.id;
    }),
    delete: (id) => makeRequest(() => records.delete(id)),
  };

  const database = {
    objectStoreNames: {
      contains: () => storeCreated,
    },
    createObjectStore: () => {
      storeCreated = true;
      return store;
    },
    transaction: () => ({
      objectStore: () => store,
      error: null,
      onabort: null,
    }),
    close: vi.fn(),
  };

  return {
    records,
    open: () => {
      const request = {};
      queueMicrotask(() => {
        request.result = database;
        if (!storeCreated) request.onupgradeneeded?.({ target: request });
        request.onsuccess?.({ target: request });
      });
      return request;
    },
  };
};

const fromBase64Url = (value) => {
  const padded = String(value).replace(/-/g, '+').replace(/_/g, '/')
    .padEnd(Math.ceil(value.length / 4) * 4, '=');
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
};

describe('candidate proof-of-possession binding', () => {
  let memoryIndexedDb;

  beforeEach(async () => {
    vi.useRealTimers();
    window.sessionStorage.clear();
    window.localStorage.clear();
    Object.defineProperty(globalThis, 'crypto', {
      configurable: true,
      value: webcrypto,
    });
    memoryIndexedDb = createMemoryIndexedDb();
    Object.defineProperty(globalThis, 'indexedDB', {
      configurable: true,
      value: memoryIndexedDb,
    });
    const module = await import('./candidateProofBinding');
    module.__resetCandidateProofBindingForTests();
  });

  it('persists one non-extractable private key without storing the invite token', async () => {
    const module = await import('./candidateProofBinding');
    const first = await module.getOrCreateCandidateProofBinding('invite-secret-a');
    module.__resetCandidateProofBindingForTests();
    const recovered = await module.getOrCreateCandidateProofBinding('invite-secret-a');

    expect(recovered).toEqual(first);
    expect(first.keyId).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(first.publicJwk).toMatchObject({ kty: 'EC', crv: 'P-256' });
    expect(first.publicJwk).not.toHaveProperty('d');
    expect(memoryIndexedDb.records.size).toBe(1);

    const [record] = memoryIndexedDb.records.values();
    expect(record.privateKey.extractable).toBe(false);
    expect(record.privateKey.usages).toEqual(['sign']);
    await expect(webcrypto.subtle.exportKey('jwk', record.privateKey)).rejects.toThrow();
    expect(JSON.stringify({
      id: record.id,
      publicJwk: record.publicJwk,
      keyId: record.keyId,
    })).not.toContain('invite-secret-a');
  });

  it('signs the exact method, path, body hash, timestamp, and nonce', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-20T12:00:00Z'));
    const module = await import('./candidateProofBinding');
    const body = { code: 'print("hello")', selected_file_path: 'src/main.py' };
    const request = {
      method: 'POST',
      pathAndQuery: '/api/v1/assessments/42/execute',
      body,
    };
    const binding = await module.getOrCreateCandidateProofBinding('invite-secret-b');
    const headers = await module.createCandidateProofHeaders('invite-secret-b', request);

    expect(headers).toMatchObject({
      'X-Assessment-Key-Id': binding.keyId,
      'X-Assessment-Proof-Timestamp': '1784548800',
    });
    expect(headers['X-Assessment-Proof-Nonce']).toMatch(/^[A-Za-z0-9_-]{24}$/);
    expect(headers['X-Assessment-Proof']).toMatch(/^[A-Za-z0-9_-]{86}$/);

    const canonical = await module.buildCandidateProofCanonicalMessage({
      ...request,
      timestamp: headers['X-Assessment-Proof-Timestamp'],
      nonce: headers['X-Assessment-Proof-Nonce'],
    });
    expect(canonical.split('\n')).toHaveLength(6);
    expect(canonical).toContain('v1\nPOST\n/api/v1/assessments/42/execute\n');
    expect(canonical.split('\n')[3]).toMatch(/^[a-f0-9]{64}$/);

    const publicKey = await webcrypto.subtle.importKey(
      'jwk',
      binding.publicJwk,
      { name: 'ECDSA', namedCurve: 'P-256' },
      false,
      ['verify'],
    );
    await expect(webcrypto.subtle.verify(
      { name: 'ECDSA', hash: 'SHA-256' },
      publicKey,
      fromBase64Url(headers['X-Assessment-Proof']),
      new TextEncoder().encode(canonical),
    )).resolves.toBe(true);
  });

  it('keeps invite recovery tab-scoped and scrubs it from the live URL', async () => {
    const module = await import('./candidateProofBinding');
    module.rememberCandidateRuntime('invite-secret-c', 42);

    expect(module.recoverCandidateRuntimeToken()).toBe('invite-secret-c');
    expect(module.recoverCandidateRuntimeToken(42)).toBe('invite-secret-c');
    expect(module.recoverCandidateRuntimeToken(43)).toBeNull();
    expect(JSON.stringify({ ...window.localStorage })).not.toContain('invite-secret-c');

    window.history.replaceState(null, '', '/assessment/live?token=invite-secret-c&support=1');
    module.scrubCandidateInviteTokenFromUrl();
    expect(window.location.pathname).toBe('/assessment/live');
    expect(window.location.search).toBe('?support=1');

    module.clearCandidateRuntimeRecovery('invite-secret-c');
    expect(module.recoverCandidateRuntimeToken()).toBeNull();
  });

  it('fails closed with a useful error when durable key storage is unavailable', async () => {
    Object.defineProperty(globalThis, 'indexedDB', {
      configurable: true,
      value: undefined,
    });
    const module = await import('./candidateProofBinding');
    module.__resetCandidateProofBindingForTests();

    await expect(module.getOrCreateCandidateProofBinding('invite-secret-d')).rejects.toMatchObject({
      code: 'CANDIDATE_PROOF_UNAVAILABLE',
    });
    await expect(module.getOrCreateCandidateProofBinding('invite-secret-e')).rejects.toThrow(/key storage|bind/i);
  });
});
