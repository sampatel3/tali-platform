import React from 'react';

import { ActivityFeed } from '../../home/ActivityFeed';
import { AssessmentScorecard } from '../../candidates/AssessmentScorecard';
import { LandingPreviewNav, LandingPreviewFooter, ClosingCtaBand, containerClass } from './LandingPreviewChrome';
import { LandingPreviewHero } from './LandingPreviewHero';
import { ValuePillars, EvidenceStrip } from './LandingPreviewSections';

// ---------------------------------------------------------------------------
// Fixture data for the TWO real product moments embedded below.
// ---------------------------------------------------------------------------

const _NOW = Date.now();

// (1) "Your morning queue" — one pending advance-to-interview decision, shaped
// exactly like the AgentDecision rows the live <ActivityFeed> renders on /home.
// Humanized 1–2 sentence reasoning, a Taali score, and score provenance so the
// requirement/score chips + provenance pill render as in production.
const MORNING_QUEUE_ROWS = [
  {
    id: 312,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Maya Chen',
    application_id: 1042,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 88,
    score_summary: {
      score_provenance: {
        engine_version: '2.1.0',
        scored_at: new Date(_NOW - 12 * 60 * 1000).toISOString(),
        model: 'Sonnet',
      },
    },
    confidence: 0.91,
    reasoning:
      "Clears every must-have with strong AWS + Python evidence, and the assessment landed at 88 — top of this role's pipeline. Ready for the technical panel.",
    created_at: new Date(_NOW - 8 * 60 * 1000).toISOString(),
  },
];

// (2) "The proof behind every score" — a fixture assessment whose
// score_breakdown.rubric_grading.fluency_4d drives the real <AssessmentScorecard>
// (computeScorecard reads it rubric-first). Each axis also expands into graded
// rubric dimensions via evaluation_rubric + score_breakdown.rubric_grading.
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
            'Owned the schema + retry design themselves and handed the boilerplate to Claude — the load-bearing calls stayed with the candidate.',
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

// ---------------------------------------------------------------------------
// Annotation scaffold — abstract labels that point at a framed artifact. On
// desktop the callouts float to the side; on mobile they stack underneath as a
// plain legend. Purely presentational.
// ---------------------------------------------------------------------------

const ArtifactFrame = ({ browserPath, children }) => (
  <div className="overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
    <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5 font-[var(--font-mono)] text-[0.6875rem] text-[var(--mute)]">
      <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#f06' }} />
      <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#ffb020' }} />
      <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#39c66d' }} />
      <span className="ml-3">{browserPath}</span>
      <span className="ml-auto rounded-full bg-[color:var(--bg)] px-2 py-0.5 text-[0.625rem] font-semibold text-[var(--mute)]">
        Live component
      </span>
    </div>
    <div className="px-4 py-5 sm:px-6">{children}</div>
  </div>
);

const Callouts = ({ items }) => (
  <ul className="mt-6 grid gap-4 sm:grid-cols-3 lg:mt-0 lg:grid-cols-1 lg:gap-6">
    {items.map((item, i) => (
      <li key={item.label} className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="mt-0.5 inline-flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-[var(--purple)] font-[var(--font-mono)] text-[0.6875rem] font-semibold text-white"
        >
          {i + 1}
        </span>
        <div>
          <div className="text-[0.90625rem] font-semibold text-[var(--ink)]">{item.label}</div>
          <div className="mt-0.5 text-[0.8125rem] leading-[1.5] text-[var(--ink-2)]">{item.body}</div>
        </div>
      </li>
    ))}
  </ul>
);

const NarrativeArtifact = ({ kicker, title, blurb, callouts, browserPath, children }) => (
  <section className="border-t border-[var(--line)] bg-[var(--bg)]">
    <div className={`${containerClass} py-20 md:py-24`}>
      <div className="mb-10 max-w-[46rem]">
        <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.14em] text-[var(--purple)]">
          {kicker}
        </div>
        <h2 className="mt-3 font-[var(--font-display)] text-[clamp(28px,3.8vw,44px)] font-semibold leading-[1.05] tracking-[-0.03em] text-[var(--ink)]">
          {title}
        </h2>
        <p className="mt-4 text-[0.96875rem] leading-[1.6] text-[var(--ink-2)]">{blurb}</p>
      </div>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_260px] lg:items-start lg:gap-12">
        <ArtifactFrame browserPath={browserPath}>{children}</ArtifactFrame>
        <Callouts items={callouts} />
      </div>
    </div>
  </section>
);

// VARIANT B — "Narrative + one live artifact". Same hero as A. Then a narrative
// scroll with EXACTLY TWO real product moments (the Home decision card and the
// 5-Ds scorecard), each framed large and annotated, with abstract value
// sections reused from A between and after.
export const LandingVariantB = ({ onNavigate }) => (
  <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
    <LandingPreviewNav onNavigate={onNavigate} />

    <LandingPreviewHero
      onNavigate={onNavigate}
      headline={(
        <h1
          className="font-[var(--font-display)] font-semibold"
          style={{
            fontSize: 'clamp(44px,6.6vw,78px)',
            lineHeight: 1.0,
            letterSpacing: '-0.045em',
            margin: '0 0 24px',
            maxWidth: 1040,
          }}
        >
          Hiring has an AI-fluency problem.
          <br />
          <span className="text-[var(--purple)]">
            Here&apos;s what our agent hands you every morning.
          </span>
        </h1>
      )}
    />

    <NarrativeArtifact
      kicker="YOUR MORNING QUEUE"
      title="You open Taali. The agent has already worked your pipeline."
      blurb="This is the real decision card from the Home queue — every call the agent made overnight, waiting for your approval. Nothing moves without you."
      browserPath="app.taali.ai/home"
      callouts={[
        { label: 'Evidence-linked reasoning', body: 'One or two honest sentences, tied to the must-haves and the assessment behind them.' },
        { label: 'Requirement + score chips', body: 'The Taali score and its provenance — how, when, and on which engine it was scored.' },
        { label: 'One-click approve', body: 'Approve, override, or teach it back. The agent recommends; the decision is yours.' },
      ]}
    >
      <ActivityFeed
        rows={MORNING_QUEUE_ROWS}
        selectedId={null}
        onSelect={() => {}}
        onNavigate={() => {}}
        kicker="AWAITING YOU · 1 DECISION"
        title="Your morning queue"
        subtitle="The agent advanced Maya Chen to interview overnight. Approve, override, or teach it back in one click."
      />
    </NarrativeArtifact>

    <ValuePillars condensed />

    <NarrativeArtifact
      kicker="THE PROOF BEHIND EVERY SCORE"
      title="Every score opens into the evidence that made it."
      blurb="This is the real 5-Ds scorecard from the candidate report. Each dimension rolls up from graded criteria — expand one to see the reasoning and the exact moments it cites."
      browserPath="app.taali.ai/candidates/1042"
      callouts={[
        { label: 'The 5 Ds', body: 'Delegation, Description, Discernment, Diligence, Deliverable — how they worked with AI, scored.' },
        { label: 'Rolls up from criteria', body: 'Each axis is an average of graded rubric criteria, not an opaque number.' },
        { label: 'Cited to the transcript', body: 'Expand a dimension to read the reasoning and the prompts, edits, and test runs behind it.' },
      ]}
    >
      <AssessmentScorecard assessment={SCORECARD_ASSESSMENT} />
    </NarrativeArtifact>

    <EvidenceStrip />
    <ClosingCtaBand onNavigate={onNavigate} />
    <LandingPreviewFooter onNavigate={onNavigate} />
  </div>
);

export default LandingVariantB;
