// Internal investment-deck canvas — Sam's pitch-deck source page.
//
// Not linked from anywhere. Direct URL only: /deck.
//
// Sections (each is a stand-alone visual you can screenshot for a
// slide):
//
//   1 · Cover            (logo + tagline + positioning)
//   2 · Problem          (3 pain points)
//   3 · Solution         (the agent + AI-native assessment, in one frame)
//   4 · Why now          (3 converging tailwinds)
//   5 · Market           (UAE → GCC → global, with sourced numbers)
//   6 · Pipeline         (the 6-stage diagram with Taali cards under each)
//   7 · Agent            (orchestrator → sub-agents → policy → human gate)
//   8 · Decision feed    (live ActivityFeed product component)
//   9 · Candidate report (5 simple bars)
//  10 · IDE              (live AssessmentRuntimePreviewView)
//  11 · Competitive      (2×2 — agentic × AI-native)
//  12 · Business model   (usage-based pricing recap)
//  13 · Traction         (placeholder — Sam fills)
//  14 · Roadmap          (3 quarters)
//  15 · Team / Ask       (placeholder — Sam fills)

import React from 'react';

import { ActivityFeed } from '../home/ActivityFeed';
import { AssessmentRuntimePreviewView } from '../assessment_runtime/AssessmentRuntimePreviewView';

import './deck.css';

const _NOW = Date.now();

// ---------------------------------------------------------------------------
// Pipeline data — same shape as the marketing version. Fraud detection now
// also lives under PRE-SCREEN (alongside the existing card under ASSESS).
// ---------------------------------------------------------------------------

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
      { t: 'AI-CV fraud detection', d: '78% of applications now contain AI-generated content. Taali flags ChatGPT-pattern CVs, identity mismatches, and embellishments.' },
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
      { t: 'Live fraud detection', d: 'Autopilot detection flags pasted-without-reading, copy-from-elsewhere, proxy-candidate signals.' },
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

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

const Section = ({ id, eyebrow, title, kicker, children }) => (
  <section id={id} className="deck-section">
    <header className="deck-section-head">
      {kicker ? <div className="deck-section-kicker">{kicker}</div> : null}
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

const Stat = ({ value, label, source }) => (
  <div className="deck-stat">
    <div className="deck-stat-value">{value}</div>
    <div className="deck-stat-label">{label}</div>
    {source ? <div className="deck-stat-source">{source}</div> : null}
  </div>
);

const Card = ({ kicker, title, body }) => (
  <div className="deck-card">
    {kicker ? <div className="deck-card-kicker">{kicker}</div> : null}
    <div className="deck-card-title">{title}</div>
    <div className="deck-card-body">{body}</div>
  </div>
);

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export const InvestmentDeckPage = () => (
  <div className="deck-page">
    {/* 1 · Cover ============================================================ */}
    <header id="cover" className="deck-cover">
      <div className="deck-cover-mark">taali<span className="text-[var(--purple)]">.</span></div>
      <div className="deck-cover-eyebrow">INVESTOR DECK · 2026 · UAE / MENA</div>
      <h1 className="deck-cover-title">
        The recruiter&apos;s <em className="not-italic text-[var(--purple)]">agent</em>.<br />
        Built to hire engineers who ship with AI<span className="text-[var(--purple)]">.</span>
      </h1>
      <p className="deck-cover-sub">
        Taali is the first agentic hiring platform — and the only one that measures how candidates actually <em className="not-italic font-semibold text-[var(--ink)]">use AI</em> on the job. Built in the UAE for the AI-native era.
      </p>
      <div className="deck-cover-badges">
        {[
          { k: 'AGENTIC', v: 'Runs your pipeline 24/7 — pauses for your judgment' },
          { k: 'AI-NATIVE', v: 'Scores AI fluency in hands-on tasks' },
          { k: 'UAE-NATIVE', v: 'Built for Emiratisation + AI Strategy 2031' },
        ].map((b) => (
          <div key={b.k} className="deck-cover-badge">
            <span className="font-[var(--font-mono)] text-[10.5px] font-semibold tracking-[0.08em] text-[var(--purple)]">{b.k}</span>
            <span>{b.v}</span>
          </div>
        ))}
      </div>
      <nav className="deck-cover-toc">
        {[
          ['problem', 'Problem'],
          ['solution', 'Solution'],
          ['why-now', 'Why now'],
          ['market', 'Market'],
          ['pipeline', 'Pipeline'],
          ['agent', 'The agent'],
          ['feed', 'Decision feed'],
          ['report', 'Report'],
          ['ide', 'IDE'],
          ['competitive', 'Competitive'],
          ['model', 'Business model'],
          ['traction', 'Traction'],
          ['roadmap', 'Roadmap'],
          ['ask', 'Team & ask'],
        ].map(([id, label]) => (
          <a key={id} href={`#${id}`}>{label}</a>
        ))}
      </nav>
    </header>

    {/* 2 · Problem ========================================================= */}
    <Section
      id="problem"
      eyebrow="02 · PROBLEM"
      title={<>Hiring engineers in 2026 is <em className="not-italic text-[var(--purple)]">broken in three ways</em>.</>}
    >
      <div className="deck-problem-grid">
        <Card
          kicker="THE FLOOD"
          title="78% of CVs now contain AI-generated content."
          body="ChatGPT averages 14 embellishments per CV. 59% of hiring managers suspect candidates of misrepresenting themselves with AI. Resume fraud already costs employers $600B/year — Gartner predicts 1 in 4 candidates will be fraudulent by 2028."
        />
        <Card
          kicker="THE BLIND SPOT"
          title="Nobody knows if candidates can actually use AI."
          body="Engineering work is done with Claude, Cursor, Copilot. Legacy assessments measure leetcode in a sterile sandbox. Karat reports a 5× increase in cheating detection across 500,000+ technical interviews; one tech leader saw 80% of candidates use an LLM despite explicit prohibition."
        />
        <Card
          kicker="THE UAE PRESSURE"
          title="Emiratisation deadlines are non-negotiable."
          body="Every UAE company with 50+ staff must hit 10% Emirati skilled hires by end of 2026 or pay AED 6,000/month per missing hire. Plus the National AI Strategy needs 10,000 new AI/ML engineers by 2031. Recruiters are squeezed on volume, quality, and compliance simultaneously."
        />
      </div>
    </Section>

    {/* 3 · Solution ======================================================== */}
    <Section
      id="solution"
      eyebrow="03 · SOLUTION"
      title={<>One agent runs the loop. <em className="not-italic text-[var(--purple)]">You decide what matters.</em></>}
    >
      <div className="deck-solution">
        <p className="deck-solution-lede">
          Taali is an autonomous agent that surveys your role every cycle, decides where the work is — fetch CVs, pre-screen with fraud detection, score, send assessments, queue advances or rejects — and pauses to ask you when it can&apos;t decide on its own. Every assessment opens a real in-browser IDE with Claude / Cursor / Copilot in the candidate&apos;s hand; we capture every prompt, paste, edit and test run, then score AI fluency as a first-class dimension. Every recommendation is rule-traceable. Every consequential call still goes through you.
        </p>
        <div className="deck-solution-pillars">
          <Card
            kicker="PILLAR 1"
            title="Agentic"
            body="Single LLM orchestrator + deterministic decision policy. Surveys cohort state every 30 min, batches the cheap deterministic work, queues high-stakes decisions for human approval, asks you when input is genuinely missing. Never silently spends budget."
          />
          <Card
            kicker="PILLAR 2"
            title="AI-native assessment"
            body="Real IDE, real AI in the side panel. 8-axis rubric: coding ability, working with AI, problem solving, independence, communication. Live fraud + autopilot detection flags pasted-without-reading and proxy-candidate signals."
          />
          <Card
            kicker="PILLAR 3"
            title="Auditable"
            body="DecisionPolicy is pure-Python, pinned to a versioned RubricRevision. Every queued decision carries policy_revision_id + rule_path. Override once → nightly retune updates the policy. No black box."
          />
        </div>
      </div>
    </Section>

    {/* 4 · Why now ========================================================= */}
    <Section
      id="why-now"
      eyebrow="04 · WHY NOW"
      title={<>Three tailwinds <em className="not-italic text-[var(--purple)]">converging in 2026</em>.</>}
    >
      <div className="deck-whynow">
        {[
          {
            year: '2024',
            title: 'AI in every dev workflow',
            body: 'Claude Code, Cursor, Copilot are the way engineers ship code. Hiring assessments that pretend AI doesn\'t exist measure the wrong skill.',
          },
          {
            year: '2025',
            title: 'AI-CV flood + fraud crisis',
            body: '78% of applications contain AI-generated content. 1 in 3 hiring managers caught a fake identity / proxy candidate. Recruiter trust is breaking down.',
          },
          {
            year: '2026',
            title: 'UAE Emiratisation + AI Strategy 2031',
            body: '10% Emirati hires by end-2026 deadline. 10,000 AI/ML engineers needed by 2031. Local-first hiring tooling has policy tailwind.',
          },
        ].map((t) => (
          <div key={t.year} className="deck-whynow-row">
            <div className="deck-whynow-year">{t.year}</div>
            <div className="deck-whynow-line" aria-hidden="true" />
            <div className="deck-whynow-body">
              <div className="deck-whynow-title">{t.title}</div>
              <div className="deck-whynow-text">{t.body}</div>
            </div>
          </div>
        ))}
      </div>
      <p className="deck-callout">
        <strong>The window:</strong> the next 18 months. Incumbents (HackerRank, Greenhouse, Workable) are bolting AI onto legacy assessment + ATS surfaces. Nobody has built around the agent + AI-native assessment from day one. Taali is positioned to own that wedge in UAE / GCC and expand from there.
      </p>
    </Section>

    {/* 5 · Market ========================================================== */}
    <Section
      id="market"
      eyebrow="05 · MARKET"
      title={<>UAE-native wedge into a <em className="not-italic text-[var(--purple)]">$4.5B global recruiting-software market</em>.</>}
    >
      <div className="deck-market">
        <div className="deck-market-rings">
          {[
            { ring: 'TAM', value: '$4.5B', label: 'Global recruiting software (2028 forecast)' },
            { ring: 'SAM', value: '$1.2B', label: 'AI-native assessment + ATS in MENA + EMEA mid-market' },
            { ring: 'SOM', value: 'AED 1.12B', label: 'UAE recruitment software (2026 — direct wedge)' },
          ].map((r) => (
            <div key={r.ring} className="deck-market-ring">
              <div className="deck-market-ring-label">{r.ring}</div>
              <div className="deck-market-ring-value">{r.value}</div>
              <div className="deck-market-ring-text">{r.label}</div>
            </div>
          ))}
        </div>
        <div className="deck-market-stats">
          <Stat value="AED 880M → 1.12B" label="UAE recruitment software, 2025 → 2026" source="Cognitive Market Research / Connect Staff" />
          <Stat value="10% by Dec 2026" label="Emiratisation skilled-hire mandate (50+ staff orgs)" source="UAE Government / Khaleej Times" />
          <Stat value="10,000" label="AI/ML engineers UAE plans to train by 2031" source="UAE National AI Strategy 2031" />
          <Stat value="$600B / yr" label="Global cost of resume fraud, growing" source="Crosschq" />
          <Stat value="78%" label="Of CVs in 2026 contain AI-generated content" source="JobCannon, 72-stat study" />
          <Stat value="172,800 / day" label="Assessments processed by HackerRank — proves the demand surface" source="HackerRank" />
        </div>
      </div>
    </Section>

    {/* 6 · Pipeline ======================================================== */}
    <Section
      id="pipeline"
      eyebrow="06 · PRODUCT — PIPELINE"
      title={<>Taali on every stage of your pipeline. <em className="not-italic text-[var(--purple)]">You stay in charge of the calls that matter.</em></>}
    >
      <div className="taali-pipeline mt-2">
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

    {/* 7 · Agent architecture ============================================== */}
    <Section
      id="agent"
      eyebrow="07 · PRODUCT — THE AGENT"
      title={<>Single LLM orchestrator, five specialist sub-agents, <em className="not-italic text-[var(--purple)]">deterministic verdict layer</em>.</>}
    >
      <div className="deck-agent-flow">
        {/* Orchestrator */}
        <div className="deck-agent-node deck-agent-node-orchestrator">
          <div className="deck-agent-node-kicker">ORCHESTRATOR</div>
          <div className="deck-agent-node-title">Claude Haiku · single planner</div>
          <div className="deck-agent-node-body">Wakes every 30 min per active role, surveys cohort state, decides where to spend the cycle.</div>
        </div>

        <div className="deck-agent-arrow" aria-hidden="true">↓ delegates to ↓</div>

        {/* Sub-agents */}
        <div className="deck-agent-subgrid">
          {[
            { t: 'pre_screen', d: 'Yes/no/maybe filter' },
            { t: 'cv_scoring', d: '8-axis CV ↔ JD match' },
            { t: 'assessment_scoring', d: 'Telemetry → fluency' },
            { t: 'intent_parser', d: 'Recruiter intent → directives' },
            { t: 'graph_priors', d: 'Network-based priors' },
          ].map((a) => (
            <div key={a.t} className="deck-agent-subnode">
              <div className="deck-agent-subnode-name">{a.t}</div>
              <div className="deck-agent-subnode-desc">{a.d}</div>
            </div>
          ))}
        </div>

        <div className="deck-agent-arrow" aria-hidden="true">↓ feeds ↓</div>

        {/* Decision policy */}
        <div className="deck-agent-node deck-agent-node-policy">
          <div className="deck-agent-node-kicker">DECISION POLICY · DETERMINISTIC</div>
          <div className="deck-agent-node-title">Pure-Python verdict, pinned to a versioned RubricRevision</div>
          <div className="deck-agent-node-body">No LLM in the verdict path. Every recommendation traces to a named rule + revision id. Recruiter overrides feed the nightly retune that updates the policy.</div>
        </div>

        <div className="deck-agent-arrow" aria-hidden="true">↓ surfaces to ↓</div>

        {/* Human gate */}
        <div className="deck-agent-node deck-agent-node-human">
          <div className="deck-agent-node-kicker">HUMAN GATE</div>
          <div className="deck-agent-node-title">Approve · Override · Teach · Answer</div>
          <div className="deck-agent-node-body">High-stakes calls (advance, reject) always queue. The agent asks you when it can&apos;t decide alone. Your overrides + teach actions become the next policy revision overnight.</div>
        </div>
      </div>
    </Section>

    {/* 8 · Decision feed =================================================== */}
    <Section
      id="feed"
      eyebrow="08 · PRODUCT — DECISION FEED"
      title={<>Every recommendation lands here. <em className="not-italic text-[var(--purple)]">One click to approve, override or teach.</em></>}
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

    {/* 9 · Candidate report ================================================ */}
    <Section
      id="report"
      eyebrow="09 · PRODUCT — CANDIDATE REPORT"
      title={<>One scorecard per candidate. <em className="not-italic text-[var(--purple)]">Five plain-English bars.</em></>}
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

    {/* 10 · IDE ============================================================ */}
    <Section
      id="ide"
      eyebrow="10 · PRODUCT — THE WORKSPACE"
      title={<>Real IDE, real AI in the side panel. <em className="not-italic text-[var(--purple)]">We watch how they use it.</em></>}
    >
      <p className="mb-3 text-[14px] text-[var(--ink-2)]">
        <strong className="text-[var(--ink)]">Candidates work here.</strong>{' '}
        Editor, terminal, repo, Claude Code in the panel — every keystroke, prompt, paste, edit, test run captured time-stamped to the second.
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

    {/* 11 · Competitive ==================================================== */}
    <Section
      id="competitive"
      eyebrow="11 · COMPETITIVE LANDSCAPE"
      title={<>Incumbents are bolting AI on. <em className="not-italic text-[var(--purple)]">Taali is built around it.</em></>}
    >
      <div className="deck-quad">
        <div className="deck-quad-axis-y">↑ AI-NATIVE ASSESSMENT</div>
        <div className="deck-quad-axis-x">AGENTIC →</div>

        {/* Plot points */}
        <div className="deck-quad-grid">
          <div className="deck-quad-cell deck-quad-cell-tl">
            <div className="deck-quad-tag">Sandboxed AI assessment</div>
            <div className="deck-quad-cos">CoderPad · CodeSignal · Karat</div>
          </div>
          <div className="deck-quad-cell deck-quad-cell-tr">
            <div className="deck-quad-tag deck-quad-tag-us">TAALI</div>
            <div className="deck-quad-cos">Agentic + AI-native + auditable</div>
          </div>
          <div className="deck-quad-cell deck-quad-cell-bl">
            <div className="deck-quad-tag">Legacy assessment</div>
            <div className="deck-quad-cos">HackerRank · Codility · TestGorilla</div>
          </div>
          <div className="deck-quad-cell deck-quad-cell-br">
            <div className="deck-quad-tag">ATS + automation</div>
            <div className="deck-quad-cos">Workable · Greenhouse · Lever · Ashby</div>
          </div>
        </div>
      </div>

      <table className="deck-compare">
        <thead>
          <tr>
            <th></th>
            <th>ATS<br /><small>Workable / Greenhouse</small></th>
            <th>Assessment<br /><small>HackerRank / Codility</small></th>
            <th className="deck-compare-us">Taali</th>
          </tr>
        </thead>
        <tbody>
          {[
            ['Agent decides what to do this cycle', false, false, true],
            ['Measures AI fluency on a hands-on task', false, false, true],
            ['Live in-browser IDE with Claude / Cursor / Copilot', false, false, true],
            ['Fraud + AI-CV detection', 'partial', 'partial', true],
            ['Deterministic, rule-traced verdict', false, false, true],
            ['Recruiter override → policy retune overnight', false, false, true],
            ['UAE-native (Emiratisation, MoHRE alignment)', 'partial', false, true],
          ].map(([feature, ats, asm, us]) => (
            <tr key={feature}>
              <td>{feature}</td>
              <td>{cellMark(ats)}</td>
              <td>{cellMark(asm)}</td>
              <td className="deck-compare-us">{cellMark(us)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>

    {/* 12 · Business model ================================================== */}
    <Section
      id="model"
      eyebrow="12 · BUSINESS MODEL"
      title={<>Usage-based. <em className="not-italic text-[var(--purple)]">Pay only for what the agent actually does.</em></>}
    >
      <p className="deck-paragraph">
        Same shape as Anthropic, OpenAI, Cursor: customers buy credit packs, the agent draws down per Claude call. No subscriptions, no minimums, no procurement cycle. New orgs get $1.50 of free credits to try the full platform on one role.
      </p>
      <div className="deck-pricing-grid">
        {[
          { label: 'Free trial', price: '$1.50', detail: 'on signup', body: '~100 candidates pre-screened, ~30 fully scored, 3 assessment runs. No card.' },
          { label: 'Starter', price: '$20', detail: '$20 of credits', body: '~1,300 scored candidates.' },
          { label: 'Growth', price: '$100', detail: '$110 (+10% bonus)', body: '~7,300 scored candidates.' },
          { label: 'Scale', price: '$500', detail: '$600 (+20% bonus)', body: '~40,000 scored candidates.' },
        ].map((p) => (
          <div key={p.label} className="deck-pricing-card">
            <div className="deck-pricing-card-label">{p.label}</div>
            <div className="deck-pricing-card-price">{p.price}</div>
            <div className="deck-pricing-card-detail">{p.detail}</div>
            <div className="deck-pricing-card-body">{p.body}</div>
          </div>
        ))}
      </div>
      <div className="deck-callout">
        <strong>Margin model.</strong> Pre-screen at cost (1×). CV scoring + assessment workspace at 3× Anthropic token cost — covers infra, support, retune compute, R&D. Every Claude call itemised in the recruiter&apos;s billing tab.
      </div>
    </Section>

    {/* 13 · Traction ======================================================= */}
    <Section
      id="traction"
      eyebrow="13 · TRACTION"
      title={<>Live with our first design partner. <em className="not-italic text-[var(--purple)]">More to fill in.</em></>}
    >
      <div className="deck-traction-grid">
        <Stat value="DeepLight" label="First design partner — UAE engineering hires" />
        <Stat value="Phase 7" label="Cohort-planner agent shipped (Apr 2026)" />
        <Stat value="8-axis rubric" label="Live AI-fluency scoring on every assessment" />
        <Stat value="115" label="Backend tests on the agent surface · 100% pass" />
        <Stat value="<<< placeholder >>>" label="Candidates processed (Sam to fill)" />
        <Stat value="<<< placeholder >>>" label="Roles live (Sam to fill)" />
        <Stat value="<<< placeholder >>>" label="Letters of intent (Sam to fill)" />
        <Stat value="<<< placeholder >>>" label="MRR / pipeline (Sam to fill)" />
      </div>
    </Section>

    {/* 14 · Roadmap ======================================================== */}
    <Section
      id="roadmap"
      eyebrow="14 · ROADMAP"
      title={<>Three quarters. <em className="not-italic text-[var(--purple)]">Wedge first, then expand.</em></>}
    >
      <div className="deck-roadmap">
        {[
          {
            q: 'Q2 · 2026',
            theme: 'UAE wedge',
            bullets: [
              'Emiratisation hiring quota tracking + reporting in-product',
              'MoHRE-aligned candidate data export',
              'Arabic + English candidate-facing copy',
              '5 paying UAE customers (target)',
            ],
          },
          {
            q: 'Q3 · 2026',
            theme: 'GCC + non-eng roles',
            bullets: [
              'KSA + Qatar localisation',
              'Sales / GTM / data-analyst assessment templates',
              'Greenhouse + Ashby ATS integrations',
              '25 paying customers across GCC',
            ],
          },
          {
            q: 'Q4 · 2026',
            theme: 'Enterprise + EMEA',
            bullets: [
              'Enterprise SSO + audit-log compliance pack',
              'EU GDPR data residency option',
              'Self-serve role library + bespoke task marketplace',
              '$1M ARR run-rate (target)',
            ],
          },
        ].map((q) => (
          <div key={q.q} className="deck-roadmap-q">
            <div className="deck-roadmap-q-head">{q.q}</div>
            <div className="deck-roadmap-q-theme">{q.theme}</div>
            <ul className="deck-roadmap-q-list">
              {q.bullets.map((b) => <li key={b}>{b}</li>)}
            </ul>
          </div>
        ))}
      </div>
    </Section>

    {/* 15 · Team & ask ===================================================== */}
    <Section
      id="ask"
      eyebrow="15 · TEAM & ASK"
      title={<>Built by founders who&apos;ve hired engineers, shipped AI products, and lived the UAE talent market.</>}
    >
      <div className="deck-team-grid">
        <Card
          kicker="FOUNDER"
          title="Sam Patel"
          body="<<< Sam fills: prior roles, what you built, why you're the person to solve this. >>>"
        />
        <Card
          kicker="ADVISORS"
          title="<<< placeholder >>>"
          body="<<< Sam fills: 2–3 advisors with credibility in UAE tech, AI, or recruiting. >>>"
        />
        <Card
          kicker="HIRING NEXT"
          title="Founding engineer · GTM lead · UAE BD"
          body="Use of funds: 40% engineering (cohort-planner Phase 8 + integrations), 35% GTM (UAE design partners → paying customers), 25% runway."
        />
      </div>
      <div className="deck-ask">
        <div className="deck-ask-eyebrow">THE ASK</div>
        <div className="deck-ask-headline">
          Raising <em className="not-italic text-[var(--purple)]">$&lt;&lt;&lt; X &gt;&gt;&gt;</em> to land 25 UAE / GCC paying customers and reach $1M ARR by end of 2026.
        </div>
        <p className="deck-ask-body">
          Lead investor with conviction in agentic + AI-native + emerging-markets distribution. Soft commits welcome; rolling close.
        </p>
      </div>
    </Section>

    <footer className="deck-footer">
      <div>taali<span className="text-[var(--purple)]">.</span> · investor deck · 2026 · internal</div>
      <div>hello@taali.ai</div>
    </footer>
  </div>
);

const cellMark = (v) => {
  if (v === true) return <span className="deck-compare-yes" aria-label="yes">●</span>;
  if (v === 'partial') return <span className="deck-compare-partial" aria-label="partial">◐</span>;
  return <span className="deck-compare-no" aria-label="no">○</span>;
};

export default InvestmentDeckPage;
