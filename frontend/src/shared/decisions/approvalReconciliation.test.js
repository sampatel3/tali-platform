import { describe, expect, it, vi } from 'vitest';

import {
  APPROVAL_OUTCOME_UNKNOWN_CODE,
  APPROVAL_OUTCOME_UNKNOWN_MESSAGE,
  ApprovalOutcomeUnknownError,
  approveDecisionWithReconciliation,
  isAmbiguousApprovalFailure,
  isApprovalOutcomeUnknownError,
} from './approvalReconciliation';

const decision = { id: 42, application_id: 7 };

const createAgentApi = () => ({
  approveDecision: vi.fn(),
  listDecisions: vi.fn(),
});

describe('isAmbiguousApprovalFailure', () => {
  it.each([
    ['an Axios timeout', { code: 'ECONNABORTED' }],
    ['a transport timeout', { code: 'ETIMEDOUT' }],
    ['an Axios network failure', { code: 'ERR_NETWORK' }],
    ['a response-less transport failure', { code: 'EPIPE' }],
    ['a response-less Axios failure', { isAxiosError: true }],
    ['a 502 gateway response', { response: { status: 502 } }],
    ['a 504 gateway response', { response: { status: 504 } }],
    [
      'the backend unknown-outcome response',
      {
        response: {
          status: 500,
          data: { detail: APPROVAL_OUTCOME_UNKNOWN_MESSAGE },
        },
      },
    ],
  ])('classifies %s as ambiguous', (_label, error) => {
    expect(isAmbiguousApprovalFailure(error)).toBe(true);
  });

  it.each([
    ['a definitive 503', { response: { status: 503 } }],
    ['a definitive 503 carrying otherwise ambiguous signals', {
      code: 'ERR_NETWORK',
      response: {
        status: 503,
        data: { detail: APPROVAL_OUTCOME_UNKNOWN_MESSAGE },
      },
    }],
    ['a normal validation response', { response: { status: 422 } }],
    ['a local exception without transport evidence', new Error('render bug')],
  ])('does not classify %s as ambiguous', (_label, error) => {
    expect(isAmbiguousApprovalFailure(error)).toBe(false);
  });
});

describe('approveDecisionWithReconciliation', () => {
  it('returns the ordinary approval receipt without an extra read on success', async () => {
    const agentApi = createAgentApi();
    const receipt = { data: { decision_id: 42, accepted: true } };
    const body = { note: 'Reviewed' };
    const opts = { force: true };
    agentApi.approveDecision.mockResolvedValue(receipt);

    await expect(
      approveDecisionWithReconciliation(agentApi, decision, body, opts),
    ).resolves.toEqual({ receipt, matchedDecision: null, reconciled: false });

    expect(agentApi.approveDecision).toHaveBeenCalledWith(42, body, opts);
    expect(agentApi.listDecisions).not.toHaveBeenCalled();
  });

  it.each(['processing', 'approved'])(
    'returns the matched %s decision after an ambiguous approval response',
    async (status) => {
      const agentApi = createAgentApi();
      const requestError = { code: 'ECONNABORTED' };
      const matchedDecision = { ...decision, status };
      agentApi.approveDecision.mockRejectedValue(requestError);
      agentApi.listDecisions.mockResolvedValue({
        data: [
          { id: 99, application_id: 7, status: 'approved' },
          matchedDecision,
        ],
      });

      await expect(
        approveDecisionWithReconciliation(agentApi, decision, {}, { force: false }),
      ).resolves.toEqual({
        receipt: null,
        matchedDecision,
        reconciled: true,
      });

      expect(agentApi.listDecisions).toHaveBeenCalledWith(
        { application_id: 7, status: 'current', limit: 50 },
        { timeout: 10000 },
      );
    },
  );

  it('matches numeric and string decision ids without confusing sibling decisions', async () => {
    const agentApi = createAgentApi();
    agentApi.approveDecision.mockRejectedValue({ code: 'ERR_NETWORK' });
    agentApi.listDecisions.mockResolvedValue({
      data: [
        { id: '41', application_id: 7, status: 'processing' },
        { id: '42', application_id: 7, status: 'processing' },
      ],
    });

    const result = await approveDecisionWithReconciliation(agentApi, decision);

    expect(result.matchedDecision.id).toBe('42');
  });

  it('reconciles the backend unknown-outcome response before giving up', async () => {
    const agentApi = createAgentApi();
    agentApi.approveDecision.mockRejectedValue({
      response: {
        status: 500,
        data: { detail: APPROVAL_OUTCOME_UNKNOWN_MESSAGE },
      },
    });
    agentApi.listDecisions.mockResolvedValue({
      data: [{ ...decision, status: 'approved' }],
    });

    const result = await approveDecisionWithReconciliation(agentApi, decision);

    expect(result.reconciled).toBe(true);
    expect(agentApi.listDecisions).toHaveBeenCalledOnce();
  });

  it.each([
    ['the row is still pending', { data: [{ ...decision, status: 'pending' }] }],
    ['the row is absent', { data: [] }],
    ['the response is malformed', { data: null }],
  ])('throws a recognizable unknown-outcome error when %s', async (_label, response) => {
    const agentApi = createAgentApi();
    const requestError = { code: 'ETIMEDOUT' };
    agentApi.approveDecision.mockRejectedValue(requestError);
    agentApi.listDecisions.mockResolvedValue(response);

    let thrown;
    try {
      await approveDecisionWithReconciliation(agentApi, decision);
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(ApprovalOutcomeUnknownError);
    expect(thrown).toMatchObject({
      name: 'ApprovalOutcomeUnknownError',
      code: APPROVAL_OUTCOME_UNKNOWN_CODE,
      message: APPROVAL_OUTCOME_UNKNOWN_MESSAGE,
      cause: requestError,
    });
    expect(isApprovalOutcomeUnknownError(thrown)).toBe(true);
  });

  it('preserves the original approval failure when reconciliation itself fails', async () => {
    const agentApi = createAgentApi();
    const requestError = { code: 'ERR_NETWORK' };
    const reconciliationError = new Error('status request failed');
    agentApi.approveDecision.mockRejectedValue(requestError);
    agentApi.listDecisions.mockRejectedValue(reconciliationError);

    let thrown;
    try {
      await approveDecisionWithReconciliation(agentApi, decision);
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(ApprovalOutcomeUnknownError);
    expect(thrown.cause).toBe(requestError);
    expect(thrown.reconciliationCause).toBe(reconciliationError);
  });

  it('rethrows a definitive 503 unchanged so the caller can offer a retry', async () => {
    const agentApi = createAgentApi();
    const requestError = {
      response: {
        status: 503,
        data: { detail: 'Nothing was sent; please try again.' },
      },
    };
    agentApi.approveDecision.mockRejectedValue(requestError);

    await expect(
      approveDecisionWithReconciliation(agentApi, decision),
    ).rejects.toBe(requestError);
    expect(agentApi.listDecisions).not.toHaveBeenCalled();
  });
});
