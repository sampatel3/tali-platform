import React, { useCallback } from 'react';
import { LazyMotion, domMax, MotionConfig } from 'motion/react';

import { useReducedMotionSync } from '../../../../shared/motion/previewMotion';
import { MarketingNav } from '../../../../shared/layout/TaaliLayout';
import { VARIANT_G_CSS } from './variantG.styles';
import { VariantGHero } from './VariantGHero';
import { VariantGFooter } from './VariantGFooter';
import { FunnelSection, FluencySection, ControlSection } from './VariantGSections';

// ---------------------------------------------------------------------------
// VARIANT G — F's "Vivid Purple" look + E's tight, one-screen-per-section
// layout, worn with the SITE'S REAL CHROME so it reads as one cohesive
// taali.ai page rather than a pasted-in mockup:
//
//   • HEADER — the shared <MarketingNav> (real Taali logo, Product / How it
//     works section links, Developers / Blog, Sign in, Book a demo, theme
//     toggle, mobile menu). Identical to /blog, /demo, /login. It renders
//     OUTSIDE the scoped `.lvg` root so the scoped `.btn`/`.nav` rules never
//     touch it. Its two section tabs resolve to the body anchors below
//     ("How it works" → #how-it-works, "Product" → #platform) via the shared
//     scrollToMarketingSection helper (native smooth scroll — same as the rest
//     of the site; no bespoke Lenis layer, which also matches the app).
//   • TOKENS — the `.lvg` design consumes the shared brand tokens
//     (data-brand="taali") for colour + type, so it tracks the palette and
//     dark mode (see variantG.styles).
//
// The vivid purple design itself — two-column hero, the OFF→ON AgentScene, the
// funnel / 5-Ds / control sections — is unchanged. Motion model is unchanged:
// LazyMotion + MotionConfig reducedMotion + the CSS one-shot <Reveal> entrances
// and the AgentScene useAnimate timeline.
// ---------------------------------------------------------------------------

export const LandingVariantG = ({ onNavigate }) => {
  const reduced = useReducedMotionSync();

  // Footer "back to top" (and any in-page anchor): native smooth scroll, which
  // matches the rest of the site. The header's section links use the shared
  // scrollToMarketingSection helper directly. Honours reduced motion.
  const scrollToSection = useCallback((id) => {
    if (typeof document === 'undefined') return;
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
  }, [reduced]);

  return (
    <LazyMotion features={domMax} strict>
      <MotionConfig reducedMotion="user">
        <MarketingNav onNavigate={onNavigate} />
        <div className="lvg">
          <style>{VARIANT_G_CSS}</style>

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
