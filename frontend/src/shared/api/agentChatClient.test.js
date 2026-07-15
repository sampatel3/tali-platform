import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('./httpClient', () => ({ default: { get: mocks.get } }));

import { agentChat } from './agentChatClient';

describe('agentChat client', () => {
  beforeEach(() => mocks.get.mockReset());

  it('caps timeline reads so the chat dock cannot load forever', () => {
    agentChat.getTimeline(26);

    expect(mocks.get).toHaveBeenCalledWith(
      '/agent-chat/conversations/26/timeline',
      { timeout: 10000 },
    );
  });
});
