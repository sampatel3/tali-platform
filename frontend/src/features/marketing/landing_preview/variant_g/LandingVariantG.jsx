import React, { useCallback, useEffect, useRef, useState } from 'react';
import { LazyMotion, domMax, MotionConfig } from 'motion/react';

import { useReducedMotionSync } from '../../../../shared/motion/previewMotion';
import { VARIANT_G_CSS } from './variantG.styles';
import { VariantGNav, NAV_LINKS } from './VariantGNav';
import { VariantGHero } from './VariantGHero';
import { VariantGFooter } from './VariantGFooter';
import { FunnelSection, FluencySection, ControlSection } from './VariantGSections';

// ---------------------------------------------------------------------------
// VARIANT G — F's "Vivid Purple" look + E's tight, one-screen-per-section
// navigation. Same visual system as F (light theme, purple family, orb glows,
// gradient agent-ON stage, the live AgentScene). What G fixes:
//
//   1. NAV THAT NAVIGATES. Every nav item maps 1:1 to a real section id
//      (Agentic hiring → #g-funnel, AI fluency → #g-fluency, Control →
//      #g-control). Clicking smooth-scrolls to that section's top, landing just
//      below the sticky nav (via each section's scroll-margin-top). Uses
//      Lenis.scrollTo when Lenis is live; falls back to scrollIntoView (with
//      scroll-margin-top) under reduced motion or when Lenis isn't present.
//      An IntersectionObserver scroll-spy emphasises the active link.
//   2. ONE SCREEN PER SECTION. Every top-level section is min-height
//      min(100svh, 900px) with its content vertically centred and copy trimmed,
//      so clicking a nav item shows everything that section needs without
//      further scrolling. The hero is two columns (copy + CTAs | agent stage)
//      so headline and the live stage both fit one screen.
//
// Motion model is unchanged from F: LazyMotion + MotionConfig reducedMotion,
// CSS one-shot <Reveal> entrances, the AgentScene useAnimate timeline. Lenis is
// scoped, dynamically imported, and never initialised under reduced motion.
// ---------------------------------------------------------------------------

const NAV_HEIGHT = 68; // matches .nav-in height in variantG.styles
const SECTION_IDS = NAV_LINKS.map((l) => l.id);

export const LandingVariantG = ({ onNavigate }) => {
  const reduced = useReducedMotionSync();
  const lenisRef = useRef(null);
  const [active, setActive] = useState('');

  // Lenis smooth-scroll — scoped, dynamically imported, held in a ref so the nav
  // + hero can drive lenis.scrollTo. Never initialised under reduced motion.
  useEffect(() => {
    if (reduced || typeof window === 'undefined') return undefined;
    let lenis;
    let rafId = 0;
    let cancelled = false;
    import('lenis')
      .then(({ default: Lenis }) => {
        if (cancelled) return;
        lenis = new Lenis({ lerp: 0.1, wheelMultiplier: 1, smoothWheel: true });
        lenisRef.current = lenis;
        const loop = (time) => {
          lenis.raf(time);
          rafId = window.requestAnimationFrame(loop);
        };
        rafId = window.requestAnimationFrame(loop);
      })
      .catch(() => {
        /* Lenis is an enhancement only — native scroll still works without it. */
      });
    return () => {
      cancelled = true;
      if (rafId) window.cancelAnimationFrame(rafId);
      if (lenis) lenis.destroy();
      lenisRef.current = null;
    };
  }, [reduced]);

  // Smooth-scroll a section to just below the sticky nav. Both paths rely on the
  // section's CSS `scroll-margin-top: 68px` for the nav offset: Lenis subtracts
  // scrollMarginTop itself (so we pass NO extra offset — a -NAV_HEIGHT here would
  // double-count and land the section ~136px down), and native scrollIntoView
  // honours scroll-margin-top directly.
  const scrollToSection = useCallback((id) => {
    if (typeof document === 'undefined') return;
    const el = document.getElementById(id);
    if (!el) return;
    if (lenisRef.current) {
      lenisRef.current.scrollTo(el);
    } else {
      el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
    }
  }, [reduced]);

  // Scroll-spy — emphasise the nav link for the section in view. Guarded so a
  // missing IntersectionObserver (older/SSR) simply leaves no link active.
  useEffect(() => {
    if (typeof window === 'undefined' || typeof IntersectionObserver === 'undefined') {
      return undefined;
    }
    const targets = SECTION_IDS.map((id) => document.getElementById(id)).filter(Boolean);
    if (!targets.length) return undefined;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setActive(entry.target.id);
        });
      },
      { rootMargin: `-${NAV_HEIGHT + 40}px 0px -55% 0px`, threshold: 0 },
    );
    targets.forEach((t) => io.observe(t));
    return () => io.disconnect();
  }, []);

  return (
    <LazyMotion features={domMax} strict>
      <MotionConfig reducedMotion="user">
        <div className="lvg" data-brand="taali">
          <style>{VARIANT_G_CSS}</style>

          <VariantGNav onNavigate={onNavigate} onSection={scrollToSection} active={active} />
          <VariantGHero onNavigate={onNavigate} />
          <FunnelSection reduced={reduced} />
          <FluencySection reduced={reduced} />
          <ControlSection reduced={reduced} onNavigate={onNavigate} />
          <VariantGFooter onSection={scrollToSection} />
        </div>
      </MotionConfig>
    </LazyMotion>
  );
};

export default LandingVariantG;
