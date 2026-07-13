import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  AGENT_LOOP_DURATION,
  AgentLoop,
  MotionDisclosure,
  MotionProgress,
  MotionSkeleton,
  MotionSpinner,
  MotionNumber,
  MotionSystemProvider,
  MotionTab,
  MotionTabs,
  PresenceSwap,
  Reveal,
  agentLoopPresets,
  motionSafeScrollBehavior,
  resolveAgentLoop,
} from './index';

const normalMatchMedia = window.matchMedia;

afterEach(() => {
  window.matchMedia = normalMatchMedia;
});

function PatternProbe() {
  const [tab, setTab] = useState('first');
  const [open, setOpen] = useState(false);
  return (
    <MotionSystemProvider>
      <button type="button" onClick={() => setOpen((value) => !value)}>Toggle details</button>
      <MotionDisclosure open={open}>
        <p>Measured content</p>
      </MotionDisclosure>
      <MotionTabs value={tab} onValueChange={setTab} aria-label="Example views">
        <MotionTab value="first">First</MotionTab>
        <MotionTab value="second">Second</MotionTab>
      </MotionTabs>
      <PresenceSwap presenceKey={tab}>{tab} panel</PresenceSwap>
      <MotionNumber value={87} />
    </MotionSystemProvider>
  );
}

describe('shared motion primitives', () => {
  it('defines one tokenized Motion.dev vocabulary for live agent loops', () => {
    expect(Object.keys(agentLoopPresets)).toEqual(['flow', 'glow', 'pulse', 'ring', 'ambient']);
    expect(agentLoopPresets.flow.transition).toMatchObject({
      duration: AGENT_LOOP_DURATION.flow,
      repeat: Infinity,
    });
    expect(agentLoopPresets.ambient.transition.duration).toBe(AGENT_LOOP_DURATION.ambient);

    const inactive = resolveAgentLoop('flow', { active: false });
    expect(inactive.state).toBe('rest');
    expect(inactive.transition.repeat).toBeUndefined();
    expect(inactive.animate).toEqual({ x: '0%' });
  });

  it('renders a semantic live-agent surface that waits for viewport confirmation', () => {
    render(
      <MotionSystemProvider>
        <AgentLoop as="button" kind="flow" type="button">Approve recommendation</AgentLoop>
      </MotionSystemProvider>,
    );

    const button = screen.getByRole('button', { name: 'Approve recommendation' });
    expect(button).toHaveAttribute('data-motion-loop', 'flow');
    // The test IntersectionObserver intentionally never intersects. Real
    // browsers flip this to running once the surface is confirmed in view.
    expect(button).toHaveAttribute('data-motion-state', 'rest');
    expect(button).toHaveClass('agent-motion-flow');
    expect(button.querySelector('.agent-motion-transform-layer')).toBeInTheDocument();
    expect(button).not.toHaveAttribute('aria-hidden');
  });

  it('provides one Motion-native vocabulary for loading and progress feedback', () => {
    render(
      <MotionSystemProvider>
        <MotionSpinner label="Loading candidates" data-testid="spinner" />
        <MotionSkeleton data-testid="skeleton" />
        <MotionProgress data-testid="progress" reduced value={0.72} />
      </MotionSystemProvider>,
    );

    expect(screen.getByRole('status', { name: 'Loading candidates' }))
      .toHaveAttribute('data-motion-loop', 'spin');
    expect(screen.getByTestId('skeleton')).toHaveAttribute('data-motion-loop', 'shimmer');
    expect(screen.getByTestId('skeleton').querySelector('.motion-loop-transform-layer'))
      .toBeInTheDocument();
    expect(screen.getByTestId('progress')).toHaveAttribute('data-motion-progress', 'x');
    expect(screen.getByTestId('progress')).toHaveAttribute('data-motion-value', '0.72');
  });

  it('coordinates disclosures, tab focus, keyed panels, and final number labels', async () => {
    render(<PatternProbe />);

    expect(screen.queryByText('Measured content')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Toggle details' }));
    expect(screen.getByText('Measured content')).toBeInTheDocument();

    const first = screen.getByRole('tab', { name: 'First' });
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: 'Second' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(screen.getByText('second panel')).toBeInTheDocument());
    expect(screen.getByLabelText('87')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Toggle details' }));
    await waitFor(() => expect(screen.queryByText('Measured content')).not.toBeInTheDocument());
  });

  it('settles Reveal and native scroll behavior immediately under reduced motion', async () => {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }));

    render(
      <MotionSystemProvider>
        <Reveal data-testid="reveal">Critical content</Reveal>
      </MotionSystemProvider>,
    );

    const reveal = screen.getByTestId('reveal');
    await waitFor(() => expect(reveal).toHaveStyle({ opacity: '1' }));
    expect(screen.getByText('Critical content')).toBeInTheDocument();
    expect(motionSafeScrollBehavior('smooth')).toBe('auto');
  });

  it('reveals an interactive region immediately when keyboard focus enters it', async () => {
    render(
      <MotionSystemProvider>
        <Reveal data-testid="focus-reveal"><button type="button">Continue</button></Reveal>
      </MotionSystemProvider>,
    );

    const reveal = screen.getByTestId('focus-reveal');
    expect(reveal).toHaveAttribute('data-motion-reveal-state', 'hidden');
    fireEvent.focus(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(reveal).toHaveAttribute('data-motion-reveal-state', 'visible'));
  });

  it('settles agent pulses and hides expanding rings under reduced motion', async () => {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }));

    render(
      <MotionSystemProvider>
        <AgentLoop kind="pulse" data-testid="agent-pulse" />
        <AgentLoop kind="ring" data-testid="agent-ring" />
      </MotionSystemProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('agent-pulse')).toHaveAttribute('data-motion-state', 'rest');
      expect(screen.getByTestId('agent-ring')).toHaveAttribute('data-motion-state', 'rest');
    });
    expect(resolveAgentLoop('pulse', { reduced: true }).animate).toEqual({ opacity: 1, scale: 1 });
    expect(resolveAgentLoop('ring', { reduced: true }).animate).toEqual({ opacity: 0, scale: 1 });
  });
});
