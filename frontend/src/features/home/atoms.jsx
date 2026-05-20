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
  X,
} from 'lucide-react';

const TYPE_BADGE = {
  advance_to_interview: {
    label: 'ADVANCE',
    color: 'var(--green)',
    Icon: CheckCircle2,
  },
  advance: {
    label: 'ADVANCE',
    color: 'var(--green)',
    Icon: CheckCircle2,
  },
  reject: {
    label: 'REJECT',
    color: 'var(--red)',
    Icon: X,
  },
  skip_assessment_reject: {
    // Deeper red than plain ``reject`` so the recruiter reads this as a
    // stronger signal: "the agent has flagged this CV as not worth
    // assessing." Pre-screen-stage rejection, distinct from general
    // mid-pipeline rejection.
    label: 'REJECT (PRE-SCREEN)',
    color: 'var(--red-deep)',
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

export const ConfBar = ({ value }) => {
  const pct = Math.round((value || 0) * 100);
  const color = value >= 0.9 ? 'var(--green)' : value >= 0.8 ? 'var(--purple)' : 'var(--amber)';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
      <span style={{ width: 64, height: 5, borderRadius: 3, background: 'var(--bg-3)', overflow: 'hidden' }}>
        <span style={{ display: 'block', width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-2)', fontWeight: 600 }}>{pct}%</span>
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

export const DeepLinkRow = ({ Icon, label, value, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    style={{
      display: 'grid',
      gridTemplateColumns: '24px 1fr auto',
      alignItems: 'center',
      gap: 10,
      padding: '8px 10px',
      width: '100%',
      border: '1px solid var(--line)',
      background: 'var(--bg)',
      borderRadius: 8,
      cursor: 'pointer',
      font: 'inherit',
      textAlign: 'left',
    }}
  >
    <span style={{ display: 'inline-grid', placeItems: 'center', color: 'var(--purple)' }}>
      <Icon size={14} strokeWidth={1.8} />
    </span>
    <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
      <span style={{ fontSize: 12, color: 'var(--mute)', fontFamily: 'var(--font-mono)', letterSpacing: '.04em' }}>
        {label}
      </span>
      <span style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {value}
      </span>
    </span>
    <ArrowUpRight size={14} strokeWidth={1.8} aria-hidden="true" style={{ color: 'var(--mute)' }} />
  </button>
);

export const FeedbackPill = ({ kind = 'teach' }) => (
  <span className={kind === 'override' ? 'rq-stream-overridepill' : 'rq-stream-teachpill'}>
    {kind === 'override' ? 'OVERRIDE' : '+ FEEDBACK'}
  </span>
);

export const formatUsd = (cents) => {
  const n = Number(cents || 0) / 100;
  if (!Number.isFinite(n)) return '$0';
  return n >= 100 ? `$${Math.round(n)}` : `$${n.toFixed(0)}`;
};

export const TeachIcon = Brain;
