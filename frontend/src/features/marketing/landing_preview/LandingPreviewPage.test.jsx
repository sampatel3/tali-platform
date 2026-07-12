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

  it('falls back to variant G (the default) for an unknown ?v value', () => {
    const { container } = renderAt('?v=zzz');
    // Variant G ("Combined") is now the default; its scoped `.lvg` shell mounts.
    expect(container.querySelector('.lvg')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /G · Combined/i }).getAttribute('aria-pressed'),
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

  // ── Variant E · v4 — rebuilt to the narrative spine (job-on hero, no repeat) ─
  it('renders variant E at ?v=e with its nav, hero scene and six-section spine', () => {
    const { container } = renderAt('?v=e');
    // Scoped `.lve` root + the E switcher chip is active at ?v=e.
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
    // HERO SCENE — the real jobs-board role card + the Taali agent-ON vocabulary.
    expect(container.querySelector('.lve-hs .job-card')).toBeTruthy();
    expect(screen.getByText('AI Engineer')).toBeTruthy();
    expect(screen.getByText(/AGENT ON/i)).toBeTruthy();
    // PROBLEM — the wedge-setting beat, said once.
    expect(screen.getByText(/Everyone works with AI now\./i)).toBeTruthy();
    expect(screen.getByText(/The CV can't prove it\./i)).toBeTruthy();
    // FUNNEL — shown once: one agent, the five funnel steps.
    expect(screen.getByText(/One agent, your/i)).toBeTruthy();
    ['Source', 'Screen', 'Assess', 'Decide', 'Hand back'].forEach((step) => {
      expect(screen.getByText(step)).toBeTruthy();
    });
    // WEDGE + CONTROL headers.
    expect(screen.getByText(/Measure how people/i)).toBeTruthy();
    expect(screen.getByText(/The agent advises\./i)).toBeTruthy();
    expect(screen.getByText(/You decide\./i)).toBeTruthy();
    // Broadened copy: "works with AI", never "ship/build with AI".
    expect(container.textContent).not.toMatch(/ship with AI/i);
    expect(container.textContent).not.toMatch(/build with AI/i);
    // Production closing + footer reused.
    expect(screen.getByText(/Ready to put the agent to work\?/i)).toBeTruthy();
    // "Book a demo" appears as the hero secondary CTA and again in the footer.
    expect(screen.getAllByRole('button', { name: /Book a demo/i }).length).toBeGreaterThan(0);
  });

  it('renders variant E at ?v=e with the live job-on hero scene and no manual toggle', () => {
    const { container } = renderAt('?v=e');
    expect(container.querySelector('.lve')).toBeTruthy();
    // v4 replaces the old hero agent toggle with an autoplay scene — no
    // role="switch", no ported pill toggle, and never the in-app `.abar` strip.
    expect(screen.queryByRole('switch')).toBeNull();
    expect(container.querySelector('.lve-switch')).toBeNull();
    expect(container.querySelector('.abar')).toBeNull();
    // The hero scene mounts the real role card + agent-ON pill.
    expect(container.querySelector('.lve-hs')).toBeTruthy();
    expect(container.querySelector('.lve-hs .job-agent-pill.is-on')).toBeTruthy();
  });

  it('grounds variant E in the real product components (job card, atoms, 5-Ds scorecard, decision card)', () => {
    const { container } = renderAt('?v=e');
    // Maya Chen threads the hero lane, the funnel scene and the control glimpse.
    expect(screen.getAllByText('Maya Chen').length).toBeGreaterThan(0);
    // The control section embeds the REAL AgentDecisionCard (its recommendation
    // slab), and the wedge embeds the REAL 5-Ds AssessmentScorecard — each in a
    // "Live component" frame.
    expect(screen.getAllByText(/Agent recommends/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/SCORECARD · THE 5 Ds/i)).toBeTruthy();
    expect(screen.getAllByText('Delegation').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Live component/i).length).toBeGreaterThan(0);
    // Real decision-outcome atoms (VerdictPill "Advance") appear in the scenes.
    expect(screen.getAllByText('Advance').length).toBeGreaterThan(0);
  });

  it('renders variant E with final scene states (never armed) under reduced-motion', () => {
    stubMatchMedia(true);
    const { container } = renderAt('?v=e');
    // No manual switch in v4.
    expect(screen.queryByRole('switch')).toBeNull();
    // Scenes render their FINAL composed state: the `data-armed` arming attribute
    // (which hides the animatable children for the timeline) is never set under
    // reduced motion, so every scene is legible without any animation running.
    expect(container.querySelector('.lve-hs')).toBeTruthy();
    expect(container.querySelector('.lve-hs[data-armed]')).toBeNull();
    expect(container.querySelector('.lve-fn[data-armed]')).toBeNull();
    // The hero role card sits in its settled agent-ON state; the replay
    // affordance is suppressed under reduced motion.
    expect(container.querySelector('.lve-hs-card.agent-on')).toBeTruthy();
    expect(container.querySelector('.lve-hs-replay')).toBeNull();
  });

  // ── Variant F · "Vivid Purple" design handoff ───────────────────────────
  it('renders variant F at ?v=f with its scoped shell, nav, hero scene and sections', () => {
    const { container } = renderAt('?v=f');
    // Scoped `.lvf` root + the F switcher chip is active at ?v=f.
    expect(container.querySelector('.lvf')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /F · Vivid/i }).getAttribute('aria-pressed'),
    ).toBe('true');
    // Sticky nav: brand wordmark + the primary "See it live" CTA + "Log in".
    expect(screen.getByRole('button', { name: /^Log in$/i })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /See it live/i }).length).toBeGreaterThan(0);
    // Verbatim hero eyebrow + H1 + lede (split across grad-text spans).
    expect(screen.getByText(/AGENT-NATIVE HIRING/i)).toBeTruthy();
    expect(screen.getByText(/The hiring agent that screens, assesses, and/i)).toBeTruthy();
    expect(screen.getByText(/decides — with you\./i)).toBeTruthy();
    expect(screen.getByText(/You stay in control of every call that matters\./i)).toBeTruthy();
    // HERO AGENT SCENE — the OFF→ON job card on its gradient stage.
    expect(container.querySelector('.lvf .stage .job-card')).toBeTruthy();
    expect(screen.getByText('AI Engineer')).toBeTruthy();
    // Problem beat (verbatim, purple family — never red).
    expect(screen.getByText(/Everyone works with AI now\./i)).toBeTruthy();
    expect(screen.getByText(/You need to see the real work\./i)).toBeTruthy();
    // Funnel — the five steps, said once.
    ['Source', 'Screen', 'Assess', 'Decide', 'Hand back'].forEach((step) => {
      expect(screen.getByText(step)).toBeTruthy();
    });
    expect(screen.getByText(/One agent,/i)).toBeTruthy();
    // Control block + the 5 Ds + proof + close, all present.
    expect(screen.getByText(/The agent advises\./i)).toBeTruthy();
    expect(screen.getByText(/Measure how people/i)).toBeTruthy();
    ['Delegation', 'Description', 'Discernment', 'Diligence', 'Deliverable'].forEach((d) => {
      expect(screen.getByText(d)).toBeTruthy();
    });
    expect(screen.getByText(/webcams or lockdown browsers/i)).toBeTruthy();
    expect(screen.getByText(/Ready to put the agent to work\?/i)).toBeTruthy();
    // Footer contact.
    expect(screen.getByText(/hello@taali\.ai/i)).toBeTruthy();
    // Purple family only — never red/amber/green vocabulary in the reject path.
    expect(screen.getByText('Tariq Al-Ahmad')).toBeTruthy();
    expect(screen.getByText('Reject')).toBeTruthy();
  });

  it('renders variant F at ?v=f with the armed autoplay hero scene (replay affordance present)', () => {
    const { container } = renderAt('?v=f');
    expect(container.querySelector('.lvf')).toBeTruthy();
    // Armed scene: data-armed marks the stage so the timeline can reveal rows;
    // the OFF-state pill + Replay affordance render under normal motion.
    expect(container.querySelector('.lvf .stage[data-armed]')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Replay/i })).toBeTruthy();
    // The decision-lane candidates thread the scene (Maya also appears in the
    // control glimpse card, so allow more than one match).
    expect(screen.getAllByText('Maya Chen').length).toBeGreaterThan(0);
    expect(screen.getByText('Jordan Patel')).toBeTruthy();
  });

  it('renders variant F in its settled ON state (never armed, no replay) under reduced-motion', () => {
    stubMatchMedia(true);
    const { container } = renderAt('?v=f');
    // Reduced motion → the scene shows its settled ON state: the job card is
    // is-on, the stage is NOT armed (rows visible with no timeline), and the
    // replay affordance is suppressed.
    expect(container.querySelector('.lvf .job-card.is-on')).toBeTruthy();
    expect(container.querySelector('.lvf .stage[data-armed]')).toBeNull();
    expect(screen.queryByRole('button', { name: /Replay/i })).toBeNull();
    // The agent-ON pill renders (not the OFF variant).
    expect(container.querySelector('.lvf .agent-pill:not(.off)')).toBeTruthy();
    // Key copy still present without any animation.
    expect(screen.getByText(/decides — with you\./i)).toBeTruthy();
  });

  // ── Variant G · Combined — F's vivid look + E's tight section-per-viewport ─
  it('renders variant G as the default (?v empty) with its scoped shell, two-column hero and sections', () => {
    const { container } = renderAt('');
    // Scoped `.lvg` root + the G switcher chip is active by default.
    expect(container.querySelector('.lvg')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /G · Combined/i }).getAttribute('aria-pressed'),
    ).toBe('true');
    // Two-column hero: the copy column + the agent stage column both mount, with
    // the verbatim H1 + grad-text accent and both CTAs.
    expect(container.querySelector('.lvg .heroC-grid .heroC-copy')).toBeTruthy();
    expect(container.querySelector('.lvg .heroC-stage-col .stage .job-card')).toBeTruthy();
    expect(screen.getByText(/The hiring agent that screens, assesses, and/i)).toBeTruthy();
    expect(screen.getByText(/decides — with you\./i)).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /See it live/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Book a demo/i }).length).toBeGreaterThan(0);
    // Funnel — the five steps, said once.
    ['Source', 'Screen', 'Assess', 'Decide', 'Hand back'].forEach((step) => {
      expect(screen.getByText(step)).toBeTruthy();
    });
    expect(screen.getByText(/One agent,/i)).toBeTruthy();
    // The 5 Ds scorecard, the control destination, proof + close, footer.
    expect(screen.getByText(/Measure how people/i)).toBeTruthy();
    ['Delegation', 'Description', 'Discernment', 'Diligence', 'Deliverable'].forEach((d) => {
      expect(screen.getByText(d)).toBeTruthy();
    });
    expect(screen.getByText(/The agent advises\./i)).toBeTruthy();
    // The closing CTA now lives inside the Control section (Proof was dropped).
    expect(screen.getByText(/Ready to put the agent to work\?/i)).toBeTruthy();
    expect(screen.getByText(/hello@taali\.ai/i)).toBeTruthy();
    // Purple family only — the reject path never uses red vocabulary.
    expect(screen.getByText('Tariq Al-Ahmad')).toBeTruthy();
    expect(screen.getByText('Reject')).toBeTruthy();
    // Broadened copy: "works with AI", never "ship/build with AI".
    expect(container.textContent).not.toMatch(/ship with AI/i);
    expect(container.textContent).not.toMatch(/build with AI/i);
  });

  it('wires every variant-G nav anchor to a matching section (nav href targets resolve)', () => {
    const { container } = renderAt('?v=g');
    // The core requirement: each center nav link points at a section that exists.
    const links = Array.from(container.querySelectorAll('.lvg .nav-links a'));
    expect(links.length).toBe(3);
    const ids = links.map((a) => (a.getAttribute('href') || '').replace('#', ''));
    expect(ids).toEqual(['g-funnel', 'g-fluency', 'g-control']);
    ids.forEach((id) => {
      expect(container.querySelector(`#${id}`)).toBeTruthy();
    });
    // The standalone Proof section is gone (its CTA now closes Control).
    expect(container.querySelector('#g-proof')).toBeNull();
    // The brand + hero anchor to the top section.
    expect(container.querySelector('.lvg .brand[href="#g-top"]')).toBeTruthy();
    expect(container.querySelector('#g-top')).toBeTruthy();
    // Every mapped section is a one-screen `.section-vp` band.
    ['g-funnel', 'g-fluency', 'g-control'].forEach((id) => {
      expect(container.querySelector(`.section-vp#${id}`)).toBeTruthy();
    });
  });

  it('renders variant G in its settled ON state (never armed, no replay) under reduced-motion', () => {
    stubMatchMedia(true);
    const { container } = renderAt('?v=g');
    expect(container.querySelector('.lvg .job-card.is-on')).toBeTruthy();
    expect(container.querySelector('.lvg .stage[data-armed]')).toBeNull();
    expect(screen.queryByRole('button', { name: /Replay/i })).toBeNull();
    expect(container.querySelector('.lvg .agent-pill:not(.off)')).toBeTruthy();
  });
});
