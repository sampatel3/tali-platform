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
import { SESSION_BOUNDARY_EVENT } from '../auth/sessionBoundary';

const CACHE_TTL_MS = 9 * 60 * 1000; // 9 min — under the 10-min S3 presign window

const blobCache = new Map(); // key -> { url, mime, fetchedAt, size }
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
    return { url: cached.url, mime: cached.mime, fromCache: true };
  }
  if (cached) {
    // Stale — drop the URL so the GC can reclaim the underlying blob.
    revokeEntry(cached);
    blobCache.delete(key);
  }

  if (inflight.has(key)) return inflight.get(key);

  const generation = cacheGeneration;
  let promise;
  promise = (async () => {
    try {
      const result = await fetchBlob({ applicationId, candidateId, docType });
      if (result && generation === cacheGeneration) {
        blobCache.set(key, { ...result, fetchedAt: Date.now() });
      } else if (result) {
        // An account switch happened while this private document was loading.
        // Never let the old response repopulate the new session's cache.
        revokeEntry(result);
      }
      return result;
    } finally {
      // A post-logout request for the same document key may now be in flight;
      // the older promise must not remove that newer request from the map.
      if (inflight.get(key) === promise) inflight.delete(key);
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
  // Fetch promises cannot be cancelled here, but their generation guard keeps
  // them from publishing after logout. Clearing permits the new account to
  // request the same logical document without sharing the old promise.
  inflight.clear();
};

if (typeof window !== 'undefined') {
  window.addEventListener('auth:logout', clearDocumentCache);
  window.addEventListener(SESSION_BOUNDARY_EVENT, clearDocumentCache);
}
