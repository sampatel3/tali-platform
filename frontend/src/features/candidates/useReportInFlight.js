import { useEffect, useRef } from 'react';

const TERMINAL_SCORE_STATUSES = new Set([
  'done',
  'error',
  'cancelled',
  'unscorable',
  'excluded',
]);

/**
 * Keeps the candidate standing report fresh without asking the recruiter to
 * refresh, and defers the CV text off the critical path. Extracted from
 * CandidateStandingReportPage so the page stays under the architecture gate's
 * line cap.
 *
 * Two concerns, both keyed on the route's application id:
 *
 *  1. In-flight poll — while a full CV evaluation was just queued (`evaluating`)
 *     or the pending decision carries `rescore_in_flight` (Re-evaluate), poll
 *     the application every 4s (paused when the tab is hidden). A full
 *     evaluation completes the moment a real cv_match_score appears; either way
 *     a silent reload swaps the fresh dossier in with no spinner. Recruiter-view
 *     only (share routes are unauth and can't call these APIs).
 *
 *  2. Lazy CV text — the initial load drops include_cv_text (the CV tab is one
 *     of six). The first time the CV tab is opened, fetch the parsed text once
 *     and merge it into the application in place.
 */
export function useReportInFlight({
  rolesApi,
  numericApplicationId,
  viewRoleId,
  isShareRoute,
  activeTab,
  application,
  agentDecision,
  evaluating,
  setEvaluating,
  setApplication,
  loadAgentDecision,
  loadStandingReport,
}) {
  const rescoreInFlight = Boolean(agentDecision?.rescore_in_flight);
  const hasCurrentApplication = Number(application?.id) === numericApplicationId;
  const shouldPollScore = !isShareRoute
    && hasCurrentApplication
    && (evaluating || rescoreInFlight);
  const hadScore = application?.cv_match_score != null;
  const applicationRoleId = Number(application?.role_id);
  const reportRoleId = Number.isInteger(applicationRoleId) && applicationRoleId > 0
    ? applicationRoleId
    : viewRoleId;

  // When a re-score finishes, the decision poll clears rescore_in_flight — but
  // the score ring / requirements / provenance are all application-derived, so
  // the report must be silently reloaded on that clear, not just the decision.
  // Track the previous in-flight state to catch the true→false transition.
  const wasRescoringRef = useRef(false);
  useEffect(() => {
    if (isShareRoute || !hasCurrentApplication) {
      wasRescoringRef.current = false;
      return;
    }
    if (wasRescoringRef.current && !rescoreInFlight) {
      // Re-score just completed — refresh the whole dossier in place.
      void loadStandingReport({ silent: true });
    }
    wasRescoringRef.current = rescoreInFlight;
  }, [hasCurrentApplication, rescoreInFlight, isShareRoute, loadStandingReport]);

  useEffect(() => {
    if (!shouldPollScore || !rolesApi?.getApplication || !Number.isFinite(numericApplicationId)) {
      return undefined;
    }
    let cancelled = false;
    let handle;
    const scheduleNext = () => {
      if (!cancelled) handle = window.setTimeout(poll, 4000);
    };
    const poll = async () => {
      let continuePolling = true;
      if (typeof document !== 'undefined' && document.hidden) {
        scheduleNext();
        return;
      }
      try {
        const res = reportRoleId
          ? await rolesApi.getApplication(numericApplicationId, { params: { view_role_id: reportRoleId } })
          : await rolesApi.getApplication(numericApplicationId);
        const fresh = res?.data;
        if (cancelled || !fresh) return;
        // A full evaluation is done the moment a real cv_match_score appears
        // (the pre-screen-out path had none). For a re-evaluate, we refetch the
        // decision; when rescore_in_flight clears the effect above does the
        // silent report reload. Either way the fresh dossier lands with no
        // spinner and no manual refresh.
        const scored = fresh.cv_match_score != null;
        const terminalWithoutScore = !scored && TERMINAL_SCORE_STATUSES.has(
          String(fresh.score_status || '').trim().toLowerCase(),
        );
        if (evaluating && ((scored && !hadScore) || terminalWithoutScore)) {
          // A terminal attempt can legitimately finish without a score. Stop
          // the optimistic spinner and re-read the complete dossier/decision so
          // the retry action and persisted outcome replace an endless poll.
          continuePolling = false;
          setEvaluating(false);
          await Promise.all([loadAgentDecision(), loadStandingReport({ silent: true })]);
        } else if (rescoreInFlight) {
          await loadAgentDecision();
        }
      } catch {
        // Transient failure — keep polling; the next tick reconciles.
      } finally {
        // Completion-based scheduling guarantees at most one report request is
        // active. A slow response therefore cannot produce overlapping reads
        // (and their duplicate downstream decision/report refreshes).
        if (continuePolling) scheduleNext();
      }
    };
    scheduleNext();
    return () => { cancelled = true; window.clearTimeout(handle); };
  }, [
    shouldPollScore, evaluating, rescoreInFlight, hadScore,
    numericApplicationId, reportRoleId, rolesApi, setEvaluating, loadAgentDecision, loadStandingReport,
  ]);

  // Lazy CV text — one-shot per application id, triggered by opening the CV tab.
  const cvTextFetchedRef = useRef(false);
  useEffect(() => { cvTextFetchedRef.current = false; }, [numericApplicationId]);
  useEffect(() => {
    if (activeTab !== 'cv' || isShareRoute || cvTextFetchedRef.current) return undefined;
    if (!rolesApi?.getApplication || !Number.isFinite(numericApplicationId)) return undefined;
    // Wait for the cold load to populate `application` before firing — if this
    // request beats the initial wave, merging into a null application would
    // discard the CV text and the one-shot guard would block any retry.
    if (!hasCurrentApplication) return undefined;
    if (application.cv_text) { cvTextFetchedRef.current = true; return undefined; }
    let cancelled = false;
    rolesApi.getApplication(numericApplicationId, {
      params: { include_cv_text: true, ...(reportRoleId ? { view_role_id: reportRoleId } : {}) },
    })
      .then((res) => {
        const fresh = res?.data;
        if (cancelled || Number(fresh?.id) !== numericApplicationId) return;
        // Only mark fetched once the merge actually lands, so a failed/empty
        // response can be retried on the next CV-tab activation.
        setApplication((cur) => {
          if (Number(cur?.id) !== numericApplicationId) return cur;
          cvTextFetchedRef.current = true;
          return { ...cur, cv_text: fresh.cv_text, cv_sections: fresh.cv_sections ?? cur.cv_sections };
        });
      })
      .catch(() => { /* leave the viewer's download-original fallback; allow retry */ });
    return () => { cancelled = true; };
  }, [activeTab, isShareRoute, hasCurrentApplication, application, numericApplicationId,
    reportRoleId, rolesApi, setApplication]);
}

export default useReportInFlight;
