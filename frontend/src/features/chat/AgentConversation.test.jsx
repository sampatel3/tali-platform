import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getTimeline: vi.fn(),
  sendMessage: vi.fn(),
  answerNeedsInput: vi.fn(),
  markRead: vi.fn(),
  listDecisions: vi.fn(),
  approveDecision: vi.fn(),
  overrideDecision: vi.fn(),
  reEvaluateDecision: vi.fn(),
  snoozeDecision: vi.fn(),
  sendFeedback: vi.fn(),
  getWorkableStages: vi.fn(),
}));

vi.mock('../../shared/api', () => ({
  agentChat: {
    getTimeline: mocks.getTimeline,
    sendMessage: mocks.sendMessage,
    answerNeedsInput: mocks.answerNeedsInput,
    dismissNeedsInput: vi.fn().mockResolvedValue({ data: {} }),
    markRead: mocks.markRead,
    approveDraftTask: vi.fn().mockResolvedValue({ data: { timeline: [] } }),
    reviseDraftTask: vi.fn().mockResolvedValue({ data: { timeline: [] } }),
  },
  agent: {
    listDecisions: mocks.listDecisions,
    approveDecision: mocks.approveDecision,
    overrideDecision: mocks.overrideDecision,
    reEvaluateDecision: mocks.reEvaluateDecision,
    snoozeDecision: mocks.snoozeDecision,
    sendFeedback: mocks.sendFeedback,
  },
  organizations: {
    getWorkableStages: mocks.getWorkableStages,
  },
}));

import { ToastProvider } from '../../context/ToastContext';
import { AgentConversation } from './AgentConversation';

const TIMELINE_DECISION = {
  kind: 'decision',
  id: 'decision-21',
  decision_id: 21,
  application_id: 77,
  decision_type: 'send_assessment',
  recommendation: 'send_assessment',
  candidate_name: 'Lena Ortiz',
  score: 82,
  confidence: 0.91,
  status: 'pending',
  reasoning: 'Strong match across the must-have criteria.',
  created_at: '2026-07-14T08:00:00Z',
};

const FULL_DECISION = {
  id: 21,
  role_id: 4,
  role_name: 'Platform Engineer',
  application_id: 77,
  decision_type: 'send_assessment',
  recommendation: 'send_assessment',
  candidate_name: 'Lena Ortiz',
  candidate_email: 'lena@example.com',
  taali_score: 82,
  confidence: 0.91,
  status: 'pending',
  reasoning: 'Strong match across the must-have criteria.',
  evidence: {},
  requirements: [],
  is_stale: false,
  staleness_reasons: [],
};

beforeEach(() => {
  mocks.getTimeline.mockReset();
  mocks.sendMessage.mockReset();
  mocks.answerNeedsInput.mockReset();
  mocks.markRead.mockReset();
  mocks.listDecisions.mockReset();
  mocks.approveDecision.mockReset();
  mocks.overrideDecision.mockReset();
  mocks.reEvaluateDecision.mockReset();
  mocks.snoozeDecision.mockReset();
  mocks.sendFeedback.mockReset();
  mocks.getWorkableStages.mockReset();
  mocks.getTimeline.mockResolvedValue({ data: { timeline: [TIMELINE_DECISION], agent_working: false } });
  mocks.answerNeedsInput.mockResolvedValue({ data: {} });
  mocks.markRead.mockResolvedValue({ data: {} });
  mocks.listDecisions.mockResolvedValue({ data: [FULL_DECISION] });
  mocks.approveDecision.mockResolvedValue({ data: { ...FULL_DECISION, status: 'processing' } });
  mocks.overrideDecision.mockResolvedValue({ data: {} });
  mocks.reEvaluateDecision.mockResolvedValue({ data: {} });
  mocks.snoozeDecision.mockResolvedValue({ data: {} });
  mocks.sendFeedback.mockResolvedValue({ data: {} });
  mocks.getWorkableStages.mockResolvedValue({ data: { stages: [] } });
});

const renderConversation = () => render(
  <ToastProvider>
    <AgentConversation roleId={4} roleName="Platform Engineer" />
  </ToastProvider>,
);

describe('AgentConversation decisions', () => {
  it('renders the canonical decision card in Chat > Agents', async () => {
    renderConversation();

    expect(await screen.findByText('Lena Ortiz')).toBeInTheDocument();
    expect(screen.getByText('Strong match across the must-have criteria.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Candidate report' })).toHaveAttribute('href', '/candidates/77?from=home');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send assessment' })).toBeEnabled();
    });
  });

  it('dispatches the recommended action through the shared agent API', async () => {
    renderConversation();

    const approve = await screen.findByRole('button', { name: 'Send assessment' });
    await waitFor(() => expect(approve).toBeEnabled());
    fireEvent.click(approve);

    await waitFor(() => {
      expect(mocks.approveDecision).toHaveBeenCalledWith(21, {}, { force: false });
    });
  });
});

describe('AgentConversation proactive helper behavior', () => {
  it('puts a helper quick reply in the composer without sending it', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'helper-message',
          author: 'agent',
          message_kind: 'proactive',
          text: 'Five candidates are close to the cut-off.\n\nWould you like to review them?',
          actions: [{
            type: 'helper_prompt',
            title: 'Review the close calls',
            summary: 'Five candidates are close to the cut-off.',
            question: 'Would you like to review them?',
            suggestions: [{ label: 'Show me', prompt: 'Show me the candidates just below the cut-off.' }],
          }],
        }],
        agent_working: false,
      },
    });
    renderConversation();

    fireEvent.click(await screen.findByRole('button', { name: 'Show me' }));

    expect(screen.getAllByText('Five candidates are close to the cut-off.')).toHaveLength(1);
    expect(screen.getAllByText('Would you like to review them?')).toHaveLength(1);
    const composer = screen.getByPlaceholderText(/Ask about this role/);
    expect(composer).toHaveValue(
      'Show me the candidates just below the cut-off.',
    );
    expect(composer).toHaveFocus();
    expect(screen.getByText('Added to composer')).toHaveAttribute('aria-live', 'polite');
    expect(mocks.sendMessage).not.toHaveBeenCalled();
  });

  it('renders a dedicated agent event once and only prefills its follow-up prompt', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'event-message',
          author: 'agent',
          message_kind: 'event',
          text: 'The assessment window is nearly over.\n\nOne candidate has not started.',
          actions: [{
            type: 'agent_event',
            event_type: 'assessment_expiring',
            severity: 'warning',
            title: 'The assessment window is nearly over',
            summary: 'One candidate has not started.',
            source: { type: 'assessment', id: 44, href: '/assessments/44' },
            occurred_at: '2026-07-15T09:00:00Z',
            suggestions: [{ label: 'Draft a reminder', prompt: 'Draft a reminder for the candidate.' }],
          }],
        }],
        agent_working: false,
      },
    });
    renderConversation();

    expect(await screen.findByRole('article', {
      name: 'Warning agent event: The assessment window is nearly over',
    })).toBeInTheDocument();
    expect(screen.getAllByText('The assessment window is nearly over')).toHaveLength(1);
    expect(screen.getAllByText('One candidate has not started.')).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'Open Assessment #44' })).toHaveAttribute(
      'href',
      '/assessments/44',
    );

    fireEvent.click(screen.getByRole('button', { name: /Draft a reminder/ }));
    expect(screen.getByPlaceholderText(/Ask about this role/)).toHaveValue(
      'Draft a reminder for the candidate.',
    );
    expect(mocks.sendMessage).not.toHaveBeenCalled();
  });

  it('reconciles an answered recruiter input when the timeline length is unchanged', async () => {
    const openQuestion = {
      kind: 'needs_input',
      id: 'needs-8',
      needs_input_id: 8,
      prompt: 'Which region should I prioritize?',
      options: [{ value: 'mena', label: 'MENA' }],
      status: 'open',
      response: null,
      resolved_at: null,
    };
    mocks.getTimeline
      .mockResolvedValueOnce({ data: { timeline: [openQuestion], agent_working: false } })
      .mockResolvedValueOnce({
        data: {
          timeline: [{
            ...openQuestion,
            status: 'answered',
            response: { value: 'mena', label: 'MENA' },
            resolved_at: '2026-07-15T08:00:00Z',
          }],
          agent_working: false,
        },
      });
    renderConversation();

    fireEvent.click(await screen.findByRole('button', { name: 'MENA' }));

    expect(await screen.findByText('Answered')).toBeInTheDocument();
    expect(mocks.answerNeedsInput).toHaveBeenCalledWith(8, { value: 'mena', label: 'MENA' });
  });

  it('keeps an unpinned reading position and offers a motion-safe jump to a new update', async () => {
    const history = {
      kind: 'message',
      id: 'history-agent',
      author: 'agent',
      text: 'Existing agent history',
      created_at: '2026-07-15T08:00:00Z',
    };
    mocks.getTimeline.mockResolvedValue({ data: { timeline: [history], agent_working: false } });
    mocks.sendMessage.mockResolvedValue({
      data: {
        timeline: [
          history,
          { kind: 'message', id: 'user-2', author: 'recruiter', text: 'What changed?' },
          { kind: 'message', id: 'agent-2', author: 'agent', text: 'A new assessment is ready.' },
        ],
        agent_working: false,
      },
    });
    renderConversation();

    expect(await screen.findByText('Existing agent history')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'New agent update' })).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toBeEmptyDOMElement();

    const scroll = document.querySelector('.cp-scroll');
    Object.defineProperties(scroll, {
      scrollHeight: { configurable: true, value: 1400 },
      clientHeight: { configurable: true, value: 500 },
      scrollTop: { configurable: true, writable: true, value: 250 },
    });
    fireEvent.scroll(scroll);

    const composer = screen.getByPlaceholderText(/Ask about this role/);
    fireEvent.change(composer, { target: { value: 'What changed?' } });
    fireEvent.keyDown(composer, { key: 'Enter' });

    expect(await screen.findByText('A new assessment is ready.')).toBeInTheDocument();
    const notice = await screen.findByRole('button', { name: 'New agent update' });
    expect(scroll.scrollTop).toBe(250);
    expect(screen.getByRole('status')).toHaveTextContent('New agent update');
    expect(notice).toHaveAttribute('aria-controls', scroll.id);

    fireEvent.click(notice);
    expect(scroll.scrollTop).toBe(1400);
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'New agent update' })).not.toBeInTheDocument();
    });
  });

  it('acknowledges the selected thread only after it has visibly settled', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: { timeline: [{ kind: 'message', id: 'ready', author: 'agent', text: 'Ready to help.' }], agent_working: false },
    });
    renderConversation();

    expect(await screen.findByText('Ready to help.')).toBeInTheDocument();
    expect(mocks.markRead).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.markRead).toHaveBeenCalledWith(4), { timeout: 1600 });
  });
});
