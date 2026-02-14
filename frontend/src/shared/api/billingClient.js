import api from './httpClient';

export const billing = {
  usage: () => api.get('/billing/usage'),
  costs: () => api.get('/billing/costs'),
  createCheckoutSession: (data) => api.post('/billing/checkout-session', data),
};
