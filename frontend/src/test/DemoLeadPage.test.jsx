import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DemoLeadPage } from '../features/marketing/DemoLeadPage';

// Lead capture posts via plain fetch (public page — no httpClient/auth
// machinery). VITE_API_URL is '' under vitest, so the request uses the
// same-origin public endpoint just as a same-origin deployment does.

describe('DemoLeadPage submit', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    global.fetch = vi.fn().mockResolvedValue({ ok: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('requires an email before navigating', () => {
    const onNavigate = vi.fn();
    render(<DemoLeadPage onNavigate={onNavigate} />);
    fireEvent.submit(screen.getByRole('button', { name: /open the live walkthrough/i }));
    act(() => vi.runAllTimers());
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it('navigates to the demo after submit', () => {
    const onNavigate = vi.fn();
    render(<DemoLeadPage onNavigate={onNavigate} />);
    fireEvent.change(screen.getByLabelText(/work email/i), {
      target: { value: 'jane@acme-corp.io' },
    });
    fireEvent.submit(screen.getByRole('button', { name: /open the live walkthrough/i }));
    act(() => vi.runAllTimers());
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v1/public/demo-lead',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('jane@acme-corp.io'),
        keepalive: true,
      }),
    );
    expect(onNavigate).toHaveBeenCalledWith('demo');
  });

  it('keeps the primary heading inside the main form landmark', () => {
    const { container } = render(<DemoLeadPage onNavigate={vi.fn()} />);

    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(container.querySelector('main h1')).toHaveTextContent(
      'See it run on a realistic role.',
    );
    expect(container.querySelector('aside .mc-demo-lead-title')).toHaveTextContent(
      /Let the agent find\s*your AI-native hires/i,
    );
    expect(container.querySelector('aside h1, aside h2')).toBeNull();
  });

  it('supports roving arrow-key selection in both radio groups', () => {
    render(<DemoLeadPage onNavigate={vi.fn()} />);

    const backend = screen.getByRole('radio', { name: 'Backend' });
    const frontend = screen.getByRole('radio', { name: 'Frontend' });
    expect(backend).toHaveAttribute('tabindex', '0');
    expect(frontend).toHaveAttribute('tabindex', '-1');

    backend.focus();
    fireEvent.keyDown(backend, { key: 'ArrowRight' });
    expect(frontend).toHaveFocus();
    expect(frontend).toHaveAttribute('aria-checked', 'true');

    const selectedVolume = screen.getByRole('radio', { name: '6–20' });
    selectedVolume.focus();
    fireEvent.keyDown(selectedVolume, { key: 'End' });
    expect(screen.getByRole('radio', { name: '50+' })).toHaveFocus();
    expect(screen.getByRole('radio', { name: '50+' })).toHaveAttribute('aria-checked', 'true');
  });
});
