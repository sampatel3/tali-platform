import { useCallback, useEffect, useRef, useState } from 'react';

const itemIdentity = (item, index) => {
  const kind = item?.kind || 'item';
  const id = item?.id ?? item?.needs_input_id ?? item?.decision_id ?? item?.created_at ?? index;
  return `${kind}:${String(id)}`;
};

const isAgentUpdate = (item) => (
  item?.kind === 'needs_input'
  || item?.kind === 'decision'
  || (item?.kind === 'message' && item?.author === 'agent')
);

/**
 * Keeps an agent transcript pinned only while its reader is already near the
 * bottom. Loaded history establishes the baseline silently; later agent-owned
 * items surface the shared update notice when the reader is further up.
 */
export function useAgentUpdateAwareness({
  items,
  ready,
  scopeKey,
  scrollRef,
  threshold = 80,
}) {
  const [hasNewAgentUpdate, setHasNewAgentUpdate] = useState(false);
  const pinnedRef = useRef(true);
  const initializedRef = useRef(false);
  const knownIdsRef = useRef(new Set());

  const scrollToBottom = useCallback(({ force = false } = {}) => {
    const el = scrollRef.current;
    if (!el || (!force && !pinnedRef.current)) return;
    el.scrollTop = el.scrollHeight;
  }, [scrollRef]);

  const jumpToLatest = useCallback(() => {
    pinnedRef.current = true;
    setHasNewAgentUpdate(false);
    scrollToBottom({ force: true });
  }, [scrollToBottom]);

  useEffect(() => {
    pinnedRef.current = true;
    initializedRef.current = false;
    knownIdsRef.current = new Set();
    setHasNewAgentUpdate(false);
  }, [scopeKey]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const onScroll = () => {
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      const pinned = distanceFromBottom <= threshold;
      pinnedRef.current = pinned;
      if (pinned) setHasNewAgentUpdate(false);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [scopeKey, scrollRef, threshold]);

  useEffect(() => {
    if (!ready) return;

    const nextIds = new Set(items.map(itemIdentity));
    if (!initializedRef.current) {
      initializedRef.current = true;
      knownIdsRef.current = nextIds;
      // Opening a thread starts at its latest item, but its existing history is
      // neither announced nor treated as a newly-arrived update.
      pinnedRef.current = true;
      scrollToBottom({ force: true });
      return;
    }

    const previousIds = knownIdsRef.current;
    const unseenAgentItem = items.some(
      (item, index) => !previousIds.has(itemIdentity(item, index)) && isAgentUpdate(item),
    );
    knownIdsRef.current = nextIds;

    if (pinnedRef.current) {
      scrollToBottom({ force: true });
      setHasNewAgentUpdate(false);
    } else if (unseenAgentItem) {
      setHasNewAgentUpdate(true);
    }
  }, [items, ready, scopeKey, scrollToBottom]);

  return {
    hasNewAgentUpdate,
    jumpToLatest,
    scrollToBottom,
  };
}
