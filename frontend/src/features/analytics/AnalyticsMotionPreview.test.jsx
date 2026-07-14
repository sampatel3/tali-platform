import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AnalyticsMotionPreview, ANALYTICS_SHOWCASE } from './AnalyticsMotionPreview';

// Smoke coverage for the public /analytics-preview Motion mockup:
//  - renders logged-out on the authored ANALYTICS_SHOWCASE fixture (no auth, no
//    APIs) with the real AgentHeader + pulse band + the real OutcomesTab
//    (funnel + advance→hire + by-role),
//  - switches to the real prop-driven FleetView and keeps the Outcomes pulse
//    out of that operational view,
//  - under prefers-reduced-motion the pulse KPI tickers show their final value
//    immediately rather than counting up from 0.

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

const renderPreview = () => render(
  <MemoryRouter>
    <AnalyticsMotionPreview />
  </MemoryRouter>,
);

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AnalyticsMotionPreview (/analytics-preview)', () => {
  it('renders logged-out with the pulse band and the real Outcomes view', () => {
    setReducedMotion(false);
    renderPreview();

    // Real AgentHeader (the title; "Analytics" also appears in the breadcrumb).
    expect(screen.getAllByText(/Analytics/).length).toBeGreaterThan(0);
    // Pulse band label (pulse-only, not echoed by OutcomesTab).
    expect(screen.getByText('Taught')).toBeInTheDocument();
    // Real OutcomesTab — funnel conversion card + its static counts.
    expect(screen.getByText(/Funnel conversion/i)).toBeInTheDocument();
    expect(screen.getByText('1,240')).toBeInTheDocument();
    // Preview switcher chip.
    expect(screen.getByText(/PREVIEW · Analytics on Motion/i)).toBeInTheDocument();
  });

  it('renders the final KPI values under prefers-reduced-motion', () => {
    setReducedMotion(true);
    renderPreview();

    // The "Decisions" ticker lands on its final fixture value immediately.
    expect(screen.getByText(String(ANALYTICS_SHOWCASE.summary.kpis.decisions_made.current))).toBeInTheDocument();
  });

  it('switches to the redesigned real Fleet view without the Outcomes pulse', () => {
    setReducedMotion(true);
    renderPreview();

    fireEvent.click(screen.getByRole('tab', { name: /Agent fleet/i }));

    expect(screen.getByText('Active agents')).toBeInTheDocument();
    expect(screen.getByText('Needs review')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Agents' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Recent activity' })).toBeInTheDocument();
    expect(screen.getAllByText('AI Engineer').length).toBeGreaterThan(0);
    expect(screen.getByText(/Working · Reviewing 3 candidates/i)).toBeInTheDocument();
    expect(screen.getByText(/Paused · monthly budget reached/i)).toBeInTheDocument();
    expect(screen.queryByText('Taught')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /View decision log/i }));
    expect(screen.getByRole('tab', { name: /Decision log/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('heading', { name: 'Agents' })).not.toBeInTheDocument();
  });
});
