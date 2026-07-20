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
    listDecisions: vi.fn(),
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

const skipAssessmentAdvance = {
  action: 'skip_assessment_advance',
  label: 'Skip & advance',
  kicker: 'SKIP ASSESSMENT',
  headline: 'Move {name} to the advance queue?',
  body: 'Queues them as an advance.',
  confirmLabel: 'Move to advance queue',
  confirmClass: 'rq-approve',
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
    agentApi.listDecisions.mockResolvedValue({ data: [] });
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

  it('blocks primary approval when the decision inputs changed', () => {
    render(
      <OverrideModal
        decision={{
          ...baseDecision,
          decision_type: 'advance_to_interview',
          is_stale: true,
          staleness_reasons: ['score_generation_changed'],
        }}
        alternative={primaryAdvance}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    expect(screen.getByRole('button', { name: 'Advance' })).toBeDisabled();
    expect(agentApi.approveDecision).not.toHaveBeenCalled();
  });

  it('forces only the bounded old-engine approval', async () => {
    render(
      <OverrideModal
        decision={{
          ...baseDecision,
          decision_type: 'advance_to_interview',
          is_stale: true,
          staleness_reasons: ['engine_outdated'],
        }}
        alternative={primaryAdvance}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => expect(agentApi.approveDecision).toHaveBeenCalled());
    expect(agentApi.approveDecision.mock.calls[0][2]).toEqual({ force: true });
  });

  it('silently reconciles an approval timeout that the server already accepted', async () => {
    const onClose = vi.fn();
    const onSubmitted = vi.fn();
    agentApi.approveDecision.mockRejectedValue({ code: 'ECONNABORTED' });
    agentApi.listDecisions.mockResolvedValue({
      data: [{ ...baseDecision, status: 'processing' }],
    });
    render(
      <OverrideModal
        decision={{ ...baseDecision, decision_type: 'advance_to_interview' }}
        alternative={primaryAdvance}
        workableStages={stages}
        onClose={onClose}
        onSubmitted={onSubmitted}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledOnce());
    expect(agentApi.approveDecision).toHaveBeenCalledOnce();
    expect(agentApi.listDecisions).toHaveBeenCalledWith(
      {
        application_id: 7,
        status: 'current',
        limit: 50,
      },
      { timeout: 10000 },
    );
    expect(onSubmitted).toHaveBeenCalledWith(
      expect.objectContaining({ id: 42, status: 'processing' }),
    );
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Couldn't approve/i)).not.toBeInTheDocument();
  });

  it('blocks a duplicate approval when a timeout cannot be reconciled', async () => {
    const onClose = vi.fn();
    agentApi.approveDecision.mockRejectedValue({ code: 'ECONNABORTED' });
    agentApi.listDecisions.mockResolvedValue({
      data: [{ ...baseDecision, status: 'pending' }],
    });
    render(
      <OverrideModal
        decision={{ ...baseDecision, decision_type: 'advance_to_interview' }}
        alternative={primaryAdvance}
        workableStages={stages}
        onClose={onClose}
        onSubmitted={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    expect(
      await screen.findByText(/We couldn't confirm this action\. Refresh before taking another action\./i),
    ).toBeInTheDocument();
    const advance = screen.getByRole('button', { name: 'Advance' });
    expect(advance).toBeDisabled();
    fireEvent.click(advance);
    expect(agentApi.approveDecision).toHaveBeenCalledOnce();
    expect(screen.queryByText(/try again/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh status' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByRole('dialog').parentElement);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('blocks a duplicate approval when the server reports an ambiguous outcome', async () => {
    agentApi.approveDecision.mockRejectedValue({
      response: {
        status: 500,
        data: {
          detail: "We couldn't confirm this action. Refresh before taking another action.",
        },
      },
    });
    render(
      <OverrideModal
        decision={{ ...baseDecision, decision_type: 'advance_to_interview' }}
        alternative={primaryAdvance}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    expect(
      await screen.findByText(/We couldn't confirm this action\. Refresh before taking another action\./i),
    ).toBeInTheDocument();
    const advance = screen.getByRole('button', { name: 'Advance' });
    expect(advance).toBeDisabled();
    fireEvent.click(advance);
    expect(agentApi.approveDecision).toHaveBeenCalledOnce();
    expect(agentApi.listDecisions).not.toHaveBeenCalled();
  });

  it('hands an ambiguous approval to its parent lock before closing', async () => {
    const onClose = vi.fn();
    const onOutcomeUnknown = vi.fn();
    agentApi.approveDecision.mockRejectedValue({ code: 'ETIMEDOUT' });
    agentApi.listDecisions.mockResolvedValue({
      data: [{ ...baseDecision, status: 'pending' }],
    });
    render(
      <OverrideModal
        decision={{ ...baseDecision, decision_type: 'advance_to_interview' }}
        alternative={primaryAdvance}
        workableStages={stages}
        onClose={onClose}
        onSubmitted={vi.fn()}
        onOutcomeUnknown={onOutcomeUnknown}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => expect(onOutcomeUnknown).toHaveBeenCalledWith(
      expect.objectContaining({ decision_id: 42, outcome_unknown: true }),
    ));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('treats an override timeout as outcome-unknown instead of retryable', async () => {
    const onOutcomeUnknown = vi.fn();
    agentApi.overrideDecision.mockRejectedValue({ code: 'ECONNABORTED' });
    agentApi.listDecisions.mockResolvedValue({ data: [] });
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
        onOutcomeUnknown={onOutcomeUnknown}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    fireEvent.change(screen.getByLabelText(/Why\?/i), {
      target: { value: 'Internal referral' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => expect(onOutcomeUnknown).toHaveBeenCalledOnce());
    expect(agentApi.listDecisions).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Couldn't override — try again/i)).not.toBeInTheDocument();
  });

  it('reconciles an Axios HTTP 408 and hands a locked unknown outcome to the parent', async () => {
    const onClose = vi.fn();
    const onOutcomeUnknown = vi.fn();
    const onRejected = vi.fn();
    agentApi.overrideDecision.mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 408,
        data: { detail: 'Request timed out.' },
      },
    });
    agentApi.listDecisions.mockResolvedValue({
      data: [{ ...baseDecision, status: 'pending' }],
    });
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={stages}
        onClose={onClose}
        onSubmitted={vi.fn()}
        onOutcomeUnknown={onOutcomeUnknown}
        onRejected={onRejected}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    fireEvent.change(screen.getByLabelText(/Why\?/i), {
      target: { value: 'Internal referral' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => expect(agentApi.listDecisions).toHaveBeenCalledWith(
      {
        application_id: 7,
        status: 'current',
        limit: 50,
      },
      { timeout: 10000 },
    ));
    await waitFor(() => expect(onOutcomeUnknown).toHaveBeenCalledWith(
      expect.objectContaining({ decision_id: 42, outcome_unknown: true }),
    ));
    expect(onRejected).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.getByText(
      /We couldn't confirm this action\. Refresh before taking another action\./i,
    )).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Advance' })).toBeDisabled();
    expect(screen.queryByText(/try again|retry/i)).not.toBeInTheDocument();
  });

  it('silently reconciles an override timeout that the server already completed', async () => {
    const onClose = vi.fn();
    const onSubmitted = vi.fn();
    agentApi.overrideDecision.mockRejectedValue({ code: 'ECONNABORTED' });
    agentApi.listDecisions.mockResolvedValue({
      data: [{ ...baseDecision, status: 'overridden' }],
    });
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={stages}
        onClose={onClose}
        onSubmitted={onSubmitted}
      />,
    );

    fireEvent.click(screen.getByRole('radio', { name: /Phone screen/i }));
    fireEvent.change(screen.getByLabelText(/Why\?/i), {
      target: { value: 'Internal referral' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Advance' }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith(
      expect.objectContaining({ id: 42, status: 'overridden' }),
    ));
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Refresh before taking another action/i)).not.toBeInTheDocument();
  });

  it('silently reconciles a timed-out skip when the row was reclassified to advance', async () => {
    const onClose = vi.fn();
    const onSubmitted = vi.fn();
    agentApi.overrideDecision.mockRejectedValue({ code: 'ECONNABORTED' });
    agentApi.listDecisions.mockResolvedValue({
      data: [{
        ...baseDecision,
        status: 'pending',
        decision_type: 'advance_to_interview',
        evidence: { reclassified_by: 'recruiter_skip_assessment_advance' },
      }],
    });
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={skipAssessmentAdvance}
        workableStages={stages}
        onClose={onClose}
        onSubmitted={onSubmitted}
      />,
    );

    fireEvent.change(screen.getByLabelText(/Why\?/i), {
      target: { value: 'Internal referral' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Move to advance queue' }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 42,
        status: 'pending',
        decision_type: 'advance_to_interview',
      }),
    ));
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Refresh before taking another action/i)).not.toBeInTheDocument();
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

  it('locks body scroll while mounted and restores it on unmount', () => {
    document.body.style.overflow = 'auto';
    const { unmount } = render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).toBe('auto');
  });

  it('labels the dialog via aria-labelledby pointing at the headline', () => {
    render(
      <OverrideModal
        decision={baseDecision}
        alternative={advanceInsteadAlt}
        workableStages={stages}
        onClose={vi.fn()}
        onSubmitted={vi.fn()}
      />,
    );
    const dialog = screen.getByRole('dialog');
    const labelId = dialog.getAttribute('aria-labelledby');
    expect(labelId).toBeTruthy();
    const heading = document.getElementById(labelId);
    expect(heading).toBeTruthy();
    expect(heading).toHaveTextContent(/Advance Tarig Elamin instead\?/i);
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
