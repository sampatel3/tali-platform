// Visual primitives shared across the Home (Hub) page. Kept tiny — no
// state, no I/O — so the page-level components can stay focused on data.

import React from 'react';
import {
  AlertTriangle,
  ArrowUpRight,
  Brain,
  CheckCircle2,
  CircleHelp,
  DollarSign,
  FilterX,
  Send,
  X,
} from 'lucide-react';

// Decision-outcome vocabulary is purple (positive / action you take) vs grey
// (terminal / already-resolved) — NOT traffic-light green/red. Matches the
// design system (purple variations, not brights) and the FunnelBoard chips.
const TYPE_BADGE = {
  advance_to_interview: {
    label: 'ADVANCE',
    color: 'var(--purple)',
    Icon: CheckCircle2,
  },
  advance: {
    label: 'ADVANCE',
    color: 'var(--purple)',
    Icon: CheckCircle2,
  },
  reject: {
    label: 'REJECT',
    color: 'var(--mute)',
    Icon: X,
  },
  skip_assessment_reject: {
    // Pre-screen-stage rejection, distinct from general mid-pipeline
    // rejection — carried by the label, not colour (both are terminal, so
    // both read grey rather than an alarming red).
    label: 'REJECT (PRE-SCREEN)',
    color: 'var(--mute)',
    Icon: X,
  },
  // Phase 4 abstention — sub-agents disagreed or were too uncertain.
  // Distinct purple treatment so it doesn't read as a confident
  // recommendation; the recruiter must adjudicate from scratch.
  escalate_low_confidence: {
    label: 'ESCALATE',
    color: 'var(--purple)',
    Icon: CircleHelp,
  },
  budget: {
    label: 'BUDGET',
    color: 'var(--amber)',
    Icon: DollarSign,
  },
  flag: {
    label: 'FLAG',
    color: 'var(--amber)',
    Icon: AlertTriangle,
  },
};

export const TypeBadge = ({ type, size = 'md' }) => {
  const cfg = TYPE_BADGE[type] || { label: String(type || '').toUpperCase(), color: 'var(--purple)', Icon: CircleHelp };
  const Icon = cfg.Icon;
  const small = size === 'sm';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: small ? 5 : 6,
        padding: small ? '2px 8px 2px 6px' : '4px 10px 4px 8px',
        borderRadius: small ? 6 : 7,
        background: `color-mix(in oklab, ${cfg.color} 18%, transparent)`,
        color: cfg.color,
        fontFamily: 'var(--font-mono)',
        fontSize: small ? 10.5 : 11.5,
        letterSpacing: small ? '.06em' : '.08em',
        fontWeight: 600,
      }}
    >
      <Icon size={small ? 11 : 12} strokeWidth={2.2} aria-hidden="true" />
      {cfg.label}
    </span>
  );
};

// Compact verdict pill for the pending-queue rows (home-preview `.vpill`): an
// icon + a short action word, toned purple for positive actions (Send /
// Advance), grey for terminal (Reject / Pre-screen), amber for an escalation
// the agent couldn't resolve. Distinct from the mono TypeBadge — the queue list
// reads as "what the agent wants to do", at a glance.
const VERDICT = {
  send_assessment: { label: 'Send', Icon: Send, tone: 'act' },
  resend_assessment_invite: { label: 'Resend', Icon: Send, tone: 'act' },
  advance_to_interview: { label: 'Advance', Icon: ArrowUpRight, tone: 'act' },
  advance: { label: 'Advance', Icon: ArrowUpRight, tone: 'act' },
  reject: { label: 'Reject', Icon: X, tone: 'rej' },
  skip_assessment_reject: { label: 'Pre-screen', Icon: FilterX, tone: 'rej' },
  escalate_low_confidence: { label: 'Escalate', Icon: CircleHelp, tone: 'q' },
};

export const VerdictPill = ({ type }) => {
  const cfg = VERDICT[type] || { label: String(type || '').replace(/_/g, ' '), Icon: CircleHelp, tone: 'act' };
  const Icon = cfg.Icon;
  const tones = {
    act: { background: 'var(--purple-soft)', color: 'var(--purple-2)' },
    rej: { background: 'var(--bg-3)', color: 'var(--mute)' },
    q: { background: 'var(--amber-soft)', color: 'var(--amber-ink)' },
  };
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        fontSize: 10,
        fontWeight: 500,
        borderRadius: 999,
        padding: '2px 8px',
        ...tones[cfg.tone],
      }}
    >
      <Icon size={11} strokeWidth={2.2} aria-hidden="true" />
      {cfg.label}
    </span>
  );
};

export const ConfBar = ({ value }) => {
  const pct = Math.round((value || 0) * 100);
  // Purple-only confidence: deep purple for high, lavender for lower — no
  // green/amber brights (design-system: purple variations).
  const color = value >= 0.8 ? 'var(--purple)' : 'var(--purple-lav)';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
      <span style={{ width: 64, height: 5, borderRadius: 3, background: 'var(--bg-3)', overflow: 'hidden' }}>
        <span style={{ display: 'block', width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-2)', fontWeight: 600 }}>{pct}%</span>
    </span>
  );
};

// Compact chip surfacing a candidate's Tali score, 0–100, on the decision
// list rows. Takes the numeric score directly and renders nothing when it's
// absent — pre-screen rejects aren't scored, so they show no chip. Purple
// tones only; the TypeBadge already carries the decision's red/green signal.
export const ScoreChip = ({ score, size = 'md' }) => {
  if (score == null || !Number.isFinite(Number(score))) return null;
  const value = Math.round(Number(score));
  const small = size === 'sm';
  return (
    <span
      title={`Tali score ${value} / 100`}
      style={{
        display: 'inline-flex',
        alignItems: 'baseline',
        gap: 4,
        padding: small ? '2px 8px' : '3px 9px',
        borderRadius: 6,
        background: 'color-mix(in oklab, var(--purple) 12%, transparent)',
        color: 'var(--purple)',
        fontFamily: 'var(--font-mono)',
        fontSize: small ? 10.5 : 11.5,
        fontWeight: 600,
        letterSpacing: '.04em',
        lineHeight: 1.4,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ opacity: 0.7 }}>SCORE</span>
      <span>{value}</span>
    </span>
  );
};

export const Avatar = ({ initials, size = 36 }) => (
  <span
    style={{
      width: size,
      height: size,
      borderRadius: '50%',
      background: 'var(--purple-soft)',
      color: 'var(--purple)',
      display: 'inline-grid',
      placeItems: 'center',
      fontFamily: 'var(--font-display)',
      fontSize: Math.round(size * 0.36),
      fontWeight: 600,
      flexShrink: 0,
    }}
  >
    {initials}
  </span>
);

export const initialsFrom = (name) => {
  const seed = String(name || '').trim();
  if (!seed) return '·';
  const parts = seed.split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] || '').concat(parts[1]?.[0] || '').toUpperCase() || seed.slice(0, 2).toUpperCase();
};

export const formatRelativeAge = (iso) => {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return '';
  if (diff < 60_000) return 'just now';
  const m = Math.round(diff / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.round(h / 24)}d`;
};

// Pass `href` to render as an anchor (defaults to opening in a new tab —
// the candidate-report deep link on /home uses this so click / cmd-click
// / middle-click all open in a new tab consistently). Falls back to a
// button + onClick for in-page navigation (role pipeline, assessment).
export const DeepLinkRow = ({ Icon, label, value, onClick, href }) => {
  const Tag = href ? 'a' : 'button';
  const tagProps = href
    ? { href, target: '_blank', rel: 'noopener noreferrer' }
    : { type: 'button', onClick };
  return (
  <Tag
    {...tagProps}
    style={{
      // rem-based so it scales with the 80% density root (these were inline
      // px and stayed full-size after the type-scale refactor — which is why
      // the header deep-links read as oversized vs the rem-scaled text).
      display: 'grid',
      gridTemplateColumns: '1.05rem 1fr auto',
      alignItems: 'center',
      gap: '0.45rem',
      padding: value ? '0.45rem 0.6rem' : '0.3rem 0.55rem',
      width: value ? '100%' : 'auto',
      border: '1px solid var(--line)',
      background: 'var(--bg)',
      borderRadius: 8,
      cursor: 'pointer',
      font: 'inherit',
      textAlign: 'left',
      color: 'inherit',
      textDecoration: 'none',
    }}
  >
    <span style={{ display: 'inline-grid', placeItems: 'center', color: 'var(--purple)' }}>
      <Icon size={13} strokeWidth={1.8} />
    </span>
    {value ? (
      <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
        <span style={{ fontSize: '0.72rem', color: 'var(--mute)', fontFamily: 'var(--font-mono)', letterSpacing: '.04em' }}>
          {label}
        </span>
        <span style={{ fontSize: '0.8rem', color: 'var(--ink)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {value}
        </span>
      </span>
    ) : (
      <span style={{ fontSize: '0.78rem', color: 'var(--ink)', fontWeight: 500, whiteSpace: 'nowrap' }}>
        {label}
      </span>
    )}
    <ArrowUpRight size={12} strokeWidth={1.8} aria-hidden="true" style={{ color: 'var(--mute)' }} />
  </Tag>
  );
};

export const FeedbackPill = ({ kind = 'teach' }) => (
  <span className={kind === 'override' ? 'rq-stream-overridepill' : 'rq-stream-teachpill'}>
    {kind === 'override' ? 'OVERRIDE' : '+ FEEDBACK'}
  </span>
);

// A muted purple pill that names the role a decision belongs to. Used in
// the pending sidebar (display-only — the whole row is the click target)
// and the decision feed (clickable, jumps to the role pipeline). Role
// names can be long so we cap width and ellipsis with a title tooltip.
export const RolePill = ({ roleName, roleId, onClick }) => {
  if (!roleName && roleId == null) return null;
  const label = roleName || `Role #${roleId}`;
  const baseStyle = {
    display: 'inline-block',
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    padding: '2px 8px',
    borderRadius: 6,
    background: 'color-mix(in oklab, var(--purple) 12%, transparent)',
    color: 'var(--purple)',
    fontFamily: 'var(--font-mono)',
    fontSize: 10.5,
    fontWeight: 600,
    letterSpacing: '.04em',
    lineHeight: 1.4,
    verticalAlign: 'middle',
  };
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        title={label}
        style={{ ...baseStyle, border: 0, cursor: 'pointer', font: 'inherit', textAlign: 'left' }}
      >
        {label}
      </button>
    );
  }
  return <span style={baseStyle} title={label}>{label}</span>;
};

export const formatUsd = (cents) => {
  const n = Number(cents || 0) / 100;
  if (!Number.isFinite(n)) return '$0';
  return n >= 100 ? `$${Math.round(n)}` : `$${n.toFixed(0)}`;
};

export const TeachIcon = Brain;
