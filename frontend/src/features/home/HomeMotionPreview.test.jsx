import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HomeMotionPreview } from './HomeMotionPreview';

// Smoke coverage for the public /home-preview Motion mockup:
//  - it renders logged-out on fixtures (no auth, no APIs) with the real agent
//    header + the empty "agent off" queue,
//  - the OFF→ON activation flip lights the strip and populates the queue,
//  - under prefers-reduced-motion it renders the final state (no reliance on
//    staggered timers or tickers tweening).

const setReducedMotion = (reduced) => {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: reduced && String(query).includes('prefers-reduced-motion'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('HomeMotionPreview (/home-preview)', () => {
  it('renders logged-out with the agent header, agent OFF, and an empty queue', () => {
    setReducedMotion(false);
    render(<HomeMotionPreview />);

    // Real AgentHeader
    expect(screen.getByText('Good morning')).toBeInTheDocument();
    // Real `.abar` strip, starting OFF
    expect(screen.getByText('Agent off')).toBeInTheDocument();
    // The activation control (real AgentOffActivator) is present
    expect(screen.getByRole('button', { name: /turn on/i })).toBeInTheDocument();
    // Queue is empty before activation
    expect(screen.getByText(/Agent is off\./i)).toBeInTheDocument();
    // Preview badge
    expect(screen.getByText(/PREVIEW · Home on Motion/i)).toBeInTheDocument();
  });

  it('lights the strip and populates the queue on the agent-ON flip', async () => {
    setReducedMotion(false);
    render(<HomeMotionPreview />);

    fireEvent.click(screen.getByRole('button', { name: /turn on/i }));

    // Strip flips to ON and the first pending decision streams into the feed.
    await waitFor(() => expect(screen.getByText('Agent on')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('Maya Chen')).toBeInTheDocument());
  });

  it('renders the final state under prefers-reduced-motion', async () => {
    setReducedMotion(true);
    render(<HomeMotionPreview />);

    // Header renders and the KPI tickers show their FINAL value immediately
    // (no count-up) rather than 0.
    expect(screen.getByText('Good morning')).toBeInTheDocument();
    expect(screen.getByText('103')).toBeInTheDocument();

    // Activation drops straight to the fully populated queue.
    fireEvent.click(screen.getByRole('button', { name: /turn on/i }));
    await waitFor(() => expect(screen.getByText('Agent on')).toBeInTheDocument());
    expect(screen.getByText('Maya Chen')).toBeInTheDocument();
  });
});
