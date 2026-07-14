import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import CreateSisterRoleDialog from './CreateSisterRoleDialog';

const sourceRole = {
  id: 12,
  name: 'AI Engineer',
  job_spec_text: 'Original AI engineer specification with Python, production ML, evaluation, and observability responsibilities.',
};

describe('CreateSisterRoleDialog', () => {
  it('previews the coupled roster and creates the sister scoring view', async () => {
    const createdRole = { id: 22, name: 'AI Engineer · Sister', role_kind: 'sister' };
    const rolesApi = {
      previewSister: vi.fn().mockResolvedValue({
        data: { candidates_total: 14, candidates_with_cv: 12, candidates_missing_cv: 2 },
      }),
      createSister: vi.fn().mockResolvedValue({ data: { role: createdRole } }),
    };
    const onCreated = vi.fn();

    render(
      <CreateSisterRoleDialog
        open
        sourceRole={sourceRole}
        rolesApi={rolesApi}
        onClose={vi.fn()}
        onCreated={onCreated}
      />,
    );

    expect(await screen.findByText((_, element) => (
      element?.tagName === 'DIV'
      && element.textContent === '14 candidates will appear in the sister role.'
    ))).toBeInTheDocument();
    expect(screen.getByText((_, element) => (
      element?.tagName === 'DIV'
      && element.textContent === '12 have CV text and will be scored now.'
    ))).toBeInTheDocument();
    expect(screen.getByText(/2 without CV text/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /create and score candidates/i }));

    await waitFor(() => {
      expect(rolesApi.createSister).toHaveBeenCalledWith(sourceRole.id, {
        name: 'AI Engineer · Sister',
        job_spec_text: sourceRole.job_spec_text,
      });
      expect(onCreated).toHaveBeenCalledWith(createdRole);
    });
  });

  it('requires a complete specification before scoring', () => {
    const rolesApi = {
      previewSister: vi.fn().mockReturnValue(new Promise(() => {})),
      createSister: vi.fn(),
    };
    render(
      <CreateSisterRoleDialog
        open
        sourceRole={{ ...sourceRole, job_spec_text: '' }}
        rolesApi={rolesApi}
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /create and score candidates/i })).toBeDisabled();
  });
});
