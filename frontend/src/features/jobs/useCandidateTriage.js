import { useCallback, useEffect, useMemo, useState } from 'react';

import * as apiClient from '../../shared/api';
import { getErrorMessage } from '../candidates/candidatesUiUtils';
import {
  atsProviderLabel,
  roleAtsProvider,
  roleExternalJobId,
} from './atsType';

const BULLHORN_WRITABLE_INTENTS = ['invited', 'in_assessment', 'review', 'advanced'];
const ATS_MOVE_STATUS_POLL_DELAYS_MS = [0, 500, 1000, 1500, 2500, 4000, 6000];
const ATS_MOVE_SUCCESS_STATUS = 'completed';
const ATS_MOVE_FAILURE_STATUSES = new Set(['completed_with_errors', 'failed']);

const waitFor = (delayMs) => new Promise((resolve) => {
  window.setTimeout(resolve, delayMs);
});

const writebackRunId = (response) => {
  const raw = response?.data?.ats_writeback_job_run_id;
  if (raw == null || String(raw).trim() === '') return null;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
};

const pollAtsWriteback = async (rolesApi, response) => {
  const jobRunId = writebackRunId(response);
  if (!jobRunId || typeof rolesApi.backgroundJobRun !== 'function') return null;
  for (const delayMs of ATS_MOVE_STATUS_POLL_DELAYS_MS) {
    if (delayMs > 0) await waitFor(delayMs);
    try {
      const res = await rolesApi.backgroundJobRun(jobRunId);
      const run = res?.data || null;
      const status = String(run?.status || '').trim().toLowerCase();
      if (status === ATS_MOVE_SUCCESS_STATUS || ATS_MOVE_FAILURE_STATUSES.has(status)) {
        return run;
      }
    } catch {
      // The operation is durably queued. A transient status-read failure does
      // not change its accepted state; the background-jobs rail remains truth.
    }
  }
  return null;
};

/**
 * Convert the server-resolved Bullhorn write targets into picker options.
 *
 * The option value is always Taali's intent; the visible label is the exact
 * Bullhorn status the backend will write. This matters because Bullhorn status
 * names are org-defined and because the reverse mapping can be ambiguous. The
 * server is the source of truth for the safe target (including its special
 * advanced-vs-placed resolver).
 *
 * Older backends do not return `resolved_write_targets`. For those payloads we
 * only expose unambiguous non-advance mappings. Advance still has a dedicated,
 * deterministic server resolver, but its exact remote label cannot be known on
 * an old payload, so it gets an explicitly generic fallback label.
 */
export function buildBullhornAtsStageOptions(payload) {
  const resolved = payload?.resolved_write_targets;
  if (resolved && typeof resolved === 'object' && !Array.isArray(resolved)) {
    return BULLHORN_WRITABLE_INTENTS.flatMap((intent) => {
      const remoteStatus = String(resolved[intent] || '').trim();
      return remoteStatus
        ? [{ slug: intent, name: remoteStatus, kind: intent }]
        : [];
    });
  }

  const mappings = Array.isArray(payload?.mappings) ? payload.mappings : [];
  const byIntent = new Map();
  mappings.forEach((mapping) => {
    const intent = String(mapping?.taali_stage || '').trim().toLowerCase();
    const remoteStatus = String(mapping?.remote_status || '').trim();
    if (
      mapping?.is_reject
      || !BULLHORN_WRITABLE_INTENTS.includes(intent)
      || !remoteStatus
    ) return;
    const statuses = byIntent.get(intent) || [];
    statuses.push(remoteStatus);
    byIntent.set(intent, statuses);
  });

  return BULLHORN_WRITABLE_INTENTS.flatMap((intent) => {
    const statuses = [...new Set(byIntent.get(intent) || [])];
    if (intent === 'advanced') {
      return statuses.length > 0
        ? [{ slug: intent, name: 'Mapped Bullhorn advance', kind: intent }]
        : [];
    }
    return statuses.length === 1
      ? [{ slug: intent, name: statuses[0], kind: intent }]
      : [];
  });
}

/**
 * Extracts the triage drawer state, handlers and external-ATS-stage fetch
 * out of the role detail page so the page itself stays under the
 * frontend architecture gate's 2,600-line cap.
 *
 * Returns props ready to spread onto <CandidateTriageDrawer>, the
 * application currently in the drawer, the close-drawer callback, and
 * a row-click handler that opens the drawer for plain clicks while
 * letting modifier-clicks fall through to the link's default
 * behaviour.
 */
export function useCandidateTriage({
  role,
  roleApplications,
  roleTasks,
  loadRoleWorkspace,
  // Patch a single application row after a mutation instead of reloading the
  // whole workspace. Falls back to loadRoleWorkspace when not provided.
  patchApplicationRow,
  showToast,
  rolesApi,
  viewCandidateReport,
}) {
  const [triageApplicationId, setTriageApplicationId] = useState(null);
  const [stageBusy, setStageBusy] = useState(false);
  const [assessmentBusy, setAssessmentBusy] = useState(false);
  const [rejectBusy, setRejectBusy] = useState(false);
  const [atsMoveBusy, setAtsMoveBusy] = useState(false);
  const [atsStages, setAtsStages] = useState([]);
  const [loadingAtsStages, setLoadingAtsStages] = useState(false);
  const atsProvider = roleAtsProvider(role);
  const externalJobId = roleExternalJobId(role);

  const triageApplication = useMemo(
    () => roleApplications.find((a) => Number(a?.id) === Number(triageApplicationId)) || null,
    [roleApplications, triageApplicationId],
  );

  // Pull the owning provider's stages once the role is known. Workable exposes
  // the job's stage catalogue; Bullhorn exposes the org's explicit remote
  // status map. Unmapped Bullhorn statuses deliberately stay unavailable here
  // rather than being guessed.
  useEffect(() => {
    if (!atsProvider) {
      setAtsStages([]);
      return undefined;
    }
    if (atsProvider === 'workable' && !externalJobId) {
      setAtsStages([]);
      return undefined;
    }
    let cancelled = false;
    setLoadingAtsStages(true);
    const request = atsProvider === 'bullhorn'
      ? apiClient.organizations.getBullhornStageMap()
      : apiClient.organizations.getWorkableStages({ shortcode: externalJobId });
    request
      .then((res) => {
        if (cancelled) return;
        const list = atsProvider === 'bullhorn'
          ? buildBullhornAtsStageOptions(res?.data)
          : (Array.isArray(res?.data?.stages) ? res.data.stages : []);
        setAtsStages(list);
      })
      .catch(() => {
        if (cancelled) return;
        setAtsStages([]);
      })
      .finally(() => {
        if (cancelled) return;
        setLoadingAtsStages(false);
      });
    return () => { cancelled = true; };
  }, [atsProvider, externalJobId]);

  const closeDrawer = useCallback(() => {
    setTriageApplicationId(null);
  }, []);

  // Single-candidate mutations patch just the affected row (a fast one-row
  // refetch) instead of re-downloading the whole 4,000-row workspace. The
  // success toast fires immediately; the row reconciles behind it.
  const refreshRow = useCallback(async (applicationId) => {
    if (patchApplicationRow) return patchApplicationRow(applicationId);
    return loadRoleWorkspace();
  }, [patchApplicationRow, loadRoleWorkspace]);

  const handleMoveStage = useCallback(async (application, nextStage) => {
    if (!application?.id || !nextStage) return;
    setStageBusy(true);
    try {
      if (role?.role_kind === 'sister' && rolesApi.updateRelatedApplicationStage) {
        await rolesApi.updateRelatedApplicationStage(
          role.id,
          application.id,
          { pipeline_stage: nextStage },
        );
      } else {
        await rolesApi.updateApplicationStage(application.id, { pipeline_stage: nextStage });
      }
      showToast(`Moved to ${String(nextStage).replace(/_/g, ' ')}.`, 'success');
      await refreshRow(application.id);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to move stage.'), 'error');
    } finally {
      setStageBusy(false);
    }
  }, [role?.id, role?.role_kind, rolesApi, refreshRow, showToast]);

  const handleSendAssessment = useCallback(async (application, taskId) => {
    if (!application?.id || !taskId) return;
    setAssessmentBusy(true);
    try {
      // 'auto' ⇒ omit task_id so an active A/B experiment on the role assigns
      // the arm (50/50, stable per candidate); otherwise force the picked task.
      const isAuto = String(taskId) === 'auto';
      const relatedRoleContext = role?.role_kind === 'sister'
        ? { role_id: Number(role.id) }
        : {};
      await rolesApi.createAssessment(
        application.id,
        isAuto
          ? relatedRoleContext
          : { ...relatedRoleContext, task_id: Number(taskId) },
      );
      showToast(
        isAuto ? 'Assessment invite sent (A/B-assigned task).' : 'Assessment invite sent.',
        'success',
      );
      await refreshRow(application.id);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to send invite.'), 'error');
    } finally {
      setAssessmentBusy(false);
    }
  }, [role?.id, role?.role_kind, rolesApi, refreshRow, showToast]);

  const handleReject = useCallback(async (application) => {
    if (!application?.id) return;
    setRejectBusy(true);
    try {
      const outcomeResponse = await rolesApi.updateApplicationOutcome(application.id, {
        application_outcome: 'rejected',
        reason: 'Rejected in Taali following recruiter review.',
        ...(role?.role_kind === 'sister' ? { acting_role_id: role.id } : {}),
      });
      setTriageApplicationId(null);
      // Patch the one row: it flips application_outcome → 'rejected' and the
      // active/rejected buckets re-derive, so it leaves the open list without
      // a 4,000-row refetch.
      await refreshRow(application.id);

      const jobRunId = writebackRunId(outcomeResponse);
      if (!jobRunId) {
        showToast('Candidate rejected.', 'success');
      } else {
        const providerLabel = atsProviderLabel(atsProvider);
        showToast(
          `Candidate rejected in Taali. Waiting for ${providerLabel} confirmation…`,
          'info',
        );
        const terminalRun = await pollAtsWriteback(rolesApi, outcomeResponse);
        const terminalStatus = String(terminalRun?.status || '').trim().toLowerCase();
        if (terminalStatus === ATS_MOVE_SUCCESS_STATUS) {
          // Pull the worker's persisted confirmed receipt; otherwise reopening
          // the drawer from the locally-patched row would still say queued.
          await refreshRow(application.id);
          showToast(`Candidate rejected in ${providerLabel}.`, 'success');
        } else if (ATS_MOVE_FAILURE_STATUSES.has(terminalStatus)) {
          await refreshRow(application.id);
          const detail = terminalRun?.error
            || terminalRun?.counters?.message
            || terminalRun?.counters?.code
            || `${providerLabel} rejected the outcome update.`;
          showToast(String(detail), 'error');
        } else {
          showToast(
            `${providerLabel} outcome sync is still queued and will retry automatically.`,
            'info',
          );
        }
      }
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to reject.'), 'error');
    } finally {
      setRejectBusy(false);
    }
  }, [atsProvider, role?.id, role?.role_kind, rolesApi, refreshRow, showToast]);

  const handleMoveToAtsStage = useCallback(async (application, targetStage, targetLabel = null) => {
    if (!application?.id || !targetStage) return;
    setAtsMoveBusy(true);
    const providerLabel = atsProviderLabel(atsProvider);
    try {
      const request = {
        target_stage: targetStage,
        ...(role?.role_kind === 'sister' ? { acting_role_id: role.id } : {}),
      };
      let moveResponse;
      if (rolesApi.moveApplicationToAtsStage) {
        try {
          moveResponse = await rolesApi.moveApplicationToAtsStage(application.id, request);
        } catch (error) {
          // During a rolling deployment, an older backend may not have the
          // provider-neutral route yet. Workable can safely use its established
          // endpoint; Bullhorn must never be rerouted to Workable.
          const status = Number(error?.response?.status || 0);
          if (
            atsProvider === 'workable'
            && [404, 405].includes(status)
            && rolesApi.moveApplicationToWorkableStage
          ) {
            moveResponse = await rolesApi.moveApplicationToWorkableStage(application.id, request);
          } else {
            throw error;
          }
        }
      } else if (atsProvider === 'workable' && rolesApi.moveApplicationToWorkableStage) {
        moveResponse = await rolesApi.moveApplicationToWorkableStage(application.id, request);
      } else {
        throw new Error(`No ${providerLabel} move endpoint is available.`);
      }

      showToast(`${providerLabel} move queued. Waiting for confirmation…`, 'info');

      const terminalRun = await pollAtsWriteback(rolesApi, moveResponse);

      const terminalStatus = String(terminalRun?.status || '').trim().toLowerCase();
      if (terminalStatus === ATS_MOVE_SUCCESS_STATUS) {
        await refreshRow(application.id);
        showToast(`Moved in ${providerLabel}: ${targetLabel || targetStage}.`, 'success');
      } else if (ATS_MOVE_FAILURE_STATUSES.has(terminalStatus)) {
        const detail = terminalRun?.error
          || terminalRun?.counters?.message
          || terminalRun?.counters?.code
          || `${providerLabel} rejected the stage move.`;
        showToast(String(detail), 'error');
      } else {
        showToast(
          `${providerLabel} move is still queued. The stage will update after the provider confirms it.`,
          'info',
        );
      }
    } catch (error) {
      showToast(getErrorMessage(error, `Failed to queue the move in ${providerLabel}.`), 'error');
    } finally {
      setAtsMoveBusy(false);
    }
  }, [atsProvider, role?.id, role?.role_kind, rolesApi, refreshRow, showToast]);

  // Plain click on a candidate row opens the drawer in-place. Modifier-
  // click (cmd/ctrl/shift/alt) and middle-click keep the anchor's
  // default behaviour so power-users still get the full standing
  // report in a new tab. ``event.button > 0`` treats undefined as a
  // left click, which matters for synthetic events from
  // @testing-library where ``button`` may not be set.
  const handleRowClick = useCallback((event, application) => {
    if (
      event.defaultPrevented
      || event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
      || event.button > 0
    ) {
      return;
    }
    event.preventDefault();
    const nextId = Number(application?.id) || null;
    // Toggle: clicking the same candidate's row again closes the
    // drawer. Clicking a different candidate moves the drawer to
    // that row.
    setTriageApplicationId((current) => (current === nextId ? null : nextId));
  }, []);

  const drawerProps = useMemo(() => ({
    application: triageApplication,
    roleId: role?.id ?? null,
    isRelatedRole: role?.role_kind === 'sister',
    hasRelatedRoles: Number(role?.sister_role_count || 0) > 0,
    roleFamily: role?.role_family ?? null,
    roleTasks,
    mode: 'inline',
    stageBusy,
    assessmentBusy,
    rejectBusy,
    atsProvider,
    atsStages,
    loadingAtsStages,
    atsMoveBusy,
    onClose: closeDrawer,
    onMoveStage: handleMoveStage,
    onSendAssessment: handleSendAssessment,
    onReject: handleReject,
    onMoveToAtsStage: handleMoveToAtsStage,
    onViewFullReport: viewCandidateReport,
  }), [
    triageApplication,
    role?.id,
    role?.role_kind,
    role?.sister_role_count,
    role?.role_family,
    roleTasks,
    stageBusy,
    assessmentBusy,
    rejectBusy,
    atsProvider,
    atsStages,
    loadingAtsStages,
    atsMoveBusy,
    closeDrawer,
    handleMoveStage,
    handleSendAssessment,
    handleReject,
    handleMoveToAtsStage,
    viewCandidateReport,
  ]);

  return {
    triageApplication,
    drawerProps,
    handleRowClick,
    closeDrawer,
  };
}

export default useCandidateTriage;
