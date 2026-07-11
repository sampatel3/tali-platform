import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { LandingPreviewPage } from './LandingPreviewPage';

const renderAt = (search) =>
  render(
    <MemoryRouter initialEntries={[`/landing-preview${search}`]}>
      <LandingPreviewPage onNavigate={vi.fn()} />
    </MemoryRouter>,
  );

describe('LandingPreviewPage', () => {
  it('renders variant A by default without crashing', () => {
    renderAt('');
    // Shared hero copy is present in both variants.
    expect(screen.getByText(/Hiring has an AI-fluency problem\./i)).toBeTruthy();
    // Variant A exclusive: the how-it-works "Connect your ATS" step.
    expect(screen.getByText(/Connect your ATS/i)).toBeTruthy();
    // Switcher chip renders with A active.
    expect(screen.getByRole('button', { name: /A · Value-abstract/i }).getAttribute('aria-pressed')).toBe('true');
  });

  it('renders variant B (?v=b) with the two live artifacts without crashing', () => {
    renderAt('?v=b');
    // Real <ActivityFeed> row — the pending morning-queue decision.
    expect(screen.getByText('Maya Chen')).toBeTruthy();
    // Real <AssessmentScorecard> — the 5 Ds spine.
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeTruthy();
    expect(screen.getByText('Delegation')).toBeTruthy();
    expect(screen.getByRole('button', { name: /B · One live artifact/i }).getAttribute('aria-pressed')).toBe('true');
  });

  it('falls back to variant A for an unknown ?v value', () => {
    renderAt('?v=zzz');
    expect(screen.getByText(/Connect your ATS/i)).toBeTruthy();
  });

  it('switches variants when the chip is clicked', () => {
    renderAt('');
    fireEvent.click(screen.getByRole('button', { name: /B · One live artifact/i }));
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeTruthy();
  });
});
