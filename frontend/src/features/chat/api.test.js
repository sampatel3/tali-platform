import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../../shared/api/httpClient', () => ({
  default: { get: mocks.get },
}));

import { conversationsApi } from './api';

describe('conversationsApi transcript pagination', () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockResolvedValue({ data: { messages: [] } });
  });

  it('keeps the legacy get call and adds bounded cursor parameters when provided', async () => {
    await conversationsApi.get(12);
    expect(mocks.get).toHaveBeenNthCalledWith(1, '/taali-chat/conversations/12');

    await conversationsApi.get(12, { before: 401, limit: 60 });
    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      '/taali-chat/conversations/12',
      { params: { before: 401, limit: 60 } },
    );
  });
});
