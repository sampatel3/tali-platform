import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./httpClient', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import api from './httpClient';
import { roles } from './rolesClient';

describe('graph ingest reconciliation client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get.mockResolvedValue({ data: {} });
  });

  it('omits a cursor on page one and does not send a mutable offset', () => {
    roles.graphIngestReconciliations();

    expect(api.get).toHaveBeenCalledWith(
      '/background-jobs/graph-ingest-reconciliations',
      { params: { limit: 20 } },
    );
  });

  it('forwards the opaque next cursor unchanged', () => {
    const cursor = 'opaque.cursor+/=unchanged';
    roles.graphIngestReconciliations({ limit: 20, cursor });

    expect(api.get).toHaveBeenCalledWith(
      '/background-jobs/graph-ingest-reconciliations',
      { params: { limit: 20, cursor } },
    );
  });
});
