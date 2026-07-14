import { describe, expect, it } from 'vitest';

import { isPublishedRequisition, requisitionStatusLabel } from './RequisitionsPage';

describe('requisition lifecycle labels', () => {
  it('maps backend submitted/applied states to recruiter-facing lifecycle language', () => {
    expect(requisitionStatusLabel('draft')).toBe('Draft');
    expect(requisitionStatusLabel('submitted')).toBe('Ready to publish');
    expect(requisitionStatusLabel('applied')).toBe('Published');
    expect(isPublishedRequisition('submitted')).toBe(false);
    expect(isPublishedRequisition('applied')).toBe(true);
  });

  it('keeps legacy published payloads compatible', () => {
    expect(requisitionStatusLabel('published')).toBe('Published');
    expect(isPublishedRequisition('published')).toBe(true);
  });
});
