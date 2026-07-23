import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const motionState = vi.hoisted(() => ({ reduced: false }));

vi.mock('../../shared/motion', () => ({
  m: {
    button: ({ initial, animate, transition, ...props }) => (
      <button
        {...props}
        data-initial={JSON.stringify(initial)}
        data-animate={JSON.stringify(animate)}
      />
    ),
  },
  motionTransition: { instant: { duration: 0 } },
  useReducedMotionSync: () => motionState.reduced,
}));

import { OriginalRoleButton, RoleFamilyHeaderNote } from './RoleFamilyHeaderUi';

describe('RoleFamilyHeaderUi', () => {
  beforeEach(() => {
    motionState.reduced = false;
  });

  it('starts the finite original-role pulse from an explicit rest state', () => {
    render(<OriginalRoleButton owner={{ id: 31, name: 'Data Platform Lead' }} onOpen={vi.fn()} />);

    const button = screen.getByRole('button', {
      name: 'Open original role Data Platform Lead #31',
    });
    expect(button).toHaveAttribute('data-motion-role-origin', 'two-beat');
    expect(button.getAttribute('data-initial')).toContain('boxShadow');
    expect(button).toHaveTextContent('Original: Data Platform Lead #31');
  });

  it('is static under reduced motion', () => {
    motionState.reduced = true;
    render(<OriginalRoleButton owner={{ id: 31, name: 'Data Platform Lead' }} onOpen={vi.fn()} />);

    const button = screen.getByRole('button', {
      name: 'Open original role Data Platform Lead #31',
    });
    expect(button).toHaveAttribute('data-motion-role-origin', 'static');
    expect(button).toHaveAttribute('data-initial', 'false');
  });

  it('describes an ATS link without coupling role lifecycle state', () => {
    render(
      <RoleFamilyHeaderNote
        providerLabel="Workable"
        role={{
          id: 47,
          name: 'AI Engineer',
          role_kind: 'sister',
          ats_owner_role_id: 31,
          ats_owner_role_name: 'Data Platform Lead',
          sister_role_count: 2,
        }}
      />,
    );

    expect(screen.getByRole('note')).toHaveTextContent(
      'Independent related role with a Workable link to Data Platform Lead #31. Each role keeps its own candidate membership, scores, decisions, and pipeline state.',
    );
  });
});
