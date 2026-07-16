import { useCallback, useEffect, useRef, useState } from 'react';

import { getErrorMessage } from '../candidates/candidatesUiUtils';

export const TASK_CATALOGUE_PAGE_SIZE = 50;
export const TASK_CATALOGUE_SEARCH_DEBOUNCE_MS = 250;

const mergeUniqueTasks = (current, incoming) => {
  const byId = new Map(current.map((task) => [String(task?.id), task]));
  incoming.forEach((task) => {
    if (task?.id != null) byId.set(String(task.id), task);
  });
  return [...byId.values()];
};

export function useTaskCatalogue({ enabled, listTasks }) {
  const [items, setItems] = useState([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const requestSequenceRef = useRef(0);

  useEffect(() => {
    if (!enabled || typeof listTasks !== 'function') return undefined;
    const sequence = (requestSequenceRef.current += 1);
    const normalizedQuery = query.trim();
    setLoading(true);
    setError('');
    setHasMore(false);
    setItems([]);
    const timer = window.setTimeout(async () => {
      try {
        const response = await listTasks({
          ...(normalizedQuery ? { search: normalizedQuery } : {}),
          limit: TASK_CATALOGUE_PAGE_SIZE,
          offset: 0,
        });
        if (sequence !== requestSequenceRef.current) return;
        const page = Array.isArray(response?.data) ? response.data : [];
        setItems(page);
        setHasMore(page.length === TASK_CATALOGUE_PAGE_SIZE);
      } catch (requestError) {
        if (sequence !== requestSequenceRef.current) return;
        setError(getErrorMessage(requestError, 'Reusable tasks could not be loaded.'));
      } finally {
        if (sequence === requestSequenceRef.current) setLoading(false);
      }
    }, normalizedQuery ? TASK_CATALOGUE_SEARCH_DEBOUNCE_MS : 0);
    return () => window.clearTimeout(timer);
  }, [enabled, listTasks, query, retryNonce]);

  const loadMore = useCallback(async () => {
    if (!enabled || typeof listTasks !== 'function' || loading || !hasMore) return;
    const sequence = (requestSequenceRef.current += 1);
    const normalizedQuery = query.trim();
    setLoading(true);
    setError('');
    try {
      const response = await listTasks({
        ...(normalizedQuery ? { search: normalizedQuery } : {}),
        limit: TASK_CATALOGUE_PAGE_SIZE,
        offset: items.length,
      });
      if (sequence !== requestSequenceRef.current) return;
      const page = Array.isArray(response?.data) ? response.data : [];
      setItems((current) => mergeUniqueTasks(current, page));
      setHasMore(page.length === TASK_CATALOGUE_PAGE_SIZE);
    } catch (requestError) {
      if (sequence !== requestSequenceRef.current) return;
      setError(getErrorMessage(requestError, 'More reusable tasks could not be loaded.'));
    } finally {
      if (sequence === requestSequenceRef.current) setLoading(false);
    }
  }, [enabled, hasMore, items.length, listTasks, loading, query]);

  const retry = useCallback(() => setRetryNonce((value) => value + 1), []);

  return {
    items,
    query,
    setQuery,
    loading,
    error,
    hasMore,
    loadMore,
    retry,
  };
}

export default useTaskCatalogue;
