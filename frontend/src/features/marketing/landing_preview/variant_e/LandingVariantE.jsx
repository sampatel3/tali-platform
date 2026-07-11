import React, { useEffect } from 'react';
import { LazyMotion, domMax, MotionConfig } from 'motion/react';

import { useReducedMotionSync } from '../../../../shared/motion/previewMotion';
import { VARIANT_E_CSS } from './variantE.styles';
import { VariantENav } from './VariantENav';
import { ClosingCta, ProductionFooter } from './VariantEFooter';
import {
  HeroSection,
  ProblemSection,
  FunnelSection,
  WedgeSection,
  ControlSection,
  ProofSection,
} from './VariantESections';

// ---------------------------------------------------------------------------
// VARIANT E v4 — rebuilt to the narrative spine. One story, told once: turn a
// job on, the agent works your whole funnel — and it's the only one that
// measures how people actually work with AI. Six sections, no repetition:
//
//   1. HERO      — the product's core loop, LIVE (a real role card turns its
//                  agent ON; candidates flow out into a decision lane).
//   2. PROBLEM   — one tight beat: the CV can't prove it, the interview can't
//                  catch it.
//   3. FUNNEL    — shown ONCE: one candidate through Source → Screen → Assess →
//                  Decide → Hand back.
//   4. WEDGE     — the differentiator: the real 5-Ds AI-fluency scorecard.
//   5. CONTROL   — the agent advises, you decide (a real decision glimpse).
//   6. PROOF     — a tight stats row, the closing CTA, and the production footer.
//
// Motion (motion.dev): section entrances use the shared, one-shot CSS <Reveal>
// (can't get stuck); the hero + funnel SCENES use useAnimate/useInView autoplay
// timelines. <MotionConfig reducedMotion="user"> + a synchronous reduced-motion
// read make every scene render its final composed state under reduced motion.
// Lenis smooth-scroll is scoped to this variant and skipped under reduced motion.
// ---------------------------------------------------------------------------

export const LandingVariantE = ({ onNavigate }) => {
  const reduced = useReducedMotionSync();

  // Lenis smooth-scroll — scoped to this variant, dynamically imported, torn
  // down on unmount. Never initialised under reduced motion.
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
    <LazyMotion features={domMax} strict>
      <MotionConfig reducedMotion="user">
        <div className="lve" data-brand="taali">
          <style>{VARIANT_E_CSS}</style>

          <VariantENav onNavigate={onNavigate} />
          <HeroSection onNavigate={onNavigate} />
          <ProblemSection />
          <FunnelSection />
          <WedgeSection />
          <ControlSection />
          <ProofSection />

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
