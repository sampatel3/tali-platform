// Static content model for LandingVariantG (F's "Vivid Purple" content, reused
// verbatim). The single source of truth for the funnel, the 5 Ds, the control
// points, the proof stats and the decision-lane candidates. Copy is
// founder-reviewed; do not paraphrase.

// The three candidates that flow into the hero's decision lane. Tariq is the
// reject — a MUTED GREY chip + outline pill, never red (firm brand rule).
export const CANDIDATES = [
  { initials: 'MC', name: 'Maya Chen', sub: 'Senior · applied 2d ago', score: 88, verdict: 'advance' },
  { initials: 'JP', name: 'Jordan Patel', sub: 'Senior · applied 3d ago', score: 84, verdict: 'advance' },
  { initials: 'TA', name: 'Tariq Al-Ahmad', sub: 'Mid · applied 4d ago', score: 41, verdict: 'reject' },
];

// The 5-step funnel. `viz` describes the glimpse chip(s) pinned to each card's
// foot — modelled as data so the JSX renders it (no dangerouslySetInnerHTML).
export const FUNNEL = [
  {
    n: '01',
    key: 'Intake',
    body: 'Applicants flow in from your live Taali job page and connected ATS. Outbound uses only a lawful audience and campaign you approve.',
    viz: { kind: 'chips', chips: [{ label: 'taali apply', variant: 'plain' }, { label: 'workable', variant: 'plain' }, { label: 'bullhorn', variant: 'plain' }] },
  },
  {
    n: '02',
    key: 'Screen',
    body: "Reads every CV against the role's real requirements. Weak fits gated with evidence, not guesswork.",
    viz: { kind: 'evidence', text: '5y Python · matched to req 3' },
  },
  {
    n: '03',
    key: 'Assess',
    body: 'A task authored from your JD, battle-tested in a sandbox. Candidates pair with Claude on real work — engineering or knowledge work.',
    viz: { kind: 'score', value: '88', unit: '/100' },
  },
  {
    n: '04',
    key: 'Decide',
    body: 'A deterministic verdict on every candidate, the evidence attached.',
    viz: { kind: 'chips', chips: [{ label: 'Advance →', variant: 'ok' }] },
  },
  {
    n: '05',
    key: 'Hand back',
    body: 'Decisions, notes and reports written back to your ATS. The audit trail comes free.',
    viz: { kind: 'chips', chips: [{ label: '↻ synced to Workable', variant: 'default' }] },
  },
];

// The 5 Ds — always five dimensions, scored from the real session. Composite is
// the rounded average (renders as 84).
export const DDS = [
  { name: 'Delegation', def: 'Deciding what to own vs. hand to the agent.', val: 82 },
  { name: 'Description', def: 'Directing the agent — clear prompts, the right context.', val: 86 },
  { name: 'Discernment', def: 'Catching what the AI gets wrong.', val: 90 },
  { name: 'Diligence', def: 'Verifying before claiming done.', val: 80 },
  { name: 'Deliverable', def: 'What actually shipped, on its merits.', val: 84 },
];

export const CONTROL = [
  'Every consequential call is deterministic and evidence-linked.',
  'Approve, override, or teach it back — in one click.',
  'A full audit trail comes free.',
  'It advises; it never acts on protected characteristics.',
];

// Hero funnel-stat row (the OFF→ON job card). Last cell ("Advanced") goes hot
// (purple value) when the agent is ON.
export const FUNNEL_STATS = [
  { k: 'Applied', v: '312' },
  { k: 'Screened', v: '184' },
  { k: 'Assessed', v: '22' },
  { k: 'Advanced', v: '9', hot: true },
];

// Footer link columns (mono uppercase heads). ONLY real, working destinations —
// no links to pages that don't exist. `section` scrolls to an in-page anchor
// (via onSection); `page` is a real route rendered through <PageLink>; `href` is
// a literal link (mailto). Privacy/Terms/DPA pages don't exist yet, so they are
// deliberately omitted rather than faked.
export const FOOTER_COLS = [
  {
    head: 'Product',
    links: [
      { label: 'How it works', section: 'how-it-works' },
      { label: 'AI fluency', section: 'platform' },
      { label: 'Control', section: 'g-control' },
    ],
  },
  {
    head: 'Company',
    links: [
      { label: 'Blog', page: 'blog' },
      { label: 'Book a demo', page: 'demo-lead' },
      { label: 'Sign in', page: 'login' },
    ],
  },
  {
    head: 'Contact',
    links: [
      { label: 'hello@taali.ai', href: 'mailto:hello@taali.ai' },
    ],
  },
];

export const COMPOSITE = Math.round(DDS.reduce((a, d) => a + d.val, 0) / DDS.length);

export const verdictLabel = (v) => (v === 'advance' ? 'Advance' : v === 'assess' ? 'Assess' : 'Reject');
