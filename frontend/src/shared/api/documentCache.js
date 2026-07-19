// Module-scoped cache for CV / job-spec blob URLs.
//
// Why a global cache instead of React Query: the binary stays out of
// the cache key system (we never want to serialise PDF bytes through
// any state normaliser), and we want hover-prefetch from the candidate
// list to seed the same store the detail view reads from.
//
// Entries expire after CACHE_TTL_MS so the next view re-presigns the
// S3 URL before the 10-minute window the backend grants.

import { candidates as candidatesApi } from './candidatesClient';
import { roles as rolesApi } from './rolesClient';

const CACHE_TTL_MS = 9 * 60 * 1000; // 9 min — under the 10-min S3 presign window
const MAX_CACHE_BYTES = 25 * 1024 * 1024;
const MAX_CACHE_ENTRIES = 12;

const blobCache = new Map(); // key -> { url, mime, fetchedAt, lastAccessedAt, size }
const inflight = new Map();  // key -> Promise<{ url, mime }>
let cacheGeneration = 0;

const cacheKey = ({ applicationId, candidateId, docType }) =>
  applicationId
    ? `app:${applicationId}:${docType}`
    : `cand:${candidateId}:${docType}`;

const isFresh = (entry) => entry && (Date.now() - entry.fetchedAt) < CACHE_TTL_MS;

const revokeEntry = (entry) => {
  if (!entry?.url) return;
  try { URL.revokeObjectURL(entry.url); } catch {}
};

const pruneCache = () => {
  const now = Date.now();
  for (const [key, entry] of blobCache.entries()) {
    if ((now - entry.fetchedAt) >= CACHE_TTL_MS) {
      revokeEntry(entry);
      blobCache.delete(key);
    }
  }

  let totalBytes = [...blobCache.values()].reduce((sum, entry) => sum + Number(entry.size || 0), 0);
  const oldestFirst = [...blobCache.entries()]
    .sort(([, a], [, b]) => Number(a.lastAccessedAt || a.fetchedAt) - Number(b.lastAccessedAt || b.fetchedAt));
  while ((blobCache.size > MAX_CACHE_ENTRIES || totalBytes > MAX_CACHE_BYTES) && oldestFirst.length) {
    const [key, entry] = oldestFirst.shift();
    if (!blobCache.has(key)) continue;
    revokeEntry(entry);
    blobCache.delete(key);
    totalBytes -= Number(entry.size || 0);
  }
};

const fetchBlob = async ({ applicationId, candidateId, docType }) => {
  // Cache-bust query param. Railway/Vercel edge happily caches 4xx
  // responses by default — once a row 410'd before being refetched,
  // the cached 410 served forever even after the DB was fixed. The
  // backend now sends Cache-Control: no-store but already-cached
  // entries persist; the timestamp param sidesteps them entirely.
  const cfg = { params: { _: Date.now() } };
  const res = applicationId
    ? await rolesApi.downloadApplicationDocument(applicationId, docType, cfg)
    : await candidatesApi.downloadDocument(candidateId, docType, cfg);
  if (!res) return null;
  const mime = res?.headers?.['content-type'] || undefined;
  const blob = res.data instanceof Blob
    ? res.data
    : new Blob([res.data], mime ? { type: mime } : undefined);
  const url = URL.createObjectURL(blob);
  return { url, mime, size: blob.size };
};

export const getCachedDocumentBlob = async ({ applicationId, candidateId, docType = 'cv' }) => {
  if (!applicationId && !candidateId) return null;
  const key = cacheKey({ applicationId, candidateId, docType });

  const cached = blobCache.get(key);
  if (isFresh(cached)) {
    cached.lastAccessedAt = Date.now();
    return { url: cached.url, mime: cached.mime, fromCache: true };
  }
  if (cached) {
    // Stale — drop the URL so the GC can reclaim the underlying blob.
    revokeEntry(cached);
    blobCache.delete(key);
  }

  if (inflight.has(key)) return inflight.get(key);

  const generation = cacheGeneration;
  const promise = (async () => {
    try {
      const result = await fetchBlob({ applicationId, candidateId, docType });
      if (result && generation === cacheGeneration) {
        const now = Date.now();
        blobCache.set(key, { ...result, fetchedAt: now, lastAccessedAt: now });
        pruneCache();
      } else if (result) {
        // Logout/account switch happened while the download was in flight.
        revokeEntry(result);
      }
      return result;
    } finally {
      inflight.delete(key);
    }
  })();
  inflight.set(key, promise);
  return promise;
};

// Fire-and-forget prefetch — used on hover. Swallows errors silently
// because the user hasn't explicitly asked to see the file yet.
export const prefetchDocumentBlob = ({ applicationId, candidateId, docType = 'cv' }) => {
  if (!applicationId && !candidateId) return;
  const key = cacheKey({ applicationId, candidateId, docType });
  if (isFresh(blobCache.get(key)) || inflight.has(key)) return;
  void getCachedDocumentBlob({ applicationId, candidateId, docType }).catch(() => {});
};

export const invalidateDocumentBlob = ({ applicationId, candidateId, docType = 'cv' }) => {
  const key = cacheKey({ applicationId, candidateId, docType });
  const entry = blobCache.get(key);
  if (entry) {
    revokeEntry(entry);
    blobCache.delete(key);
  }
};

export const clearDocumentCache = () => {
  cacheGeneration += 1;
  for (const entry of blobCache.values()) revokeEntry(entry);
  blobCache.clear();
  // Promises cannot be cancelled here, but the generation guard above keeps
  // their results from repopulating the cache after an account switch.
  inflight.clear();
};

if (typeof window !== 'undefined') {
  window.addEventListener('auth:logout', clearDocumentCache);
}
