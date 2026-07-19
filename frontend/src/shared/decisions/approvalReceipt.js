export const asProcessingDecision = (decision, receipt) => ({
  ...(decision || {}),
  ...(receipt && typeof receipt === 'object' ? receipt : {}),
  id: receipt?.id ?? decision?.id,
  status: 'processing',
});

const isActionableDecision = (decision) => (
  decision?.status === 'pending' || decision?.status === 'reverted_for_feedback'
);

const hasChangedQueueNote = (canonical, source) => {
  const canonicalNote = String(canonical?.resolution_note || '').trim();
  const sourceNote = String(source?.resolution_note || '').trim();
  return Boolean(canonicalNote) && canonicalNote !== sourceNote;
};

export const createApprovalReceiptOverlay = (
  source,
  row,
  { outcomeUnknown = false } = {},
) => ({ row, source, outcomeUnknown });

// Object identity is not a server generation: an older poll or another Home
// filter cache can return a different pending object after the mutation starts.
// Keep every accepted/unknown receipt read-only across those pending snapshots.
// Approval workers stamp genuine retries with a non-empty changed resolution
// note (including provider retry exhaustion, whose copy has no common prefix);
// only that durable marker may restore the action without a page reload.
export const reconcileProcessingDecision = (canonical, overlay) => {
  const keepOverlay = Boolean(
    overlay?.row
    && canonical
    && Number(canonical.id) === Number(overlay.row.id)
    && isActionableDecision(canonical)
    && !hasChangedQueueNote(canonical, overlay.source),
  );
  return keepOverlay
    ? { decision: asProcessingDecision(canonical, overlay.row), overlay }
    : { decision: canonical, overlay: null };
};
