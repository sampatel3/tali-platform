import React from 'react';
import { Pause, Play, Power, Sparkles } from 'lucide-react';

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

// ── FIX 1 — the REAL agent-ON strip (.abar), OFF → ON. ──────────────────────
// Faithful replica of AgentStrip's markup so the real 13-page-hero CSS lights
// it up: the animated dark-purple gradient (.abar-on::before / abarFlow), the
// pulsing spark, the pending pill and the live budget meter. The single ON/OFF
// control keeps role="switch" so it toggles the hero product card alongside it.
export const HeroAgentStrip = ({ on, pressing, onToggle }) => {
  const status = on ? 'on' : 'off';
  return (
    <div className="lve-hero-abar">
      <div className={`abar abar-${status}`}>
        <span className="ab-spark">
          <Sparkles size={15} strokeWidth={2} />
          {on ? <span className="ab-pulse" aria-hidden="true" /> : null}
        </span>
        <span className="ab-label">{on ? 'Agent on' : 'Agent off'}</span>
        {on ? <span className="ab-pending" title="3 awaiting your review">3</span> : null}
        <span className="ab-tick" title={on ? 'Advanced Maya Chen to Review · 2m ago' : 'Turn the agent on to work this role.'}>
          {on ? 'Advanced Maya Chen to Review · 2m ago' : 'Turn the agent on to work this role.'}
        </span>
        {on ? (
          <span className="ab-budget" title="Covers pre-screen, scoring, search, assessments and the agent on this role.">
            <span className="ab-budget-amt">$18<span className="of"> / $50</span></span>
            <span className="ab-budget-bar"><i style={{ width: '36%' }} /></span>
          </span>
        ) : null}
        <span className="ab-actions">
          <button
            type="button"
            role="switch"
            aria-checked={on}
            aria-label={on ? 'Agent on. Turn the agent off.' : 'Agent off. Turn the agent on.'}
            className={`ab-btn primary${pressing ? ' is-pressing' : ''}`}
            onClick={onToggle}
          >
            {on ? <Pause size={11} strokeWidth={2} /> : <Play size={11} strokeWidth={2} fill="currentColor" />}
            {on ? 'Pause' : 'Turn on'}
          </button>
          {on ? (
            <button
              type="button"
              className="ab-btn ic"
              title="Turn off agent for this role"
              aria-label="Turn off agent"
              onClick={onToggle}
            >
              <Power size={13} strokeWidth={2} />
            </button>
          ) : null}
        </span>
      </div>
    </div>
  );
};

// ── FIX 2 — the three embedded real product surfaces. ───────────────────────
export const HeroDecisionArtifact = () => (
  <ArtifactFrame browserPath="app.taali.ai/home" className="lve-frame--hero">
    <AgentDecisionCard
      decision={HERO_DECISION}
      onApprove={noop}
      onAlternative={noop}
      onTeach={noop}
      onSnooze={noop}
      onNavigate={noop}
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

export const ScorecardArtifact = () => (
  <ArtifactFrame browserPath="app.taali.ai/candidates/1042">
    <AssessmentScorecard assessment={SCORECARD_ASSESSMENT} />
  </ArtifactFrame>
);

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
