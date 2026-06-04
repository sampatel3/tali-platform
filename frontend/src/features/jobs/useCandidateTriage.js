import { useCallback, useEffect, useMemo, useState } from 'react';

import * as apiClient from '../../shared/api';
import { getErrorMessage } from '../candidates/candidatesUiUtils';

/**
 * Extracts the triage drawer state, handlers and Workable-stage fetch
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
  showToast,
  rolesApi,
  viewCandidateReport,
}) {
  const [triageApplicationId, setTriageApplicationId] = useState(null);
  const [stageBusy, setStageBusy] = useState(false);
  const [assessmentBusy, setAssessmentBusy] = useState(false);
  const [rejectBusy, setRejectBusy] = useState(false);
  const [workableMoveBusy, setWorkableMoveBusy] = useState(false);
  const [workableStages, setWorkableStages] = useState([]);
  const [loadingWorkableStages, setLoadingWorkableStages] = useState(false);

  const triageApplication = useMemo(
    () => roleApplications.find((a) => Number(a?.id) === Number(triageApplicationId)) || null,
    [roleApplications, triageApplicationId],
  );

  // Pull the role's Workable stages once we know the job shortcode. We
  // load eagerly so the picker is ready by the time the recruiter opens
  // the drawer at ``review``; failures fall back to an empty list and
  // the picker shows a "no Workable stages found" placeholder.
  useEffect(() => {
    const shortcode = role?.workable_job_id;
    if (!shortcode) {
      setWorkableStages([]);
      return undefined;
    }
    let cancelled = false;
    setLoadingWorkableStages(true);
    apiClient.organizations.getWorkableStages({ shortcode })
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res?.data?.stages) ? res.data.stages : [];
        setWorkableStages(list);
      })
      .catch(() => {
        if (cancelled) return;
        setWorkableStages([]);
      })
      .finally(() => {
        if (cancelled) return;
        setLoadingWorkableStages(false);
      });
    return () => { cancelled = true; };
  }, [role?.workable_job_id]);

  const closeDrawer = useCallback(() => {
    setTriageApplicationId(null);
  }, []);

  const handleMoveStage = useCallback(async (application, nextStage) => {
    if (!application?.id || !nextStage) return;
    setStageBusy(true);
    try {
      await rolesApi.updateApplicationStage(application.id, { pipeline_stage: nextStage });
      await loadRoleWorkspace();
      showToast(`Moved to ${String(nextStage).replace(/_/g, ' ')}.`, 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to move stage.'), 'error');
    } finally {
      setStageBusy(false);
    }
  }, [rolesApi, loadRoleWorkspace, showToast]);

  const handleSendAssessment = useCallback(async (application, taskId) => {
    if (!application?.id || !taskId) return;
    setAssessmentBusy(true);
    try {
      // 'auto' ⇒ omit task_id so an active A/B experiment on the role assigns
      // the arm (50/50, stable per candidate); otherwise force the picked task.
      const isAuto = String(taskId) === 'auto';
      await rolesApi.createAssessment(
        application.id,
        isAuto ? {} : { task_id: Number(taskId) },
      );
      await loadRoleWorkspace();
      showToast(
        isAuto ? 'Assessment invite sent (A/B-assigned task).' : 'Assessment invite sent.',
        'success',
      );
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to send invite.'), 'error');
    } finally {
      setAssessmentBusy(false);
    }
  }, [rolesApi, loadRoleWorkspace, showToast]);

  const handleReject = useCallback(async (application) => {
    if (!application?.id) return;
    setRejectBusy(true);
    try {
      await rolesApi.updateApplicationOutcome(application.id, {
        application_outcome: 'rejected',
        reason: 'Recruiter reject from role view',
      });
      await loadRoleWorkspace();
      showToast('Candidate rejected.', 'success');
      setTriageApplicationId(null);
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to reject.'), 'error');
    } finally {
      setRejectBusy(false);
    }
  }, [rolesApi, loadRoleWorkspace, showToast]);

  const handleMoveToWorkableStage = useCallback(async (application, targetStage) => {
    if (!application?.id || !targetStage) return;
    setWorkableMoveBusy(true);
    try {
      await rolesApi.moveApplicationToWorkableStage(application.id, { target_stage: targetStage });
      await loadRoleWorkspace();
      showToast(`Sent to Workable: ${targetStage}.`, 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to move in Workable.'), 'error');
    } finally {
      setWorkableMoveBusy(false);
    }
  }, [rolesApi, loadRoleWorkspace, showToast]);

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
    roleTasks,
    mode: 'inline',
    stageBusy,
    assessmentBusy,
    rejectBusy,
    workableStages,
    loadingWorkableStages,
    workableMoveBusy,
    onClose: closeDrawer,
    onMoveStage: handleMoveStage,
    onSendAssessment: handleSendAssessment,
    onReject: handleReject,
    onMoveToWorkableStage: handleMoveToWorkableStage,
    onViewFullReport: viewCandidateReport,
  }), [
    triageApplication,
    role?.id,
    roleTasks,
    stageBusy,
    assessmentBusy,
    rejectBusy,
    workableStages,
    loadingWorkableStages,
    workableMoveBusy,
    closeDrawer,
    handleMoveStage,
    handleSendAssessment,
    handleReject,
    handleMoveToWorkableStage,
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
