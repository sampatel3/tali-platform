import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { processRole } = vi.hoisted(() => ({ processRole: vi.fn() }));

vi.mock('../../shared/api', () => ({ roles: { processRole } }));

import { ProcessCandidatesDialog } from './ProcessCandidatesDialog';

describe('ProcessCandidatesDialog', () => {
  beforeEach(() => {
    processRole.mockReset().mockResolvedValue({
      data: {
        fetch_cvs: { will_attempt: 2, no_cv_no_workable: 0 },
        pre_screen: { will_run: 2 },
        score: { will_run: 2 },
        graph_sync: { will_run: 2, estimated_cost_cents: 4 },
      },
    });
  });

  it('previews and submits the full unified processing cascade', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <ProcessCandidatesDialog
        open
        roleId={42}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    await waitFor(() => expect(processRole).toHaveBeenCalledWith(
      42,
      expect.objectContaining({ fetch_cvs: true, pre_screen: true, score: 'new', sync_graph: true }),
      { dry_run: true },
    ));
    fireEvent.click(await screen.findByRole('button', { name: /Run 4 steps/i }));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith({
      fetch_cvs: true,
      refresh_cvs: false,
      pre_screen: true,
      refresh_pre_screen: false,
      score: 'new',
      sync_graph: true,
      refresh_graph: false,
    }));
  });
});
