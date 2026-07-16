import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

const openAgentFeed = () => {
  fireEvent.click(screen.getByRole('tab', { name: /Agent feed/ }));
};

const expandFeedRow = (title) => {
  fireEvent.click(screen.getByText(title).closest('button'));
};

describe('AgentConversation decisions', () => {
  it('keeps candidate decisions out of Chat and exposes a compact feed reference', async () => {
    renderConversation();

    expect(await screen.findByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true');
    const chatPanel = screen.getByRole('tabpanel', { name: 'Chat' });
    expect(within(chatPanel).queryByText('Lena Ortiz · Assessment recommended')).not.toBeInTheDocument();
    expect(document.querySelector('.rq-hybrid-detail')).not.toBeInTheDocument();
    expect(mocks.listDecisions).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: 'Agent feed' }));
    expect(await screen.findByText('1 candidate decision ready')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }));
    const rowTitle = await screen.findByText('Lena Ortiz · Assessment recommended');
    expect(screen.queryByText('Strong match across the must-have criteria.')).not.toBeInTheDocument();
    fireEvent.click(rowTitle.closest('button'));

    expect(screen.getByText('Strong match across the must-have criteria.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Review in queue' })).toHaveAttribute(
      'href',
      '/home?role=4&pending=21',
    );
    expect(document.querySelector('.rq-hybrid-detail')).not.toBeInTheDocument();
    expect(mocks.listDecisions).not.toHaveBeenCalled();
  });

  it('returns to Chat when the recruiter submits while browsing Agent feed', async () => {
    mocks.sendMessage.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'sent-from-feed',
          author: 'recruiter',
          text: 'Follow up on the queue',
        }],
        agent_working: true,
      },
    });
    renderConversation();
    await screen.findByRole('tab', { name: 'Chat' });
    openAgentFeed();
    const composer = screen.getByRole('textbox', { name: 'Chat message' });
    fireEvent.change(composer, { target: { value: 'Follow up on the queue' } });
    fireEvent.click(screen.getByRole('button', { name: 'send' }));

    expect(screen.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalledWith(4, 'Follow up on the queue'));
  });
});

describe('AgentConversation proactive helper behavior', () => {
  it('renders grounded candidate evidence and its shareable report in Agent Chat', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'message',
          id: 'grounded-shortlist',
          author: 'agent',
          message_kind: 'chat',
          text: 'I ranked these against the requested evidence.',
          actions: [{
            type: 'candidate_evidence',
            shown: 1,
            evidence_model: 'grounder-v1',
            database_matches: 1,
            criteria_requested: ['Platform ownership'],
            criteria_checked: ['Platform ownership'],
            criteria_unchecked: [],
            deep_checked: 1,
            evidence_succeeded: 1,
            qualified: 1,
            capped: false,
            report_url: '/report/agent-grounded',
            candidates: [{
              application_id: 77,
              rank: 1,
              candidate_name: 'Lena Ortiz',
              criteria: [{
                criterion: 'Platform ownership',
                status: 'met',
                grounded: true,
                evidence: [{ quote: 'Owned the platform roadmap and launch.', source: 'cv' }],
              }],
            }],
          }],
        }],
        agent_working: false,
      },
    });
    renderConversation();

    expect(await screen.findByText(/Owned the platform roadmap and launch/)).toBeInTheDocument();
    expect(screen.getByText(/grounded vs CV \+ notes/)).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open shareable grounded candidate report' }),
    ).toHaveAttribute('href', '/report/agent-grounded');
  });

  it('answers a free-form request through reply mode and restores the saved draft', async () => {
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'needs_input',
          id: 'request-31',
          needs_input_id: 31,
          status: 'open',
          prompt: 'Which region should I prioritise?',
          input_mode: 'string',
          can_answer: true,
          can_dismiss: false,
        }],
        agent_working: false,
      },
    });
    renderConversation();

    const composer = await screen.findByRole('textbox', { name: 'Chat message' });
    fireEvent.change(composer, { target: { value: 'Keep this draft for later' } });
    openAgentFeed();
    expandFeedRow('Choose the next step');
    fireEvent.click(screen.getByRole('button', { name: 'Reply in chat' }));

    const answerBox = screen.getByRole('textbox', { name: 'Answer the agent' });
    expect(answerBox).toHaveFocus();
    expect(answerBox).toHaveValue('');
    fireEvent.change(answerBox, { target: { value: 'Prioritise MENA' } });
    fireEvent.keyDown(answerBox, { key: 'Enter' });

    await waitFor(() => {
      expect(mocks.answerNeedsInput).toHaveBeenCalledWith(31, { value: 'Prioritise MENA' });
    });
    expect(mocks.sendMessage).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox', { name: 'Chat message' }))
      .toHaveValue('Keep this draft for later');
  });

  it('does not let a slow reply completion erase a draft restored by cancel', async () => {
    let finishAnswer;
    mocks.answerNeedsInput.mockImplementation(() => new Promise((resolve) => {
      finishAnswer = resolve;
    }));
    mocks.getTimeline.mockResolvedValue({
      data: {
        timeline: [{
          kind: 'needs_input',
          id: 'request-32',
          needs_input_id: 32,
          status: 'open',
          prompt: 'Which region should I prioritise?',
          input_mode: 'string',
          can_answer: true,
          can_dismiss: false,
        }],
        agent_working: false,
      },
    });
    renderConversation();

    const composer = await screen.findByRole('textbox', { name: 'Chat message' });
    fireEvent.change(composer, { target: { value: 'Keep this draft' } });
    openAgentFeed();
    expandFeedRow('Choose the next step');
    fireEvent.click(screen.getByRole('button', { name: 'Reply in chat' }));
    const answerBox = screen.getByRole('textbox', { name: 'Answer the agent' });
    fireEvent.change(answerBox, { target: { value: 'Prioritise MENA' } });
    fireEvent.keyDown(answerBox, { key: 'Enter' });

    await waitFor(() => expect(mocks.answerNeedsInput).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Cancel reply and restore draft' }));
    expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveValue('Keep this draft');

    finishAnswer({ data: {} });
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Chat message' })).not.toBeDisabled();
    });
    expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveValue('Keep this draft');
  });

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

    await screen.findByRole('tab', { name: /Agent feed/ });
    openAgentFeed();
    expandFeedRow('Review the close calls');
    fireEvent.click(screen.getByRole('button', { name: 'Show me' }));

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

    await screen.findByRole('tab', { name: /Agent feed/ });
    expect(within(screen.getByRole('tabpanel', { name: 'Chat' }))
      .queryByText('The assessment window is nearly over')).not.toBeInTheDocument();
    openAgentFeed();
    expandFeedRow('The assessment window is nearly over');
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

    await screen.findByRole('tab', { name: /Agent feed/ });
    openAgentFeed();
    expandFeedRow('Choose the next step');
    fireEvent.click(await screen.findByRole('button', { name: 'MENA' }));

    expect(await screen.findByText('Direction received.')).toBeInTheDocument();
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
    const updateStatus = document.querySelector('.tk-new-update-status');
    expect(updateStatus).toBeEmptyDOMElement();

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
    expect(updateStatus).toHaveTextContent('New agent update');
    expect(notice).toHaveAttribute('aria-controls', scroll.id);

    fireEvent.click(notice);
    expect(scroll.scrollTop).toBe(1400);
    expect(updateStatus).toBeEmptyDOMElement();
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
