import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import { CandidateDetailPage } from './features/candidates/CandidateDetailPage';

vi.mock('./shared/api', () => ({
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
    addNote: vi.fn(),
    downloadReport: vi.fn(),
    postToWorkable: vi.fn(),
    remove: vi.fn(),
    resend: vi.fn(),
    updateManualEvaluation: vi.fn(),
    generateInterviewDebrief: vi.fn(),
    aiEvalSuggestions: vi.fn(),
  },
  organizations: { get: vi.fn() },
  analytics: { get: vi.fn().mockResolvedValue({ data: {} }) },
  billing: {},
  team: {},
  tasks: {},
  candidates: { downloadDocument: vi.fn() },
  roles: {
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    listApplicationEvents: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn(),
    getApplication: vi.fn(),
    updateApplicationStage: vi.fn(),
    downloadApplicationReport: vi.fn(),
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

const candidate = {
  id: 1,
  name: 'Jane Doe',
  email: 'jane@example.com',
  position: 'Engineer',
  task: 'Debugging',
  time: '30m',
  score: 8.4,
  completedDate: 'Today',
  promptsList: [{ message: 'help' }],
  timeline: [{ timestamp: '2026-03-05T09:00:00Z', event: 'Started' }],
  _raw: {
    id: 1,
    candidate_name: 'Jane Doe',
    candidate_email: 'jane@example.com',
    role_name: 'Engineer',
    task_name: 'Debugging',
    status: 'completed',
    final_score: 84,
    prompt_quality_score: 8.0,
    error_recovery_score: 6.5,
    independence_score: 6.0,
    context_utilization_score: 7.0,
    design_thinking_score: 6.5,
    time_to_first_prompt_seconds: 240,
    started_at: '2026-03-05T09:00:00Z',
    completed_at: '2026-03-05T09:30:00Z',
    prompts_list: [{ message: 'help', timestamp: '2026-03-05T09:04:00Z' }],
    timeline: [{ timestamp: '2026-03-05T09:04:00Z', event: 'First prompt' }],
  },
};

describe('Candidate detail radar redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('taali_user', JSON.stringify({
      id: 1,
      email: 'admin@taali.ai',
      full_name: 'Admin User',
      organization_name: 'Taali',
    }));
  });

  it('renders the six builder-faithful scoring dimensions on the assessment tab', async () => {
    render(
      <AuthProvider>
        <ToastProvider>
          <CandidateDetailPage
            candidate={candidate}
            onNavigate={vi.fn()}
            onDeleted={vi.fn()}
          />
        </ToastProvider>
      </AuthProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Assessment' }));

    await waitFor(() => {
      expect(screen.getByText('Prompt quality')).toBeInTheDocument();
      expect(screen.getByText('Error recovery')).toBeInTheDocument();
      expect(screen.getByText('Independence')).toBeInTheDocument();
      expect(screen.getByText('Context utilization')).toBeInTheDocument();
      expect(screen.getByText('Design thinking')).toBeInTheDocument();
      expect(screen.getByText('Time to first prompt')).toBeInTheDocument();
      expect(screen.getByTestId('radar-chart')).toBeInTheDocument();
    });
  });
});
