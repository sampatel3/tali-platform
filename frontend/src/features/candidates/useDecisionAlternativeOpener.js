import { useCallback, useRef } from 'react';

import { loadDecisionWorkableStages } from '../../shared/decisions/loadDecisionWorkableStages';
import {
  DECISION_ACTIONS,
  expectedRoleFamilyRequestBody,
  isDecisionChangedError,
  isDecisionStaleError,
  isRoleFamilyChangedError,
} from '../../shared/decisions/decisionActions';
import { getErrorMessage } from './candidatesUiUtils';

export const useDecisionAlternativeOpener = ({
  organizationsApi,
  isCurrent = () => true,
  setAlternativeFor,
  setBusy,
  showToast,
}) => {
  const inFlightRef = useRef(null);
  return useCallback(async (decision, alternative) => {
    if (!decision || !alternative) return;
    const requestIsCurrent = () => isCurrent(decision);
    const currentRequest = inFlightRef.current;
    if (currentRequest?.isCurrent?.()) return;
    const request = { isCurrent: requestIsCurrent };
    inFlightRef.current = request;
    setBusy(true);
    try {
      const workableStages = await loadDecisionWorkableStages(
        organizationsApi,
        decision,
        alternative,
      );
      if (!requestIsCurrent()) return;
      setAlternativeFor({ decision, alternative, workableStages });
    } catch (error) {
      if (!requestIsCurrent()) return;
      // Never degrade a failed stage lookup into a local-only advance. The
      // action remains available so the recruiter can retry the fresh lookup.
      showToast(
        getErrorMessage(error, "Couldn't load Workable stages. Try again."),
        'error',
      );
    } finally {
      if (inFlightRef.current === request) {
        inFlightRef.current = null;
        if (isCurrent()) setBusy(false);
      }
    }
  }, [isCurrent, organizationsApi, setAlternativeFor, setBusy, showToast]);
};

export const useRouteOperationFence = (identity) => {
  const identityRef = useRef(identity);
  const epochRef = useRef(0);
  const activeRef = useRef(new Map());
  identityRef.current = identity;
  const isCurrent = useCallback((token) => Boolean(
    token
    && identityRef.current === token.identity
    && epochRef.current === token.epoch
    && activeRef.current.get(token.kind) === token
  ), []);
  const begin = useCallback((kind, expectedIdentity) => {
    if (identityRef.current !== expectedIdentity) return null;
    const active = activeRef.current.get(kind);
    if (isCurrent(active)) return null;
    const token = { kind, identity: expectedIdentity, epoch: epochRef.current };
    activeRef.current.set(kind, token);
    return token;
  }, [isCurrent]);
  const commit = useCallback((token, callback) => {
    if (!isCurrent(token)) return false;
    callback();
    return true;
  }, [isCurrent]);
  const finish = useCallback((token, callback) => {
    if (!isCurrent(token)) return false;
    callback?.();
    activeRef.current.delete(token.kind);
    return true;
  }, [isCurrent]);
  const invalidate = useCallback(() => {
    epochRef.current += 1;
    activeRef.current.clear();
  }, []);
  return { begin, commit, finish, identityRef, invalidate, isCurrent };
};

export const useAgentDecisionReader = ({
  agentApi,
  applicationId,
  identity,
  isShareRoute,
  setAgentDecision,
}) => {
  const identityRef = useRef(identity);
  const generationRef = useRef(0);
  const inFlightRef = useRef(null);
  identityRef.current = identity;
  const invalidate = useCallback(() => {
    generationRef.current += 1;
    inFlightRef.current = null;
  }, []);
  const begin = useCallback(() => {
    invalidate();
    return generationRef.current;
  }, [invalidate]);
  const isCurrent = useCallback((generation, expectedIdentity) => (
    generationRef.current === generation && identityRef.current === expectedIdentity
  ), []);
  const load = useCallback(() => {
    if (isShareRoute || !agentApi?.listDecisions || !applicationId
      || identityRef.current !== identity) return Promise.resolve(false);
    const current = inFlightRef.current;
    if (current?.identity === identity && current.generation === generationRef.current) {
      return current.promise;
    }
    const generation = ++generationRef.current;
    const request = { identity, generation, promise: null };
    request.promise = (async () => {
      try {
        const response = await agentApi.listDecisions({
          application_id: applicationId, status: 'current', limit: 1,
        });
        if (!isCurrent(generation, identity)) return false;
        setAgentDecision(Array.isArray(response?.data) ? (response.data[0] || null) : null);
        return true;
      } catch {
        return false;
      } finally {
        if (inFlightRef.current === request) inFlightRef.current = null;
      }
    })();
    inFlightRef.current = request;
    return request.promise;
  }, [agentApi, applicationId, identity, isCurrent, isShareRoute, setAgentDecision]);
  return { begin, identityRef, invalidate, isCurrent, load };
};

export const useCandidateDecisionActions = ({
  agentApi,
  beginOperation,
  finishOperation,
  isOperationCurrent,
  loadAgentDecision,
  loadStandingReport,
  openDecisionAlternative,
  refreshRoleFamilyDecision,
  routeIdentity,
  setDecisionBusy,
  showToast,
}) => {
  const approve = useCallback(async (decision) => {
    if (!decision) return;
    const spec = DECISION_ACTIONS[decision.decision_type];
    if (spec?.primary) return openDecisionAlternative(decision, spec.primary);
    const operation = beginOperation('decision', routeIdentity);
    if (!operation) return;
    setDecisionBusy(true);
    try {
      await agentApi.approveDecision(decision.id, expectedRoleFamilyRequestBody(decision));
      if (!isOperationCurrent(operation)) return;
      showToast('Approved.', 'success');
      await loadStandingReport({ silent: true });
    } catch (error) {
      if (!isOperationCurrent(operation)) return;
      if (isRoleFamilyChangedError(error) || isDecisionChangedError(error)) {
        await refreshRoleFamilyDecision();
      } else if (isDecisionStaleError(error)) {
        showToast("This decision's inputs changed — re-evaluate to refresh it.", 'warning');
      } else {
        showToast(getErrorMessage(error, "Couldn't approve this decision."), 'error');
      }
    } finally {
      finishOperation(operation, () => setDecisionBusy(false));
    }
  }, [agentApi, beginOperation, finishOperation, isOperationCurrent, loadStandingReport, openDecisionAlternative, refreshRoleFamilyDecision, routeIdentity, setDecisionBusy, showToast]);
  const snooze = useCallback(async (decision) => {
    if (!decision) return;
    const operation = beginOperation('decision', routeIdentity);
    if (!operation) return;
    setDecisionBusy(true);
    try {
      await agentApi.snoozeDecision(decision.id);
      if (!isOperationCurrent(operation)) return;
      showToast('Snoozed for 1h.', 'success');
      await loadAgentDecision();
    } catch (error) {
      if (isOperationCurrent(operation)) showToast(getErrorMessage(error, 'Snooze failed'), 'error');
    } finally {
      finishOperation(operation, () => setDecisionBusy(false));
    }
  }, [agentApi, beginOperation, finishOperation, isOperationCurrent, loadAgentDecision, routeIdentity, setDecisionBusy, showToast]);
  const reEvaluate = useCallback(async (decision) => {
    if (!decision) return;
    const operation = beginOperation('decision', routeIdentity);
    if (!operation) return;
    setDecisionBusy(true);
    try {
      await agentApi.reEvaluateDecision(decision.id);
      if (!isOperationCurrent(operation)) return;
      showToast('Re-evaluating with fresh inputs…', 'success');
      await loadStandingReport({ silent: true });
    } catch (error) {
      if (isOperationCurrent(operation)) showToast(getErrorMessage(error, 'Re-evaluate failed'), 'error');
    } finally {
      finishOperation(operation, () => setDecisionBusy(false));
    }
  }, [agentApi, beginOperation, finishOperation, isOperationCurrent, loadStandingReport, routeIdentity, setDecisionBusy, showToast]);
  return { approve, alternative: openDecisionAlternative, reEvaluate, snooze };
};

export default useDecisionAlternativeOpener;
