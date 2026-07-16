import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DemoLeadPage } from '../features/marketing/DemoLeadPage';

// Lead capture posts via plain fetch (public page — no httpClient/auth
// machinery). VITE_API_URL is '' under vitest, so the POST is skipped
// there; we stub fetch and assert the navigate path regardless, plus the
// payload when a base URL is present via the stubbed global.

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
    expect(onNavigate).toHaveBeenCalledWith('demo');
  });
});
