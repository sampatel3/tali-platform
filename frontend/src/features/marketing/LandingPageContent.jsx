import React, { useEffect } from 'react';

import { AssessmentRuntimePreviewView } from '../assessment_runtime/AssessmentRuntimePreviewView';
import { PRODUCT_WALKTHROUGH, PRODUCT_WALKTHROUGH_TASK } from '../demo/productWalkthroughModels';
import {
  consumePendingMarketingSection,
  scrollToMarketingSection,
} from '../../lib/marketingScroll';
import { MarketingNav, TaaliLogo } from '../../shared/layout/TaaliLayout';

const containerClass = 'mx-auto max-w-[1360px] px-6 md:px-10 xl:px-16';

const heroSignals = [
  { label: 'Prompt quality', width: '91%', value: '9.1' },
  { label: 'Error recovery', width: '78%', value: '7.8' },
  { label: 'Context utilization', width: '86%', value: '8.6' },
  { label: 'Independence', width: '72%', value: '7.2' },
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
      { label: 'Book a demo', page: 'demo' },
      { label: 'AI collab score', section: 'platform' },
      { label: 'Question bank', section: 'platform' },
      { label: 'Integrations', section: 'platform' },
      { label: 'Product walkthrough', page: 'demo' },
    ],
  },
  {
    title: 'Company',
    items: [
      { label: 'Manifesto', section: 'problem' },
      { label: 'Careers', page: 'demo' },
      { label: 'Blog', page: 'demo' },
      { label: 'Contact', href: 'mailto:hello@taali.ai' },
    ],
  },
  {
    title: 'Resources',
    items: [
      { label: 'Sample walkthrough', page: 'demo' },
      { label: 'Rubric library', section: 'platform' },
      { label: 'Docs', page: 'demo' },
      { label: 'Security', page: 'demo' },
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

const HeroSignalRow = ({ label, width, value }) => (
  <div className="grid grid-cols-[140px_1fr_38px] items-center gap-3 text-[13.5px]">
    <span className="text-[var(--ink-2)]">{label}</span>
    <div className="h-1.5 overflow-hidden rounded-full bg-[var(--bg-3)]">
      <div className="h-full rounded-full bg-gradient-to-r from-[var(--purple-2)] to-[var(--purple)]" style={{ width }} />
    </div>
    <span className="text-right font-[var(--font-mono)] text-[var(--ink)]">{value}</span>
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

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const sectionId = consumePendingMarketingSection() || window.location.hash.replace(/^#/, '');
    if (!sectionId) return undefined;

    const timer = window.setTimeout(() => {
      scrollToMarketingSection(sectionId, { behavior: 'smooth' });
    }, 40);

    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <MarketingNav onNavigate={onNavigate} />

      <section className="relative overflow-hidden pb-20 pt-16 md:pb-28 md:pt-20">
        <div
          className="pointer-events-none absolute inset-0 opacity-60 tally-bg-soft"
          style={{ maskImage: 'radial-gradient(60% 60% at 85% 20%, black, transparent 70%)' }}
        />
        <div className={`${containerClass} grid gap-12 lg:grid-cols-[1.05fr_.95fr] lg:items-center`}>
          <div>
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
            <h1 className="h-display mt-2 text-[clamp(56px,7.3vw,108px)] leading-[0.96]">
              The recruiter's <em>agent.</em><br />Built to hire engineers<br />who ship with AI<span className="text-[var(--purple)]">.</span>
            </h1>
            <p className="mt-5 max-w-[640px] text-[18px] leading-[1.55] text-[var(--ink-2)]">
              Taali is the first agentic hiring platform — and the only one that measures how candidates actually <em className="text-[var(--ink)] font-medium">use AI</em> on the job. The agent triages your pipeline, runs hands-on assessments in a real IDE, and surfaces calibrated evidence. You stay in charge of every consequential decision.
            </p>
            <div className="mt-7 flex flex-wrap gap-3 text-[13px] text-[var(--ink-2)]">
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
            <div className="mt-7 flex flex-wrap gap-3">
              <button type="button" className="btn btn-primary btn-lg" onClick={() => onNavigate('demo')}>
                Book a demo
              </button>
              <button
                type="button"
                className="btn btn-outline btn-lg"
                onClick={() => {
                  window.location.href = '/c/demo?view=client&k=demo-token&showcase=1';
                }}
              >
                Try the live walkthrough <span className="arrow">→</span>
              </button>
            </div>
            <div className="mt-14 flex max-w-[520px] items-start gap-3 rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-4 text-[13.5px] leading-[1.55] text-[var(--mute)] shadow-[var(--shadow-sm)]">
              <div className="grid h-7 w-7 shrink-0 place-items-center rounded-[8px] bg-[color-mix(in_oklab,var(--purple)_14%,var(--bg))] text-[var(--purple)]">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 2 L4 6 v6 c0 5 3.5 8 8 10 4.5-2 8-5 8-10 V6 z" />
                  <path d="M9 12 l2 2 4-4" />
                </svg>
              </div>
              <div>
                Built for engineering teams running <b className="text-[var(--ink)]">50+ technical interviews a year</b>. Real tasks, real AI tools, evidence-linked scoring.
              </div>
            </div>
          </div>

          <div className="rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] p-5 shadow-[var(--shadow-lg)]">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-dashed border-[var(--line)] px-2 pb-4">
              <div className="flex flex-wrap gap-1 font-[var(--font-mono)] text-xs text-[var(--mute)]">
                <span className="rounded-[8px] bg-[var(--bg-3)] px-3 py-1.5 text-[var(--ink)]">Overview</span>
                <span className="px-3 py-1.5">CV &amp; match</span>
              </div>
              <div className="flex shrink-0 items-center gap-2 font-[var(--font-mono)] text-xs text-[var(--ink-2)]">
                <span className="h-2 w-2 rounded-full bg-[var(--green)] shadow-[0_0_0_3px_color-mix(in_oklab,var(--green)_25%,transparent)]" />
                <span>Maya Chen</span>
              </div>
            </div>

            <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-4 rounded-[var(--radius)] bg-[var(--bg)] p-5 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
              <div className="relative grid h-[72px] w-[72px] place-items-center">
                <svg width="72" height="72" viewBox="0 0 72 72" className="absolute inset-0 -rotate-90">
                  <circle cx="36" cy="36" r="30" fill="none" stroke="var(--bg-3)" strokeWidth="6" />
                  <circle cx="36" cy="36" r="30" fill="none" stroke="var(--purple)" strokeWidth="6" strokeLinecap="round" strokeDasharray="188.4" strokeDashoffset="34" />
                </svg>
                <span className="font-[var(--font-display)] text-2xl leading-none text-[var(--ink)]">8.2</span>
              </div>
              <div className="min-w-0">
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Composite · vs your team&apos;s bar</div>
                <div className="mt-1 text-[17px] font-semibold">Strong hire - recommend on-site</div>
                <div className="mt-1 text-[13px] text-[var(--mute)]">Top 12% of 47 candidates for this role · scored against your rubric</div>
              </div>
              <div className="col-span-2 w-fit rounded-full bg-[color-mix(in_oklab,var(--green)_18%,transparent)] px-3 py-1.5 font-[var(--font-mono)] text-xs text-[var(--green)] sm:col-span-1">
                Strong hire
              </div>
            </div>

            <div className="mt-4 grid gap-3">
              {heroSignals.map((signal) => (
                <HeroSignalRow key={signal.label} label={signal.label} width={signal.width} value={signal.value} />
              ))}
            </div>

            <div className="mt-4 flex gap-3 rounded-[var(--radius)] border border-dashed border-[color-mix(in_oklab,var(--purple)_28%,var(--line))] bg-[color-mix(in_oklab,var(--purple)_8%,var(--bg-2))] p-4 text-[13.5px] leading-6 text-[var(--ink-2)]">
              <div className="grid h-7 w-7 shrink-0 place-items-center rounded-[8px] bg-[var(--purple)] text-[var(--taali-inverse-text)]">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
                  <path d="M12 2 L14 10 L22 12 L14 14 L12 22 L10 14 L2 12 L10 10 Z" />
                </svg>
              </div>
              <div>
                <b className="block text-[var(--ink)]">Evidence · prompt 14 of 23</b>
                <span className="my-1 block leading-[1.5] text-[var(--ink)] italic">
                  &quot;Before I implement, can you show me where the existing eval-gate is wired so I don&apos;t duplicate the abstraction?&quot;
                </span>
                <span className="block font-[var(--font-mono)] text-[11px] tracking-[0.02em] text-[var(--purple)]">
                  → scored 9.4 on prompt quality · context-aware, ownership signal
                </span>
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
                d: 'The agent scores every CV against your bar, paces invitations within budget, and surfaces only the candidates worth your attention. You set the criteria once; it works the pipeline 24/7.',
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
        </div>
      </section>

      {/* WE MEASURE HOW CANDIDATES USE AI — the differentiator (white bg) */}
      <section id="platform" className="border-t border-[var(--line)] bg-[var(--bg)]">
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
                Every assessment runs in a real IDE with Claude or Copilot in the candidate&apos;s hand — exactly as they&apos;d work on the job.
                We capture every prompt, paste, and decision, then score AI fluency as a first-class dimension alongside craft.
              </p>
              <ul className="mt-7 flex flex-col gap-3.5">
                {[
                  { t: 'AI fluency score', d: 'Did they prompt well? Catch a hallucination? Know when not to use it?' },
                  { t: 'Prompt-by-prompt replay', d: 'See exactly how they worked the agent — not just the final code.' },
                  { t: 'Autopilot detection', d: 'We flag candidates who pasted without reading. Calibrated, not punitive.' },
                ].map((bullet) => (
                  <li key={bullet.t} className="flex items-start gap-3">
                    <span className="mt-0.5 inline-flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-full bg-[var(--purple)] text-[12px] font-semibold text-white">
                      ✓
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

      {/* TRUST STRIP */}
      <section id="proof" className="border-t border-[var(--line)] bg-[var(--bg-2)]">
        <div className={`${containerClass} flex flex-wrap items-center justify-between gap-12 py-14 text-[var(--mute)]`}>
          <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.14em]">
            TRUSTED BY HIRING TEAMS AT
          </div>
          <div className="flex flex-wrap gap-12 font-[var(--font-display)] text-[18px] font-medium tracking-[-0.01em] opacity-55">
            <span>Linear</span>
            <span>Stripe</span>
            <span>Ramp</span>
            <span>Vercel</span>
            <span>Notion</span>
          </div>
        </div>
      </section>

      {/* RUNTIME (kept short — anchors the nav 'Runtime' link) */}
      <section id="runtime" className="border-t border-[var(--line)] bg-[var(--bg)]">
        <div className={`${containerClass} py-20`}>
          <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.14em] text-[var(--purple)]">
            INSIDE THE RUNTIME
          </div>
          <h2 className="mt-3 max-w-[760px] font-[var(--font-display)] text-[clamp(30px,3.6vw,38px)] font-semibold leading-[1.1] tracking-[-0.02em] text-[var(--ink)]">
            What your candidate <em className="not-italic text-[var(--purple)]">actually sees.</em>
          </h2>
          <p className="mt-4 max-w-[680px] text-[15.5px] leading-[1.6] text-[var(--ink-2)]">
            A browser-based IDE with the same repo, runtime, and AI tooling your hiring team uses to evaluate.
            No proctoring overlay. No screen capture. The transcript is the record.
          </p>
        </div>
      </section>

      {/* BOTTOM CTA — purple gradient */}
      <section className="bg-[var(--bg)]">
        <div className={`${containerClass} pb-24 pt-10`}>
          <div
            className="relative overflow-hidden rounded-[18px] px-12 py-14"
            style={{
              background: 'linear-gradient(135deg, #5b2ca8 0%, #7c3aed 60%, #a78bfa 100%)',
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
                  See it work on a real role.
                </h2>
                <p className="mt-3 max-w-[520px] text-[16px] leading-[1.55] opacity-85">
                  No sales call. Tell us who you&apos;d hire next and we&apos;ll spin up a sandbox with your shortlist.
                </p>
              </div>
              <button
                type="button"
                className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[14px] font-semibold text-[var(--purple)]"
                style={{ boxShadow: '0 10px 28px -8px rgba(0,0,0,0.3)' }}
                onClick={() => onNavigate('demo-lead')}
              >
                Try the live walkthrough →
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
