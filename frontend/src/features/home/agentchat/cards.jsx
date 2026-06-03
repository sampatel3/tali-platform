// Live renderers for the agent chat dock: chat bubbles, impact cards, and
// the agent's clarifying-question cards. Decision cards are intentionally NOT
// rendered here in Option C — those live in the main decision feed; the dock
// stays focused on the conversation.

import { useState } from 'react';
import { Bot, Check, CircleHelp, Sparkles, SlidersHorizontal, TrendingDown } from 'lucide-react';

const initials = (name) =>
  String(name || '?')
    .split(' ')
    .map((p) => p[0])
    .slice(0, 2)
    .join('')
    .toUpperCase();

export function Avatar({ name, kind = 'agent', size = 28 }) {
  const isAgent = kind === 'agent';
  return (
    <div
      className={`ac-avatar ${isAgent ? 'ac-avatar-agent' : 'ac-avatar-cand'}`}
      style={{ width: size, height: size }}
    >
      {isAgent ? <Bot size={size * 0.52} /> : initials(name)}
    </div>
  );
}

const fmtTime = (iso) => {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
};

export function ChatBubble({ item, children }) {
  const isAgent = item.author === 'agent';
  return (
    <div className={`ac-row ${isAgent ? 'ac-row-agent' : 'ac-row-user'}`}>
      {isAgent && <Avatar kind="agent" size={28} />}
      <div className="ac-bubble-wrap">
        {item.text ? (
          <div className={`ac-bubble ${isAgent ? 'ac-bubble-agent' : 'ac-bubble-user'}`}>{item.text}</div>
        ) : null}
        {children}
        <span className="ac-time">{fmtTime(item.created_at)}</span>
      </div>
    </div>
  );
}

export function ThinkingBubble() {
  return (
    <div className="ac-row ac-row-agent">
      <Avatar kind="agent" size={28} />
      <div className="ac-bubble-wrap">
        <div className="ac-bubble ac-bubble-agent ac-thinking">
          <span /> <span /> <span />
        </div>
      </div>
    </div>
  );
}

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
      </div>
    );
  }

  if (card.type === 'threshold_recommendation' || card.type === 'threshold_simulation') {
    const sim = card.type === 'threshold_simulation';
    const target = sim ? card.simulated_threshold : card.recommended_threshold;
    const gain = sim ? card.delta_above : card.projected_additional;
    return (
      <div className="ac-card">
        <div className="ac-card-head">
          <Sparkles size={14} />
          <span>{sim ? 'Simulation' : 'Recommendation'}</span>
        </div>
        <div className="ac-thresh-line">
          <span className="ac-thresh-old">{numOrDash(card.current_threshold)}</span>
          <span className="ac-arrow">→</span>
          <span className="ac-thresh-new">{numOrDash(target)}</span>
          {typeof gain === 'number' && gain > 0 && (
            <span className="ac-thresh-gain">+{gain} candidates</span>
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
          <div className="ac-card-actions">
            <button className="ac-btn ac-btn-primary" disabled={busy} onClick={() => onApply(target)}>
              <Check size={13} /> Apply {target}
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

export { TrendingDown };
