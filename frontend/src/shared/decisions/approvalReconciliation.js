// Approval is an outcome-ambiguous mutation: a gateway or connection can fail
// after the backend has durably accepted the action. Retrying in that state can
// duplicate an ATS side effect, so callers must reconcile the decision first.

export const APPROVAL_OUTCOME_UNKNOWN_MESSAGE =
  "We couldn't confirm this action. Refresh before taking another action.";

export const APPROVAL_OUTCOME_UNKNOWN_CODE = 'APPROVAL_OUTCOME_UNKNOWN';

const AMBIGUOUS_TRANSPORT_CODES = new Set([
  'ECONNABORTED',
  'ETIMEDOUT',
  'ERR_NETWORK',
]);

const RECONCILED_STATUSES = new Set(['processing', 'approved']);

export class ApprovalOutcomeUnknownError extends Error {
  constructor(cause, { reconciliationCause = null, observedDecision = null } = {}) {
    super(APPROVAL_OUTCOME_UNKNOWN_MESSAGE);
    this.name = 'ApprovalOutcomeUnknownError';
    this.code = APPROVAL_OUTCOME_UNKNOWN_CODE;
    this.cause = cause;
    this.reconciliationCause = reconciliationCause;
    this.observedDecision = observedDecision;
  }
}

export const isApprovalOutcomeUnknownError = (error) =>
  error instanceof ApprovalOutcomeUnknownError
  || error?.code === APPROVAL_OUTCOME_UNKNOWN_CODE;

export const isAmbiguousApprovalFailure = (error) => {
  const responseStatus = Number(error?.response?.status || 0);

  // The approval endpoint uses 503 only when it knows nothing was published.
  // It is therefore safe for the UI to offer a retry even if an adapter also
  // attached a transport-looking error code.
  if (responseStatus === 503) return false;

  if (error?.response?.data?.detail === APPROVAL_OUTCOME_UNKNOWN_MESSAGE) {
    return true;
  }
  if (AMBIGUOUS_TRANSPORT_CODES.has(error?.code)) return true;
  if (responseStatus === 502 || responseStatus === 504) return true;

  // Axios supplies at least one of these fields when a request was made but no
  // response arrived. Requiring transport evidence avoids turning an ordinary
  // local programming exception into a permanently locked approval outcome.
  return !error?.response && Boolean(
    error?.request
    || error?.isAxiosError
    || (typeof error?.code === 'string' && error.code.length > 0),
  );
};

/**
 * Submit an approval and reconcile any outcome-ambiguous failure.
 *
 * The return wrapper preserves the ordinary Axios receipt on direct success.
 * If the mutation response was lost but the decision is now processing or
 * approved, `matchedDecision` carries the authoritative row instead.
 */
export const approveDecisionWithReconciliation = async (
  agentApi,
  decision,
  body = {},
  opts = {},
) => {
  let requestError;
  try {
    const receipt = await agentApi.approveDecision(decision.id, body, opts);
    return { receipt, matchedDecision: null, reconciled: false };
  } catch (error) {
    if (!isAmbiguousApprovalFailure(error)) throw error;
    requestError = error;
  }

  let observedDecision = null;
  let reconciliationCause = null;
  try {
    const response = await agentApi.listDecisions(
      {
        application_id: decision.application_id,
        status: 'current',
        limit: 50,
      },
      { timeout: 10000 },
    );
    observedDecision = (Array.isArray(response?.data) ? response.data : [])
      .find((row) => Number(row?.id) === Number(decision.id)) || null;

    if (RECONCILED_STATUSES.has(observedDecision?.status)) {
      return {
        receipt: null,
        matchedDecision: observedDecision,
        reconciled: true,
      };
    }
  } catch (error) {
    reconciliationCause = error;
  }

  throw new ApprovalOutcomeUnknownError(requestError, {
    reconciliationCause,
    observedDecision,
  });
};
