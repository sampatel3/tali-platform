import React, { useEffect, useRef, useState } from 'react';

import { Button } from '../../shared/ui/TaaliPrimitives';
import { getErrorMessage } from './candidatesUiUtils';

const RECONCILABLE = new Set([
  'dispatch_failed',
  'failed',
  'legacy_reconciliation_required',
  'manual_reconciliation_required',
]);

const labelForStatus = (status) => ({
  cancelled: 'Delivery intentionally disabled',
  confirmed: 'Delivered to Workable',
  dispatching: 'Delivery dispatching',
  dispatch_failed: 'Delivery dispatch needs review',
  failed: 'Delivery failed',
  legacy_reconciliation_required: 'Legacy delivery needs verification',
  manual_reconciliation_required: 'Delivery outcome needs verification',
  pending: 'Delivery queued',
  provider_call_started: 'Delivery in progress',
  retry_wait: 'Waiting for Workable configuration',
  superseded: 'Delivery no longer applicable',
}[status] || 'Workable delivery status unavailable');

export const needsResultDeliveryReconciliation = (evidence) => Boolean(
  evidence?.reconciliation_required
  || RECONCILABLE.has(String(evidence?.status || '').toLowerCase()),
);

export function AssessmentResultDeliveryPanel({
  assessment,
  assessmentsApi,
  onResolved = null,
}) {
  const assessmentEvidence = assessment?.workable_result_delivery;
  const [presentAttested, setPresentAttested] = useState(false);
  const [absentAttested, setAbsentAttested] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [resolvedEvidenceState, setResolvedEvidenceState] = useState(null);
  const assessmentIdentity = `${assessment?.id ?? ''}\u0000${assessmentEvidence?.operation_id ?? ''}`;
  const assessmentIdentityRef = useRef(assessmentIdentity);
  const authoritativeEvidenceRef = useRef(assessmentEvidence);
  const reconciliationGenerationRef = useRef(0);
  const reconciliationInFlightRef = useRef(null);
  assessmentIdentityRef.current = assessmentIdentity;
  authoritativeEvidenceRef.current = assessmentEvidence;

  useEffect(() => {
    reconciliationGenerationRef.current += 1;
    reconciliationInFlightRef.current = null;
    setBusy('');
  }, [assessmentIdentity]);

  useEffect(() => {
    setPresentAttested(false);
    setAbsentAttested(false);
    setError('');
    setNotice('');
    setResolvedEvidenceState(null);
  }, [assessmentEvidence]);

  const resolvedEvidence = resolvedEvidenceState
    && resolvedEvidenceState.source === assessmentEvidence
    ? resolvedEvidenceState.evidence
    : null;
  const evidence = resolvedEvidence || assessmentEvidence;
  if (!evidence || typeof evidence !== 'object') return null;
  const status = String(evidence.status || '').toLowerCase();
  const needsReconciliation = needsResultDeliveryReconciliation(evidence);
  const hasOperationIdentity = typeof evidence.operation_id === 'string'
    && evidence.operation_id.length > 0;
  const canReconcile = evidence.can_reconcile === true && hasOperationIdentity;

  const reconcile = async (action) => {
    if (!assessment?.id || !assessmentsApi?.reconcileWorkableResultDelivery) return;
    const currentRequest = reconciliationInFlightRef.current;
    if (currentRequest?.identity === assessmentIdentity) return;
    const generation = ++reconciliationGenerationRef.current;
    const request = { identity: assessmentIdentity, generation, evidence: assessmentEvidence };
    reconciliationInFlightRef.current = request;
    const requestIsCurrent = () => assessmentIdentityRef.current === request.identity
      && reconciliationGenerationRef.current === request.generation
      && reconciliationInFlightRef.current === request;
    setBusy(action);
    setError('');
    setNotice('');
    try {
      const payload = action === 'confirm_delivered'
        ? {
          action,
          expected_operation_id: evidence.operation_id,
          provider_result_present_attested: true,
          provider_result_absent_attested: false,
        }
        : {
          action,
          expected_operation_id: evidence.operation_id,
          provider_result_present_attested: false,
          provider_result_absent_attested: true,
        };
      const response = await assessmentsApi.reconcileWorkableResultDelivery(
        assessment.id,
        payload,
      );
      if (!requestIsCurrent()) return;
      const responseData = response?.data || response || {};
      const nextEvidence = responseData.workable_result_delivery || {
        ...evidence,
        status: responseData.status || evidence.status,
        operation_id: responseData.operation_id || evidence.operation_id,
        reconciliation_required: false,
        can_reconcile: false,
      };
      // The mutation response is authoritative evidence too. Apply it before
      // revalidation so a successful recovery never remains actionable merely
      // because the follow-up report request failed.
      setResolvedEvidenceState({ evidence: nextEvidence, source: request.evidence });
      setPresentAttested(false);
      setAbsentAttested(false);
      setNotice('The delivery reconciliation was saved.');
      try {
        const refreshed = await onResolved?.(responseData);
        if (!requestIsCurrent()) return;
        if (refreshed === true) {
          // The report GET carries current role-derived capabilities such as
          // can_reconcile. Stop shadowing it only after that refresh succeeds.
          if (authoritativeEvidenceRef.current !== request.evidence) {
            setResolvedEvidenceState(null);
          }
        } else if (refreshed === false) {
          setNotice(
            'The delivery reconciliation was saved, but fresh report data could not be loaded. '
            + 'Showing the server-confirmed delivery state; refresh before taking another action.',
          );
        }
      } catch {
        if (!requestIsCurrent()) return;
        setNotice(
          'The delivery reconciliation was saved, but fresh report data could not be loaded. '
          + 'Showing the server-confirmed delivery state; refresh before taking another action.',
        );
      }
    } catch (requestError) {
      if (!requestIsCurrent()) return;
      setError(getErrorMessage(
        requestError,
        'Could not reconcile the Workable assessment result.',
      ));
    } finally {
      if (requestIsCurrent()) {
        reconciliationInFlightRef.current = null;
        setBusy('');
      }
    }
  };

  return (
    <section
      className={`mt-4 rounded-md border px-3 py-3 text-sm ${needsReconciliation ? 'border-amber-300 bg-amber-50 text-amber-950' : 'border-slate-200 bg-slate-50 text-slate-800'}`}
      aria-label="Workable assessment result delivery"
      role={needsReconciliation ? 'alert' : 'status'}
    >
      <strong>{labelForStatus(status)}</strong>
      <p className="mt-1 text-xs">
        Provider calls {evidence.provider_attempts || 0} · queue handoffs {evidence.publish_attempts || 0}
        {evidence.configuration_attempts
          ? ` · configuration checks ${evidence.configuration_attempts}`
          : ''}
        {evidence.last_error_code ? ` · ${String(evidence.last_error_code).replace(/_/g, ' ')}` : ''}
      </p>
      {needsReconciliation ? (
        <>
          <p className="mt-2 text-xs">
            Inspect the exact candidate in Workable first. The prior operation will never be
            retried automatically while its outcome is uncertain.
          </p>
          {canReconcile ? (
            <div className="mt-3 grid gap-3">
              <label className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={presentAttested}
                  disabled={Boolean(busy)}
                  onChange={(event) => {
                    setPresentAttested(event.target.checked);
                    if (event.target.checked) setAbsentAttested(false);
                  }}
                />
                I checked Workable and confirmed this exact assessment result is present.
              </label>
              <Button
                type="button"
                variant="secondary"
                size="xs"
                disabled={!presentAttested || Boolean(busy)}
                loading={busy === 'confirm_delivered'}
                onClick={() => reconcile('confirm_delivered')}
              >
                Mark delivered without sending
              </Button>
              <label className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={absentAttested}
                  disabled={Boolean(busy)}
                  onChange={(event) => {
                    setAbsentAttested(event.target.checked);
                    if (event.target.checked) setPresentAttested(false);
                  }}
                />
                I checked Workable and confirmed this exact assessment result is absent.
              </label>
              <Button
                type="button"
                variant="danger"
                size="xs"
                disabled={!absentAttested || Boolean(busy)}
                loading={busy === 'retry_after_provider_absence'}
                onClick={() => reconcile('retry_after_provider_absence')}
              >
                Authorize one new delivery operation
              </Button>
            </div>
          ) : evidence.can_reconcile === true ? (
            <p className="mt-2 text-xs" role="note">
              This delivery record has no safe operation identity. Contact support before
              attempting recovery.
            </p>
          ) : (
            <p className="mt-2 text-xs" role="note">
              A workspace owner must verify the provider record and choose the recovery action.
            </p>
          )}
        </>
      ) : null}
      {error ? <p className="mt-2 text-xs" role="status">{error}</p> : null}
      {notice ? <p className="mt-2 text-xs" aria-live="polite">{notice}</p> : null}
    </section>
  );
}

export default AssessmentResultDeliveryPanel;
