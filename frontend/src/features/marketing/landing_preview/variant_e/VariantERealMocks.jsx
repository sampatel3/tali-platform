import React, { useEffect, useRef } from 'react';
import {
  MOTION_DURATION,
  MOTION_EASE,
  MOTION_STAGGER,
  animate as animateValue,
  stagger,
  useAnimate,
  useInView,
  useReducedMotionSync,
} from '../../../../shared/motion';
import { AgentDecisionCard } from '../../../../shared/decisions/AgentDecisionCard';
import { AssessmentScorecard } from '../../../candidates/AssessmentScorecard';

// ---------------------------------------------------------------------------
// The REAL product surfaces the variant-E spine embeds, each in a light
// browser-chrome "Live component" frame:
//   • THE WEDGE (section 4) — the real 5-Ds <AssessmentScorecard>, animated.
//   • YOU STAY IN CONTROL (section 5) — a compact real <AgentDecisionCard>
//     (hideDecisionParts) as the decision glimpse.
// Fixtures mirror the exact shapes the live APIs return; data-brand="taali" on
// the variant root resolves every --purple / --grad-agent-on token.
// ---------------------------------------------------------------------------

const _NOW = Date.now();
const _prov = (hoursAgo, version = '2.1.0') => ({
  engine_version: version,
  scored_at: new Date(_NOW - hoursAgo * 60 * 60 * 1000).toISOString(),
});
const noop = () => {};

// (4) THE WEDGE — a fixture assessment whose score_breakdown.rubric_grading
// drives the real 5-Ds scorecard (computeScorecard reads it rubric-first).
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
      fluency_4d: { delegation: 86, description: 88, discernment: 90, diligence: 82, deliverable: 84 },
      dimensions: [
        {
          id: 'load_bearing_design', score: 8.6, rating: 'excellent',
          reasoning: 'Owned the schema + retry design themselves and handed the boilerplate to the agent — the load-bearing calls stayed with the candidate.',
          evidence_citations: ['prompt #3', 'edit db/models.py'],
        },
        {
          id: 'prompt_clarity', score: 8.8, rating: 'excellent',
          reasoning: 'Prompts named the constraint and the expected shape up front, so the agent had the context it needed.',
          evidence_citations: ['prompt #5'],
        },
        {
          id: 'caught_hallucination', score: 9.0, rating: 'excellent',
          reasoning: 'Spotted an invented API method in the agent output and corrected it before running — a planted trap, caught.',
          evidence_citations: ['prompt #7', 'terminal run #2'],
        },
        {
          id: 'verified_before_done', score: 8.2, rating: 'good',
          reasoning: 'Ran the failing test, watched it go green, then re-ran the suite before calling it done.',
          evidence_citations: ['test run #4'],
        },
        {
          id: 'shipped_correctness', score: 8.4, rating: 'good',
          reasoning: 'The revenue-recovery flow was restored and the finance-close guard held under the edge-case input.',
          evidence_citations: ['final diff'],
        },
      ],
    },
  },
};

// (5) YOU STAY IN CONTROL — one fully-evidenced pending decision, the shape
// AgentDecisionCard consumes. hideDecisionParts leaves the compact glimpse: the
// score ring + name/role, the agent-recommends verdict slab, and the three
// requirement bars (reasoning / evidence grid / trace / action bar dropped).
const CONTROL_DECISION = {
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
  created_at: new Date(_NOW - 6 * 60 * 1000).toISOString(),
  requirements: [
    { label: 'Distributed systems', score: 92 },
    { label: 'AWS depth', score: 84 },
    { label: 'Verification habit', score: 90 },
  ],
  // The redesigned recommendation kicker reads its source label + confidence chip
  // from decision_explanation — give the glimpse the same shape production sends.
  decision_explanation: {
    source: 'agent',
    summary: 'Advance recommended — distributed-systems depth and a proven verification habit clear the bar with room to spare.',
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

// Animate the real 5-Ds scorecard's reveal. On enter (once) the five D rows
// stagger in (fade + rise), each score bar fills 0 → value, and the score
// number ticks up. The AssessmentScorecard itself is untouched; we drive its
// DOM via scoped Motion timelines, and arm the
// hidden initial state through the `data-lve-sc` CSS contract.
const EASE = MOTION_EASE.enter;

export const ScorecardArtifact = () => {
  const [scope, animate] = useAnimate();
  const inView = useInView(scope, { amount: 0.35 });
  const reduced = useReducedMotionSync();
  const playedRef = useRef(false);

  useEffect(() => {
    const root = scope.current;
    if (!root) return undefined;

    if (reduced) {
      root.removeAttribute('data-lve-sc');
      return undefined;
    }
    root.setAttribute('data-lve-sc', 'true');
    if (!inView || playedRef.current) return undefined;
    playedRef.current = true;

    animate('.sc5-row', { opacity: [0, 1], y: [14, 0] }, { duration: MOTION_DURATION.reveal, delay: stagger(MOTION_STAGGER.default), ease: EASE });
    animate('.sc5-bar > i', { scaleX: [0, 1] }, { duration: MOTION_DURATION.data, delay: stagger(MOTION_STAGGER.default), ease: EASE });

    const controls = [];
    root.querySelectorAll('.sc5-score').forEach((el, i) => {
      const node = el.firstChild;
      if (!node) return;
      const target = parseInt(node.textContent, 10);
      if (!Number.isFinite(target)) return;
      node.textContent = '0';
      controls.push(animateValue(0, target, {
        duration: MOTION_DURATION.data,
        delay: i * MOTION_STAGGER.default,
        ease: EASE,
        onUpdate: (value) => { node.textContent = String(Math.round(value)); },
      }));
    });
    return () => controls.forEach((control) => control.stop());
  }, [inView, reduced, animate, scope]);

  return (
    <ArtifactFrame browserPath="app.taali.ai/candidates/1042" className="lve-frame--scorecard">
      <div ref={scope} data-lve-scorecard {...(reduced ? {} : { 'data-lve-sc': 'true' })}>
        <AssessmentScorecard assessment={SCORECARD_ASSESSMENT} />
      </div>
    </ArtifactFrame>
  );
};

export const ControlDecisionArtifact = () => (
  <ArtifactFrame browserPath="app.taali.ai/home" className="lve-frame--decision">
    <AgentDecisionCard
      decision={CONTROL_DECISION}
      onApprove={noop}
      onAlternative={noop}
      onTeach={noop}
      onSnooze={noop}
      onNavigate={noop}
      hideDecisionParts
    />
  </ArtifactFrame>
);
