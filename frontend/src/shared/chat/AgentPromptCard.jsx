import { useId, useRef, useState } from 'react';
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  CircleHelp,
  Sparkles,
  X,
} from 'lucide-react';

import {
  MOTION_EASE,
  MotionDisclosure,
  PresenceSwap,
  m,
  motionTransition,
  useReducedMotionSync,
} from '../motion';
import { Button, Input, Textarea } from '../ui/TaaliPrimitives.jsx';
import { ChatMarkdown } from './ChatMarkdown';
import { safeInternalRoute } from './safeInternalRoute';
import './AgentPromptCard.css';

const PROMPT_TITLES = {
  candidate_tie_break: 'Choose who to prioritise',
  confirm_material_change: 'Review the role change',
  cv_unreadable: 'Make these CVs readable',
  intent_clarification: 'Clarify the role brief',
  intent_slot_missing: 'Complete the role brief',
  missing_cv: 'Add the missing CVs',
  missing_job_spec: 'Add the job description',
  monthly_budget_missing: 'Set an agent budget',
  task_assignment_missing: 'Choose an assessment task',
  threshold_ambiguous: 'Set the screening threshold',
};

const PROMPT_ASSISTS = {
  cv_unreadable: {
    label: 'Ask agent to review',
    prompt: 'Show me the candidates whose CVs could not be read and what I can do for each.',
  },
  missing_cv: {
    label: 'Ask agent to review',
    prompt: 'Show me the candidates who are missing a CV and what I can do for each.',
  },
  missing_job_spec: {
    label: 'Ask agent for help',
    prompt: 'Help me add the missing job specification for this role.',
  },
  monthly_budget_missing: {
    label: 'Ask agent for help',
    prompt: 'Help me review and set the monthly agent budget.',
  },
  task_assignment_missing: {
    label: 'Ask agent for help',
    prompt: 'Help me choose or create an assessment task for this role.',
  },
};

export const agentPromptTitle = (item = {}) => (
  item.title || PROMPT_TITLES[item.question_kind || item.kind] || 'Choose the next step'
);

const promptAssistFor = (item) => PROMPT_ASSISTS[item.question_kind || item.kind] || {
  label: 'Ask agent for help',
  prompt: `Help me resolve this request: ${String(item.prompt || '').trim()}`,
};

const promptStatus = ({ answered, autoResolved, dismissed, position, total }) => {
  if (autoResolved) return 'Setup completed';
  if (answered) return 'Direction received';
  if (dismissed) return 'Dismissed';
  if (position && total > 1) return `Waiting on you · ${position} of ${total}`;
  return 'Waiting on you';
};

/**
 * Shared, role-agent request card used by both Home → Agent Chat and Chat →
 * Agents. It owns its styles so a cold direct load of either surface cannot
 * accidentally render an unstyled Home-only component.
 */
export function AgentPromptCard({
  item,
  onAnswer,
  onDismiss,
  onPrompt,
  onReply,
  position,
  total,
  extraActions = null,
  detailOnly = false,
}) {
  const headingId = useId();
  const promptId = useId();
  const rationaleId = useId();
  const reduced = useReducedMotionSync();
  const [pendingAction, setPendingAction] = useState(null);
  const [answer, setAnswer] = useState('');
  const [showRationale, setShowRationale] = useState(false);
  const [inlineError, setInlineError] = useState('');

  const busy = pendingAction !== null;
  const answered = item.status === 'answered' || item.status === 'resolved';
  const autoResolved = answered && (
    item.response?.auto_resolved === true || item.response?.value === 'auto_resolved'
  );
  const dismissed = item.status === 'dismissed';
  const schema = item.response_schema || {};
  const valueSchema = schema?.properties?.value || schema;
  const options = Array.isArray(item.options) ? item.options : [];
  const questionKind = item.question_kind || item.kind;
  const requestId = item.needs_input_id ?? item.id;
  const expectedVersion = item.role_version;
  const inputMode = item.input_mode
    || (['integer', 'number'].includes(valueSchema.type) ? valueSchema.type : 'string');
  const canAnswer = item.can_answer !== false;
  const canDismiss = item.can_dismiss !== false;
  const acceptsTypedAnswer = canAnswer && (
    !options.length || inputMode === 'option_or_number'
  );
  const title = agentPromptTitle(item);
  const linkHref = safeInternalRoute(schema.link_url || item.link_url);
  const linkLabel = schema.link_label || item.link_label || 'Open settings';
  const hasOptions = options.length > 0;
  const promptAssist = !hasOptions && !acceptsTypedAnswer ? promptAssistFor(item) : null;
  const isLongForm = inputMode === 'string'
    && ['intent_slot_missing', 'intent_clarification'].includes(questionKind);
  const stateKey = answered ? 'answered' : dismissed ? 'dismissed' : 'open';
  // Persisted history often includes requests that were resolved long before
  // this transcript opened. Only make the receipt live when an already-mounted
  // open request resolves; otherwise opening history would replay old status.
  const initialStateRef = useRef(stateKey);
  const announceReceipt = initialStateRef.current === 'open' && stateKey !== 'open';

  const choose = async (option) => {
    if (busy) return;
    const actionKey = `option:${String(option.value)}`;
    setInlineError('');
    setPendingAction(actionKey);
    try {
      const answerArgs = [
        requestId,
        { value: option.value, label: option.label },
      ];
      if (expectedVersion != null) answerArgs.push(expectedVersion);
      const saved = await onAnswer?.(...answerArgs);
      if (saved === false) setInlineError('That answer was not saved. Try again.');
    } catch {
      setInlineError('That answer was not saved. Try again.');
    } finally {
      setPendingAction(null);
    }
  };

  const submitTypedAnswer = async (event) => {
    event.preventDefault();
    if (busy || String(answer).trim() === '') return;
    const value = ['integer', 'number', 'option_or_number'].includes(inputMode)
      ? Number(answer)
      : answer.trim();
    if (typeof value === 'number' && !Number.isFinite(value)) return;
    setInlineError('');
    setPendingAction('typed-answer');
    try {
      const answerArgs = [requestId, { value }];
      if (expectedVersion != null) answerArgs.push(expectedVersion);
      const saved = await onAnswer?.(...answerArgs);
      // Never eat the recruiter's text after a transient failure.
      if (saved === false) {
        setInlineError('That answer was not saved. Try again.');
      } else {
        setAnswer('');
      }
    } catch {
      setInlineError('That answer was not saved. Try again.');
    } finally {
      setPendingAction(null);
    }
  };

  const dismiss = async () => {
    if (busy) return;
    setInlineError('');
    setPendingAction('dismiss');
    try {
      const dismissedRequest = await onDismiss?.(requestId);
      if (dismissedRequest === false) setInlineError('This request was not dismissed. Try again.');
    } catch {
      setInlineError('This request was not dismissed. Try again.');
    } finally {
      setPendingAction(null);
    }
  };

  const statusText = promptStatus({ answered, autoResolved, dismissed, position, total });

  return (
    <article
      className={`tk-agent-prompt${detailOnly ? ' is-detail-only' : ''}`}
      aria-labelledby={detailOnly ? undefined : headingId}
      aria-label={detailOnly ? title : undefined}
      aria-describedby={promptId}
      aria-busy={busy}
      data-status={stateKey}
      data-needs-input-id={requestId}
      tabIndex={-1}
    >
      {!detailOnly ? (
        <>
          <span className="tk-agent-prompt-accent" aria-hidden="true" />
          <header className="tk-agent-prompt-head">
            <span className="tk-agent-prompt-icon" aria-hidden="true">
              <CircleHelp size={17} />
            </span>
            <span className="tk-agent-prompt-heading-copy">
              <span className="tk-agent-prompt-eyebrow">
                <span className="tk-agent-prompt-status-dot" aria-hidden="true" />
                {statusText}
              </span>
              <h3 id={headingId} className="tk-agent-prompt-title">{title}</h3>
            </span>
            {!answered && !dismissed && canDismiss ? (
              <Button
                className="tk-agent-prompt-skip"
                variant="ghost"
                size="sm"
                iconOnly
                loading={pendingAction === 'dismiss'}
                disabled={busy}
                aria-label={pendingAction === 'dismiss' ? 'Dismissing request' : 'Dismiss request'}
                title="Dismiss request"
                onClick={dismiss}
              >
                {pendingAction === 'dismiss' ? null : <X size={15} />}
              </Button>
            ) : null}
          </header>
        </>
      ) : null}

      <div id={promptId} className="tk-agent-prompt-copy">
        <ChatMarkdown>{item.prompt}</ChatMarkdown>
      </div>

      {item.rationale ? (
        <div className="tk-agent-prompt-rationale-shell">
          <button
            type="button"
            className="tk-agent-prompt-rationale-trigger"
            aria-expanded={showRationale}
            aria-controls={rationaleId}
            onClick={() => setShowRationale((open) => !open)}
          >
            Why this is needed
            <m.span
              className="tk-agent-prompt-rationale-chevron"
              aria-hidden="true"
              animate={{ rotate: showRationale ? 180 : 0 }}
              transition={reduced ? motionTransition.instant : motionTransition.fast}
            >
              <ChevronDown size={14} />
            </m.span>
          </button>
          <MotionDisclosure open={showRationale} id={rationaleId}>
            <p className="tk-agent-prompt-rationale">{item.rationale}</p>
          </MotionDisclosure>
        </div>
      ) : null}

      <PresenceSwap presenceKey={stateKey} className="tk-agent-prompt-state">
        {answered ? (
          <div
            className="tk-agent-prompt-receipt"
            role={announceReceipt ? 'status' : undefined}
            aria-live={announceReceipt ? 'polite' : undefined}
          >
            <m.span
              className="tk-agent-prompt-receipt-icon"
              aria-hidden="true"
              initial={reduced ? false : { opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={reduced ? motionTransition.instant : {
                ...motionTransition.base,
                ease: MOTION_EASE.confirm,
              }}
            >
              <Check size={14} />
            </m.span>
            <span>
              <strong>{autoResolved ? 'Setup detected.' : 'Direction received.'}</strong>{' '}
              The blocker is cleared.
            </span>
          </div>
        ) : dismissed ? (
          <div
            className="tk-agent-prompt-receipt is-muted"
            role={announceReceipt ? 'status' : undefined}
            aria-live={announceReceipt ? 'polite' : undefined}
          >
            <span><strong>Request dismissed.</strong> No answer was sent.</span>
          </div>
        ) : (
          <>
            <div
              className={`tk-agent-prompt-actions ${linkHref ? 'has-direct-action' : ''}`}
              role="group"
              aria-label={`Respond to ${title}`}
            >
              {options.map((option) => {
                const actionKey = `option:${String(option.value)}`;
                return (
                  <Button
                    key={option.value}
                    className="tk-agent-prompt-choice"
                    variant="soft"
                    size="sm"
                    loading={pendingAction === actionKey}
                    disabled={busy}
                    onClick={() => choose(option)}
                  >
                    {option.label}
                  </Button>
                );
              })}

              {acceptsTypedAnswer && onReply ? (
                <Button
                  className="tk-agent-prompt-reply"
                  variant={hasOptions ? 'soft' : 'primary'}
                  size="sm"
                  disabled={busy}
                  onClick={() => onReply(item)}
                >
                  {hasOptions ? 'Something else…' : 'Reply in chat'} <ArrowRight size={13} />
                </Button>
              ) : acceptsTypedAnswer ? (
                <form className="tk-agent-prompt-answer" onSubmit={submitTypedAnswer}>
                  {isLongForm ? (
                    <Textarea
                      aria-label="Answer the agent"
                      className="tk-agent-prompt-input"
                      rows={3}
                      placeholder="Type your answer"
                      value={answer}
                      disabled={busy}
                      onChange={(event) => setAnswer(event.target.value)}
                    />
                  ) : (
                    <Input
                      aria-label="Answer the agent"
                      className="tk-agent-prompt-input"
                      type={['integer', 'number', 'option_or_number'].includes(inputMode) ? 'number' : 'text'}
                      min={valueSchema.minimum}
                      max={valueSchema.maximum}
                      step={inputMode === 'integer' || inputMode === 'option_or_number' ? 1 : valueSchema.multipleOf || 'any'}
                      placeholder={inputMode === 'option_or_number' ? 'Or enter a number' : 'Type your answer'}
                      value={answer}
                      disabled={busy}
                      onChange={(event) => setAnswer(event.target.value)}
                    />
                  )}
                  <Button
                    variant="primary"
                    size="sm"
                    loading={pendingAction === 'typed-answer'}
                    loadingLabel="Saving"
                    disabled={busy || !answer.trim()}
                    type="submit"
                  >
                    Answer
                  </Button>
                </form>
              ) : null}

              {linkHref ? (
                <Button
                  as="a"
                  className="tk-agent-prompt-primary-action"
                  variant="primary"
                  size="sm"
                  href={linkHref}
                >
                  {linkLabel} <ArrowUpRight size={14} />
                </Button>
              ) : null}

              {promptAssist && onPrompt ? (
                <Button
                  className="tk-agent-prompt-assist"
                  variant={linkHref ? 'soft' : 'primary'}
                  size="sm"
                  onClick={() => onPrompt(promptAssist.prompt)}
                >
                  <Sparkles size={14} /> {promptAssist.label}
                </Button>
              ) : null}

              {extraActions}

              {detailOnly && canDismiss ? (
                <Button
                  className="tk-agent-prompt-dismiss-detail"
                  variant="ghost"
                  size="sm"
                  loading={pendingAction === 'dismiss'}
                  disabled={busy}
                  onClick={dismiss}
                >
                  Dismiss
                </Button>
              ) : null}
            </div>

            {inlineError ? (
              <p className="tk-agent-prompt-error" role="alert">{inlineError}</p>
            ) : null}
            <p className="tk-agent-prompt-footnote">
              Resolve this to unblock the agent’s next cycle.
            </p>
          </>
        )}
      </PresenceSwap>
    </article>
  );
}

/** A proactive, non-blocking prompt. Quick replies only prefill the composer. */
export function AgentHelperPromptCard({ card, onPrompt, detailOnly = false }) {
  const headingId = useId();
  const suggestions = Array.isArray(card?.suggestions) ? card.suggestions : [];
  const priorityLabel = card?.priority === 'attention' ? 'Needs attention' : 'Suggestion';

  return (
    <section
      className={`tk-agent-helper${detailOnly ? ' is-detail-only' : ''}`}
      data-testid="helper-prompt"
      data-priority={card?.priority || 'suggestion'}
      aria-labelledby={detailOnly ? undefined : headingId}
      aria-label={detailOnly ? (card?.title || 'Suggested next step') : undefined}
    >
      {!detailOnly ? (
        <header className="tk-agent-helper-head">
          <span className="tk-agent-helper-icon" aria-hidden="true"><Sparkles size={16} /></span>
          <span className="tk-agent-helper-heading-copy">
            <span className="tk-agent-helper-eyebrow">Agent suggestion</span>
            <h3 id={headingId} className="tk-agent-helper-title">
              {card?.title || 'Suggested next step'}
            </h3>
          </span>
          <span className="tk-agent-helper-priority">{priorityLabel}</span>
        </header>
      ) : null}

      {card?.summary ? <p className="tk-agent-helper-summary">{card.summary}</p> : null}
      {card?.question ? <p className="tk-agent-helper-question">{card.question}</p> : null}

      {suggestions.length > 0 ? (
        <div className="tk-agent-helper-actions" role="group" aria-label="Suggested replies">
          {suggestions.map((suggestion, index) => {
            const prompt = String(suggestion?.prompt || '').trim();
            const label = String(suggestion?.label || prompt).trim();
            if (!prompt || !label) return null;
            return (
              <Button
                key={`${label}-${index}`}
                className="tk-agent-helper-action"
                variant="soft"
                size="sm"
                onClick={() => onPrompt?.(prompt)}
              >
                {label} <ArrowRight size={13} />
              </Button>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

export default AgentPromptCard;
