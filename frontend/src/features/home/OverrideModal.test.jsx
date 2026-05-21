// OverrideModal stage-picker contract.
//
// The modal is the single confirmation surface for both override flows
// (Reject / Skip & advance / Advance instead) AND the primary
// Advance-to-interview action. When `alternative.requireStagePick` is
// set, recruiters must pick a Workable stage from the populated <select>;
// the picked value rides on the approve / override request body as
// `workable_target_stage`.

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  agent: {
    approveDecision: vi.fn(),
    overrideDecision: vi.fn(),
  },
}));

import { agent as agentApi } from '../../shared/api';
import { OverrideModal } from './OverrideModal';

const baseDecision = {
  id: 42,
  application_id: 7,
  candidate_name: 'Tarig Elamin',
  decision_type: 'send_assessment',
};

const skipAndAdvanceAlt = {
  action: 'skip_assessment_advance',
  label: 'Skip & advance',
  kicker: 'SKIP ASSESSMENT',
  headline: 'Skip the assessment and advance {name}?',
  body: 'Pick the Workable stage to move them into.',
  confirmLabel: 'Advance',
  confirmClass: 'rq-approve',
  requireStagePick: true,
};

const primaryAdvance = {
  mode: 'approve',
  kicker: 'ADVANCE',
  headline: 'Advance {name} to the next stage?',
  body: 'Pick the Workable stage to move them into.',
  confirmLabel: 'Advance',
  confirmClass: 'rq-approve',
  requireStagePick: true,
};

const stages = [
  { slug: 'phone-screen', name: 'Phone screen' },
  { slug: 'tech-interview', name: 'Technical interview' },
];

describe('OverrideModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    agentApi.overrideDecision.mockResolvedValue({ data: { ok: true } });
    agentApi.approveDecision.mockResolvedValue({ data: { ok: true } });
  });

  it('blocks submit until a Workable stage is picked (override mode)', () => {
    const onSubmitted = vi.fn();
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={skipAndAdvanceAlt}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={onSubmitted}
      />,
    );

    const confirm = screen.getByRole('button', { name: 'Advance' });
    expect(confirm).toBeDisabled();

    // Type the required "why" — still blocked because stage isn't picked.
    fireEvent.change(screen.getByLabelText(/Why\?/i), {
      target: { value: 'Internal referral' },
    });
    expect(confirm).toBeDisabled();

    // Pick a stage — now enabled.
    fireEvent.change(screen.getByLabelText(/Move to which Workable stage/i), {
      target: { value: 'phone-screen' },
    });
    expect(confirm).not.toBeDisabled();
  });

  it('sends workable_target_stage on Skip & advance override', async () => {
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={skipAndAdvanceAlt}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Move to which Workable stage/i), {
      target: { value: 'tech-interview' },
    });
    fireEvent.change(screen.getByLabelText(/Why\?/i), {
      target: { value: 'Internal referral — pre-vetted' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => {
      expect(agentApi.overrideDecision).toHaveBeenCalled();
    });
    const [decisionId, payload] = agentApi.overrideDecision.mock.calls[0];
    expect(decisionId).toBe(42);
    expect(payload.override_action).toBe('skip_assessment_advance');
    expect(payload.workable_target_stage).toBe('tech-interview');
    expect(payload.note).toBe('Internal referral — pre-vetted');
  });

  it('uses /approve endpoint with workable_target_stage when mode=approve (primary Advance)', async () => {
    render(
      <OverrideModal
        decision={{ ...baseDecision, decision_type: 'advance_to_interview' }}
        alternative={primaryAdvance}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );

    // Primary advance: the "why" textarea is optional (no required hint).
    expect(screen.getByText(/Note \(optional\)/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Move to which Workable stage/i), {
      target: { value: 'phone-screen' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => {
      expect(agentApi.approveDecision).toHaveBeenCalled();
    });
    expect(agentApi.overrideDecision).not.toHaveBeenCalled();
    const [decisionId, payload] = agentApi.approveDecision.mock.calls[0];
    expect(decisionId).toBe(42);
    expect(payload.workable_target_stage).toBe('phone-screen');
    expect(payload.override_action).toBeUndefined();
  });

  it('shows a "no stages found" hint when the role has no Workable stages', () => {
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={skipAndAdvanceAlt}
        workableStages={[]}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/No Workable stages found for this role/i),
    ).toBeInTheDocument();
  });
});
