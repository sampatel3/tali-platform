import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentPromptPreviewPage } from './AgentPromptPreviewPage';

const renderAt = (search = '') => render(
  <MemoryRouter initialEntries={[`/agent-prompts-preview${search}`]}>
    <AgentPromptPreviewPage />
  </MemoryRouter>,
);

const stubMatchMedia = (reduced = false) => {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query.includes('prefers-reduced-motion') ? reduced : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

describe('AgentPromptPreviewPage', () => {
  beforeEach(() => stubMatchMedia(false));

  it.each([
    ['?v=a', 'Conversation turn chat mockup'],
    ['?v=b', 'Needs-you tray chat mockup'],
    ['?v=c', 'Composer reply mode chat mockup'],
    ['?v=d', 'Compact run ledger chat mockup'],
  ])('renders the requested concept at %s', (search, label) => {
    renderAt(search);
    expect(screen.getByLabelText(label)).toBeTruthy();
    expect(screen.getAllByRole('tab')).toHaveLength(4);
  });

  it('falls back to the conversational concept for an unknown variant', () => {
    renderAt('?v=unknown');
    expect(screen.getByLabelText('Conversation turn chat mockup')).toBeTruthy();
    expect(screen.getByRole('tab', { name: /Conversation turn/i })).toHaveAttribute('aria-selected', 'true');
  });

  it('switches concepts with the accessible Motion tab control', () => {
    renderAt('?v=a');
    fireEvent.click(screen.getByRole('tab', { name: /Composer reply mode/i }));
    expect(screen.getByLabelText('Composer reply mode chat mockup')).toBeTruthy();
    expect(screen.getByLabelText('Write a different answer')).toBeTruthy();
  });

  it('opens the explanation with the shared measured disclosure', () => {
    renderAt('?v=a');
    fireEvent.click(screen.getByRole('button', { name: 'Explain stop' }));
    expect(screen.getByText(/worker restarted while processing the remaining candidates/i)).toBeTruthy();
    expect(document.querySelector('.motion-disclosure')).toBeTruthy();
  });

  it('moves from request to working to an auditable receipt', () => {
    vi.useFakeTimers();
    try {
      renderAt('?v=d');
      fireEvent.click(screen.getByRole('button', { name: 'Retry unfinished work' }));
      expect(screen.getByText(/Retrying from the last saved candidate/i)).toBeTruthy();
      act(() => vi.advanceTimersByTime(1200));
      expect(screen.getByText(/Retry started/i)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('settles essential content immediately with reduced motion enabled', () => {
    stubMatchMedia(true);
    renderAt('?v=b');
    expect(screen.getByText(/Retry the unfinished work\?/i)).toBeTruthy();
    expect(screen.getByText('Reduced motion on')).toBeTruthy();
  });
});
