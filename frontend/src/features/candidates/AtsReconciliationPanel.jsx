import React, {
  useLayoutEffect, useMemo, useRef, useState,
} from 'react';

import { roles as rolesApi } from '../../shared/api';
import { Button } from '../../shared/ui/TaaliPrimitives';
import { getErrorMessage } from './candidatesUiUtils';

export const ATS_RECONCILIATION_RECEIPTS = [
  ['decision_provider_operation', 'Decision Hub action'],
  ['stage_move_operation', 'Stage move'],
  ['auto_reject_operation', 'Automatic rejection'],
  ['cv_gap_rejection_operation', 'CV-gap rejection'],
  ['outcome_writeback', 'Outcome write-back'],
  ['outcome_writeback_reconciliation', 'Prior outcome write-back'],
];

const RESOLUTION_DISPOSITIONS = new Set([
  'confirm_provider_matches_local',
  'align_local_to_provider',
  'confirm_decision_provider_effect',
]);

const normalized = (value) => String(value || '').trim().toLowerCase();

export const hasExactAtsResolution = (receipt, receiptKey) => {
  const evidence = receipt?.reconciliation_evidence;
  const operationId = String(receipt?.operation_id || '');
  return Boolean(
    normalized(receipt?.reconciliation_status) === 'resolved'
    && operationId
    && String(receipt?.resolved_operation_id || '') === operationId
    && String(receipt?.resolved_receipt_key || '') === receiptKey
    && receipt?.reconciliation_resolved_by_actor_id != null
    && String(receipt?.reconciliation_resolved_by_actor_type || '').trim()
    && RESOLUTION_DISPOSITIONS.has(String(receipt?.reconciliation_disposition || ''))
    && evidence
    && typeof evidence === 'object'
    && String(evidence.operation_id || '') === operationId
    && String(evidence.receipt_key || '') === receiptKey
    && String(evidence.provider || '') === normalized(receipt?.provider)
    && String(evidence.provider_target_id || '') === String(receipt?.provider_target_id || '')
    && String(evidence.observation_id || '')
      === String(receipt?.reconciliation_observation_id || '')
    && (
      ['open', 'rejected'].includes(String(evidence.remote_outcome || ''))
      || evidence.provider_effect_matches === true
    )
  );
};

export const needsAtsReconciliation = (receipt, receiptKey) => {
  if (!receipt || typeof receipt !== 'object' || hasExactAtsResolution(receipt, receiptKey)) return false;
  const status = normalized(receipt.status);
  return [
    'provider_call_started',
    'provider_succeeded',
    'manual_reconciliation_required',
    'retry_authorized',
  ].includes(status)
    || receipt.manual_reconciliation_required === true
    || receipt.provider_outcome_uncertain === true
    || (receipt.provider_succeeded === true && !['completed', 'confirmed'].includes(status));
};

const identityPayload = (receiptKey, receipt, actingRoleId) => ({
  receipt_key: receiptKey,
  operation_id: String(receipt.operation_id || ''),
  provider: normalized(receipt.provider),
  provider_target_id: String(receipt.provider_target_id || ''),
  ...(actingRoleId ? { acting_role_id: Number(actingRoleId) } : {}),
});

const providerLabel = (provider) => {
  const value = normalized(provider);
  if (value === 'bullhorn') return 'Bullhorn';
  if (value === 'workable') return 'Workable';
  return 'The ATS';
};

export function AtsReconciliationPanel({
  application,
  canMutate = true,
  actingRoleId = null,
  onResolved = null,
  heading = 'ATS operation needs reconciliation',
}) {
  const [observations, setObservations] = useState({});
  const [busyKey, setBusyKey] = useState('');
  const [errors, setErrors] = useState({});
  const [resolvedKeys, setResolvedKeys] = useState(new Set());
  const receiptGenerationRef = useRef(0);
  const applicationId = application?.id;
  const localOutcome = normalized(application?.application_outcome) || 'open';
  const receiptGeneration = useMemo(() => JSON.stringify([
    applicationId ?? null,
    actingRoleId ?? null,
    application?.version ?? null,
    localOutcome,
    application?.integration_sync_state ?? null,
  ]), [
    actingRoleId,
    application?.integration_sync_state,
    application?.version,
    applicationId,
    localOutcome,
  ]);
  const receipts = useMemo(() => {
    const state = application?.integration_sync_state;
    if (!state || typeof state !== 'object') return [];
    return ATS_RECONCILIATION_RECEIPTS.flatMap(([key, label]) => {
      const current = state[key];
      const operationHistory = key === 'stage_move_operation'
        ? state.stage_move_operation_history
        : (key === 'decision_provider_operation'
          ? state.decision_provider_operation_history
          : null);
      const stageHistory = Array.isArray(operationHistory)
        ? [...operationHistory].reverse()
        : [];
      const candidates = [current, ...stageHistory];
      const seen = new Set();
      return candidates.flatMap((storedReceipt, index) => {
        const legacyAutoReject = key === 'auto_reject_operation'
          && normalized(application?.auto_reject_state) === 'manual_reconciliation_required'
          && !hasExactAtsResolution(storedReceipt, key);
        const receipt = legacyAutoReject && !needsAtsReconciliation(storedReceipt, key)
          ? {
            ...(storedReceipt || {}),
            status: 'manual_reconciliation_required',
            manual_reconciliation_required: true,
          }
          : storedReceipt;
        const identityKey = JSON.stringify([
          key,
          receipt?.operation_id || '',
          normalized(receipt?.provider),
          receipt?.provider_target_id || '',
        ]);
        if (seen.has(identityKey)) return [];
        seen.add(identityKey);
        return needsAtsReconciliation(receipt, key) && !resolvedKeys.has(identityKey)
          ? [{ key, identityKey, label, receipt, archived: index > 0 }]
          : [];
      });
    });
  }, [application?.auto_reject_state, application?.integration_sync_state, resolvedKeys]);

  useLayoutEffect(() => {
    receiptGenerationRef.current += 1;
    setObservations({});
    setErrors({});
    setResolvedKeys(new Set());
    setBusyKey('');
    return () => {
      receiptGenerationRef.current += 1;
    };
  }, [receiptGeneration]);

  if (!receipts.length) return null;

  const checkStatus = async ({ key, identityKey, receipt, archived }) => {
    if (!canMutate || archived || busyKey) return;
    const requestGeneration = receiptGenerationRef.current;
    setBusyKey(identityKey);
    setErrors((current) => ({ ...current, [identityKey]: '' }));
    try {
      const response = await rolesApi.checkApplicationAtsReconciliation(
        applicationId,
        identityPayload(key, receipt, actingRoleId),
      );
      if (receiptGenerationRef.current !== requestGeneration) return;
      setObservations((current) => ({
        ...current,
        [identityKey]: response?.data || response,
      }));
    } catch (error) {
      if (receiptGenerationRef.current !== requestGeneration) return;
      setErrors((current) => ({
        ...current,
        [identityKey]: getErrorMessage(error, 'Could not check the exact ATS status.'),
      }));
    } finally {
      if (receiptGenerationRef.current === requestGeneration) setBusyKey('');
    }
  };

  const resolveStatus = async ({ key, identityKey, receipt, archived }, observation) => {
    if (!canMutate || archived || busyKey || !observation?.observation_id) return;
    const requestGeneration = receiptGenerationRef.current;
    const remoteOutcome = normalized(observation.remote_outcome);
    const disposition = key === 'decision_provider_operation'
      ? 'confirm_decision_provider_effect'
      : (key === 'stage_move_operation'
        ? (observation.remote_matches_expected ? 'confirm_stage_move' : 'retry_stage_move')
        : (remoteOutcome === localOutcome
          ? 'confirm_provider_matches_local'
          : 'align_local_to_provider'));
    setBusyKey(identityKey);
    setErrors((current) => ({ ...current, [identityKey]: '' }));
    try {
      await rolesApi.resolveApplicationAtsReconciliation(applicationId, {
        ...identityPayload(key, receipt, actingRoleId),
        observation_id: String(observation.observation_id),
        disposition,
      });
      if (receiptGenerationRef.current !== requestGeneration) return;
      setResolvedKeys((current) => new Set([...current, identityKey]));
      await onResolved?.(applicationId);
    } catch (error) {
      if (receiptGenerationRef.current !== requestGeneration) return;
      setErrors((current) => ({
        ...current,
        [identityKey]: getErrorMessage(error, 'Could not resolve the exact ATS operation.'),
      }));
    } finally {
      if (receiptGenerationRef.current === requestGeneration) setBusyKey('');
    }
  };

  return (
    <section
      className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-950"
      aria-label="ATS reconciliation"
      role="alert"
    >
      <strong>{heading}</strong>
      <p className="mt-1 text-xs">
        Check the exact provider record first. Outcome receipts can then be explicitly aligned;
        stage moves can only be confirmed on an exact match or retried from proof that the expected
        stage is not currently applied. Decision Hub actions are finalized locally only after the
        exact requested provider effect is observed.
      </p>
      <div className="mt-2 grid gap-2">
        {receipts.map((item) => {
          const {
            key, identityKey, label, receipt, archived,
          } = item;
          const retainedObservation = archived
            && receipt?.reconciliation_observation
            && typeof receipt.reconciliation_observation === 'object'
            ? receipt.reconciliation_observation
            : null;
          const observation = observations[identityKey] || retainedObservation;
          const stageMove = key === 'stage_move_operation';
          const decisionProvider = key === 'decision_provider_operation';
          const remoteOutcome = normalized(observation?.remote_outcome);
          const remoteStage = String(observation?.provider_remote_stage || '').trim();
          const expectedStage = String(
            observation?.expected_remote_stage
            || receipt.provider_remote_stage
            || receipt.target_stage
            || '',
          ).trim();
          const safelyResolvable = decisionProvider
            ? observation?.provider_effect_matches === true
            : (stageMove
              ? Boolean(remoteStage && expectedStage)
              : ['open', 'rejected'].includes(remoteOutcome));
          const matches = decisionProvider
            ? observation?.provider_effect_matches === true
            : (stageMove
              ? observation?.remote_matches_expected === true
              : safelyResolvable && remoteOutcome === localOutcome);
          const exactIdentityAvailable = Boolean(
            String(receipt.operation_id || '').trim()
            && normalized(receipt.provider)
            && String(receipt.provider_target_id || '').trim(),
          );
          return (
            <article
              key={identityKey}
              className="rounded border border-[var(--taali-danger-border)] bg-[var(--taali-surface)] p-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  <strong>{label}</strong>
                  {' · '}
                  {providerLabel(receipt.provider)}
                  {archived ? ' · Archived evidence' : ''}
                </span>
                {archived ? null : (
                  <Button
                    type="button"
                    variant="secondary"
                    size="xs"
                    disabled={!canMutate || !exactIdentityAvailable || Boolean(busyKey)}
                    loading={busyKey === identityKey && !observation}
                    onClick={() => checkStatus(item)}
                  >
                    {observation ? 'Check ATS status again' : 'Check ATS status'}
                  </Button>
                )}
              </div>
              {archived ? (
                <p className="mt-1 text-xs" role="status">
                  This archived receipt is retained as read-only evidence. Only the current receipt
                  can be checked or resolved.
                </p>
              ) : null}
              {key === 'auto_reject_operation' && application?.auto_reject_reason ? (
                <p className="mt-1 text-xs">{application.auto_reject_reason}</p>
              ) : null}
              {stageMove || decisionProvider ? (
                <p className="mt-1 text-xs">
                  Exact target {String(receipt.provider_target_id || '—')} · expected provider effect{' '}
                  <strong>{String(receipt.provider_remote_stage || receipt.target_stage || '—')}</strong>
                </p>
              ) : null}
              {!exactIdentityAvailable ? (
                <p className="mt-1 text-xs" role="status">
                  This older receipt has no exact provider target. Support-assisted reconciliation
                  is required; no status was guessed.
                </p>
              ) : null}
              {observation ? (
                <div className="mt-2">
                  <p>
                    {stageMove || decisionProvider ? (
                      <>
                        {providerLabel(receipt.provider)} reports stage{' '}
                        <strong>{remoteStage || 'unknown'}</strong>; the exact expected stage is{' '}
                        <strong>{expectedStage || 'unknown'}</strong>.
                      </>
                    ) : (
                      <>
                        {providerLabel(receipt.provider)} reports{' '}
                        <strong>{remoteOutcome || 'unknown'}</strong>
                        {observation.remote_status ? ` (${observation.remote_status})` : ''}; Taali is{' '}
                        <strong>{localOutcome}</strong>.
                      </>
                    )}
                  </p>
                  {archived ? null : (safelyResolvable ? (
                    <Button
                      type="button"
                      variant={matches ? 'secondary' : 'danger'}
                      size="xs"
                      className="mt-2"
                      disabled={!canMutate || Boolean(busyKey)}
                      loading={busyKey === identityKey}
                      onClick={() => resolveStatus(item, observation)}
                    >
                      {decisionProvider
                        ? 'Confirm ATS effect and finish Decision Hub action'
                        : (stageMove
                          ? (matches
                            ? 'Confirm completed stage move'
                            : `Retry exact move to ${expectedStage}`)
                        : (matches
                          ? 'Confirm ATS and Taali match'
                          : `Align Taali to ATS: ${remoteOutcome}`))}
                    </Button>
                  ) : (
                    <p className="mt-1 text-xs" role="status">
                      {decisionProvider
                        ? 'The exact requested provider effect was not observed. The original write remains ambiguous and will not be retried automatically.'
                        : (stageMove
                          ? 'The provider did not return an exact stage. Inspect it in the ATS; resolution remains blocked.'
                          : 'This provider status cannot safely be classified as open or rejected. Map or inspect it in the ATS; resolution remains blocked.')}
                    </p>
                  ))}
                </div>
              ) : null}
              {errors[identityKey] ? (
                <p className="mt-1 text-xs" role="status">{errors[identityKey]}</p>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

export default AtsReconciliationPanel;
