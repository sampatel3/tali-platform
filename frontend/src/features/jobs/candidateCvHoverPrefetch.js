import { prefetchDocumentBlob } from '../../shared/api/documentCache';

// A recruiter sweeping the cursor down a long table should not fire a
// presigned-S3 PDF download for every crossed row. Wait for hover intent and
// cap concurrent prefetches so a held-down scroll cannot queue dozens at once.
const HOVER_INTENT_MS = 200;
const HOVER_PREFETCH_MAX = 3;
let hoverPrefetchActive = 0;

export const makeCandidateCvHoverPrefetch = () => {
  let timer = null;
  const start = (applicationId) => {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      timer = null;
      if (hoverPrefetchActive >= HOVER_PREFETCH_MAX) return;
      hoverPrefetchActive += 1;
      Promise.resolve(prefetchDocumentBlob({ applicationId, docType: 'cv' }))
        .catch(() => {})
        .finally(() => {
          hoverPrefetchActive = Math.max(0, hoverPrefetchActive - 1);
        });
    }, HOVER_INTENT_MS);
  };
  const cancel = () => {
    if (timer) {
      window.clearTimeout(timer);
      timer = null;
    }
  };
  return { start, cancel };
};
