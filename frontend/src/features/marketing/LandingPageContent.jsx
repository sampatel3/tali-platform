import React, { useEffect } from 'react';

import { LandingVariantG } from './landing_preview/variant_g/LandingVariantG';
import {
  consumePendingMarketingSection,
  scrollToMarketingSection,
} from '../../lib/marketingScroll';

// The production homepage renders variant G — F's "Vivid Purple" look with E's
// tight, one-screen-per-section layout, wearing the site's real chrome (the
// shared <MarketingNav> header + brand tokens) so it reads as a native taali.ai
// page, not a pasted-in mockup. The design lives in landing_preview/variant_g
// and is SHARED with the /landing-preview switcher (which wraps the same
// <LandingVariantG> with an internal-only variant chip); this route renders it
// clean, with no chip. CTAs route through `onNavigate` (AppShell's
// navigateToPage): the hero/closing "See it live" → showcase and "Book a demo"
// → demo-lead; the shared header's "Sign in" → login (see the variant_g
// components + shared MarketingNav).
export const LandingPage = ({ onNavigate }) => {
  // Preserve the marketing deep-link scroll: another page's marketing nav can
  // queue a section (e.g. the demo page's "Product" tab) or land here with a
  // URL hash; on mount we scroll to it. Section ids that no longer exist simply
  // no-op — hash links into variant G's real sections (#how-it-works, #platform,
  // #g-control) still resolve.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const sectionId = consumePendingMarketingSection() || window.location.hash.replace(/^#/, '');
    if (!sectionId) return undefined;

    const timer = window.setTimeout(() => {
      scrollToMarketingSection(sectionId, { behavior: 'smooth' });
    }, 40);

    return () => window.clearTimeout(timer);
  }, []);

  return <LandingVariantG onNavigate={onNavigate} />;
};

export default LandingPage;
