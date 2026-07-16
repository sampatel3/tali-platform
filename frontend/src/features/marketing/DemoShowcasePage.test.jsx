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
});
