import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { JobsMotionPreview } from './JobsMotionPreview';

// Smoke coverage for the public /jobs-preview Motion mockup:
//  - renders logged-out on the JOBS_SHOWCASE fixtures (no auth, no APIs) with
//    the real AgentHeader agent strip and the real role-card grid,
//  - under prefers-reduced-motion the per-role count tickers show their final
//    value immediately (no tween).

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
  window.history.replaceState({}, '', '/');
});

describe('JobsMotionPreview (/jobs-preview)', () => {
  it('renders logged-out with the agent header and the real role cards', () => {
    setReducedMotion(false);
    render(<JobsMotionPreview />);

    // Real AgentHeader agent strip, ON.
    expect(screen.getByText('Agent on')).toBeInTheDocument();
    // Real role cards from the fixture.
    expect(screen.getByText('AI Engineer')).toBeInTheDocument();
    expect(screen.getByText('Senior Data Engineer')).toBeInTheDocument();
    // Real agent-status pill vocabulary (ON with spend) + the pending count.
    expect(screen.getByText('ON · $18/$50')).toBeInTheDocument();
    // Both the global agent strip and the matching role card surface the same
    // actionable count; the header now labels it instead of showing a bare 3.
    expect(screen.getByLabelText('3 items awaiting review')).toHaveTextContent('3 to review');
    expect(screen.getByText(/3 awaiting you/)).toBeInTheDocument();
    const runningCard = screen.getByText('AI Engineer').closest('.job-card');
    const pausedCard = screen.getByText('Senior Data Engineer').closest('.job-card');
    const offCard = screen.getByText('Frontend Engineer').closest('.job-card');
    const inactiveCard = screen.getByText('Staff Backend Engineer').closest('.job-card');
    expect(runningCard).toHaveClass('agent-on');
    expect(pausedCard).not.toHaveClass('not-live');
    expect(offCard).not.toHaveClass('not-live');
    expect(inactiveCard).toHaveClass('not-live');
    // Preview switcher chip.
    expect(screen.getByText(/PREVIEW · Jobs on Motion/i)).toBeInTheDocument();
  });

  it('renders the final state under prefers-reduced-motion', () => {
    setReducedMotion(true);
    render(<JobsMotionPreview />);

    // The role board is present and the per-role count tickers land on their
    // final fixture values immediately (AI Engineer · rejected = 18) rather
    // than counting up from 0.
    expect(screen.getByText('AI Engineer')).toBeInTheDocument();
    expect(screen.getAllByText('18').length).toBeGreaterThan(0);
  });

  it('exposes the dense paused-header state for responsive browser QA', () => {
    window.history.replaceState({}, '', '/jobs-preview?agent=paused');
    setReducedMotion(true);
    render(<JobsMotionPreview />);

    expect(screen.getByLabelText('148 items awaiting review')).toHaveTextContent('148 to review');
    expect(screen.getByLabelText(/Paused by Sam Patel \(you\)/i)).toHaveAttribute(
      'title',
      expect.stringMatching(/only member present at the time/i),
    );
    expect(screen.getByText('AI spend')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^resume$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /turn off agent/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /configure agent/i })).toBeInTheDocument();
  });
});
