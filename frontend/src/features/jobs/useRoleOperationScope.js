import { useCallback, useRef, useState } from 'react';

const normalizeRoleId = (value) => {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null;
};

const operationToken = (scope, operation) => `${scope.roleId}:${operation}`;

/**
 * Shared route boundary for every async mutation on the job page.
 *
 * A role operation remains valid for the role that started it, even if the
 * recruiter briefly visits another role. UI commits and toasts only land when
 * that role is currently visible, while pending state is retained per role so
 * A -> B -> A navigation cannot dispatch a duplicate write.
 */
export const useRoleOperationScope = (roleId) => {
  const normalizedRoleId = normalizeRoleId(roleId);
  const currentRoleIdRef = useRef(normalizedRoleId);
  currentRoleIdRef.current = normalizedRoleId;
  const pendingRef = useRef(new Set());
  const [pendingOperations, setPendingOperations] = useState(() => new Set());

  const captureRoleScope = useCallback((expectedRoleId = currentRoleIdRef.current) => {
    const normalizedExpected = normalizeRoleId(expectedRoleId);
    if (normalizedExpected === null || currentRoleIdRef.current !== normalizedExpected) return null;
    return Object.freeze({ roleId: normalizedExpected });
  }, []);

  const isCurrentRoleScope = useCallback((scope) => (
    scope?.roleId != null && currentRoleIdRef.current === scope.roleId
  ), []);

  const commitRoleScope = useCallback((scope, commit) => {
    if (!isCurrentRoleScope(scope)) return false;
    commit();
    return true;
  }, [isCurrentRoleScope]);

  const beginRoleOperation = useCallback((scope, operation) => {
    if (!scope || !operation) return false;
    const token = operationToken(scope, operation);
    if (pendingRef.current.has(token)) return false;
    const next = new Set(pendingRef.current);
    next.add(token);
    pendingRef.current = next;
    setPendingOperations(next);
    return true;
  }, []);

  const finishRoleOperation = useCallback((scope, operation) => {
    if (!scope || !operation) return;
    const token = operationToken(scope, operation);
    if (!pendingRef.current.has(token)) return;
    const next = new Set(pendingRef.current);
    next.delete(token);
    pendingRef.current = next;
    setPendingOperations(next);
  }, []);

  const isRoleOperationPending = useCallback((operation, targetRoleId = currentRoleIdRef.current) => {
    const normalizedTarget = normalizeRoleId(targetRoleId);
    if (normalizedTarget === null || !operation) return false;
    return pendingOperations.has(operationToken({ roleId: normalizedTarget }, operation));
  }, [pendingOperations]);

  return {
    beginRoleOperation,
    captureRoleScope,
    commitRoleScope,
    currentRoleIdRef,
    finishRoleOperation,
    isCurrentRoleScope,
    isRoleOperationPending,
  };
};

export default useRoleOperationScope;
