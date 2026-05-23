import { render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// Mirrors the api surface the standing report touches. Kept in sync with
// SecureCandidateShareLinks.test.jsx — the report renders the same way
// whether reached via /share/:token or the authenticated /candidates/:id.
vi.mock('../shared/api', () => ({
  viewShareLink: vi.fn(),
  auth: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    ssoCheck: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    forgotPassword: vi.fn(),
    resetPassword: vi.fn(),
  },
  assessments: {
    list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    get: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    resend: vi.fn(),
    downloadReport: vi.fn(),
    addNote: vi.fn(),
    uploadCv: vi.fn(),
    postToWorkable: vi.fn(),
  },
  billing: { usage: vi.fn(), costs: vi.fn(), credits: vi.fn(), createCheckoutSession: vi.fn() },
  organizations: {
    get: vi.fn().mockResolvedValue({ data: { workable_connected: false } }),
    update: vi.fn(),
  },
  analytics: { get: vi.fn().mockResolvedValue({ data: {} }) },
  tasks: {
    list: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    generate: vi.fn(),
  },
  candidates: {
    list: vi.fn().mockResolvedValue({ data: { items: [] } }),
    get: vi.fn(),
    create: vi.fn(),
    createWithCv: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    uploadCv: vi.fn(),
    uploadJobSpec: vi.fn(),
    downloadDocument: vi.fn(),
  },
  roles: {
    list: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn(),
    update: vi.fn(),
    listApplicationsGlobal: vi.fn().mockResolvedValue({ data: { items: [], total: 0, limit: 50, offset: 0 } }),
    listPipeline: vi.fn().mockResolvedValue({
      data: {
        role_id: 0,
        role_name: '',
        stage: 'all',
        stage_counts: { applied: 0, invited: 0, in_assessment: 0, review: 0 },
        active_candidates_count: 0,
        items: [],
        total: 0,
        limit: 50,
        offset: 0,
      },
    }),
    listApplications: vi.fn().mockResolvedValue({ data: [] }),
    getApplication: vi.fn(),
    listApplicationShareLinks: vi.fn().mockResolvedValue({ data: { links: [] } }),
    createApplicationShareLink: vi.fn(),
    revokeShareLink: vi.fn(),
    listApplicationEvents: vi.fn().mockResolvedValue({ data: [] }),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    batchScoreStatus: vi.fn(),
    fetchCvsStatus: vi.fn(),
    batchScore: vi.fn(),
    fetchCvs: vi.fn(),
    regenerateInterviewFocus: vi.fn(),
    generateApplicationInterviewDebrief: vi.fn(),
    downloadApplicationReport: vi.fn(),
  },
  team: { list: vi.fn().mockResolvedValue({ data: [] }), invite: vi.fn() },
  default: {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn(),
    post: vi.fn(),
    create: vi.fn().mockReturnValue({
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    }),
  },
}));

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  RadarChart: () => <div data-testid="radar-chart" />,
  PolarGrid: () => <div />,
  PolarAngleAxis: () => <div />,
  PolarRadiusAxis: () => <div />,
  Radar: () => <div />,
  LineChart: () => <div data-testid="line-chart" />,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  CartesianGrid: () => <div />,
  Tooltip: () => <div />,
}));

vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="code-editor" />,
}));

import { auth, roles as rolesApi } from '../shared/api';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const roleBearingApplication = {
  id: 77,
  candidate_id: 207,
  candidate_email: 'rami@example.com',
  candidate_name: 'Rami Reddy',
  candidate_position: 'AI Engineer',
  role_id: 31,
  role_name: 'AI Engineer',
  pipeline_stage: 'advanced',
  application_outcome: 'open',
  status: 'applied',
  cv_filename: 'rami.pdf',
  cv_match_score: 68,
  cv_match_details: {
    score_scale: '0-100',
    summary: 'Strong enough CV evidence to review before sending an assessment.',
    requirements_match_score_100: 68,
  },
  assessment_history: [],
  created_at: '2026-01-10T10:00:00Z',
  updated_at: '2026-01-10T10:00:00Z',
};

const renderAppAt = (path) => {
  window.history.replaceState(null, '', path);
  return render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );
};

describe('Candidate report back link', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('taali_access_token', 'fake-jwt-token');
    localStorage.setItem('taali_user', JSON.stringify(mockUser));
    auth.me.mockResolvedValue({ data: mockUser });
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
    localStorage.clear();
  });

  it('falls back to the candidate role when ?from is absent (job-opened report)', async () => {
    // Reaching the report from a job board without the ?from tag must not
    // strand the recruiter on "Back to home" — the candidate belongs to a
    // role, so the back link offers that role.
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });

    renderAppAt('/candidates/77');

    expect(
      await screen.findByRole('button', { name: /Back to job/i }, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Back to home/i })).not.toBeInTheDocument();
  });

  it('still honours an explicit ?from=home (Hub-opened report)', async () => {
    rolesApi.getApplication.mockResolvedValue({ data: roleBearingApplication });

    renderAppAt('/candidates/77?from=home');

    expect(
      await screen.findByRole('button', { name: /Back to home/i }, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Back to job/i })).not.toBeInTheDocument();
  });
});
