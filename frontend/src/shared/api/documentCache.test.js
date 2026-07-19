import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { downloadApplicationDocument } = vi.hoisted(() => ({
  downloadApplicationDocument: vi.fn(),
}));

vi.mock('./rolesClient', () => ({
  roles: { downloadApplicationDocument },
}));
vi.mock('./candidatesClient', () => ({
  candidates: { downloadDocument: vi.fn() },
}));

import { clearDocumentCache, getCachedDocumentBlob } from './documentCache';

describe('documentCache', () => {
  let nextUrl;

  beforeEach(() => {
    clearDocumentCache();
    nextUrl = 0;
    URL.createObjectURL = vi.fn(() => `blob:test-${nextUrl += 1}`);
    URL.revokeObjectURL = vi.fn();
    downloadApplicationDocument.mockReset();
  });

  afterEach(() => clearDocumentCache());

  it('bounds cached document bytes with least-recently-used eviction', async () => {
    downloadApplicationDocument.mockImplementation(async () => ({
      data: new Blob([new Uint8Array(3 * 1024 * 1024)]),
      headers: { 'content-type': 'application/pdf' },
    }));

    for (let applicationId = 1; applicationId <= 9; applicationId += 1) {
      await getCachedDocumentBlob({ applicationId, docType: 'cv' });
    }
    expect(downloadApplicationDocument).toHaveBeenCalledTimes(9);
    expect(URL.revokeObjectURL).toHaveBeenCalled();

    // 9 × 3 MB exceeds the 25 MB budget, so the oldest entry is fetched again.
    await getCachedDocumentBlob({ applicationId: 1, docType: 'cv' });
    expect(downloadApplicationDocument).toHaveBeenCalledTimes(10);
  });

  it('revokes all private blob URLs on account cleanup', async () => {
    downloadApplicationDocument.mockResolvedValue({
      data: new Blob(['private candidate document']),
      headers: { 'content-type': 'application/pdf' },
    });
    await getCachedDocumentBlob({ applicationId: 42, docType: 'cv' });

    clearDocumentCache();

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-1');
  });
});
