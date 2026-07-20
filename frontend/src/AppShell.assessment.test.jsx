import { act, fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';

const routeProbe = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }));
const authState = vi.hoisted(() => ({ isAuthenticated: true, loading: false }));

vi.mock('./context/AuthContext', () => ({
  useAuth: () => authState,
}));

vi.mock('./app/lazyPages', async (importOriginal) => {
  const React = await import('react');
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

  return {
    ...actual,
    AssessmentPage: AssessmentPageProbe,
    CandidateWelcomePage: () => <div>Candidate welcome</div>,
    HomePage: () => <div>Recruiter home</div>,
  };
});

import App from './AppShell';
import {
  clearCandidateRuntimeRecovery,
  rememberCandidateRuntime,
} from './shared/assessment/candidateProofBinding';

describe('AppShell public assessment route stability', () => {
  beforeEach(() => {
    routeProbe.mounts = 0;
    routeProbe.unmounts = 0;
    authState.isAuthenticated = true;
    authState.loading = false;
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
});
