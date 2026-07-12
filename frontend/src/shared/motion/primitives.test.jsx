import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  AGENT_LOOP_DURATION,
  AgentLoop,
  MotionDisclosure,
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
    expect(inactive.animate).toEqual({ backgroundPosition: '50% 50%' });
  });

  it('renders a semantic live-agent surface through the shared loop primitive', () => {
    render(
      <MotionSystemProvider>
        <AgentLoop as="button" kind="flow" type="button">Approve recommendation</AgentLoop>
      </MotionSystemProvider>,
    );

    const button = screen.getByRole('button', { name: 'Approve recommendation' });
    expect(button).toHaveAttribute('data-motion-loop', 'flow');
    expect(button).toHaveAttribute('data-motion-state', 'running');
    expect(button).not.toHaveAttribute('aria-hidden');
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
