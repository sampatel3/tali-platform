import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

vi.mock('../shared/api', () => ({
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
    getApplicationByShareToken: vi.fn(),
    getApplicationShareLink: vi.fn().mockResolvedValue({
      data: {
        application_id: 12,
        share_token: 'shr_candidate_report_12',
        share_url: 'https://www.taali.ai/c/12?view=interview&k=shr_candidate_report_12',
        created_at: '2026-01-16T10:00:00Z',
        member_access_only: false,
      },
    }),
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
  email: 'member@taali.ai',
  full_name: 'Member User',
  organization_id: 1,
  role: 'admin',
};

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
    rolesApi.getApplicationByShareToken.mockResolvedValue({ data: sharedApplication });
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
    localStorage.clear();
  });

  it('opens unauthenticated interviewer report links with token access', async () => {
    renderAppAt('/c/12?view=interview&k=shr_candidate_report_12');

    await waitFor(() => {
      expect(window.location.pathname).toBe('/c/12');
      expect(rolesApi.getApplicationByShareToken).toHaveBeenCalledWith('shr_candidate_report_12');
      expect(screen.getByText(/Interview view/i)).toBeInTheDocument();
    });
  });

  it('returns members to legacy shared report links after sign-in', async () => {
    auth.login.mockResolvedValue({ data: { access_token: 'tok123' } });
    auth.me.mockResolvedValue({ data: mockUser });

    renderAppAt('/login?next=%2Fcandidates%2Fshr_candidate_report_12');

    await waitFor(() => {
      expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'member@taali.ai' },
    });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), {
      target: { value: 'password123' },
    });
    const primarySignInButton = screen.getAllByRole('button').find((button) => (
      /sign in/i.test(button.textContent || '')
      && (button.textContent || '').includes('→')
    ));
    expect(primarySignInButton).toBeTruthy();
    fireEvent.click(primarySignInButton);

    await waitFor(() => {
      expect(window.location.pathname).toBe('/candidates/shr_candidate_report_12');
      expect(rolesApi.getApplicationByShareToken).toHaveBeenCalledWith('shr_candidate_report_12');
      expect(screen.getByText('Candidate standing report')).toBeInTheDocument();
    });
  });
});
