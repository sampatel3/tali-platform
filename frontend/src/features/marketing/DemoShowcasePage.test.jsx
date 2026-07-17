import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DemoShowcasePage } from './DemoShowcasePage';

describe('DemoShowcasePage walkthrough navigation', () => {
  it('uses the shared peer-view bar and preserves the active walkthrough content', () => {
    render(<DemoShowcasePage />);

    const navigation = screen.getByRole('navigation', { name: 'Walkthrough sections' });
    const hub = within(navigation).getByRole('button', { name: /The Hub/i });
    const assessment = within(navigation).getByRole('button', { name: /AI assessment/i });

    expect(hub).toHaveAttribute('aria-pressed', 'true');
    expect(assessment).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/Mission control: steer the agent/i)).toBeInTheDocument();

    fireEvent.click(assessment);

    expect(hub).toHaveAttribute('aria-pressed', 'false');
    expect(assessment).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/Step into the chat-first workspace/i)).toBeInTheDocument();
  });
});
