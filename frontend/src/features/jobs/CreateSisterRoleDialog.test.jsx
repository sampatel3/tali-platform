import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import CreateSisterRoleDialog from './CreateSisterRoleDialog';

const sourceRole = {
  id: 12,
  version: 4,
  name: 'AI Engineer',
  ats_provider: 'workable',
  external_job_id: 'WK-12',
  job_spec_text: 'Original AI engineer specification with Python, production ML, evaluation, and observability responsibilities.',
};

describe('CreateSisterRoleDialog', () => {
  const preview = {
    source_role_id: 12,
    source_role_name: 'AI Engineer',
    source_role_version: 4,
    candidates_total: 14,
    candidates_with_cv: 12,
    candidates_missing_cv: 2,
    candidates_scoreable: 12,
    candidates_unscorable: 2,
    candidates_excluded: 0,
    estimated_cost_usd: 1,
    minimum_initial_budget_cents: 100,
    proposed_monthly_budget_cents: 5000,
    ongoing_score_cost_usd: 0.083,
  };

  it('previews the coupled roster and creates the related scoring view', async () => {
    const createdRole = { id: 22, name: 'AI Engineer · Related', role_kind: 'sister' };
    const rolesApi = {
      previewSister: vi.fn().mockResolvedValue({
        data: preview,
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
      && element.textContent === '14 candidates will appear in the related role.'
    ))).toBeInTheDocument();
    expect(screen.getByText((_, element) => (
      element?.tagName === 'DIV'
      && element.textContent === '12 have CV text and will be scored now.'
    ))).toBeInTheDocument();
    expect(screen.getByText((_, element) => (
      element?.tagName === 'DIV'
      && element.textContent === '2 without CV text will show as “Not scorable”.'
    ))).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /create and score candidates/i }));

    await waitFor(() => {
      expect(rolesApi.createSister).toHaveBeenCalledWith(sourceRole.id, {
        name: 'AI Engineer · Related',
        job_spec_text: sourceRole.job_spec_text,
        related_role_authorization: {
          expected_source_role_id: 12,
          expected_source_role_name: 'AI Engineer',
          expected_source_role_version: 4,
          expected_default_monthly_budget_cents: 5000,
          approved_max_candidates_total: 14,
          approved_max_scoreable_count: 12,
          approved_monthly_budget_cents: 5000,
        },
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

  it('blocks an inadequate cap before sending any paid mutation', async () => {
    const rolesApi = {
      previewSister: vi.fn().mockResolvedValue({ data: preview }),
      createSister: vi.fn(),
    };
    render(
      <CreateSisterRoleDialog
        open
        sourceRole={sourceRole}
        rolesApi={rolesApi}
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    );

    fireEvent.change(await screen.findByLabelText('Monthly scoring cap (USD)'), {
      target: { value: '0.50' },
    });
    expect(screen.getByRole('button', { name: /create and score candidates/i })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent(/covers the current initial scoreable roster/i);
    expect(rolesApi.createSister).not.toHaveBeenCalled();
  });

  it('refreshes a changed paid scope and requires another click without retrying', async () => {
    const refreshedPreview = {
      ...preview,
      source_role_version: 5,
      candidates_total: 15,
      candidates_scoreable: 13,
      candidates_with_cv: 13,
      minimum_initial_budget_cents: 108,
    };
    const rolesApi = {
      previewSister: vi.fn()
        .mockResolvedValueOnce({ data: preview })
        .mockResolvedValueOnce({ data: refreshedPreview }),
      createSister: vi.fn().mockRejectedValue({
        response: {
          status: 409,
          data: { detail: { code: 'RELATED_ROLE_PAID_SCOPE_CHANGED' } },
        },
      }),
    };
    render(
      <CreateSisterRoleDialog
        open
        sourceRole={sourceRole}
        rolesApi={rolesApi}
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    );

    const createButton = screen.getByRole('button', { name: /create and score candidates/i });
    await waitFor(() => expect(createButton).toBeEnabled());
    fireEvent.click(createButton);

    expect(await screen.findByRole('alert')).toHaveTextContent(/Nothing was created/i);
    expect(rolesApi.previewSister).toHaveBeenCalledTimes(2);
    expect(rolesApi.createSister).toHaveBeenCalledTimes(1);
    expect(screen.getByText((_, element) => (
      element?.tagName === 'DIV'
      && element.textContent === '15 candidates will appear in the related role.'
    ))).toBeInTheDocument();
  });

  it('names Bullhorn as the owning provider for a Bullhorn source role', async () => {
    const rolesApi = {
      previewSister: vi.fn().mockResolvedValue({
        data: {
          ...preview,
          candidates_total: 2,
          candidates_with_cv: 2,
          candidates_missing_cv: 0,
          candidates_scoreable: 2,
          candidates_unscorable: 0,
          minimum_initial_budget_cents: 17,
        },
      }),
      createSister: vi.fn(),
    };
    render(
      <CreateSisterRoleDialog
        open
        sourceRole={{
          ...sourceRole,
          ats_provider: 'bullhorn',
          external_job_id: 'BH-12',
        }}
        rolesApi={rolesApi}
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    );

    expect(await screen.findByText(/actions stay coupled to the original Bullhorn job/i))
      .toBeInTheDocument();
  });
});
