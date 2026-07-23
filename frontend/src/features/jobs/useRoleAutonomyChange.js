import { useCallback } from 'react';

import { getErrorMessage } from '../candidates/candidatesUiUtils';
import {
  conflictActorLabel,
  roleVersionConflict,
  versionedRolePayload,
} from './roleConcurrency';
import {
  GRANULAR_AUTOMATION_KEYS,
  resolvedRoleAutomation,
} from './jobPipelineUtils';

const SUPPORTED_KEYS = new Set([
  ...GRANULAR_AUTOMATION_KEYS,
  'auto_reject',
  'auto_reject_pre_screen',
  'auto_skip_assessment',
]);

const ENABLED_MESSAGES = {
  auto_send_assessment: 'Assessment sending on — approved on-policy invites send automatically.',
  auto_resend_assessment: 'Assessment resending on — policy-approved retries run automatically.',
  auto_advance: 'Candidate advancement on — qualified candidates move to recruiter handoff automatically.',
  auto_reject: 'Scored auto-reject on — deterministic rejects after CV and role-fit scoring run automatically.',
  auto_reject_pre_screen: 'Pre-screen auto-reject on — failed pre-screens reject automatically.',
};

const DISABLED_MESSAGES = {
  auto_send_assessment: 'Assessment sending off — each initial invite waits in the Decision Hub.',
  auto_resend_assessment: 'Assessment resending off — each retry waits for approval.',
  auto_advance: 'Candidate advancement off — each advance waits in the Decision Hub.',
  auto_reject: 'Scored auto-reject off — scored reject decisions wait in the Decision Hub.',
  auto_reject_pre_screen: 'Pre-screen auto-reject off — failed pre-screens wait in the Decision Hub.',
};

const buildPayload = (role, key, value) => {
  if (!GRANULAR_AUTOMATION_KEYS.includes(key)) return { [key]: value };

  // Untouched roles preview the safe HITL policy while their stored values are
  // null. Materialize the complete visible policy on the first change so the
  // remaining switches cannot silently inherit a legacy aggregate value.
  const granularPolicy = Object.fromEntries(
    GRANULAR_AUTOMATION_KEYS.map((automationKey) => [
      automationKey,
      automationKey === key
        ? Boolean(value)
        : resolvedRoleAutomation(role, automationKey),
    ]),
  );
  return {
    ...granularPolicy,
    auto_promote: GRANULAR_AUTOMATION_KEYS.every(
      (automationKey) => granularPolicy[automationKey],
    ),
  };
};

const successMessage = (key, value) => {
  if (key === 'auto_skip_assessment') {
    return value
      ? 'Assessment skip on — qualified candidates bypass assessment.'
      : 'Assessment skip off — assessment invites resume for this role.';
  }
  return value ? ENABLED_MESSAGES[key] : DISABLED_MESSAGES[key];
};

export const useRoleAutonomyChange = ({
  beginRoleOperation,
  captureRoleScope,
  commitRoleScope,
  finishRoleOperation,
  isCurrentRoleScope,
  numericRoleId,
  role,
  rolesApi,
  setRole,
  showToast,
}) => useCallback(async (key, value) => {
  const actionScope = captureRoleScope?.(numericRoleId);
  if (!actionScope || !SUPPORTED_KEYS.has(key)) return;
  if (!beginRoleOperation?.(actionScope, 'autonomy')) return;
  const isGranular = GRANULAR_AUTOMATION_KEYS.includes(key);
  const payload = buildPayload(role, key, value);

  try {
    const response = await rolesApi.update(
      numericRoleId,
      versionedRolePayload(role, payload),
    );
    commitRoleScope?.(actionScope, () => {
      setRole((current) => {
        if (!current) return response?.data || current;
        const effectivePatch = isGranular
            ? Object.fromEntries(
              GRANULAR_AUTOMATION_KEYS.map((automationKey) => [
                automationKey,
                payload[automationKey],
              ]),
            )
            : { [key]: value };
        return {
          ...current,
          ...payload,
          ...(response?.data || {}),
          agent_effective_policy: {
            ...(current.agent_effective_policy || {}),
            ...effectivePatch,
            ...(response?.data?.agent_effective_policy || {}),
          },
        };
      });
      showToast(successMessage(key, value), 'success');
    });
  } catch (error) {
    if (!isCurrentRoleScope?.(actionScope)) return;
    const conflict = roleVersionConflict(error);
    if (!conflict) {
      showToast(getErrorMessage(error, 'Failed to update autonomy setting.'), 'error');
      return;
    }

    // A 409 is a real collaborator boundary. Do not retry automatically:
    // granular saves carry the complete visible policy, so replaying one could
    // overwrite a teammate. Replace the page state from an authoritative GET
    // and emit one notification even if that reconciliation read also fails.
    let refreshed = false;
    try {
      const latest = await rolesApi.get(numericRoleId);
      if (isCurrentRoleScope?.(actionScope) && latest?.data) {
        setRole(latest.data);
        refreshed = true;
      }
    } catch {
      // Preserve the original conflict as the single actionable error.
    }
    const changedBy = conflictActorLabel(conflict.changedBy);
    const actorCopy = changedBy ? ` ${changedBy} saved a newer version.` : '';
    commitRoleScope?.(actionScope, () => showToast(
      `${conflict.message || 'This job changed before your update was saved.'}${actorCopy} ${refreshed
        ? 'Latest settings are shown; review them and try again.'
        : 'Reload the job to review the latest settings before trying again.'}`,
      'error',
    ));
  } finally {
    finishRoleOperation?.(actionScope, 'autonomy');
  }
}, [beginRoleOperation, captureRoleScope, commitRoleScope, finishRoleOperation, isCurrentRoleScope, numericRoleId, role, rolesApi, setRole, showToast]);

export default useRoleAutonomyChange;
