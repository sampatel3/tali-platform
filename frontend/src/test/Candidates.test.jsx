import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
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

import { auth, candidates as candidatesApi, assessments as assessmentsApi, roles as rolesApi } from '../lib/api.js';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const mockUser = {
  id: 1,
  email: 'admin@taali.ai',
  full_name: 'Admin User',
  organization_id: 1,
  role: 'admin',
};

const mockCandidates = [
  {
    id: 100,
    email: 'alice@example.com',
    full_name: 'Alice Johnson',
    position: 'Senior Engineer',
    cv_filename: 'alice_cv.pdf',
    job_spec_filename: null,
    created_at: '2026-01-10T10:00:00Z',
  },
  {
    id: 101,
    email: 'bob@example.com',
    full_name: 'Bob Smith',
    position: 'Junior Developer',
    cv_filename: null,
    job_spec_filename: 'bob_jd.pdf',
    created_at: '2026-01-12T10:00:00Z',
  },
  {
    id: 102,
    email: 'carol@example.com',
    full_name: 'Carol White',
    position: 'Staff Engineer',
    cv_filename: 'carol_cv.pdf',
    job_spec_filename: 'carol_jd.pdf',
    created_at: '2026-01-15T10:00:00Z',
  },
];

const setupAuthenticatedUser = () => {
  localStorage.setItem('taali_access_token', 'fake-jwt-token');
  localStorage.setItem('taali_user', JSON.stringify(mockUser));
  auth.me.mockResolvedValue({ data: mockUser });
};

const renderAppOnCandidatesPage = async () => {
  // The app auto-redirects authenticated users to dashboard.
  // We need to navigate to candidates via the nav.
  assessmentsApi.list.mockResolvedValue({ data: { items: [], total: 0 } });

  const result = render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );

  // Wait for dashboard to load
  await waitFor(() => {
    expect(screen.getByText('Assessments', { selector: 'h1' })).toBeInTheDocument();
  });

  // Navigate to Candidates via nav link
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
    candidatesApi.list.mockResolvedValue({ data: { items: mockCandidates } });
    rolesApi.list.mockResolvedValue({ data: [] });
    rolesApi.listTasks.mockResolvedValue({ data: [] });
    rolesApi.listApplications.mockResolvedValue({ data: [] });
  });

  afterEach(() => {
    window.location.hash = '';
    localStorage.clear();
  });

  it('renders Candidates heading', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Candidates', { selector: 'h1' })).toBeInTheDocument();
      expect(screen.getByText('Search and manage candidate profiles')).toBeInTheDocument();
    });
  });

  it('renders candidate list', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Alice Johnson')).toBeInTheDocument();
      expect(screen.getByText('Bob Smith')).toBeInTheDocument();
      expect(screen.getByText('Carol White')).toBeInTheDocument();
    });
  });

  it('renders candidate emails', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('alice@example.com')).toBeInTheDocument();
      expect(screen.getByText('bob@example.com')).toBeInTheDocument();
    });
  });

  it('renders search input', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search by name or email')).toBeInTheDocument();
    });
  });

  it('search input calls API with query parameter', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search by name or email')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Search by name or email');
    fireEvent.change(searchInput, { target: { value: 'alice' } });

    await waitFor(() => {
      expect(candidatesApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ q: 'alice' })
      );
    });
  });

  it('renders Create Candidate form', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create Candidate' })).toBeInTheDocument();
      expect(screen.getByPlaceholderText('email@company.com')).toBeInTheDocument();
      expect(screen.getByPlaceholderText('Full name')).toBeInTheDocument();
      expect(screen.getByPlaceholderText('Position')).toBeInTheDocument();
      expect(screen.getByText('CV Upload (required for new candidates)')).toBeInTheDocument();
    });
  });

  it('validates email required on create', async () => {
    // window.alert should be called with error
    const alertMock = vi.spyOn(window, 'alert').mockImplementation(() => {});

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Create Candidate' })).toBeInTheDocument();
    });

    // Click create without filling email
    fireEvent.click(screen.getByRole('button', { name: 'Create Candidate' }));

    await waitFor(() => {
      expect(alertMock).toHaveBeenCalledWith('Email is required');
    });

    alertMock.mockRestore();
  });

  it('calls candidatesApi.createWithCv with form data', async () => {
    candidatesApi.createWithCv.mockResolvedValue({ data: { id: 200, email: 'new@test.com' } });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByPlaceholderText('email@company.com')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText('email@company.com'), {
      target: { value: 'new@test.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('Full name'), {
      target: { value: 'New Candidate' },
    });
    fireEvent.change(screen.getByPlaceholderText('Position'), {
      target: { value: 'Mid Engineer' },
    });
    const file = new File(['cv-content'], 'resume.pdf', { type: 'application/pdf' });
    const fileInput = document.querySelector('input[type="file"][accept=".pdf,.docx"]');
    fireEvent.change(fileInput, { target: { files: [file] } });

    fireEvent.click(screen.getByRole('button', { name: 'Create Candidate' }));

    await waitFor(() => {
      expect(candidatesApi.createWithCv).toHaveBeenCalledWith({
        email: 'new@test.com',
        full_name: 'New Candidate',
        position: 'Mid Engineer',
        file,
      });
    });
  });

  it('renders Delete button for each candidate', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      const deleteButtons = screen.getAllByText('Delete');
      expect(deleteButtons.length).toBe(mockCandidates.length);
    });
  });

  it('calls candidatesApi.remove when delete is confirmed', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true);
    candidatesApi.remove.mockResolvedValue({ data: {} });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Alice Johnson')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByText('Delete');
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith('Delete this candidate?');
      expect(candidatesApi.remove).toHaveBeenCalledWith(100);
    });

    confirmMock.mockRestore();
  });

  it('shows document status badges with CV checkmark', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      // Alice has CV uploaded, so should see "CV ✓"
      const cvBadges = screen.getAllByText(/CV/);
      expect(cvBadges.length).toBeGreaterThanOrEqual(1);
    });
  });

  it('shows Upload Docs buttons', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      const uploadButtons = screen.getAllByText('Upload Docs');
      expect(uploadButtons.length).toBe(mockCandidates.length);
    });
  });

  it('renders Edit button for each candidate', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      const editButtons = screen.getAllByText('Edit');
      expect(editButtons.length).toBe(mockCandidates.length);
    });
  });

  it('shows total candidates count', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('3 total')).toBeInTheDocument();
    });
  });

  it('renders table headers', async () => {
    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Name')).toBeInTheDocument();
      expect(screen.getByText('Email')).toBeInTheDocument();
      expect(screen.getByText('Position')).toBeInTheDocument();
      expect(screen.getByText('Documents')).toBeInTheDocument();
      expect(screen.getByText('Created')).toBeInTheDocument();
      expect(screen.getByText('Actions')).toBeInTheDocument();
    });
  });

  it('shows no candidates message when list is empty', async () => {
    candidatesApi.list.mockResolvedValue({ data: { items: [] } });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('No candidates found.')).toBeInTheDocument();
    });
  });

  it('disables Add Candidate until selected role has job spec', async () => {
    rolesApi.list.mockResolvedValue({
      data: [{ id: 1, name: 'Backend Engineer', job_spec_filename: null }],
    });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Role workflow')).toBeInTheDocument();
    });

    const addApplicationBtn = screen.getByRole('button', { name: 'Add Candidate' });
    expect(addApplicationBtn).toBeDisabled();
  });

  it('enables Add Candidate when selected role has job spec', async () => {
    rolesApi.list.mockResolvedValue({
      data: [{ id: 2, name: 'ML Engineer', job_spec_filename: 'ml-role-spec.pdf' }],
    });

    await renderAppOnCandidatesPage();

    await waitFor(() => {
      expect(screen.getByText('Role workflow')).toBeInTheDocument();
    });

    const addApplicationBtn = screen.getByRole('button', { name: 'Add Candidate' });
    expect(addApplicationBtn).not.toBeDisabled();
  });
});
