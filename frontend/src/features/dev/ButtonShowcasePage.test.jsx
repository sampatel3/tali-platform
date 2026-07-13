import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ButtonShowcasePage } from './ButtonShowcasePage';

describe('ButtonShowcasePage', () => {
  it('presents the canonical system and every mapped legacy family', () => {
    render(<ButtonShowcasePage />);

    expect(screen.getAllByTestId(/^button-family-/)).toHaveLength(13);
    expect(screen.getByRole('heading', { name: 'One button system, everywhere.' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Assessment runtime actions' })).toBeInTheDocument();
    expect(screen.getByText('7', { selector: '.button-lab__audit strong' })).toBeInTheDocument();
    expect(screen.getByText('4', { selector: '.button-lab__audit strong' })).toBeInTheDocument();
    expect(screen.getByText('12', { selector: '.button-lab__audit strong' })).toBeInTheDocument();
    expect(screen.getByText('1', { selector: '.button-lab__audit strong' })).toBeInTheDocument();
    expect(screen.getAllByText('mapped', { selector: '.button-lab__variant-label' })).toHaveLength(12);
    expect(screen.getByText('canonical', { selector: '.button-lab__variant-label' })).toBeInTheDocument();

    expect(screen.queryByText(/unsupported purple/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/CSS missing/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/No consolidation has been applied/i)).not.toBeInTheDocument();
  });

  it('renders every canonical variant, size, and shared state', () => {
    render(<ButtonShowcasePage />);

    const sharedFamily = screen.getByTestId('button-family-A');
    const family = within(sharedFamily);

    expect(family.getByRole('button', { name: 'Create shortlist' })).toHaveClass('taali-btn-primary');
    expect(family.getByRole('button', { name: 'Edit details' })).toHaveClass('taali-btn-secondary');
    expect(family.getByRole('button', { name: 'View details' })).toHaveClass('taali-btn-ghost');
    expect(family.getByRole('button', { name: 'Review first' })).toHaveClass('taali-btn-soft');
    expect(family.getByRole('button', { name: 'Delete role' })).toHaveClass('taali-btn-danger');
    expect(family.getByRole('button', { name: 'Use recommendation' })).toHaveClass('taali-btn-agent');
    expect(family.getByRole('button', { name: 'Pause agent' })).toHaveClass('taali-btn-inverse');

    expect(family.getByRole('button', { name: 'XS' })).toHaveClass('taali-btn-xs');
    expect(family.getByRole('button', { name: 'Small' })).toHaveClass('taali-btn-sm');
    expect(family.getByRole('button', { name: 'Medium' })).toHaveClass('taali-btn-md');
    expect(family.getByRole('button', { name: 'Large' })).toHaveClass('taali-btn-lg');

    expect(family.getByRole('button', { name: 'Saving changes' })).toBeDisabled();
    expect(family.getByRole('button', { name: 'Saving changes' })).toHaveAttribute('aria-busy', 'true');
    expect(family.getByRole('button', { name: 'Can’t continue' })).toBeDisabled();
    expect(family.getByRole('button', { name: 'Settings' })).toHaveClass('taali-btn-icon-only');
  });
});
