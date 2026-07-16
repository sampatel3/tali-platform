import { describe, expect, it } from 'vitest';

import {
  REQUISITION_ATTACHMENT_ACCEPT,
  requisitionAttachmentErrorDetail,
} from './requisitionAttachments';

describe('shared requisition attachment policy', () => {
  it('exposes the complete backend-compatible picker allowlist', () => {
    expect(REQUISITION_ATTACHMENT_ACCEPT.split(',')).toEqual([
      '.txt', '.text', '.vtt', '.srt', '.md', '.markdown', '.pdf', '.docx',
      '.jpg', '.jpeg', '.png', '.gif', '.webp',
    ]);
    expect(REQUISITION_ATTACHMENT_ACCEPT).not.toContain('image/*');
  });
});

describe('attachment request error details', () => {
  it.each([413, 415, 422])('surfaces a plain detail for safe status %s', (status) => {
    const error = { response: { status, data: { detail: '  Use a supported attachment.  ' } } };

    expect(requisitionAttachmentErrorDetail(error, 'Try again.')).toBe(
      'Use a supported attachment.',
    );
  });

  it.each([400, 409, 500])('keeps the fallback for non-attachment status %s', (status) => {
    const error = { response: { status, data: { detail: 'Internal detail' } } };

    expect(requisitionAttachmentErrorDetail(error, 'Try again.')).toBe('Try again.');
  });

  it('does not expose structured validation payloads', () => {
    const error = { response: { status: 422, data: { detail: [{ msg: 'Internal shape' }] } } };

    expect(requisitionAttachmentErrorDetail(error, 'Try again.')).toBe('Try again.');
  });
});
