import { beforeEach, describe, expect, it, vi } from 'vitest';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('./httpClient', () => ({
  default: { get },
}));

import { outreach } from './outreachClient';

describe('outreachClient campaign detail pagination', () => {
  beforeEach(() => get.mockReset());

  it('maps a bounded message page to the detail query parameters', () => {
    outreach.getCampaign(42, { limit: 50, offset: 100 });

    expect(get).toHaveBeenCalledWith('/outreach/campaigns/42', {
      params: {
        message_limit: 50,
        message_offset: 100,
      },
    });
  });

  it('keeps the original unparameterized call compatible for other consumers', () => {
    outreach.getCampaign(42);

    expect(get).toHaveBeenCalledWith('/outreach/campaigns/42');
  });
});
