import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DemoShowcasePage } from './DemoShowcasePage';

describe('DemoShowcasePage', () => {
  it('mounts only the active product iframe and labels fixtures honestly', () => {
    render(<DemoShowcasePage onNavigate={vi.fn()} />);

    expect(screen.getByText('Curated sample data')).toBeInTheDocument();
    expect(screen.getAllByTitle('The Hub')).toHaveLength(1);
    expect(document.querySelectorAll('iframe')).toHaveLength(1);

    fireEvent.click(screen.getByRole('tab', { name: /Standing report/i }));
    expect(screen.getByTitle('Standing report')).toHaveAttribute(
      'src',
      '/c/demo?view=client&k=demo-token&showcase=1',
    );
    expect(document.querySelectorAll('iframe')).toHaveLength(1);
  });

  it('uses roving keyboard tabs linked to the active panel', () => {
    render(<DemoShowcasePage onNavigate={vi.fn()} />);

    const hubTab = screen.getByRole('tab', { name: /The Hub/i });
    const agentTab = screen.getByRole('tab', { name: /Agentic triage/i });
    expect(hubTab).toHaveAttribute('tabindex', '0');
    expect(agentTab).toHaveAttribute('tabindex', '-1');

    hubTab.focus();
    fireEvent.keyDown(hubTab, { key: 'ArrowRight' });

    expect(agentTab).toHaveFocus();
    expect(agentTab).toHaveAttribute('aria-selected', 'true');
    expect(agentTab).toHaveAttribute('aria-controls', 'mc-show-active-panel');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'mc-show-tab-agent');
    expect(screen.getByTitle('Agentic triage')).toHaveAttribute('src', '/showcase/jobs');
  });
});
