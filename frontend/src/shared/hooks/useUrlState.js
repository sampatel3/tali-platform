import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

// React Router's setSearchParams keeps its `prev` value in a ref that
// only updates on render. Two synchronous calls in the same handler
// (e.g. "change filter + reset page") would both see stale state and
// the second would clobber the first. Reading from window.location at
// update time is the authoritative source of truth.
const readLiveParams = () => {
  if (typeof window === 'undefined') return new URLSearchParams();
  return new URLSearchParams(window.location.search || '');
};

// Read/write a single URL search-param as if it were a useState hook.
// Returns [value, setValue]. setValue(null) or setValue('') removes the
// param. Updates use { replace: true } so filter changes don't pollute
// browser history. No-op when the value is already set.
export function useUrlState(key, defaultValue = '') {
  const [searchParams, setSearchParams] = useSearchParams();
  const value = searchParams.get(key) ?? defaultValue;
  const setValue = useCallback(
    (next) => {
      const params = readLiveParams();
      const normalized = next == null || next === '' ? null : String(next);
      const current = params.get(key);
      if ((normalized ?? '') === (current ?? '')) return;
      if (normalized == null) params.delete(key);
      else params.set(key, normalized);
      setSearchParams(params, { replace: true });
    },
    [key, setSearchParams],
  );
  return [value, setValue];
}

// Same idea but for multi-value filters. Stored as comma-separated.
// Empty array removes the param.
export function useUrlListState(key, defaultValue = []) {
  const [searchParams, setSearchParams] = useSearchParams();
  const value = useMemo(() => {
    const raw = searchParams.get(key);
    if (!raw) return defaultValue;
    return raw.split(',').map((s) => s.trim()).filter(Boolean);
  }, [searchParams, key, defaultValue]);
  const setValue = useCallback(
    (next) => {
      const params = readLiveParams();
      const list = Array.isArray(next) ? next.filter(Boolean) : [];
      const joined = list.join(',');
      const current = params.get(key) || '';
      if (joined === current) return;
      if (list.length === 0) params.delete(key);
      else params.set(key, joined);
      setSearchParams(params, { replace: true });
    },
    [key, setSearchParams],
  );
  return [value, setValue];
}

export default useUrlState;
