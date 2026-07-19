import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';
import { publicJobApi } from '../requisitions/api';
import { ApplyForm } from './ApplyForm';
import { PublicJobPage } from './PublicJobPage';

vi.mock('../requisitions/api', () => ({
  publicJobApi: { get: vi.fn(), apply: vi.fn(), submitEeo: vi.fn() },
}));

// ChatMarkdown pulls in the shared chat kit; stub it to keep the render light.
vi.mock('../../shared/chat', () => ({ ChatMarkdown: ({ children }) => <div>{children}</div> }));

const renderPage = (job) => {
  publicJobApi.get.mockResolvedValue(job);
  return render(
    <TestMemoryRouter initialEntries={['/job/tok-1']}>
      <Routes>
        <Route path="/job/:token" element={<PublicJobPage />} />
      </Routes>
    </TestMemoryRouter>,
  );
};

describe('ApplyForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('submits an application with name + contact and reports the eeo_token', async () => {
    publicJobApi.apply.mockResolvedValue({ status: 'received', application_id: 9, eeo_token: 'eeo_x' });
    render(<ApplyForm token="tok-1" questions={[]} organizationName="Acme" />);

    fireEvent.change(screen.getByLabelText(/Full name/i), { target: { value: 'Casey R' } });
    fireEvent.change(screen.getByLabelText(/^Email/i), { target: { value: 'casey@x.test' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit application/i }));

    await waitFor(() => expect(publicJobApi.apply).toHaveBeenCalledTimes(1));
    const [token, payload] = publicJobApi.apply.mock.calls[0];
    expect(token).toBe('tok-1');
    expect(payload).toMatchObject({ full_name: 'Casey R', email: 'casey@x.test', answers: {} });
    // Friendly confirmation shows.
    expect(await screen.findByTestId('apply-confirmation')).toBeInTheDocument();
  });

  it('renders kind-appropriate screening inputs and includes answers in the submit', async () => {
    publicJobApi.apply.mockResolvedValue({ status: 'received', application_id: 1, eeo_token: null });
    const questions = [
      { id: 5, prompt: 'Years of Python?', kind: 'number', options: null, required: true },
      { id: 6, prompt: 'Authorized to work?', kind: 'boolean', options: null, required: false },
    ];
    render(<ApplyForm token="tok-1" questions={questions} organizationName="Acme" />);

    expect(screen.getByLabelText(/Years of Python/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Years of Python/i)).toBeRequired();
    fireEvent.change(screen.getByLabelText(/Full name/i), { target: { value: 'Sam' } });
    fireEvent.change(screen.getByLabelText(/^Phone/i), { target: { value: '+9715' } });
    fireEvent.change(screen.getByLabelText(/Years of Python/i), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit application/i }));

    await waitFor(() => expect(publicJobApi.apply).toHaveBeenCalled());
    expect(publicJobApi.apply.mock.calls[0][1].answers).toEqual({ '5': '4' });
  });

  it('groups multi-select answers and exposes required fields to assistive technology', () => {
    render(
      <ApplyForm
        token="tok-1"
        questions={[{ id: 8, prompt: 'Preferred locations', kind: 'multi_select', options: ['Dubai', 'Remote'], required: true }]}
        organizationName="Acme"
        resumeRequired
      />,
    );

    expect(screen.getByLabelText(/Full name/i)).toBeRequired();
    expect(screen.getByRole('group', { name: /Preferred locations/i })).toHaveAttribute('aria-required', 'true');
    expect(screen.getByTestId('resume-input')).toBeRequired();
  });

  it('blocks submit until a required question is answered', async () => {
    const questions = [{ id: 7, prompt: 'Confirm relocation', kind: 'text', options: null, required: true }];
    render(<ApplyForm token="tok-1" questions={questions} organizationName="Acme" />);
    fireEvent.change(screen.getByLabelText(/Full name/i), { target: { value: 'Sam' } });
    fireEvent.change(screen.getByLabelText(/^Email/i), { target: { value: 'a@b.test' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit application/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/required questions/i);
    expect(publicJobApi.apply).not.toHaveBeenCalled();
  });

  it('requires a resume when the agent-run job says it is required', async () => {
    render(
      <ApplyForm
        token="tok-1"
        questions={[]}
        organizationName="Acme"
        resumeRequired
      />,
    );
    fireEvent.change(screen.getByLabelText(/Full name/i), { target: { value: 'Sam' } });
    fireEvent.change(screen.getByLabelText(/^Email/i), { target: { value: 'sam@x.test' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit application/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/upload your resume/i);
    expect(publicJobApi.apply).not.toHaveBeenCalled();
  });

  it('offers the optional EEO step only when an eeo_token comes back, and it is dismissible', async () => {
    publicJobApi.apply.mockResolvedValue({ status: 'received', application_id: 3, eeo_token: 'eeo_tok' });
    publicJobApi.submitEeo.mockResolvedValue(undefined);
    render(<ApplyForm token="tok-1" questions={[]} organizationName="Acme" />);

    fireEvent.change(screen.getByLabelText(/Full name/i), { target: { value: 'Jo' } });
    fireEvent.change(screen.getByLabelText(/^Email/i), { target: { value: 'jo@x.test' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit application/i }));

    const eeo = await screen.findByTestId('eeo-step');
    expect(eeo).toBeInTheDocument();
    // The applicant picks a value, then chooses Skip — the selected demographic
    // must NOT leave the browser. Skip sends ONLY the decline marker.
    fireEvent.change(within(eeo).getByLabelText(/Gender/i), { target: { value: 'Female' } });
    fireEvent.click(screen.getByRole('button', { name: /^Skip$/i }));
    await waitFor(() => expect(publicJobApi.submitEeo).toHaveBeenCalledWith('eeo_tok', { declined_to_answer: true }));
    await waitFor(() => expect(screen.queryByTestId('eeo-step')).not.toBeInTheDocument());
  });

  it('does NOT offer the EEO step when no token is returned', async () => {
    publicJobApi.apply.mockResolvedValue({ status: 'received', application_id: 4, eeo_token: null });
    render(<ApplyForm token="tok-1" questions={[]} organizationName="Acme" />);
    fireEvent.change(screen.getByLabelText(/Full name/i), { target: { value: 'Jo' } });
    fireEvent.change(screen.getByLabelText(/^Email/i), { target: { value: 'jo@x.test' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit application/i }));
    await screen.findByTestId('apply-confirmation');
    expect(screen.queryByTestId('eeo-step')).not.toBeInTheDocument();
  });
});

describe('PublicJobPage apply gating (zero regression)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the apply form when accepts_applications is true', async () => {
    renderPage({
      title: 'Staff Engineer',
      jd_markdown: 'Build things.',
      organization_name: 'Acme',
      accepts_applications: true,
      screening_questions: [],
    });
    expect(await screen.findByTestId('apply-form')).toBeInTheDocument();
  });

  it('renders a clear inactive state when accepts_applications is false', async () => {
    renderPage({
      title: 'Staff Engineer',
      jd_markdown: 'Build things.',
      organization_name: 'Acme',
      accepts_applications: false,
    });
    expect(await screen.findByText(/Applications are not open for this role right now/i)).toBeInTheDocument();
    expect(screen.queryByTestId('apply-form')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Submit application/i })).not.toBeInTheDocument();
  });
});
