// Surface-specific cards the agent chat dock slots into a shared <ChatMessage>:
// impact cards (threshold/constraint), the draft-task review card, and the
// agent's clarifying-question card. The chat chrome (bubbles, composer, markdown,
// empty state) now comes from the shared kit at shared/chat. Decision cards are
// intentionally NOT rendered here in Option C — those live in the main feed.

import { useId, useState } from 'react';
import {
  ArrowUpRight,
  Check,
  CircleHelp,
  ExternalLink,
  FileText,
  GitFork,
  Sparkles,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { AgentLoop, PresenceSwap } from '../../../shared/motion';
import { Button, Input } from '../../../shared/ui/TaaliPrimitives.jsx';
import { AgentEventCard } from './AgentEventCard.jsx';
import { safeInternalRoute } from './safeInternalRoute';

const numOrDash = (v) => (typeof v === 'number' ? v : v == null ? '—' : v);

export function ImpactCard({ card, onApply, onPrompt, busy }) {
  if (!card || !card.type) return null;

  if (card.type === 'agent_event') {
    return <AgentEventCard card={card} onPrompt={onPrompt} />;
  }

  if (card.type === 'helper_prompt') {
    const suggestions = Array.isArray(card.suggestions) ? card.suggestions : [];
    return (
      <div className="ac-card ac-card-helper" data-testid="helper-prompt">
        <div className="ac-card-head">
          <Sparkles size={14} />
          <span>{card.title || 'A useful next step'}</span>
          {card.priority ? <span className="ac-helper-priority">{card.priority}</span> : null}
        </div>
        {card.summary ? <p className="ac-helper-summary">{card.summary}</p> : null}
        {card.question ? <p className="ac-helper-question">{card.question}</p> : null}
        {suggestions.length > 0 ? (
          <div className="ac-card-actions">
            {suggestions.map((suggestion, index) => {
              const prompt = String(suggestion?.prompt || '').trim();
              const label = String(suggestion?.label || prompt).trim();
              if (!prompt || !label) return null;
              return (
                <button
                  key={`${label}-${index}`}
                  type="button"
                  className="ac-btn ac-btn-soft"
                  onClick={() => onPrompt?.(prompt)}
                >
                  {label}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    );
  }

  if (card.type === 'operation_preview') {
    const preview = card.preview || {};
    const labels = {
      create_application: 'Create application',
      post_workable_note: 'Post Workable note',
      run_agent_now: 'Run agent now',
    };
    const subject = preview.candidate
      || preview.candidate_name
      || preview.candidate_email
      || (preview.application_id ? `Application ${preview.application_id}` : 'This role');
    return (
      <div className="ac-card ac-card-constraint" data-testid="operation-preview">
        <div className="ac-card-head">
          <CircleHelp size={14} />
          <span>Confirmation required</span>
        </div>
        <div className="ac-draft-title">{labels[card.operation] || card.operation}</div>
        <div className="ac-rescreen-estimate">{subject}</div>
        {preview.body_preview ? (
          <div className="ac-rescreen-estimate">“{preview.body_preview}”</div>
        ) : null}
        <div className="ac-rescreen-estimate">No action has run. Confirm in a new message.</div>
      </div>
    );
  }

  if (card.type === 'decision_action_preview') {
    const decision = card.decision || {};
    const requested = card.requested_action || {};
    const action = card.operation === 'approve_decision'
      ? 'Approve recommendation'
      : card.operation === 'override_decision'
        ? `Override → ${requested.alternative || 'alternative action'}`
        : card.operation === 'teach_decision'
          ? `Teach → ${requested.failure_mode || 'structured correction'} · ${requested.scope || 'decision'}`
          : 'Re-evaluate decision';
    return (
      <div className="ac-card ac-card-constraint" data-testid="decision-action-preview">
        <div className="ac-card-head">
          <CircleHelp size={14} />
          <span>Confirmation required</span>
        </div>
        <div className="ac-draft-title">{decision.candidate_name || `Decision ${decision.decision_id}`}</div>
        <div className="ac-rescreen-estimate">
          {action} · {decision.decision_type || decision.recommendation || 'pending decision'}
          {requested.workable_target_stage ? ` · ${requested.workable_target_stage}` : ''}
        </div>
        <div className="ac-rescreen-estimate">No action has run. Confirm in a new message.</div>
      </div>
    );
  }

  if (card.type === 'operation_receipt') {
    return (
      <div className="ac-card ac-card-applied" data-testid="operation-receipt">
        <div className="ac-card-head">
          <Check size={14} />
          <span>{card.status || 'Accepted'}</span>
        </div>
        <div className="ac-rescreen-estimate">{card.message || 'The operation was accepted.'}</div>
      </div>
    );
  }

  if (card.type === 'constraint_change') {
    const c = card.criterion || {};
    return (
      <div className="ac-card ac-card-constraint">
        <div className="ac-card-head">
          <SlidersHorizontal size={14} />
          <span>Constraint {card.action}</span>
          {card.rescreening_count > 0 && (
            <span className="ac-card-live">
              <AgentLoop kind="pulse" className="ac-pulse" /> re-screening {card.rescreening_count}
            </span>
          )}
        </div>
        {c.text && (
          <div className="ac-chip-row">
            <span className="ac-constraint-chip">{c.text}</span>
          </div>
        )}
        {card.would_rescreen && card.would_rescreen.count > 0 && (
          <div className="ac-rescreen-estimate">
            Would re-screen ~{card.would_rescreen.count} candidate{card.would_rescreen.count === 1 ? '' : 's'}
            {typeof card.would_rescreen.est_cost_usd === 'number' ? ` (~$${card.would_rescreen.est_cost_usd})` : ''} — awaiting your OK.
          </div>
        )}
      </div>
    );
  }

  if (card.type === 'job_spec_change') {
    const added = card.added || [];
    const removed = card.removed || [];
    return (
      <div className="ac-card ac-card-constraint">
        <div className="ac-card-head">
          <FileText size={14} />
          <span>Job spec updated</span>
        </div>
        {added.length > 0 && (
          <div className="ac-spec-diff">
            <span className="ac-spec-diff-label add">+ Added</span>
            <div className="ac-chip-row">
              {added.map((t, i) => <span key={`a${i}`} className="ac-constraint-chip ac-chip-add">{t}</span>)}
            </div>
          </div>
        )}
        {removed.length > 0 && (
          <div className="ac-spec-diff">
            <span className="ac-spec-diff-label remove">− Removed</span>
            <div className="ac-chip-row">
              {removed.map((t, i) => <span key={`r${i}`} className="ac-constraint-chip ac-chip-remove">{t}</span>)}
            </div>
          </div>
        )}
        {added.length === 0 && removed.length === 0 && (
          <div className="ac-rescreen-estimate">Same criteria re-derived from the new wording — no chip changes.</div>
        )}
        {card.would_rescreen && card.would_rescreen.count > 0 && (
          <div className="ac-rescreen-estimate">
            New spec re-derives every criterion — would re-screen ~{card.would_rescreen.count} candidate{card.would_rescreen.count === 1 ? '' : 's'}
            {typeof card.would_rescreen.est_cost_usd === 'number' ? ` (~$${card.would_rescreen.est_cost_usd})` : ''} — awaiting your OK.
          </div>
        )}
      </div>
    );
  }

  if (card.type === 'related_role_preview') {
    const total = card.candidates_total ?? 0;
    const scorable = card.candidates_with_cv ?? 0;
    const missing = card.candidates_missing_cv ?? 0;
    const sourceProviderLabel = String(card.ats_provider || card.source_ats_provider || '').toLowerCase() === 'bullhorn'
      ? 'Bullhorn'
      : 'Workable';
    return (
      <div className="ac-card ac-card-constraint">
        <div className="ac-card-head">
          <GitFork size={14} />
          <span>Related role preview</span>
        </div>
        <div className="ac-rescreen-estimate">
          <strong>{card.proposed_name || 'New related role'}</strong> will share {total} candidate{total === 1 ? '' : 's'} with {card.source_role_name || `the original ${sourceProviderLabel} role`}.
        </div>
        <div className="ac-statrow">
          <span><b>{scorable}</b> score now</span>
          <span><b>{missing}</b> missing CV text</span>
          {typeof card.estimated_cost_usd === 'number' ? <span><b>~${card.estimated_cost_usd}</b> estimated AI usage</span> : null}
        </div>
        <div className="ac-rescreen-estimate">
          Candidate stages and actions stay coupled to the original {sourceProviderLabel} job. Awaiting your confirmation.
        </div>
      </div>
    );
  }

  if (card.type === 'related_role_created') {
    const counts = card.evaluation_counts || {};
    return (
      <div className="ac-card ac-card-applied">
        <div className="ac-card-head">
          <Check size={14} />
          <span>Related role created</span>
        </div>
        <div className="ac-rescreen-estimate">
          <strong>{card.role_name}</strong> is scoring the shared roster now.
        </div>
        <div className="ac-statrow">
          <span><b>{counts.pending ?? 0}</b> queued</span>
          <span><b>{counts.unscorable ?? 0}</b> missing CV text</span>
        </div>
        {card.frontend_url ? (
          <div className="ac-card-actions">
            <a className="ac-btn ac-btn-soft" href={card.frontend_url}>
              Open related role <ExternalLink size={12} />
            </a>
          </div>
        ) : null}
      </div>
    );
  }

  if (card.type === 'threshold_recommendation' || card.type === 'threshold_simulation') {
    const sim = card.type === 'threshold_simulation';
    const target = sim ? card.simulated_threshold : card.recommended_threshold;
    const gain = sim ? card.delta_above : card.projected_additional;
    // Compact threshold-impact box — matches the home-preview `.impact`: a
    // purple-tint bordered card with an inline "old → new · +N candidates clear"
    // line and an Apply button beneath. No icon header / oversized numerals.
    return (
      <div className="ac-impact">
        <div className="ac-impact-line">
          <span className="ac-impact-label">Threshold</span>
          <span className="ac-impact-old">{numOrDash(card.current_threshold)}</span>
          <span className="ac-impact-arrow">→</span>
          <b className="ac-impact-new">{numOrDash(target)}</b>
          {typeof gain === 'number' && gain !== 0 && (
            <span className="ac-impact-gain">
              · {gain > 0 ? `+${gain}` : gain} candidate{Math.abs(gain) === 1 ? '' : 's'} clear the cut-off
            </span>
          )}
          {typeof gain === 'number' && gain === 0 && (
            <span className="ac-impact-gain">· no change</span>
          )}
        </div>
        {Array.isArray(card.added_sample) && card.added_sample.length > 0 && (
          <div className="ac-chip-row">
            {card.added_sample.map((n) => (
              <span key={n} className="ac-name-chip">{n}</span>
            ))}
          </div>
        )}
        {!sim && target != null && onApply && (
          <div className="ac-impact-actions">
            <button
              type="button"
              className="taali-btn taali-btn-primary taali-btn-xs ac-impact-apply"
              disabled={busy}
              onClick={() => onApply(target)}
            >
              Apply {target}
            </button>
          </div>
        )}
      </div>
    );
  }

  if (card.type === 'threshold_change') {
    return (
      <div className="ac-card ac-card-applied">
        <div className="ac-card-head">
          <Check size={14} />
          <span>Threshold applied</span>
        </div>
        <div className="ac-thresh-line">
          <span className="ac-thresh-old">{numOrDash(card.before_threshold)}</span>
          <span className="ac-arrow">→</span>
          <span className="ac-thresh-new ac-thresh-applied">{numOrDash(card.after_threshold)}</span>
        </div>
        <div className="ac-statrow">
          <span><b>{card.discarded_advances ?? 0}</b> advances retracted</span>
          <span><b>{card.created_rejects ?? 0}</b> new rejects</span>
          <span><b>{card.above_after ?? '—'}</b> clear the cut-off</span>
        </div>
      </div>
    );
  }

  return null;
}

// Claude-Code-style structured reject form: a set of questions (multi- or
// single-select) + an optional free-text note, collected in ONE round and
// submitted together — no chat back-and-forth. Driven entirely by the
// `questions` the backend defines, so the two never drift.
function RejectQuestionnaire({ questions = [], onSubmit, onCancel, busy }) {
  const [answers, setAnswers] = useState({});
  const [note, setNote] = useState('');

  const toggle = (q, value) => {
    setAnswers((prev) => {
      if (q.multi) {
        const cur = new Set(prev[q.key] || []);
        cur.has(value) ? cur.delete(value) : cur.add(value);
        return { ...prev, [q.key]: cur };
      }
      return { ...prev, [q.key]: prev[q.key] === value ? undefined : value };
    });
  };

  const isOn = (q, value) =>
    q.multi ? (answers[q.key] || new Set()).has(value) : answers[q.key] === value;

  const hasAny =
    note.trim() ||
    questions.some((q) => {
      const a = answers[q.key];
      return q.multi ? a && a.size > 0 : Boolean(a);
    });

  const submit = () => {
    const out = {};
    questions.forEach((q) => {
      const a = answers[q.key];
      if (q.multi) {
        if (a && a.size) out[q.key] = Array.from(a);
      } else if (a) {
        out[q.key] = a;
      }
    });
    onSubmit?.({ answers: out, note: note.trim() });
  };

  return (
    <div className="ac-reject">
      {questions.map((q) => (
        <div key={q.key} className="ac-reject-q">
          <div className="ac-reject-prompt">{q.prompt}</div>
          <div className="ac-reject-opts">
            {(q.options || []).map((o) => (
              <button
                key={o.value}
                type="button"
                className={`ac-chip-toggle ${isOn(q, o.value) ? 'on' : ''}`}
                disabled={busy}
                onClick={() => toggle(q, o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>
      ))}
      <textarea
        className="ac-reject-note"
        rows={2}
        placeholder="Anything specific? (optional)"
        value={note}
        disabled={busy}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="ac-card-actions">
        <button className="ac-btn ac-btn-primary" disabled={busy || !hasAny} onClick={submit}>
          <Check size={13} /> Revise draft
        </button>
        <button className="ac-btn ac-btn-ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// The draft_task_review card — the agent surfaces this role's generated
// assessment-task drafts. Manual review can approve or revise a draft; once a
// durable Turn-on intent owns it, the card is progress-only so no second human
// approval can race the automatic activation flow.
export function DraftTaskCard({ card, onApprove, onRevise, busy }) {
  const [rejectingId, setRejectingId] = useState(null);
  const drafts = card?.drafts || [];
  const automaticActivation = Boolean(card?.automatic_activation);
  if (!drafts.length) return null;

  return (
    <div className="ac-card ac-card-draft">
      <div className="ac-card-head">
        <FileText size={14} />
        <span>
          {automaticActivation
            ? `${drafts.length} assessment${drafts.length === 1 ? '' : 's'} being validated for Turn on`
            : `${drafts.length} task draft${drafts.length === 1 ? '' : 's'} available for optional review`}
        </span>
      </div>
      {drafts.map((d) => (
        <div key={d.task_id} className="ac-draft">
          <div className="ac-draft-title">{d.name}</div>
          <div className="ac-draft-meta">
            {d.deliverable_kind && <span className="ac-draft-tag">{d.deliverable_kind}</span>}
            <span>{(d.decisions || []).length} decisions</span>
            <span>{(d.rubric || []).length} rubric criteria</span>
            <span>{d.repo_file_count || 0} files</span>
          </div>
          {(d.decisions || []).length > 0 && (
            <ul className="ac-draft-decisions">
              {d.decisions.slice(0, 3).map((dec, i) => (
                <li key={i}>{dec.headline}</li>
              ))}
            </ul>
          )}
          {automaticActivation ? (
            <div className="ac-draft-auto" role="status">
              Turn on is saved. The agent will battle-test, verify, and approve this task automatically; you can leave this page and no second click is needed.
            </div>
          ) : rejectingId === d.task_id ? (
            <RejectQuestionnaire
              questions={card.reject_questions}
              busy={busy}
              onCancel={() => setRejectingId(null)}
              onSubmit={(fb) => {
                setRejectingId(null);
                onRevise?.(d.task_id, fb);
              }}
            />
          ) : (
            <div className="ac-card-actions">
              <button
                className="ac-btn ac-btn-primary"
                disabled={busy}
                onClick={() => onApprove?.(d.task_id)}
              >
                <Check size={13} /> Approve
              </button>
              <button
                className="ac-btn ac-btn-soft"
                disabled={busy}
                onClick={() => setRejectingId(d.task_id)}
              >
                <X size={13} /> Reject &amp; revise
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const NEEDS_INPUT_TITLES = {
  candidate_tie_break: 'Choose between candidates',
  confirm_material_change: 'Confirm a material change',
  cv_unreadable: 'CVs need readable text',
  intent_clarification: 'Clarify the request',
  intent_slot_missing: 'Complete the brief',
  missing_cv: 'CVs are missing',
  missing_job_spec: 'Add a job specification',
  monthly_budget_missing: 'Set the review budget',
  task_assignment_missing: 'Assign the task',
  threshold_ambiguous: 'Choose a screening threshold',
};

const NEEDS_INPUT_PROMPT_ASSISTS = {
  cv_unreadable: {
    label: 'Review affected candidates',
    prompt: 'Show me the candidates whose CVs could not be read and what I can do for each.',
  },
  missing_cv: {
    label: 'Review affected candidates',
    prompt: 'Show me the candidates who are missing a CV and what I can do for each.',
  },
  missing_job_spec: {
    label: 'Help add the job spec',
    prompt: 'Help me add the missing job specification for this role.',
  },
  monthly_budget_missing: {
    label: 'Review the budget',
    prompt: 'Help me review and set the monthly agent budget.',
  },
};

const promptAssistFor = (item) => NEEDS_INPUT_PROMPT_ASSISTS[item.question_kind] || {
  label: 'Help me resolve this',
  prompt: `Help me resolve this request from the agent: ${String(item.prompt || '').trim()}`,
};

export function NeedsInputCard({ item, onAnswer, onDismiss, onPrompt }) {
  const headingId = useId();
  const [pendingAction, setPendingAction] = useState(null);
  const [answer, setAnswer] = useState('');
  const busy = pendingAction !== null;
  const answered = item.status === 'answered';
  const dismissed = item.status === 'dismissed';
  const schema = item.response_schema || {};
  const valueSchema = schema?.properties?.value || schema;
  const options = Array.isArray(item.options) ? item.options : [];
  const inputMode = item.input_mode
    || (['integer', 'number'].includes(valueSchema.type) ? valueSchema.type : 'string');
  const canAnswer = item.can_answer !== false;
  const canDismiss = item.can_dismiss !== false;
  const acceptsTypedAnswer = canAnswer && (
    !options.length || inputMode === 'option_or_number'
  );
  const title = item.title || NEEDS_INPUT_TITLES[item.question_kind] || 'Choose the next step';
  const linkHref = safeInternalRoute(schema.link_url);
  const hasOptions = options.length > 0;
  const needsPromptAssist = !hasOptions && !acceptsTypedAnswer && !linkHref;
  const promptAssist = needsPromptAssist ? promptAssistFor(item) : null;

  const choose = async (opt) => {
    if (busy) return;
    const actionKey = `option:${String(opt.value)}`;
    setPendingAction(actionKey);
    try {
      await onAnswer?.(item.needs_input_id, { value: opt.value, label: opt.label });
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
    setPendingAction('typed-answer');
    try {
      const saved = await onAnswer?.(item.needs_input_id, { value });
      // Parent surfaces return false after showing their error toast. Keep the
      // recruiter's text intact so a transient API failure never eats it.
      if (saved !== false) setAnswer('');
    } finally {
      setPendingAction(null);
    }
  };

  const dismiss = async () => {
    if (busy) return;
    setPendingAction('dismiss');
    try {
      await onDismiss?.(item.needs_input_id);
    } finally {
      setPendingAction(null);
    }
  };

  return (
    <article
      className="ac-needs"
      aria-labelledby={headingId}
      aria-busy={busy}
      data-status={answered ? 'answered' : dismissed ? 'dismissed' : 'open'}
    >
      <header className="ac-needs-head">
        <span className="ac-needs-icon" aria-hidden="true">
          <CircleHelp size={15} />
        </span>
        <span className="ac-needs-heading-copy">
          <span className="ac-needs-eyebrow">Needs your input</span>
          <h3 id={headingId} className="ac-needs-title">{title}</h3>
        </span>
        {!answered && !dismissed && canDismiss ? (
          <Button
            className="ac-needs-dismiss"
            variant="ghost"
            size="xs"
            iconOnly
            loading={pendingAction === 'dismiss'}
            disabled={busy}
            aria-label={pendingAction === 'dismiss' ? 'Skipping request' : 'Skip for now'}
            title="Skip for now"
            onClick={dismiss}
          >
            {pendingAction === 'dismiss' ? null : <X size={14} />}
          </Button>
        ) : null}
      </header>
      <p className="ac-needs-prompt">{item.prompt}</p>
      {item.rationale ? <p className="ac-needs-rationale">{item.rationale}</p> : null}
      <PresenceSwap presenceKey={answered ? 'answered' : dismissed ? 'dismissed' : 'open'}>
        {answered ? (
          <div className="ac-needs-status"><Check size={13} /> Answered</div>
        ) : dismissed ? (
          <div className="ac-needs-status ac-needs-status-muted">Skipped for now</div>
        ) : (
          <div className="ac-needs-options" role="group" aria-label={`Respond to ${title}`}>
            {options.map((option) => {
              const actionKey = `option:${String(option.value)}`;
              return (
                <Button
                  key={option.value}
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
            {acceptsTypedAnswer ? (
              <form className="ac-needs-answer" onSubmit={submitTypedAnswer}>
                <Input
                  aria-label="Answer the agent"
                  className="ac-needs-input"
                  type={['integer', 'number', 'option_or_number'].includes(inputMode) ? 'number' : 'text'}
                  min={valueSchema.minimum}
                  max={valueSchema.maximum}
                  step={inputMode === 'integer' || inputMode === 'option_or_number' ? 1 : valueSchema.multipleOf || 'any'}
                  placeholder={inputMode === 'option_or_number' ? 'Or enter a number' : 'Type your answer'}
                  value={answer}
                  disabled={busy}
                  onChange={(event) => setAnswer(event.target.value)}
                />
                <Button
                  variant="primary"
                  size="sm"
                  loading={pendingAction === 'typed-answer'}
                  disabled={busy || !answer.trim()}
                  type="submit"
                >
                  Answer
                </Button>
              </form>
            ) : null}
            {linkHref ? (
              <Button as="a" variant="soft" size="sm" href={linkHref}>
                <ArrowUpRight size={13} /> {schema.link_label || 'Open settings'}
              </Button>
            ) : null}
            {promptAssist && onPrompt ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => onPrompt(promptAssist.prompt)}
              >
                {promptAssist.label}
              </Button>
            ) : null}
          </div>
        )}
      </PresenceSwap>
    </article>
  );
}
