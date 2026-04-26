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
  { label: 'Prompt quality', width: '62%' },
  { label: 'Error recovery', width: '74%' },
  { label: 'Context utilization', width: '55%' },
  { label: 'Independence', width: '68%' },
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

const HeroSignalRow = ({ label, width }) => (
  <div className="grid grid-cols-[140px_1fr_32px] items-center gap-3 text-[13.5px]">
    <span className="text-[var(--ink-2)]">{label}</span>
    <div className="h-2 rounded-full bg-[var(--bg-3)]">
      <div className="h-full rounded-full bg-[var(--bg-3)]" style={{ width }} />
    </div>
    <span className="text-right text-[var(--mute)]">—</span>
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
            <span className="eyebrow">
              <span className="eyebrow-tag">NEW</span>
              AI-tool proficiency scoring - now live
              <span className="text-[var(--mute-2)]">→</span>
            </span>
            <h1 className="h-display mt-6 text-[clamp(56px,7.3vw,108px)] leading-[0.94]">
              Hire engineers who can actually <em>ship</em> with AI<span className="text-[var(--purple)]">.</span>
            </h1>
            <p className="mt-5 max-w-[560px] text-[19px] leading-[1.55] text-[var(--mute)]">
              Taali is the only technical assessment platform that measures how candidates <em>use</em> AI tools to solve real engineering problems - not just whether they can code without them.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <button type="button" className="btn btn-primary btn-lg" onClick={() => onNavigate('demo')}>
                Book a demo <span className="arrow">→</span>
              </button>
              <button type="button" className="btn btn-outline btn-lg" onClick={() => onNavigate('demo')}>
                See the product
              </button>
            </div>
            <div className="mt-10 flex items-center gap-5 text-[13px] text-[var(--mute)]">
              <div className="flex">
                {['#E9DDFE', '#FFD1B8', '#C8F169', '#CDE0FF'].map((color, index) => (
                  <div
                    key={color}
                    className="h-7 w-7 rounded-full border border-[var(--bg)]"
                    style={{ marginLeft: index === 0 ? 0 : -8, background: color }}
                  />
                ))}
              </div>
              <div>
                Built for hiring teams evaluating engineers who already work with AI every day.
              </div>
            </div>
          </div>

          <div className="rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] p-5 shadow-[var(--shadow-lg)]">
            <div className="mb-4 flex items-center justify-between border-b border-dashed border-[var(--line)] px-2 pb-4">
              <div className="flex gap-1 font-[var(--font-mono)] text-xs text-[var(--mute)]">
                <span className="rounded-[8px] bg-[var(--bg-3)] px-3 py-1.5 text-[var(--ink)]">Overview</span>
                <span className="px-3 py-1.5">Signals</span>
                <span className="px-3 py-1.5">Recording</span>
                <span className="px-3 py-1.5">Notes</span>
              </div>
              <div className="font-[var(--font-mono)] text-[10px] uppercase tracking-[0.12em] text-[var(--mute)]">
                Illustrative · what a report looks like
              </div>
            </div>

            <div className="grid grid-cols-[auto_1fr_auto] items-center gap-4 rounded-[var(--radius)] bg-[var(--bg)] p-5">
              <div className="relative grid h-[72px] w-[72px] place-items-center">
                <svg width="72" height="72" viewBox="0 0 72 72" className="absolute inset-0 -rotate-90">
                  <circle cx="36" cy="36" r="30" fill="none" stroke="var(--bg-3)" strokeWidth="6" />
                  <circle cx="36" cy="36" r="30" fill="none" stroke="var(--purple)" strokeWidth="6" strokeLinecap="round" strokeDasharray="188.4" strokeDashoffset="56" strokeOpacity="0.55" />
                </svg>
                <span className="font-[var(--font-display)] text-[18px] leading-none text-[var(--mute)]">—</span>
              </div>
              <div>
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Composite score · per role</div>
                <div className="mt-1 text-[17px] font-semibold">A six-axis read on every session</div>
                <div className="mt-1 text-[13px] text-[var(--mute)]">Calibrated against your team&apos;s bar, not ours.</div>
              </div>
              <div className="rounded-full border border-dashed border-[var(--line)] px-3 py-1.5 font-[var(--font-mono)] text-xs text-[var(--mute)]">
                Verdict
              </div>
            </div>

            <div className="mt-4 grid gap-3">
              {heroSignals.map((signal) => (
                <HeroSignalRow key={signal.label} label={signal.label} width={signal.width} />
              ))}
            </div>

            <div className="mt-4 flex gap-3 rounded-[var(--radius)] border border-dashed border-[color-mix(in_oklab,var(--purple)_28%,var(--line))] bg-[color-mix(in_oklab,var(--purple)_8%,var(--bg-2))] p-4 text-[13.5px] leading-6 text-[var(--ink-2)]">
              <div className="grid h-7 w-7 shrink-0 place-items-center rounded-[8px] bg-[var(--purple)] text-[var(--taali-inverse-text)]">AI</div>
              <div>
                <b className="block text-[var(--ink)]">What the AI-collab read tells you</b>
                Where the candidate delegated, where they owned the work, and how they handled an incorrect suggestion. Evidence-linked, prompt by prompt.
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="problem" className="pb-20 md:pb-28">
        <div className={containerClass}>
          <SectionHeading
            kicker="01 · THE PROBLEM"
            title={<>Your test is measuring the <em>wrong</em> thing.</>}
            copy="A candidate who memorized Dijkstra but cannot read Claude's output critically will shipwreck your codebase by week three. Taali swaps algorithm trivia for the skills your team actually uses - in the environment they actually use them."
          />
          <div className="grid gap-5 lg:grid-cols-2">
            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-7 shadow-[var(--shadow-sm)]">
              <span className="inline-flex rounded-full bg-[var(--bg-3)] px-3 py-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">Legacy platforms</span>
              <h3 className="mt-4 font-[var(--font-display)] text-[42px] leading-[0.96] tracking-[-0.03em] text-[color:color-mix(in_oklab,var(--ink)_55%,var(--bg))]">
                <span className="relative inline-block after:absolute after:left-[-2px] after:right-[-2px] after:top-1/2 after:h-[2px] after:-translate-y-1/2 after:bg-[color:color-mix(in_oklab,var(--ink)_42%,transparent)] after:content-['']">
                  Invert a binary tree
                </span>
                <br />
                <span className="relative inline-block after:absolute after:left-[-2px] after:right-[-2px] after:top-1/2 after:h-[2px] after:-translate-y-1/2 after:bg-[color:color-mix(in_oklab,var(--ink)_42%,transparent)] after:content-['']">
                  on a whiteboard.
                </span>
              </h3>
              <p className="mt-4 text-[14px] leading-7 text-[var(--mute)]">
                Tests pattern recall. Optimizes for candidates who grind LeetCode. Correlates weakly with on-the-job performance, and correlates <i>negatively</i> with AI-era collaboration skills.
              </p>
              <div className="mt-6 rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4 font-[var(--font-mono)] text-[12px] leading-6 text-[var(--ink-2)]">
                <div className="mb-2 text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">01 · algorithmic</div>
                <div className="text-[color:var(--mute)]">// given root of binary tree</div>
                <div><span className="text-[var(--purple)]">function</span> invert(root) {'{'}</div>
                <div>&nbsp;&nbsp;<span className="text-[var(--purple)]">if</span> (!root) <span className="text-[var(--purple)]">return</span> root;</div>
                <div>&nbsp;&nbsp;[root.left, root.right] = ...</div>
                <div>{'}'}</div>
              </div>
            </div>

            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-7 shadow-[var(--shadow-sm)]">
              <span className="inline-flex rounded-full bg-[var(--purple)] px-3 py-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--taali-inverse-text)]">Taali</span>
              <h3 className="mt-4 font-[var(--font-display)] text-[42px] leading-[0.96] tracking-[-0.03em]">Review a GenAI release with Claude Code.</h3>
              <p className="mt-4 text-[14px] leading-7 text-[var(--mute)]">
                Tests how candidates use AI on a real engineering problem. We look at prompt quality, judgment, recovery, and final decisions.
              </p>
              <div className="mt-6 rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4 font-[var(--font-mono)] text-[12px] leading-6 text-[var(--ink-2)]">
                <div className="mb-2 text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">01 · real task · genai production readiness</div>
                <div className="text-[var(--mute)]"># BUG: moderation outages should not default to allow</div>
                <div><span className="text-[var(--purple)]">if</span> moderation_result <span className="text-[var(--purple)]">is</span> None:</div>
                <div>&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> True</div>
                <div><span className="text-[var(--purple)]">if</span> user_intent <span className="text-[var(--purple)]">in</span> SAFETY_POLICY[&quot;always_escalate&quot;]:</div>
                <div>&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> False</div>
                <div className="mt-4 flex gap-3 rounded-[12px] border border-[var(--line)] bg-[var(--purple-soft)] p-3 text-[11.5px] leading-5 text-[var(--ink-2)]">
                  <div className="grid h-6 w-6 place-items-center rounded-[7px] bg-[var(--purple)] text-[10px] text-[var(--taali-inverse-text)]">AI</div>
                  <div>
                    <b>What we score here:</b> Whether the candidate spots the unsafe default, uses AI carefully, and checks the answer before shipping it.
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="platform" className="pb-20 md:pb-28">
        <div className={containerClass}>
          <SectionHeading
            kicker="02 · THE PLATFORM"
            title={<>Everything you need to <em>see</em> real engineering.</>}
            copy="Candidate workspace, AI activity, recruiter reporting, and hiring workflow integrations in one place."
          />

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
            <PlatformCard
              kicker="// 01 · LIVE CODING"
              title={<>Real IDE.<br />Real stack.<br />Real AI tools.</>}
              body="Candidates work in a browser workspace with the repo, editor, terminal, and AI tools in one place. It feels like real work because it is real work."
              className="md:col-span-2 xl:col-span-3"
            >
              <div className="mt-5 rounded-[16px] border border-[var(--line)] bg-[var(--bg)] p-4">
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-[var(--red)]" />
                    <span className="h-2 w-2 rounded-full bg-[var(--amber)]" />
                    <span className="h-2 w-2 rounded-full bg-[var(--green)]" />
                  </div>
                  <span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">app/release_guardrails.py · repo file</span>
                </div>
                <div className="space-y-1 font-[var(--font-mono)] text-[12px] leading-6 text-[var(--ink-2)]">
                  <div><span className="text-[var(--mute)]">1</span> <span className="text-[var(--purple)]">from</span> app.policy <span className="text-[var(--purple)]">import</span> SAFETY_POLICY</div>
                  <div><span className="text-[var(--mute)]">3</span> <span className="text-[var(--purple)]">def</span> should_allow_response(*, moderation_result, confidence):</div>
                  <div><span className="text-[var(--mute)]">4</span> &nbsp;&nbsp;<span className="text-[var(--purple)]">if</span> moderation_result <span className="text-[var(--purple)]">is</span> None:</div>
                  <div><span className="text-[var(--mute)]">5</span> &nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--mute)]"># BUG: outage shouldn&apos;t default to allow</span></div>
                  <div><span className="text-[var(--mute)]">6</span> &nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> True</div>
                  <div><span className="text-[var(--mute)]">7</span> &nbsp;&nbsp;<span className="text-[var(--purple)]">if</span> moderation_result.get(<span className="text-[var(--purple)]">&quot;blocked&quot;</span>):</div>
                  <div><span className="text-[var(--mute)]">8</span> &nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> False</div>
                  <div><span className="text-[var(--mute)]">9</span> &nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> confidence &gt;= 0.42</div>
                </div>
              </div>
              <div className="mt-4 rounded-[14px] border border-[color-mix(in_oklab,var(--purple)_20%,var(--line))] bg-[color-mix(in_oklab,var(--purple)_8%,var(--bg))] p-4">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--purple)]">What the AI-collab read covers</div>
                <div className="mt-2 text-[13px] font-semibold text-[var(--ink)]">Per-prompt evidence</div>
                <div className="mt-2 text-[13px] leading-6 text-[var(--ink-2)]">
                  Every prompt is linked back to the timeline so recruiters can see how the candidate used AI, what they accepted, and what they checked themselves.
                </div>
              </div>
            </PlatformCard>

            <PlatformCard
              kicker="// 02 · AI COLLAB SCORING"
              title={<>We score AI judgment,<br />not AI <em>avoidance</em>.</>}
              body="The signal is how candidates use AI: prompt quality, context use, verification, recovery, and final judgment."
              className="xl:col-span-3"
            >
              <div className="mt-5 space-y-2 rounded-[16px] border border-[var(--line)] bg-[var(--bg)] p-4">
                {sixAxes.map((axis) => (
                  <div key={axis}>
                    <div className="flex items-center justify-between text-[12px]">
                      <span>{axis}</span>
                      <b className="text-[var(--mute)]">—</b>
                    </div>
                    <div className="mt-2 h-2 rounded-full bg-[var(--bg-3)]">
                      <div className="h-full w-[62%] rounded-full bg-[var(--bg-3)]" />
                    </div>
                  </div>
                ))}
                <div className="pt-2 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
                  The six axes · every report
                </div>
              </div>
            </PlatformCard>

            <PlatformCard
              kicker="// 03 · QUESTION BANK"
              title={<>A focused set of<br />AI-first <em>tasks</em>.</>}
              body="From release-readiness reviews to production debugging, each assessment runs inside a working repo instead of a whiteboard puzzle."
              className="md:col-span-2 xl:col-span-3"
            >
              <div className="mt-5 space-y-2 rounded-[16px] border border-[var(--line)] bg-[var(--bg)] p-4">
                {questionBankRows.map(([id, title, difficulty, tone]) => (
                  <div key={id} className="flex items-center justify-between gap-4 rounded-[12px] bg-[var(--bg-2)] px-4 py-3">
                    <span className="font-[var(--font-mono)] text-[11px] text-[var(--mute)]">{id}</span>
                    <span className="grow text-[13px] text-[var(--ink-2)]">{title}</span>
                    <span className={`chip ${tone}`}>{difficulty}</span>
                  </div>
                ))}
              </div>
            </PlatformCard>

            <PlatformCard
              kicker="// 04 · INTEGRITY"
              title="Proctoring that won't insult anyone."
              body="Signal, not surveillance. We flag contract farms and paste-ins - and trust everyone else."
              className="xl:col-span-3"
            >
              <div className="mt-5 flex flex-wrap gap-2">
                {['Stealth paste detection', 'Window focus trails', 'Voice verification'].map((item) => (
                  <span key={item} className="chip">{item}</span>
                ))}
              </div>
            </PlatformCard>

            <PlatformCard
              kicker="// 05 · INTEGRATIONS"
              title={<>Built into <em>Workable</em> workflows.<br />Connected to interview transcription.</>}
              body="Taali fits into the hiring workflow your team already runs, bringing ATS context and interview transcripts into the same recruiter review."
              className="md:col-span-2 xl:col-span-4"
            >
              <div className="mt-5 grid gap-4 lg:grid-cols-2">
                <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-5">
                  <div className="flex items-center gap-3">
                    <div className="grid h-[30px] w-[30px] place-items-center rounded-[8px] bg-[var(--green)] font-[var(--font-mono)] text-[13px] font-bold text-[var(--taali-inverse-text)]">WK</div>
                    <div className="text-[15px] font-semibold">Workable · recruiting workflow</div>
                  </div>
                  <ul className="mt-4 list-disc space-y-1 pl-5 text-[13px] leading-[1.65] text-[var(--ink-2)]">
                    <li>Keep assessments inside the same hiring flow recruiters already use</li>
                    <li>Carry stage context and recruiter notes into the candidate record</li>
                    <li>Make handoffs from screening to technical review cleaner</li>
                    <li>Bring Taali signal back into the ATS view the team already works from</li>
                  </ul>
                </div>
                <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-5">
                  <div className="flex items-center gap-3">
                    <div className="grid h-[30px] w-[30px] place-items-center rounded-[8px] bg-[var(--amber)] font-[var(--font-mono)] text-[13px] font-bold text-[var(--taali-inverse-text)]">FF</div>
                    <div className="text-[15px] font-semibold">Transcription services · interview context</div>
                  </div>
                  <ul className="mt-4 list-disc space-y-1 pl-5 text-[13px] leading-[1.65] text-[var(--ink-2)]">
                    <li>Attach interview transcripts and summaries to the candidate record</li>
                    <li>Help recruiters jump straight to the moments that mattered</li>
                    <li>Use transcript evidence to prepare better follow-up interviews</li>
                    <li>Work with services like Fireflies without changing the interview flow</li>
                  </ul>
                </div>
              </div>
              <div className="mt-4 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.06em] text-[var(--mute)]">
                More ATS and transcription integrations are on the roadmap
              </div>
            </PlatformCard>

            <PlatformCard
              kicker="// 06 · CALIBRATION"
              title="Your bar, not ours."
              body="Paste in three rubrics from your team and we'll calibrate every score to them."
              className="xl:col-span-1"
            />

            <PlatformCard
              kicker="// 07 · RECORDINGS"
              title="Watch the thinking, not the typing."
              body="Scrub to every AI prompt, every rejection, every green test."
              className="xl:col-span-1"
            />
          </div>
        </div>
      </section>

      <section id="runtime" className="pb-20 md:pb-28">
        <div className={containerClass}>
          <SectionHeading
            kicker="02.5 · INSIDE THE RUNTIME"
            title={<>What your<br />candidate<br /><em>actually sees</em>.</>}
            copy="Not a whiteboard and not a toy sandbox. Candidates see the real browser workspace: task brief, files, editor, terminal, and AI panel in one place."
          >
            <div className="mt-6 flex flex-wrap gap-3">
              <button type="button" className="btn btn-primary btn-lg" onClick={() => onNavigate('demo')}>
                See the product <span className="arrow">→</span>
              </button>
              <button type="button" className="btn btn-outline btn-lg" onClick={() => onNavigate('demo')}>
                Book a demo
              </button>
            </div>
          </SectionHeading>

          <div className="rounded-[18px] border border-[var(--line)] bg-[var(--bg-2)] p-5">
            <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">Assessment example</div>
            <h3 className="mt-3 font-[var(--font-display)] text-[32px] leading-[1.02] tracking-[-0.03em]">
              {showcaseAssessment.title}
            </h3>
            <p className="mt-3 max-w-[900px] text-[14px] leading-7 text-[var(--mute)]">
              {showcaseAssessment.description}
            </p>
            <div className="mt-4 flex flex-wrap gap-5 font-[var(--font-mono)] text-[12px] text-[var(--mute)]">
              <span>{showcaseAssessment.durationLabel}</span>
              <span>{showcaseAssessment.stack}</span>
              <span>{showcaseAssessment.tools}</span>
            </div>
          </div>

          <AssessmentRuntimePreviewView
            className="mt-6 shadow-[var(--shadow-lg)]"
            taskName={runtimeShowcase.taskName}
            taskRole={runtimeShowcase.taskRole}
            taskContext={runtimeShowcase.taskContext}
            repoFiles={runtimeShowcase.repoFiles}
            initialSelectedRepoPath={runtimeShowcase.initialSelectedRepoPath}
            initialClaudePrompt={runtimeShowcase.initialClaudePrompt}
            claudeConversation={runtimeShowcase.claudeConversation}
            output={runtimeShowcase.output}
          />

          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {runtimeSignalCards.map((card) => (
              <div key={card.title} className="rounded-[18px] border border-[var(--line)] bg-[var(--bg-2)] p-5 shadow-[var(--shadow-sm)]">
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">{card.title}</div>
                <p className="mt-3 text-[13px] leading-6 text-[var(--ink-2)]">{card.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="how-it-works" className="pb-20 md:pb-28">
        <div className={containerClass}>
          <SectionHeading
            kicker="03 · HOW IT WORKS"
            title={<>From job requirement<br />to <em>confident</em> hire.</>}
            copy="Four clear steps. Low coordination overhead. Your team keeps the hiring workflow it already uses, while Taali adds the assessment signal underneath it."
          />

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {howItWorksSteps.map((step) => (
              <div key={step.step} className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple)]">{step.step}</div>
                <h3 className="mt-4 font-[var(--font-display)] text-[30px] leading-[1.02] tracking-[-0.03em]">{step.title}</h3>
                <p className="mt-4 text-[14px] leading-7 text-[var(--mute)]">{step.body}</p>
                <div className="mt-5 rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-3 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">
                  {step.meta}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="proof" className="pb-20 md:pb-28">
        <div className={containerClass}>
          <div className="rounded-[var(--radius-xl)] bg-[var(--ink)] px-8 py-10 text-[var(--bg)] shadow-[var(--shadow-lg)] md:px-12 md:py-12">
            <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple-2)]">04 · WHAT&apos;S DIFFERENT</div>
            <h2 className="mt-4 max-w-[820px] font-[var(--font-display)] text-[clamp(36px,4.8vw,60px)] leading-[0.95] tracking-[-0.04em]">
              Real stack. Real AI. Every keystroke. <em>Your bar.</em>
            </h2>
            <div className="mt-8 grid gap-6 md:grid-cols-2 xl:grid-cols-4">
              {proofItems.map((item) => (
                <div key={item.title}>
                  <div className="text-[42px] font-semibold tracking-[-0.03em] text-[var(--lime)]">{item.title}</div>
                  <p className="mt-3 text-[15px] leading-7 text-[var(--taali-inverse-text)] opacity-70">{item.body}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="pb-20 md:pb-28">
        <div className={containerClass}>
          <div className="relative overflow-hidden rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] px-8 py-10 shadow-[var(--shadow-md)] md:grid md:grid-cols-[1fr_320px] md:gap-8 md:px-10 md:py-12">
            <div
              className="pointer-events-none absolute inset-0 opacity-60 tally-bg-soft"
              style={{ maskImage: 'radial-gradient(60% 80% at 85% 50%, black, transparent 70%)' }}
            />
            <div className="relative">
              <div className="kicker">05 · GET STARTED</div>
              <h3 className="mt-4 font-[var(--font-display)] text-[clamp(34px,4.8vw,58px)] leading-[0.95] tracking-[-0.04em]">
                See Taali run on a <em>real</em> role of yours.
              </h3>
              <p className="mt-4 max-w-[560px] text-[15px] leading-7 text-[var(--mute)]">
                We&apos;ll walk through the recruiter workflow, reporting surfaces, and settings using a role close to yours, then map the product to your hiring process in about 30 minutes.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <button type="button" className="btn btn-primary btn-lg" onClick={() => onNavigate('demo')}>
                  Book a demo <span className="arrow">→</span>
                </button>
                <button
                  type="button"
                  className="btn btn-outline btn-lg"
                  onClick={() => {
                    window.location.href = 'mailto:hello@taali.ai?subject=Talk%20to%20founders';
                  }}
                >
                  Talk to founders
                </button>
              </div>
            </div>
            <div className="relative mt-8 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg)] p-5 md:mt-0">
              <div className="mb-2 text-sm font-semibold">A typical demo</div>
              {[
                ['ROLE', 'One of yours, calibrated'],
                ['PRODUCT', 'Recruiter workflow + reports'],
                ['DURATION', '~30 min walkthrough'],
                ['INTEGRATIONS', 'Workable + Fireflies fit'],
                ['DEBRIEF', 'With a Taali specialist'],
              ].map(([key, value]) => (
                <div key={key} className="flex items-center justify-between border-b border-[var(--line-2)] py-3 last:border-b-0">
                  <span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">{key}</span>
                  <span className="text-sm text-[var(--ink-2)]">{value}</span>
                </div>
              ))}
            </div>
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
