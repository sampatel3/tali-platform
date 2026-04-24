import React, { useEffect } from 'react';
import { Sparkles } from 'lucide-react';

import {
  MomentCards,
  ShowcaseCtaBand,
  TaskBriefCard,
  WorkspaceReplayFrame,
} from '../../components/ProductPreviewFrames';
import {
  consumePendingMarketingSection,
  navigateToMarketingSection,
  scrollToMarketingSection,
} from '../../lib/marketingScroll';
import { MarketingNav, TaaliLogo } from '../../shared/layout/TaaliLayout';

const containerClass = 'mx-auto max-w-[1360px] px-6 md:px-10 xl:px-16';

// TODO(copy): verify uniqueness claim with marketing/legal before shipping.
const heroSignals = [
  { label: 'Prompt quality', value: 91 },
  { label: 'Error recovery', value: 86 },
  { label: 'Context utilization', value: 88 },
  { label: 'Independence', value: 94 },
];

const capabilityCards = [
  {
    kicker: '// 01 · LIVE CODING',
    title: 'Real IDE. Real stack. Real AI tools.',
    body: 'Candidates write code in an in-browser IDE that mirrors your team setup, with Claude, Copilot, and a terminal available inside the task.',
    tone: 'hero',
  },
  {
    kicker: '// 02 · AI COLLAB SCORING',
    title: 'A score for how they use AI.',
    body: 'Every prompt, accept, reject, and edit becomes a signal across six scored dimensions.',
    tone: 'meter',
  },
  {
    kicker: '// 03 · QUESTION BANK',
    title: '600+ calibrated real-world tasks.',
    body: 'From debugging a production outage to extending a flaky migration, each task starts from a working repo instead of a whiteboard puzzle.',
    tone: 'list',
  },
  {
    kicker: '// 04 · INTEGRITY',
    title: 'Proctoring that won’t insult anyone.',
    body: 'Signal, not surveillance. We flag paste-ins and suspicious patterns without turning the session into a trust exercise.',
    tone: 'pill',
  },
  {
    kicker: '// 05 · INTEGRATIONS',
    title: 'Fits your stack.',
    body: 'Recruiters stay in the workflow they already use while Taali handles the assessment signal underneath.',
    tone: 'pill',
  },
  {
    kicker: '// 06 · CALIBRATION',
    title: 'Your bar, not ours.',
    body: 'Scores can be calibrated to the hiring team, rubric, and role rather than a generic benchmark.',
    tone: 'plain',
  },
];

// TODO(copy): confirm these product-capability claims against current product reality.
const proofItems = [
  {
    title: 'Live stack',
    body: 'Candidates ship real code on your real tech, not a toy sandbox.',
  },
  {
    title: 'Real AI',
    body: 'Claude and companion tools are available in the session, and Taali scores how candidates use them.',
  },
  {
    title: 'Every keystroke',
    body: 'Session replay, prompt log, and validation runs roll into the report.',
  },
  {
    title: 'Your bar',
    body: 'Calibrate scoring to the workflow and hiring bar your team actually uses.',
  },
];

const processSteps = [
  {
    step: 'STEP 01',
    title: 'Pick a role.',
    body: 'Start from the role you are hiring for and attach a rubric aligned to the work.',
    foot: '◐ auto-calibrated rubric',
  },
  {
    step: 'STEP 02',
    title: 'Invite the candidate.',
    body: 'They get a link into an in-browser IDE with your stack and real AI tools already loaded.',
    foot: '→ 1-click invite from ATS',
  },
  {
    step: 'STEP 03',
    title: 'They ship the task.',
    body: 'We record prompts, accept/reject decisions, test runs, and refactors while the candidate works.',
    foot: '● silent scoring, every 30s',
  },
  {
    step: 'STEP 04',
    title: 'You get the signal.',
    body: 'A composite score, an AI-collab band, replay highlights, and a recommendation land in the recruiter workspace.',
    foot: '✓ delivered to the hiring team fast',
  },
];

const productTourItems = [
  { kicker: '04 · DEMO / ONBOARDING', title: 'Book a demo.', body: 'Enter role, company, and work email, then move directly into the product showcase.', page: 'demo' },
  { kicker: '05 · CANDIDATE WELCOME', title: 'Candidate welcome.', body: 'Clear rules, system check, and assessment framing before the timer starts.', page: 'demo' },
  { kicker: '06 · CANDIDATE WORKSPACE', title: 'Workspace replay.', body: 'Repo, runtime, Claude chat, and evidence capture in one place.', page: 'demo' },
  { kicker: '07 · STANDING REPORT', title: 'Assessment report.', body: 'Verdict, scored dimensions, and evidence recruiters can share with the panel.', page: 'login' },
  { kicker: '08 · PIPELINE', title: 'Jobs + candidates.', body: 'Open the role, sort the pipeline, and jump straight into the strongest report.', page: 'login' },
  { kicker: '09 · SETTINGS', title: 'Settings.', body: 'Scoring policy, AI tooling permissions, members, and ATS access.', page: 'login' },
];

const footerColumns = [
  {
    title: 'Product',
    items: [
      { label: 'Live coding', section: 'runtime-preview' },
      { label: 'AI collab score', section: 'platform' },
      { label: 'Question bank', section: 'platform' },
      { label: 'Integrations', section: 'platform' },
      { label: 'Pricing', page: 'demo' },
    ],
  },
  {
    title: 'Company',
    items: [
      { label: 'Customers', section: 'proof' },
      { label: 'Manifesto', section: 'problem' },
      { label: 'Careers', page: 'demo' },
      { label: 'Blog', page: 'demo' },
    ],
  },
  {
    title: 'Resources',
    items: [
      { label: 'Sample reports', section: 'runtime-preview' },
      { label: 'Rubric library', section: 'platform' },
      { label: 'Docs', page: 'demo' },
      { label: 'Security', page: 'demo' },
    ],
  },
];

const SectionHeading = ({ kicker, title, copy }) => (
  <div className="mb-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_420px] lg:items-end">
    <div>
      <div className="kicker">{kicker}</div>
      <h2 className="mt-3 font-[var(--font-display)] text-[clamp(38px,5vw,60px)] font-semibold leading-[0.95] tracking-[-0.04em]">
        {title}
      </h2>
    </div>
    <p className="max-w-[420px] text-[15px] leading-7 text-[var(--mute)]">{copy}</p>
  </div>
);

const SignalRow = ({ label, value }) => (
  <div className="grid grid-cols-[140px_1fr_42px] items-center gap-3 text-[13.5px]">
    <span className="text-[var(--ink-2)]">{label}</span>
    <div className="bar"><i style={{ width: `${value}%` }} /></div>
    <span className="text-right font-[var(--font-mono)]">{value}</span>
  </div>
);

export const LandingPage = ({ onNavigate }) => {
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
            AI-tool proficiency scoring — now live
            <span className="text-[var(--mute-2)]">→</span>
          </span>
          <h1 className="h-display mt-6 text-[clamp(56px,7.3vw,108px)] leading-[0.94]">
            Hire engineers who can actually <em>ship</em> with AI.
          </h1>
          <p className="mt-5 max-w-[560px] text-[19px] leading-[1.55] text-[var(--mute)]">
            Taali is the only technical assessment platform that measures how candidates <em>use</em> AI tools to solve real engineering problems, not just whether they can code without them.
          </p>
          <div className="mt-9 flex flex-wrap gap-3">
            <button type="button" className="btn btn-primary btn-lg" onClick={() => onNavigate('demo')}>
              Book a demo <span className="arrow">→</span>
            </button>
            <button type="button" className="btn btn-outline btn-lg" onClick={() => navigateToMarketingSection('runtime-preview', onNavigate)}>
              See the assessment example
            </button>
          </div>
          <div className="mt-10 flex items-center gap-5 text-[13px] text-[var(--mute)]">
            <div className="flex">
              {['#E9DDFE', '#FFD1B8', '#C8F169', '#CDE0FF'].map((color, index) => (
                <div
                  key={color}
                  className="h-7 w-7 rounded-full border-2 border-[var(--bg)]"
                  style={{ marginLeft: index === 0 ? 0 : -8, background: color }}
                />
              ))}
            </div>
            <div>
              {/* TODO(copy): verify customer proof references or replace with approved social proof. */}
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
            <div className="flex items-center gap-2 font-[var(--font-mono)] text-xs text-[var(--ink-2)]">
              <span className="h-2 w-2 rounded-full bg-[var(--green)] shadow-[0_0_0_3px_color-mix(in_oklab,var(--green)_25%,transparent)]" />
              LIVE · 00:42:18
            </div>
          </div>

          <div className="grid grid-cols-[auto_1fr_auto] items-center gap-4 rounded-[var(--radius)] bg-[var(--bg)] p-5">
            <div className="relative grid h-[72px] w-[72px] place-items-center">
              <svg width="72" height="72" viewBox="0 0 72 72" className="absolute inset-0 -rotate-90">
                <circle cx="36" cy="36" r="30" fill="none" stroke="var(--bg-3)" strokeWidth="6" />
                <circle cx="36" cy="36" r="30" fill="none" stroke="var(--purple)" strokeWidth="6" strokeLinecap="round" strokeDasharray="188.4" strokeDashoffset="32" />
              </svg>
              <span className="font-[var(--font-display)] text-[28px] leading-none">83</span>
            </div>
            <div>
              <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Composite · Senior Fullstack</div>
              <div className="mt-1 text-[17px] font-semibold">Priya Anand</div>
              <div className="mt-1 text-[13px] text-[var(--mute)]">Task: “Review a GenAI production release with Claude Code”</div>
            </div>
            <div className="rounded-full bg-[color-mix(in_oklab,var(--green)_18%,transparent)] px-3 py-1.5 font-[var(--font-mono)] text-xs text-[var(--green)]">
              Strong hire
            </div>
          </div>

          <div className="mt-4 grid gap-3">
            {/* TODO(copy): these hero signal values are illustrative; confirm label + values before ship. */}
            {heroSignals.map((signal) => (
              <SignalRow key={signal.label} label={signal.label} value={signal.value} />
            ))}
          </div>

          <div className="mt-4 flex gap-3 rounded-[var(--radius)] border border-dashed border-[color-mix(in_oklab,var(--purple)_40%,var(--line))] bg-[color-mix(in_oklab,var(--purple)_8%,var(--bg-2))] p-4 text-[13.5px] leading-6 text-[var(--ink-2)]">
            <div className="grid h-7 w-7 shrink-0 place-items-center rounded-[8px] bg-[var(--purple)] text-white">
              <Sparkles size={14} />
            </div>
            <div>
              <b className="block text-[var(--ink)]">AI-assisted signal</b>
              Delegated boilerplate and schema work, wrote the risky release logic herself, and pushed back on Claude&apos;s incorrect suggestions without prompting.
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
          copy="A candidate who can memorize algorithm trivia but cannot guide AI, validate output, or debug under pressure will still struggle in the real job. Taali swaps trivia for the behaviors modern engineering teams actually need to review."
        />
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-7 shadow-[var(--shadow-sm)]">
            <span className="inline-flex rounded-full bg-[var(--bg-3)] px-3 py-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">Legacy platforms</span>
            <h3 className="mt-4 font-[var(--font-display)] text-[42px] leading-[0.96] tracking-[-0.03em]">Invert a binary tree on a whiteboard.</h3>
            <p className="mt-4 text-[14px] leading-7 text-[var(--mute)]">
              Tests pattern recall. Optimizes for candidates who grind LeetCode. Correlates weakly with on-the-job performance and says almost nothing about AI-era collaboration skill.
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
            <span className="inline-flex rounded-full bg-[var(--purple)] px-3 py-1 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-white">Taali</span>
            <h3 className="mt-4 font-[var(--font-display)] text-[42px] leading-[0.96] tracking-[-0.03em]">Review a GenAI release with Claude Code.</h3>
            <p className="mt-4 text-[14px] leading-7 text-[var(--mute)]">
              Tests real judgment. The candidate uses your stack, your AI tools, and your workflow. Taali scores prompt quality, error recovery, independence, and design thinking across the session.
            </p>
            <div className="mt-6 rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4 font-[var(--font-mono)] text-[12px] leading-6 text-[var(--ink-2)]">
              <div className="mb-2 text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">01 · real task · genai production readiness</div>
              <div className="text-[var(--mute)]"># BUG: moderation outages should not default to allow</div>
              <div><span className="text-[var(--purple)]">if</span> moderation_result <span className="text-[var(--purple)]">is</span> None:</div>
              <div>&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> True</div>
              <div><span className="text-[var(--purple)]">if</span> user_intent <span className="text-[var(--purple)]">in</span> SAFETY_POLICY[&quot;always_escalate&quot;]:</div>
              <div>&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> False</div>
              <div className="mt-4 flex gap-3 rounded-[12px] border border-[var(--line)] bg-[var(--purple-soft)] p-3 text-[11.5px] leading-5 text-[var(--ink-2)]">
                <div className="grid h-6 w-6 place-items-center rounded-[7px] bg-[var(--purple)] text-[10px] text-white">AI</div>
                <div>
                  <b>Taali scored:</b> Candidate prompted “highest-risk launch blockers first” and rejected Claude’s premature cache wrapper until the evidence was clear.
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
          copy="Live coding in-browser, real AI tools, calibrated tasks, and recruiter views that surface only the signals that matter."
        />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {capabilityCards.map((card) => (
            <div
              key={card.kicker}
              className={`rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)] ${card.tone === 'hero' ? 'md:col-span-2 xl:row-span-2' : ''}`.trim()}
            >
              <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple)]">{card.kicker}</div>
              <h3 className="mt-3 font-[var(--font-display)] text-[34px] leading-[1.02] tracking-[-0.03em]">{card.title}</h3>
              <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">{card.body}</p>
              {card.tone === 'hero' ? (
                <div className="mt-5 rounded-[16px] border border-[var(--line)] bg-[var(--bg)] p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="flex gap-1.5">
                      <span className="h-2 w-2 rounded-full bg-[var(--red)]" />
                      <span className="h-2 w-2 rounded-full bg-[var(--amber)]" />
                      <span className="h-2 w-2 rounded-full bg-[var(--green)]" />
                    </div>
                    <span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">app/release_guardrails.py</span>
                  </div>
                  <div className="space-y-1 font-[var(--font-mono)] text-[12px] leading-6 text-[var(--ink-2)]">
                    <div><span className="text-[var(--purple)]">from</span> app.policy <span className="text-[var(--purple)]">import</span> SAFETY_POLICY</div>
                    <div><span className="text-[var(--purple)]">def</span> should_allow_response(*, moderation_result, confidence):</div>
                    <div>&nbsp;&nbsp;<span className="text-[var(--purple)]">if</span> moderation_result <span className="text-[var(--purple)]">is</span> None:</div>
                    <div className="text-[var(--mute)]">&nbsp;&nbsp;&nbsp;&nbsp;# BUG: outage shouldn&apos;t default to allow</div>
                    <div>&nbsp;&nbsp;&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> True</div>
                    <div>&nbsp;&nbsp;<span className="text-[var(--purple)]">return</span> confidence &gt;= 0.42</div>
                  </div>
                </div>
              ) : null}
              {card.tone === 'meter' ? (
                <div className="mt-5 grid gap-3">
                  {heroSignals.map((signal) => <SignalRow key={signal.label} label={signal.label} value={signal.value} />)}
                </div>
              ) : null}
              {card.tone === 'list' ? (
                <div className="mt-5 space-y-2 rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4 font-[var(--font-mono)] text-[12px] text-[var(--ink-2)]">
                  <div className="flex items-center justify-between"><span>Q.041</span><span className="chip red">Hard</span></div>
                  <div className="flex items-center justify-between"><span>Q.088</span><span className="chip amber">Medium</span></div>
                  <div className="flex items-center justify-between"><span>Q.124</span><span className="chip amber">Medium</span></div>
                  <div className="flex items-center justify-between"><span>Q.207</span><span className="chip red">Hard</span></div>
                </div>
              ) : null}
              {card.tone === 'pill' ? (
                <div className="mt-5 flex flex-wrap gap-2">
                  {card.kicker.includes('INTEGRATIONS')
                    ? ['Greenhouse', 'Ashby', 'Lever', 'Slack'].map((item) => <span key={item} className="chip">{item}</span>)
                    : ['Stealth paste detection', 'Window focus trails', 'Voice verification'].map((item) => <span key={item} className="chip">{item}</span>)}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </section>

    <section id="runtime-preview" className="pb-20 md:pb-28">
      <div className={containerClass}>
        <SectionHeading
          kicker="02.5 · PRODUCT SHOWCASE"
          title={<>Here&apos;s what they <em>actually do</em>.</>}
          copy="One real task, one real candidate, one real session. We replay a GenAI Production Readiness review so you can see exactly how Taali captures signal in real time."
        />
        <TaskBriefCard />
        <WorkspaceReplayFrame className="mt-6" />
        <MomentCards className="mt-6" />
        <div className="mt-6">
          <ShowcaseCtaBand
            onPrimaryAction={() => onNavigate('demo')}
            onSecondaryAction={() => onNavigate('login')}
          />
        </div>
      </div>
    </section>

    <section id="how-it-works" className="pb-20 md:pb-28">
      <div className={containerClass}>
        <SectionHeading
          kicker="03 · HOW IT WORKS"
          title={<>From job req to <em>confident</em> hire.</>}
          copy="Four steps. Minimal coordination tax. Recruiters stay in their workflow while Taali runs underneath as signal."
        />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {processSteps.map((step) => (
            <div key={step.step} className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
              <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple)]">{step.step}</div>
              <h4 className="mt-4 text-[22px] font-semibold tracking-[-0.02em]">{step.title}</h4>
              <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">{step.body}</p>
              <div className="mt-5 rounded-[12px] bg-[var(--bg)] px-4 py-3 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--ink-2)]">
                {step.foot}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>

    <section id="proof" className="pb-20 md:pb-28">
      <div className={containerClass}>
        <div className="rounded-[var(--radius-xl)] bg-[var(--ink)] px-8 py-10 text-[var(--bg)] shadow-[var(--shadow-lg)] md:px-12 md:py-12">
          <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple-2)]">04 · THE EVIDENCE</div>
          <h2 className="mt-4 max-w-[820px] font-[var(--font-display)] text-[clamp(36px,4.8vw,60px)] leading-[0.95] tracking-[-0.04em]">
            Teams using Taali ship faster and <em>keep</em> the engineers they hire.
          </h2>
          <div className="mt-8 grid gap-6 md:grid-cols-2 xl:grid-cols-4">
            {proofItems.map((item) => (
              <div key={item.title}>
                <div className="text-[42px] font-semibold tracking-[-0.03em] text-[var(--lime)]">{item.title}</div>
                <p className="mt-3 text-[15px] leading-7 text-white/70">{item.body}</p>
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
            <h3 className="mt-4 font-[var(--font-display)] text-[clamp(34px,4.8vw,58px)] leading-[0.95] tracking-[-0.04em]">See Taali run on a <em>real</em> role of yours.</h3>
            <p className="mt-4 max-w-[560px] text-[15px] leading-7 text-[var(--mute)]">
              We&apos;ll build a calibrated assessment for one of your open roles, run sample candidates through it, and walk you through the signals.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <button type="button" className="btn btn-primary btn-lg" onClick={() => onNavigate('demo')}>
                Book a demo <span className="arrow">→</span>
              </button>
              <button type="button" className="btn btn-outline btn-lg" onClick={() => onNavigate('demo')}>
                Request callback
              </button>
            </div>
          </div>
          <div className="relative mt-8 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg)] p-5 md:mt-0">
            <div className="mb-2 text-sm font-semibold">Your demo · preview</div>
            {/* TODO(copy): confirm the specific demo offer details before ship. */}
            {[
              ['ROLE', 'Senior Fullstack · AI Workflow'],
              ['STACK', 'TS · Postgres · Claude'],
              ['DURATION', '75 minutes'],
              ['CANDIDATES', '3 sample (we provide)'],
              ['DEBRIEF', '30 min · founders'],
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

    <section id="product-tour" className="pb-24">
      <div className={containerClass}>
        <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="kicker">06 · PRODUCT TOUR</div>
            <h3 className="mt-3 font-[var(--font-display)] text-[46px] leading-none tracking-[-0.04em]">Walk the whole <em>product</em>.</h3>
          </div>
          <p className="max-w-[560px] text-[15px] leading-7 text-[var(--mute)]">
            Every surface in Taali, redesigned to match this page: demo request, candidate assessment flow, recruiter workspace, and assessment reporting.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {productTourItems.map((item) => (
            <button
              key={item.kicker}
              type="button"
              className="tile text-left transition-transform hover:-translate-y-0.5"
              onClick={() => onNavigate(item.page)}
            >
              <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple)]">{item.kicker}</div>
              <div className="mt-2 font-[var(--font-display)] text-[26px] tracking-[-0.02em]">{item.title}</div>
              <p className="mt-2 text-[13.5px] leading-6 text-[var(--mute)]">{item.body}</p>
            </button>
          ))}
        </div>
      </div>
    </section>

    <footer className="border-t border-[var(--line)] bg-[var(--ink)] text-[var(--bg)]">
      <div className={`${containerClass} py-14`}>
        <div className="grid gap-10 lg:grid-cols-[1.1fr_.9fr_.9fr_.9fr]">
          <div>
            <TaaliLogo onClick={() => onNavigate('landing')} wordmarkClassName="!text-[var(--bg)]" />
            <p className="mt-5 max-w-[280px] text-[15px] leading-7 text-white/68">
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
                    className="w-fit text-left text-[14px] text-white/66 transition hover:text-white"
                    onClick={() => {
                      if (item.section) {
                        navigateToMarketingSection(item.section, onNavigate);
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

        <div className="mt-12 font-[var(--font-display)] text-[clamp(72px,12vw,164px)] leading-none tracking-[-0.08em] text-white/8">
          taali<em className="text-[var(--purple)] not-italic">.</em>
        </div>

        <div className="mt-6 flex flex-col gap-3 border-t border-white/10 pt-5 text-[13px] text-white/52 md:flex-row md:items-center md:justify-between">
          <div>© 2026 Taali, Inc. · San Francisco</div>
          <button
            type="button"
            className="w-fit text-left text-white/68 transition hover:text-white"
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
