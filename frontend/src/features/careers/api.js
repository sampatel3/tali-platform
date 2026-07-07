// Public careers API — no auth. These endpoints live at the app root under
// /careers/v1 (NOT the /api/v1 recruiter surface), so we use raw axios against
// API_URL rather than the authed httpClient instance.
import axios from 'axios';

import { API_URL } from '../../shared/api/httpClient';

const base = (orgSlug) => `${API_URL}/careers/v1/${encodeURIComponent(orgSlug)}`;

export const careersApi = {
  listJobs: (orgSlug) =>
    axios.get(`${base(orgSlug)}/jobs`).then((r) => r.data),

  getJob: (orgSlug, roleSlug) =>
    axios.get(`${base(orgSlug)}/jobs/${encodeURIComponent(roleSlug)}`).then((r) => r.data),

  apply: (orgSlug, roleSlug, payload) =>
    axios
      .post(`${base(orgSlug)}/jobs/${encodeURIComponent(roleSlug)}/apply`, payload)
      .then((r) => r.data),

  submitEeo: (orgSlug, applicationId, payload) =>
    axios.post(
      `${base(orgSlug)}/applications/${encodeURIComponent(applicationId)}/eeo`,
      payload,
    ),
};
