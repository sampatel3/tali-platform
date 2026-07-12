import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useCountUp } from './useCountUp';

// The production ticker differs from the preview NumberTicker in two ways that
// the real pages depend on: reduced motion must render the final value with no
// tween, and the tween must RE-RUN when `to` changes (async fetches settle
// after first paint). These probes lock both in.

const Probe = ({ to, reduced }) => <span data-testid="v">{useCountUp(to, { reduced })}</span>;

describe('useCountUp', () => {
  it('returns the final value immediately under reduced motion', () => {
    render(<Probe to={82} reduced />);
    expect(screen.getByTestId('v').textContent).toBe('82');
  });

  it('re-runs when `to` changes (not mount-only)', () => {
    // reduced=true isolates the re-run behaviour from rAF timing: the value must
    // track each new `to`, proving the effect keys on `to` rather than mount.
    const { rerender } = render(<Probe to={0} reduced />);
    expect(screen.getByTestId('v').textContent).toBe('0');
    act(() => {
      rerender(<Probe to={1204} reduced />);
    });
    expect(screen.getByTestId('v').textContent).toBe('1,204');
  });
});
