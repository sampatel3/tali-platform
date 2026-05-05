// Module-scoped cache for CV / job-spec blob URLs.
//
// Why a global cache instead of React Query: the binary stays out of
// the cache key system (we never want to serialise PDF bytes through
// any state normaliser), and we want hover-prefetch from the candidate
// list to seed the same store the detail view reads from.
//
// Entries expire after CACHE_TTL_MS so the next view re-presigns the
// S3 URL before the 10-minute window the backend grants.

import { roles as rolesApi, candidates as candidatesApi } from './index';

const CACHE_TTL_MS = 9 * 60 * 1000; // 9 min — under the 10-min S3 presign window

const blobCache = new Map(); // key -> { url, mime, fetchedAt, size }
const inflight = new Map();  // key -> Promise<{ url, mime }>

const cacheKey = ({ applicationId, candidateId, docType }) =>
  applicationId
    ? `app:${applicationId}:${docType}`
    : `cand:${candidateId}:${docType}`;

const isFresh = (entry) => entry && (Date.now() - entry.fetchedAt) < CACHE_TTL_MS;

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
    try { URL.revokeObjectURL(cached.url); } catch {}
    blobCache.delete(key);
  }

  if (inflight.has(key)) return inflight.get(key);

  const promise = (async () => {
    try {
      const result = await fetchBlob({ applicationId, candidateId, docType });
      if (result) {
        blobCache.set(key, { ...result, fetchedAt: Date.now() });
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
    try { URL.revokeObjectURL(entry.url); } catch {}
    blobCache.delete(key);
  }
};
