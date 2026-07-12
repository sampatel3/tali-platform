// Surface-specific cards the agent chat dock slots into a shared <ChatMessage>:
// impact cards (threshold/constraint), the draft-task review card, and the
// agent's clarifying-question card. The chat chrome (bubbles, composer, markdown,
// empty state) now comes from the shared kit at shared/chat. Decision cards are
// intentionally NOT rendered here in Option C — those live in the main feed.

import { useState } from 'react';
import { Check, CircleHelp, FileText, SlidersHorizontal, X } from 'lucide-react';

const numOrDash = (v) => (typeof v === 'number' ? v : v == null ? '—' : v);

export function ImpactCard({ card, onApply, busy }) {
  if (!card || !card.type) return null;

  if (card.type === 'constraint_change') {
    const c = card.criterion || {};
    return (
      <div className="ac-card ac-card-constraint">
        <div className="ac-card-head">
          <SlidersHorizontal size={14} />
          <span>Constraint {card.action}</span>
          {card.rescreening_count > 0 && (
            <span className="ac-card-live">
              <span className="ac-pulse" /> re-screening {card.rescreening_count}
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
// assessment-task drafts. Approve activates one; Reject opens the structured
// questionnaire that re-authors (not deletes) it.
export function DraftTaskCard({ card, onApprove, onRevise, busy }) {
  const [rejectingId, setRejectingId] = useState(null);
  const drafts = card?.drafts || [];
  if (!drafts.length) return null;

  return (
    <div className="ac-card ac-card-draft">
      <div className="ac-card-head">
        <FileText size={14} />
        <span>
          {drafts.length} task draft{drafts.length === 1 ? '' : 's'} awaiting review
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
          {rejectingId === d.task_id ? (
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

export function NeedsInputCard({ item, onAnswer, onDismiss }) {
  const [busy, setBusy] = useState(false);
  const answered = item.status === 'answered';
  const dismissed = item.status === 'dismissed';

  const choose = async (opt) => {
    if (busy) return;
    setBusy(true);
    try {
      await onAnswer?.(item.needs_input_id, { value: opt.value, label: opt.label });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ac-needs">
      <div className="ac-needs-head">
        <CircleHelp size={14} />
        <span>Agent needs a steer</span>
      </div>
      <p className="ac-needs-prompt">{item.prompt}</p>
      {answered ? (
        <div className="ac-needs-answered"><Check size={13} /> Answered</div>
      ) : dismissed ? (
        <div className="ac-needs-answered" style={{ color: 'var(--ink-soft)' }}>Dismissed</div>
      ) : (
        <div className="ac-needs-options">
          {(item.options || []).map((o) => (
            <button key={o.value} className="ac-btn ac-btn-soft" disabled={busy} onClick={() => choose(o)}>
              {o.label}
            </button>
          ))}
          <button
            className="ac-btn ac-btn-ghost"
            disabled={busy}
            onClick={() => onDismiss?.(item.needs_input_id)}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
