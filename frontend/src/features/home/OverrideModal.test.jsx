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

// The stage-pick mechanism is exercised by the "Advance instead" override on
// a reject card (action 'advance') — the one remaining override that still
// requires a Workable stage. ("Skip & advance" now reclassifies into the
// advance queue with no stage pick, so it no longer drives this modal path.)
const advanceInsteadAlt = {
  action: 'advance',
  label: 'Advance instead',
  kicker: 'OVERRIDE TO ADVANCE',
  headline: 'Advance {name} instead?',
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

  it('blocks submit until a Workable stage pill is clicked (override mode)', () => {
    const onSubmitted = vi.fn();
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
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

    // Click a stage pill — now enabled.
    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    expect(confirm).not.toBeDisabled();
  });

  it('sends workable_target_stage on Advance-instead override', async () => {
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Technical interview/i }));
    fireEvent.change(screen.getByLabelText(/Why\?/i), {
      target: { value: 'Internal referral — pre-vetted' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => {
      expect(agentApi.overrideDecision).toHaveBeenCalled();
    });
    const [decisionId, payload] = agentApi.overrideDecision.mock.calls[0];
    expect(decisionId).toBe(42);
    expect(payload.override_action).toBe('advance');
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

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
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

  it('marks the candidate\'s current Workable stage pill as disabled', () => {
    render(
      <OverrideModal
        decision={{ ...baseDecision, workable_stage: 'phone-screen' }}
        alternative={advanceInsteadAlt}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );
    const currentPill = screen.getByRole('radio', { name: /Phone screen.*Current/i });
    expect(currentPill).toBeDisabled();
    const otherPill = screen.getByRole('radio', { name: /Technical interview/i });
    expect(otherPill).not.toBeDisabled();
  });

  it('shows a "no advance stages" hint when the role has no Workable stages', () => {
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={[]}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/no advance stages/i),
    ).toBeInTheDocument();
  });

  it('never offers Sourced/Applied as an advance target — they are pre-application', () => {
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={[
          { slug: 'sourced', name: 'Sourced', kind: 'sourced' },
          { slug: 'applied', name: 'Applied', kind: 'applied' },
          { slug: 'technical-interview', name: 'Technical Interview', kind: 'assessment' },
        ]}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );
    expect(screen.queryByRole('radio', { name: /Sourced/i })).toBeNull();
    expect(screen.queryByRole('radio', { name: /Applied/i })).toBeNull();
    expect(screen.getByRole('radio', { name: /Technical Interview/i })).toBeInTheDocument();
  });

  it('shows the "no advance stages" hint for a job that only has Sourced/Applied', () => {
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={[
          { slug: 'sourced', name: 'Sourced', kind: 'sourced' },
          { slug: 'applied', name: 'Applied', kind: 'applied' },
        ]}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );
    expect(screen.getByText(/no advance stages/i)).toBeInTheDocument();
    expect(screen.queryByRole('radio')).toBeNull();
  });
});
