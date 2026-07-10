// Single source of truth for the public Developer Portal (/developers).
// Adding a public endpoint = one entry here; the portal renders from this.
// When a custom API domain is set up, change API_BASE in this one place.

export const API_BASE = 'https://api.taali.ai/public/v1';

export const SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'authentication', label: 'Authentication' },
  { id: 'base-url', label: 'Base URL' },
  { id: 'endpoints', label: 'Endpoints' },
  { id: 'errors', label: 'Errors & status codes' },
  { id: 'webhooks', label: 'Webhooks' },
  { id: 'workable', label: 'Workable' },
  { id: 'changelog', label: 'Changelog' },
];

export const SCOPES = [
  { id: 'roles:read', desc: 'Read roles and the assessment catalog.' },
  { id: 'applications:read', desc: 'Read candidate applications: stage, scores, recommendation.' },
  { id: 'assessments:read', desc: 'Read assessment status and results.' },
  { id: 'assessments:write', desc: 'Create assessments.' },
  { id: 'share-links:write', desc: 'Mint shareable report links.' },
];

export const ENDPOINT_GROUPS = [
  {
    name: 'Catalog',
    endpoints: [
      { method: 'GET', path: '/tests', scope: 'roles:read', desc: 'List your available assessment tasks.' },
    ],
  },
  {
    name: 'Roles',
    endpoints: [
      { method: 'GET', path: '/roles', scope: 'roles:read', desc: 'List roles. Paginate with limit + offset.' },
      { method: 'GET', path: '/roles/{id}', scope: 'roles:read', desc: 'Fetch one role with its linked assessment tasks.' },
    ],
  },
  {
    name: 'Applications & assessments',
    endpoints: [
      { method: 'GET', path: '/applications/{id}', scope: 'applications:read', desc: 'A candidate application: stage, scores, live recommendation, CV-fit.' },
      { method: 'GET', path: '/assessments/{id}', scope: 'assessments:read', desc: "An assessment's status, scores, and timestamps." },
      { method: 'POST', path: '/applications/{id}/share-links', scope: 'share-links:write', desc: 'Mint a shareable report link (a results_url).' },
    ],
  },
  {
    name: 'Job applications & metrics',
    endpoints: [
      { method: 'GET', path: '/roles/{id}/applications', scope: 'applications:read', desc: "A role's candidate applications — Taali's signal plus the synced Workable stage. Filter by workable_stage or pipeline_stage." },
      { method: 'GET', path: '/roles/{id}/metrics', scope: 'applications:read', desc: 'Job metrics: totals, the Taali funnel, decision outcomes, and Workable-stage counts.' },
    ],
  },
];

export const ERRORS = [
  { code: '200', meaning: 'Success.' },
  { code: '401', meaning: 'Missing, malformed, invalid, revoked, or expired key. The response body’s "detail" says which.' },
  { code: '403', meaning: 'The key lacks a required scope.' },
  { code: '404', meaning: 'Not found — or not in your organization (every key is tenant-isolated).' },
  { code: '429', meaning: 'Rate limited — retry after the window.' },
];

// Newest first. Add an entry whenever the public surface changes.
export const CHANGELOG = [
  {
    date: '2026-06-09',
    items: [
      'Job metrics + applications list: GET /roles/{id}/applications and /roles/{id}/metrics.',
      'Applications now expose the synced Workable hiring-stage (workable_stage).',
      'v1 launch: API keys (Settings → Developers) + the curated /public/v1 surface.',
      'Read endpoints: tests, roles, applications, assessments.',
      'Write endpoint: share-links (mint a results_url).',
      'Workable Assessments-Provider add-on available on request.',
    ],
  },
];
