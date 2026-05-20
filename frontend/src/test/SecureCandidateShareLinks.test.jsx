import { render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

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

import { auth, viewShareLink } from '../shared/api';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const sharedApplication = {
  id: 12,
  candidate_id: 212,
  candidate_email: 'candidate@example.com',
  candidate_name: 'Shared Candidate',
  candidate_position: 'Platform Engineer',
  role_name: 'Platform Engineer',
  pipeline_stage: 'review',
  application_outcome: 'open',
  status: 'applied',
  cv_filename: 'shared.pdf',
  cv_match_score: 81,
  cv_match_details: {
    score_scale: '0-100',
    summary: 'Strong enough CV evidence to review before sending an assessment.',
    requirements_match_score_100: 74,
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

describe('SecureCandidateShareLinks', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.history.replaceState(null, '', '/');
    auth.me.mockRejectedValue(new Error('Not authenticated'));
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
    localStorage.clear();
  });

  it('renders the unauthenticated /share/:token route via viewShareLink', async () => {
    // HANDOFF v2 §3 — recipients land on /share/:token. The page calls
    // the public unauth endpoint, gets the application payload plus the
    // view mode in one round-trip, and renders the standing report
    // without requiring a recruiter session.
    viewShareLink.mockResolvedValue({
      data: {
        application_id: 12,
        mode: 'recruiter',
        view: 'recruiter',
        expires_at: '2026-05-27T08:20:31.421293+00:00',
        application: sharedApplication,
      },
    });

    renderAppAt('/share/shr_candidate_report_12');

    await waitFor(() => {
      expect(viewShareLink).toHaveBeenCalledWith('shr_candidate_report_12');
      expect(window.location.pathname).toBe('/share/shr_candidate_report_12');
    });
  });

  it('switches to client-scrubbed view when the link mode is client', async () => {
    viewShareLink.mockResolvedValue({
      data: {
        application_id: 12,
        mode: 'client',
        view: 'client',
        expires_at: '2026-05-27T08:20:31.421293+00:00',
        application: {
          ...sharedApplication,
          client_share_summary: {
            verdict: 'Strong fit for the platform-engineering role',
            why_now: 'Direct experience with the JD requirements.',
          },
        },
      },
    });

    renderAppAt('/share/shr_client_view_xyz');

    await waitFor(() => {
      expect(viewShareLink).toHaveBeenCalledWith('shr_client_view_xyz');
      // Client-mode-only block from the candidate report renders only
      // when the view mode is "client".
      expect(screen.getByText(/Why we['’]re sharing this candidate/i)).toBeInTheDocument();
    });
  });
});
