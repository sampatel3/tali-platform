import { useCallback, useEffect, useState } from 'react';

import { requisitionApi } from './api';

const PAGE_SIZE = 25;

export const useRequisitionList = (setError) => {
  const [briefs, setBriefs] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  const loadList = useCallback(async ({ append = false, offset = 0 } = {}) => {
    if (append) setLoadingMore(true);
    try {
      const list = await requisitionApi.list({ limit: PAGE_SIZE, offset });
      const page = Array.isArray(list) ? list : [];
      setBriefs((current) => {
        if (!append) return page;
        const seen = new Set(current.map((brief) => Number(brief.id)));
        return [...current, ...page.filter((brief) => !seen.has(Number(brief.id)))];
      });
      setHasMore(page.length >= PAGE_SIZE);
    } catch {
      setError('Could not load job drafts.');
    } finally {
      setListLoading(false);
      setLoadingMore(false);
    }
  }, [setError]);

  useEffect(() => { void loadList(); }, [loadList]);

  const loadMore = useCallback(() => {
    if (!loadingMore && hasMore) {
      void loadList({ append: true, offset: briefs.length });
    }
  }, [briefs.length, hasMore, loadList, loadingMore]);

  const patchListRow = useCallback((id, patch) => {
    if (id == null || !patch) return;
    setBriefs((current) => current.map((brief) => (brief.id === id
      ? {
          ...brief,
          ...(patch.title !== undefined ? { title: patch.title } : {}),
          ...(patch.status !== undefined ? { status: patch.status } : {}),
          ...(patch.completeness !== undefined ? { completeness: patch.completeness } : {}),
        }
      : brief)));
  }, []);

  return {
    briefs,
    hasMore,
    listLoading,
    loadingMore,
    loadList,
    loadMore,
    patchListRow,
  };
};

export default useRequisitionList;
