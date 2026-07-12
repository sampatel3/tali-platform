import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MotionSystemProvider, Reveal } from './index';

// The old preview-only CSS reveal was removed. Preview and production narrative
// content now use the same true once-in-view primitive.

describe('Reveal (shared system)', () => {
  it('keeps meaningful children mounted before the in-view entrance', () => {
    render(
      <MotionSystemProvider>
        <Reveal className="rev-probe">
          <span>evidence content</span>
        </Reveal>
      </MotionSystemProvider>,
    );
    expect(screen.getByText('evidence content')).toBeInTheDocument();
    expect(document.querySelector('.rev-probe')).not.toBeNull();
  });

  it('renders the settled wrapper under reduced motion', async () => {
    render(
      <MotionSystemProvider>
        <Reveal className="rev-reduced" reduced>
          <span>reduced content</span>
        </Reveal>
      </MotionSystemProvider>,
    );
    const wrapper = document.querySelector('.rev-reduced');
    expect(wrapper).not.toBeNull();
    await waitFor(() => expect(wrapper).toHaveStyle({ opacity: '1' }));
    expect(screen.getByText('reduced content')).toBeInTheDocument();
  });
});
