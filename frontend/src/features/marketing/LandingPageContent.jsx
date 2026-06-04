import React, { useEffect, useRef, useState } from 'react';
import { Check, Pause, Play } from 'lucide-react';

import { AssessmentRuntimePreviewView } from '../assessment_runtime/AssessmentRuntimePreviewView';
import { ActivityFeed } from '../home/ActivityFeed';
import { PRODUCT_WALKTHROUGH, PRODUCT_WALKTHROUGH_TASK } from '../demo/productWalkthroughModels';
import {
  consumePendingMarketingSection,
  scrollToMarketingSection,
} from '../../lib/marketingScroll';
import { MarketingNav, TaaliLogo } from '../../shared/layout/TaaliLayout';

const containerClass = 'mx-auto max-w-[85rem] px-6 md:px-10 xl:px-16';

// Mock rows for the marketing decision feed. Shape mirrors the
// AgentDecisionPayload the live <ActivityFeed> consumes on /home, so the
// feed renders with the same score chips, role pills, confidence line, and
// decision-type badges the recruiter sees in product. Each row carries
// role_name (drives RolePill), taali_score (ScoreChip — null for pre-screen
// rejects, which aren't scored), and confidence (drives "agent N% confident").
// The decision types span the agent's real vocabulary: advance_to_interview
// (ADVANCE), escalate_low_confidence (ESCALATE — sub-agents disagreed),
// skip_assessment_reject (pre-screen REJECT, deeper red, unscored), and a
// post-assessment reject overridden + taught back to the agent. Timestamps
// are anchored to a recent moment so formatRelativeAge renders "Xm/h ago".
const _NOW = Date.now();
const MARKETING_DECISION_FEED_ROWS = [
  {
    id: 312,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Maya Chen',
    application_id: 1042,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 88,
    confidence: 0.91,
    reasoning:
      "Clears every must-have with strong AWS + Python evidence. Assessment 88/100 — top of this role's pipeline. Ready for the technical panel.",
    created_at: new Date(_NOW - 6 * 60 * 1000).toISOString(),
  },
  {
    id: 311,
    status: 'pending',
    decision_type: 'escalate_low_confidence',
    candidate_name: 'Aisha Bello',
    application_id: 1031,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 64,
    confidence: 0.5,
    reasoning:
      "Sub-agents split on systems-design depth — two said advance, one said assess again. I can't call this one confidently. Over to you.",
    created_at: new Date(_NOW - 23 * 60 * 1000).toISOString(),
  },
  {
    id: 309,
    status: 'pending',
    decision_type: 'skip_assessment_reject',
    candidate_name: 'Marco Rossi',
    application_id: 1024,
    role_id: 112,
    role_name: 'Data Engineer',
    taali_score: null,
    reasoning:
      "Pre-screen: the must-have Spark / streaming experience isn't evidenced, and the AI-tooling claims have no supporting projects. Not worth an assessment seat.",
    created_at: new Date(_NOW - 38 * 60 * 1000).toISOString(),
  },
  {
    id: 305,
    status: 'approved',
    decision_type: 'advance_to_interview',
    candidate_name: 'Priya Raman',
    application_id: 1003,
    role_id: 112,
    role_name: 'Data Engineer',
    taali_score: 84,
    human_disposition: 'approved',
    resolved_at: new Date(_NOW - 18 * 60 * 1000).toISOString(),
  },
  {
    id: 301,
    status: 'overridden',
    decision_type: 'reject',
    candidate_name: 'Jonas Weber',
    application_id: 994,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 58,
    human_disposition: 'taught',
    resolution_note: 'override → advance',
    resolved_at: new Date(_NOW - 52 * 60 * 1000).toISOString(),
  },
];

const dashboardCandidates = [
  { name: 'Candidate', status: 'submitted' },
  { name: 'Candidate', status: 'in-progress' },
  { name: 'Candidate', status: 'submitted' },
  { name: 'Candidate', status: 'submitted' },
  { name: 'More candidates', status: 'view all', avatar: '+' },
];

const timelineItems = [
  { label: 'plan', pill: 'plan', body: 'Whether they thought before they prompted - design notes, tradeoffs, decisions.' },
  { label: 'prompt', pill: 'claude', body: 'Prompt quality - scoped vs. vague, with-context vs. cold.', tone: 'ai' },
  { label: 'recover', pill: 'claude', body: 'How they handled an incorrect AI suggestion - accepted, rejected, verified.', tone: 'ai' },
  { label: 'test', pill: 'test', body: 'Whether tests came before or after the AI implementation.', tone: 'pass' },
  { label: 'refactor', pill: 'refactor', body: 'Whether they shipped AI boilerplate or tightened it to your team\'s style.' },
  { label: 'ship', pill: 'ship', body: 'Final state - tests passing, edge cases handled, graceful failure paths.', tone: 'pass' },
];

const sixAxes = [
  'Prompt quality',
  'Error recovery',
  'Context utilization',
  'Independence',
  'Design thinking',
  'Debugging strategy',
];

const questionBankRows = [
  ['AI.01', 'GenAI production readiness review', 'Medium', 'amber'],
  ['AI.01A', 'Tighten safety defaults during moderation outages', 'Hard', 'red'],
  ['DE.01', 'AWS Glue pipeline recovery', 'Medium', 'amber'],
  ['DE.01A', 'Fix schema drift, dedupe, and bookmark trust', 'Hard', 'red'],
];

const runtimeSignalCards = [
  {
    title: 'Prompt quality',
    body: 'Whether the prompt was scoped and sequenced, with the exact prompt text linked back to the timeline.',
  },
  {
    title: 'Error recovery',
    body: 'Whether the candidate verified, rejected, or accepted incorrect AI suggestions before they touched production logic.',
  },
  {
    title: 'Independence',
    body: 'Where the candidate delegated to AI versus where they wrote and owned the critical reasoning themselves.',
  },
];

const howItWorksSteps = [
  {
    step: 'STEP 01',
    title: 'Start from the job requirement.',
    body: 'Pick the role, stack, and bar you care about. Taali maps the assessment to the workflow you actually hire for.',
    meta: 'calibrated to your team',
  },
  {
    step: 'STEP 02',
    title: 'Invite the candidate.',
    body: 'They receive a simple link into the in-browser workspace with the task, repo, editor, and AI tools ready to go.',
    meta: 'simple invite flow',
  },
  {
    step: 'STEP 03',
    title: 'They complete the task.',
    body: 'We capture the prompts, tests, edits, and decision-making that show how the candidate actually works with AI.',
    meta: 'silent scoring throughout',
  },
  {
    step: 'STEP 04',
    title: 'Review and decide.',
    body: 'Recruiters and hiring managers get the report, AI-collaboration evidence, transcript context, and ATS-ready decision view in one place.',
    meta: 'signal delivered on submit',
  },
];

const proofItems = [
  {
    title: 'Live stack',
    body: 'Candidates ship real code on your real tech - TypeScript, Python, Go, whatever you run.',
  },
  {
    title: 'Real AI',
    body: 'Claude, Cursor, Copilot in-browser. We do not block them - we score how they use them.',
  },
  {
    title: 'Every keystroke',
    body: 'Session replay, prompt log, test runs, and evaluation - all tied to the final report.',
  },
  {
    title: 'Your bar',
    body: 'Calibrate scoring to your team. Taali measures what you care about, not generic rubrics.',
  },
];

const footerColumns = [
  {
    title: 'Product',
    items: [
      { label: 'Book a demo', page: 'demo-lead' },
      { label: 'AI collab score', section: 'platform' },
      { label: 'Question bank', section: 'platform' },
      { label: 'Integrations', section: 'platform' },
      { label: 'Product walkthrough', page: 'showcase' },
    ],
  },
  {
    title: 'Company',
    items: [
      { label: 'Manifesto', section: 'problem' },
      { label: 'Careers', page: 'demo-lead' },
      { label: 'Blog', page: 'demo-lead' },
      { label: 'Contact', href: 'mailto:hello@taali.ai' },
    ],
  },
  {
    title: 'Resources',
    items: [
      { label: 'Sample walkthrough', page: 'showcase' },
      { label: 'Rubric library', section: 'platform' },
      { label: 'Docs', page: 'demo-lead' },
      { label: 'Security', page: 'demo-lead' },
    ],
  },
];

const SectionHeading = ({ kicker, title, copy, children = null }) => (
  <div className="mb-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_420px] lg:items-end">
    <div>
      <div className="kicker">{kicker}</div>
      <h2 className="mt-3 font-[var(--font-display)] text-[clamp(38px,5vw,60px)] font-semibold leading-[0.95] tracking-[-0.04em]">
        {title}
      </h2>
    </div>
    <div>
      <p className="max-w-[26.25rem] text-[0.9375rem] leading-7 text-[var(--mute)]">{copy}</p>
      {children}
    </div>
  </div>
);

const PlatformCard = ({ kicker, title, body, children, className = '' }) => (
  <div className={`rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)] ${className}`.trim()}>
    <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.12em] text-[var(--purple)]">{kicker}</div>
    <h3 className="mt-3 font-[var(--font-display)] text-[2.125rem] leading-[1.02] tracking-[-0.03em]">{title}</h3>
    <p className="mt-3 text-[0.875rem] leading-7 text-[var(--mute)]">{body}</p>
    {children}
  </div>
);

export const LandingPage = ({ onNavigate }) => {
  const showcaseAssessment = PRODUCT_WALKTHROUGH_TASK;
  const runtimeShowcase = PRODUCT_WALKTHROUGH.runtime;

  // Hero AgentHeader is interactive. The agent flips ON only when the
  // visitor scrolls to the edge of the next section, simulating a real
  // click on the "Turn on agent" button (button presses for 220ms, then
  // state flips with the same cross-fade animation we ship in-app). Once
  // the visitor clicks the toggle themselves, refs lock so we don't keep
  // overriding their state.
  const [agentOn, setAgentOn] = useState(false);
  const [pressing, setPressing] = useState(false);
  const userToggledRef = useRef(false);
  const autoTriggeredRef = useRef(false);

  const toggleAgent = () => {
    userToggledRef.current = true;
    setAgentOn((value) => !value);
  };

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const sectionId = consumePendingMarketingSection() || window.location.hash.replace(/^#/, '');
    if (!sectionId) return undefined;

    const timer = window.setTimeout(() => {
      scrollToMarketingSection(sectionId, { behavior: 'smooth' });
    }, 40);

    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    if (typeof IntersectionObserver === 'undefined') return undefined;
    const target = document.getElementById('how-it-works');
    if (!target) return undefined;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting && !autoTriggeredRef.current && !userToggledRef.current) {
            autoTriggeredRef.current = true;
            // Fake a click on the OFF panel's "Turn on agent" button:
            // press for 220ms (CSS scales it down + dims), then flip state.
            setPressing(true);
            window.setTimeout(() => {
              setAgentOn(true);
              setPressing(false);
            }, 220);
            observer.disconnect();
            break;
          }
        }
      },
      // Fire as soon as the next section's top edge enters the viewport,
      // i.e. just before the visitor breaks past the hero.
      { threshold: 0 },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <MarketingNav onNavigate={onNavigate} />

      <section className="relative overflow-hidden pb-16 pt-12 md:pb-24 md:pt-16">
        <div className={containerClass}>
          <div className="mc-kicker" style={{ display: 'inline-flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <span
              aria-hidden="true"
              style={{
                display: 'inline-flex',
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: 'var(--purple)',
                boxShadow: '0 0 0 4px var(--purple-soft)',
              }}
            />
            AGENTIC-FIRST · AI-NATIVE HIRING
          </div>
          <h1
            className="font-[var(--font-display)] font-semibold"
            style={{
              fontSize: 'clamp(44px,6.4vw,72px)',
              lineHeight: 1.02,
              letterSpacing: '-0.04em',
              margin: '0 0 22px',
              maxWidth: 980,
            }}
          >
            The recruiter&apos;s <em className="not-italic text-[var(--purple)]">agent.</em><br />
            Built to hire engineers<br />who ship with AI<span className="text-[var(--purple)]">.</span>
          </h1>
          <p className="text-[1.125rem] leading-[1.55] text-[var(--ink-2)]" style={{ maxWidth: 640, margin: '0 0 22px' }}>
            Taali is the first agentic hiring platform — and the only one that measures how candidates actually <em className="not-italic font-medium text-[var(--ink)]">use AI</em> on the job. The agent <em className="not-italic font-medium text-[var(--ink)]">decides</em> what to work on each cycle — fetch CVs, score, send assessments, queue advances or rejects — paces it within budget, and asks you when it can&apos;t decide on its own. Every consequential call still goes through you.
          </p>
          <div className="flex flex-wrap gap-3 text-[0.8125rem] text-[var(--ink-2)]" style={{ marginBottom: 30 }}>
            {[
              { k: 'AGENTIC', v: 'Runs your pipeline 24/7 — pauses for your judgment' },
              { k: 'AI-NATIVE', v: 'The only platform that scores AI fluency in hands-on tasks' },
            ].map((badge) => (
              <div
                key={badge.k}
                className="inline-flex items-center gap-2.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3.5 py-2"
              >
                <span className="font-[var(--font-mono)] text-[0.65625rem] font-semibold tracking-[0.08em] text-[var(--purple)]">
                  {badge.k}
                </span>
                <span>{badge.v}</span>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-3" style={{ marginBottom: 48 }}>
            <button
              type="button"
              className="btn btn-primary"
              style={{ height: 46, padding: '0 22px', fontSize: 14 }}
              onClick={() => onNavigate('demo-lead')}
            >
              Book a demo
            </button>
            <button
              type="button"
              className="btn btn-outline"
              style={{ height: 46, padding: '0 22px', fontSize: 14 }}
              onClick={() => onNavigate('showcase')}
            >
              Try the live walkthrough <span className="arrow">→</span>
            </button>
          </div>

          {/* Hero composition — browser frame showing the unified
              AgentHeader (HANDOFF unified-headers.md §2-§4): dark-purple
              slab with right-side Agent mode panel, then 4 KPI tiles. */}
          <div
            className="overflow-hidden rounded-[16px] border border-[var(--line)] bg-[var(--bg-2)]"
            style={{ boxShadow: '0 24px 60px -30px rgba(91,44,168,0.35)' }}
          >
            <div
              className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5 font-[var(--font-mono)] text-[0.6875rem] text-[var(--mute)]"
            >
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#f06' }} />
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#ffb020' }} />
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#39c66d' }} />
              <span className="ml-3">app.taali.ai/jobs</span>
            </div>
            {/* Live AgentHeader mock — same `.agent-running` / `.agent-quiet`
                classes and `.ah-bright-overlay` layer as the real product, so
                the OFF→ON cross-fade plays here exactly as it does in-app.
                Hero auto-flips ON shortly after mount; visitors can click
                the panel toggle to replay the transition. */}
            <div className={`agent-header ${agentOn ? 'agent-running' : 'agent-quiet'}`}>
              <span className="ah-bright-overlay" aria-hidden="true" />
              <div className="agent-header-inner">
                <div className="agent-header-left">
                  <div className="ah-headings">
                    <div className="ah-kicker">JOBS · 5 ACTIVE ROLES</div>
                    <div className="ah-title-row"><h1>5 active <em>roles</em></h1></div>
                    <p className="ah-subtitle">You&apos;re hiring. Star a role to keep its candidates flowing in automatically.</p>
                  </div>
                </div>
                <div className="agent-header-right">
                  {/* Live agent strip — the same persistent `.abar` box as the
                      product; the ON/OFF look crossfades in place (no remount). */}
                  <div className={`abar abar-${agentOn ? 'on' : 'off'}`}>
                    <span className="ab-spark">
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
                      </svg>
                      {agentOn ? <span className="ab-pulse" aria-hidden="true" /> : null}
                    </span>
                    <span className="ab-label">{agentOn ? 'Agent on' : 'Agent off'}</span>
                    {agentOn ? <span className="ab-pending">3</span> : null}
                    <span className="ab-tick">{agentOn ? 'Advanced Maya Chen · 2m ago' : 'Set a monthly cap, then turn on'}</span>
                    {agentOn ? (
                      <span className="ab-budget">
                        <span className="ab-budget-amt">$31<span className="of"> / $50</span></span>
                        <span className="ab-budget-bar"><i style={{ width: '62%' }} /></span>
                      </span>
                    ) : null}
                    {agentOn ? (
                      <span className="ab-actions">
                        <button type="button" className="ab-btn" onClick={toggleAgent}>
                          <Pause size={11} strokeWidth={2} /> Pause
                        </button>
                      </span>
                    ) : (
                      <span className="ab-activate">
                        <span className="ab-capbox"><span className="pfx">$</span><input type="number" defaultValue={50} aria-label="Monthly budget" inputMode="numeric" readOnly /><span className="sfx">/mo</span></span>
                        <button type="button" className={`ab-btn primary${pressing ? ' is-pressing' : ''}`} onClick={toggleAgent}>
                          <Play size={11} strokeWidth={2} fill="currentColor" /> Turn on
                        </button>
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
            <div className="px-6 py-5 bg-[var(--bg-2)]">
              <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
                {[
                  { k: 'CANDIDATES PROCESSED', v: '847', d: 'this week' },
                  { k: 'INVITATIONS SENT', v: '312', d: 'auto-paced' },
                  { k: 'AWAITING YOU', v: '7', d: 'review' },
                  { k: 'BUDGET USED', v: '62%', d: '$31 of $50' },
                ].map((tile) => (
                  <div key={tile.k} className="mc-jobs-kpi" style={{ background: 'var(--bg-2)' }}>
                    <div className="k">{tile.k}</div>
                    <div className="v">{tile.v}</div>
                    <div className="d">{tile.d}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* HOW THE AGENT WORKS — 3-step section, white surface */}
      <section id="how-it-works" className="border-t border-[var(--line)] bg-[var(--bg-2)]">
        <div className={`${containerClass} py-20`}>
          <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.14em] text-[var(--purple)]">
            HOW THE AGENT WORKS
          </div>
          <h2 className="mt-3 max-w-[52.5rem] font-[var(--font-display)] text-[clamp(32px,4vw,42px)] font-semibold leading-[1.1] tracking-[-0.025em] text-[var(--ink)]">
            An autonomous agent in your pipeline. <em className="not-italic text-[var(--purple)]">Built for the AI-native hire.</em>
          </h2>
          <p className="mt-5 max-w-[42.5rem] text-[0.96875rem] leading-[1.6] text-[var(--ink-2)]">
            Taali runs three loops continuously — triage, assess, decide — and pauses the moment your judgment is needed.
            Every assessment puts the candidate in a real IDE with AI in their hand, then measures how well they wield it.
          </p>
          <div className="mt-14 grid gap-7 lg:grid-cols-3">
            {[
              {
                n: '01',
                t: 'Triage — autonomously',
                d: "Every cycle, the agent surveys the role, decides where the work is — fetch CVs, pre-screen, score, send assessments, queue advances or rejects — and pauses to ask you when it needs input it can't derive on its own. You set the criteria once; it works the pipeline 24/7 within the budget you set.",
              },
              {
                n: '02',
                t: 'Assess — for the AI era',
                d: "Hands-on, role-relevant tasks in a chat-first workspace — Claude in the candidate's hands. We track every prompt, paste, and decision — then score AI collaboration alongside craft. The only platform that tells you whether a candidate can actually ship with AI.",
              },
              {
                n: '03',
                t: 'Decide — with you in charge',
                d: 'A standing report per candidate: score, dimension radar, AI-usage trace, interview-ready questions. The agent recommends; you approve. Every consequential call is yours.',
              },
            ].map((step) => (
              <div key={step.n} className="border-t border-[var(--ink)] pt-7">
                <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.1em] text-[var(--purple)]">
                  {step.n} · TAALI
                </div>
                <h3 className="mt-2.5 font-[var(--font-display)] text-[1.625rem] font-semibold tracking-[-0.015em] text-[var(--ink)]">
                  {step.t}
                </h3>
                <p className="mt-2.5 text-[0.90625rem] leading-[1.55] text-[var(--ink-2)]">{step.d}</p>
              </div>
            ))}
          </div>

          {/* Decision feed — uses the live <ActivityFeed /> component
              from features/home (the same one rendered on the Hub at
              /home), fed mock rows that match its expected shape.
              Wrapped in browser chrome so the visual reads as a product
              snapshot, not a marketing illustration. */}
          <div className="mt-14 overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
            <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5 font-[var(--font-mono)] text-[0.6875rem] text-[var(--mute)]">
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#f06' }} />
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#ffb020' }} />
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#39c66d' }} />
              <span className="ml-3">app.taali.ai/home</span>
              <span className="ml-auto rounded-full bg-[color:var(--bg)] px-2 py-0.5 text-[0.625rem] font-semibold text-[var(--mute)]">Locked preview</span>
            </div>
            <div className="px-5 py-5">
              <ActivityFeed
                rows={MARKETING_DECISION_FEED_ROWS}
                selectedId={null}
                onSelect={() => {}}
                onNavigate={() => {}}
                subtitle="Every call the agent made across your open roles today — advance, escalate, pre-screen reject. Approve, override, or teach it back in one click."
              />
            </div>
          </div>
        </div>
      </section>

      {/* WE MEASURE HOW CANDIDATES USE AI — the differentiator (white bg per HANDOFF v2 §1) */}
      <section id="platform" className="border-t border-[var(--line)] bg-white">
        <div className={`${containerClass} py-20`}>
          <div className="grid gap-16 lg:grid-cols-[1fr_1.1fr] lg:items-center">
            <div>
              <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.14em] text-[var(--purple)]">
                AI-NATIVE ASSESSMENT
              </div>
              <h2 className="mt-3 font-[var(--font-display)] text-[clamp(34px,4.6vw,44px)] font-semibold leading-[1.05] tracking-[-0.03em] text-[var(--ink)]">
                You hire people <em className="not-italic text-[var(--purple)]">who use AI.</em><br />
                We&apos;re the only platform that measures it.
              </h2>
              <p className="mt-5 text-[1rem] leading-[1.6] text-[var(--ink-2)]">
                Every assessment opens a chat-first workspace — Claude at the centre, your repo, a real editor, and a live terminal around it — exactly the way engineers ship now.
                Behind the scenes the runtime captures every prompt, paste, edit, file open, test run, and commit, time-stamped to the second.
                Those traces feed a six-axis rubric — systems design, code craft, reasoning under pressure, AI collaboration, release safety, communication — so how a candidate works with AI is scored as a first-class dimension alongside craft.
              </p>
              <ul className="mt-7 flex flex-col gap-3.5">
                {[
                  { t: 'AI collaboration score', d: 'Did they prompt well? Catch a hallucination? Know when not to use it?' },
                  { t: 'Prompt-by-prompt replay', d: 'See exactly how they worked the agent — not just the final code.' },
                  { t: 'Full session telemetry', d: 'Edit timeline, test runs, terminal output, file opens — everything tied back to the final report.' },
                  { t: 'Autopilot detection', d: 'We flag candidates who pasted without reading. Calibrated, not punitive.' },
                ].map((bullet) => (
                  <li key={bullet.t} className="flex items-start gap-3">
                    <span className="mt-0.5 inline-flex h-[1.375rem] w-[1.375rem] flex-shrink-0 items-center justify-center rounded-full bg-[var(--purple)] text-white">
                      <Check size={13} strokeWidth={2.6} aria-hidden="true" />
                    </span>
                    <div>
                      <div className="text-[0.90625rem] font-medium text-[var(--ink)]">{bullet.t}</div>
                      <div className="mt-0.5 text-[0.8125rem] leading-[1.5] text-[var(--ink-2)]">{bullet.d}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            {/* Standing report — the six recruiter-facing axes the live
                CandidateStandingReportPage renders (computeFluencyAxes rolls
                the atomic backend scores into these six). Verdict uses the
                production band vocabulary (Strong Hire ≥ 80). Mock scores. */}
            <div className="overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
              <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3 font-[var(--font-mono)] text-[0.71875rem] text-[var(--mute)]">
                <span>MAYA CHEN · CANDIDATE REPORT</span>
                <span className="font-semibold text-[var(--purple)]">Strong Hire · Tali 86</span>
              </div>
              <div className="space-y-4 px-5 py-6">
                {[
                  { label: 'Systems design', score: 86 },
                  { label: 'Code craft', score: 88 },
                  { label: 'Reasoning under pressure', score: 82 },
                  { label: 'AI collaboration', score: 90 },
                  { label: 'Release safety', score: 79 },
                  { label: 'Communication', score: 84 },
                ].map(({ label, score }) => (
                  <div
                    key={label}
                    className="grid grid-cols-[184px_minmax(0,1fr)] items-center gap-3"
                  >
                    <div className="text-[0.875rem] text-[var(--ink)]">{label}</div>
                    <div className="h-2 overflow-hidden rounded-full bg-[var(--line)]">
                      <div className="h-2 rounded-full bg-[var(--purple)]" style={{ width: `${score}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* IDE preview at the end of the AI-NATIVE section — the actual
              workspace component (AssessmentRuntimePreviewView) in
              staticPreview mode: a non-interactive snapshot of a live
              candidate session — brief collapsed, the code editor open on
              the right, and a real candidate↔Claude transcript in the chat.
              Scaled to 80% via CSS transform so the IDE renders at its
              natural ~1440-wide layout and visually fits the landing band
              without cramping. The wrapper compensates for the scale
              (width 125% = 1/0.8); the outer band clips with overflow-hidden.
              Height is sized to show brief → workspace → dock without a
              scrollbar (the snapshot has no internal scroll). */}
          <p className="mt-12 mb-3 text-[0.875rem] text-[var(--ink-2)]">
            <strong className="text-[var(--ink)]">Candidates work here.</strong>{' '}
            Claude sits at the centre — they drive the task in conversation, open and edit files beside it, run tests in a live terminal. We watch every prompt.
          </p>
          <div className="overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
            <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5 font-[var(--font-mono)] text-[0.6875rem] text-[var(--mute)]">
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#f06' }} />
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#ffb020' }} />
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#39c66d' }} />
              <span className="ml-3">app.taali.ai/assess/preview</span>
              <span className="ml-auto rounded-full bg-[color:var(--bg)] px-2 py-0.5 text-[0.625rem] font-semibold text-[var(--mute)]">Locked preview</span>
            </div>
            <div style={{ height: 740, overflow: 'hidden', position: 'relative' }}>
              <div
                style={{
                  width: '140%',
                  height: 'calc(100% / 0.714)',
                  transform: 'scale(0.714)',
                  transformOrigin: 'top left',
                }}
              >
                <AssessmentRuntimePreviewView
                  staticPreview
                  heightClass="h-full"
                  lightMode={false}
                  taskName="Revenue Recovery Incident"
                  taskRole="Senior Backend Engineer"
                  taskContext="Restore the batch revenue-recovery flow before finance close."
                />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Trust strip removed — early product, no public customer logos. */}

      {/* BOTTOM CTA — softened, token-based purple gradient (v4) */}
      <section className="bg-[var(--bg)]">
        <div className={`${containerClass} py-16`}>
          <div
            className="relative overflow-hidden rounded-[18px] px-12 py-14"
            style={{
              background:
                'linear-gradient(135deg, color-mix(in oklab, var(--purple) 75%, #000) 0%, var(--purple) 60%, var(--purple-lav) 100%)',
              color: '#fff',
            }}
          >
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0"
              style={{ background: 'radial-gradient(600px 280px at 80% 20%, rgba(255,255,255,0.18), transparent 60%)' }}
            />
            <div className="relative flex flex-wrap items-center justify-between gap-8">
              <div>
                <h2 className="font-[var(--font-display)] text-[clamp(28px,3.6vw,40px)] font-semibold leading-[1.05] tracking-[-0.025em]">
                  Ready to put the agent to work?
                </h2>
                <p className="mt-3 max-w-[35rem] text-[1rem] leading-[1.55] opacity-85">
                  Take the full product walkthrough — pre-loaded with a real role, no card, no install.
                  Or book a 20-minute demo with a founder and we&apos;ll run it on a role of yours.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className="inline-flex h-12 items-center gap-2 rounded-full px-7 text-[0.875rem] font-semibold text-white"
                  style={{ border: '1px solid rgba(255,255,255,0.55)', background: 'transparent' }}
                  onClick={() => onNavigate('showcase')}
                >
                  Open walkthrough →
                </button>
                <button
                  type="button"
                  className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[0.875rem] font-semibold text-[var(--purple)]"
                  style={{ boxShadow: '0 10px 28px -8px rgba(0,0,0,0.3)' }}
                  onClick={() => onNavigate('demo-lead')}
                >
                  Book a demo →
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t border-[var(--line)] bg-[var(--ink)] text-[var(--bg)]">
        <div className={`${containerClass} py-14`}>
          <div className="grid gap-10 lg:grid-cols-[1.1fr_.9fr_.9fr_.9fr]">
            <div>
              <TaaliLogo onClick={() => onNavigate('landing')} wordmarkClassName="!text-[var(--bg)]" />
              <p className="mt-5 max-w-[17.5rem] text-[0.9375rem] leading-7 text-[var(--taali-inverse-text)] opacity-70">
                AI-native technical assessments that <span className="font-[var(--font-display)] text-[var(--purple)]">tally</span> real skill.
              </p>
            </div>

            {footerColumns.map((column) => (
              <div key={column.title}>
                <h4 className="font-[var(--font-display)] text-[1.25rem] tracking-[-0.02em]">{column.title}</h4>
                <div className="mt-4 flex flex-col gap-3">
                  {column.items.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      className="w-fit text-left text-[0.875rem] text-[var(--taali-inverse-text)] opacity-70 transition hover:opacity-100"
                      onClick={() => {
                        if (item.href) {
                          window.location.href = item.href;
                          return;
                        }
                        if (item.section) {
                          scrollToMarketingSection(item.section);
                          return;
                        }
                        if (item.page) {
                          onNavigate(item.page);
                        }
                      }}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-12 font-[var(--font-display)] text-[clamp(72px,12vw,164px)] leading-none tracking-[-0.08em] text-[var(--taali-inverse-text)] opacity-[0.08]">
            taali<em className="text-[var(--purple)] not-italic">.</em>
          </div>

          <div
            className="mt-6 flex flex-col gap-3 border-t pt-5 text-[0.8125rem] text-[var(--taali-inverse-text)] md:flex-row md:items-center md:justify-between"
            style={{
              borderColor: 'color-mix(in oklab, var(--taali-inverse-text) 10%, transparent)',
              color: 'color-mix(in oklab, var(--taali-inverse-text) 52%, transparent)',
            }}
          >
            <div>© 2026 Taali, Inc. · San Francisco</div>
            <button
              type="button"
              className="w-fit text-left text-[var(--taali-inverse-text)] opacity-70 transition hover:opacity-100"
              onClick={() => {
                window.location.href = 'mailto:hello@taali.ai';
              }}
            >
              hello@taali.ai
            </button>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;
