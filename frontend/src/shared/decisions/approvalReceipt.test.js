import { describe, expect, it } from 'vitest';

import {
  asProcessingDecision,
  createApprovalReceiptOverlay,
  reconcileProcessingDecision,
} from './approvalReceipt';

describe('asProcessingDecision', () => {
  it('merges the accepted receipt while keeping it non-terminal', () => {
    expect(asProcessingDecision(
      { id: 42, status: 'pending', candidate_name: 'Sam Patel' },
      { id: 42, status: 'approved', accepted_at: '2026-07-19T12:00:00Z' },
    )).toEqual({
      id: 42,
      status: 'processing',
      candidate_name: 'Sam Patel',
      accepted_at: '2026-07-19T12:00:00Z',
    });
  });

  it('still freezes when an older server omits a receipt body', () => {
    expect(asProcessingDecision({ id: 42, status: 'pending' }, null)).toEqual({
      id: 42,
      status: 'processing',
    });
  });
});

describe('reconcileProcessingDecision', () => {
  const source = { id: 42, application_id: 7, status: 'pending', reasoning: 'canonical' };
  const row = { id: 42, application_id: 7, status: 'processing' };

  it('keeps a known receipt for its source generation', () => {
    const overlay = createApprovalReceiptOverlay(source, row);
    expect(reconcileProcessingDecision(source, overlay)).toEqual({
      decision: {
        id: 42,
        application_id: 7,
        status: 'processing',
        reasoning: 'canonical',
      },
      overlay,
    });
  });

  it('does not mistake a distinct stale pending object for a worker requeue', () => {
    const canonical = { ...source };
    const overlay = createApprovalReceiptOverlay(source, row);
    expect(reconcileProcessingDecision(
      canonical,
      overlay,
    )).toEqual({
      decision: { ...canonical, status: 'processing' },
      overlay,
    });
  });

  it.each([
    'Returned to queue after an unexpected error. Please try approving it again.',
    "Workable didn't accept the update after several tries. Provider unavailable.",
  ])('releases a receipt for a changed worker requeue note: %s', (resolutionNote) => {
    const canonical = { ...source, resolution_note: resolutionNote };
    expect(reconcileProcessingDecision(
      canonical,
      createApprovalReceiptOverlay(source, row),
    )).toEqual({ decision: canonical, overlay: null });
  });

  it('does not spend a new receipt on an old requeue note copied into a stale poll', () => {
    const priorNote = 'Returned to queue after an unexpected error. Please try approving it again.';
    const reapprovedSource = { ...source, resolution_note: priorNote };
    const canonical = { ...reapprovedSource };
    const nextOverlay = createApprovalReceiptOverlay(reapprovedSource, row);

    expect(reconcileProcessingDecision(canonical, nextOverlay)).toEqual({
      decision: { ...canonical, status: 'processing' },
      overlay: nextOverlay,
    });
  });

  it('keeps the receipt after a canonical processing row is observed', () => {
    const canonical = { ...source, status: 'processing', accepted_at: '2026-07-19T12:00:00Z' };
    const overlay = createApprovalReceiptOverlay(source, row);

    expect(reconcileProcessingDecision(canonical, overlay)).toEqual({
      decision: canonical,
      overlay,
    });
  });

  it('releases a receipt when the same pending decision is reclassified', () => {
    const canonical = { ...source, decision_type: 'advance_to_interview' };
    const reclassifiedSource = { ...source, decision_type: 'send_assessment' };

    expect(reconcileProcessingDecision(
      canonical,
      createApprovalReceiptOverlay(reclassifiedSource, row),
    )).toEqual({ decision: canonical, overlay: null });
  });

  it.each([
    ['a terminal row', { id: 42, status: 'approved' }],
    ['a replacement decision', { id: 43, status: 'pending' }],
    ['no current decision', null],
  ])('releases a receipt for %s', (_label, canonical) => {
    const overlay = createApprovalReceiptOverlay(source, row);
    expect(reconcileProcessingDecision(canonical, overlay)).toEqual({
      decision: canonical,
      overlay: null,
    });
  });
});
