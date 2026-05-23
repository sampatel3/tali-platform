// Tiny in-memory stale-while-revalidate cache.
//
// Navigation in the app re-mounts page components, which re-fire every data
// fetch from scratch — so going back to a role you just viewed shows a full
// spinner again. This cache lets a page paint instantly from the last known
// payload while it revalidates in the background.
//
// Scope: module-level (per tab/session). Not persisted. Cleared on logout so
// a different account never sees a previous user's cached data.

const store = new Map();

// Default freshness window. `readCache` still returns stale entries (the
// caller decides whether to show-then-revalidate); this only flags staleness.
const DEFAULT_TTL_MS = 60_000;

export const readCache = (key) => {
  const entry = store.get(key);
  if (!entry) return null;
  return { data: entry.data, isStale: Date.now() - entry.ts > (entry.ttl ?? DEFAULT_TTL_MS) };
};

export const writeCache = (key, data, ttl = DEFAULT_TTL_MS) => {
  store.set(key, { data, ts: Date.now(), ttl });
};

export const dropCache = (key) => {
  store.delete(key);
};

export const clearCache = () => {
  store.clear();
};

// Drop every entry whose key starts with the given prefix (e.g. all role
// workspaces). Useful after a mutation that could affect a family of keys.
export const dropCacheByPrefix = (prefix) => {
  for (const key of store.keys()) {
    if (key.startsWith(prefix)) store.delete(key);
  }
};

if (typeof window !== 'undefined') {
  // The httpClient interceptor dispatches this on 401; clear cached data so a
  // re-login (possibly as another user) starts clean.
  window.addEventListener('auth:logout', clearCache);
}
