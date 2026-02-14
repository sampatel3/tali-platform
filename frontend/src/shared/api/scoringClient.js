import api from './httpClient';

export const scoring = {
  metadata: () => api.get('/scoring/metadata'),
};
