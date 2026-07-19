import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  approveDecision: vi.fn(),
  listDecisions: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock('../../../shared/api', () => ({
  agent: {
    approveDecision: (...args) => mocks.approveDecision(...args),
    listDecisions: (...args) => mocks.listDecisions(...args),
    overrideDecision: vi.fn(),
    reEvaluateDecision: vi.fn(),
  },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ showToast: mocks.showToast }),
}));

import { PendingAgentDecisionsPanel } from './PendingAgentDecisionsPanel';

const roleFamily = {
  owner: { id: 31, name: 'Data Platform Lead' },
  related: [{ id: 47, name: 'AI Engineer' }],
};

const rejectDecision = {
  id: 7,
  application_id: 90,
  candidate_name: 'Aisha Khan',
  confidence: 0.91,
  confidence_band: 'high',
  created_at: '2026-07-16T10:00:00Z',
  decision_type: 'reject',
  model_version: 'deterministic',
  prompt_version: 'policy-v1',
  reasoning: 'A required skill is missing.',
  role_family: roleFamily,
};

describe('PendingAgentDecisionsPanel linked-family authority', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listDecisions.mockResolvedValue({ data: [rejectDecision] });
    mocks.approveDecision.mockResolvedValue({ data: { status: 'processing' } });
  });

  it('submits the exact family shown beside a reject approval', async () => {
    render(
      <PendingAgentDecisionsPanel
        role={{ id: 31, agentic_mode_enabled: true }}
        onAfterAction={vi.fn()}
      />,
    );

    await screen.findByText('Aisha Khan');
    fireEvent.click(screen.getByRole('button', {
      name: /Approve agent recommendation for Aisha Khan/i,
    }));

    await waitFor(() => expect(mocks.approveDecision).toHaveBeenCalledWith(7, {
      expected_decision_type: 'reject',
      expected_role_family: roleFamily,
    }));
    expect(mocks.showToast).toHaveBeenCalledWith(
      'Approved agent recommendation #7',
      'success',
    );
  });

  it('refreshes for a new preview and does not report success on family drift', async () => {
    const familyChanged = {
      response: {
        status: 409,
        data: {
          detail: {
            code: 'ROLE_FAMILY_CHANGED',
            message: 'The linked role family changed.',
          },
        },
      },
    };
    mocks.approveDecision.mockRejectedValueOnce(familyChanged);
    render(
      <PendingAgentDecisionsPanel
        role={{ id: 31, agentic_mode_enabled: true }}
        onAfterAction={vi.fn()}
      />,
    );

    await screen.findByText('Aisha Khan');
    fireEvent.click(screen.getByRole('button', {
      name: /Approve agent recommendation for Aisha Khan/i,
    }));

    await waitFor(() => expect(mocks.listDecisions).toHaveBeenCalledTimes(2));
    expect(mocks.showToast).toHaveBeenCalledWith(
      expect.stringMatching(/family changed.*refreshed/i),
      'warning',
    );
    expect(mocks.showToast).not.toHaveBeenCalledWith(expect.anything(), 'success');
  });
});
