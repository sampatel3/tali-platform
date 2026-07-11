import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Reveal } from './previewMotion';

// The preview reveal wrapper must render its content on MOUNT (a one-shot CSS
// entrance, `.pv-reveal`), never gated behind a scroll — the regression the
// live review caught was above-the-fold sections (report scorecard, analytics
// pulse) loading hidden. A plain CSS animation with fill:both can't get stuck.

describe('Reveal (mount entrance)', () => {
  it('renders its children on mount in view', () => {
    render(
      <Reveal className="rev-probe">
        <span>evidence content</span>
      </Reveal>,
    );
    expect(screen.getByText('evidence content')).toBeInTheDocument();
    expect(document.querySelector('.rev-probe')).not.toBeNull();
  });

  it('renders a plain, always-visible wrapper under reduced motion', () => {
    render(
      <Reveal className="rev-reduced" reduced>
        <span>reduced content</span>
      </Reveal>,
    );
    const wrapper = document.querySelector('.rev-reduced');
    expect(wrapper).not.toBeNull();
    // Reduced motion → plain div, no motion opacity styling at all.
    expect(wrapper.style.opacity === '' || wrapper.style.opacity === '1').toBe(true);
    expect(screen.getByText('reduced content')).toBeInTheDocument();
  });
});
