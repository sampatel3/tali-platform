import { describe, expect, it } from 'vitest';

import { hydrateMessage, stitchToolResults } from './conversationHistory';

describe('paginated conversation history', () => {
  it('keeps a leading tool result until the older page supplies its tool call', () => {
    const recent = stitchToolResults([
      hydrateMessage({
        id: 42,
        role: 'user',
        content: [{
          type: 'tool_result',
          tool_use_id: 'tool_1',
          content: '{"applications":[7]}',
        }],
      }),
      hydrateMessage({
        id: 43,
        role: 'assistant',
        content: [{ type: 'text', text: 'One strong match.' }],
      }),
    ]);

    expect(recent[0]).toMatchObject({ id: 'm_42', _historyHidden: true });

    const combined = stitchToolResults([
      hydrateMessage({
        id: 41,
        role: 'assistant',
        content: [{
          type: 'tool_use',
          id: 'tool_1',
          name: 'search_applications',
          input: { query: 'platform' },
        }],
      }),
      ...recent,
    ]);

    expect(combined).toHaveLength(2);
    expect(combined[0]).toMatchObject({
      id: 'm_41',
      parts: [{
        type: 'tool_call',
        toolCallId: 'tool_1',
        result: { applications: [7] },
        status: 'complete',
      }],
    });
    expect(combined.some((message) => message._historyHidden)).toBe(false);
  });
});
