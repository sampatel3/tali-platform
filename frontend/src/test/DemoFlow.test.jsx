import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DemoExperiencePage } from '../features/demo/DemoExperiencePage';
import { LandingPage } from '../features/marketing/LandingPage';
import { assessments } from '../shared/api';

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
    requestDemo: vi.fn(),
    startDemo: vi.fn(),
    start: vi.fn(),
    execute: vi.fn(),
    claudeChat: vi.fn(),
    submit: vi.fn().mockResolvedValue({ data: { id: 321, status: 'completed' } }),
  },
  billing: { usage: vi.fn(), costs: vi.fn(), credits: vi.fn(), createCheckoutSession: vi.fn() },
  organizations: { get: vi.fn() },
  analytics: { get: vi.fn().mockResolvedValue({ data: {} }) },
  tasks: { list: vi.fn().mockResolvedValue({ data: [] }), get: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), generate: vi.fn() },
  candidates: { list: vi.fn().mockResolvedValue({ data: { items: [] } }), get: vi.fn(), create: vi.fn(), createWithCv: vi.fn(), update: vi.fn(), remove: vi.fn(), uploadCv: vi.fn(), uploadJobSpec: vi.fn() },
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

const renderLanding = (onNavigate = vi.fn()) => render(<LandingPage onNavigate={onNavigate} />);
const renderDemo = (onNavigate = vi.fn()) => render(<DemoExperiencePage onNavigate={onNavigate} />);

describe('Demo flow redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    window.scrollTo = vi.fn();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    // The landing renders variant G (motion + prefers-reduced-motion reads),
    // which needs matchMedia; jsdom doesn't implement it. Stub it as "no
    // reduced-motion preference" so the scene renders its animated path.
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    assessments.requestDemo.mockResolvedValue({ data: { success: true, candidate_id: 321 } });
  });

  it('wires the landing CTAs to the same marketing targets as before', async () => {
    const onNavigate = vi.fn();
    renderLanding(onNavigate);

    // "Sign in" → the login page. It appears in both the shared MarketingNav
    // header and the footer, each a real <a href> (PageLink; rendered outside a
    // Router in this test, it falls back to a plain anchor). Every one resolves
    // to /login.
    const signIn = screen.getAllByRole('link', { name: /Sign in/i });
    expect(signIn.length).toBeGreaterThan(0);
    signIn.forEach((link) => expect(link).toHaveAttribute('href', '/login'));

    // "See it live" (hero + closing band) → the live product walkthrough
    // (the old landing's "Try the live walkthrough" target).
    fireEvent.click(screen.getAllByRole('button', { name: /See it live/i })[0]);
    expect(onNavigate).toHaveBeenCalledWith('showcase');

    // "Book a demo" (hero + closing band) → the demo-lead intake, unchanged.
    fireEvent.click(screen.getAllByRole('button', { name: /^Book a demo$/i })[0]);
    expect(onNavigate).toHaveBeenCalledWith('demo-lead');
  });

  it('renders the landing (hero + agent scene, variant G funnel + 5-Ds, closing CTA) with no preview chip', async () => {
    const { container } = renderLanding();

    // The production landing, NOT variant G's scoped `.lvg` shell. The only
    // scoped subtrees are `.lvg-scene` (the grafted hero AgentScene) and the
    // `.mc-vg` funnel + 5-Ds bands grafted from variant G.
    expect(container.querySelector('.lvg')).toBeNull();
    expect(container.querySelector('.lvg-scene')).toBeTruthy();
    expect(container.querySelectorAll('.mc-vg').length).toBe(2);
    // Production entrances use the shared Motion system; the legacy CSS
    // reveal classes and per-card delay indexes are no longer present.
    expect(container.querySelectorAll('[data-motion-reveal]').length).toBeGreaterThanOrEqual(5);
    expect(container.querySelector('[data-motion-stagger="agent-funnel"]')).toBeTruthy();
    expect(container.querySelector('.reveal, .reveal-stagger')).toBeNull();
    // The internal preview switcher chip lives only on /landing-preview.
    expect(screen.queryByRole('group', { name: /Landing preview variant/i })).toBeNull();

    // Grafted hero copy: eyebrow + refined H1 (purple split) + the two CTAs.
    expect(screen.getByText(/AGENT-NATIVE HIRING/i)).toBeInTheDocument();
    expect(screen.getByText(/The hiring agent that screens, assesses, and/i)).toBeInTheDocument();
    expect(screen.getByText(/decides — with you\./i)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /See it live/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /^Book a demo$/i }).length).toBeGreaterThan(0);

    // Variant G's 5-step funnel replaces the old 3-step + decision-feed band.
    expect(screen.getByText(/your whole funnel\./i)).toBeInTheDocument();
    ['Source', 'Screen', 'Assess', 'Decide', 'Hand back'].forEach((step) => {
      expect(screen.getByText(step)).toBeInTheDocument();
    });
    // No leftover decision-feed / 3-step copy.
    expect(screen.queryByText(/HOW THE AGENT WORKS/i)).toBeNull();
    expect(screen.queryByText(/Triage — autonomously/i)).toBeNull();

    // The single assessment section — variant G's 5-Ds scorecard (Delegation /
    // Description / Discernment / Diligence / Deliverable), one section only.
    expect(screen.getByText(/actually work with AI\./i)).toBeInTheDocument();
    ['Delegation', 'Description', 'Discernment', 'Diligence', 'Deliverable'].forEach((d) => {
      expect(screen.getByText(d)).toBeInTheDocument();
    });
    // The IDE walkthrough band is gone.
    expect(screen.queryByText(/Candidates work here\./i)).toBeNull();

    // The closing CTA is intact.
    expect(screen.getByText(/Ready to put the agent to work\?/i)).toBeInTheDocument();

    // Maya Chen threads the design — the hero decision lane and the 5-Ds card.
    expect(screen.getAllByText(/Maya Chen/i).length).toBeGreaterThan(0);
  });

  it('keeps the theme toggle button in demo navigation', async () => {
    renderDemo();
    expect(screen.getByRole('button', { name: 'Toggle theme' })).toBeInTheDocument();
  });

  it('shows intake validation before a demo request can be saved', async () => {
    renderDemo();

    fireEvent.click(screen.getByRole('button', { name: /Open walkthrough/i }));

    await waitFor(() => {
      expect(screen.getByText(/Please complete:/i)).toBeInTheDocument();
      expect(assessments.requestDemo).not.toHaveBeenCalled();
    });
  });

  it('trims the shared marketing nav to Developers / Sign in / Book a demo', async () => {
    renderDemo();

    // The only marketing-nav destinations now are Developers, Sign in, and
    // Book a demo — the old "How it works" section jump is gone.
    expect(screen.queryByRole('button', { name: 'How it works' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'How it works' })).toBeNull();

    const developers = screen.getAllByRole('link', { name: /^Developers$/i });
    expect(developers.length).toBeGreaterThan(0);
    developers.forEach((link) => expect(link).toHaveAttribute('href', '/developers'));

    expect(screen.getAllByRole('link', { name: /Sign in/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: /Book a demo/i }).length).toBeGreaterThan(0);
  });

  it('gates the walkthrough on /demo until details are submitted', async () => {
    renderDemo();

    expect(screen.getByRole('heading', { level: 1, name: /See Taali/i })).toBeInTheDocument();
    expect(screen.getByText(/No setup, no fake data screens/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 2, name: /Try the five things/i })).not.toBeInTheDocument();
    expect(screen.queryByTitle(/^Jobs you’re hiring for$/i)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/^Candidates flowing in$/i)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/^Ask about your candidates$/i)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/^Client-share profile$/i)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/^Candidate workspace$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Demo' })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Track/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Start my session/i })).not.toBeInTheDocument();
  });

  it('saves the demo request and unlocks the actual product walkthrough', async () => {
    const onNavigate = vi.fn();
    renderDemo(onNavigate);

    fireEvent.change(screen.getByLabelText(/^Full name$/i), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByLabelText(/^Position$/i), { target: { value: 'Engineering Manager' } });
    fireEvent.change(screen.getByLabelText(/^Work email$/i), { target: { value: 'jane@company.com' } });
    fireEvent.change(screen.getByLabelText(/^Company$/i), { target: { value: 'Acme' } });
    fireEvent.click(screen.getByLabelText(/^Company size$/i));
    fireEvent.click(await screen.findByRole('option', { name: '51–200' }));

    fireEvent.click(screen.getByRole('button', { name: /Open walkthrough/i }));

    await waitFor(() => {
      expect(assessments.requestDemo).toHaveBeenCalledWith(
        expect.objectContaining({
          full_name: 'Jane Doe',
          email: 'jane@company.com',
          work_email: 'jane@company.com',
          company_name: 'Acme',
          company_size: '51–200',
        }),
      );
    });

    expect(screen.getByRole('heading', { level: 2, name: /Try the five things/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Jobs you’re hiring for/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Candidates flowing in/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Ask about your candidates/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Client-share profile/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Candidate workspace/i })).toBeInTheDocument();

    const jobsFrame = screen.getByTitle('Jobs you’re hiring for');
    const candidatesFrame = screen.getByTitle('Candidates flowing in');
    const chatFrame = screen.getByTitle('Ask about your candidates');
    const profileFrame = screen.getByTitle('Client-share profile');
    const workspaceFrame = screen.getByTitle('Candidate workspace');

    expect(jobsFrame).toHaveAttribute('src', '/jobs?demo=1&showcase=1');
    expect(candidatesFrame).toHaveAttribute('src', '/candidates?demo=1&showcase=1');
    expect(chatFrame).toHaveAttribute('src', '/showcase/chat');
    expect(profileFrame).toHaveAttribute('src', '/c/demo?view=client&k=demo-token&showcase=1');
    expect(workspaceFrame).toHaveAttribute('src', '/assessment/live?demo=1&showcase=1');

    [jobsFrame, candidatesFrame, chatFrame, profileFrame, workspaceFrame].forEach((frame) => {
      expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-same-origin');
    });

    expect(screen.getAllByText('Locked preview')).toHaveLength(5);
    expect(screen.queryByText(/Open full size/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Platform' })).not.toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalledWith('candidate-welcome', expect.anything());
  });

  it('orders showcase panes in the hiring narrative', async () => {
    renderDemo();

    fireEvent.change(screen.getByLabelText(/^Full name$/i), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByLabelText(/^Work email$/i), { target: { value: 'jane@company.com' } });
    fireEvent.change(screen.getByLabelText(/^Company$/i), { target: { value: 'Acme' } });
    fireEvent.click(screen.getByLabelText(/^Company size$/i));
    fireEvent.click(await screen.findByRole('option', { name: '51–200' }));

    fireEvent.click(screen.getByRole('button', { name: /Open walkthrough/i }));

    const tablist = await screen.findByRole('tablist', { name: /Walkthrough views/i });
    const tabButtons = Array.from(tablist.querySelectorAll('button')).map((button) => button.textContent || '');
    expect(tabButtons.length).toBe(5);
    expect(tabButtons[0]).toMatch(/Jobs/);
    expect(tabButtons[1]).toMatch(/Candidates/);
    expect(tabButtons[2]).toMatch(/Ask about your candidates/);
    expect(tabButtons[3]).toMatch(/Candidate workspace/);
    expect(tabButtons[4]).toMatch(/Client-share profile/);
  });
});
