import { useCallback } from 'react';

import { viewShareLink } from '../../shared/api';
import { COMPLETED_ASSESSMENT_STATUSES } from './assessmentViewModels';
import { resolveAssessmentId, resolveAssessmentStatus } from './assessmentApplicationState';
import { getErrorMessage } from './candidatesUiUtils';

export const positiveIntegerOrNull = (value) => {
  if (value == null || String(value).trim() === '') return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
};

const decisionUnavailable = () => new Error('Decision refresh unavailable');

/**
 * Loads the recruiter-only report resources while keeping the application and
 * decision scoped to the same role. Kept outside the page component so the
 * reconciliation rules are independently testable and the page remains an
 * orchestrator rather than another data-access implementation.
 */
export async function loadRecruiterStandingReportData({
  agentApi,
  assessmentsApi,
  numericApplicationId,
  requireDecision = false,
  rolesApi,
  viewRoleId,
}) {
  const requestedRoleId = positiveIntegerOrNull(viewRoleId);
  const loadDecisionForRole = (roleId) => {
    const resolvedRoleId = positiveIntegerOrNull(roleId);
    if (!resolvedRoleId || !agentApi?.listDecisions) {
      return requireDecision
        ? Promise.reject(decisionUnavailable())
        : Promise.resolve(null);
    }
    return Promise.resolve().then(() => agentApi.listDecisions({
      application_id: numericApplicationId,
      role_id: resolvedRoleId,
      status: 'current',
      limit: 1,
    }));
  };

  // The projected application, events and role-scoped decision start together
  // when the URL already carries role context. Legacy links wait for the
  // application and use its canonical role before reading a decision.
  const appRequest = Promise.resolve().then(() => rolesApi.getApplication(
    numericApplicationId,
    requestedRoleId ? { params: { view_role_id: requestedRoleId } } : {},
  ));
  const decisionRequest = requestedRoleId
    ? loadDecisionForRole(requestedRoleId)
    : appRequest.then((appResponse) => loadDecisionForRole(appResponse?.data?.role_id));
  const eventsRequest = rolesApi?.listApplicationEvents
    ? Promise.resolve()
      .then(() => rolesApi.listApplicationEvents(numericApplicationId))
      .catch(() => null)
    : Promise.resolve(null);

  const [appRes, eventsRes, initialDecisionRes] = await Promise.all([
    appRequest,
    eventsRequest,
    requireDecision ? decisionRequest : decisionRequest.catch(() => null),
  ]);
  const application = appRes?.data || null;
  const returnedRoleId = positiveIntegerOrNull(application?.role_id);
  let decisionRes = initialDecisionRes;

  // The detail endpoint can intentionally fall back to the canonical
  // application when a requested sister role is no longer projectable. Re-read
  // the decision for the role actually returned so scores and recommendations
  // can never come from different role projections.
  if (requestedRoleId && returnedRoleId !== requestedRoleId) {
    const reconciledRequest = loadDecisionForRole(returnedRoleId);
    decisionRes = requireDecision
      ? await reconciledRequest
      : await reconciledRequest.catch(() => null);
  }

  const assessmentId = resolveAssessmentId(application);
  const hasCompletedAssessment = Boolean(
    assessmentId
    && COMPLETED_ASSESSMENT_STATUSES.has(resolveAssessmentStatus(application)),
  );
  const assessmentRes = hasCompletedAssessment && assessmentsApi?.get
    ? await assessmentsApi.get(Number(assessmentId))
    : null;
  const embeddedEvents = Array.isArray(application?.application_events)
    ? application.application_events
    : [];

  return {
    application,
    completedAssessment: assessmentRes?.data || null,
    decision: Array.isArray(decisionRes?.data) ? (decisionRes.data[0] || null) : null,
    events: Array.isArray(eventsRes?.data)
      ? eventsRes.data
      : (eventsRes?.data?.items || embeddedEvents),
  };
}

/**
 * Owns the report's cold/silent load lifecycle and route-generation fences.
 * The caller retains presentation state; this hook only commits a complete,
 * route-matching snapshot.
 */
export function useStandingReportLoader({
  agentApi,
  assessmentsApi,
  beginDecisionRead,
  isDecisionReadCurrent,
  isShareRoute,
  numericApplicationId,
  reportRequestGenerationRef,
  reportRouteIdentity,
  reportRouteIdentityRef,
  rolesApi,
  routeApplicationKey,
  setAgentDecision,
  setApplication,
  setApplicationEvents,
  setCompletedAssessment,
  setError,
  setLoading,
  setRefreshing,
  setReportStateRouteIdentity,
  setShareViewMode,
  sharedRouteToken,
  showcaseReportViewMode,
  showToast,
  viewRoleId,
}) {
  return useCallback(async ({
    silent = false,
    requireDecision = false,
    toastOnError = true,
  } = {}) => {
    const requestedRouteIdentity = reportRouteIdentity;
    if (reportRouteIdentityRef.current !== requestedRouteIdentity) return false;
    const requestGeneration = ++reportRequestGenerationRef.current;
    const decisionReadGeneration = beginDecisionRead();
    const isCurrentRequest = () => reportRequestGenerationRef.current === requestGeneration
      && reportRouteIdentityRef.current === requestedRouteIdentity;
    const canCommitDecision = () => isCurrentRequest()
      && isDecisionReadCurrent(decisionReadGeneration, requestedRouteIdentity);
    setReportStateRouteIdentity(requestedRouteIdentity);

    if (routeApplicationKey === 'demo') {
      const {
        AI_SHOWCASE_APPLICATION,
        AI_SHOWCASE_APPLICATION_EVENTS,
        AI_SHOWCASE_AGENT_DECISION,
        AI_SHOWCASE_COMPLETED_ASSESSMENT,
      } = await import('../demo/productWalkthroughModels');
      if (!isCurrentRequest()) return false;
      setApplication(AI_SHOWCASE_APPLICATION);
      setCompletedAssessment(AI_SHOWCASE_COMPLETED_ASSESSMENT);
      setApplicationEvents(AI_SHOWCASE_APPLICATION_EVENTS);
      if (canCommitDecision()) setAgentDecision(AI_SHOWCASE_AGENT_DECISION);
      setShareViewMode(showcaseReportViewMode);
      setError('');
      setLoading(false);
      return true;
    }

    const canLoadById = !isShareRoute
      && rolesApi?.getApplication
      && Number.isFinite(numericApplicationId);
    const canLoadByShare = Boolean(isShareRoute && sharedRouteToken);
    if (!canLoadById && !canLoadByShare) {
      setApplication(null);
      setCompletedAssessment(null);
      setError('Candidate report unavailable.');
      setLoading(false);
      return false;
    }

    if (silent) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      if (isShareRoute) {
        const shareRes = await viewShareLink(sharedRouteToken);
        if (!isCurrentRequest()) return false;
        const payload = shareRes?.data || {};
        const application = payload.application || null;
        setShareViewMode(payload.view === 'client' ? 'client' : 'recruiter');
        setApplication(application);
        setCompletedAssessment(null);
        if (canCommitDecision()) setAgentDecision(null);
        setApplicationEvents(Array.isArray(application?.application_events)
          ? application.application_events
          : []);
      } else {
        const snapshot = await loadRecruiterStandingReportData({
          agentApi,
          assessmentsApi,
          numericApplicationId,
          requireDecision,
          rolesApi,
          viewRoleId,
        });
        if (!isCurrentRequest()) return false;
        setShareViewMode(null);
        setApplication(snapshot.application);
        setCompletedAssessment(snapshot.completedAssessment);
        if (canCommitDecision()) setAgentDecision(snapshot.decision);
        setApplicationEvents(snapshot.events);
      }
      return true;
    } catch (error) {
      if (!isCurrentRequest()) return false;
      const message = getErrorMessage(error, 'Failed to load candidate report.');
      if (!silent) {
        setApplication(null);
        setCompletedAssessment(null);
        setApplicationEvents([]);
        setError(message);
      }
      if (!isShareRoute && toastOnError) showToast(message, 'error');
      return false;
    } finally {
      if (isCurrentRequest()) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [
    agentApi,
    assessmentsApi,
    beginDecisionRead,
    isDecisionReadCurrent,
    isShareRoute,
    numericApplicationId,
    reportRequestGenerationRef,
    reportRouteIdentity,
    reportRouteIdentityRef,
    rolesApi,
    routeApplicationKey,
    setAgentDecision,
    setApplication,
    setApplicationEvents,
    setCompletedAssessment,
    setError,
    setLoading,
    setRefreshing,
    setReportStateRouteIdentity,
    setShareViewMode,
    sharedRouteToken,
    showcaseReportViewMode,
    showToast,
    viewRoleId,
  ]);
}

export default useStandingReportLoader;
