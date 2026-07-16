// Surface-specific cards the agent chat dock slots into a shared <ChatMessage>:
// impact cards (threshold/constraint), the draft-task review card, and the
// agent's clarifying-question card. The chat chrome (bubbles, composer, markdown,
// empty state) now comes from the shared kit at shared/chat. Decision cards are
// intentionally NOT rendered here in Option C — those live in the main feed.

import { useState } from 'react';
import {
  Check,
  CircleHelp,
  FileText,
  GitFork,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import {
  AgentHelperPromptCard,
  AgentPromptCard,
  ChatActivity,
  ChatArtifact,
} from '../../../shared/chat';
import { AgentLoop } from '../../../shared/motion';
import { Button } from '../../../shared/ui/TaaliPrimitives';
import '../../../shared/chat/ChatArtifacts.css';
import { AgentEventCard } from './AgentEventCard.jsx';

const numOrDash = (v) => (typeof v === 'number' ? v : v == null ? '—' : v);
const humanizeOperation = (value) => String(value || 'Operation').replace(/_/g, ' ');

export function ImpactCard({ card, onApply, onPrompt, busy, detailOnly = false }) {
  if (!card || !card.type) return null;

  if (card.type === 'agent_event') {
    return <AgentEventCard card={card} onPrompt={onPrompt} detailOnly={detailOnly} />;
  }

  if (card.type === 'helper_prompt') {
    return <AgentHelperPromptCard card={card} onPrompt={onPrompt} detailOnly={detailOnly} />;
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
    const operationLabel = labels[card.operation] || humanizeOperation(card.operation);
    const confirmationPrompt = `Confirm ${operationLabel.toLowerCase()} for ${subject}.`;
    return (
      <ChatArtifact
        data-testid="operation-preview"
        eyebrow="Confirmation required"
        title={operationLabel}
        summary={subject}
        icon={CircleHelp}
        status={{ label: 'Not run', tone: 'neutral' }}
        footer={onPrompt ? (
          <Button variant="primary" size="xs" onClick={() => onPrompt(confirmationPrompt)}>
            Review in composer
          </Button>
        ) : null}
      >
        {preview.body_preview ? (
          <div className="tk-artifact-rescreen-estimate">“{preview.body_preview}”</div>
        ) : null}
        <div className="tk-artifact-rescreen-estimate">No action has run. Confirm in a new message.</div>
      </ChatArtifact>
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
    const subject = decision.candidate_name || `Decision ${decision.decision_id}`;
    const confirmationPrompt = `Confirm this action for ${subject}: ${action}.`;
    return (
      <ChatArtifact
        data-testid="decision-action-preview"
        eyebrow="Confirmation required"
        title={subject}
        summary={action}
        icon={CircleHelp}
        status={{ label: 'Not run', tone: 'neutral' }}
        footer={onPrompt ? (
          <Button variant="primary" size="xs" onClick={() => onPrompt(confirmationPrompt)}>
            Review in composer
          </Button>
        ) : null}
      >
        <div className="tk-artifact-rescreen-estimate">
          {action} · {decision.decision_type || decision.recommendation || 'pending decision'}
          {requested.workable_target_stage ? ` · ${requested.workable_target_stage}` : ''}
        </div>
        <div className="tk-artifact-rescreen-estimate">No action has run. Confirm in a new message.</div>
      </ChatArtifact>
    );
  }

  if (card.type === 'operation_receipt') {
    return (
      <ChatActivity
        data-testid="operation-receipt"
        severity="success"
        severityLabel="Completed"
        typeLabel="Operation receipt"
        title={card.status || 'Operation accepted'}
        summary={card.message || 'The operation was accepted.'}
        icon={Check}
      />
    );
  }

  if (card.type === 'constraint_change') {
    const c = card.criterion || {};
    return (
      <div className="tk-artifact-card tk-artifact-card-constraint">
        <div className="tk-artifact-card-head">
          <SlidersHorizontal size={14} />
          <span>Constraint {card.action}</span>
          {card.rescreening_count > 0 && (
            <span className="tk-artifact-card-live">
              <AgentLoop kind="pulse" className="tk-artifact-pulse" /> re-screening {card.rescreening_count}
            </span>
          )}
        </div>
        {c.text && (
          <div className="tk-artifact-chip-row">
            <span className="tk-artifact-constraint-chip">{c.text}</span>
          </div>
        )}
        {card.would_rescreen && card.would_rescreen.count > 0 && (
          <div className="tk-artifact-rescreen-estimate">
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
      <div className="tk-artifact-card tk-artifact-card-constraint">
        <div className="tk-artifact-card-head">
          <FileText size={14} />
          <span>Job spec updated</span>
        </div>
        {added.length > 0 && (
          <div className="tk-artifact-spec-diff">
            <span className="tk-artifact-spec-diff-label add">+ Added</span>
            <div className="tk-artifact-chip-row">
              {added.map((t, i) => <span key={`a${i}`} className="tk-artifact-constraint-chip tk-artifact-chip-add">{t}</span>)}
            </div>
          </div>
        )}
        {removed.length > 0 && (
          <div className="tk-artifact-spec-diff">
            <span className="tk-artifact-spec-diff-label remove">− Removed</span>
            <div className="tk-artifact-chip-row">
              {removed.map((t, i) => <span key={`r${i}`} className="tk-artifact-constraint-chip tk-artifact-chip-remove">{t}</span>)}
            </div>
          </div>
        )}
        {added.length === 0 && removed.length === 0 && (
          <div className="tk-artifact-rescreen-estimate">Same criteria re-derived from the new wording — no chip changes.</div>
        )}
        {card.would_rescreen && card.would_rescreen.count > 0 && (
          <div className="tk-artifact-rescreen-estimate">
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
      <div className="tk-artifact-card tk-artifact-card-constraint">
        <div className="tk-artifact-card-head">
          <GitFork size={14} />
          <span>Related role preview</span>
        </div>
        <div className="tk-artifact-rescreen-estimate">
          <strong>{card.proposed_name || 'New related role'}</strong> will share {total} candidate{total === 1 ? '' : 's'} with {card.source_role_name || `the original ${sourceProviderLabel} role`}.
        </div>
        <div className="tk-artifact-statrow">
          <span><b>{scorable}</b> score now</span>
          <span><b>{missing}</b> missing CV text</span>
          {typeof card.estimated_cost_usd === 'number' ? <span><b>~${card.estimated_cost_usd}</b> estimated AI usage</span> : null}
        </div>
        <div className="tk-artifact-rescreen-estimate">
          Candidate stages and actions stay coupled to the original {sourceProviderLabel} job. Awaiting your confirmation.
        </div>
      </div>
    );
  }

  if (card.type === 'related_role_created') {
    const counts = card.evaluation_counts || {};
    return (
      <ChatActivity
        severity="success"
        severityLabel="Completed"
        typeLabel="Role created"
        title={card.role_name || 'Related role created'}
        summary={`${counts.pending ?? 0} queued · ${counts.unscorable ?? 0} missing CV text`}
        icon={Check}
        source={card.frontend_url ? {
          label: 'Open related role',
          href: card.frontend_url,
          ariaLabel: `Open ${card.role_name || 'related role'}`,
        } : null}
      />
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
      <div className="tk-artifact-impact">
        <div className="tk-artifact-impact-line">
          <span className="tk-artifact-impact-label">Threshold</span>
          <span className="tk-artifact-impact-old">{numOrDash(card.current_threshold)}</span>
          <span className="tk-artifact-impact-arrow">→</span>
          <b className="tk-artifact-impact-new">{numOrDash(target)}</b>
          {typeof gain === 'number' && gain !== 0 && (
            <span className="tk-artifact-impact-gain">
              · {gain > 0 ? `+${gain}` : gain} candidate{Math.abs(gain) === 1 ? '' : 's'} clear the cut-off
            </span>
          )}
          {typeof gain === 'number' && gain === 0 && (
            <span className="tk-artifact-impact-gain">· no change</span>
          )}
        </div>
        {Array.isArray(card.added_sample) && card.added_sample.length > 0 && (
          <div className="tk-artifact-chip-row">
            {card.added_sample.map((n) => (
              <span key={n} className="tk-artifact-name-chip">{n}</span>
            ))}
          </div>
        )}
        {!sim && target != null && onApply && (
          <div className="tk-artifact-impact-actions">
            <Button
              variant="primary"
              size="xs"
              disabled={busy}
              onClick={() => onApply(target)}
            >
              Apply {target}
            </Button>
          </div>
        )}
      </div>
    );
  }

  if (card.type === 'threshold_change') {
    return (
      <ChatActivity
        severity="success"
        severityLabel="Completed"
        typeLabel="Threshold update"
        title={`Threshold ${numOrDash(card.before_threshold)} → ${numOrDash(card.after_threshold)}`}
        summary={`${card.discarded_advances ?? 0} advances retracted · ${card.created_rejects ?? 0} new rejects · ${card.above_after ?? '—'} clear the cut-off`}
        icon={Check}
      />
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
    <div className="tk-artifact-reject">
      {questions.map((q) => (
        <div key={q.key} className="tk-artifact-reject-q">
          <div className="tk-artifact-reject-prompt">{q.prompt}</div>
          <div className="tk-artifact-reject-opts">
            {(q.options || []).map((o) => (
              <button
                key={o.value}
                type="button"
                className={`tk-artifact-chip-toggle ${isOn(q, o.value) ? 'on' : ''}`}
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
        className="tk-artifact-reject-note"
        rows={2}
        placeholder="Anything specific? (optional)"
        value={note}
        disabled={busy}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="tk-artifact-card-actions">
        <Button variant="primary" size="xs" disabled={busy || !hasAny} onClick={submit}>
          <Check size={13} /> Revise draft
        </Button>
        <Button variant="ghost" size="xs" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
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
    <div className="tk-artifact-card tk-artifact-card-draft">
      <div className="tk-artifact-card-head">
        <FileText size={14} />
        <span>
          {automaticActivation
            ? `${drafts.length} assessment${drafts.length === 1 ? '' : 's'} being validated for Turn on`
            : `${drafts.length} task draft${drafts.length === 1 ? '' : 's'} available for optional review`}
        </span>
        {card?.role_version != null && (
          <span className="ac-draft-tag">Job revision {card.role_version}</span>
        )}
      </div>
      {drafts.map((d) => (
        <div key={d.task_id} className="tk-artifact-draft">
          <div className="tk-artifact-draft-title">{d.name}</div>
          <div className="tk-artifact-draft-meta">
            {d.deliverable_kind && <span className="tk-artifact-draft-tag">{d.deliverable_kind}</span>}
            <span>{(d.decisions || []).length} decisions</span>
            <span>{(d.rubric || []).length} rubric criteria</span>
            <span>{d.repo_file_count || 0} files</span>
          </div>
          {(d.decisions || []).length > 0 && (
            <ul className="tk-artifact-draft-decisions">
              {d.decisions.slice(0, 3).map((dec, i) => (
                <li key={i}>{dec.headline}</li>
              ))}
            </ul>
          )}
          {automaticActivation ? (
            <div className="tk-artifact-draft-auto" role="status">
              Turn on is saved. The agent will battle-test, verify, and approve this task automatically; you can leave this page and no second click is needed.
            </div>
          ) : rejectingId === d.task_id ? (
            <RejectQuestionnaire
              questions={card.reject_questions}
              busy={busy}
              onCancel={() => setRejectingId(null)}
              onSubmit={(fb) => {
                setRejectingId(null);
                onRevise?.(d.task_id, fb, card.role_version);
              }}
            />
          ) : (
            <div className="tk-artifact-card-actions">
              <Button
                variant="primary"
                size="xs"
                disabled={busy}
                onClick={() => onApprove?.(d.task_id, card.role_version)}
              >
                <Check size={13} /> Approve
              </Button>
              <Button
                variant="soft"
                size="xs"
                disabled={busy}
                onClick={() => setRejectingId(d.task_id)}
              >
                <X size={13} /> Reject &amp; revise
              </Button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// Compatibility export for older feature imports. New surfaces import the
// shared component from `shared/chat` directly.
export const NeedsInputCard = AgentPromptCard;
