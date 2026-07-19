import React, {
  useLayoutEffect, useMemo, useRef, useState,
} from 'react';

import { getErrorMessage } from '../../shared/getErrorMessage';
import { Button } from '../../shared/ui/TaaliPrimitives';

const issueLabel = (issueCode) => ({
  ambiguous_provider_outcome: 'AI response outcome is uncertain',
  claim_finalization_state_malformed: 'AI response finalization state needs recovery',
  claim_identity_malformed: 'AI request identity needs recovery',
  claim_record_malformed: 'AI request record needs recovery',
  claim_record_oversized: 'AI request record needs support review',
  claim_state_malformed: 'AI request state needs recovery',
  claims_container_malformed: 'AI request records need recovery',
  claims_container_oversized: 'AI request records need support review',
  finalization_input_malformed: 'AI response finalization data needs recovery',
  provider_checkpoint_malformed: 'AI response checkpoint needs recovery',
  provider_checkpoint_unsuccessful: 'Unsuccessful AI response needs recovery',
}[issueCode] || 'AI request needs recovery');

export function CandidateChatReconciliationPanel({
  assessment,
  assessmentsApi,
  onResolved = null,
}) {
  const summary = assessment?.candidate_chat_reconciliation;
  const [expanded, setExpanded] = useState(false);
  const [operations, setOperations] = useState([]);
  const [attestations, setAttestations] = useState({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const requestGenerationRef = useRef(0);
  const busyRef = useRef('');
  const reconciliationGeneration = useMemo(() => JSON.stringify([
    assessment?.id ?? null,
    assessment?.updated_at ?? null,
    assessment?.status ?? null,
    summary?.operation_count ?? null,
    summary?.reconciliation_required === true,
    summary?.can_reconcile === true,
    Array.isArray(summary?.operations)
      ? summary.operations.map((operation) => [
        operation?.operation_id || '',
        operation?.request_reference || '',
        operation?.issue_code || '',
      ])
      : [],
  ]), [
    assessment?.id,
    assessment?.status,
    assessment?.updated_at,
    summary?.can_reconcile,
    summary?.operation_count,
    summary?.operations,
    summary?.reconciliation_required,
  ]);

  useLayoutEffect(() => {
    requestGenerationRef.current += 1;
    busyRef.current = '';
    setExpanded(false);
    setOperations([]);
    setAttestations({});
    setBusy('');
    setError('');
    return () => {
      requestGenerationRef.current += 1;
      busyRef.current = '';
    };
  }, [reconciliationGeneration]);

  if (!summary?.reconciliation_required) return null;

  const loadOperations = async () => {
    if (!assessment?.id || !assessmentsApi?.listCandidateChatReconciliations) return;
    if (expanded) {
      setExpanded(false);
      return;
    }
    if (busyRef.current) return;
    const requestGeneration = requestGenerationRef.current;
    busyRef.current = 'load';
    setBusy('load');
    setError('');
    try {
      const response = await assessmentsApi.listCandidateChatReconciliations(
        assessment.id,
      );
      if (requestGenerationRef.current !== requestGeneration) return;
      const payload = response?.data || response || {};
      setOperations(Array.isArray(payload.operations) ? payload.operations : []);
      setExpanded(true);
    } catch (requestError) {
      if (requestGenerationRef.current !== requestGeneration) return;
      setError(getErrorMessage(
        requestError,
        'Could not load the AI chat recovery records.',
      ));
    } finally {
      if (requestGenerationRef.current === requestGeneration) {
        busyRef.current = '';
        setBusy('');
      }
    }
  };

  const resolve = async (operation) => {
    if (
      !assessment?.id
      || !operation?.operation_id
      || !assessmentsApi?.resolveCandidateChatReconciliation
      || busyRef.current
    ) return;
    const requestGeneration = requestGenerationRef.current;
    busyRef.current = operation.operation_id;
    setBusy(operation.operation_id);
    setError('');
    try {
      const response = await assessmentsApi.resolveCandidateChatReconciliation(
        assessment.id,
        operation.operation_id,
        {
          action: 'close_without_replay',
          expected_request_reference: operation.request_reference,
          provider_outcome_discarded_attested: true,
        },
      );
      if (requestGenerationRef.current !== requestGeneration) return;
      const payload = response?.data || response || {};
      const next = payload.candidate_chat_reconciliation || {};
      setOperations(Array.isArray(next.operations) ? next.operations : []);
      setAttestations({});
      await onResolved?.(payload);
    } catch (requestError) {
      if (requestGenerationRef.current !== requestGeneration) return;
      setError(getErrorMessage(
        requestError,
        'Could not close the exact AI chat request. Refresh and try again.',
      ));
    } finally {
      if (requestGenerationRef.current === requestGeneration) {
        busyRef.current = '';
        setBusy('');
      }
    }
  };

  return (
    <div
      className="mt-2 max-w-sm rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-950"
      role="alert"
    >
      <strong>AI chat recovery required</strong>
      <p className="mt-1">
        {summary.operation_count} request{summary.operation_count === 1 ? '' : 's'}
        {' '}cannot be finalized safely. No ambiguous AI response will be replayed.
      </p>
      {summary.can_reconcile ? (
        <Button
          type="button"
          variant="secondary"
          size="xs"
          className="mt-2"
          loading={busy === 'load'}
          disabled={Boolean(busy && busy !== 'load')}
          onClick={loadOperations}
        >
          {expanded ? 'Hide recovery details' : 'Review recovery'}
        </Button>
      ) : (
        <p className="mt-1" role="note">
          A workspace owner must review the exact request before it can be closed.
        </p>
      )}

      {expanded ? (
        <div className="mt-2 grid gap-2">
          {operations.map((operation) => {
            const operationId = operation.operation_id;
            const attested = attestations[operationId] === true;
            return (
              <div
                key={operationId}
                className="rounded border border-[var(--taali-warning-border)] bg-[var(--taali-surface)] p-2"
              >
                <strong>{issueLabel(operation.issue_code)}</strong>
                <p className="mt-1 font-mono text-[0.6875rem]">
                  {operation.request_reference}
                </p>
                {operation.can_close_without_replay ? (
                  <>
                    <label className="mt-2 flex items-start gap-2">
                      <input
                        type="checkbox"
                        checked={attested}
                        disabled={Boolean(busy)}
                        onChange={(event) => setAttestations((current) => ({
                          ...current,
                          [operationId]: event.target.checked,
                        }))}
                      />
                      <span>
                        Discard this unresolved response without importing or replaying it.
                        Keep its stored evidence for audit.
                      </span>
                    </label>
                    <Button
                      type="button"
                      variant="danger"
                      size="xs"
                      className="mt-2"
                      disabled={!attested || Boolean(busy)}
                      loading={busy === operationId}
                      onClick={() => resolve(operation)}
                    >
                      Close exact request without replay
                    </Button>
                  </>
                ) : (
                  <p className="mt-1" role="note">
                    This record is too large for safe self-service recovery. Contact support;
                    no evidence has been changed.
                  </p>
                )}
              </div>
            );
          })}
          {!operations.length ? (
            <p role="status">No unresolved AI chat requests remain.</p>
          ) : null}
        </div>
      ) : null}
      {error ? <p className="mt-2 text-red-700" role="status">{error}</p> : null}
    </div>
  );
}

export default CandidateChatReconciliationPanel;
