import { useCallback, useEffect, useRef, useState } from 'react';

import { conversationsApi } from './api';
import { hydrateMessage, stitchToolResults } from './conversationHistory';

const HISTORY_PAGE_SIZE = 60;
const EMPTY_HISTORY_PAGE = { hasMore: false, before: null };

// Owns persisted transcript hydration and pagination. The live stream remains
// in useChatStream; this hook only reconciles saved pages around it and rejects
// responses that belong to an older route generation.
export function useConversationHistory({
  conversationId,
  locallyCreated,
  prependHistory,
  reset,
  setHistory,
}) {
  const [hydrating, setHydrating] = useState(false);
  const [hydrateError, setHydrateError] = useState(false);
  const [historyPage, setHistoryPage] = useState(EMPTY_HISTORY_PAGE);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState(false);
  const [hydrateNonce, setHydrateNonce] = useState(0);
  const activeConversationIdRef = useRef(conversationId);
  const historyGenerationRef = useRef(0);
  activeConversationIdRef.current = conversationId;

  useEffect(() => {
    const generation = historyGenerationRef.current + 1;
    historyGenerationRef.current = generation;
    let cancelled = false;

    const clearPageState = () => {
      setHydrateError(false);
      setHistoryPage(EMPTY_HISTORY_PAGE);
      setLoadingOlder(false);
      setOlderError(false);
    };

    const run = async () => {
      if (!conversationId) {
        reset();
        setHydrating(false);
        clearPageState();
        return;
      }
      // A conversation created by the current stream already contains its
      // optimistic assistant turn; the persisted API is intentionally behind.
      if (locallyCreated.current.has(conversationId)) {
        setHydrating(false);
        clearPageState();
        return;
      }

      reset();
      clearPageState();
      setHydrating(true);
      try {
        const data = await conversationsApi.get(conversationId, { limit: HISTORY_PAGE_SIZE });
        if (cancelled) return;
        setHistory(stitchToolResults((data.messages || []).map(hydrateMessage)));
        setHistoryPage({
          hasMore: Boolean(data.has_more && data.next_before != null),
          before: data.next_before ?? null,
        });
      } catch {
        if (!cancelled) setHydrateError(true);
      } finally {
        if (!cancelled) setHydrating(false);
      }
    };

    void run();
    return () => {
      cancelled = true;
      if (historyGenerationRef.current === generation) {
        historyGenerationRef.current += 1;
      }
    };
  }, [conversationId, hydrateNonce, locallyCreated, reset, setHistory]);

  const loadOlder = useCallback(async () => {
    const id = conversationId;
    const before = historyPage.before;
    if (id == null || before == null || loadingOlder) return;
    const generation = historyGenerationRef.current;
    const requestIsCurrent = () => (
      activeConversationIdRef.current === id
      && historyGenerationRef.current === generation
    );

    setLoadingOlder(true);
    setOlderError(false);
    try {
      const data = await conversationsApi.get(id, { before, limit: HISTORY_PAGE_SIZE });
      if (!requestIsCurrent()) return;
      prependHistory((data.messages || []).map(hydrateMessage), stitchToolResults);
      setHistoryPage({
        hasMore: Boolean(data.has_more && data.next_before != null),
        before: data.next_before ?? null,
      });
    } catch {
      if (requestIsCurrent()) setOlderError(true);
    } finally {
      if (requestIsCurrent()) setLoadingOlder(false);
    }
  }, [conversationId, historyPage.before, loadingOlder, prependHistory]);

  const retryHydration = useCallback(() => setHydrateNonce((value) => value + 1), []);

  return {
    hydrateError,
    hydrating,
    historyPage,
    loadOlder,
    loadingOlder,
    olderError,
    retryHydration,
  };
}

export default useConversationHistory;
