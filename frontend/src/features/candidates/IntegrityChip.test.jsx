import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { IntegrityChip, DimScore } from './CandidateStandingReportPage';

describe('IntegrityChip', () => {
  it('renders nothing on an ok verdict (no signals)', () => {
    const { container } = render(
      <IntegrityChip verdict="ok" trustBand="high" warnings={[]} corroborations={[]} unverifiedEmployers={[]} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when there is no verdict', () => {
    const { container } = render(<IntegrityChip verdict={null} warnings={['x']} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows the chip on a review verdict and expands to the warnings', () => {
    render(
      <IntegrityChip
        verdict="review"
        trustBand="medium"
        warnings={['CV closely mirrors the job description (62% phrase overlap).']}
        corroborations={[]}
        unverifiedEmployers={[]}
      />
    );
    // Chip is collapsed by default — the warning text is not yet shown.
    expect(screen.getByText('Integrity')).toBeTruthy();
    expect(screen.getByText('Medium trust')).toBeTruthy();
    expect(screen.queryByText(/phrase overlap/)).toBeNull();

    // Expand → the canonical warning appears.
    fireEvent.click(screen.getByRole('button', { name: /Integrity/ }));
    expect(screen.getByText(/62% phrase overlap/)).toBeTruthy();
  });

  it('omits the trust-band pill when the band is unknown (never defaults to High)', () => {
    render(
      <IntegrityChip verdict="review" trustBand={null} warnings={['x']} />
    );
    // The chip renders, but no "High trust" false-reassurance pill.
    expect(screen.getByText('Integrity')).toBeTruthy();
    expect(screen.queryByText(/trust/)).toBeNull();
  });

  it('renders the High-trust pill only for an explicit high band', () => {
    render(
      <IntegrityChip verdict="review" trustBand="high" warnings={['x']} />
    );
    expect(screen.getByText('High trust')).toBeTruthy();
  });

  it('surfaces unverified employers and corroborations in the expanded block', () => {
    render(
      <IntegrityChip
        verdict="strong_review"
        trustBand="low"
        warnings={['Timeline: Acme: ends 2018 before it starts 2020']}
        corroborations={['GitHub profile matches the candidate named on the CV.']}
        unverifiedEmployers={['Ghost Corp', 'Faketron']}
      />
    );
    expect(screen.getByText('Low trust')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Integrity/ }));
    expect(screen.getByText(/"Ghost Corp", "Faketron"/)).toBeTruthy();
    expect(screen.getByText(/GitHub profile matches/)).toBeTruthy();
    // Advisory disclaimer — never a verdict.
    expect(screen.getByText(/never changes the match score/)).toBeTruthy();
  });
});

describe('DimScore (5-Ds score ticker)', () => {
  it('reduced-motion → renders the final rounded score immediately, no tween', () => {
    const { container } = render(<DimScore score={82.4} hasSignal reduced />);
    const el = container.querySelector('.mc-overview-dim-score');
    // MotionNumber settles immediately when reduced, so the integer + suffix show
    // at once — no lingering 0 from a mount-only tween.
    expect(el.textContent).toBe('82/100');
  });

  it('renders an em dash and no /100 suffix when the axis has no signal', () => {
    const { container } = render(<DimScore score={0} hasSignal={false} reduced />);
    const el = container.querySelector('.mc-overview-dim-score');
    expect(el.textContent).toBe('—');
  });
});
