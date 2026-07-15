import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  ListChecks,
  MessageCircle,
  PanelTop,
  Play,
  RotateCcw,
  Send,
  Sparkles,
  X,
} from 'lucide-react';

import {
  AgentLoop,
  MotionAttentionBadge,
  MotionChatItem,
  MotionDisclosure,
  MotionStagger,
  MotionTab,
  MotionTabs,
  PresenceSwap,
  m,
  motionTransition,
  useReducedMotionSync,
} from '../../shared/motion';
import './AgentPromptPreviewPage.css';

const DEFAULT_VARIANT = 'a';

const CONCEPTS = {
  a: {
    id: 'a',
    number: '01',
    name: 'Conversation turn',
    short: 'Inline',
    icon: MessageCircle,
    thesis: 'Make the request feel like dialogue, not a notification card.',
    bestFor: 'Everyday questions and one-to-three quick choices.',
    watchOut: 'Important blockers can scroll out of view in a long thread.',
    motion: ['Message rises 4px', 'Actions stagger 35ms', 'Answer morphs to receipt'],
    verdict: 'Best default',
  },
  b: {
    id: 'b',
    number: '02',
    name: 'Needs-you tray',
    short: 'Pinned tray',
    icon: PanelTop,
    thesis: 'Keep one genuine blocker visible without turning the transcript into an alert feed.',
    bestFor: 'Multiple unresolved blockers and time-sensitive decisions.',
    watchOut: 'Too heavy for suggestions; reserve it for work that is truly paused.',
    motion: ['Tray enters from header', 'Prompt swaps horizontally', 'Final answer collapses tray'],
    verdict: 'Best for blockers',
  },
  c: {
    id: 'c',
    number: '03',
    name: 'Composer reply mode',
    short: 'Composer',
    icon: Send,
    thesis: 'Turn the composer into the answer control so the whole workflow stays chat-native.',
    bestFor: 'Typed answers, quick replies, and the end-to-end chat vision.',
    watchOut: 'Needs a persistent pending count so older requests remain discoverable.',
    motion: ['Composer grows with layout spring', 'Choices reveal in sequence', 'Draft returns after send'],
    verdict: 'Best expression of the vision',
  },
  d: {
    id: 'd',
    number: '04',
    name: 'Compact run ledger',
    short: 'Ledger',
    icon: ListChecks,
    thesis: 'Treat warnings and tool runs as compact history, not oversized chat messages.',
    bestFor: 'Run failures, tool calls, retries, and auditable system events.',
    watchOut: 'Not the right surface for nuanced questions or multi-field decisions.',
    motion: ['Rows arrive once', 'Duplicate count acknowledges once', 'Details expand in place'],
    verdict: 'Best for operational events',
  },
};

const SCENARIO = {
  prompt: 'That run stopped before finishing. The 6 decisions already made are safe. Should I retry the unfinished work now?',
  summary: 'I screened 145 candidates and saved 6 decisions before the cycle stopped.',
  details: 'The worker restarted while processing the remaining candidates. No decisions were lost, and the retry will continue from the last saved candidate.',
};

function ActionButton({ children, primary = false, onClick, disabled = false, icon: Icon }) {
  const reduced = useReducedMotionSync();
  return (
    <m.button
      type="button"
      className={`apc-action${primary ? ' is-primary' : ''}`}
      disabled={disabled}
      onClick={onClick}
      whileTap={reduced || disabled ? undefined : { scale: 0.98 }}
      transition={motionTransition.fast}
    >
      {Icon ? <Icon size={14} aria-hidden="true" /> : null}
      {children}
    </m.button>
  );
}

function WorkingGlyph() {
  return (
    <span className="apc-working-glyph" aria-hidden="true">
      <Sparkles size={14} />
      <AgentLoop kind="ring" className="apc-working-ring" />
    </span>
  );
}

function ResolutionState({ phase, onRetry, onExplain, detailsOpen, className = '' }) {
  return (
    <PresenceSwap
      presenceKey={phase}
      className={`apc-resolution-state ${className}`.trim()}
      aria-live="polite"
    >
      {phase === 'open' ? (
        <>
          <MotionStagger className="apc-actions" step={0.035}>
            <ActionButton primary icon={Play} onClick={onRetry}>Retry unfinished work</ActionButton>
            <ActionButton onClick={onExplain}>Explain stop</ActionButton>
          </MotionStagger>
          <button
            type="button"
            className="apc-details-link"
            aria-expanded={detailsOpen}
            onClick={onExplain}
          >
            Details <ChevronDown size={12} aria-hidden="true" />
          </button>
        </>
      ) : phase === 'working' ? (
        <span className="apc-working-copy" role="status">
          <WorkingGlyph />
          Retrying unfinished work…
        </span>
      ) : (
        <span className="apc-receipt" role="status">
          <span className="apc-receipt-check" aria-hidden="true"><Check size={13} /></span>
          Retry started · I’ll continue from the last saved candidate.
        </span>
      )}
    </PresenceSwap>
  );
}

function Details({ open, id }) {
  return (
    <MotionDisclosure open={open} id={id} className="apc-details">
      <p>{SCENARIO.details}</p>
    </MotionDisclosure>
  );
}

function AgentIdentity({ label = 'Agent', time = 'now' }) {
  return (
    <div className="apc-message-meta">
      <span className="apc-agent-avatar" aria-hidden="true"><Sparkles size={12} /></span>
      <strong>{label}</strong>
      <span>· {time}</span>
    </div>
  );
}

function PriorContext() {
  return (
    <div className="apc-prior-context">
      <div className="apc-user-bubble">Keep the existing decisions and tell me if anything blocks the rest.</div>
      <div className="apc-agent-note">
        <AgentIdentity time="19:28" />
        <p>{SCENARIO.summary}</p>
      </div>
    </div>
  );
}

function ConversationTurn({ phase, onRetry, detailsOpen, onExplain }) {
  return (
    <>
      <PriorContext />
      <MotionChatItem as="article" className="apc-inline-turn" aria-labelledby="apc-inline-title">
        <AgentIdentity />
        <p id="apc-inline-title" className="apc-question-copy">{SCENARIO.prompt}</p>
        <div className="apc-inline-shelf">
          <ResolutionState
            phase={phase}
            onRetry={onRetry}
            onExplain={onExplain}
            detailsOpen={detailsOpen}
          />
          <Details open={detailsOpen && phase === 'open'} id="apc-inline-details" />
        </div>
      </MotionChatItem>
    </>
  );
}

function NeedsYouTray({ phase, onRetry, detailsOpen, onExplain }) {
  const reduced = useReducedMotionSync();
  return (
    <m.section
      className="apc-focus-tray"
      aria-labelledby="apc-tray-title"
      initial={reduced ? false : { opacity: 0, y: -8, height: 0 }}
      animate={{ opacity: 1, y: 0, height: 'auto' }}
      transition={reduced ? motionTransition.instant : motionTransition.spatial}
    >
      <div className="apc-focus-tray-head">
        <span className="apc-needs-label"><span aria-hidden="true" /> Needs you · 1 of 2</span>
        <div className="apc-pips" aria-label="Request 1 of 2"><i className="is-active" /><i /></div>
      </div>
      <p id="apc-tray-title">Retry the unfinished work? Your 6 saved decisions are safe.</p>
      <ResolutionState
        phase={phase}
        onRetry={onRetry}
        onExplain={onExplain}
        detailsOpen={detailsOpen}
        className="apc-tray-state"
      />
      <Details open={detailsOpen && phase === 'open'} id="apc-tray-details" />
    </m.section>
  );
}

function TrayTranscript() {
  return (
    <>
      <PriorContext />
      <MotionChatItem className="apc-request-anchor">
        <span className="apc-request-anchor-icon" aria-hidden="true"><CircleHelp size={13} /></span>
        <span><strong>Agent paused for your decision</strong><small>Open request is pinned above</small></span>
        <ArrowRight size={13} aria-hidden="true" />
      </MotionChatItem>
    </>
  );
}

function ComposerReply({ phase, onRetry, detailsOpen, onExplain }) {
  const reduced = useReducedMotionSync();
  return (
    <m.div
      className="apc-composer apc-composer-reply"
      layout={reduced ? false : true}
      transition={reduced ? motionTransition.instant : motionTransition.layout}
    >
      <div className="apc-reply-context">
        <span><CircleHelp size={13} aria-hidden="true" /> Replying to agent</span>
        <button type="button" aria-label="Close reply mode"><X size={13} /></button>
      </div>
      <p>What should I do with the unfinished work?</p>
      <PresenceSwap presenceKey={phase} className="apc-composer-state" aria-live="polite">
        {phase === 'open' ? (
          <>
            <MotionStagger className="apc-reply-options" step={0.035}>
              <ActionButton primary icon={Play} onClick={onRetry}>Retry unfinished work</ActionButton>
              <ActionButton onClick={onExplain}>Explain first</ActionButton>
            </MotionStagger>
            <div className="apc-reply-input-row">
              <input aria-label="Write a different answer" placeholder="Or write a different answer…" />
              <button type="button" aria-label="Send answer" disabled><Send size={14} /></button>
            </div>
          </>
        ) : phase === 'working' ? (
          <span className="apc-working-copy" role="status"><WorkingGlyph /> Sending your direction…</span>
        ) : (
          <span className="apc-receipt" role="status">
            <span className="apc-receipt-check" aria-hidden="true"><Check size={13} /></span>
            Sent · Retry unfinished work
          </span>
        )}
      </PresenceSwap>
      <Details open={detailsOpen && phase === 'open'} id="apc-composer-details" />
    </m.div>
  );
}

function ComposerTranscript({ phase }) {
  return (
    <>
      <PriorContext />
      <MotionChatItem as="article" className="apc-composer-turn">
        <AgentIdentity />
        <p>{SCENARIO.prompt}</p>
        <span className="apc-answer-chip"><Send size={11} aria-hidden="true" /> Answering below</span>
      </MotionChatItem>
      {phase === 'resolved' ? (
        <MotionChatItem className="apc-user-bubble apc-user-bubble-reply">
          Retry the unfinished work.
        </MotionChatItem>
      ) : null}
    </>
  );
}

function LedgerResolutionState({ phase, onRetry, detailsOpen, onExplain }) {
  const reduced = useReducedMotionSync();
  return (
    <PresenceSwap presenceKey={phase} className="apc-ledger-state" aria-live="polite">
      {phase === 'open' ? (
        <div className="apc-ledger-inline-actions">
          <m.button
            type="button"
            className="is-primary"
            onClick={onRetry}
            whileTap={reduced ? undefined : { scale: 0.98 }}
            transition={motionTransition.fast}
          >
            <Play size={12} aria-hidden="true" /> Retry unfinished work
          </m.button>
          <button type="button" aria-expanded={detailsOpen} onClick={onExplain}>
            Why it stopped <ChevronDown size={11} aria-hidden="true" />
          </button>
        </div>
      ) : phase === 'working' ? (
        <span className="apc-ledger-working" role="status">
          <WorkingGlyph /> Retrying from the last saved candidate…
        </span>
      ) : (
        <span className="apc-ledger-receipt" role="status">
          <span aria-hidden="true"><Check size={11} /></span>
          Retry started · continuing from the last saved candidate
        </span>
      )}
    </PresenceSwap>
  );
}

function RunLedger({ phase, onRetry, detailsOpen, onExplain }) {
  return (
    <>
      <PriorContext />
      <div className="apc-ledger-label"><span>Run history</span><span>Today</span></div>
      <div className="apc-ledger-list" role="list" aria-label="Agent run history">
        <MotionChatItem
          as="article"
          className="apc-ledger-row is-warning"
          aria-labelledby="apc-ledger-title"
          role="listitem"
        >
          <span className="apc-ledger-rail" aria-hidden="true"><AlertTriangle size={13} /></span>
          <div className="apc-ledger-main">
            <div className="apc-ledger-title-row">
              <div>
                <strong id="apc-ledger-title">Run stopped</strong>
                <span>Cycle &#35;7042 · 6 decisions retained</span>
              </div>
              <time><Clock3 size={11} aria-hidden="true" /> 19:30</time>
            </div>
            <LedgerResolutionState
              phase={phase}
              onRetry={onRetry}
              detailsOpen={detailsOpen}
              onExplain={onExplain}
            />
            <Details open={detailsOpen && phase === 'open'} id="apc-ledger-details" />
          </div>
        </MotionChatItem>
        <MotionChatItem className="apc-ledger-row is-grouped" role="listitem">
          <span className="apc-ledger-dot" aria-hidden="true" />
          <div className="apc-ledger-main apc-ledger-group-copy">
            <span><strong>Similar stops grouped</strong><small>Latest two events · decisions retained</small></span>
            <MotionAttentionBadge value={2} className="apc-ledger-count" />
          </div>
        </MotionChatItem>
        <div className="apc-ledger-row is-success" role="listitem">
          <span className="apc-ledger-rail" aria-hidden="true"><Check size={12} /></span>
          <div className="apc-ledger-main apc-ledger-success-copy">
            <span><strong>Assessment batch sent</strong><small>8 candidate invitations delivered</small></span>
            <time>18:42</time>
          </div>
        </div>
      </div>
    </>
  );
}

function StandardComposer({ disabled = false }) {
  return (
    <div className="apc-composer apc-composer-standard">
      <div className="apc-composer-input">
        <span>{disabled ? 'Agent is working…' : 'Message the AI Engineer agent…'}</span>
        <button type="button" aria-label="Send message" disabled><Send size={14} /></button>
      </div>
      <small>Enter to send · Shift + Enter for newline</small>
    </div>
  );
}

function DockHeader({ pending }) {
  return (
    <header className="apc-dock-head">
      <span className="apc-dock-title"><MessageCircle size={17} aria-hidden="true" /> Ask the agent</span>
      <span className="apc-role-pill">AI Engineer</span>
      {pending > 0 ? (
        <span className="apc-dock-count" aria-label={`${pending} request${pending === 1 ? '' : 's'} waiting`}>
          <CircleHelp size={13} aria-hidden="true" />
          <MotionAttentionBadge value={pending} />
        </span>
      ) : null}
    </header>
  );
}

function ConceptDock({ variant }) {
  const [phase, setPhase] = useState('open');
  const [detailsOpen, setDetailsOpen] = useState(false);

  useEffect(() => {
    if (phase !== 'working') return undefined;
    const timer = window.setTimeout(() => setPhase('resolved'), 1100);
    return () => window.clearTimeout(timer);
  }, [phase]);

  const retry = () => {
    setDetailsOpen(false);
    setPhase('working');
  };
  const explain = () => setDetailsOpen((open) => !open);
  const pending = phase === 'resolved' ? (variant === 'b' ? 1 : 0) : (variant === 'b' ? 2 : 1);

  return (
    <aside className="apc-chat-dock" aria-label={`${CONCEPTS[variant].name} chat mockup`}>
      <DockHeader pending={pending} />
      {variant === 'b' ? (
        <NeedsYouTray
          phase={phase}
          onRetry={retry}
          detailsOpen={detailsOpen}
          onExplain={explain}
        />
      ) : null}
      <div className={`apc-transcript is-${variant}`}>
        {variant === 'a' ? (
          <ConversationTurn
            phase={phase}
            onRetry={retry}
            detailsOpen={detailsOpen}
            onExplain={explain}
          />
        ) : variant === 'b' ? (
          <TrayTranscript />
        ) : variant === 'c' ? (
          <ComposerTranscript phase={phase} />
        ) : (
          <RunLedger
            phase={phase}
            onRetry={retry}
            detailsOpen={detailsOpen}
            onExplain={explain}
          />
        )}
      </div>
      {variant === 'c' ? (
        <ComposerReply
          phase={phase}
          onRetry={retry}
          detailsOpen={detailsOpen}
          onExplain={explain}
        />
      ) : (
        <StandardComposer disabled={phase === 'working'} />
      )}
    </aside>
  );
}

function WorkspaceContext() {
  return (
    <div className="apc-workspace-context" aria-hidden="true">
      <div className="apc-context-head">
        <div><span>Now · needs you</span><strong>Review queue.</strong></div>
        <span className="apc-context-paused"><i /> Agent paused</span>
      </div>
      <div className="apc-context-stats">
        <div><span>Applied</span><strong>145</strong></div>
        <div><span>Scored</span><strong>138</strong></div>
        <div><span>Invited</span><strong>8</strong></div>
        <div><span>Advanced</span><strong>8</strong></div>
      </div>
      <div className="apc-context-grid">
        <div className="apc-context-roles">
          <span>Your agents</span>
          <div className="is-selected"><i /> <b>AI Engineer</b><small>6 decisions waiting</small></div>
          <div><i /> <b>Platform Engineer</b><small>Agent on</small></div>
          <div><i /> <b>Data Engineer</b><small>Agent paused</small></div>
        </div>
        <div className="apc-context-candidate">
          <div className="apc-context-candidate-head"><span>DK</span><div><strong>Dinesh Kumar</strong><small>AI Engineer · score 72</small></div></div>
          <div className="apc-context-score"><span>72</span><div><b>Assessment recommended</b><small>Clears 4 of 5 must-haves</small></div></div>
          <p>Strong ML platform experience with clear production ownership. Knowledge graph depth still needs verification.</p>
          <button type="button">Send assessment</button>
        </div>
      </div>
    </div>
  );
}

function ConceptNotes({ concept }) {
  return (
    <section className="apc-notes" aria-label={`${concept.name} design notes`}>
      <article>
        <span>Core idea</span>
        <h2>{concept.thesis}</h2>
      </article>
      <article>
        <span>Best for</span>
        <p>{concept.bestFor}</p>
      </article>
      <article>
        <span>Watch out</span>
        <p>{concept.watchOut}</p>
      </article>
      <article className="apc-motion-notes">
        <span>Motion choreography</span>
        <ul>
          {concept.motion.map((note) => <li key={note}>{note}</li>)}
        </ul>
      </article>
    </section>
  );
}

export function AgentPromptPreviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const reduced = useReducedMotionSync();
  const [replay, setReplay] = useState(0);
  const requested = (searchParams.get('v') || DEFAULT_VARIANT).toLowerCase();
  const active = CONCEPTS[requested] ? requested : DEFAULT_VARIANT;
  const concept = CONCEPTS[active];

  const pick = (variant) => {
    const next = new URLSearchParams(searchParams);
    next.set('v', variant);
    setSearchParams(next, { replace: true });
    setReplay((value) => value + 1);
  };

  return (
    <main className="apc-lab">
      <header className="apc-lab-head">
        <div>
          <p className="apc-kicker"><Sparkles size={13} aria-hidden="true" /> Interaction study · Motion.dev</p>
          <h1>How should an agent ask for help?</h1>
          <p>Four structural directions, one identical scenario, shown at the real chat-dock density.</p>
        </div>
        <div className="apc-lab-actions">
          <span>{reduced ? 'Reduced motion on' : 'Motion on'}</span>
          <button type="button" onClick={() => setReplay((value) => value + 1)}>
            <RotateCcw size={14} aria-hidden="true" /> Replay motion
          </button>
        </div>
      </header>

      <MotionTabs
        value={active}
        onValueChange={pick}
        className="apc-concept-tabs"
        aria-label="Agent prompt design direction"
      >
        {Object.values(CONCEPTS).map((item) => {
          const Icon = item.icon;
          return (
            <MotionTab
              key={item.id}
              value={item.id}
              className={active === item.id ? 'is-active' : ''}
              indicatorClassName="apc-tab-indicator"
            >
              <span className="apc-tab-number">{item.number}</span>
              <span className="apc-tab-icon"><Icon size={15} aria-hidden="true" /></span>
              <span><strong>{item.name}</strong><small>{item.verdict}</small></span>
            </MotionTab>
          );
        })}
      </MotionTabs>

      <section className="apc-stage" aria-labelledby="apc-stage-title">
        <div className="apc-stage-head">
          <div>
            <span>Direction {concept.number}</span>
            <h2 id="apc-stage-title">{concept.name}</h2>
          </div>
          <span className="apc-stage-verdict">{concept.verdict}</span>
        </div>
        <PresenceSwap presenceKey={`${active}-${replay}`} className="apc-preview-swap">
          <div className="apc-workspace">
            <WorkspaceContext />
            <ConceptDock key={`${active}-${replay}`} variant={active} />
          </div>
        </PresenceSwap>
      </section>

      <ConceptNotes concept={concept} />

      <aside className="apc-recommendation">
        <span className="apc-recommendation-icon" aria-hidden="true"><Sparkles size={17} /></span>
        <div>
          <span>My recommendation</span>
          <strong>Use a small system, not one universal card.</strong>
          <p>Conversation turn for normal asks, composer mode for typed replies, the tray only for real blockers, and the ledger for run/tool history.</p>
        </div>
      </aside>
    </main>
  );
}

export default AgentPromptPreviewPage;
