import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// Mock the API module
vi.mock('../shared/api', () => ({
  auth: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
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
    generateInterviewDebrief: vi.fn(),
    uploadCv: vi.fn(),
    postToWorkable: vi.fn(),
    updateManualEvaluation: vi.fn(),
  },
  billing: { usage: vi.fn(), costs: vi.fn(), credits: vi.fn(), createCheckoutSession: vi.fn() },
  organizations: { get: vi.fn(), update: vi.fn() },
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

// Mock recharts
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

// Mock monaco editor
vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="code-editor" />,
}));

import { auth, assessments as assessmentsApi, analytics as analyticsApi, candidates as candidatesApi } from '../shared/api';
import { CandidateDetailPage } from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const setupAuthenticatedUser = () => {
  localStorage.setItem('taali_access_token', 'fake-jwt-token');
  localStorage.setItem('taali_user', JSON.stringify(mockUser));
  auth.me.mockResolvedValue({ data: mockUser });
};

const mockCandidate = {
  id: 1,
  name: 'Alice Johnson',
  email: 'alice@example.com',
  task: 'Async Pipeline Debugging',
  status: 'completed',
  score: 8.5,
  time: '45m',
  position: 'Senior Engineer',
  completedDate: '1/15/2026',
  prompts: 5,
  promptsList: [
    { message: 'How do I fix the race condition in the pipeline?', timestamp: '2026-01-15T09:15:00Z' },
    { message: 'Can you explain the async iterator pattern?', timestamp: '2026-01-15T09:25:00Z' },
    { message: 'The test for batch processing is failing with a timeout', timestamp: '2026-01-15T09:35:00Z' },
  ],
  timeline: [
    { time: '09:00', event: 'Assessment started' },
    { time: '09:15', event: 'First prompt sent', prompt: 'How do I fix the race condition in the pipeline?' },
    { time: '09:45', event: 'Assessment submitted' },
  ],
  results: [
    { title: 'Pipeline Processing', score: '9/10', description: 'Correctly handled async events.' },
    { title: 'Error Handling', score: '8/10', description: 'Good error boundaries.' },
  ],
  breakdown: {
    categoryScores: {
      task_completion: 9,
      prompt_clarity: 8,
      context_provision: 7,
      independence: 8,
      utilization: 7,
      communication: 9,
      approach: 8,
      cv_match: 6,
    },
    testsPassed: '8/10',
    codeQuality: 8,
    timeEfficiency: 7,
    aiUsage: 8,
  },
  _raw: {
    id: 1,
    taali_score: 85,
    assessment_score: 85,
    final_score: 85,
    status: 'completed',
    role_name: 'Backend Engineer',
    application_status: 'applied',
    total_duration_seconds: 2700,
    total_prompts: 5,
    total_input_tokens: 5000,
    total_output_tokens: 8000,
    tests_passed: 8,
    tests_total: 10,
    started_at: '2026-01-15T09:00:00Z',
    completed_at: '2026-01-15T09:45:00Z',
    prompt_quality_score: 7.8,
    time_to_first_prompt_seconds: 900,
    browser_focus_ratio: 0.95,
    tab_switch_count: 2,
    calibration_score: 7.5,
    cv_uploaded: true,
    cv_filename: 'alice_cv.pdf',
    prompt_fraud_flags: [],
    cv_job_match_score: 74.2,
    cv_job_match_details: {
      score_scale: '0-100',
      matching_skills: ['Python', 'AsyncIO', 'Testing'],
      missing_skills: ['Kubernetes', 'Terraform'],
      experience_highlights: ['5 years of backend development', 'Led team of 4 engineers'],
      concerns: ['No cloud infrastructure experience'],
      score_rationale_bullets: [
        'Composite fit 74.2/100 from skills 78.8/100, experience 71.5/100, recruiter requirements 69.0/100.',
        'Recruiter requirements coverage: 2/3 met, 1 partial, 0 missing.',
      ],
      summary: 'Strong technical skills with gaps in DevOps tooling.',
      requirements_match_score_100: 69.0,
      requirements_coverage: {
        total: 3,
        met: 2,
        partially_met: 1,
        missing: 0,
      },
      requirements_assessment: [
        {
          requirement: 'Async backend systems experience',
          priority: 'must_have',
          status: 'met',
          evidence: 'Candidate has shipped Python and AsyncIO backend services in production.',
        },
        {
          requirement: 'Infrastructure automation exposure',
          priority: 'must_have',
          status: 'partially_met',
          evidence: 'Testing depth is clear, but Kubernetes and Terraform coverage is still limited.',
        },
        {
          requirement: 'Hands-on debugging ownership',
          priority: 'nice_to_have',
          status: 'met',
          evidence: 'Timeline and prompt evidence show direct debugging and test iteration ownership.',
        },
      ],
      role_fit_score_100: 71.1,
    },
    score_breakdown: {
      score_components: {
        taali_score: 85,
        assessment_score: 85,
        cv_fit_score: 74.2,
        requirements_fit_score: 69.0,
        role_fit_score: 71.1,
      },
    },
    evaluation_rubric: {
      correctness: { weight: 0.6 },
      code_quality: { weight: 0.4 },
    },
    prompt_analytics: {
      detailed_scores: {
        task_completion: { tests_passed_ratio: 8, time_compliance: 9 },
        prompt_clarity: { prompt_length_quality: 8, question_clarity: 7 },
      },
      explanations: {
        task_completion: { tests_passed_ratio: 'Passed 8 out of 10 tests.' },
      },
      per_prompt_scores: [
        { clarity: 8, specificity: 7, efficiency: 8, word_count: 12, has_context: true, is_vague: false },
        { clarity: 7, specificity: 8, efficiency: 7, word_count: 8, has_context: false, is_vague: false },
        { clarity: 9, specificity: 8, efficiency: 9, word_count: 15, has_context: true, is_vague: false },
      ],
      cv_job_match: {
        overall: 74.2,
        skills: 78.8,
        experience: 71.5,
        details: {
          matching_skills: ['Python', 'AsyncIO', 'Testing'],
          missing_skills: ['Kubernetes', 'Terraform'],
          experience_highlights: ['5 years of backend development', 'Led team of 4 engineers'],
          concerns: ['No cloud infrastructure experience'],
          score_rationale_bullets: [
            'Composite fit 74.2/100 from skills 78.8/100, experience 71.5/100, recruiter requirements 69.0/100.',
            'Recruiter requirements coverage: 2/3 met, 1 partial, 0 missing.',
          ],
          summary: 'Strong technical skills with gaps in DevOps tooling.',
        },
      },
    },
  },
};

const mockOnNavigate = vi.fn();
const mockOnDeleted = vi.fn();
const mockOnNoteAdded = vi.fn();

const renderCandidateDetail = async (candidateOverrides = {}) => {
  const candidate = { ...mockCandidate, ...candidateOverrides };
  const view = render(
    <AuthProvider>
      <CandidateDetailPage
        candidate={candidate}
        onNavigate={mockOnNavigate}
        onDeleted={mockOnDeleted}
        onNoteAdded={mockOnNoteAdded}
      />
    </AuthProvider>
  );
  await waitFor(() => expect(analyticsApi.get).toHaveBeenCalled());
  return view;
};

describe('CandidateDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.location.hash = '';
    setupAuthenticatedUser();
    analyticsApi.get.mockResolvedValue({ data: { avg_calibration_score: 6.5 } });
    assessmentsApi.generateInterviewDebrief.mockResolvedValue({
      data: {
        cached: true,
        generated_at: '2026-01-15T10:00:00Z',
        interview_debrief: {
          summary: 'Probe async debugging depth and cloud infrastructure gaps.',
          probing_questions: [
            {
              dimension: 'Context provision',
              score: 7.0,
              question: 'How would you triage an intermittent batch timeout in production?',
              what_to_listen_for: 'Concrete debugging steps, file references, and dependency awareness.',
            },
          ],
          strengths_to_validate: [{ text: 'Async backend ownership' }],
          red_flags: [{ text: 'Limited cloud automation depth', follow_up_question: 'Where have you used Terraform or Kubernetes directly?' }],
        },
      },
    });
  });

  afterEach(() => {
    window.location.hash = '';
    localStorage.clear();
  });

  it('renders candidate name and email', async () => {
    await renderCandidateDetail();
    expect(screen.getAllByText('Alice Johnson').length).toBeGreaterThan(0);
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();
  });

  it('renders score badge with recommendation', async () => {
    await renderCandidateDetail();
    // TAALI score is 85 => Strong Hire
    expect(screen.getAllByText('85.0').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Strong Hire').length).toBeGreaterThanOrEqual(1);
  });

  it('renders position and task info', async () => {
    await renderCandidateDetail();
    expect(screen.getByText('Position: Senior Engineer')).toBeInTheDocument();
    expect(screen.getByText('Task: Async Pipeline Debugging')).toBeInTheDocument();
  });

  it('renders role and application context badges', async () => {
    await renderCandidateDetail();
    expect(screen.getByText('Role: Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText('Application: applied')).toBeInTheDocument();
  });

  it('renders duration info', async () => {
    await renderCandidateDetail();
    expect(screen.getByText('Duration: 45m')).toBeInTheDocument();
  });

  it('renders summary tab by default', async () => {
    await renderCandidateDetail();
    expect(screen.getByRole('tab', { name: 'SUMMARY' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Assessment results')).toBeInTheDocument();
  });

  it('renders category scores in results tab', async () => {
    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));
    // Category names appear in the expandable sections
    const taskCompletionElements = screen.getAllByText('Task completion');
    expect(taskCompletionElements.length).toBeGreaterThanOrEqual(1);
    const promptClarityElements = screen.getAllByText('Prompt clarity');
    expect(promptClarityElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Independence & efficiency').length).toBeGreaterThanOrEqual(1);
  });

  it('renders radar chart in results tab', async () => {
    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));
    expect(screen.getByTestId('radar-chart')).toBeInTheDocument();
  });

  it('assessment results tab includes AI usage and prompt quality', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));

    await waitFor(() => {
      expect(screen.getByText('Avg Prompt clarity')).toBeInTheDocument();
      expect(screen.getByText('Time to First Prompt')).toBeInTheDocument();
      expect(screen.getByText('Browser Focus')).toBeInTheDocument();
    });
  });

  it('shows prompt log in assessment results tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));

    await waitFor(() => {
      expect(screen.getByText(/Prompt Log/)).toBeInTheDocument();
      expect(screen.getByText(/How do I fix the race condition/)).toBeInTheDocument();
    });
  });

  it('tab switching works - role fit tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByRole('tab', { name: 'ROLE FIT' }));

    await waitFor(() => {
      expect(screen.getAllByText('Summary').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('CV fit').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Requirements fit').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Why this score').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('shows matching skills in role fit tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByRole('tab', { name: 'ROLE FIT' }));

    await waitFor(() => {
      expect(screen.getAllByText('Matching skills').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Python').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('AsyncIO').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('shows missing skills in role fit tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByRole('tab', { name: 'ROLE FIT' }));

    await waitFor(() => {
      expect(screen.getAllByText('Gaps').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Kubernetes').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Terraform').length).toBeGreaterThanOrEqual(1);
    });
  });

  it('shows score rationale bullets in role fit tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByRole('tab', { name: 'ROLE FIT' }));

    await waitFor(() => {
      expect(screen.getAllByText('Summary').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Why this score').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/Composite fit 74\.2/).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/Recruiter requirements coverage: 2\/3 met/).length).toBeGreaterThanOrEqual(1);
    });
  });

  it('assessment results tab includes timeline evidence', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));

    await waitFor(() => {
      expect(screen.getByText('Assessment started')).toBeInTheDocument();
      expect(screen.getByText('First prompt sent')).toBeInTheDocument();
      expect(screen.getByText('Submitted')).toBeInTheDocument();
    });
  });

  it('interview guidance tab renders recruiter feedback input and save button', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByRole('tab', { name: 'INTERVIEW GUIDANCE' }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Add recruiter feedback from the interview')).toBeInTheDocument();
      expect(screen.getByText('Save feedback')).toBeInTheDocument();
    });
  });

  it('interview guidance tab saves recruiter feedback notes', async () => {
    assessmentsApi.addNote.mockResolvedValue({ data: { timeline: [] } });

    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'INTERVIEW GUIDANCE' }));

    const noteInput = await screen.findByPlaceholderText('Add recruiter feedback from the interview');
    fireEvent.change(noteInput, { target: { value: 'Great candidate, recommend for next round' } });

    fireEvent.click(screen.getByText('Save feedback'));

    await waitFor(() => {
      expect(assessmentsApi.addNote).toHaveBeenCalledWith(1, 'Great candidate, recommend for next round');
    });
  });

  it('interview guidance tab surfaces load errors and stops loading state', async () => {
    assessmentsApi.generateInterviewDebrief.mockRejectedValueOnce({
      response: {
        data: {
          detail: 'Interview guidance is temporarily unavailable.',
        },
      },
    });

    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'INTERVIEW GUIDANCE' }));

    await waitFor(() => {
      expect(assessmentsApi.generateInterviewDebrief).toHaveBeenCalledWith(1, { force_regenerate: false });
    });

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Loading guidance...' })).not.toBeInTheDocument();
      expect(screen.queryByText('Generating interview guide...')).not.toBeInTheDocument();
    });
  });

  it('Download client report button exists', async () => {
    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'CLIENT REPORT' }));
    expect(screen.getByText('Download client report')).toBeInTheDocument();
  });

  it('Download client report calls downloadReport API', async () => {
    assessmentsApi.downloadReport.mockResolvedValue({ data: new Blob(['pdf-content']) });

    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'CLIENT REPORT' }));

    fireEvent.click(screen.getByText('Download client report'));

    await waitFor(() => {
      expect(assessmentsApi.downloadReport).toHaveBeenCalledWith(1);
    });
  });

  it('Delete button exists and calls remove API after confirm', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true);
    assessmentsApi.remove.mockResolvedValue({ data: {} });

    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'CLIENT REPORT' }));

    const deleteButton = screen.getByRole('button', { name: 'Delete assessment' });
    fireEvent.click(deleteButton);

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith('Delete this assessment? This cannot be undone.');
      expect(assessmentsApi.remove).toHaveBeenCalledWith(1);
    });

    confirmMock.mockRestore();
  });

  it('Back to Assessments button calls onNavigate', async () => {
    await renderCandidateDetail();

    const backButton = screen.getByText('Back to Assessments');
    fireEvent.click(backButton);

    expect(mockOnNavigate).toHaveBeenCalledWith('assessments');
  });

  it('renders assessment metadata in results tab', async () => {
    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));

    expect(screen.getByText('Assessment Metadata')).toBeInTheDocument();
    // Duration appears in both header and metadata, check metadata section specifically
    const durationElements = screen.getAllByText(/Duration:/);
    expect(durationElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Total Prompts:/)).toBeInTheDocument();
    expect(screen.getByText(/Tests:/)).toBeInTheDocument();
  });

  it('renders test results when available', async () => {
    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));

    expect(screen.getByText('Test results')).toBeInTheDocument();
    expect(screen.getByText('Pipeline Processing')).toBeInTheDocument();
    expect(screen.getByText('Error Handling')).toBeInTheDocument();
  });


  it('renders scoring glossary with plain-English dimension descriptions', async () => {
    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));

    expect(screen.getByText('Scoring Glossary')).toBeInTheDocument();
    expect(screen.getAllByText('Task completion').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Measures delivery outcomes under assessment constraints/i)).toBeInTheDocument();
  });

  it('renders Post to Workable button', async () => {
    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'CLIENT REPORT' }));

    expect(screen.getByText('Post to Workable')).toBeInTheDocument();
  });

  it('shows inline comparison hint and action', async () => {
    await renderCandidateDetail();
    fireEvent.click(screen.getByRole('tab', { name: 'ASSESSMENT RESULTS' }));

    expect(screen.getByText(/Compare this candidate with others in the same role/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Compare with...' })).toBeInTheDocument();
  });
});
