import React from 'react';

import { LandingPreviewNav, LandingPreviewFooter, ClosingCtaBand } from './LandingPreviewChrome';
import { LandingPreviewHero } from './LandingPreviewHero';
import { ValuePillars, FiveDsBand, EvidenceStrip, HowItWorks } from './LandingPreviewSections';

// VARIANT A — "Value-abstract". No product UI chrome anywhere: hero, three
// value pillars (abstract line-art motifs), the 5 Ds band (radial SVG motif),
// an evidence strip of hard claims, a plain-language how-it-works, and a
// closing CTA band.
export const LandingVariantA = ({ onNavigate }) => (
  <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
    <LandingPreviewNav onNavigate={onNavigate} />
    <LandingPreviewHero onNavigate={onNavigate} />
    <ValuePillars />
    <FiveDsBand />
    <EvidenceStrip />
    <HowItWorks />
    <ClosingCtaBand onNavigate={onNavigate} />
    <LandingPreviewFooter onNavigate={onNavigate} />
  </div>
);

export default LandingVariantA;
