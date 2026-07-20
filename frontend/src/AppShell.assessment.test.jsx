import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { vi } from 'vitest';

const routeProbe = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }));
const welcomeRouteProbe = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }));
const toastScopeProbe = vi.hoisted(() => ({
  mounts: 0,
  unmounts: 0,
  staleShowToast: null,
}));
const authState = vi.hoisted(() => ({
  isAuthenticated: true,
  loading: false,
  sessionBoundary: 'boundary-a',
}));
const assessmentsApi = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('./context/AuthContext', () => ({
  useAuth: () => authState,
}));

vi.mock('./shared/api/assessmentsClient', () => ({
  assessments: assessmentsApi,
}));

vi.mock('./app/lazyPages', async (importOriginal) => {
  const React = await import('react');
  const { useToast } = await import('./context/ToastContext');
  const actual = await importOriginal();

  function AssessmentPageProbe() {
    const [draft, setDraft] = React.useState('');
    const [submitted, setSubmitted] = React.useState(false);
    React.useEffect(() => {
      routeProbe.mounts += 1;
      return () => {
        routeProbe.unmounts += 1;
      };
    }, []);
    if (submitted) {
      return <h1>Task submitted</h1>;
    }
    return (
      <>
        <label>
          Runtime draft
          <input
            aria-label="Runtime draft"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
        <button type="button" onClick={() => setSubmitted(true)}>Complete runtime</button>
      </>
    );
  }

  function CandidateWelcomePageProbe({ token }) {
    React.useEffect(() => {
      welcomeRouteProbe.mounts += 1;
      return () => {
        welcomeRouteProbe.unmounts += 1;
      };
    }, []);
    return (
      <>
        <div>Candidate welcome</div>
        <div>Welcome token: {token}</div>
      </>
    );
  }

  function SessionStateProbe() {
    const { activities, showToast, toasts } = useToast();
    React.useEffect(() => {
      toastScopeProbe.mounts += 1;
      return () => {
        toastScopeProbe.unmounts += 1;
      };
    }, []);
    return (
      <>
        <div>Recruiter home</div>
        <button type="button" onClick={() => showToast('Account A failed', 'error')}>
          Show account toast
        </button>
        <button
          type="button"
          onClick={() => { toastScopeProbe.staleShowToast = showToast; }}
        >
          Capture toast callback
        </button>
        <output data-testid="session-toast-count">{toasts.length}</output>
        <output data-testid="session-activity-count">{activities.length}</output>
      </>
    );
  }

  return {
    ...actual,
    AssessmentPage: AssessmentPageProbe,
    CandidateStandingReportPage: () => <div>Candidate file</div>,
    CandidateWelcomePage: CandidateWelcomePageProbe,
    HomeMotionPreview: SessionStateProbe,
    HomePage: SessionStateProbe,
  };
});

import App from './AppShell';
import {
  clearCandidateRuntimeRecovery,
  rememberCandidateRuntime,
} from './shared/assessment/candidateProofBinding';

const deferred = () => {
  let resolve;
  const promise = new Promise((settle) => { resolve = settle; });
  return { promise, resolve };
};

describe('AppShell public assessment route stability', () => {
  beforeEach(() => {
    routeProbe.mounts = 0;
    routeProbe.unmounts = 0;
    welcomeRouteProbe.mounts = 0;
    welcomeRouteProbe.unmounts = 0;
    toastScopeProbe.mounts = 0;
    toastScopeProbe.unmounts = 0;
    toastScopeProbe.staleShowToast = null;
    authState.isAuthenticated = true;
    authState.loading = false;
    authState.sessionBoundary = 'boundary-a';
    assessmentsApi.get.mockReset();
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState(null, '', '/assessment/live?token=tok-live');
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it.each([
    ['authenticated', true],
    ['unauthenticated', false],
  ])('preserves the live runtime and ignores recruiter shortcuts for an %s candidate route', async (_label, isAuthenticated) => {
    authState.isAuthenticated = isAuthenticated;
    render(<App />);

    const draft = await screen.findByRole('textbox', { name: 'Runtime draft' });
    fireEvent.change(draft, { target: { value: 'unsaved candidate work' } });
    expect(routeProbe.mounts).toBe(1);

    await act(async () => {
      window.history.pushState(null, '', '/assessment/live?token=tok-live&view=workspace');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(screen.getByRole('textbox', { name: 'Runtime draft' })).toHaveValue('unsaved candidate work');
    expect(routeProbe.mounts).toBe(1);
    expect(routeProbe.unmounts).toBe(0);

    fireEvent.keyDown(window, { key: '?' });

    expect(screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Runtime draft' })).toHaveValue('unsaved candidate work');
    expect(routeProbe.mounts).toBe(1);
  });

  it.each([
    ['authenticated', '/assess/tok-live', true],
    ['unauthenticated', '/assess/tok-live', false],
    ['authenticated', '/assessment/42?token=tok-live', true],
    ['unauthenticated', '/assessment/42?token=tok-live', false],
  ])('ignores recruiter shortcuts for an %s candidate welcome at %s', async (_label, path, isAuthenticated) => {
    authState.isAuthenticated = isAuthenticated;
    window.history.replaceState(null, '', path);
    render(<App />);

    expect(await screen.findByText('Candidate welcome')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: '?' });

    expect(screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })).not.toBeInTheDocument();
  });

  it('remounts the candidate welcome page when the invite token changes', async () => {
    window.history.replaceState(null, '', '/assess/token-a');
    render(<App />);

    expect(await screen.findByText('Welcome token: token-a')).toBeInTheDocument();
    expect(welcomeRouteProbe.mounts).toBe(1);

    await act(async () => {
      window.history.pushState(null, '', '/assess/token-b');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(await screen.findByText('Welcome token: token-b')).toBeInTheDocument();
    expect(welcomeRouteProbe.mounts).toBe(2);
    expect(welcomeRouteProbe.unmounts).toBe(1);
  });

  it('preserves the live runtime when AppContent closes recruiter UI after public navigation', async () => {
    window.history.replaceState(null, '', '/home');
    render(<App />);

    expect(await screen.findByText('Recruiter home')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: '?' });
    expect(screen.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeInTheDocument();

    await act(async () => {
      window.history.pushState(null, '', '/assessment/live?token=tok-live');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    const draft = await screen.findByRole('textbox', { name: 'Runtime draft' });
    fireEvent.change(draft, { target: { value: 'work survives parent state updates' } });
    await act(async () => {});

    expect(screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Runtime draft' })).toHaveValue('work survives parent state updates');
    expect(routeProbe.mounts).toBe(1);
    expect(routeProbe.unmounts).toBe(0);
  });

  it('starts a clean runtime when the assessment token changes', async () => {
    render(<App />);

    const draft = await screen.findByRole('textbox', { name: 'Runtime draft' });
    fireEvent.change(draft, { target: { value: 'work for the first assessment' } });

    await act(async () => {
      window.history.pushState(null, '', '/assessment/live?token=tok-next');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(screen.getByRole('textbox', { name: 'Runtime draft' })).toHaveValue('');
    expect(routeProbe.mounts).toBe(2);
    expect(routeProbe.unmounts).toBe(1);
  });

  it('keeps the accepted submission mounted when token recovery is cleared', async () => {
    rememberCandidateRuntime('tok-live', 42);
    render(<App />);

    await screen.findByRole('textbox', { name: 'Runtime draft' });
    fireEvent.click(screen.getByRole('button', { name: 'Complete runtime' }));
    expect(screen.getByRole('heading', { name: 'Task submitted' })).toBeInTheDocument();

    await act(async () => {
      clearCandidateRuntimeRecovery('tok-live');
      window.history.replaceState(null, '', '/assessment/live');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(screen.getByRole('heading', { name: 'Task submitted' })).toBeInTheDocument();
    expect(routeProbe.mounts).toBe(1);
    expect(routeProbe.unmounts).toBe(0);
  });

  it('clears account toasts and activity across logout and the next session boundary', async () => {
    window.history.replaceState(null, '', '/home-preview');
    const { rerender } = render(<App />);

    await screen.findByText('Recruiter home');
    fireEvent.click(screen.getByRole('button', { name: 'Show account toast' }));
    fireEvent.click(screen.getByRole('button', { name: 'Capture toast callback' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Account A failed');
    expect(screen.getByTestId('session-toast-count')).toHaveTextContent('1');
    expect(screen.getByTestId('session-activity-count')).toHaveTextContent('1');

    authState.isAuthenticated = false;
    authState.sessionBoundary = 'logout-boundary';
    rerender(<App />);

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByTestId('session-toast-count')).toHaveTextContent('0');
    expect(screen.getByTestId('session-activity-count')).toHaveTextContent('0');
    expect(toastScopeProbe.mounts).toBe(2);
    expect(toastScopeProbe.unmounts).toBe(1);

    authState.isAuthenticated = true;
    authState.sessionBoundary = 'boundary-b';
    rerender(<App />);

    expect(screen.getByTestId('session-toast-count')).toHaveTextContent('0');
    expect(screen.getByTestId('session-activity-count')).toHaveTextContent('0');
    expect(toastScopeProbe.mounts).toBe(3);
    expect(toastScopeProbe.unmounts).toBe(2);

    act(() => {
      toastScopeProbe.staleShowToast?.('Late account A failure', 'error');
    });
    expect(screen.queryByText('Late account A failure')).not.toBeInTheDocument();
    expect(screen.getByTestId('session-toast-count')).toHaveTextContent('0');
    expect(screen.getByTestId('session-activity-count')).toHaveTextContent('0');
  });

  it('refetches the same assessment for a new session without reusing account A application state', async () => {
    const accountBResponse = deferred();
    assessmentsApi.get
      .mockResolvedValueOnce({
        data: { id: 42, application_id: 111, candidate_name: 'Account A candidate' },
      })
      .mockImplementationOnce(() => accountBResponse.promise);
    window.history.replaceState(null, '', '/assessments/42');
    const { rerender } = render(<App />);

    await waitFor(() => {
      expect(window.location.pathname).toBe('/candidates/111');
    });
    expect(assessmentsApi.get).toHaveBeenNthCalledWith(1, 42);

    await act(async () => {
      authState.sessionBoundary = 'boundary-b';
      window.history.replaceState(null, '', '/assessments/42');
      window.dispatchEvent(new PopStateEvent('popstate'));
      rerender(<App />);
    });

    await waitFor(() => {
      expect(assessmentsApi.get).toHaveBeenCalledTimes(2);
    });
    expect(assessmentsApi.get).toHaveBeenNthCalledWith(2, 42);
    expect(window.location.pathname).toBe('/assessments/42');
    expect(window.location.pathname).not.toContain('111');

    await act(async () => {
      accountBResponse.resolve({
        data: { id: 42, application_id: 222, candidate_name: 'Account B candidate' },
      });
    });
    await waitFor(() => {
      expect(window.location.pathname).toBe('/candidates/222');
    });
  });
});
