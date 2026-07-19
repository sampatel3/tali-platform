import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DemoShowcasePage } from './DemoShowcasePage';

describe('DemoShowcasePage', () => {
  it('mounts only the active product iframe and labels fixtures honestly', () => {
    render(<DemoShowcasePage onNavigate={vi.fn()} />);

    expect(screen.getByText('Curated sample data')).toBeInTheDocument();
    expect(screen.getAllByTitle('The Hub')).toHaveLength(1);
    expect(document.querySelectorAll('iframe')).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: /Standing report/i }));
    expect(screen.getByTitle('Standing report')).toHaveAttribute(
      'src',
      '/c/demo?view=client&k=demo-token&showcase=1',
    );
    expect(document.querySelectorAll('iframe')).toHaveLength(1);
  });

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
