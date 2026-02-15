import api from './httpClient';

export const billing = {
  usage: () => api.get('/billing/usage'),
  costs: () => api.get('/billing/costs'),
  credits: () => api.get('/billing/credits'),
  createCheckoutSession: (data) => api.post('/billing/checkout-session', data),
};
