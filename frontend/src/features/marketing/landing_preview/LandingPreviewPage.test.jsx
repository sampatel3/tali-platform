import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { LandingPreviewPage } from './LandingPreviewPage';

const renderAt = (search) =>
  render(
    <MemoryRouter initialEntries={[`/landing-preview${search}`]}>
      <LandingPreviewPage onNavigate={vi.fn()} />
    </MemoryRouter>,
  );

// matchMedia is not implemented in jsdom — variant C reads
// prefers-reduced-motion through it, so tests stub it per case.
const stubMatchMedia = (reducedMotion) => {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query.includes('prefers-reduced-motion') ? reducedMotion : false,
    media: query,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

describe('LandingPreviewPage', () => {
  beforeEach(() => {
    stubMatchMedia(false);
  });

  it('renders variant C (the cinematic switch) by default without crashing', () => {
    renderAt('');
    // Variant C OFF-state hero copy.
    expect(screen.getByText(/Hiring runs on guesswork\./i)).toBeTruthy();
    // The agent switch is present as a role="switch".
    expect(screen.getByRole('switch')).toBeTruthy();
    // Switcher chip renders with C active.
    expect(
      screen.getByRole('button', { name: /C · Turn hiring on/i }).getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('flips the agent switch from off to on when clicked', () => {
    vi.useFakeTimers();
    try {
      renderAt('');
      const toggle = screen.getByRole('switch');
      expect(toggle.getAttribute('aria-checked')).toBe('false');
      act(() => {
        fireEvent.click(toggle);
      });
      // Press animation is 200ms, then state flips.
      act(() => {
        vi.advanceTimersByTime(260);
      });
      expect(toggle.getAttribute('aria-checked')).toBe('true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('renders variant C directly in the ON state under prefers-reduced-motion', () => {
    stubMatchMedia(true);
    renderAt('?v=c');
    // Under reduced-motion the switch loads already on (no auto-flip animation).
    const toggle = screen.getByRole('switch');
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    // ON-state switch exposes the "turn hiring off" affordance in its label.
    expect(toggle.getAttribute('aria-label')).toMatch(/Turn hiring off/i);
  });

  it('renders variant A (?v=a) without crashing', () => {
    renderAt('?v=a');
    // Shared hero copy is present in variants A/B.
    expect(screen.getByText(/Hiring has an AI-fluency problem\./i)).toBeTruthy();
    // Variant A exclusive: the how-it-works "Connect your ATS" step.
    expect(screen.getByText(/Connect your ATS/i)).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /A · Value-abstract/i }).getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('renders variant B (?v=b) with the two live artifacts without crashing', () => {
    renderAt('?v=b');
    // Real <ActivityFeed> row — the pending morning-queue decision.
    expect(screen.getByText('Maya Chen')).toBeTruthy();
    // Real <AssessmentScorecard> — the 5 Ds spine.
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeTruthy();
    expect(screen.getByText('Delegation')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /B · One live artifact/i }).getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('falls back to variant C for an unknown ?v value', () => {
    renderAt('?v=zzz');
    expect(screen.getByText(/Hiring runs on guesswork\./i)).toBeTruthy();
  });

  it('switches variants when a chip is clicked', () => {
    renderAt('');
    fireEvent.click(screen.getByRole('button', { name: /B · One live artifact/i }));
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeTruthy();
  });
});
