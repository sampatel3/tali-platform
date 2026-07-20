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

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

describe('documentCache', () => {
  let nextUrl;

  beforeEach(() => {
    window.dispatchEvent(new Event('auth:logout'));
    nextUrl = 0;
    URL.createObjectURL = vi.fn(() => `blob:test-${nextUrl += 1}`);
    URL.revokeObjectURL = vi.fn();
    downloadApplicationDocument.mockReset();
  });

  afterEach(() => clearDocumentCache());

  it('revokes all private blob URLs on account cleanup', async () => {
    downloadApplicationDocument.mockResolvedValue({
      data: new Blob(['private candidate document']),
      headers: { 'content-type': 'application/pdf' },
    });
    await getCachedDocumentBlob({ applicationId: 42, docType: 'cv' });

    window.dispatchEvent(new Event('auth:logout'));

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-1');
  });

  it('cannot repopulate or disrupt the new account cache with a late download', async () => {
    const oldDownload = deferred();
    const newDownload = deferred();
    downloadApplicationDocument
      .mockImplementationOnce(() => oldDownload.promise)
      .mockImplementationOnce(() => newDownload.promise);

    const oldResult = getCachedDocumentBlob({ applicationId: 42, docType: 'cv' });
    clearDocumentCache();
    const newResult = getCachedDocumentBlob({ applicationId: 42, docType: 'cv' });

    oldDownload.resolve({
      data: new Blob(['old account document']),
      headers: { 'content-type': 'application/pdf' },
    });
    await oldResult;
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-1');

    const joinedNewResult = getCachedDocumentBlob({
      applicationId: 42,
      docType: 'cv',
    });
    expect(downloadApplicationDocument).toHaveBeenCalledTimes(2);

    newDownload.resolve({
      data: new Blob(['new account document']),
      headers: { 'content-type': 'application/pdf' },
    });
    await expect(newResult).resolves.toMatchObject({ url: 'blob:test-2' });
    await expect(joinedNewResult).resolves.toMatchObject({ url: 'blob:test-2' });

    await expect(getCachedDocumentBlob({
      applicationId: 42,
      docType: 'cv',
    })).resolves.toMatchObject({ url: 'blob:test-2', fromCache: true });
    expect(downloadApplicationDocument).toHaveBeenCalledTimes(2);
  });
});
