import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

vi.mock('./httpClient', () => ({ default: http }));

import { organizations } from './orgClient';

describe('organizations Workable OAuth client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('posts both the authorization code and state to the connect endpoint', () => {
    organizations.connectWorkable('workable-code', 'signed-state');

    expect(http.post).toHaveBeenCalledWith(
      '/organizations/workable/connect',
      { code: 'workable-code', state: 'signed-state' },
    );
  });
});
