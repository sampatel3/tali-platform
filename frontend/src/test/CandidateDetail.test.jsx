import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../context/AuthContext';
import { ToastProvider } from '../context/ToastContext';
import { CandidateDetailPage } from '../features/candidates/CandidateDetailPage';
import { assessments as assessmentsApi } from '../shared/api';

vi.mock('../shared/api', () => ({
  auth: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    forgotPassword: vi.fn(),
    resetPassword: vi.fn(),
    ssoCheck: vi.fn(),
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
    updateManualEvaluation: vi.fn(),
    generateInterviewDebrief: vi.fn(),
    aiEvalSuggestions: vi.fn(),
  },
  billing: { usage: vi.fn(), costs: vi.fn(), credits: vi.fn(), createCheckoutSession: vi.fn() },
  organizations: { get: vi.fn() },
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
    list: vi.fn(),
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
    getApplication: vi.fn(),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    listApplicationEvents: vi.fn().mockResolvedValue({ data: [] }),
    updateApplicationStage: vi.fn(),
    downloadApplicationReport: vi.fn(),
  },
  team: { list: vi.fn(), invite: vi.fn() },
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
  RadarChart: ({ children }) => <div data-testid="radar-chart">{children}</div>,
  PolarGrid: () => <div />,
  PolarAngleAxis: () => <div />,
  PolarRadiusAxis: () => <div />,
  Radar: () => <div />,
  CartesianGrid: () => <div />,
  LineChart: ({ children }) => <div>{children}</div>,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
}));

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_name: 'Taali',
  role: 'admin',
};

const candidate = {
  id: 42,
  name: 'Alice Johnson',
  email: 'alice@example.com',
  task: 'Async Pipeline Debugging',
  status: 'completed',
  score: 8.5,
  time: '45m',
  position: 'Senior Engineer',
  completedDate: '3/5/2026',
  promptsList: [
    { message: 'How should I verify the pipeline fix?', timestamp: '2026-03-05T09:10:00Z' },
  ],
  timeline: [
    { timestamp: '2026-03-05T09:00:00Z', event: 'Assessment started' },
    { timestamp: '2026-03-05T09:10:00Z', event: 'First prompt' },
  ],
  _raw: {
    id: 42,
    candidate_name: 'Alice Johnson',
    candidate_email: 'alice@example.com',
    role_name: 'Backend Engineer',
    task_name: 'Async Pipeline Debugging',
    status: 'completed',
    final_score: 85,
    application_status: 'review',
    total_duration_seconds: 2700,
    total_prompts: 3,
    tests_passed: 8,
    tests_total: 10,
    browser_focus_ratio: 0.95,
    prompt_quality_score: 7.8,
    error_recovery_score: 8.4,
    independence_score: 7.5,
    context_utilization_score: 7.1,
    design_thinking_score: 8.2,
    started_at: '2026-03-05T09:00:00Z',
    completed_at: '2026-03-05T09:45:00Z',
    timeline: [
      { timestamp: '2026-03-05T09:00:00Z', event: 'Assessment started' },
      { timestamp: '2026-03-05T09:10:00Z', event: 'First prompt', detail: 'How should I verify the pipeline fix?' },
    ],
    prompts_list: [
      { message: 'How should I verify the pipeline fix?', timestamp: '2026-03-05T09:10:00Z' },
    ],
    score_breakdown: {
      heuristic_summary: 'Clear validation loop and strong recovery behavior under pressure.',
    },
  },
};

const renderPage = (props = {}) => render(
  <AuthProvider>
    <ToastProvider>
      <CandidateDetailPage
        candidate={candidate}
        onNavigate={vi.fn()}
        onDeleted={vi.fn()}
        onNoteAdded={vi.fn()}
        {...props}
      />
    </ToastProvider>
  </AuthProvider>,
);

describe('Candidate detail redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('taali_user', JSON.stringify(mockUser));
    assessmentsApi.addNote.mockResolvedValue({ data: { timeline: [] } });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('renders the redesigned summary tab with candidate context and score recommendation', async () => {
    renderPage();

    expect(screen.getByRole('heading', { level: 1, name: /Alice/i })).toBeInTheDocument();
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();
    expect(screen.getByText('Composite')).toBeInTheDocument();
    expect(screen.getByText('Recommendation')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: /One-line/i })).toBeInTheDocument();
  });

  it('renders the redesigned assessment tab with dimensions, evidence, and radar chart', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: 'Assessment' }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: /Scored/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 2, name: /Live/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 2, name: /AI-collaboration/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 2, name: /Session/i })).toBeInTheDocument();
      expect(screen.getByTestId('radar-chart')).toBeInTheDocument();
    });
  });

  it('posts recruiter notes from the redesigned summary workflow', async () => {
    const onNoteAdded = vi.fn();
    renderPage({ onNoteAdded });

    fireEvent.change(screen.getByPlaceholderText('Add a recruiter note for the next reviewer'), {
      target: { value: 'Flag for final panel review.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Post note' }));

    await waitFor(() => {
      expect(assessmentsApi.addNote).toHaveBeenCalledWith(42, 'Flag for final panel review.');
      expect(onNoteAdded).toHaveBeenCalled();
    });
  });
});
