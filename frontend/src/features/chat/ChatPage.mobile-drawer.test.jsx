import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';

const mocks = vi.hoisted(() => ({
  clearError: vi.fn(),
  listConversations: vi.fn(),
  listAgents: vi.fn(),
  prependHistory: vi.fn(),
  reset: vi.fn(),
  send: vi.fn(),
  setHistory: vi.fn(),
  stop: vi.fn(),
}));

vi.mock('./api', () => ({
  conversationsApi: {
    list: mocks.listConversations,
    get: vi.fn(),
    remove: vi.fn(),
  },
}));

vi.mock('../../shared/api', () => ({
  agentChat: {
    listConversations: mocks.listAgents,
  },
}));

vi.mock('./useChatStream', () => ({
  default: () => ({
    messages: [],
    isStreaming: false,
    error: null,
    send: mocks.send,
    stop: mocks.stop,
    setHistory: mocks.setHistory,
    prependHistory: mocks.prependHistory,
    reset: mocks.reset,
    clearError: mocks.clearError,
  }),
}));

vi.mock('./AgentConversation', () => ({
  default: ({ onOpenList }) => (
    <main>
      <button type="button" className="cp-mobile-menu" onClick={onOpenList} aria-label="Show agents">
        Show agents
      </button>
    </main>
  ),
}));

import { ChatPage } from './ChatPage';

const originalMatchMedia = window.matchMedia;

const useMobileViewport = () => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn((query) => ({
      matches: query === '(max-width: 900px)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
};

const renderPage = ({ mode = 'ask', path = '/chat' } = {}) => render(
  <TestMemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/chat" element={<ChatPage mode={mode} />} />
      <Route path="/chat/agents" element={<ChatPage mode={mode} />} />
    </Routes>
  </TestMemoryRouter>,
);

beforeEach(() => {
  useMobileViewport();
  mocks.listConversations.mockReset();
  mocks.listAgents.mockReset();
  mocks.listConversations.mockResolvedValue([]);
  mocks.listAgents.mockResolvedValue({ data: { agents: [] } });
});

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: originalMatchMedia,
  });
});

describe('ChatPage mobile navigation drawer', () => {
  it('keeps the closed drawer out of the accessibility and focus trees, then focuses it when opened', async () => {
    renderPage();

    const opener = screen.getByRole('button', { name: 'Show conversations' });
    const drawer = document.getElementById('chat-navigation-drawer');
    expect(drawer).toHaveAttribute('aria-hidden', 'true');
    expect(drawer).toHaveAttribute('inert');
    expect(opener).toHaveAttribute('aria-controls', 'chat-navigation-drawer');
    expect(opener).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(opener);

    await waitFor(() => expect(drawer).toHaveFocus());
    expect(drawer).not.toHaveAttribute('aria-hidden');
    expect(drawer).not.toHaveAttribute('inert');
    expect(drawer).toHaveAttribute('aria-modal', 'true');
    expect(opener).toHaveAttribute('aria-expanded', 'true');
  });

  it('closes on Escape and restores focus to the opening control', async () => {
    renderPage();

    const opener = screen.getByRole('button', { name: 'Show conversations' });
    fireEvent.click(opener);
    await waitFor(() => expect(document.getElementById('chat-navigation-drawer')).toHaveFocus());

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => expect(opener).toHaveFocus());
    expect(opener).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById('chat-navigation-drawer')).toHaveAttribute('inert');
  });

  it('synchronizes the Agents opener with the same drawer', async () => {
    renderPage({ mode: 'agents', path: '/chat/agents' });

    const opener = screen.getByRole('button', { name: 'Show agents' });
    await waitFor(() => {
      expect(opener).toHaveAttribute('aria-controls', 'chat-navigation-drawer');
      expect(opener).toHaveAttribute('aria-expanded', 'false');
    });

    fireEvent.click(opener);
    await waitFor(() => expect(opener).toHaveAttribute('aria-expanded', 'true'));
  });
});
