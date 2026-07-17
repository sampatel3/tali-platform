import {
  act, fireEvent, render, screen, waitFor, within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AtsReconciliationPanel,
  hasExactAtsResolution,
  needsAtsReconciliation,
} from './AtsReconciliationPanel';

const mocks = vi.hoisted(() => ({
  check: vi.fn(),
  resolve: vi.fn(),
}));

vi.mock('../../shared/api', () => ({
  roles: {
    checkApplicationAtsReconciliation: mocks.check,
    resolveApplicationAtsReconciliation: mocks.resolve,
  },
}));

const receipt = (key, overrides = {}) => ({
  operation_id: `${key}:operation`,
  provider: 'workable',
  provider_target_id: `${key}:target`,
  status: 'manual_reconciliation_required',
  provider_outcome_uncertain: true,
  manual_reconciliation_required: true,
  ...overrides,
});

const application = (state, outcome = 'open') => ({
  id: 41,
  application_outcome: outcome,
  integration_sync_state: state,
});

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
};

describe('AtsReconciliationPanel', () => {
  beforeEach(() => {
    mocks.check.mockReset();
    mocks.resolve.mockReset();
    mocks.resolve.mockResolvedValue({ data: { ok: true } });
  });

  it('shows every unresolved receipt family, including CV-gap rejection', () => {
    render(<AtsReconciliationPanel application={application({
      auto_reject_operation: receipt('auto_reject_operation'),
      cv_gap_rejection_operation: receipt('cv_gap_rejection_operation'),
      outcome_writeback: receipt('outcome_writeback'),
      outcome_writeback_reconciliation: receipt('outcome_writeback_reconciliation'),
    })} />);

    expect(screen.getByText('Automatic rejection')).toBeInTheDocument();
    expect(screen.getByText('CV-gap rejection')).toBeInTheDocument();
    expect(screen.getByText('Outcome write-back')).toBeInTheDocument();
    expect(screen.getByText('Prior outcome write-back')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Check ATS status' })).toHaveLength(4);
  });

  it('checks and confirms only an exact matching stage-move observation', async () => {
    const current = receipt('stage_move_operation', {
      target_stage: 'technical-interview',
      provider_remote_stage: 'technical-interview',
    });
    mocks.check.mockResolvedValue({
      data: {
        observation_id: 'stage-observation-match',
        provider_remote_stage: 'technical-interview',
        expected_remote_stage: 'technical-interview',
        remote_matches_expected: true,
      },
    });
    render(<AtsReconciliationPanel application={application({
      stage_move_operation: current,
    })} />);

    fireEvent.click(screen.getByRole('button', { name: 'Check ATS status' }));
    expect(await screen.findByText(/reports stage/)).toHaveTextContent('technical-interview');
    fireEvent.click(screen.getByRole('button', { name: 'Confirm completed stage move' }));

    await waitFor(() => expect(mocks.resolve).toHaveBeenCalledWith(41, {
      receipt_key: 'stage_move_operation',
      operation_id: 'stage_move_operation:operation',
      provider: 'workable',
      provider_target_id: 'stage_move_operation:target',
      observation_id: 'stage-observation-match',
      disposition: 'confirm_stage_move',
    }));
  });

  it('finishes a Decision Hub action only after the exact provider effect is observed', async () => {
    const current = receipt('decision_provider_operation', {
      operation_action: 'reject',
      provider_remote_stage: 'disqualified',
    });
    mocks.check.mockResolvedValue({
      data: {
        observation_id: 'decision-observation-match',
        provider_remote_stage: 'disqualified',
        expected_remote_stage: 'disqualified',
        provider_effect_matches: true,
      },
    });
    render(<AtsReconciliationPanel application={application({
      decision_provider_operation: current,
    })} />);

    fireEvent.click(screen.getByRole('button', { name: 'Check ATS status' }));
    const confirm = await screen.findByRole('button', {
      name: 'Confirm ATS effect and finish Decision Hub action',
    });
    fireEvent.click(confirm);

    await waitFor(() => expect(mocks.resolve).toHaveBeenCalledWith(41, {
      receipt_key: 'decision_provider_operation',
      operation_id: 'decision_provider_operation:operation',
      provider: 'workable',
      provider_target_id: 'decision_provider_operation:target',
      observation_id: 'decision-observation-match',
      disposition: 'confirm_decision_provider_effect',
    }));
  });

  it('never offers a blind Decision Hub retry when the provider effect is absent', async () => {
    mocks.check.mockResolvedValue({
      data: {
        observation_id: 'decision-observation-mismatch',
        provider_remote_stage: 'applied',
        expected_remote_stage: 'disqualified',
        provider_effect_matches: false,
      },
    });
    render(<AtsReconciliationPanel application={application({
      decision_provider_operation: receipt('decision_provider_operation', {
        operation_action: 'reject',
        provider_remote_stage: 'disqualified',
      }),
    })} />);

    fireEvent.click(screen.getByRole('button', { name: 'Check ATS status' }));
    expect(await screen.findByText(/will not be retried automatically/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
    expect(mocks.resolve).not.toHaveBeenCalled();
  });

  it('keeps archived stage evidence visible but exposes no check, confirm, or retry path', () => {
    const current = receipt('stage_move_operation', {
      operation_id: 'stage_move_operation:current',
      provider_target_id: 'stage_move_operation:current-target',
      target_stage: 'onsite-interview',
      provider_remote_stage: 'onsite-interview',
    });
    const archived = receipt('stage_move_operation', {
      operation_id: 'stage_move_operation:archived',
      provider_target_id: 'stage_move_operation:archived-target',
      target_stage: 'technical-interview',
      provider_remote_stage: 'technical-interview',
      reconciliation_observation: {
        observation_id: 'archived-stage-observation',
        provider_remote_stage: 'screening',
        expected_remote_stage: 'technical-interview',
        remote_matches_expected: false,
      },
    });
    render(<AtsReconciliationPanel application={application({
      stage_move_operation: current,
      stage_move_operation_history: [archived],
    })} />);

    const archivedCard = screen.getByText(/Archived evidence/).closest('article');
    expect(within(archivedCard).getByRole('status')).toHaveTextContent(/read-only evidence/i);
    expect(within(archivedCard).getByText(/reports stage/)).toHaveTextContent(
      'screening; the exact expected stage is technical-interview',
    );
    expect(within(archivedCard).queryByRole('button', {
      name: /check|confirm|retry/i,
    })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check ATS status' })).toBeEnabled();
    expect(mocks.check).not.toHaveBeenCalled();
    expect(mocks.resolve).not.toHaveBeenCalled();
  });

  it('keeps archived Decision Hub evidence visible but exposes no check or confirm path', () => {
    const current = receipt('decision_provider_operation', {
      operation_id: 'decision_provider_operation:current',
      provider_target_id: 'decision_provider_operation:current-target',
      operation_action: 'reject',
      provider_remote_stage: 'disqualified',
    });
    const archived = receipt('decision_provider_operation', {
      operation_id: 'decision_provider_operation:archived',
      provider_target_id: 'decision_provider_operation:archived-target',
      operation_action: 'advance',
      provider_remote_stage: 'interview',
      reconciliation_observation: {
        observation_id: 'archived-decision-observation',
        provider_remote_stage: 'interview',
        expected_remote_stage: 'interview',
        provider_effect_matches: true,
      },
    });
    render(<AtsReconciliationPanel application={application({
      decision_provider_operation: current,
      decision_provider_operation_history: [archived],
    })} />);

    const archivedCard = screen.getByText(/Archived evidence/).closest('article');
    expect(within(archivedCard).getByRole('status')).toHaveTextContent(/read-only evidence/i);
    expect(within(archivedCard).getByText(/reports stage/)).toHaveTextContent(
      'interview; the exact expected stage is interview',
    );
    expect(within(archivedCard).queryByRole('button', {
      name: /check|confirm/i,
    })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check ATS status' })).toBeEnabled();
    expect(mocks.check).not.toHaveBeenCalled();
    expect(mocks.resolve).not.toHaveBeenCalled();
  });

  it('checks exact related-roster identity then explicitly confirms a match', async () => {
    const current = receipt('cv_gap_rejection_operation');
    mocks.check.mockResolvedValue({
      data: {
        observation_id: 'observation-1',
        remote_outcome: 'open',
        remote_status: 'Applied',
      },
    });
    const onResolved = vi.fn().mockResolvedValue(undefined);
    render(<AtsReconciliationPanel
      application={application({ cv_gap_rejection_operation: current })}
      actingRoleId={91}
      onResolved={onResolved}
    />);

    fireEvent.click(screen.getByRole('button', { name: 'Check ATS status' }));
    expect(await screen.findByText(/Workable reports/)).toHaveTextContent('open');
    expect(mocks.check).toHaveBeenCalledWith(41, {
      receipt_key: 'cv_gap_rejection_operation',
      operation_id: 'cv_gap_rejection_operation:operation',
      provider: 'workable',
      provider_target_id: 'cv_gap_rejection_operation:target',
      acting_role_id: 91,
    });

    fireEvent.click(screen.getByRole('button', { name: 'Confirm ATS and Taali match' }));
    await waitFor(() => expect(mocks.resolve).toHaveBeenCalledWith(41, {
      receipt_key: 'cv_gap_rejection_operation',
      operation_id: 'cv_gap_rejection_operation:operation',
      provider: 'workable',
      provider_target_id: 'cv_gap_rejection_operation:target',
      acting_role_id: 91,
      observation_id: 'observation-1',
      disposition: 'confirm_provider_matches_local',
    }));
    expect(onResolved).toHaveBeenCalledWith(41);
  });

  it('offers an explicit align action only for a safe open/rejected mismatch', async () => {
    mocks.check.mockResolvedValue({
      data: {
        observation_id: 'observation-rejected',
        remote_outcome: 'rejected',
        remote_status: 'Disqualified',
      },
    });
    render(<AtsReconciliationPanel application={application({
      auto_reject_operation: receipt('auto_reject_operation'),
    })} />);

    fireEvent.click(screen.getByRole('button', { name: 'Check ATS status' }));
    const align = await screen.findByRole('button', { name: 'Align Taali to ATS: rejected' });
    fireEvent.click(align);

    await waitFor(() => expect(mocks.resolve).toHaveBeenCalledWith(
      41,
      expect.objectContaining({ disposition: 'align_local_to_provider' }),
    ));
  });

  it('observes unknown provider status but never offers resolution', async () => {
    mocks.check.mockResolvedValue({
      data: {
        observation_id: 'observation-unknown',
        remote_outcome: 'unknown',
        remote_status: 'Custom Unmapped Status',
      },
    });
    render(<AtsReconciliationPanel application={application({
      outcome_writeback: receipt('outcome_writeback'),
    })} />);

    fireEvent.click(screen.getByRole('button', { name: 'Check ATS status' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /cannot safely be classified as open or rejected/i,
    );
    expect(screen.queryByRole('button', { name: /Confirm ATS|Align Taali/ })).not.toBeInTheDocument();
    expect(mocks.resolve).not.toHaveBeenCalled();
  });

  it('does not render an exactly resolved receipt even when original ambiguity flags remain', () => {
    const resolved = receipt('auto_reject_operation', {
      reconciliation_status: 'resolved',
      resolved_operation_id: 'auto_reject_operation:operation',
      resolved_receipt_key: 'auto_reject_operation',
      reconciliation_resolved_by_actor_id: 7,
      reconciliation_resolved_by_actor_type: 'recruiter',
      reconciliation_disposition: 'confirm_provider_matches_local',
      reconciliation_observation_id: 'obs-7',
      reconciliation_evidence: {
        receipt_key: 'auto_reject_operation',
        operation_id: 'auto_reject_operation:operation',
        provider: 'workable',
        provider_target_id: 'auto_reject_operation:target',
        observation_id: 'obs-7',
        remote_outcome: 'open',
      },
    });

    expect(resolved.manual_reconciliation_required).toBe(true);
    expect(hasExactAtsResolution(resolved, 'auto_reject_operation')).toBe(true);
    expect(needsAtsReconciliation(resolved, 'auto_reject_operation')).toBe(false);
    const { container } = render(<AtsReconciliationPanel application={application({
      auto_reject_operation: resolved,
    })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('rejects cross-family copied resolution evidence in the UI contract', () => {
    const copied = receipt('outcome_writeback', {
      reconciliation_status: 'resolved',
      resolved_operation_id: 'outcome_writeback:operation',
      resolved_receipt_key: 'auto_reject_operation',
      reconciliation_resolved_by_actor_id: 7,
      reconciliation_resolved_by_actor_type: 'recruiter',
      reconciliation_disposition: 'confirm_provider_matches_local',
      reconciliation_observation_id: 'obs-cross',
      reconciliation_evidence: {
        receipt_key: 'auto_reject_operation',
        operation_id: 'outcome_writeback:operation',
        provider: 'workable',
        provider_target_id: 'outcome_writeback:target',
        observation_id: 'obs-cross',
        remote_outcome: 'open',
      },
    });

    expect(hasExactAtsResolution(copied, 'outcome_writeback')).toBe(false);
    expect(needsAtsReconciliation(copied, 'outcome_writeback')).toBe(true);
  });

  it('keeps controls visible but disabled for read-only viewers', () => {
    render(<AtsReconciliationPanel
      application={application({ outcome_writeback: receipt('outcome_writeback') })}
      canMutate={false}
    />);

    const card = screen.getByText('Outcome write-back').closest('article');
    expect(within(card).getByRole('button', { name: 'Check ATS status' })).toBeDisabled();
  });

  it('does not mislabel an unknown legacy provider as Workable', () => {
    render(<AtsReconciliationPanel application={application({
      auto_reject_operation: receipt('auto_reject_operation', {
        provider: '',
        provider_target_id: '',
      }),
    })} />);

    expect(screen.getByText(/Automatic rejection/).closest('span')).toHaveTextContent(
      'Automatic rejection · The ATS',
    );
  });

  it('never carries an observation onto a newer operation for the same application', async () => {
    mocks.check.mockResolvedValue({
      data: {
        observation_id: 'old-observation',
        remote_outcome: 'open',
        remote_status: 'Applied',
      },
    });
    const { rerender } = render(<AtsReconciliationPanel application={application({
      outcome_writeback: receipt('outcome_writeback'),
    })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check ATS status' }));
    expect(await screen.findByText(/Workable reports/)).toHaveTextContent('open');

    rerender(<AtsReconciliationPanel application={application({
      outcome_writeback: receipt('outcome_writeback', {
        operation_id: 'outcome_writeback:new-operation',
        provider_target_id: 'outcome_writeback:new-target',
      }),
    })} />);

    expect(screen.queryByText(/Workable reports/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check ATS status' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Confirm ATS|Align Taali/ })).not.toBeInTheDocument();
  });

  it('discards an outstanding observation when the same receipt identity advances generation', async () => {
    const check = deferred();
    mocks.check.mockReturnValueOnce(check.promise);
    const { rerender } = render(<AtsReconciliationPanel application={application({
      outcome_writeback: receipt('outcome_writeback', { updated_at: '2026-07-17T10:00:00Z' }),
    })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check ATS status' }));

    rerender(<AtsReconciliationPanel application={application({
      outcome_writeback: receipt('outcome_writeback', { updated_at: '2026-07-17T10:01:00Z' }),
    })} />);
    await act(async () => {
      check.resolve({
        data: {
          observation_id: 'stale-observation',
          remote_outcome: 'open',
          remote_status: 'Applied',
        },
      });
      await check.promise;
    });

    expect(mocks.check).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Workable reports/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check ATS status' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Confirm ATS|Align Taali/ })).not.toBeInTheDocument();
  });
});
