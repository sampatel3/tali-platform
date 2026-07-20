import { act, fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';

const routeProbe = vi.hoisted(() => ({ mounts: 0, unmounts: 0 }));

vi.mock('./context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, loading: false }),
}));

vi.mock('./app/lazyPages', async (importOriginal) => {
  const React = await import('react');
  const actual = await importOriginal();

  function AssessmentPageProbe() {
    const [draft, setDraft] = React.useState('');
    React.useEffect(() => {
      routeProbe.mounts += 1;
      return () => {
        routeProbe.unmounts += 1;
      };
    }, []);
    return (
      <label>
        Runtime draft
        <input
          aria-label="Runtime draft"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
    );
  }

  return { ...actual, AssessmentPage: AssessmentPageProbe };
});

import App from './AppShell';

describe('AppShell public assessment route stability', () => {
  beforeEach(() => {
    routeProbe.mounts = 0;
    routeProbe.unmounts = 0;
    localStorage.clear();
    window.history.replaceState(null, '', '/assessment/live?token=tok-live');
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('preserves the live runtime across parent route updates and ignores recruiter shortcuts for an authenticated recruiter', async () => {
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
});
