import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getConversation: vi.fn(),
  isStreaming: false,
  listConversations: vi.fn(),
  messages: [{
    id: 'm_61',
    role: 'user',
    parts: [{ type: 'text', text: 'Recent message' }],
  }],
  navigate: vi.fn(),
  prependHistory: vi.fn(),
  reset: vi.fn(),
  routeConversationId: '7',
  setHistory: vi.fn(),
  setSearchParams: vi.fn(),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
  useParams: () => (
    mocks.routeConversationId == null
      ? {}
      : { conversationId: mocks.routeConversationId }
  ),
  useSearchParams: () => [new URLSearchParams(), mocks.setSearchParams],
}));

vi.mock('./api', () => ({
  conversationsApi: {
    get: mocks.getConversation,
    list: mocks.listConversations,
    remove: vi.fn(),
  },
}));

vi.mock('./useChatStream', () => ({
  default: () => ({
    messages: mocks.messages,
    isStreaming: mocks.isStreaming,
    error: null,
    send: vi.fn(),
    stop: vi.fn(),
    setHistory: mocks.setHistory,
    prependHistory: mocks.prependHistory,
    reset: mocks.reset,
    clearError: vi.fn(),
  }),
}));

vi.mock('./Thread', () => ({
  default: ({ hasOlder, loadingOlder, olderError, onLoadOlder }) => {
    const label = olderError
      ? 'Try again'
      : loadingOlder
        ? 'Loading older messages…'
        : 'Load older messages';
    return (
      <div data-testid="thread">
        {hasOlder || olderError ? (
          <button type="button" disabled={loadingOlder} onClick={onLoadOlder}>
            {label}
          </button>
        ) : null}
      </div>
    );
  },
}));

vi.mock('./Sidebar', async () => {
  const { forwardRef } = await vi.importActual('react');
  return {
    default: forwardRef(function MockSidebar(_props, ref) {
      return <div ref={ref} />;
    }),
  };
});
vi.mock('./EmptyState', () => ({ default: () => null }));
vi.mock('./ConfirmDialog', () => ({ default: () => null }));
vi.mock('./AgentConversation', () => ({ default: () => null }));
vi.mock('../../shared/chat', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    ChatComposer: () => null,
    ChatMessage: ({ children }) => <div>{children}</div>,
    ThinkingDots: () => <span>Loading</span>,
  };
});
vi.mock('../../shared/api', () => ({
  agentChat: { listConversations: vi.fn() },
}));
vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

import ChatPage from './ChatPage';

describe('ChatPage transcript pagination', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.routeConversationId = '7';
    mocks.isStreaming = false;
    mocks.messages = [{
      id: 'm_61',
      role: 'user',
      parts: [{ type: 'text', text: 'Recent message' }],
    }];
    mocks.listConversations.mockResolvedValue([]);
  });

  it('refreshes the sidebar once on a streaming-to-idle transition', async () => {
    mocks.getConversation.mockResolvedValue({
      messages: [],
      has_more: false,
      next_before: null,
    });

    const { rerender } = render(<ChatPage />);
    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalled());
    mocks.listConversations.mockClear();

    rerender(<ChatPage />);
    expect(mocks.listConversations).not.toHaveBeenCalled();

    mocks.isStreaming = true;
    rerender(<ChatPage />);
    expect(mocks.listConversations).not.toHaveBeenCalled();

    mocks.isStreaming = false;
    rerender(<ChatPage />);
    await waitFor(() => expect(mocks.listConversations).toHaveBeenCalledTimes(1));
  });

  it('hydrates the recent page and explicitly prepends the requested older page', async () => {
    mocks.getConversation
      .mockResolvedValueOnce({
        messages: [{
          id: 61,
          role: 'user',
          content: [{ type: 'text', text: 'Recent message' }],
        }],
        has_more: true,
        next_before: 61,
      })
      .mockResolvedValueOnce({
        messages: [{
          id: 1,
          role: 'user',
          content: [{ type: 'text', text: 'Oldest message' }],
        }],
        has_more: false,
        next_before: null,
      });

    render(<ChatPage />);

    await waitFor(() => {
      expect(mocks.getConversation).toHaveBeenNthCalledWith(1, 7, { limit: 60 });
    });
    expect(mocks.setHistory).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'm_61' }),
    ]);

    fireEvent.click(await screen.findByRole('button', { name: 'Load older messages' }));

    await waitFor(() => {
      expect(mocks.getConversation).toHaveBeenNthCalledWith(2, 7, {
        before: 61,
        limit: 60,
      });
    });
    expect(mocks.prependHistory).toHaveBeenCalledWith(
      [expect.objectContaining({ id: 'm_1' })],
      expect.any(Function),
    );
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Load older messages' })).toBeNull();
    });
  });

  it('ignores an older-page response from a previous visit to the same route', async () => {
    let resolveStaleOlder;
    const staleOlder = new Promise((resolve) => {
      resolveStaleOlder = resolve;
    });
    mocks.getConversation
      .mockResolvedValueOnce({
        messages: [{ id: 41, role: 'user', content: [] }],
        has_more: true,
        next_before: 41,
      })
      .mockReturnValueOnce(staleOlder)
      .mockResolvedValueOnce({
        messages: [{ id: 200, role: 'user', content: [] }],
        has_more: false,
        next_before: null,
      })
      .mockResolvedValueOnce({
        messages: [{ id: 42, role: 'user', content: [] }],
        has_more: true,
        next_before: 42,
      });

    const { rerender } = render(<ChatPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Load older messages' }));
    await waitFor(() => expect(mocks.getConversation).toHaveBeenCalledTimes(2));

    mocks.routeConversationId = '8';
    rerender(<ChatPage />);
    await waitFor(() => {
      expect(mocks.getConversation).toHaveBeenNthCalledWith(3, 8, { limit: 60 });
    });

    mocks.routeConversationId = '7';
    rerender(<ChatPage />);
    await waitFor(() => {
      expect(mocks.getConversation).toHaveBeenNthCalledWith(4, 7, { limit: 60 });
    });

    await act(async () => {
      resolveStaleOlder({
        messages: [{ id: 1, role: 'user', content: [] }],
        has_more: false,
        next_before: null,
      });
      await staleOlder;
    });
    expect(mocks.prependHistory).not.toHaveBeenCalled();
  });
});
