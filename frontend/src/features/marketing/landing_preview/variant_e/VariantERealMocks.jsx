import React, { useEffect, useRef } from 'react';
import { Sparkles } from 'lucide-react';
import { stagger, useAnimate, useInView } from 'motion/react';

import { useReducedMotion } from './motion';
import { ActivityFeed } from '../../../home/ActivityFeed';
import { AgentDecisionCard } from '../../../../shared/decisions/AgentDecisionCard';
import { AssessmentScorecard } from '../../../candidates/AssessmentScorecard';
import { Avatar, ScoreChip, VerdictPill, initialsFrom } from '../../../home/atoms';
import { ScoreRing } from '../../../../shared/ui/ScoreRing';

// ---------------------------------------------------------------------------
// Variant E v2 — the REAL product surfaces, fed fixtures.
//
// Every "mock" on this page is now an actual production component (the same
// ones /home, the candidate report and the header render), wrapped in a light
// browser-chrome frame so it reads as a real screen. This is the proven-safe
// pattern LandingVariantB uses. Fixtures mirror the exact row/decision/
// assessment shapes the live APIs return.
//
// data-brand="taali" lives on the variant root (see LandingVariantE), so the
// components' --purple / --accent / --grad-agent-on* tokens all resolve to the
// Taali purple palette.
// ---------------------------------------------------------------------------

const _NOW = Date.now();
const _prov = (hoursAgo, version = '2.1.0') => ({
  engine_version: version,
  scored_at: new Date(_NOW - hoursAgo * 60 * 60 * 1000).toISOString(),
});
const noop = () => {};

// ── FIXTURES ────────────────────────────────────────────────────────────

// (1) HERO — one fully-evidenced pending decision, the shape AgentDecisionCard
// consumes: score ring + provenance, recommendation slab, requirement bars,
// evidence cells and a decision trace.
const HERO_DECISION = {
  id: 312,
  status: 'pending',
  decision_type: 'advance_to_interview',
  candidate_name: 'Maya Chen',
  candidate_email: 'maya.chen@example.com',
  application_id: 1042,
  role_id: 109,
  role_name: 'Senior Backend Engineer',
  taali_score: 88,
  score_summary: { score_provenance: _prov(0.2) },
  confidence: 0.91,
  reasoning:
    "Clears every must-have with strong AWS + Python evidence, and the assessment landed at 88 — top of this role's pipeline. Ready for the technical panel.",
  created_at: new Date(_NOW - 6 * 60 * 1000).toISOString(),
  requirements: [
    { label: 'Distributed systems', score: 92 },
    { label: 'AWS depth', score: 84 },
    { label: 'Verification habit', score: 90 },
  ],
  evidence: {
    cells: [
      { k: 'CV match', v: '94 / 100', good: true },
      { k: 'Assessment', v: '88 / 100', good: true },
      { k: 'Discernment', v: 'Top 12%', good: true },
      { k: 'Must-haves', v: '6 / 6', good: true },
    ],
    trace: [
      { who: 'agent', t: 'Pre-screened CV', m: 'Python + AWS + 4y backend — clears every must-have.' },
      { who: 'agent', t: 'Scored assessment', m: 'Revenue-recovery task: 88/100. Verified the dedupe before touching the loader.' },
      { who: 'agent', t: 'Recommendation', m: 'Advance to the technical panel — strongest in this pipeline today.' },
    ],
  },
};

// (2) PRODUCT-IN-ACTION — the morning queue the live <ActivityFeed> renders on
// /home: pending advances, a reject, an escalation, plus resolved rows, each
// with evidence.{cells,trace}.
const FUNNEL_FEED_ROWS = [
  {
    id: 28,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Maya Chen',
    candidate_email: 'maya.chen@example.com',
    application_id: 1042,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 88,
    score_summary: { score_provenance: _prov(0.1) },
    confidence: 0.92,
    reasoning:
      "Strong fit — clears every must-have with room to spare. Assessment 88/100; verified the dedupe before editing. Top of this role's pipeline.",
    created_at: new Date(_NOW - 2 * 60 * 1000).toISOString(),
    evidence: {
      cells: [
        { k: 'CV match', v: '94 / 100', good: true },
        { k: 'Assessment', v: '88 / 100', good: true },
        { k: 'Must-haves', v: '6 / 6', good: true },
      ],
      trace: [
        { who: 'agent', t: 'Pre-screened CV', m: 'Python + AWS + 4y backend — clears every must-have.' },
        { who: 'agent', t: 'Recommendation', m: 'Advance to the technical panel — strongest today.' },
      ],
    },
  },
  {
    id: 27,
    status: 'pending',
    decision_type: 'reject',
    candidate_name: 'Tariq Al-Ahmad',
    application_id: 1018,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 41,
    score_summary: { score_provenance: _prov(0.8) },
    confidence: 0.81,
    reasoning:
      'Well below your bar. Missing the must-have distributed-systems and AWS depth; assessment stalled on the schema-drift path.',
    created_at: new Date(_NOW - 44 * 60 * 1000).toISOString(),
    evidence: {
      cells: [
        { k: 'CV match', v: '52 / 100', good: false },
        { k: 'Assessment', v: '41 / 100', good: false },
        { k: 'Must-haves', v: '2 / 6', good: false },
      ],
      trace: [
        { who: 'agent', t: 'Pre-screened CV', m: 'No distributed-systems evidence; AWS named but not demonstrated.' },
        { who: 'agent', t: 'Recommendation', m: 'Reject — below the bar you set for this role.' },
      ],
    },
  },
  {
    id: 26,
    status: 'pending',
    decision_type: 'escalate_low_confidence',
    candidate_name: 'Aisha Bello',
    application_id: 1031,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 64,
    score_summary: { score_provenance: _prov(0.6) },
    confidence: 0.5,
    reasoning:
      "I'm split on her systems-design depth — two checks said advance, one said assess again. I don't want to call this one for you. Take a look?",
    created_at: new Date(_NOW - 71 * 60 * 1000).toISOString(),
  },
  {
    id: 25,
    status: 'approved',
    decision_type: 'advance_to_interview',
    candidate_name: 'Priya Raman',
    application_id: 1003,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 86,
    score_summary: { score_provenance: _prov(0.4) },
    human_disposition: 'approved',
    resolved_at: new Date(_NOW - 18 * 60 * 1000).toISOString(),
  },
  {
    id: 24,
    status: 'overridden',
    decision_type: 'reject',
    candidate_name: 'Jonas Weber',
    application_id: 994,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 58,
    score_summary: { score_provenance: _prov(0.9) },
    human_disposition: 'taught',
    resolution_note: 'override → advance',
    resolved_at: new Date(_NOW - 52 * 60 * 1000).toISOString(),
  },
];

// (2b) SCREEN band — a screening cohort for the real <ActivityFeed>: two clears
// and one gated-out, each carrying the evidence the agent found against the
// role's must-haves.
const SCREEN_FEED_ROWS = [
  {
    id: 71,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Maya Chen',
    application_id: 1042,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 88,
    score_summary: { score_provenance: _prov(0.2) },
    confidence: 0.92,
    reasoning: 'Clears all six must-haves — Python, AWS and distributed-systems evidence on the CV, corroborated across two projects.',
    created_at: new Date(_NOW - 12 * 60 * 1000).toISOString(),
  },
  {
    id: 70,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Jordan Patel',
    application_id: 1039,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 84,
    score_summary: { score_provenance: _prov(0.3) },
    confidence: 0.86,
    reasoning: 'Five of six must-haves with strong backend depth; one AWS service named but not demonstrated — flagged for the panel.',
    created_at: new Date(_NOW - 26 * 60 * 1000).toISOString(),
  },
  {
    id: 69,
    status: 'pending',
    decision_type: 'reject',
    candidate_name: 'Tariq Al-Ahmad',
    application_id: 1018,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 41,
    score_summary: { score_provenance: _prov(0.5) },
    confidence: 0.81,
    reasoning: 'Two of six must-haves. No distributed-systems evidence anywhere in the CV; below the bar you set for this role.',
    created_at: new Date(_NOW - 38 * 60 * 1000).toISOString(),
  },
];

// (3) ASSESS — a fixture assessment whose score_breakdown.rubric_grading drives
// the real 5-Ds <AssessmentScorecard> (computeScorecard reads it rubric-first).
const SCORECARD_ASSESSMENT = {
  id: 9001,
  candidate_name: 'Maya Chen',
  evaluation_rubric: {
    load_bearing_design: { lens: 'delegation', fluency: 'delegation' },
    prompt_clarity: { lens: 'description', fluency: 'description' },
    caught_hallucination: { lens: 'discernment', fluency: 'discernment' },
    verified_before_done: { lens: 'diligence', fluency: 'diligence' },
    shipped_correctness: { lens: 'deliverable', fluency: 'deliverable' },
  },
  score_breakdown: {
    rubric_grading: {
      fluency_4d: {
        delegation: 86,
        description: 88,
        discernment: 90,
        diligence: 82,
        deliverable: 84,
      },
      dimensions: [
        {
          id: 'load_bearing_design',
          score: 8.6,
          rating: 'excellent',
          reasoning:
            'Owned the schema + retry design themselves and handed the boilerplate to the agent — the load-bearing calls stayed with the candidate.',
          evidence_citations: ['prompt #3', 'edit db/models.py'],
        },
        {
          id: 'prompt_clarity',
          score: 8.8,
          rating: 'excellent',
          reasoning: 'Prompts named the constraint and the expected shape up front, so the agent had the context it needed.',
          evidence_citations: ['prompt #5'],
        },
        {
          id: 'caught_hallucination',
          score: 9.0,
          rating: 'excellent',
          reasoning: 'Spotted an invented API method in the agent output and corrected it before running — a planted trap, caught.',
          evidence_citations: ['prompt #7', 'terminal run #2'],
        },
        {
          id: 'verified_before_done',
          score: 8.2,
          rating: 'good',
          reasoning: 'Ran the failing test, watched it go green, then re-ran the suite before calling it done.',
          evidence_citations: ['test run #4'],
        },
        {
          id: 'shipped_correctness',
          score: 8.4,
          rating: 'good',
          reasoning: 'The revenue-recovery flow was restored and the finance-close guard held under the edge-case input.',
          evidence_citations: ['final diff'],
        },
      ],
    },
  },
};

// ── ARTIFACT FRAME — light browser-chrome card the real components sit in. ──
export const ArtifactFrame = ({ browserPath, children, className = '' }) => (
  <div className={`lve-frame${className ? ` ${className}` : ''}`}>
    <div className="lve-frame-bar">
      <span className="lve-frame-dot" />
      <span className="lve-frame-dot" />
      <span className="lve-frame-dot" />
      <span className="lve-frame-path">{browserPath}</span>
      <span className="lve-frame-live">Live component</span>
    </div>
    <div className="lve-frame-body">{children}</div>
  </div>
);

// ── FIX 1 — variant-D's clean pill toggle (grey OFF → purple ON). ───────────
// The standout hero moment: a simple switch, not the in-app .abar chrome. Same
// markup + vocabulary as variant D (lvd-switch → lve-switch), keeping role=
// "switch" so it flips the hero product card + brings the purple alive. The
// "AGENT: ON/OFF" mono caption sits alongside.
export const HeroAgentSwitch = ({ on, pressing, onToggle }) => (
  <div className="lve-switch-wrap">
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={on ? 'Agent on. Turn the agent off.' : 'Agent off. Turn the agent on.'}
      className={`lve-switch${on ? ' is-on' : ''}${pressing ? ' is-pressing' : ''}`}
      onClick={onToggle}
    >
      <span className="lve-switch-track" aria-hidden="true">
        <span className="lve-switch-glow" />
        <span className="lve-switch-knob">
          <span className="lve-switch-ring" />
        </span>
      </span>
    </button>
    <span className="lve-switch-caption" aria-hidden="true">
      agent: <b>{on ? 'on' : 'off'}</b>
    </span>
  </div>
);

// ── FIX 2 — the hero product card: the REAL AgentDecisionCard, contained. ────
// `hideDecisionParts` drops the reasoning paragraph, evidence grid, decision
// trace and action bar — leaving the compact glimpse the founder asked for:
// ScoreRing + name/role, the agent-recommends verdict slab, and the three
// requirement bars. Constrained further to ~400px in CSS (.lve-frame--hero).
export const HeroDecisionArtifact = () => (
  <ArtifactFrame browserPath="app.taali.ai/home" className="lve-frame--hero">
    <AgentDecisionCard
      decision={HERO_DECISION}
      onApprove={noop}
      onAlternative={noop}
      onTeach={noop}
      onSnooze={noop}
      onNavigate={noop}
      hideDecisionParts
    />
  </ArtifactFrame>
);

export const FunnelFeedArtifact = () => (
  <ArtifactFrame browserPath="app.taali.ai/home">
    <ActivityFeed
      rows={FUNNEL_FEED_ROWS}
      selectedId={null}
      onSelect={noop}
      onNavigate={noop}
      kicker="AWAITING YOU · 3 DECISIONS"
      title="Your morning queue"
      subtitle="The agent worked the pipeline overnight. Approve, override, or teach back every call — nothing moves without you."
    />
  </ArtifactFrame>
);

// ── FIX 3 — the SCREEN band is now the REAL <ActivityFeed>, not hand-drawn
// bars. A screening cohort: each CV gated against the role's requirements, with
// the real ScoreChip / VerdictPill / evidence the product renders. ────────────
export const ScreenFeedArtifact = () => (
  <ArtifactFrame browserPath="app.taali.ai/roles/109" className="lve-frame--screen">
    <ActivityFeed
      rows={SCREEN_FEED_ROWS}
      selectedId={null}
      onSelect={noop}
      onNavigate={noop}
      kicker="SCREENED · AGAINST REQUIREMENTS"
      title="Every CV, gated with evidence"
      subtitle="The agent checked each candidate against the role's real must-haves — and shows what it found, or didn't."
    />
  </ArtifactFrame>
);

// ── FIX 4 — animate the real 5-Ds scorecard's reveal. On enter (once) the five
// D rows stagger in (fade + rise), each score bar fills 0 → value, and the
// score number ticks up. The AssessmentScorecard itself is untouched; we drive
// its DOM via a scoped Motion timeline + a light rAF number tween, and arm the
// hidden initial state through the `data-lve-sc` CSS contract. ────────────────
const EASE = [0.16, 1, 0.3, 1];

export const ScorecardArtifact = () => {
  const [scope, animate] = useAnimate();
  const inView = useInView(scope, { amount: 0.35 });
  const reduced = useReducedMotion();
  const playedRef = useRef(false);

  useEffect(() => {
    const root = scope.current;
    if (!root) return undefined;

    if (reduced) {
      root.removeAttribute('data-lve-sc'); // show the final composed scorecard
      return undefined;
    }
    root.setAttribute('data-lve-sc', 'true'); // CSS hides rows + zeroes the bars
    if (!inView || playedRef.current) return undefined;
    playedRef.current = true;

    animate('.sc5-row', { opacity: [0, 1], y: [14, 0] }, { duration: 0.5, delay: stagger(0.09), ease: EASE });
    animate('.sc5-bar > i', { scaleX: [0, 1] }, { duration: 0.7, delay: stagger(0.09), ease: EASE });

    // Tick each score number 0 → target with a light rAF loop (mirrors Ticker),
    // so it counts up alongside its bar without touching React state.
    const rafs = [];
    root.querySelectorAll('.sc5-score').forEach((el, i) => {
      const node = el.firstChild; // text node holding the "88" before <em>/100</em>
      if (!node) return;
      const target = parseInt(node.textContent, 10);
      if (!Number.isFinite(target)) return;
      const start = performance.now() + i * 90;
      const dur = 900;
      const step = (now) => {
        const t = Math.max(0, Math.min(1, (now - start) / dur));
        const eased = 1 - Math.pow(1 - t, 3);
        node.textContent = String(Math.round(target * eased));
        if (t < 1) rafs.push(requestAnimationFrame(step));
      };
      rafs.push(requestAnimationFrame(step));
    });
    return () => rafs.forEach((r) => cancelAnimationFrame(r));
  }, [inView, reduced, animate, scope]);

  return (
    <ArtifactFrame browserPath="app.taali.ai/candidates/1042">
      {/* Seed the armed (hidden) state on first paint when a reveal will run, so
          the scorecard doesn't flash full before the timeline starts. */}
      <div ref={scope} data-lve-scorecard {...(reduced ? {} : { 'data-lve-sc': 'true' })}>
        <AssessmentScorecard assessment={SCORECARD_ASSESSMENT} />
      </div>
    </ArtifactFrame>
  );
};

// ── FIX 3 — bespoke pillar micro-visuals, built from the REAL product atoms. ─

// Pillar 1 (SCREEN) — a mini stack of the real feed rows: avatar + name +
// score chip + verdict pill, the gated-with-evidence shortlist at a glance.
const SCREEN_MINI = [
  { name: 'Maya Chen', score: 88, verdict: 'advance_to_interview' },
  { name: 'Jordan Patel', score: 84, verdict: 'advance_to_interview' },
  { name: 'Tariq Al-Ahmad', score: 41, verdict: 'reject' },
];

export const ScreenPillarVisual = () => (
  <div className="lve-pv lve-pv-screen" aria-hidden="true">
    {SCREEN_MINI.map((r) => (
      <div className="lve-pv-row" key={r.name}>
        <Avatar initials={initialsFrom(r.name)} size={26} />
        <span className="lve-pv-name">{r.name}</span>
        <ScoreChip score={r.score} size="sm" />
        <VerdictPill type={r.verdict} />
      </div>
    ))}
  </div>
);

// Pillar 2 (ASSESS) — a compact 5-Ds mini-scorecard: the five fluency axes as
// labelled bars in the brand purple.
const DS_MINI = [
  { name: 'Delegation', w: 86 },
  { name: 'Description', w: 88 },
  { name: 'Discernment', w: 90 },
  { name: 'Diligence', w: 82 },
  { name: 'Deliverable', w: 84 },
];

export const AssessPillarVisual = () => (
  <div className="lve-pv lve-pv-ds" aria-hidden="true">
    {DS_MINI.map((d) => (
      <div className="lve-pv-ds-row" key={d.name}>
        <span className="lve-pv-ds-name">{d.name}</span>
        <span className="lve-pv-ds-track">
          <span className="lve-pv-ds-fill" style={{ width: `${d.w}%` }} />
        </span>
        <span className="lve-pv-ds-val">{d.w}</span>
      </div>
    ))}
  </div>
);

// Pillar 3 (DECIDE) — a mini decision-card header: the real ScoreRing + name/
// role + a verdict pill and the "agent recommends" line.
export const DecidePillarVisual = () => (
  <div className="lve-pv lve-pv-decide" aria-hidden="true">
    <ScoreRing score={88} size={62} label="TAALI" />
    <div className="lve-pv-decide-body">
      <div className="lve-pv-decide-name">Maya Chen</div>
      <div className="lve-pv-decide-role">Senior Engineer · req #A-114</div>
      <div className="lve-pv-decide-verdict">
        <VerdictPill type="advance_to_interview" />
        <span className="lve-pv-decide-rec">
          <Sparkles size={11} strokeWidth={2} /> Agent recommends · 91%
        </span>
      </div>
    </div>
  </div>
);
