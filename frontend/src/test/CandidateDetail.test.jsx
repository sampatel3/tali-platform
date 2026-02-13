import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// Mock the API module
vi.mock('../lib/api.js', () => ({
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
    uploadCv: vi.fn(),
    postToWorkable: vi.fn(),
    updateManualEvaluation: vi.fn(),
  },
  billing: { usage: vi.fn() },
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
    update: vi.fn(),
    remove: vi.fn(),
    uploadCv: vi.fn(),
    uploadJobSpec: vi.fn(),
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

import { auth, assessments as assessmentsApi, analytics as analyticsApi, candidates as candidatesApi } from '../lib/api.js';
import { CandidateDetailPage } from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@tali.com',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const setupAuthenticatedUser = () => {
  localStorage.setItem('tali_access_token', 'fake-jwt-token');
  localStorage.setItem('tali_user', JSON.stringify(mockUser));
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
    final_score: 85,
    status: 'completed',
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
        overall: 7,
        skills: 8,
        experience: 6,
        details: {
          matching_skills: ['Python', 'AsyncIO', 'Testing'],
          missing_skills: ['Kubernetes', 'Terraform'],
          experience_highlights: ['5 years of backend development', 'Led team of 4 engineers'],
          concerns: ['No cloud infrastructure experience'],
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
    // Final score is 85 => STRONG HIRE
    expect(screen.getByText('85')).toBeInTheDocument();
    expect(screen.getByText('STRONG HIRE')).toBeInTheDocument();
  });

  it('renders position and task info', async () => {
    await renderCandidateDetail();
    expect(screen.getByText('Senior Engineer')).toBeInTheDocument();
    expect(screen.getByText('Task: Async Pipeline Debugging')).toBeInTheDocument();
  });

  it('renders duration info', async () => {
    await renderCandidateDetail();
    expect(screen.getByText('Duration: 45m')).toBeInTheDocument();
  });

  it('renders results tab by default', async () => {
    await renderCandidateDetail();
    // Results tab should be active
    expect(screen.getByText('Category Breakdown')).toBeInTheDocument();
  });

  it('renders category scores in results tab', async () => {
    await renderCandidateDetail();
    // Category names appear in the expandable sections
    const taskCompletionElements = screen.getAllByText('Task Completion');
    expect(taskCompletionElements.length).toBeGreaterThanOrEqual(1);
    const promptClarityElements = screen.getAllByText('Prompt Clarity');
    expect(promptClarityElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Independence & Efficiency').length).toBeGreaterThanOrEqual(1);
  });

  it('renders radar chart in results tab', async () => {
    await renderCandidateDetail();
    expect(screen.getByTestId('radar-chart')).toBeInTheDocument();
  });

  it('tab switching works - AI Usage tab', async () => {
    await renderCandidateDetail();

    const aiUsageTab = screen.getByText('AI Usage');
    fireEvent.click(aiUsageTab);

    await waitFor(() => {
      expect(screen.getByText('Avg Prompt Quality')).toBeInTheDocument();
      expect(screen.getByText('Time to First Prompt')).toBeInTheDocument();
      expect(screen.getByText('Browser Focus')).toBeInTheDocument();
    });
  });

  it('shows prompt log in AI Usage tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByText('AI Usage'));

    await waitFor(() => {
      expect(screen.getByText(/Prompt Log/)).toBeInTheDocument();
      expect(screen.getByText(/How do I fix the race condition/)).toBeInTheDocument();
    });
  });

  it('tab switching works - CV & Fit tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByText('CV & Fit'));

    await waitFor(() => {
      expect(screen.getByText('Overall Match')).toBeInTheDocument();
      expect(screen.getByText('Skills Match')).toBeInTheDocument();
      expect(screen.getByText('Experience')).toBeInTheDocument();
    });
  });

  it('shows matching skills in CV & Fit tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByText('CV & Fit'));

    await waitFor(() => {
      expect(screen.getByText('Matching Skills')).toBeInTheDocument();
      expect(screen.getByText('Python')).toBeInTheDocument();
      expect(screen.getByText('AsyncIO')).toBeInTheDocument();
    });
  });

  it('shows missing skills in CV & Fit tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByText('CV & Fit'));

    await waitFor(() => {
      expect(screen.getByText('Missing Skills')).toBeInTheDocument();
      expect(screen.getByText('Kubernetes')).toBeInTheDocument();
      expect(screen.getByText('Terraform')).toBeInTheDocument();
    });
  });

  it('tab switching works - Timeline tab', async () => {
    await renderCandidateDetail();

    fireEvent.click(screen.getByText('Timeline'));

    await waitFor(() => {
      expect(screen.getByText('Assessment started')).toBeInTheDocument();
      expect(screen.getByText('First prompt sent')).toBeInTheDocument();
      expect(screen.getByText('Assessment submitted')).toBeInTheDocument();
    });
  });

  it('add note form renders with input and save button', async () => {
    await renderCandidateDetail();

    expect(screen.getByPlaceholderText('Add note about this candidate')).toBeInTheDocument();
    expect(screen.getByText('Save Note')).toBeInTheDocument();
  });

  it('add note form calls API on save', async () => {
    assessmentsApi.addNote.mockResolvedValue({ data: { timeline: [] } });
    const alertMock = vi.spyOn(window, 'alert').mockImplementation(() => {});

    await renderCandidateDetail();

    const noteInput = screen.getByPlaceholderText('Add note about this candidate');
    fireEvent.change(noteInput, { target: { value: 'Great candidate, recommend for next round' } });

    fireEvent.click(screen.getByText('Save Note'));

    await waitFor(() => {
      expect(assessmentsApi.addNote).toHaveBeenCalledWith(1, 'Great candidate, recommend for next round');
    });

    alertMock.mockRestore();
  });

  it('Download PDF button exists', async () => {
    await renderCandidateDetail();
    expect(screen.getByText('Download PDF')).toBeInTheDocument();
  });

  it('Download PDF calls downloadReport API', async () => {
    assessmentsApi.downloadReport.mockResolvedValue({ data: new Blob(['pdf-content']) });

    await renderCandidateDetail();

    fireEvent.click(screen.getByText('Download PDF'));

    await waitFor(() => {
      expect(assessmentsApi.downloadReport).toHaveBeenCalledWith(1);
    });
  });

  it('Delete button exists and calls remove API after confirm', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true);
    assessmentsApi.remove.mockResolvedValue({ data: {} });

    await renderCandidateDetail();

    // Find the Delete button (red text)
    const deleteButton = screen.getByRole('button', { name: 'Delete' });
    fireEvent.click(deleteButton);

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith('Delete this assessment? This cannot be undone.');
      expect(assessmentsApi.remove).toHaveBeenCalledWith(1);
    });

    confirmMock.mockRestore();
  });

  it('Back to Dashboard button calls onNavigate', async () => {
    await renderCandidateDetail();

    const backButton = screen.getByText('Back to Dashboard');
    fireEvent.click(backButton);

    expect(mockOnNavigate).toHaveBeenCalledWith('dashboard');
  });

  it('renders assessment metadata in results tab', async () => {
    await renderCandidateDetail();

    expect(screen.getByText('Assessment Metadata')).toBeInTheDocument();
    // Duration appears in both header and metadata, check metadata section specifically
    const durationElements = screen.getAllByText(/Duration:/);
    expect(durationElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Total Prompts:/)).toBeInTheDocument();
    expect(screen.getByText(/Tests:/)).toBeInTheDocument();
  });

  it('renders test results when available', async () => {
    await renderCandidateDetail();

    expect(screen.getByText('Test Results')).toBeInTheDocument();
    expect(screen.getByText('Pipeline Processing')).toBeInTheDocument();
    expect(screen.getByText('Error Handling')).toBeInTheDocument();
  });


  it('renders scoring glossary with plain-English dimension descriptions', async () => {
    await renderCandidateDetail();

    expect(screen.getByText('Scoring Glossary')).toBeInTheDocument();
    expect(screen.getAllByText('Task Completion').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Measures delivery outcomes under the assessment constraints/i)).toBeInTheDocument();
  });

  it('renders Post to Workable button', async () => {
    await renderCandidateDetail();

    expect(screen.getByText('Post to Workable')).toBeInTheDocument();
  });

  it('shows hint to compare candidates from Dashboard', async () => {
    await renderCandidateDetail();

    expect(screen.getByText(/Compare this candidate with others from the Dashboard/)).toBeInTheDocument();
  });

  it('saves structured manual evaluation from Evaluate tab', async () => {
    assessmentsApi.updateManualEvaluation.mockResolvedValue({
      data: {
        manual_evaluation: {
          category_scores: {
            correctness: { score: 'excellent', evidence: ['All tests pass'], weight: 0.6 },
          },
          strengths: ['Strong debugging'],
          improvements: ['Add more edge-case tests'],
          overall_score: 9.2,
          completed_due_to_timeout: false,
        },
      },
    });
    const alertMock = vi.spyOn(window, 'alert').mockImplementation(() => {});

    await renderCandidateDetail();
    fireEvent.click(screen.getByText('Evaluate'));

    const gradeSelect = screen.getAllByRole('combobox')[0];
    fireEvent.change(gradeSelect, { target: { value: 'excellent' } });
    fireEvent.change(screen.getAllByPlaceholderText('Evidence (required for this category)')[0], {
      target: { value: 'All tests pass' },
    });
    fireEvent.change(screen.getByPlaceholderText('Strong debugging discipline'), {
      target: { value: 'Strong debugging' },
    });
    fireEvent.change(screen.getByPlaceholderText('Add stronger edge-case tests'), {
      target: { value: 'Add more edge-case tests' },
    });

    fireEvent.click(screen.getByText('Save manual evaluation'));

    await waitFor(() => {
      expect(assessmentsApi.updateManualEvaluation).toHaveBeenCalledWith(1, {
        category_scores: {
          correctness: { score: 'excellent', evidence: ['All tests pass'] },
        },
        strengths: ['Strong debugging'],
        improvements: ['Add more edge-case tests'],
      });
    });

    alertMock.mockRestore();
  });
});
