import React, { useCallback, useEffect, useRef, useState } from 'react';
import { LazyMotion, domAnimation, MotionConfig } from 'motion/react';

import { useReducedMotion } from './motion';
import { VARIANT_E_CSS } from './variantE.styles';
import { VariantENav } from './VariantENav';
import { ClosingCta, ProductionFooter } from './VariantEFooter';
import {
  HeroSection,
  TrustStrip,
  ProductInAction,
  ValuePillars,
  FeatureBands,
  HowItWorks,
  TrustControl,
  StatsBand,
  Integrations,
} from './VariantESections';

// ---------------------------------------------------------------------------
// VARIANT E — "Watch it work", conventional B2B-SaaS structure with Cursor-style
// AUTOPLAY-ON-ENTER motion (Motion / motion.dev). NOT scroll-scrubbed. Components
// come alive on a loop when scrolled into view; reveals carry the rest.
//
// Motion bundle: `m` + <LazyMotion features={domAnimation}> keeps the core to
// ~5kb and lazy-loads DOM features once; <MotionConfig reducedMotion="user">
// makes every Reveal/Stagger respect prefers-reduced-motion in one line. The
// autoplay mocks additionally branch on useReducedMotion() and render their final
// state with no loop (see motion.jsx / VariantEMocks.jsx).
//
// The subtle hero AGENT switch (grey → purple) auto-flips ~1.2s after mount and
// is replayable; flipping it ON triggers the hero product mock coming alive
// (its autoplay starts) — not a whole-page grayscale flood. Smooth scroll comes
// from Lenis (already a dependency), scoped to this variant and skipped entirely
// under reduced motion. Everything is lazy-loaded behind the /landing-preview
// route.
// ---------------------------------------------------------------------------

export const LandingVariantE = ({ onNavigate }) => {
  const reduced = !!useReducedMotion();
  const [on, setOn] = useState(reduced); // reduced-motion → straight to ON
  const [pressing, setPressing] = useState(false);
  const userToggledRef = useRef(reduced);

  const toggle = useCallback(() => {
    userToggledRef.current = true;
    if (reduced) {
      setOn((v) => !v);
      return;
    }
    setPressing(true);
    window.setTimeout(() => {
      setOn((v) => !v);
      setPressing(false);
    }, 180);
  }, [reduced]);

  // Auto-flip ON ~1.2s after mount, unless the visitor already toggled.
  useEffect(() => {
    if (reduced || userToggledRef.current) return undefined;
    const t = window.setTimeout(() => {
      if (userToggledRef.current) return;
      setPressing(true);
      window.setTimeout(() => {
        if (userToggledRef.current) return;
        setOn(true);
        setPressing(false);
      }, 180);
    }, 1200);
    return () => window.clearTimeout(t);
  }, [reduced]);

  // Lenis smooth-scroll — scoped to this variant, dynamically imported, torn down
  // on unmount. Never initialised under reduced motion.
  useEffect(() => {
    if (reduced || typeof window === 'undefined') return undefined;
    let lenis;
    let rafId = 0;
    let cancelled = false;
    import('lenis')
      .then(({ default: Lenis }) => {
        if (cancelled) return;
        lenis = new Lenis({ lerp: 0.1, wheelMultiplier: 1, smoothWheel: true });
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
    };
  }, [reduced]);

  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <div className="lve" data-brand="taali">
          <style>{VARIANT_E_CSS}</style>

          <VariantENav onNavigate={onNavigate} />
          <HeroSection on={on} pressing={pressing} onToggle={toggle} onNavigate={onNavigate} />
          <TrustStrip />
          <ProductInAction />
          <ValuePillars />
          <FeatureBands />
          <HowItWorks />
          <TrustControl />
          <StatsBand />
          <Integrations />

          <div className="lve-footer">
            <ClosingCta onNavigate={onNavigate} />
            <ProductionFooter onNavigate={onNavigate} />
          </div>
        </div>
      </MotionConfig>
    </LazyMotion>
  );
};

export default LandingVariantE;
