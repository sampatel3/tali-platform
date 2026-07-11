import React, { useRef } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useRevealOnView, Reveal } from './previewMotion';

// The preview reveal trigger must fire on MOUNT when the element is already in
// the viewport — not only on scroll. This is the regression the live review
// caught: above-the-fold sections (report scorecard, analytics pulse) loaded
// hidden because the initial in-view IntersectionObserver callback was missed.
// jsdom's stubbed getBoundingClientRect reports a non-zero in-view box, so the
// mount-in-view branch resolves deterministically here.

const Harness = () => {
  const ref = useRef(null);
  const shown = useRevealOnView(ref);
  return <div ref={ref} data-testid="probe">{shown ? 'revealed' : 'hidden'}</div>;
};

describe('useRevealOnView', () => {
  it('returns true on mount when the element is already in view', () => {
    render(<Harness />);
    // No scroll happened — the IO mock never fires — yet the mount-in-view
    // check reveals it, so it is never stuck hidden above the fold.
    expect(screen.getByTestId('probe')).toHaveTextContent('revealed');
  });
});

describe('Reveal (mount-in-view)', () => {
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
