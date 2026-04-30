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
  // Replaces createCheckoutSession (Lemon). Returns { url } from Stripe.
  topup: (data) => api.post('/billing/topup', data),
};
