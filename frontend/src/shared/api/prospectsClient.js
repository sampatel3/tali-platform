import api from './httpClient';

// Sourced-prospect CRUD + CSV import — the outreach foundations surface.
// Org-scoped via the recruiter session. `list` returns each prospect with a
// `suppressed` flag (reason | null) so the UI can badge un-mailable rows.
export const prospects = {
  list: (params = {}) => api.get('/prospects', { params }),
  create: (data) => api.post('/prospects', data),
  update: (id, data) => api.patch(`/prospects/${id}`, data),
  // Soft-delete → status=archived.
  archive: (id) => api.delete(`/prospects/${id}`),
  importCsv: (file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post('/prospects/import', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
};
