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

const hasChangedDecisionType = (canonical, source) => {
  const canonicalType = String(canonical?.decision_type || '').trim();
  const sourceType = String(source?.decision_type || '').trim();
  return Boolean(canonicalType && sourceType && canonicalType !== sourceType);
};

export const hasApprovalReceiptCausalTransition = (canonical, source) => (
  hasChangedQueueNote(canonical, source)
  || hasChangedDecisionType(canonical, source)
);

export const createApprovalReceiptOverlay = (source, row) => ({ row, source });

// Object identity is not a server generation: an older poll or another Home
// filter cache can return a different pending object after the mutation starts.
// Keep every accepted/unknown receipt read-only across those pending snapshots.
// Approval workers stamp genuine retries with a non-empty changed resolution
// note (including provider retry exhaustion, whose copy has no common prefix);
// only that durable marker may restore the action without a page reload.
export const reconcileProcessingDecision = (canonical, overlay) => {
  const sameDecision = Boolean(
    overlay?.row
    && canonical
    && Number(canonical.id) === Number(overlay.row.id)
  );
  // Retain the receipt tombstone after processing is observed. A slower stale
  // pending response must not unlock the row after a newer poll proved that the
  // mutation was accepted; the canonical processing payload remains visible.
  if (sameDecision && canonical.status === 'processing') {
    return { decision: canonical, overlay };
  }
  const keepOverlay = Boolean(
    sameDecision
    && isActionableDecision(canonical)
    && !hasApprovalReceiptCausalTransition(canonical, overlay.source)
  );
  return keepOverlay
    ? { decision: asProcessingDecision(canonical, overlay.row), overlay }
    : { decision: canonical, overlay: null };
};
