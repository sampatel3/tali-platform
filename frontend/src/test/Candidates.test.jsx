import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

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
    uploadCv: vi.fn(),
    postToWorkable: vi.fn(),
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
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    createWithCv: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    uploadCv: vi.fn(),
    uploadJobSpec: vi.fn(),
  },
  roles: {
    list: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    uploadJobSpec: vi.fn(),
    listTasks: vi.fn().mockResolvedValue({ data: [] }),
    addTask: vi.fn(),
    removeTask: vi.fn(),
    listApplications: vi.fn().mockResolvedValue({ data: [] }),
    createApplication: vi.fn(),
    updateApplication: vi.fn(),
    uploadApplicationCv: vi.fn(),
    createAssessment: vi.fn(),
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

import {
  auth,
  roles as rolesApi,
  tasks as tasksApi,
} from '../shared/api';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const baseRoles = [
  {
    id: 9,
    name: 'ML Engineer',
    description: 'Own model serving reliability.',
    job_spec_filename: 'ml-role-spec.pdf',
    tasks_count: 1,
    applications_count: 1,
  },
];

const setupAuthenticatedUser = () => {
  localStorage.setItem('taali_access_token', 'fake-jwt-token');
  localStorage.setItem('taali_user', JSON.stringify(mockUser));
  auth.me.mockResolvedValue({ data: mockUser });
};

const renderAppOnCandidatesPage = async () => {
  const result = render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );

  await waitFor(() => {
    expect(screen.getByText('Assessments', { selector: 'h1' })).toBeInTheDocument();
  });

  const candidatesNav = screen.getByText('Candidates', { selector: 'button' });
  await act(async () => {
    fireEvent.click(candidatesNav);
  });

  return result;
};

describe('CandidatesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.location.hash = '';
    setupAuthenticatedUser();

    rolesApi.list.mockResolvedValue({ data: baseRoles });
    rolesApi.listTasks.mockResolvedValue({ data: [{ id: 700, name: 'Async Debugging Challenge' }] });
    rolesApi.listApplications.mockResolvedValue({
      data: [
        {
          id: 501,
          candidate_id: 42,
          candidate_email: 'apply@example.com',
          candidate_name: 'Apply Person',
          candidate_position: 'ML Engineer',
          status: 'applied',
          cv_filename: 'apply.pdf',
          created_at: '2026-01-10T10:00:00Z',
          updated_at: '2026-01-10T10:00:00Z',
        },
      ],
    });
    tasksApi.list.mockResolvedValue({ data: [{ id: 700, name: 'Async Debugging Challenge' }] });
  });

  afterEach(() => {
    window.location.hash = '';
    localStorage.clear();
  });

  it('renders candidates header controls', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Candidates', { selector: 'h1' })).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: 'New role' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add candidate' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Search by name, email, position, or status')).toBeInTheDocument();
  });

  it('shows role list and role summary context', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'ML Engineer', level: 2 })).toBeInTheDocument();
      expect(screen.getByText('Job spec:')).toBeInTheDocument();
      expect(screen.getByText('Tasks (1):')).toBeInTheDocument();
    });
  });

  it('shows interview focus guidance when available', async () => {
    rolesApi.list.mockResolvedValue({
      data: [
        {
          ...baseRoles[0],
          interview_focus_generated_at: '2026-01-12T10:00:00Z',
          interview_focus: {
            role_summary: 'Prioritize validation of API ownership and on-call incident depth.',
            manual_screening_triggers: ['Ownership depth', 'Incident response'],
            questions: [
              {
                question: 'Tell me about a production incident you directly owned.',
                what_to_listen_for: ['Clear root cause and mitigation details'],
                concerning_signals: ['Cannot explain personal decisions'],
              },
              {
                question: 'How did you design a backend API for reliability?',
                what_to_listen_for: ['Tradeoffs and failure-mode handling'],
                concerning_signals: ['Only high-level abstractions'],
              },
              {
                question: 'How do you verify compensation aligns with role scope?',
                what_to_listen_for: ['Evidence-based impact and ownership'],
                concerning_signals: ['Title-only justification'],
              },
            ],
          },
        },
      ],
    });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Interview focus')).toBeInTheDocument();
      expect(screen.getByText(/Q1\./)).toBeInTheDocument();
      expect(screen.getAllByText(/Look for:/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/Watch out for:/).length).toBeGreaterThan(0);
    });
  });

  it('allows collapsing and expanding interview focus guidance', async () => {
    rolesApi.list.mockResolvedValue({
      data: [
        {
          ...baseRoles[0],
          interview_focus_generated_at: '2026-01-12T10:00:00Z',
          interview_focus: {
            role_summary: 'Focus on practical ownership and tradeoffs.',
            manual_screening_triggers: ['Ownership depth'],
            questions: [
              {
                question: 'Describe an incident you owned end to end.',
                what_to_listen_for: ['Root cause and mitigation clarity'],
                concerning_signals: ['Vague contribution details'],
              },
            ],
          },
        },
      ],
    });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText(/Q1\./)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Interview focus/i }));

    await waitFor(() => {
      expect(screen.queryByText(/Q1\./)).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Interview focus/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Interview focus/i }));

    await waitFor(() => {
      expect(screen.getByText(/Q1\./)).toBeInTheDocument();
    });
  });

  it('shows empty role state and disables Add candidate when there are no roles', async () => {
    rolesApi.list.mockResolvedValue({ data: [] });
    rolesApi.listApplications.mockResolvedValue({ data: [] });
    rolesApi.listTasks.mockResolvedValue({ data: [] });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('No roles yet')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: 'Add candidate' })).toBeDisabled();
    expect(screen.getAllByRole('button', { name: 'Create your first role' }).length).toBeGreaterThan(0);
  });

  it('filters role candidates table by search text', async () => {
    rolesApi.listApplications.mockResolvedValue({
      data: [
        {
          id: 1,
          candidate_id: 8,
          candidate_email: 'alice@example.com',
          candidate_name: 'Alice Johnson',
          candidate_position: 'Senior Engineer',
          status: 'applied',
          cv_filename: 'alice.pdf',
          created_at: '2026-01-10T10:00:00Z',
          updated_at: '2026-01-10T10:00:00Z',
        },
        {
          id: 2,
          candidate_id: 9,
          candidate_email: 'bob@example.com',
          candidate_name: 'Bob Smith',
          candidate_position: 'ML Engineer',
          status: 'review',
          cv_filename: 'bob.pdf',
          created_at: '2026-01-11T10:00:00Z',
          updated_at: '2026-01-11T10:00:00Z',
        },
      ],
    });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Alice Johnson')).toBeInTheDocument();
      expect(screen.getByText('Bob Smith')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText('Search by name, email, position, or status'), {
      target: { value: 'Alice' },
    });

    await waitFor(() => {
      expect(screen.getByText('Alice Johnson')).toBeInTheDocument();
      expect(screen.queryByText('Bob Smith')).not.toBeInTheDocument();
    });
  });

  it('shows CV match score in role candidates table', async () => {
    rolesApi.listApplications.mockResolvedValue({
      data: [
        {
          id: 11,
          candidate_id: 101,
          candidate_email: 'match@example.com',
          candidate_name: 'Match Candidate',
          candidate_position: 'Backend Engineer',
          status: 'applied',
          cv_filename: 'match.pdf',
          cv_match_score: 8.2,
          created_at: '2026-01-10T10:00:00Z',
          updated_at: '2026-01-10T10:00:00Z',
        },
      ],
    });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Match Candidate')).toBeInTheDocument();
      expect(screen.getByText('8.2/10')).toBeInTheDocument();
    });
  });

  it('creates a role from the role sheet', async () => {
    rolesApi.create.mockResolvedValue({
      data: { id: 321, name: 'Platform Engineer', description: null },
    });
    rolesApi.list.mockResolvedValueOnce({ data: baseRoles });
    rolesApi.list.mockResolvedValueOnce({
      data: [
        { id: 321, name: 'Platform Engineer', job_spec_filename: null, tasks_count: 0, applications_count: 0 },
        ...baseRoles,
      ],
    });

    await renderAppOnCandidatesPage();
    fireEvent.click(screen.getByRole('button', { name: 'New role' }));

    const dialog = await screen.findByRole('dialog', { name: 'New role' });
    fireEvent.change(within(dialog).getByPlaceholderText('e.g. Senior Backend Engineer'), {
      target: { value: 'Platform Engineer' },
    });

    fireEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save role' }));

    await waitFor(() => {
      expect(rolesApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Platform Engineer' })
      );
    });
  });

  it('creates role application and uploads CV from Add candidate sheet', async () => {
    rolesApi.createApplication.mockResolvedValue({ data: { id: 200 } });
    rolesApi.uploadApplicationCv.mockResolvedValue({ data: { success: true } });

    const { container } = await renderAppOnCandidatesPage();
    fireEvent.click(screen.getByRole('button', { name: 'Add candidate' }));

    const dialog = await screen.findByRole('dialog', { name: 'Add candidate' });
    fireEvent.change(within(dialog).getByPlaceholderText('candidate@company.com'), {
      target: { value: 'new@test.com' },
    });
    fireEvent.change(within(dialog).getByPlaceholderText('Jane Doe'), {
      target: { value: 'New Candidate' },
    });
    fireEvent.change(within(dialog).getByPlaceholderText('Defaults to role title'), {
      target: { value: 'Mid Engineer' },
    });

    const file = new File(['cv-content'], 'resume.pdf', { type: 'application/pdf' });
    const fileInput = container.querySelector('input[type="file"][accept=".pdf,.docx,.doc"]');
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add candidate' }));

    await waitFor(() => {
      expect(rolesApi.createApplication).toHaveBeenCalledWith(
        '9',
        expect.objectContaining({
          candidate_email: 'new@test.com',
          candidate_name: 'New Candidate',
          candidate_position: 'Mid Engineer',
        })
      );
      expect(rolesApi.uploadApplicationCv).toHaveBeenCalledWith(200, file);
    });
  });

  it('creates assessment from candidate row action', async () => {
    const alertMock = vi.spyOn(window, 'alert').mockImplementation(() => {});
    rolesApi.createAssessment.mockResolvedValue({ data: { id: 1000 } });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create assessment' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Create assessment' }));
    fireEvent.click(screen.getByRole('button', { name: 'Send assessment' }));

    await waitFor(() => {
      expect(rolesApi.createAssessment).toHaveBeenCalledWith(501, { task_id: 700 });
    });

    alertMock.mockRestore();
  });

  it('switches active role from header selector and reloads role context', async () => {
    rolesApi.list.mockResolvedValue({
      data: [
        { id: 1, name: 'Backend Engineer', job_spec_filename: 'backend.pdf', tasks_count: 1, applications_count: 1 },
        { id: 2, name: 'Data Engineer', job_spec_filename: 'data.pdf', tasks_count: 1, applications_count: 1 },
      ],
    });
    rolesApi.listTasks.mockImplementation((roleId) => (
      Promise.resolve({ data: [{ id: Number(roleId) * 10, name: `Task ${roleId}` }] })
    ));
    rolesApi.listApplications.mockImplementation((roleId) => (
      Promise.resolve({
        data: [
          {
            id: Number(roleId) * 100,
            candidate_id: Number(roleId) * 1000,
            candidate_email: `candidate${roleId}@example.com`,
            candidate_name: `Candidate ${roleId}`,
            candidate_position: 'Engineer',
            status: 'applied',
            cv_filename: 'resume.pdf',
            created_at: '2026-01-10T10:00:00Z',
            updated_at: '2026-01-10T10:00:00Z',
          },
        ],
      })
    ));

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Candidate 1')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Active role'), { target: { value: '2' } });

    await waitFor(() => {
      expect(screen.getByText('Candidate 2')).toBeInTheDocument();
      expect(screen.queryByText('Candidate 1')).not.toBeInTheDocument();
    });
  });

});
