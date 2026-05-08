import React, { useEffect, useRef, useState } from 'react';
import { Check, Pause, Play } from 'lucide-react';

import { AssessmentRuntimePreviewView } from '../assessment_runtime/AssessmentRuntimePreviewView';
import { PRODUCT_WALKTHROUGH, PRODUCT_WALKTHROUGH_TASK } from '../demo/productWalkthroughModels';
import {
  consumePendingMarketingSection,
  scrollToMarketingSection,
} from '../../lib/marketingScroll';
import { MarketingNav, TaaliLogo } from '../../shared/layout/TaaliLayout';

const containerClass = 'mx-auto max-w-[1360px] px-6 md:px-10 xl:px-16';

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
      <p className="max-w-[420px] text-[15px] leading-7 text-[var(--mute)]">{copy}</p>
      {children}
    </div>
  </div>
);

const PlatformCard = ({ kicker, title, body, children, className = '' }) => (
  <div className={`rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)] ${className}`.trim()}>
    <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple)]">{kicker}</div>
    <h3 className="mt-3 font-[var(--font-display)] text-[34px] leading-[1.02] tracking-[-0.03em]">{title}</h3>
    <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">{body}</p>
    {children}
  </div>
);

export const LandingPage = ({ onNavigate }) => {
  const showcaseAssessment = PRODUCT_WALKTHROUGH_TASK;
  const runtimeShowcase = PRODUCT_WALKTHROUGH.runtime;

  // Hero AgentHeader is interactive: lands OFF, auto-flips ON ~1.6s later
  // so visitors passively witness the cross-fade animation. Once a visitor
  // clicks the toggle themselves, the auto-play ref locks so we don't keep
  // overriding their state.
  const [agentOn, setAgentOn] = useState(false);
  const userToggledRef = useRef(false);
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
    const timer = window.setTimeout(() => {
      if (!userToggledRef.current) setAgentOn(true);
    }, 1600);
    return () => window.clearTimeout(timer);
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
          <p className="text-[18px] leading-[1.55] text-[var(--ink-2)]" style={{ maxWidth: 640, margin: '0 0 22px' }}>
            Taali is the first agentic hiring platform — and the only one that measures how candidates actually <em className="not-italic font-medium text-[var(--ink)]">use AI</em> on the job. The agent <em className="not-italic font-medium text-[var(--ink)]">decides</em> what to work on each cycle — fetch CVs, score, send assessments, queue advances or rejects — paces it within budget, and asks you when it can&apos;t decide on its own. Every consequential call still goes through you.
          </p>
          <div className="flex flex-wrap gap-3 text-[13px] text-[var(--ink-2)]" style={{ marginBottom: 30 }}>
            {[
              { k: 'AGENTIC', v: 'Runs your pipeline 24/7 — pauses for your judgment' },
              { k: 'AI-NATIVE', v: 'The only platform that scores AI fluency in hands-on tasks' },
            ].map((badge) => (
              <div
                key={badge.k}
                className="inline-flex items-center gap-2.5 rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-3.5 py-2"
              >
                <span className="font-[var(--font-mono)] text-[10.5px] font-semibold tracking-[0.08em] text-[var(--purple)]">
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
              className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5 font-[var(--font-mono)] text-[11px] text-[var(--mute)]"
            >
              <span className="h-[9px] w-[9px] rounded-full" style={{ background: '#f06' }} />
              <span className="h-[9px] w-[9px] rounded-full" style={{ background: '#ffb020' }} />
              <span className="h-[9px] w-[9px] rounded-full" style={{ background: '#39c66d' }} />
              <span className="ml-3">app.taali.ai/jobs</span>
            </div>
            {/* Live AgentHeader mock — same `.agent-running` / `.agent-quiet`
                classes and `.ah-bright-overlay` layer as the real product, so
                the OFF→ON cross-fade plays here exactly as it does in-app.
                Hero auto-flips ON shortly after mount; visitors can click
                the panel toggle to replay the transition. */}
            <div
              className={`agent-header ${agentOn ? 'agent-running' : 'agent-quiet'}`}
              style={{ minHeight: 220, padding: '24px 24px 26px' }}
            >
              <span className="ah-bright-overlay" aria-hidden="true" />
              <div className="agent-header-inner">
                <div className="agent-header-left">
                  <div className="ah-kicker">JOBS · 5 ACTIVE ROLES</div>
                  <div className="ah-title-row">
                    <h1 style={{ fontSize: 38 }}>5 active <em>roles</em></h1>
                  </div>
                  <p className="ah-subtitle">You&apos;re hiring. Star a role to keep its candidates flowing in automatically.</p>
                </div>
                <aside
                  className={`agent-panel agent-${agentOn ? 'on' : 'off'}`}
                  style={{ width: 260, minHeight: 200 }}
                >
                  <div className="agent-panel-head">
                    <div className="agent-pulse-wrap">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83" />
                      </svg>
                      {agentOn ? <span className="agent-pulse" aria-hidden="true" /> : null}
                    </div>
                    <div className="agent-status">
                      <div className="agent-status-line">
                        <span className="agent-mode">Agent mode</span>
                        <span className={`agent-state-pill state-${agentOn ? 'on' : 'off'}`}>
                          {agentOn ? 'ON' : 'OFF'}
                        </span>
                      </div>
                      {agentOn ? <div className="agent-pending">3 awaiting your review</div> : null}
                    </div>
                  </div>
                  {/* `key` re-mounts the body on each toggle so the
                      agentPanelEnter fade-up animation plays. */}
                  <div className="agent-panel-body" key={agentOn ? 'on' : 'off'}>
                    {agentOn ? (
                      <>
                        <div className="agent-tick">Advanced Maya Chen to Review · 2m ago</div>
                        <div className="agent-budget">
                          <div className="agent-budget-row">
                            <span>This month</span>
                            <span className="amt">$31 <span className="of">/ $50</span></span>
                          </div>
                          <div className="agent-budget-bar">
                            <i className="fill" style={{ width: '62%' }} />
                          </div>
                        </div>
                        <div className="agent-actions">
                          <button type="button" className="agent-btn" onClick={toggleAgent}>
                            <Pause size={11} strokeWidth={2} />
                            Pause
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="agent-off-copy">
                          Set a monthly cap. Taali scores every CV, advances the best, pauses for your call.
                        </div>
                        <div className="agent-off-budget">
                          <span className="agent-off-budget-prefix">$</span>
                          <input type="number" defaultValue={50} aria-label="Monthly budget" inputMode="numeric" readOnly />
                          <span className="agent-off-budget-suffix">/ month</span>
                        </div>
                        <div className="agent-actions">
                          <button type="button" className="agent-btn primary" onClick={toggleAgent}>
                            <Play size={11} strokeWidth={2} fill="currentColor" />
                            Turn on agent
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                </aside>
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
          <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.14em] text-[var(--purple)]">
            HOW THE AGENT WORKS
          </div>
          <h2 className="mt-3 max-w-[840px] font-[var(--font-display)] text-[clamp(32px,4vw,42px)] font-semibold leading-[1.1] tracking-[-0.025em] text-[var(--ink)]">
            An autonomous agent in your pipeline. <em className="not-italic text-[var(--purple)]">Built for the AI-native hire.</em>
          </h2>
          <p className="mt-5 max-w-[680px] text-[15.5px] leading-[1.6] text-[var(--ink-2)]">
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
                d: 'Hands-on, role-relevant tasks in a real IDE. We track every prompt, paste, and decision — then score AI fluency alongside craft. The only platform that tells you whether a candidate can actually ship with AI.',
              },
              {
                n: '03',
                t: 'Decide — with you in charge',
                d: 'A standing report per candidate: score, dimension radar, AI-usage trace, interview-ready questions. The agent recommends; you approve. Every consequential call is yours.',
              },
            ].map((step) => (
              <div key={step.n} className="border-t border-[var(--ink)] pt-7">
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">
                  {step.n} · TAALI
                </div>
                <h3 className="mt-2.5 font-[var(--font-display)] text-[26px] font-semibold tracking-[-0.015em] text-[var(--ink)]">
                  {step.t}
                </h3>
                <p className="mt-2.5 text-[14.5px] leading-[1.55] text-[var(--ink-2)]">{step.d}</p>
              </div>
            ))}
          </div>

          {/* Agent decision log mock — proves "the agent decides + every
              action is rule-traced + asks when stuck", same visual
              language as the Maya Chen AI-usage trace below. Static
              data: this is a marketing snippet, not a live read. */}
          <div className="mt-14 overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
            <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3 font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">
              <span>AGENT DECISION LOG · ROLE 109 · SENIOR SCRUM MASTER</span>
              <span className="font-semibold text-[var(--purple)]">TODAY</span>
            </div>
            <div className="flex flex-col gap-2.5 px-5 py-5 text-[12.5px]">
              {[
                {
                  time: '14:32',
                  action: 'QUEUE_ADVANCE',
                  color: '#16a34a',
                  message: 'Maya Chen — taali_score 82, assessment 78',
                  note: 'rule_path: send_assessment → role_fit≥65 → pre_screen_clear · revision #12',
                },
                {
                  time: '14:18',
                  action: 'BATCH_SEND',
                  color: 'var(--purple)',
                  message: '8 assessments dispatched within budget ($31 of $50 MTD)',
                  note: 'auto-paced — recruiter approval not required for this role',
                },
                {
                  time: '14:05',
                  action: 'ASK_RECRUITER',
                  color: '#d97706',
                  message: '"Set the must-have skills for this role" — open',
                  note: "agent paused — won't queue rejects until you answer",
                },
                {
                  time: '13:48',
                  action: 'QUEUE_REJECT',
                  color: '#16a34a',
                  message: 'Tariq Al-Ahmad — role_fit 22, no assessment pending',
                  note: 'rule_path: reject → role_fit≤30 + no_pending_assessment · revision #12',
                },
                {
                  time: '13:32',
                  action: 'BATCH_SCORE',
                  color: 'var(--purple)',
                  message: '47 candidates scored (cached 12, fresh 35)',
                  note: 'auto-execute — deterministic, no approval gate',
                },
                {
                  time: '13:15',
                  action: 'SURVEY',
                  color: 'var(--mute)',
                  message: '405 apps · 47 needs_score · 8 ready_for_assessment_decision',
                  note: 'cohort survey — what to do this cycle',
                },
              ].map((event, idx) => (
                <div
                  key={event.time}
                  className={`grid grid-cols-[48px_120px_1fr] items-start gap-2.5 py-2 ${idx ? 'border-t border-[var(--line-2)]' : ''}`}
                >
                  <div className="font-[var(--font-mono)] text-[10.5px] text-[var(--mute)]">{event.time}</div>
                  <div className="font-[var(--font-mono)] text-[10px] font-semibold tracking-[0.06em]" style={{ color: event.color }}>{event.action}</div>
                  <div>
                    <div className="leading-[1.45] text-[var(--ink)]">{event.message}</div>
                    <div className="mt-0.5 text-[11px] italic text-[var(--mute)]">{event.note}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* WE MEASURE HOW CANDIDATES USE AI — the differentiator (white bg per HANDOFF v2 §1) */}
      <section id="platform" className="border-t border-[var(--line)] bg-white">
        <div className={`${containerClass} py-20`}>
          <div className="grid gap-16 lg:grid-cols-[1fr_1.1fr] lg:items-center">
            <div>
              <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.14em] text-[var(--purple)]">
                AI-NATIVE ASSESSMENT
              </div>
              <h2 className="mt-3 font-[var(--font-display)] text-[clamp(34px,4.6vw,44px)] font-semibold leading-[1.05] tracking-[-0.03em] text-[var(--ink)]">
                You hire people <em className="not-italic text-[var(--purple)]">who use AI.</em><br />
                We&apos;re the only platform that measures it.
              </h2>
              <p className="mt-5 text-[16px] leading-[1.6] text-[var(--ink-2)]">
                Every assessment opens a real in-browser IDE — editor, terminal, your repo, and Claude Code / Cursor / Copilot in the candidate&apos;s hand — exactly as they&apos;d work on the job.
                Behind the scenes the runtime captures every prompt, paste, edit, file open, test run, and commit, time-stamped to the second.
                Those traces feed a 6-axis rubric (prompt quality, error recovery, context utilisation, independence, design thinking, debugging strategy) so AI fluency is scored as a first-class dimension alongside craft.
              </p>
              <ul className="mt-7 flex flex-col gap-3.5">
                {[
                  { t: 'AI fluency score', d: 'Did they prompt well? Catch a hallucination? Know when not to use it?' },
                  { t: 'Prompt-by-prompt replay', d: 'See exactly how they worked the agent — not just the final code.' },
                  { t: 'Full session telemetry', d: 'Edit timeline, test runs, terminal output, file opens — everything tied back to the final report.' },
                  { t: 'Autopilot detection', d: 'We flag candidates who pasted without reading. Calibrated, not punitive.' },
                ].map((bullet) => (
                  <li key={bullet.t} className="flex items-start gap-3">
                    <span className="mt-0.5 inline-flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-full bg-[var(--purple)] text-white">
                      <Check size={13} strokeWidth={2.6} aria-hidden="true" />
                    </span>
                    <div>
                      <div className="text-[14.5px] font-medium text-[var(--ink)]">{bullet.t}</div>
                      <div className="mt-0.5 text-[13px] leading-[1.5] text-[var(--ink-2)]">{bullet.d}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            {/* AI usage trace mock */}
            <div className="overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
              <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3 font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">
                <span>MAYA CHEN · AI USAGE TRACE</span>
                <span className="font-semibold text-[var(--purple)]">FLUENCY 8.7</span>
              </div>
              <div className="flex flex-col gap-2.5 px-5 py-5 text-[12.5px]">
                {[
                  { time: '12:04', action: 'PROMPT', color: 'var(--purple)', message: '"explain what idempotency keys do in this retry handler"', note: 'Read first, then asked' },
                  { time: '12:07', action: 'EDIT', color: '#16a34a', message: 'Wrote test for duplicate-key collision before fix', note: 'Test-first instinct' },
                  { time: '12:14', action: 'PROMPT', color: 'var(--purple)', message: '"this suggests a UNIQUE constraint — what about a race?"', note: 'Caught a flawed suggestion' },
                  { time: '12:22', action: 'PASTE', color: '#d97706', message: 'Pasted SELECT … FOR UPDATE pattern, modified for our schema', note: "Adapted, didn't copy" },
                  { time: '12:31', action: 'COMMIT', color: '#16a34a', message: 'Fix + 3 tests covering retry, race, and partial-failure', note: 'Shipped' },
                ].map((event, idx) => (
                  <div
                    key={event.time}
                    className={`grid grid-cols-[48px_70px_1fr] items-start gap-2.5 py-2 ${idx ? 'border-t border-[var(--line-2)]' : ''}`}
                  >
                    <div className="font-[var(--font-mono)] text-[10.5px] text-[var(--mute)]">{event.time}</div>
                    <div className="font-[var(--font-mono)] text-[10px] font-semibold tracking-[0.06em]" style={{ color: event.color }}>{event.action}</div>
                    <div>
                      <div className="leading-[1.45] text-[var(--ink)]">{event.message}</div>
                      <div className="mt-0.5 text-[11px] italic text-[var(--mute)]">{event.note}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* IDE PREVIEW — full-width band with the actual workspace
          component (AssessmentRuntimePreviewView). Static defaults
          render the revenue-recovery scenario with editor + terminal +
          Claude Code conversation pane. Strongest "this is the real
          thing" proof on the page. */}
      <section id="ide" className="border-t border-[var(--line)] bg-[var(--bg-2)]">
        <div className={`${containerClass} py-20`}>
          <div className="mb-8 max-w-[760px]">
            <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.14em] text-[var(--purple)]">
              THE WORKSPACE
            </div>
            <h2 className="mt-3 font-[var(--font-display)] text-[clamp(32px,4vw,42px)] font-semibold leading-[1.1] tracking-[-0.025em] text-[var(--ink)]">
              The actual IDE candidates work in. <em className="not-italic text-[var(--purple)]">Try it.</em>
            </h2>
            <p className="mt-4 text-[15.5px] leading-[1.6] text-[var(--ink-2)]">
              Real editor, real terminal, real repo, real AI in the side panel — Claude Code by default, Cursor or Copilot if your candidates prefer.
              Every keystroke, prompt, paste, test run and commit is captured time-stamped so the rubric below is grounded in evidence, not vibes.
            </p>
          </div>
          <div className="overflow-hidden rounded-[14px] border border-[var(--line)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
            <AssessmentRuntimePreviewView
              heightClass="h-[44rem]"
              lightMode={false}
              taskName="Revenue Recovery Incident"
              taskRole="Senior Backend Engineer"
              taskContext="Restore the batch revenue-recovery flow before finance close."
            />
          </div>
        </div>
      </section>

      {/* Trust strip removed — early product, no public customer logos. */}

      {/* WALKTHROUGH TEASER — v4 (HANDOFF chat.md §1) */}
      <section
        id="walkthrough"
        className="border-t border-b border-[var(--line)] bg-[var(--bg-2)]"
      >
        <div className={`${containerClass} py-16`}>
          <div className="grid items-center gap-8 lg:grid-cols-[1fr_auto]">
            <div>
              <div className="mc-kicker" style={{ marginBottom: 12 }}>SEE EVERYTHING ELSE</div>
              <h2
                className="font-[var(--font-display)] font-semibold text-[var(--ink)]"
                style={{ fontSize: 'clamp(28px,3.4vw,36px)', letterSpacing: '-0.025em', lineHeight: 1.1, margin: '0 0 12px' }}
              >
                Want to see how it all fits together?
              </h2>
              <p className="text-[15.5px] leading-[1.6] text-[var(--ink-2)]" style={{ margin: 0, maxWidth: 680 }}>
                Triage, scoring, plain-English search, the standing report, ATS sync — there&apos;s a full product
                walkthrough pre-loaded with a real role. No card, no installs, no sales call.
              </p>
            </div>
            <button
              type="button"
              className="btn btn-primary"
              style={{ height: 48, padding: '0 26px', fontSize: 14, whiteSpace: 'nowrap' }}
              onClick={() => onNavigate('showcase')}
            >
              Open the walkthrough →
            </button>
          </div>
        </div>
      </section>

      {/* BOTTOM CTA — softened, token-based purple gradient (v4) */}
      <section className="bg-[var(--bg)]">
        <div className={`${containerClass} pb-24 pt-10`}>
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
                <p className="mt-3 max-w-[520px] text-[16px] leading-[1.55] opacity-85">
                  20-minute demo with a founder. We&apos;ll run it on a real role of yours.
                </p>
              </div>
              <button
                type="button"
                className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[14px] font-semibold text-[var(--purple)]"
                style={{ boxShadow: '0 10px 28px -8px rgba(0,0,0,0.3)' }}
                onClick={() => onNavigate('demo-lead')}
              >
                Book a demo →
              </button>
            </div>
          </div>
        </div>
      </section>

      <section id="pricing" className="border-t border-[var(--line)] bg-[var(--bg)]">
        <div className={`${containerClass} py-20`}>
          <div className="mb-10 max-w-[640px]">
            <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--mute)]">
              Pricing
            </div>
            <h2 className="mt-2 font-[var(--font-display)] text-[42px] leading-[1.05] tracking-[-0.02em] text-[var(--ink)]">
              Pay only for what you use.
            </h2>
            <p className="mt-4 text-[16px] leading-7 text-[var(--ink-2)]">
              Usage-based pricing — like Anthropic, OpenAI, Cursor. New users get $1.50 of free credits to try the full
              platform. After that, top up whenever you run out. No subscriptions, no monthly minimums.
            </p>
          </div>

          <div className="grid gap-5 lg:grid-cols-[1fr_1fr_1fr_1fr]">
            {/* Free tier card */}
            <div className="rounded-lg border border-[var(--line)] bg-[var(--bg-2)] p-6">
              <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">
                Free trial
              </div>
              <div className="mt-2 font-[var(--font-display)] text-[36px] leading-none tracking-[-0.02em] text-[var(--ink)]">
                $1.50
              </div>
              <div className="mt-1 text-[13px] text-[var(--mute)]">credits on signup</div>
              <ul className="mt-5 flex flex-col gap-2 text-[14px] text-[var(--ink-2)]">
                <li>1 job spec</li>
                <li>~100 candidates pre-screened</li>
                <li>~30 candidates fully scored</li>
                <li>3 assessment workspace runs</li>
                <li>No card required</li>
              </ul>
            </div>

            {/* Pack cards */}
            {[
              { label: 'Starter', price: '$20', credits: '$20', bonus: null, blurb: '~1,300 scored candidates' },
              { label: 'Growth', price: '$100', credits: '$110', bonus: '+10% bonus', blurb: '~7,300 scored candidates' },
              { label: 'Scale', price: '$500', credits: '$600', bonus: '+20% bonus', blurb: '~40,000 scored candidates' },
            ].map((pack) => (
              <div key={pack.label} className="rounded-lg border border-[var(--line)] bg-[var(--bg-2)] p-6">
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">
                  {pack.label}
                </div>
                <div className="mt-2 font-[var(--font-display)] text-[36px] leading-none tracking-[-0.02em] text-[var(--ink)]">
                  {pack.price}
                </div>
                <div className="mt-1 text-[13px] text-[var(--mute)]">
                  {pack.credits} of credits{pack.bonus ? ` • ${pack.bonus}` : ''}
                </div>
                <div className="mt-4 text-[14px] text-[var(--ink-2)]">{pack.blurb}</div>
                <div className="mt-5 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">
                  One-time payment • USD
                </div>
              </div>
            ))}
          </div>

          {/* What you get */}
          <div className="mt-12 rounded-lg border border-[var(--line)] bg-[var(--bg-2)] p-8">
            <h3 className="font-[var(--font-display)] text-[24px] tracking-[-0.02em] text-[var(--ink)]">
              What you get on every plan
            </h3>
            <div className="mt-6 grid gap-6 md:grid-cols-2 lg:grid-cols-4">
              {[
                {
                  title: 'Candidate ingestion',
                  body: 'Pull from Workable or upload CVs directly. Parsed, deduped, and queued automatically.',
                },
                {
                  title: 'AI pre-screening',
                  body: 'Cheap Claude pass that filters obvious mismatches before you spend on full scoring. Priced at cost.',
                },
                {
                  title: 'CV scoring',
                  body: 'Evidence-grounded scores with per-requirement breakdown. Cached so re-runs are free.',
                },
                {
                  title: 'Assessment workspace',
                  body: 'Live Claude-Code coding sandbox per candidate, with prompt-quality scoring and fraud signals.',
                },
              ].map((feature) => (
                <div key={feature.title}>
                  <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--purple)]">
                    {feature.title}
                  </div>
                  <p className="mt-2 text-[14px] leading-6 text-[var(--ink-2)]">{feature.body}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Pricing math note */}
          <div className="mt-8 max-w-[760px] font-[var(--font-mono)] text-[11px] leading-6 text-[var(--mute)]">
            Pricing math: pre-screening is billed at Anthropic's raw token cost (no markup). CV scoring and
            assessment workspace runs are billed at 3× token cost — covers infra, support, and ongoing R&amp;D.
            Every Claude call is itemized in your settings &gt; billing tab.
          </div>
        </div>
      </section>

      <footer className="border-t border-[var(--line)] bg-[var(--ink)] text-[var(--bg)]">
        <div className={`${containerClass} py-14`}>
          <div className="grid gap-10 lg:grid-cols-[1.1fr_.9fr_.9fr_.9fr]">
            <div>
              <TaaliLogo onClick={() => onNavigate('landing')} wordmarkClassName="!text-[var(--bg)]" />
              <p className="mt-5 max-w-[280px] text-[15px] leading-7 text-[var(--taali-inverse-text)] opacity-70">
                AI-native technical assessments that <span className="font-[var(--font-display)] text-[var(--purple)]">tally</span> real skill.
              </p>
            </div>

            {footerColumns.map((column) => (
              <div key={column.title}>
                <h4 className="font-[var(--font-display)] text-[20px] tracking-[-0.02em]">{column.title}</h4>
                <div className="mt-4 flex flex-col gap-3">
                  {column.items.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      className="w-fit text-left text-[14px] text-[var(--taali-inverse-text)] opacity-70 transition hover:opacity-100"
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
            className="mt-6 flex flex-col gap-3 border-t pt-5 text-[13px] text-[var(--taali-inverse-text)] md:flex-row md:items-center md:justify-between"
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
