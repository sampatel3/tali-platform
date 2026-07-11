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

  it('renders variant C (the light switch) by default without crashing', () => {
    const { container } = renderAt('');
    // Variant C OFF-state hero copy.
    expect(screen.getByText(/Hiring runs on guesswork\./i)).toBeTruthy();
    // The agent switch is present as a role="switch".
    expect(screen.getByRole('switch')).toBeTruthy();
    // Light theme root is mounted (the scoped `.lvc` shell).
    expect(container.querySelector('.lvc')).toBeTruthy();
    // The hero motif is the dot lattice (falling CVs removed) — ~120 dots.
    expect(container.querySelector('.lvc-lattice')).toBeTruthy();
    expect(container.querySelectorAll('.lvc-dot').length).toBeGreaterThan(100);
    // The falling-CV field is gone.
    expect(container.querySelector('.lvc-cvfield')).toBeNull();
    expect(container.querySelector('.lvc-cv')).toBeNull();
    // Switcher chip renders with C active.
    expect(
      screen.getByRole('button', { name: /C · Turn hiring on/i }).getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('carries the denser variant-C content and the "How it works" CTA (vision removed)', () => {
    const { container } = renderAt('');
    // Secondary hero CTA is now "How it works" (was "Read the vision").
    expect(screen.getByRole('button', { name: /How it works/i })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Read the vision/i })).toBeNull();
    // The removed vision section copy is gone.
    expect(screen.queryByText(/Where this goes\./i)).toBeNull();
    // Pipeline stages carry real copy plus the new mono micro-detail lines.
    expect(screen.getByText('Hand back')).toBeTruthy();
    expect(screen.getByText(/Plugs into your ATS/i)).toBeTruthy();
    expect(screen.getByText(/workable · bullhorn · api/i)).toBeTruthy();
    // The densified pipeline stats row.
    expect(screen.getByText(/battle-tested before use/i)).toBeTruthy();
    expect(screen.getByText(/webcams or lockdown browsers/i)).toBeTruthy();
    // All five Ds render as information rows. ("Discernment" also labels the
    // trap dial, so allow more than one match.)
    ['Delegation', 'Description', 'Discernment', 'Diligence', 'Deliverable'].forEach((d) => {
      expect(screen.getAllByText(d).length).toBeGreaterThan(0);
    });
    // Each D now carries a concrete evidence sentence.
    expect(screen.getByText(/plausible-but-wrong suggestion/i)).toBeTruthy();
    // Copy broadened beyond engineering: "works with AI", never "ship/build with AI".
    expect(screen.getByText(/how well\s+this person actually works with AI/i)).toBeTruthy();
    expect(screen.getByText(/Everyone works with AI now\./i)).toBeTruthy();
    expect(screen.getByText(/they can work with AI, they can\./i)).toBeTruthy();
    expect(container.textContent).not.toMatch(/ship with AI/i);
    expect(container.textContent).not.toMatch(/build with AI/i);
    // The old "Watch it decide in three minutes." CTA band is replaced by the
    // production landing's closing treatment.
    expect(screen.queryByText(/Watch it decide in three minutes\./i)).toBeNull();
    expect(screen.getByText(/Ready to put the agent to work\?/i)).toBeTruthy();
    // The production footer is reused — three link columns + contact.
    expect(screen.getByRole('button', { name: /Book a demo/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Developers \/ API/i })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /hello@taali\.ai/i }).length).toBeGreaterThan(0);
    // LeetCode mention is gone from the problem section.
    expect(screen.queryByText(/LeetCode/i)).toBeNull();
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
