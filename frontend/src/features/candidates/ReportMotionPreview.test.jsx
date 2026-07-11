import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ReportMotionPreview } from './ReportMotionPreview';

// Smoke coverage for the public /report-preview Motion mockup:
//  - renders logged-out on the AI_SHOWCASE fixtures (no auth, no APIs) with the
//    real DecisionRail + AssessmentScorecard + CandidateReportView composed the
//    way CandidateStandingReportPage composes them,
//  - under prefers-reduced-motion the report renders its final state (the
//    on-scroll evidence reveal drops to a plain, always-visible wrapper).

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

describe('ReportMotionPreview (/report-preview)', () => {
  it('renders logged-out with the real DecisionRail + 5-Ds scorecard', () => {
    setReducedMotion(false);
    render(<ReportMotionPreview />);

    // Real DecisionRail — candidate identity (also echoed in the report body)
    // + the Taali score label.
    expect(screen.getAllByText('Priya Raman').length).toBeGreaterThan(0);
    expect(screen.getByText('Taali score')).toBeInTheDocument();
    // Real AssessmentScorecard — the 5-Ds spine.
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeInTheDocument();
    // Preview switcher chip.
    expect(screen.getByText(/PREVIEW · Report on Motion/i)).toBeInTheDocument();
  });

  it('renders the final state under prefers-reduced-motion', () => {
    setReducedMotion(true);
    render(<ReportMotionPreview />);

    // The report is fully present (the on-scroll evidence reveal is a plain
    // wrapper under reduced motion, so CandidateReportView is not stuck hidden).
    expect(screen.getAllByText('Priya Raman').length).toBeGreaterThan(0);
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeInTheDocument();
  });
});
