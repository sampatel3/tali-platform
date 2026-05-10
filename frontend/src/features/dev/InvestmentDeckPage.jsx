// Internal "deck assets" page — Sam's investment-deck screenshot canvas.
//
// Not linked from anywhere. Direct URL only: /deck.
// Renders the marketing-grade snippets (pipeline diagram, decision feed,
// candidate report bars, IDE preview) on a clean white surface so each
// can be screenshot-captured for slides without any nav chrome around it.

import React from 'react';

import { ActivityFeed } from '../home/ActivityFeed';
import { AssessmentRuntimePreviewView } from '../assessment_runtime/AssessmentRuntimePreviewView';

import './deck.css';

const _NOW = Date.now();

const PIPELINE_STAGES = [
  {
    n: '01',
    stage: 'Apply',
    cards: [
      { t: 'Workable / ATS sync', d: 'Pulls candidates in every 15 min, parses CVs into structured fields, dedupes by email + profile.' },
      { t: 'Direct upload', d: 'Drag-and-drop CVs anywhere. The agent picks them up on its next 30-min tick.' },
      { t: 'CV parsing', d: 'Work history, education, skills extracted into a clean schema for downstream scoring.' },
    ],
  },
  {
    n: '02',
    stage: 'Pre-screen',
    cards: [
      { t: 'Cheap LLM pre-screen', d: 'Yes / no / maybe in seconds. Cuts obvious mismatches before you spend on full scoring.' },
      { t: 'Must-have matching', d: "Filters against the role's hard requirements (location, eligibility, must-have skills)." },
      { t: 'Calibrated to your bar', d: 'Per-role threshold the recruiter sets once; the agent honours it on every cycle.' },
    ],
  },
  {
    n: '03',
    stage: 'Score & rank',
    cards: [
      { t: 'CV ↔ JD matching', d: '8-axis dimension scores grounded in evidence; per-requirement breakdown.' },
      { t: 'Calibrated p_advance', d: 'Per-candidate probability of advancing, calibrated against the role family.' },
      { t: 'Cohort signals', d: "Surfaces which skills, companies, and schools cluster among the role's top decile." },
      { t: 'Knowledge graph', d: 'Pulls priors from candidates with shared work history when the data exists.' },
    ],
  },
  {
    n: '04',
    stage: 'Assess',
    cards: [
      { t: 'Live in-browser IDE', d: "Editor, terminal, repo, AI side panel — exactly the workspace they'd work in on day one." },
      { t: 'Session telemetry', d: 'Every prompt, paste, edit, file open, test run, commit — time-stamped to the second.' },
      { t: 'Fraud + autopilot detection', d: 'Flags pasted-without-reading, copy-from-elsewhere, suspicious idle patterns.' },
      { t: 'AI fluency scoring', d: '8-axis rubric — coding ability, working with AI, debugging, communication, role fit.' },
    ],
  },
  {
    n: '05',
    stage: 'Decide',
    cards: [
      { t: 'Deterministic policy', d: 'Every recommendation traces to a named rule + revision id. No black box.' },
      { t: 'Asks you when stuck', d: 'Genuine gaps (missing must-have, ambiguous threshold) surface as inline questions.' },
      { t: 'One-click approve / override', d: 'High-stakes calls (advance / reject) always queue for your sign-off.' },
      { t: 'Learns from your overrides', d: 'Nightly retune absorbs your corrections into the next policy revision.' },
    ],
  },
  {
    n: '06',
    stage: 'Interview',
    cards: [
      { t: 'Tailored interview pack', d: 'Questions per candidate, grounded in their CV + assessment evidence.' },
      { t: 'Standing report', d: 'TAALI score, dimension breakdown, AI-usage trace, prompt-by-prompt replay.' },
      { t: 'ATS write-back', d: 'Decisions sync back to Workable; Greenhouse + Ashby on the roadmap.' },
    ],
  },
];

const DECISION_FEED_ROWS = [
  {
    id: 21,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Maya Chen',
    application_id: 1042,
    role_id: 109,
    reasoning: "Strong fit. Top of this role's pipeline.",
    created_at: new Date(_NOW - 6 * 60 * 1000).toISOString(),
  },
  {
    id: 20,
    status: 'pending',
    decision_type: 'reject',
    candidate_name: 'Tariq Al-Ahmad',
    application_id: 1018,
    role_id: 109,
    reasoning: 'Well below your bar. Missing the must-have skills.',
    created_at: new Date(_NOW - 44 * 60 * 1000).toISOString(),
  },
  {
    id: 19,
    status: 'approved',
    decision_type: 'advance_to_interview',
    candidate_name: 'Priya Raman',
    application_id: 1003,
    role_id: 109,
    resolved_at: new Date(_NOW - 18 * 60 * 1000).toISOString(),
  },
  {
    id: 18,
    status: 'overridden',
    decision_type: 'reject',
    candidate_name: 'Jonas Weber',
    application_id: 994,
    role_id: 109,
    human_disposition: 'taught',
    resolution_note: 'override → advance',
    resolved_at: new Date(_NOW - 52 * 60 * 1000).toISOString(),
  },
];

const REPORT_BARS = [
  { label: 'Coding ability', score: 88 },
  { label: 'Working with AI', score: 84 },
  { label: 'Problem solving', score: 86 },
  { label: 'Independence', score: 81 },
  { label: 'Communication', score: 74 },
];

const Section = ({ id, eyebrow, title, children }) => (
  <section id={id} className="deck-section">
    <header className="deck-section-head">
      <div className="deck-section-eyebrow">{eyebrow}</div>
      <h2 className="deck-section-title">{title}</h2>
    </header>
    {children}
  </section>
);

const BrowserFrame = ({ url, children }) => (
  <div className="deck-frame">
    <div className="deck-frame-chrome">
      <span className="h-[9px] w-[9px] rounded-full" style={{ background: '#f06' }} />
      <span className="h-[9px] w-[9px] rounded-full" style={{ background: '#ffb020' }} />
      <span className="h-[9px] w-[9px] rounded-full" style={{ background: '#39c66d' }} />
      <span className="ml-3 font-[var(--font-mono)] text-[11px] text-[var(--mute)]">{url}</span>
      <span className="ml-auto rounded-full bg-[color:var(--bg)] px-2 py-0.5 text-[10px] font-semibold text-[var(--mute)]">Locked preview</span>
    </div>
    {children}
  </div>
);

export const InvestmentDeckPage = () => (
  <div className="deck-page">
    <header className="deck-header">
      <div className="deck-header-eyebrow">DECK ASSETS · INTERNAL</div>
      <h1 className="deck-header-title">
        Taali — investor deck visuals<span className="text-[var(--purple)]">.</span>
      </h1>
      <p className="deck-header-sub">
        Each section below is a stand-alone visual. Screenshot what you need; nothing is linked from the public site.
      </p>
      <nav className="deck-header-toc">
        <a href="#pipeline">1 · Pipeline</a>
        <a href="#decision-feed">2 · Decision feed</a>
        <a href="#report">3 · Candidate report</a>
        <a href="#ide">4 · IDE</a>
      </nav>
    </header>

    <Section
      id="pipeline"
      eyebrow="01 · TAALI ON EVERY STAGE OF YOUR PIPELINE"
      title={<>The agent automates the loop. <em className="not-italic text-[var(--purple)]">You stay in charge of the calls that matter.</em></>}
    >
      <div className="taali-pipeline mt-8">
        {PIPELINE_STAGES.map((stage, idx, arr) => (
          <div key={stage.n} className="taali-pipeline-stage">
            <div className="taali-pipeline-step">
              <span className="taali-pipeline-step-n">{stage.n}</span>
              <span className="taali-pipeline-step-name">{stage.stage}</span>
              {idx < arr.length - 1 ? <span className="taali-pipeline-arrow" aria-hidden="true">→</span> : null}
            </div>
            <ul className="taali-pipeline-cards">
              {stage.cards.map((card) => (
                <li key={card.t} className="taali-pipeline-card">
                  <div className="taali-pipeline-card-title">{card.t}</div>
                  <div className="taali-pipeline-card-desc">{card.d}</div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Section>

    <Section
      id="decision-feed"
      eyebrow="02 · WHAT THE AGENT JUST DID"
      title={<>Every recommendation is one click <em className="not-italic text-[var(--purple)]">approve / override / teach</em>.</>}
    >
      <BrowserFrame url="app.taali.ai/home">
        <div className="px-5 py-5">
          <ActivityFeed
            rows={DECISION_FEED_ROWS}
            selectedId={null}
            onSelect={() => {}}
            onNavigate={() => {}}
            subtitle="Every recommendation the agent has made for this role today. Approve, override, or teach it in one click."
          />
        </div>
      </BrowserFrame>
    </Section>

    <Section
      id="report"
      eyebrow="03 · THE CANDIDATE REPORT"
      title={<>One scorecard per candidate. <em className="not-italic text-[var(--purple)]">Plain-English bars, not radar charts.</em></>}
    >
      <div className="overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] shadow-[0_24px_60px_-30px_rgba(91,44,168,0.4)]">
        <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3 font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">
          <span>MAYA CHEN · CANDIDATE REPORT</span>
          <span className="font-semibold text-[var(--purple)]">Strong overall fit</span>
        </div>
        <div className="space-y-4 px-5 py-6">
          {REPORT_BARS.map(({ label, score }) => (
            <div key={label} className="grid grid-cols-[200px_minmax(0,1fr)] items-center gap-3">
              <div className="text-[14px] text-[var(--ink)]">{label}</div>
              <div className="h-2 overflow-hidden rounded-full bg-[var(--line)]">
                <div className="h-2 rounded-full bg-[var(--purple)]" style={{ width: `${score}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </Section>

    <Section
      id="ide"
      eyebrow="04 · THE WORKSPACE"
      title={<>Real IDE, real AI in the side panel. <em className="not-italic text-[var(--purple)]">We watch how they use it.</em></>}
    >
      <p className="mb-3 text-[14px] text-[var(--ink-2)]">
        <strong className="text-[var(--ink)]">Candidates work here.</strong>{' '}
        Editor, terminal, repo, Claude Code in the panel — every keystroke captured.
      </p>
      <BrowserFrame url="app.taali.ai/assess/preview">
        <div style={{ height: 640, overflow: 'hidden', position: 'relative' }}>
          <div
            style={{
              width: '125%',
              height: 'calc(100% / 0.8)',
              transform: 'scale(0.8)',
              transformOrigin: 'top left',
            }}
          >
            <AssessmentRuntimePreviewView
              heightClass="h-full"
              lightMode={false}
              taskName="Revenue Recovery Incident"
              taskRole="Senior Backend Engineer"
              taskContext="Restore the batch revenue-recovery flow before finance close."
            />
          </div>
        </div>
      </BrowserFrame>
    </Section>
  </div>
);

export default InvestmentDeckPage;
