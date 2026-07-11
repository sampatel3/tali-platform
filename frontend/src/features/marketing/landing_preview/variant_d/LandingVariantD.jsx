import React, { useCallback, useEffect, useRef, useState } from 'react';

import { VARIANT_D_CSS } from './variantD.styles';
import { VariantDHero } from './VariantDHero';
import { WatchScene } from './WatchScene';
import { StandardSection, StatsRow } from './VariantDSections';
import { VariantDFooter } from './VariantDFooter';
import { useStaticMode } from './sceneProgress';

// ---------------------------------------------------------------------------
// VARIANT D — "Watch it work". A premium, scroll-driven experience. The hero is
// the agent switch (grey OFF → purple ON); flipping it hands the visitor down
// into a PINNED, scroll-scrubbed scene that plays the whole pipeline — Source ·
// Screen · Assess · Decide · Hand back — one continuous DOM scene driven by a
// single scroll-progress value. After the scene, the 5 Ds as a sticky rail, a
// stats row, and the production closing CTA + footer.
//
// Smooth scroll comes from Lenis (the one new dep), initialised in an effect and
// destroyed on unmount, scoped entirely to this variant. Under reduced-motion or
// on short viewports we go to `staticMode`: no pin, no scrub, no Lenis — the
// scene renders as 5 stacked static panels. Nothing depends on
// IntersectionObserver for correctness.
// ---------------------------------------------------------------------------

export const LandingVariantD = ({ onNavigate }) => {
  const staticMode = useStaticMode();
  const [on, setOn] = useState(staticMode); // static/reduced → straight to ON
  const [pressing, setPressing] = useState(false);
  const userToggledRef = useRef(staticMode);
  const sceneWrapRef = useRef(null);
  const lenisRef = useRef(null);

  const toggle = useCallback(() => {
    userToggledRef.current = true;
    if (staticMode) {
      setOn((v) => !v);
      return;
    }
    setPressing(true);
    window.setTimeout(() => {
      setOn((v) => !v);
      setPressing(false);
    }, 200);
  }, [staticMode]);

  // Auto-flip ON ~1.4s after mount, unless the visitor already toggled.
  useEffect(() => {
    if (staticMode || userToggledRef.current) return undefined;
    const t = window.setTimeout(() => {
      if (userToggledRef.current) return;
      setPressing(true);
      window.setTimeout(() => {
        if (userToggledRef.current) return;
        setOn(true);
        setPressing(false);
      }, 200);
    }, 1400);
    return () => window.clearTimeout(t);
  }, [staticMode]);

  // Lenis smooth-scroll — scoped to this variant only. Dynamically imported so
  // it never loads under reduced-motion / static mode, and torn down on unmount.
  useEffect(() => {
    if (staticMode || typeof window === 'undefined') return undefined;
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
  }, [staticMode]);

  // "Watch it work" / scroll cue → smooth-scroll to the scene (via Lenis if
  // ready, native fallback otherwise).
  const scrollToScene = useCallback(() => {
    const target = sceneWrapRef.current;
    if (!target) return;
    if (lenisRef.current) {
      lenisRef.current.scrollTo(target, { offset: 0 });
      return;
    }
    target.scrollIntoView({ behavior: staticMode ? 'auto' : 'smooth', block: 'start' });
  }, [staticMode]);

  return (
    <div
      className={`lvd${on ? ' is-on' : ''}${staticMode ? ' is-static' : ''}`}
      data-on={on ? 'true' : 'false'}
    >
      <style>{VARIANT_D_CSS}</style>

      <VariantDHero
        on={on}
        pressing={pressing}
        onToggle={toggle}
        onNavigate={onNavigate}
        onWatch={scrollToScene}
      />

      <WatchScene wrapperRef={sceneWrapRef} staticMode={staticMode} />

      <StandardSection staticMode={staticMode} />
      <StatsRow />

      <VariantDFooter onNavigate={onNavigate} />
    </div>
  );
};

export default LandingVariantD;
