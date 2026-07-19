export const RELATED_ROLE_PAID_SCOPE_CHANGED = 'RELATED_ROLE_PAID_SCOPE_CHANGED';
export const RELATED_ROLE_RECOVERY_SCOPE_CHANGED = 'RELATED_ROLE_RECOVERY_SCOPE_CHANGED';

const positiveInteger = (value) => {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
};

const count = (value) => {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : null;
};

const dollarsToCents = (value) => {
  const dollars = Number(value);
  if (!Number.isFinite(dollars) || dollars <= 0) return null;
  return count(Math.round(dollars * 100));
};

export const relatedRolePublishAuthorization = (
  brief,
  preview,
  selectedMonthlyBudgetDollars = null,
) => {
  const sourceRoleId = positiveInteger(
    preview?.source_role_id ?? brief?.source_role?.role_id
      ?? brief?.source_role_id ?? brief?.id,
  );
  const sourceRoleName = String(
    preview?.source_role_name ?? brief?.source_role?.name ?? brief?.name ?? '',
  ).trim();
  const sourceRoleVersion = positiveInteger(
    preview?.source_role_version ?? brief?.source_role?.version ?? brief?.version,
  );
  const candidatesTotal = count(preview?.candidates_total);
  const scoreableCount = count(
    preview?.candidates_scoreable ?? preview?.candidates_with_cv,
  );
  const defaultMonthlyBudgetCents = positiveInteger(preview?.proposed_monthly_budget_cents);
  const minimumInitialBudgetCents = count(preview?.minimum_initial_budget_cents);
  const approvedMonthlyBudgetCents = selectedMonthlyBudgetDollars == null
    ? defaultMonthlyBudgetCents
    : dollarsToCents(selectedMonthlyBudgetDollars);
  if (!sourceRoleId || !sourceRoleName || !sourceRoleVersion
    || candidatesTotal == null || scoreableCount == null || !defaultMonthlyBudgetCents
    || minimumInitialBudgetCents == null || !approvedMonthlyBudgetCents
    || approvedMonthlyBudgetCents < minimumInitialBudgetCents
    || approvedMonthlyBudgetCents > 10_000_000) return null;
  return {
    request: {
      expected_source_role_id: sourceRoleId,
      expected_source_role_name: sourceRoleName,
      expected_source_role_version: sourceRoleVersion,
      expected_default_monthly_budget_cents: defaultMonthlyBudgetCents,
      approved_max_candidates_total: candidatesTotal,
      approved_max_scoreable_count: scoreableCount,
      approved_monthly_budget_cents: approvedMonthlyBudgetCents,
    },
    candidatesTotal,
    scoreableCount,
    estimatedCostUsd: Math.max(0, Number(preview?.estimated_cost_usd || 0)),
    monthlyBudgetCents: approvedMonthlyBudgetCents,
    minimumInitialBudgetCents,
    ongoingScoreCostUsd: Math.max(0, Number(preview?.ongoing_score_cost_usd || 0)),
  };
};

export const relatedRoleRescoreAuthorization = (role, status) => {
  const expectedVersion = positiveInteger(status?.role_version ?? role?.version);
  const scoreableCount = count(
    status?.cohort_scoreable ?? status?.scoreable_total ?? status?.total,
  );
  if (!expectedVersion || scoreableCount == null) return null;
  return {
    request: {
      expected_version: expectedVersion,
      approved_max_scoreable_count: scoreableCount,
    },
    candidatesTotal: count(status?.cohort_total ?? status?.total) ?? scoreableCount,
    scoreableCount,
    estimatedCostUsd: Math.max(0, Number(status?.estimated_rescore_cost_usd || 0)),
  };
};

export const relatedRoleRecoveryAuthorization = (role, scope) => {
  const roleId = positiveInteger(role?.id);
  const scopeRoleId = positiveInteger(scope?.role_id);
  const expectedVersion = positiveInteger(scope?.role_version);
  const workspaceVersion = positiveInteger(scope?.workspace_control_version);
  const candidatesTotal = count(scope?.cohort_total);
  const scoreableCount = count(scope?.cohort_scoreable);
  const cohortFingerprint = String(scope?.cohort_fingerprint || '').trim();
  const family = scope?.role_family;
  if (!roleId || scopeRoleId !== roleId || scope?.workspace_paused !== true
    || !expectedVersion || !workspaceVersion || candidatesTotal == null
    || scoreableCount == null || !/^[0-9a-f]{64}$/.test(cohortFingerprint)
    || !family?.owner || !Array.isArray(family?.related)) return null;
  return {
    expected_version: expectedVersion,
    expected_workspace_control_version: workspaceVersion,
    expected_role_family: family,
    cohort_fingerprint: cohortFingerprint,
    approved_max_candidates_total: candidatesTotal,
    approved_max_scoreable_count: scoreableCount,
  };
};

const errorCode = (error) => {
  const detail = error?.response?.data?.detail;
  return String(detail && typeof detail === 'object' ? detail.code : detail)
    .trim()
    .toUpperCase();
};

export const isRelatedRolePaidScopeChangedError = (error) => (
  error?.response?.status === 409 && errorCode(error) === RELATED_ROLE_PAID_SCOPE_CHANGED
);

export const isRelatedRolePaidAuthorizationError = (error) => (
  error?.response?.status === 409
  && [
    RELATED_ROLE_PAID_SCOPE_CHANGED,
    RELATED_ROLE_RECOVERY_SCOPE_CHANGED,
    'ROLE_FAMILY_CHANGED',
    'ROLE_VERSION_CONFLICT',
  ].includes(errorCode(error))
);
