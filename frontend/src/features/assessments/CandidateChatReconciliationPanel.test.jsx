import React from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CandidateChatReconciliationPanel } from './CandidateChatReconciliationPanel';

const operation = {
  operation_id: `chatrec_${'a'.repeat(64)}`,
  request_reference: `chatreq_${'b'.repeat(32)}`,
  scope: 'request',
  issue_code: 'provider_checkpoint_malformed',
  state: 'agent_completed',
  checkpoint_present: true,
  finalization_input_present: true,
  can_close_without_replay: true,
};

const assessment = (overrides = {}) => ({
  id: 42,
  candidate_chat_reconciliation: {
    reconciliation_required: true,
    operation_count: 1,
    can_reconcile: true,
    operations: [],
    ...overrides,
  },
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

describe('CandidateChatReconciliationPanel', () => {
  it('shows safe status to members without exposing recovery controls', () => {
    render(
      <CandidateChatReconciliationPanel
        assessment={assessment({ can_reconcile: false })}
        assessmentsApi={{}}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('AI chat recovery required');
    expect(screen.getByText(/workspace owner must review/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /review recovery/i })).toBeNull();
    expect(screen.queryByText(/provider-secret|request-corrupt/i)).toBeNull();
  });

  it('loads fresh evidence and cannot close it before explicit attestation', async () => {
    const listCandidateChatReconciliations = vi.fn().mockResolvedValue({
      data: { operations: [operation] },
    });
    render(
      <CandidateChatReconciliationPanel
        assessment={assessment()}
        assessmentsApi={{ listCandidateChatReconciliations }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /review recovery/i }));
    expect(await screen.findByText('AI response checkpoint needs recovery')).toBeInTheDocument();
    expect(listCandidateChatReconciliations).toHaveBeenCalledWith(42);
    const close = screen.getByRole('button', { name: /close exact request without replay/i });
    expect(close).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/discard this unresolved response/i));
    expect(close).toBeEnabled();
  });

  it('sends both exact fences and the no-replay attestation', async () => {
    const listCandidateChatReconciliations = vi.fn().mockResolvedValue({
      data: { operations: [operation] },
    });
    const resolveCandidateChatReconciliation = vi.fn().mockResolvedValue({
      data: {
        status: 'reconciled_no_replay',
        candidate_chat_reconciliation: {
          reconciliation_required: false,
          operation_count: 0,
          can_reconcile: false,
          operations: [],
        },
      },
    });
    const onResolved = vi.fn();
    render(
      <CandidateChatReconciliationPanel
        assessment={assessment()}
        assessmentsApi={{
          listCandidateChatReconciliations,
          resolveCandidateChatReconciliation,
        }}
        onResolved={onResolved}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /review recovery/i }));
    fireEvent.click(await screen.findByLabelText(/discard this unresolved response/i));
    fireEvent.click(screen.getByRole('button', { name: /close exact request without replay/i }));

    await waitFor(() => expect(resolveCandidateChatReconciliation).toHaveBeenCalledWith(
      42,
      operation.operation_id,
      {
        action: 'close_without_replay',
        expected_request_reference: operation.request_reference,
        provider_outcome_discarded_attested: true,
      },
    ));
    expect(onResolved).toHaveBeenCalledWith(expect.objectContaining({
      status: 'reconciled_no_replay',
    }));
    expect(screen.getByText(/no unresolved ai chat requests remain/i)).toBeInTheDocument();
  });

  it('surfaces a stale-operation conflict without losing the guarded choice', async () => {
    const listCandidateChatReconciliations = vi.fn().mockResolvedValue({
      data: { operations: [operation] },
    });
    const resolveCandidateChatReconciliation = vi.fn().mockRejectedValue({
      response: {
        data: {
          detail: 'The candidate-chat recovery operation changed. Refresh before reconciling it.',
        },
      },
    });
    render(
      <CandidateChatReconciliationPanel
        assessment={assessment()}
        assessmentsApi={{
          listCandidateChatReconciliations,
          resolveCandidateChatReconciliation,
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /review recovery/i }));
    fireEvent.click(await screen.findByLabelText(/discard this unresolved response/i));
    fireEvent.click(screen.getByRole('button', { name: /close exact request without replay/i }));

    expect(await screen.findByText(/recovery operation changed/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/discard this unresolved response/i)).toBeInTheDocument();
  });

  it('ignores a late list response from a previous assessment', async () => {
    const pending = deferred();
    const listCandidateChatReconciliations = vi.fn().mockReturnValue(pending.promise);
    const { rerender } = render(
      <CandidateChatReconciliationPanel
        assessment={assessment()}
        assessmentsApi={{ listCandidateChatReconciliations }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /review recovery/i }));
    rerender(
      <CandidateChatReconciliationPanel
        assessment={{ ...assessment(), id: 43 }}
        assessmentsApi={{ listCandidateChatReconciliations }}
      />,
    );
    await act(async () => {
      pending.resolve({ data: { operations: [operation] } });
      await pending.promise;
    });

    expect(screen.queryByText('AI response checkpoint needs recovery')).toBeNull();
    expect(screen.getByRole('button', { name: /review recovery/i })).toBeEnabled();
  });

  it('does not apply or announce a late resolution from a previous assessment', async () => {
    const pending = deferred();
    const listCandidateChatReconciliations = vi.fn().mockResolvedValue({
      data: { operations: [operation] },
    });
    const resolveCandidateChatReconciliation = vi.fn().mockReturnValue(pending.promise);
    const onResolved = vi.fn();
    const api = {
      listCandidateChatReconciliations,
      resolveCandidateChatReconciliation,
    };
    const { rerender } = render(
      <CandidateChatReconciliationPanel
        assessment={assessment()}
        assessmentsApi={api}
        onResolved={onResolved}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /review recovery/i }));
    fireEvent.click(await screen.findByLabelText(/discard this unresolved response/i));
    fireEvent.click(screen.getByRole('button', { name: /close exact request without replay/i }));
    rerender(
      <CandidateChatReconciliationPanel
        assessment={{ ...assessment(), id: 43 }}
        assessmentsApi={api}
        onResolved={onResolved}
      />,
    );
    await act(async () => {
      pending.resolve({
        data: {
          status: 'reconciled_no_replay',
          candidate_chat_reconciliation: { operations: [] },
        },
      });
      await pending.promise;
    });

    expect(onResolved).not.toHaveBeenCalled();
    expect(screen.queryByText(/no unresolved ai chat requests remain/i)).toBeNull();
    expect(screen.getByRole('button', { name: /review recovery/i })).toBeEnabled();
  });
});
