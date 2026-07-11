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

  it('renders variant C (the light switch) at ?v=c without crashing', () => {
    const { container } = renderAt('?v=c');
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
    const { container } = renderAt('?v=c');
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

  it('flips the variant-C agent switch from off to on when clicked', () => {
    vi.useFakeTimers();
    try {
      renderAt('?v=c');
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

  it('falls back to variant E (the default) for an unknown ?v value', () => {
    const { container } = renderAt('?v=zzz');
    // Variant E is now the default; its conventional B2B shell mounts.
    expect(container.querySelector('.lve')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /E · Watch it work/i }).getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('switches variants when a chip is clicked', () => {
    renderAt('');
    fireEvent.click(screen.getByRole('button', { name: /B · One live artifact/i }));
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeTruthy();
  });

  // ── Variant D · "Watch it work" (pinned/scrubbed) ──────────────────────
  it('renders variant D (the pinned "watch it work" scene) at ?v=d', () => {
    const { container } = renderAt('?v=d');
    // Scoped root + the agent switch mount.
    expect(container.querySelector('.lvd')).toBeTruthy();
    expect(screen.getByRole('switch')).toBeTruthy();
    // D-unique hero CTA + the sourcing counter caption from beat 1.
    expect(screen.getByRole('button', { name: /^Watch it work$/i })).toBeTruthy();
    expect(screen.getByText(/candidates sourced/i)).toBeTruthy();
    // The scene lays out five beats.
    expect(container.querySelectorAll('.lvd-beat').length).toBe(5);
    // Switcher chip renders with D active.
    expect(
      screen.getByRole('button', { name: /D · Watch it work/i }).getAttribute('aria-pressed'),
    ).toBe('true');
    // Broadened copy: "works with AI", never "ship/build with AI".
    expect(screen.getByText(/how well\s+this person actually works with AI/i)).toBeTruthy();
    expect(container.textContent).not.toMatch(/ship with AI/i);
    expect(container.textContent).not.toMatch(/build with AI/i);
    // Production closing + footer reused.
    expect(screen.getByText(/Ready to put the agent to work\?/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Book a demo/i })).toBeTruthy();
  });

  it('flips the variant-D agent switch from off to on when clicked', () => {
    vi.useFakeTimers();
    try {
      renderAt('?v=d');
      const toggle = screen.getByRole('switch');
      expect(toggle.getAttribute('aria-checked')).toBe('false');
      act(() => {
        fireEvent.click(toggle);
      });
      act(() => {
        vi.advanceTimersByTime(260);
      });
      expect(toggle.getAttribute('aria-checked')).toBe('true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('renders variant D as stacked static beats (no pin/scrub) under reduced-motion', () => {
    stubMatchMedia(true);
    const { container } = renderAt('?v=d');
    // Reduced-motion → static mode: switch loads already on, no scrub.
    const toggle = screen.getByRole('switch');
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    expect(container.querySelector('.lvd.is-static')).toBeTruthy();
    // All five beats render as static panels with their caption copy.
    expect(container.querySelectorAll('.lvd-beat').length).toBe(5);
    expect(screen.getByText(/Every candidate, role and JD flows in\./i)).toBeTruthy();
    // Final-state values are shown statically (counter, audit line).
    expect(screen.getByText(/1,240/)).toBeTruthy();
    expect(screen.getByText(/synced to Workable/i)).toBeTruthy();
  });

  // ── Variant E · "Watch it work" (autoplay-on-enter, conventional B2B) ────
  it('renders variant E as the default (?v empty) with its nav, hero and mocks', () => {
    const { container } = renderAt('');
    // Scoped `.lve` root + the E switcher chip is active by default.
    expect(container.querySelector('.lve')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /E · Watch it work/i }).getAttribute('aria-pressed'),
    ).toBe('true');
    // Verbatim hero H1 + sub.
    expect(
      screen.getByText(/Taali is the hiring agent that screens, assesses, and decides/i),
    ).toBeTruthy();
    expect(screen.getByText(/You stay in control of every call that matters/i)).toBeTruthy();
    // Sticky nav: primary "See it live" CTA + "Log in". The desktop nav cluster
    // is display:none in the mobile-first base CSS (jsdom doesn't resolve the
    // min-width media query), so query it with `hidden: true`.
    expect(screen.getAllByRole('button', { name: /See it live/i, hidden: true }).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /^Log in$/i, hidden: true })).toBeTruthy();
    // The subtle agent switch mounts as role="switch".
    expect(screen.getByRole('switch')).toBeTruthy();
    // Signature autoplay mock + a feature band are present.
    expect(screen.getByText(/Watch the agent run your/i)).toBeTruthy();
    expect(screen.getByText(/The agent advises\./i)).toBeTruthy();
    // Broadened copy: "works with AI", never "ship/build with AI".
    expect(container.textContent).not.toMatch(/ship with AI/i);
    expect(container.textContent).not.toMatch(/build with AI/i);
    // Production closing + footer reused.
    expect(screen.getByText(/Ready to put the agent to work\?/i)).toBeTruthy();
    // "Book a demo" appears as the hero secondary CTA and again in the footer.
    expect(screen.getAllByRole('button', { name: /Book a demo/i }).length).toBeGreaterThan(0);
  });

  it('renders variant E at the explicit ?v=e and flips the agent switch off → on', () => {
    vi.useFakeTimers();
    try {
      const { container } = renderAt('?v=e');
      expect(container.querySelector('.lve')).toBeTruthy();
      const toggle = screen.getByRole('switch');
      expect(toggle.getAttribute('aria-checked')).toBe('false');
      act(() => {
        fireEvent.click(toggle);
      });
      // Press animation is 180ms, then state flips.
      act(() => {
        vi.advanceTimersByTime(240);
      });
      expect(toggle.getAttribute('aria-checked')).toBe('true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('grounds variant E in the real product components (v3 — D-style toggle, live feeds, 5-Ds scorecard)', () => {
    const { container } = renderAt('?v=e');
    // FIX 1 (v3) — the clean variant-D pill toggle REPLACES the in-app .abar
    // strip in the hero; its ON/OFF control is still the role="switch" the hero
    // flips to reveal the product card.
    expect(container.querySelector('.lve-switch')).toBeTruthy();
    expect(container.querySelector('.abar')).toBeNull();
    expect(screen.getByRole('switch')).toBeTruthy();
    // FIX 2 / FIX 3 — the embedded real surfaces render, each in a "Live
    // component" frame. The compact AgentDecisionCard (hero) keeps its verdict
    // slab; the real ActivityFeed backs both the morning queue and the screening
    // cohort; the real 5-Ds AssessmentScorecard renders its spine.
    expect(screen.getAllByText('Maya Chen').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Agent recommends/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Your morning queue/i)).toBeTruthy();
    expect(screen.getByText(/Every CV, gated with evidence/i)).toBeTruthy();
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeTruthy();
    expect(screen.getAllByText('Delegation').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Live component/i).length).toBeGreaterThan(0);
  });

  it('renders variant E with final mock states and no autoplay under reduced-motion', () => {
    stubMatchMedia(true);
    const { container } = renderAt('?v=e');
    // Reduced motion → switch loads already ON, no auto-flip.
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true');
    // Mocks render their FINAL composed state: the `[data-animated]` arming
    // attribute (which hides children for the loop) must be absent everywhere,
    // so every mock is legible without any animation running.
    expect(container.querySelector('.lve-mock')).toBeTruthy();
    expect(container.querySelector('.lve-mock[data-animated]')).toBeNull();
  });
});
