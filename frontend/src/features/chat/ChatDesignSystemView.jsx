import { useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CircleCheck,
  Clock3,
  FileCheck2,
  ListChecks,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  WandSparkles,
} from 'lucide-react';

import {
  AgentFeedTimeline,
  AgentHelperPromptCard,
  AgentStreamTabs,
  ChatActivity,
  ChatArtifact,
  ChatComposer,
  ChatMessage,
  ChatSurface,
  NewMessageNotice,
  agentFeedAttentionCount,
} from '../../shared/chat';
import {
  MotionChatItem,
  MotionLoop,
  MotionStagger,
  useReducedMotionSync,
} from '../../shared/motion';
import { Button } from '../../shared/ui/TaaliPrimitives.jsx';
import CandidateEvidenceCard from './CandidateEvidenceCard';
import './ChatDesignSystemView.css';

const noop = () => {};

const GROUNDED_SHORTLIST = {
  type: 'candidate_evidence',
  spec: {
    echo: 'owned a production GenAI launch · Postgres in production',
    ranking_key: 'taali',
  },
  rank_by: 'taali',
  shown: 2,
  total_matched: 18,
  database_matches: 18,
  criteria_requested: [
    'owned a production GenAI launch',
    'Postgres in production',
  ],
  criteria_checked: [
    'owned a production GenAI launch',
    'Postgres in production',
  ],
  criteria_unchecked: [],
  deep_checked: 18,
  evidence_succeeded: 18,
  qualified: 2,
  capped: false,
  // Production-shaped, routable specimen URL; unlike a hash placeholder it
  // exercises the same public report route the real tool result returns.
  report_url: '/report/design-system-grounded',
  excluded: {
    not_met_total: 16,
    by_criterion: [
      { criterion: 'owned a production GenAI launch', count: 11 },
      { criterion: 'Postgres in production', count: 5 },
    ],
  },
  warnings: [],
  candidates: [
    {
      application_id: 'specimen-priya',
      rank: 1,
      candidate_name: 'Priya Raman',
      candidate_position: 'Senior AI Engineer',
      candidate_location: 'London, UK',
      role_name: 'Senior Backend Engineer',
      taali_score: 84,
      meets_all_criteria: true,
      criteria: [
        {
          criterion: 'owned a production GenAI launch',
          status: 'met',
          grounded: true,
          evidence: [{
            quote: 'Led the patient-summarisation GenAI rollout across two NHS trusts; owned the evaluation harness, retrieval grounding and release gate end to end.',
            source: 'cv',
          }],
        },
        {
          criterion: 'Postgres in production',
          status: 'met',
          grounded: true,
          evidence: [{
            quote: 'Designed the Postgres schema and partitioning for the clinical audit store processing approximately 14 million rows each day.',
            source: 'cv',
          }],
        },
      ],
    },
    {
      application_id: 'specimen-daniel',
      rank: 2,
      candidate_name: 'Daniel Okafor',
      candidate_position: 'Staff Engineer',
      candidate_location: 'Lagos, NG',
      role_name: 'Senior Backend Engineer',
      taali_score: 78,
      meets_all_criteria: true,
      criteria: [
        {
          criterion: 'owned a production GenAI launch',
          status: 'met',
          grounded: true,
          evidence: [{
            quote: 'Took the Ledger support assistant from prototype to GA for 40,000 users and owned the final go or no-go gate.',
            source: 'cv',
          }],
        },
        {
          criterion: 'Postgres in production',
          status: 'met',
          grounded: true,
          evidence: [{
            quote: 'Ran the primary Postgres fleet at Wander, including logical replication, point-in-time recovery and a zero-downtime major-version upgrade.',
            source: 'notes',
          }],
        },
      ],
    },
  ],
};

const INITIAL_REQUEST = {
  id: 'design-system-invite',
  needs_input_id: 'design-system-invite',
  kind: 'needs_input',
  question_kind: 'candidate_tie_break',
  status: 'open',
  title: 'Choose who to invite',
  prompt:
    'Priya and Daniel clear every must-have. Should I invite both to the assessment, or widen the shortlist before taking action?',
  rationale:
    'Invitations contact candidates, so the agent pauses for an explicit steer before sending them.',
  options: [
    { value: 'invite', label: 'Invite both' },
    { value: 'widen', label: 'Widen to top 5' },
  ],
  can_answer: true,
  can_dismiss: true,
};

const HELPER_CARD = {
  title: 'The shortlist is ready to move',
  summary: 'Two candidates clear every must-have with cited evidence.',
  question: 'Would you like a side-by-side comparison before deciding?',
  priority: 'suggestion',
  suggestions: [
    {
      label: 'Compare the two',
      prompt: 'Compare Priya and Daniel side by side, including evidence and risks.',
    },
    {
      label: 'Draft interview focus',
      prompt: 'Draft tailored interview focus areas for Priya and Daniel.',
    },
  ],
};

const FEED_HELPER_ITEM = {
  kind: 'message',
  id: 'design-system-helper',
  author: 'agent',
  message_kind: 'proactive',
  created_at: '2026-07-16T10:20:00Z',
  actions: [{ type: 'helper_prompt', ...HELPER_CARD }],
};

const FEED_DECISION_ITEM = {
  kind: 'decision',
  id: 'design-system-decision',
  decision_id: 7421,
  role_id: 109,
  candidate_name: 'Maya Chen',
  recommendation: 'advance',
  score: 88,
  status: 'pending',
  reasoning: 'Maya clears all six must-haves with cited evidence and leads this role’s current pipeline.',
  created_at: '2026-07-16T10:18:00Z',
};

const FEED_ERROR_ITEM = {
  kind: 'message',
  id: 'design-system-run-error',
  author: 'agent',
  message_kind: 'event',
  created_at: '2026-07-16T10:26:00Z',
  actions: [{
    type: 'agent_event',
    severity: 'error',
    event_type: 'agent_run_terminal',
    title: 'Agent run stopped before completion',
    summary: 'The cycle ended early. Six decisions were retained and unfinished work can retry safely.',
    occurred_at: '2026-07-16T10:26:00Z',
    details: [
      { label: 'Agent run', value: 'Run 7042' },
      { label: 'Work retained', value: '6 decisions' },
    ],
    suggestions: [
      {
        label: 'Explain stop',
        prompt: 'Explain why agent run 7042 stopped and what is safe to retry.',
      },
      {
        label: 'Preview retry',
        prompt: 'Preview the unfinished work from agent run 7042 before retrying it.',
      },
    ],
  }],
};

const FeedActionSpecimen = ({ card, onPrompt, detailOnly = false }) => {
  if (card?.type === 'helper_prompt') {
    return <AgentHelperPromptCard card={card} onPrompt={onPrompt} detailOnly={detailOnly} />;
  }

  if (card?.type !== 'agent_event') return null;
  const eventType = String(card.event_type || 'Agent update').replace(/[_-]+/g, ' ');
  return (
    <ChatActivity
      severity={card.severity || 'info'}
      severityLabel={card.severity === 'error' ? 'Error' : 'Update'}
      typeLabel={eventType.charAt(0).toUpperCase() + eventType.slice(1)}
      title={card.title}
      summary={card.summary}
      icon={AlertTriangle}
      timestamp={{ label: '10:26', dateTime: card.occurred_at }}
      details={card.details}
      detailOnly={detailOnly}
      actions={(card.suggestions || []).map((suggestion) => ({
        label: suggestion.label,
        onClick: () => onPrompt(suggestion.prompt),
      }))}
    />
  );
};

const SectionHeading = ({ id, eyebrow, title, description, actions }) => (
  <header className="cds-section-head">
    <div>
      <span className="cds-eyebrow">{eyebrow}</span>
      <h2 id={id}>{title}</h2>
      {description ? <p>{description}</p> : null}
    </div>
    {actions ? <div className="cds-section-actions">{actions}</div> : null}
  </header>
);

const SpecimenLabel = ({ children, detail }) => (
  <div className="cds-specimen-label">
    <span>{children}</span>
    {detail ? <small>{detail}</small> : null}
  </div>
);

const DensitySample = ({ density, title }) => (
  <figure className="cds-density-sample">
    <figcaption>
      <strong>{title}</strong>
      <span>{density === 'compact' ? 'Narrow agent dock' : 'Primary conversation'}</span>
    </figcaption>
    <ChatSurface density={density} tone="agent" className="cds-density-chat">
      <ChatMessage
        role="assistant"
        label="Taali agent"
        time="2026-07-16T10:24:00Z"
        text="I found **2 candidates** who clear the must-haves."
      />
      <ChatActivity
        severity="success"
        severityLabel="Complete"
        typeLabel="Evidence check"
        title="18 candidates checked"
        icon={CircleCheck}
      />
    </ChatSurface>
  </figure>
);

export const ChatDesignSystemView = () => {
  const [density, setDensity] = useState('comfortable');
  const [request, setRequest] = useState(INITIAL_REQUEST);
  const [streamView, setStreamView] = useState('feed');
  const [draft, setDraft] = useState('');
  const [noticeVisible, setNoticeVisible] = useState(true);
  const [replyMode, setReplyMode] = useState(false);
  const [sentMessage, setSentMessage] = useState('');
  const reducedMotion = useReducedMotionSync();

  const prefillComposer = (prompt) => {
    setDraft(prompt);
    setReplyMode(false);
  };

  const prefillFromFeed = (prompt) => {
    setStreamView('chat');
    prefillComposer(prompt);
  };

  const answerRequest = async (_requestId, response) => {
    setRequest((current) => ({ ...current, status: 'answered', response }));
    return true;
  };

  const dismissRequest = async () => {
    setRequest((current) => ({ ...current, status: 'dismissed' }));
    return true;
  };

  const submitMessage = (message) => {
    setSentMessage(message);
    setDraft('');
    setReplyMode(false);
  };

  const feedItems = [FEED_DECISION_ITEM, FEED_HELPER_ITEM, request, FEED_ERROR_ITEM];

  return (
    <main className="cds-page" aria-labelledby="chat-design-system-title">
      <header className="cds-page-head">
        <div className="cds-page-intro">
          <span className="cds-page-kicker"><Sparkles size={13} /> Product language</span>
          <h1 id="chat-design-system-title">Chat design system</h1>
          <p>
            A living reference for how Taali asks, explains, acts and proves its work across
            Search, Agent Chat and the Home dock.
          </p>
        </div>
        <div className="cds-page-meta" aria-label="Design system status">
          <span><ShieldCheck size={14} /> Canonical primitives</span>
          <span>Motion-aware</span>
          <span>Responsive</span>
        </div>
      </header>

      <div className="cds-toolbar" aria-label="Specimen controls">
        <div>
          <span className="cds-toolbar-label">Preview density</span>
          <div className="cds-segmented" role="group" aria-label="Preview density">
            <button
              type="button"
              aria-pressed={density === 'comfortable'}
              onClick={() => setDensity('comfortable')}
            >
              Comfortable
            </button>
            <button
              type="button"
              aria-pressed={density === 'compact'}
              onClick={() => setDensity('compact')}
            >
              Compact
            </button>
          </div>
        </div>
        <p>
          The anatomy stays fixed. Density changes rhythm, type and padding—not interaction
          meaning.
        </p>
      </div>

      <MotionStagger as="div" className="cds-board" distance={8}>
        <section className="cds-section cds-span-7" aria-labelledby="message-anatomy-title">
          <SectionHeading
            id="message-anatomy-title"
            eyebrow="01 · Conversation"
            title="Message anatomy"
            description="Chat holds direct dialogue and the work caused by the current instruction. Autonomous updates wait in Agent Feed."
          />
          <ChatSurface density={density} tone="agent" className="cds-surface cds-transcript">
            <SpecimenLabel detail="right aligned · concise">Recruiter turn</SpecimenLabel>
            <MotionChatItem initial={false}>
              <ChatMessage
                role="user"
                time="2026-07-16T10:21:00Z"
                text="Find the top 2 backend candidates with a shipped GenAI product and production Postgres evidence."
              />
            </MotionChatItem>

            <SpecimenLabel detail="attributed · grounded language">Assistant turn</SpecimenLabel>
            <MotionChatItem initial={false}>
              <ChatMessage
                role="assistant"
                label="Taali agent"
                time="2026-07-16T10:21:08Z"
                text="I’m treating both requirements as hard filters. I’ll quote the supporting evidence and leave anything I can’t verify visibly unconfirmed."
              />
            </MotionChatItem>

            <SpecimenLabel detail="direct causality · stays with request">Requested activity</SpecimenLabel>
            <MotionChatItem initial={false}>
              <ChatActivity
                severity="info"
                severityLabel="Running"
                typeLabel="Search candidates"
                title="Checking 18 profiles"
                summary="Matching the two hard filters and verifying each claim against CVs and notes."
                icon={({ size }) => (
                  <MotionLoop kind="spin" aria-label="Search in progress">
                    <LoaderCircle size={size} />
                  </MotionLoop>
                )}
                timestamp={{ label: 'now', dateTime: '2026-07-16T10:21:10Z' }}
              />
            </MotionChatItem>
          </ChatSurface>
        </section>

        <section className="cds-section cds-span-5" aria-labelledby="artifact-anatomy-title">
          <SectionHeading
            id="artifact-anatomy-title"
            eyebrow="02 · Evidence"
            title="Artifact anatomy"
            description="Structured work stays inside the answer, with status, provenance and a durable report."
          />
          <ChatSurface density={density} className="cds-surface cds-artifact-surface">
            <SpecimenLabel detail="status · evidence · share">Grounded Top-X result</SpecimenLabel>
            <ChatMessage
              role="assistant"
              label="Taali"
              time="2026-07-16T10:23:00Z"
              text="Two candidates clear both requirements. Every verdict below links the claim to its source."
            >
              <CandidateEvidenceCard data={GROUNDED_SHORTLIST} />
            </ChatMessage>
          </ChatSurface>
        </section>

        <section className="cds-section cds-span-7" aria-labelledby="agent-lanes-title">
          <SectionHeading
            id="agent-lanes-title"
            eyebrow="03 · Lanes"
            title="Conversation and Agent Feed"
            description="Background warnings, steers and review-queue decisions collapse to one line. They never interrupt the active conversation."
            actions={request.status !== 'open' ? (
              <Button size="sm" variant="ghost" onClick={() => setRequest(INITIAL_REQUEST)}>
                Reset steer
              </Button>
            ) : null}
          />
          <ChatSurface density={density} tone="agent" className="cds-surface cds-lane-demo">
            <AgentStreamTabs
              value={streamView}
              onChange={setStreamView}
              attentionCount={agentFeedAttentionCount(feedItems)}
              chatPanelId="design-system-chat-panel"
              feedPanelId="design-system-feed-panel"
            />
            <div className="cds-lane-panels">
              <div
                className="cds-lane-panel"
                id="design-system-chat-panel"
                role="tabpanel"
                aria-label="Chat"
                hidden={streamView !== 'chat'}
              >
                <SpecimenLabel detail="active request · direct consequence">Chat lane</SpecimenLabel>
                <ChatMessage
                  role="user"
                  time="2026-07-16T10:24:00Z"
                  text="Invite Priya and Daniel to the backend systems assessment."
                />
                <ChatActivity
                  severity="info"
                  severityLabel="Preparing"
                  typeLabel="Invitation preview"
                  title="Checking recipients and assessment"
                  summary="This status stays here because it was caused by the current instruction."
                  icon={({ size }) => (
                    <MotionLoop kind="spin" aria-label="Invitation preview in progress">
                      <LoaderCircle size={size} />
                    </MotionLoop>
                  )}
                  timestamp={{ label: 'now', dateTime: '2026-07-16T10:24:02Z' }}
                />
                <ChatArtifact
                  icon={ListChecks}
                  eyebrow="Action preview"
                  title="Invite 2 candidates"
                  summary="Priya Raman and Daniel Okafor"
                  meta="Assessment · Backend systems exercise"
                  status={{ label: 'Ready to review', tone: 'info' }}
                  footer={(
                    <div className="cds-artifact-actions" role="group" aria-label="Review invitation action">
                      <Button variant="primary" size="sm" onClick={() => prefillComposer('Invite Priya and Daniel to the backend systems assessment.')}>
                        Review in composer <ArrowRight size={13} />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={noop}>Dismiss</Button>
                    </div>
                  )}
                >
                  <dl className="cds-action-summary">
                    <div><dt>Recipients</dt><dd>2 candidates</dd></div>
                    <div><dt>External effect</dt><dd>Sends email invitations</dd></div>
                    <div><dt>Approval</dt><dd>Required before send</dd></div>
                  </dl>
                </ChatArtifact>
                <ChatActivity
                  severity="success"
                  severityLabel="Sent"
                  typeLabel="Action receipt"
                  title="Assessment invitations delivered"
                  summary="Priya Raman and Daniel Okafor were invited by Sam Patel."
                  source={{ label: '2 candidate records updated' }}
                  timestamp={{ label: '10:25', dateTime: '2026-07-16T10:25:00Z' }}
                  icon={FileCheck2}
                  details={[
                    { label: 'Assessment', value: 'Backend systems exercise' },
                    { label: 'Actor', value: 'Sam Patel' },
                  ]}
                />
                <div className="cds-awareness-demo">
                  <SpecimenLabel detail="direct replies only · never feed events">New-message awareness</SpecimenLabel>
                  <div className="cds-notice-stage">
                    <NewMessageNotice
                      visible={noticeVisible}
                      label="1 new agent reply"
                      className="cds-specimen-notice"
                      controls="design-system-chat-panel"
                      onClick={() => setNoticeVisible(false)}
                    />
                    {!noticeVisible ? (
                      <Button size="sm" variant="ghost" onClick={() => setNoticeVisible(true)}>
                        Show notice again
                      </Button>
                    ) : null}
                  </div>
                </div>
              </div>
              <div
                className="cds-lane-panel cds-feed-panel"
                id="design-system-feed-panel"
                role="tabpanel"
                aria-label="Agent feed"
                hidden={streamView !== 'feed'}
              >
                <AgentFeedTimeline
                  items={feedItems}
                  roleId={109}
                  roleName="Senior Backend Engineer"
                  openQuestionPositions={new Map([['design-system-invite', 1]])}
                  openQuestionCount={request.status === 'open' ? 1 : 0}
                  onAnswer={answerRequest}
                  onDismiss={dismissRequest}
                  onPrompt={prefillFromFeed}
                  onReply={() => {
                    setStreamView('chat');
                    setReplyMode(true);
                  }}
                  renderAction={(card, _actionIndex, _item, options = {}) => (
                    <FeedActionSpecimen
                      card={card}
                      detailOnly={Boolean(options.detailOnly)}
                      onPrompt={prefillFromFeed}
                    />
                  )}
                />
              </div>
            </div>
          </ChatSurface>
        </section>

        <section className="cds-section cds-span-5" aria-labelledby="composer-title">
          <SectionHeading
            id="composer-title"
            eyebrow="04 · Input"
            title="Composer"
            description="One input model for new instructions, contextual replies, voice and streaming control."
            actions={(
              <div className="cds-mini-toggle" role="group" aria-label="Composer mode">
                <button type="button" aria-pressed={!replyMode} onClick={() => setReplyMode(false)}>Standard</button>
                <button type="button" aria-pressed={replyMode} onClick={() => setReplyMode(true)}>Reply</button>
              </div>
            )}
          />
          <ChatSurface density={density} tone="agent" className="cds-surface cds-composer-demo">
            <div className="cds-composer-context" id="design-system-transcript">
              <MessageSquareText size={17} aria-hidden="true" />
              <span>
                {replyMode
                  ? 'Answering the agent’s invitation question'
                  : 'Starting a new instruction'}
              </span>
            </div>
            <ChatComposer
              value={draft}
              onChange={setDraft}
              onSubmit={submitMessage}
              placeholder="Ask about candidates, roles or work to do…"
              voice
              replyTo={replyMode ? {
                label: 'Reply to agent',
                prompt: INITIAL_REQUEST.prompt,
              } : null}
              onCancelReply={() => setReplyMode(false)}
            />
            <span className="cds-sent-status" role="status">
              {sentMessage ? `Sent in specimen: “${sentMessage}”` : ''}
            </span>
          </ChatSurface>
        </section>

        <section className="cds-section cds-span-7" aria-labelledby="density-title">
          <SectionHeading
            id="density-title"
            eyebrow="05 · Rhythm"
            title="Density"
            description="The same semantics adapt to a full workspace or a narrow embedded dock."
          />
          <div className="cds-density-grid">
            <DensitySample density="comfortable" title="Primary workspace" />
            <DensitySample density="compact" title="Agent dock" />
          </div>
        </section>

        <section className="cds-section cds-span-5" aria-labelledby="motion-title">
          <SectionHeading
            id="motion-title"
            eyebrow="06 · Awareness"
            title="Motion semantics"
            description="Motion explains arrival and change. It never decorates settled history."
          />
          <div className="cds-motion-status" data-reduced={reducedMotion ? 'true' : 'false'}>
            <span className="cds-motion-status-icon" aria-hidden="true">
              {reducedMotion ? <Check size={15} /> : <WandSparkles size={15} />}
            </span>
            <div>
              <strong>{reducedMotion ? 'Reduced motion active' : 'Full motion active'}</strong>
              <span>
                {reducedMotion
                  ? 'Entrances settle immediately and continuous loops stop.'
                  : 'Current preference allows brief, purposeful transitions.'}
              </span>
            </div>
          </div>
          <ul className="cds-motion-list">
            <li><Search size={15} /><span><strong>Arrival</strong> Fade + 8px once, never replayed for history.</span></li>
            <li><RefreshCw size={15} /><span><strong>State change</strong> Layout continuity when a request becomes a receipt.</span></li>
            <li><Clock3 size={15} /><span><strong>Active work</strong> Loops run only while visible and genuinely processing.</span></li>
            <li><Check size={15} /><span><strong>Completion</strong> One quiet confirmation, with no celebratory bounce.</span></li>
          </ul>
        </section>
      </MotionStagger>
    </main>
  );
};

export default ChatDesignSystemView;
