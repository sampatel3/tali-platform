import React, { useEffect } from 'react';
import { LazyMotion, domMax, MotionConfig } from 'motion/react';

import { useReducedMotionSync } from '../../../../shared/motion/previewMotion';
import { VARIANT_F_CSS } from './variantF.styles';
import { VariantFNav } from './VariantFNav';
import { VariantFHero } from './VariantFHero';
import { VariantFFooter } from './VariantFFooter';
import { ProblemSection, FunnelSection, FluencySection, CloseSection } from './VariantFSections';

// ---------------------------------------------------------------------------
// VARIANT F — the "Vivid Purple" design handoff, recreated pixel-accurately in
// React + scoped CSS on the Motion library (motion.dev). Light theme, purple
// family only. One scrolling marketing page in narrative order:
//
//   Nav → Hero (type-led + the live agent-ON → decision-lane scene) → Problem →
//   Agentic hiring (5-step funnel + folded-in "You decide" control block) →
//   AI-native assessments (5-Ds scorecard + folded-in proof stats) → Close CTA →
//   Footer.
//
// Motion model — the hero AgentScene runs a Motion `useAnimate` autoplay-once
// timeline (OFF → ON → rows land → verdicts stamp) with a Replay affordance;
// section entrances reuse the shared one-shot CSS <Reveal> (can't get stuck
// under LazyMotion); the gradient shimmer is pure CSS gated behind
// prefers-reduced-motion. <MotionConfig reducedMotion="user"> + a synchronous
// reduced-motion read make every scene render its settled final state under
// reduced motion. Lenis smooth-scroll is scoped to this variant and skipped
// under reduced motion.
// ---------------------------------------------------------------------------

export const LandingVariantF = ({ onNavigate }) => {
  const reduced = useReducedMotionSync();

  // Lenis smooth-scroll — scoped, dynamically imported, torn down on unmount,
  // never initialised under reduced motion.
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
        <div className="lvf">
          <style>{VARIANT_F_CSS}</style>

          <VariantFNav onNavigate={onNavigate} />
          <VariantFHero onNavigate={onNavigate} />
          <ProblemSection reduced={reduced} />
          <FunnelSection reduced={reduced} />
          <FluencySection reduced={reduced} />
          <CloseSection reduced={reduced} onNavigate={onNavigate} />
          <VariantFFooter />
        </div>
      </MotionConfig>
    </LazyMotion>
  );
};

export default LandingVariantF;
