import api from './httpClient';

export const candidates = {
  list: (params = {}) => api.get('/candidates/', { params }),
  get: (id) => api.get(`/candidates/${id}`),
  create: (data) => api.post('/candidates/', data),
  createWithCv: ({ email, full_name, position, file }) => {
    const form = new FormData();
    form.append('email', email);
    if (full_name) form.append('full_name', full_name);
    if (position) form.append('position', position);
    form.append('file', file);
    return api.post('/candidates/with-cv', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  update: (id, data) => api.patch(`/candidates/${id}`, data),
  remove: (id) => api.delete(`/candidates/${id}`),
  uploadCv: (candidateId, file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/candidates/${candidateId}/upload-cv`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  uploadJobSpec: (candidateId, file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post(`/candidates/${candidateId}/upload-job-spec`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
  downloadDocument: (candidateId, docType) =>
    api.get(`/candidates/${candidateId}/documents/${docType}`, { responseType: 'blob' }),
};
