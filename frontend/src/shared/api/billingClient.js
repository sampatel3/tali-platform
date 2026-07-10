import api from './httpClient';

export const billing = {
  usage: () => api.get('/billing/usage'),
  costs: () => api.get('/billing/costs'),
  credits: () => api.get('/billing/credits'),
  // Per-feature usage breakdown for the trailing N days. Used by the new
  // settings billing tab.
  usageBreakdown: (periodDays = 30) =>
    api.get('/billing/usage-breakdown', { params: { period_days: periodDays } }),
  // Recent usage events log (newest first). Used by the consumption table.
  usageEvents: (limit = 50) =>
    api.get('/billing/usage-events', { params: { limit } }),
  // Daily token + cost time series for the settings → usage tab.
  // group_by: 'model' | 'feature' | 'user'. Period clamped 1..90 days.
  usageTimeseries: (periodDays = 30, groupBy = 'model') =>
    api.get('/billing/usage-timeseries', {
      params: { period_days: periodDays, group_by: groupBy },
    }),
  // Anthropic vs internal reconciliation rows + totals. Powers the
  // "spend reconciliation" panel below the usage chart. Period clamped 1..90.
  usageReconciliation: (periodDays = 14) =>
    api.get('/billing/usage-reconciliation', {
      params: { period_days: periodDays },
    }),
  // Admin-only: claude_call_log summary — total spend, per-feature
  // breakdown, attribution gap (calls with no UsageEvent), failure rate.
  // Ground truth from the unconditional call log. Period clamped 1..90.
  meteringGap: (periodDays = 7) =>
    api.get('/billing/admin/metering-gap', {
      params: { days: periodDays },
    }),
  // Replaces createCheckoutSession (Lemon). Returns { url } from Stripe.
  topup: (data) => api.post('/billing/topup', data),
  // Stripe Billing Portal session for the org's customer. Returns { url }.
  // 409 = no Stripe customer yet (nothing purchased) — the UI hides the link.
  portalSession: (returnUrl) => api.post('/billing/portal', { return_url: returnUrl }),
};
