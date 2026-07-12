import React, { useEffect } from 'react';
import { Check } from 'lucide-react';

import { AssessmentRuntimePreviewView } from '../assessment_runtime/AssessmentRuntimePreviewView';
import { ActivityFeed } from '../home/ActivityFeed';
import { AgentScene } from './landing_preview/variant_g/AgentScene';
import {
  consumePendingMarketingSection,
  scrollToMarketingSection,
} from '../../lib/marketingScroll';
import { MarketingNav, TaaliLogo } from '../../shared/layout/TaaliLayout';
import '../../shared/motion/reveal.css';
import './heroAgentScene.css';

// The production homepage — the ORIGINAL agentic-first landing restored (the
// live <ActivityFeed> decision feed, the 5-Ds standing report, the real
// <AssessmentRuntimePreviewView> IDE walkthrough) with four learnings grafted
// in from the /landing-preview variant G experiment:
//   1. Refined, inclusive copy — the "screens, assesses, and decides — with
//      you" headline, and phrasing that covers engineering AND knowledge work
//      ("works with AI", never "engineers who ship with AI").
//   2. The animated agent-ON <AgentScene> (job flips OFF→ON on first scroll,
//      candidates flow into the decision lane, verdicts stamp) as the hero's
//      product graphic, beside the copy (styles in heroAgentScene.css).
//   3. Subtle scroll-in entrances via the shared production reveal.css .reveal
//      one-shot CSS animation (NOT motion/react — only AgentScene needs that).
//   4. A clean 5-Ds scorecard (Delegation / Description / Discernment /
//      Diligence / Deliverable).
// Chrome is the shared, site-wide <MarketingNav> (white nav, real TaaliLogo)
// and a footer whose every link resolves to a real destination. CTAs route
// through `onNavigate` (AppShell's navigateToPage): "See it live" → showcase,
// "Book a demo" → demo-lead.

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
// Score-provenance the live <ActivityFeed> renders under each score (a "v2.1.0"
// pill in the list). Mirrors production — the agent scores on the current
// holistic engine. Pre-screen rejects are unscored, so they carry none.
const _prov = (hoursAgo) => ({
  engine_version: '2.1.0',
  scored_at: new Date(_NOW - hoursAgo * 60 * 60 * 1000).toISOString(),
});
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
    score_summary: { score_provenance: _prov(0.2) },
    confidence: 0.91,
    reasoning:
      "She clears every must-have — the AWS and Python evidence is strong, and she scored 88 on the task, top of this role's pipeline. I'd put her in front of the technical panel.",
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
    score_summary: { score_provenance: _prov(0.6) },
    confidence: 0.5,
    reasoning:
      "I'm split on her systems-design depth — two of my checks said advance, one said assess again. I don't want to call this one for you. Take a look?",
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
      "On pre-screen I couldn't find the Spark or streaming experience the role needs, and the AI-tooling claims have no projects behind them. I wouldn't spend an assessment seat here.",
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
    score_summary: { score_provenance: _prov(0.5) },
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
    score_summary: { score_provenance: _prov(1.4) },
    human_disposition: 'taught',
    resolution_note: 'override → advance',
    resolved_at: new Date(_NOW - 52 * 60 * 1000).toISOString(),
  },
];

// The five recruiter-facing axes the live CandidateStandingReportPage renders —
// the 5 Ds. Mock scores; verdict uses the production band vocabulary
// (Strong Hire >= 80).
const SCORECARD_5DS = [
  { label: 'Delegation', score: 86 },
  { label: 'Description', score: 88 },
  { label: 'Discernment', score: 90 },
  { label: 'Diligence', score: 82 },
  { label: 'Deliverable', score: 84 },
];

// Footer link columns. Every destination is real and resolves: `section`
// scrolls to an in-page anchor that EXISTS on this page (#how-it-works,
// #platform); `page` is a real route through onNavigate; `href` is a literal
// mailto or static guide page. No dead links.
const footerColumns = [
  {
    title: 'Product',
    items: [
      { label: 'How it works', section: 'how-it-works' },
      { label: 'AI-native assessment', section: 'platform' },
      { label: 'Product walkthrough', page: 'showcase' },
      { label: 'Developers / API', page: 'developers' },
    ],
  },
  {
    title: 'Company',
    items: [
      { label: 'Blog', page: 'blog' },
      { label: 'Book a demo', page: 'demo-lead' },
      { label: 'Contact', href: 'mailto:hello@taali.ai' },
    ],
  },
  {
    title: 'Guides',
    items: [
      { label: 'What is agentic hiring?', href: '/agentic-hiring' },
      { label: 'AI-native hiring', href: '/ai-native-hiring' },
      { label: 'AI-native assessments', href: '/ai-native-assessments' },
    ],
  },
];

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

      {/* HERO — two columns: refined copy left, the animated agent-ON scene
          (variant G's <AgentScene>) right. */}
      <section className="relative overflow-hidden pb-16 pt-12 md:pb-24 md:pt-16">
        <div className={containerClass}>
          <div className="grid items-center gap-12 lg:grid-cols-[1.05fr_0.95fr] lg:gap-16">
            <div className="reveal">
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
                AGENT-NATIVE HIRING
              </div>
              <h1
                className="font-[var(--font-display)] font-semibold"
                style={{
                  fontSize: 'clamp(40px,5.6vw,64px)',
                  lineHeight: 1.04,
                  letterSpacing: '-0.04em',
                  margin: '0 0 22px',
                  maxWidth: 720,
                }}
              >
                The hiring agent that screens, assesses, and{' '}
                <em className="not-italic text-[var(--purple)]">decides — with you.</em>
              </h1>
              <p className="text-[1.125rem] leading-[1.55] text-[var(--ink-2)]" style={{ maxWidth: 560, margin: '0 0 22px' }}>
                Taali is the agentic hiring platform — one governed agent runs screening,
                AI-fluency assessment, and defensible decisions end to end, across
                <em className="not-italic font-medium text-[var(--ink)]"> engineering and knowledge work</em>.
                It paces itself within your budget and asks you on every call that matters.
                Every consequential decision still goes through you.
              </p>
              <div className="flex flex-wrap gap-3 text-[0.8125rem] text-[var(--ink-2)]" style={{ marginBottom: 30 }}>
                {[
                  { k: 'AGENTIC', v: 'Runs your pipeline 24/7 — pauses for your judgment' },
                  { k: 'AI-NATIVE', v: 'The only platform that scores how people work with AI' },
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
              <div className="flex flex-wrap gap-3">
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
                  See it live <span className="arrow">→</span>
                </button>
              </div>
            </div>

            {/* The agent-ON scene: job card flips OFF→ON on first scroll, three
                candidates flow into the decision lane, each verdict stamps
                (Maya 88 Advance / Jordan 84 Advance / Tariq 41 Reject). Scoped
                under .lvg-scene so heroAgentScene.css styles it. */}
            <div className="reveal lvg-scene" style={{ '--reveal-delay': '0.12s' }}>
              <AgentScene />
            </div>
          </div>
        </div>
      </section>

      {/* HOW THE AGENT WORKS — 3-step section + the live decision feed */}
      <section id="how-it-works" className="border-t border-[var(--line)] bg-[var(--bg-2)]">
        <div className={`${containerClass} py-20`}>
          <div className="reveal">
            <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.14em] text-[var(--purple)]">
              HOW THE AGENT WORKS
            </div>
            <h2 className="mt-3 max-w-[52.5rem] font-[var(--font-display)] text-[clamp(32px,4vw,42px)] font-semibold leading-[1.1] tracking-[-0.025em] text-[var(--ink)]">
              An autonomous agent in your pipeline. <em className="not-italic text-[var(--purple)]">Built for the AI-native hire.</em>
            </h2>
            <p className="mt-5 max-w-[42.5rem] text-[0.96875rem] leading-[1.6] text-[var(--ink-2)]">
              Taali runs three loops continuously — triage, assess, decide — and pauses the moment your judgment is needed.
              Every assessment puts the candidate in a real workspace with AI in their hand, then measures how well they wield it.
            </p>
          </div>
          <div className="reveal-stagger mt-14 grid gap-7 lg:grid-cols-3">
            {[
              {
                n: '01',
                t: 'Triage — autonomously',
                d: "Every cycle, the agent surveys the role, decides where the work is — fetch CVs, pre-screen, score, send assessments, queue advances or rejects — and pauses to ask you when it needs input it can't derive on its own. You set the criteria once; it works the pipeline 24/7 within the budget you set.",
              },
              {
                n: '02',
                t: 'Assess — for the AI era',
                d: 'Hands-on, role-relevant tasks in a chat-first workspace — Claude in the candidate’s hands, engineering or knowledge work. We track every prompt, paste, and decision — then score AI collaboration alongside craft. The only platform that tells you whether a candidate can actually work with AI — and whether they verified before calling it done. Every task is battle-tested in a sandbox before a candidate ever sees it.',
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

          {/* Decision feed — the live <ActivityFeed /> from features/home (the
              same component rendered on the Hub at /home), fed mock rows that
              match its shape. Wrapped in browser chrome so it reads as a
              product snapshot, not a marketing illustration. */}
          <div className="reveal mt-14 overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
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

      {/* WE MEASURE HOW PEOPLE USE AI — the differentiator + the 5-Ds scorecard */}
      <section id="platform" className="border-t border-[var(--line)] bg-white">
        <div className={`${containerClass} py-20`}>
          <div className="grid gap-16 lg:grid-cols-[1fr_1.1fr] lg:items-center">
            <div className="reveal">
              <div className="font-[var(--font-mono)] text-[0.6875rem] uppercase tracking-[0.14em] text-[var(--purple)]">
                AI-NATIVE ASSESSMENT
              </div>
              <h2 className="mt-3 font-[var(--font-display)] text-[clamp(34px,4.6vw,44px)] font-semibold leading-[1.05] tracking-[-0.03em] text-[var(--ink)]">
                You hire people <em className="not-italic text-[var(--purple)]">who use AI.</em><br />
                We&apos;re the only platform that measures it.
              </h2>
              <p className="mt-5 text-[1rem] leading-[1.6] text-[var(--ink-2)]">
                Every assessment opens a chat-first workspace — Claude at the centre, your repo, a real editor, and a sandboxed runtime around it — exactly the way people work now, engineering or knowledge work.
                Behind the scenes the runtime captures every prompt, paste, edit, file open, test run, and commit, time-stamped to the second.
                Those traces feed one scorecard — five dimensions, the 5 Ds: Delegation, Description, Discernment, Diligence, and the Deliverable itself — so how a candidate works with AI is scored as a first-class dimension alongside the result they ship.
              </p>
              <ul className="reveal-stagger mt-7 flex flex-col gap-3.5">
                {[
                  { t: 'AI collaboration score', d: 'Did they prompt well? Catch the trap we planted? Know when not to use it?' },
                  { t: 'Prompt-by-prompt replay', d: 'See exactly how they worked the agent — not just the final output.' },
                  { t: 'Full session telemetry', d: 'Edit timeline, sandboxed test runs, file opens — everything tied back to the final report.' },
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

            {/* The 5-Ds standing-report scorecard (Delegation / Description /
                Discernment / Diligence / Deliverable). Verdict band vocabulary
                (Strong Hire >= 80). Mock scores. */}
            <div className="reveal overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
              <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3 font-[var(--font-mono)] text-[0.71875rem] text-[var(--mute)]">
                <span>MAYA CHEN · CANDIDATE REPORT</span>
                <span className="font-semibold text-[var(--purple)]">Strong Hire · Taali 86</span>
              </div>
              <div className="space-y-4 px-5 py-6">
                {SCORECARD_5DS.map(({ label, score }) => (
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

          {/* IDE preview — the real workspace component
              (AssessmentRuntimePreviewView) in staticPreview mode: a
              non-interactive snapshot of a live candidate session, scaled to
              80% via CSS transform so the IDE renders at its natural ~1440-wide
              layout and fits the band without cramping. */}
          <p className="reveal mt-12 mb-3 text-[0.875rem] text-[var(--ink-2)]">
            <strong className="text-[var(--ink)]">Candidates work here.</strong>{' '}
            The AI assistant sits at the centre — they drive the task in conversation, open and edit files beside it, run tests in a sandboxed runtime. We watch every prompt.
          </p>
          <div className="reveal overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
            <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5 font-[var(--font-mono)] text-[0.6875rem] text-[var(--mute)]">
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#f06' }} />
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#ffb020' }} />
              <span className="h-[0.5625rem] w-[0.5625rem] rounded-full" style={{ background: '#39c66d' }} />
              <span className="ml-3">app.taali.ai/assess/preview</span>
              <span className="ml-auto rounded-full bg-[color:var(--bg)] px-2 py-0.5 text-[0.625rem] font-semibold text-[var(--mute)]">Locked preview</span>
            </div>
            <div className="mc-landing-ide">
              <div className="mc-landing-ide-scale">
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

      {/* BOTTOM CTA — softened, token-based purple gradient */}
      <section className="bg-[var(--bg)]">
        <div className={`${containerClass} py-16`}>
          <div
            className="reveal relative overflow-hidden rounded-[18px] px-12 py-14"
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
                  Or tell us what you&apos;re hiring for and we&apos;ll follow up by email.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[0.875rem] font-semibold text-[var(--purple)]"
                  style={{ boxShadow: '0 10px 28px -8px rgba(0,0,0,0.3)' }}
                  onClick={() => onNavigate('showcase')}
                >
                  See it live →
                </button>
                <button
                  type="button"
                  className="inline-flex h-12 items-center gap-2 rounded-full px-7 text-[0.875rem] font-semibold text-white"
                  style={{ border: '1px solid rgba(255,255,255,0.55)', background: 'transparent' }}
                  onClick={() => onNavigate('demo-lead')}
                >
                  Book a demo
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
                The agentic hiring platform. One governed agent runs your funnel — you decide every call that matters.
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
            <div>© 2026 Taali</div>
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
