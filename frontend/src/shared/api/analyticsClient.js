import api from './httpClient';

export const analytics = {
  get: () => api.get('/analytics/'),
};
