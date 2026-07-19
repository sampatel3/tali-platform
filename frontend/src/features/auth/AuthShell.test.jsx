import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AuthField, AuthShell } from './AuthShell';

describe('AuthShell', () => {
  it('keeps one home control outside the responsive editorial pane', () => {
    const onNavigate = vi.fn();

    render(
      <AuthShell title="Sign in" onNavigate={onNavigate}>
        <div>Form</div>
      </AuthShell>,
    );

    const home = screen.getByRole('button', { name: 'Taali home' });
    expect(home).toHaveClass('mc-auth-logo');
    expect(home.closest('.mc-auth-editorial')).toBeNull();

    fireEvent.click(home);
    expect(onNavigate).toHaveBeenCalledWith('landing');
  });

  it('associates helper and error messages with their fields', () => {
    const { rerender } = render(
      <AuthField label="Work email" name="email" helper="Use your company address." />,
    );

    const input = screen.getByRole('textbox', { name: 'Work email' });
    const helper = screen.getByText('Use your company address.');
    expect(input).toHaveAttribute('aria-describedby', helper.id);
    expect(helper.id).toBe('auth-email-helper');

    rerender(<AuthField label="Work email" name="email" error="Enter a valid email." />);
    const error = screen.getByRole('alert');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAttribute('aria-errormessage', error.id);
    expect(input).toHaveAttribute('aria-describedby', error.id);
  });
});
