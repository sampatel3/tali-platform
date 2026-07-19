import { describe, expect, it } from 'vitest';

import {
  mergeDecisionQueueReceipts,
  withDecisionReceipt,
  withRecordedDecisionReceipt,
} from './jobDecisionQueue';
import { createApprovalReceiptOverlay } from '../../shared/decisions/approvalReceipt';

const source = { id: 42, application_id: 7, status: 'pending' };
const row = { id: 42, application_id: 7, status: 'processing' };
const overlay = createApprovalReceiptOverlay(source, row);

describe('job decision approval receipts', () => {
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

  it('keeps an unknown receipt over a fresh pending row', () => {
    const canonical = { ...source };
    const unknown = createApprovalReceiptOverlay(source, row, { outcomeUnknown: true });
    expect(mergeDecisionQueueReceipts({ 7: canonical }, { 42: unknown })).toEqual({
      decisions: { 7: row },
      receipts: { 42: unknown },
    });
  });

  it('keeps the receipt when the capped poll returns only an actionable sibling', () => {
    const sibling = { id: 43, application_id: 7, status: 'pending' };
    expect(mergeDecisionQueueReceipts({ 7: sibling }, { 42: overlay })).toEqual({
      decisions: { 7: row },
      receipts: { 42: overlay },
    });
  });

  it.each([
    ['canonical processing', { 7: { ...row } }],
    ['disappearance', {}],
  ])('releases the overlay after %s', (_label, decisions) => {
    const unknown = createApprovalReceiptOverlay(source, row, { outcomeUnknown: true });
    expect(mergeDecisionQueueReceipts(decisions, { 42: unknown })).toEqual({
      decisions,
      receipts: {},
    });
  });
});
