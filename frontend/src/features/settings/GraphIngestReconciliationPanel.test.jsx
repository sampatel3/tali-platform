import React from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  roles: {
    graphIngestReconciliations: vi.fn(),
    resolveGraphIngestReconciliation: vi.fn(),
  },
}));

import { roles as rolesApi } from '../../shared/api';
import GraphIngestReconciliationPanel, {
  CONFIRM_PRESENT,
  RETRY_ABSENT,
  entityLabel,
  manifestEvidenceAvailable,
  operationBlocker,
  safeApiFailure,
} from './GraphIngestReconciliationPanel';
import componentSource from './GraphIngestReconciliationPanel.jsx?raw';

const operation = (overrides = {}) => ({
  operation_id: '11111111-1111-4111-8111-111111111111',
  work_kind: 'candidate',
  entity_id: 91,
  source_refs: [{ kind: 'candidate', id: 91 }],
  source_evidence_state: 'available',
  source_refs_sha256: 'a'.repeat(64),
  status: 'reconciliation_required',
  dispatch_attempts: 3,
  expected_attempt_nonce: '22222222-2222-4222-8222-222222222222',
  attempt_fence_available: true,
  provider_attempt_started_at: '2026-07-17T01:00:00Z',
  reconciliation_required_at: '2026-07-17T01:05:00Z',
  last_error_code: 'provider_outcome_ambiguous:TimeoutError',
  reconciliation_history_state: 'available',
  reconciliation_count: 0,
  operation_manifest_state: 'available',
  operation_manifest_sha256: 'b'.repeat(64),
  operation_episode_count: 1,
  operation_episodes: [{
    ordinal: 0,
    episode_name: 'candidate-91-profile',
    episode_sha256: 'c'.repeat(64),
  }],
  ...overrides,
});

const page = (operations = [operation()], nextCursor = null) => ({
  data: {
    operations,
    has_more: nextCursor !== null,
    next_cursor: nextCursor,
    limit: 20,
    offset: 0,
  },
});

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
};

describe('graph ingest reconciliation panel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rolesApi.graphIngestReconciliations.mockReset().mockResolvedValue(page());
    rolesApi.resolveGraphIngestReconciliation.mockReset().mockResolvedValue({ data: {} });
  });

  it('loads once without scheduling another polling loop and refreshes only on request', async () => {
    render(<GraphIngestReconciliationPanel />);

    expect(await screen.findByText('Candidate #91')).toBeInTheDocument();
    expect(rolesApi.graphIngestReconciliations).toHaveBeenCalledTimes(1);
    expect(componentSource).not.toMatch(/\bsetInterval\s*\(|\bsetTimeout\s*\(/);

    fireEvent.click(screen.getByRole('button', { name: 'Refresh evidence' }));
    await waitFor(() => {
      expect(rolesApi.graphIngestReconciliations).toHaveBeenCalledTimes(2);
    });
  });

  it('renders safe retained attempt and reconciliation context', async () => {
    rolesApi.graphIngestReconciliations.mockResolvedValue(page([
      operation({
        reconciliation_count: 1,
        last_resolution: {
          action: RETRY_ABSENT,
          actor_id: 7,
          resolved_at: '2026-07-17T00:30:00Z',
        },
      }),
    ]));

    render(<GraphIngestReconciliationPanel />);

    expect(await screen.findByText('Candidate #91')).toBeInTheDocument();
    expect(screen.getByText('Dispatch attempts').nextElementSibling).toHaveTextContent('3');
    expect(screen.getByText('Prior reconciliations').nextElementSibling).toHaveTextContent('1');
    expect(screen.getByText('Exact provider payload fingerprint').nextElementSibling)
      .toHaveTextContent('b'.repeat(64));
    expect(screen.getByText('Ordered provider episodes (1)').nextElementSibling)
      .toHaveTextContent('candidate-91-profile');
    expect(screen.getByText('Ordered provider episodes (1)').nextElementSibling)
      .toHaveTextContent('c'.repeat(64));
    expect(screen.getByText(/Authorized retry after full absence by workspace user #7/)).toBeInTheDocument();
  });

  it('loads every oldest-first cursor page, preserves evidence, and deduplicates IDs', async () => {
    const interview = operation({
      operation_id: '33333333-3333-4333-8333-333333333333',
      work_kind: 'interview',
      entity_id: 44,
    });
    rolesApi.graphIngestReconciliations
      .mockResolvedValueOnce(page([operation()], 'opaque-page-2'))
      .mockResolvedValueOnce(page([operation(), interview]));
    render(<GraphIngestReconciliationPanel />);

    expect(await screen.findByText('Candidate #91')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load more evidence' }));
    expect(await screen.findByText('Interview #44')).toBeInTheDocument();
    expect(screen.getAllByText('Candidate #91')).toHaveLength(1);
    expect(screen.getByRole('article', { name: 'Candidate #91' })).toBeInTheDocument();
    expect(screen.getByRole('article', { name: 'Interview #44' })).toBeInTheDocument();
    expect(rolesApi.graphIngestReconciliations).toHaveBeenNthCalledWith(1, {
      limit: 20,
    });
    expect(rolesApi.graphIngestReconciliations).toHaveBeenNthCalledWith(2, {
      limit: 20,
      cursor: 'opaque-page-2',
    });
    expect(screen.queryByRole('button', { name: 'Load more evidence' })).not.toBeInTheDocument();
    expect(entityLabel('event')).toBe('Pipeline event');
  });

  it('restarts page one after resolution and uses its fresh cursor without skipping', async () => {
    const second = operation({
      operation_id: '33333333-3333-4333-8333-333333333333',
      entity_id: 92,
    });
    const third = operation({
      operation_id: '44444444-4444-4444-8444-444444444444',
      entity_id: 93,
    });
    rolesApi.graphIngestReconciliations
      .mockResolvedValueOnce(page([operation()], 'cursor-before-resolution'))
      .mockResolvedValueOnce(page([second], 'cursor-after-resolution'))
      .mockResolvedValueOnce(page([third]));
    render(<GraphIngestReconciliationPanel />);
    await screen.findByText('Candidate #91');

    fireEvent.click(screen.getByRole('button', { name: 'Confirm fully present' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Mark exact operation complete' }));

    expect(await screen.findByText('Candidate #92')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load more evidence' }));
    expect(await screen.findByText('Candidate #93')).toBeInTheDocument();
    expect(rolesApi.graphIngestReconciliations).toHaveBeenNthCalledWith(2, {
      limit: 20,
    });
    expect(rolesApi.graphIngestReconciliations).toHaveBeenNthCalledWith(3, {
      limit: 20,
      cursor: 'cursor-after-resolution',
    });
    expect(screen.getByText('Candidate #92')).toBeInTheDocument();
    expect(screen.getByRole('article', { name: 'Candidate #92' })).toBeInTheDocument();
  });

  it('requires the exact whole-operation presence attestation and forwards its nonce', async () => {
    rolesApi.graphIngestReconciliations
      .mockResolvedValueOnce(page())
      .mockResolvedValue(page([]));
    render(<GraphIngestReconciliationPanel />);
    await screen.findByText('Candidate #91');

    fireEvent.click(screen.getByRole('button', { name: 'Confirm fully present' }));
    const submit = screen.getByRole('button', { name: 'Mark exact operation complete' });
    expect(submit).toBeDisabled();
    expect(screen.getAllByText('11111111-1111-4111-8111-111111111111').length).toBeGreaterThan(0);
    expect(screen.getAllByText('22222222-2222-4222-8222-222222222222').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(submit);

    await waitFor(() => {
      expect(rolesApi.resolveGraphIngestReconciliation).toHaveBeenCalledWith(
        '11111111-1111-4111-8111-111111111111',
        {
          action: CONFIRM_PRESENT,
          expected_attempt_nonce: '22222222-2222-4222-8222-222222222222',
          entire_operation_present_attested: true,
          entire_operation_absent_attested: false,
        },
      );
    });
    expect(
      await screen.findByText('The exact operation was marked complete without replay.'),
    ).toBeInTheDocument();
  });

  it('requires full absence before requesting an ordinary outbox retry', async () => {
    rolesApi.graphIngestReconciliations
      .mockResolvedValueOnce(page())
      .mockResolvedValue(page([]));
    render(<GraphIngestReconciliationPanel />);
    await screen.findByText('Candidate #91');

    fireEvent.click(screen.getByRole('button', { name: 'Retry after full absence' }));
    const submit = screen.getByRole('button', { name: 'Authorize exact operation retry' });
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(submit);

    await waitFor(() => {
      expect(rolesApi.resolveGraphIngestReconciliation).toHaveBeenCalledWith(
        '11111111-1111-4111-8111-111111111111',
        {
          action: RETRY_ABSENT,
          expected_attempt_nonce: '22222222-2222-4222-8222-222222222222',
          entire_operation_present_attested: false,
          entire_operation_absent_attested: true,
        },
      );
    });
  });

  it('disables every operation while one exact resolution is being saved', async () => {
    const other = operation({
      operation_id: '33333333-3333-4333-8333-333333333333',
      entity_id: 92,
    });
    const resolution = deferred();
    rolesApi.graphIngestReconciliations
      .mockResolvedValueOnce(page([operation(), other]))
      .mockResolvedValue(page([]));
    rolesApi.resolveGraphIngestReconciliation.mockReturnValueOnce(resolution.promise);
    render(<GraphIngestReconciliationPanel />);
    await screen.findByText('Candidate #91');

    fireEvent.click(screen.getAllByRole('button', { name: 'Confirm fully present' })[0]);
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Mark exact operation complete' }));

    await waitFor(() => {
      for (const button of screen.getAllByRole('button', { name: 'Confirm fully present' })) {
        expect(button).toBeDisabled();
      }
      for (const button of screen.getAllByRole('button', { name: 'Retry after full absence' })) {
        expect(button).toBeDisabled();
      }
    });
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();

    await act(async () => {
      resolution.resolve({ data: {} });
      await resolution.promise;
    });
    await waitFor(() => expect(rolesApi.graphIngestReconciliations).toHaveBeenCalledTimes(2));
  });

  it('keeps a successful resolution non-actionable when evidence refresh fails', async () => {
    rolesApi.graphIngestReconciliations
      .mockResolvedValueOnce(page())
      .mockRejectedValueOnce(new Error('refresh unavailable'));
    render(<GraphIngestReconciliationPanel />);
    await screen.findByText('Candidate #91');

    fireEvent.click(screen.getByRole('button', { name: 'Confirm fully present' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Mark exact operation complete' }));

    expect(
      await screen.findByText('The exact operation was marked complete without replay.'),
    ).toBeInTheDocument();
    expect(screen.getByText(
      /resolution was saved, but refreshed graph evidence could not be loaded/i,
    )).toBeInTheDocument();
    expect(screen.queryByText('Candidate #91')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirm fully present' })).not.toBeInTheDocument();
    expect(rolesApi.resolveGraphIngestReconciliation).toHaveBeenCalledTimes(1);
    expect(rolesApi.graphIngestReconciliations).toHaveBeenCalledTimes(2);
  });

  it('blocks remaining operations after a failed refresh until a full refresh succeeds', async () => {
    const other = operation({
      operation_id: '33333333-3333-4333-8333-333333333333',
      entity_id: 92,
    });
    rolesApi.graphIngestReconciliations
      .mockResolvedValueOnce(page([operation(), other]))
      .mockRejectedValueOnce(new Error('refresh unavailable'))
      .mockResolvedValueOnce(page([other]));
    render(<GraphIngestReconciliationPanel />);
    await screen.findByText('Candidate #91');

    fireEvent.click(screen.getAllByRole('button', { name: 'Confirm fully present' })[0]);
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Mark exact operation complete' }));

    expect(await screen.findByText(
      /resolution was saved, but refreshed graph evidence could not be loaded/i,
    )).toBeInTheDocument();
    expect(screen.getByText('Candidate #92')).toBeInTheDocument();
    expect(screen.getByText(
      /Fresh graph evidence is required before another action\./,
    )).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm fully present' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Retry after full absence' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Refresh evidence' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Confirm fully present' })).toBeEnabled();
      expect(screen.getByRole('button', { name: 'Retry after full absence' })).toBeEnabled();
    });
  });

  it('disables both actions when any retained evidence or attempt fence needs support', async () => {
    rolesApi.graphIngestReconciliations.mockResolvedValue(page([
      operation({
        attempt_fence_available: false,
        expected_attempt_nonce: null,
      }),
      operation({
        operation_id: '33333333-3333-4333-8333-333333333333',
        source_evidence_state: 'support_review_required',
      }),
      operation({
        operation_id: '44444444-4444-4444-8444-444444444444',
        reconciliation_history_state: 'support_review_required',
      }),
      operation({
        operation_id: '55555555-5555-4555-8555-555555555555',
        operation_manifest_state: 'legacy_unavailable',
        operation_manifest_sha256: null,
        operation_episode_count: null,
        operation_episodes: [],
      }),
      operation({
        operation_id: '66666666-6666-4666-8666-666666666666',
        operation_manifest_state: 'available',
        operation_manifest_sha256: 'not-a-sha',
        operation_episode_count: 1,
        operation_episodes: [],
      }),
    ]));
    render(<GraphIngestReconciliationPanel />);

    expect(await screen.findByText(/The exact provider attempt identity is unavailable\./)).toBeInTheDocument();
    expect(screen.getByText(/The source evidence requires support review\./)).toBeInTheDocument();
    expect(
      screen.getByText(/The retained reconciliation history requires support review\./),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/The exact provider payload manifest requires support review\./),
    ).toHaveLength(2);
    for (const button of screen.getAllByRole('button', { name: 'Confirm fully present' })) {
      expect(button).toBeDisabled();
    }
    for (const button of screen.getAllByRole('button', { name: 'Retry after full absence' })) {
      expect(button).toBeDisabled();
    }
    expect(operationBlocker(operation({ reconciliation_history_state: 'support_review_required' })))
      .toBe('The retained reconciliation history requires support review.');
    expect(operationBlocker(operation({ operation_manifest_state: 'legacy_unavailable' })))
      .toBe('The exact provider payload manifest requires support review.');
    expect(manifestEvidenceAvailable(operation({
      operation_episode_count: 0,
      operation_episodes: [],
    }))).toBe(false);
    expect(operationBlocker(operation({
      operation_manifest_sha256: 'not-a-sha',
    }))).toBe('The exact provider payload manifest requires support review.');
    expect(safeApiFailure({ response: { status: 422 } }))
      .toBe('The evidence page could not be continued safely. Refresh from the first page.');
    expect(safeApiFailure({ response: { status: 422 } }, 'resolve'))
      .toBe('The exact whole-operation attestation is required before this action can run.');
  });

  it('refreshes a stale operation and never echoes backend/provider secrets', async () => {
    const freshAttemptNonce = '77777777-7777-4777-8777-777777777777';
    rolesApi.graphIngestReconciliations
      .mockResolvedValueOnce(page([
        operation({ last_error_code: 'unexpected_private_provider_detail' }),
      ]))
      .mockResolvedValue(page([
        operation({ expected_attempt_nonce: freshAttemptNonce }),
      ]));
    rolesApi.resolveGraphIngestReconciliation.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'provider token=private-value' },
      },
    });
    render(<GraphIngestReconciliationPanel />);
    await screen.findByText('Candidate #91');
    expect(screen.getByText('The provider outcome requires review.')).toBeInTheDocument();
    expect(screen.queryByText(/private_provider|private-value/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm fully present' }));
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Mark exact operation complete' }));

    expect(
      await screen.findByText(
        'The operation changed. Fresh evidence is shown; review it before acting.',
      ),
    ).toBeInTheDocument();
    expect(rolesApi.graphIngestReconciliations).toHaveBeenCalledTimes(2);
    expect(screen.getByText(freshAttemptNonce)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm fully present' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Retry after full absence' })).toBeEnabled();
    expect(screen.queryByText(/private-value/i)).not.toBeInTheDocument();
  });
});
