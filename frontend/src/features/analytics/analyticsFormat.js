// Shared formatters + helpers for the standalone /analytics page. Every value
// the page shows is rounded here and falls back cleanly when data is absent —
// no fabricated numbers, no NaN leaking to the DOM.

export const safeNum = (v, fb = 0) => (Number.isFinite(Number(v)) ? Number(v) : fb);

export const pct = (part, whole) =>
  (safeNum(whole) > 0 ? Math.round((safeNum(part) / safeNum(whole)) * 100) : 0);

// Money: cents → "$61" (>= $100 whole) or "$61" / "$1" (rounded whole < 100).
// Mirrors the backend _ms_format_dollars so the pulse band matches the API.
export const fmtUsd = (cents) => {
  const n = safeNum(cents) / 100;
  return n >= 100 ? `$${Math.round(n)}` : `$${Math.round(n)}`;
};

// Money with cents precision under $100 — used where a finer figure helps
// (per-agent budgets), still rounded so nothing fabricates trailing noise.
export const fmtUsdFine = (cents) => {
  const n = safeNum(cents) / 100;
  return n >= 100
    ? `$${Math.round(n)}`
    : `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
};

export const fmtRelShort = (value) => {
  if (!value) return '—';
  const t = new Date(value).getTime();
  if (Number.isNaN(t)) return '—';
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.round(hrs / 24)}d`;
};

export const fmtRelAgo = (value) => {
  const s = fmtRelShort(value);
  return s === '—' || s === 'just now' ? s : `${s} ago`;
};

export const fmtClock = (value) => {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
};

// "2026-06" → "Jun" (short month label for the trend bars).
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const monthShort = (ym) => {
  const m = /^(\d{4})-(\d{2})$/.exec(String(ym || ''));
  if (!m) return String(ym || '');
  const idx = Number(m[2]) - 1;
  return MONTHS[idx] || String(ym);
};

// "2026-06-21T…" → "Jun 21" for the threshold-history timeline.
export const fmtDay = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { month: 'short', day: '2-digit' });
};

// Decision-type enum → friendly label (shared with the rest of the app).
export const DECISION_TYPE_LABELS = {
  advance_to_interview: 'Advance',
  reject: 'Reject',
  skip_assessment_reject: 'Pre-screen reject',
  skip_assessment_advance: 'Skip & advance',
  send_assessment: 'Send assessment',
  resend_assessment_invite: 'Resend invite',
  escalate_low_confidence: 'Escalate',
};

export const prettyKey = (key) => {
  const s = String(key || '').replace(/_/g, ' ');
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
};

export const decisionTypeLabel = (key) => DECISION_TYPE_LABELS[key] || prettyKey(key);

// Map a decision type/recommendation to the chip tone class (purple for
// advance/send, grey for reject — never traffic-light colours).
export const decisionChipClass = (decisionType) => {
  const t = String(decisionType || '').toLowerCase();
  if (t.includes('advance') || t.includes('skip_assessment_advance')) return 'adv';
  if (t.includes('send') || t.includes('invite')) return 'send';
  if (t.includes('reject')) return 'rej';
  if (t.includes('pause')) return 'pause';
  return 'send';
};

// Workable / pipeline stage key → friendly label.
export const STAGE_LABELS = {
  applied: 'Applied',
  phone_screen: 'Phone screen',
  phone_interview: 'Phone interview',
  interview: 'Interview',
  technical_interview: 'Technical interview',
  final_interview: 'Final interview',
  onsite: 'Onsite',
  assessment: 'Assessment',
  offer: 'Offer',
  offer_extended: 'Offer extended',
  offer_accepted: 'Offer accepted',
  hired: 'Hired',
  unstaged: 'No Workable stage',
};
export const stageLabel = (key) => STAGE_LABELS[key] || prettyKey(key);
