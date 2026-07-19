import { describe, expect, it } from 'vitest';

import {
  indexPendingDecisionsByApplication,
  mergeDecisionQueueReceipts,
  withDecisionReceipt,
  withRecordedDecisionReceipt,
} from './jobDecisionQueue';
import { createApprovalReceiptOverlay } from '../../shared/decisions/approvalReceipt';

const source = { id: 42, application_id: 7, status: 'pending' };
const row = { id: 42, application_id: 7, status: 'processing' };
const overlay = createApprovalReceiptOverlay(source, row);

describe('job decision approval receipts', () => {
  it('prefers a processing row when duplicate live decisions share an application', () => {
    const pending = { id: 601, application_id: 2, status: 'pending' };
    const processing = { id: 600, application_id: 2, status: 'processing' };

    expect(indexPendingDecisionsByApplication([pending, processing])[2]).toBe(processing);
    expect(indexPendingDecisionsByApplication([processing, pending])[2]).toBe(processing);
  });

  it('adds a frozen receipt to both local indexes', () => {
    expect(withDecisionReceipt({}, overlay)).toEqual({ 7: row });
    expect(withRecordedDecisionReceipt({}, overlay)).toEqual({ 42: overlay });
  });

  it('preserves a known receipt only over its source generation', () => {
    expect(mergeDecisionQueueReceipts(
      { 7: source },
      { 42: overlay },
    )).toEqual({
      decisions: {
        7: {
          id: 42,
          application_id: 7,
          status: 'processing',
        },
      },
      receipts: { 42: overlay },
    });
  });

  it('releases a known receipt for an explicitly marked worker requeue', () => {
    const canonical = {
      ...source,
      resolution_note: 'Returned to queue: the ATS did not accept the update.',
    };
    expect(mergeDecisionQueueReceipts({ 7: canonical }, { 42: overlay })).toEqual({
      decisions: { 7: canonical },
      receipts: {},
    });
  });

  it('keeps a receipt over a fresh pending object', () => {
    const canonical = { ...source };
    const freshOverlay = createApprovalReceiptOverlay(source, row);
    expect(mergeDecisionQueueReceipts({ 7: canonical }, { 42: freshOverlay })).toEqual({
      decisions: { 7: row },
      receipts: { 42: freshOverlay },
    });
  });

  it('keeps the receipt when the capped poll returns only an actionable sibling', () => {
    const sibling = { id: 43, application_id: 7, status: 'pending' };
    expect(mergeDecisionQueueReceipts({ 7: sibling }, { 42: overlay })).toEqual({
      decisions: { 7: row },
      receipts: { 42: overlay },
    });
  });

  it('retains the receipt after observing canonical processing', () => {
    const canonical = { ...row, accepted_at: '2026-07-19T12:00:00Z' };
    const receipt = createApprovalReceiptOverlay(source, row);

    expect(mergeDecisionQueueReceipts({ 7: canonical }, { 42: receipt })).toEqual({
      decisions: { 7: canonical },
      receipts: { 42: receipt },
    });
  });

  it('releases the overlay after the decision disappears', () => {
    const decisions = {};
    const receipt = createApprovalReceiptOverlay(source, row);
    expect(mergeDecisionQueueReceipts(decisions, { 42: receipt })).toEqual({
      decisions,
      receipts: {},
    });
  });
});
