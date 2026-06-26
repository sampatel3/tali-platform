import React, { useCallback, useRef, useState } from 'react';

import { TaaliTile } from '../../shared/ui/Branding';
import { PageLink } from '../../shared/ui/PageLink';

// DemoShowcasePage — v4 redesign (HANDOFF chat.md §1).
// Sells AI-first via 5 tabs that each embed a REAL product page in an
// iframe — no static mocks. The same showcase routes the existing
// `/demo-walkthrough` uses are reused here so the demo stays grounded
// in the actual product.
//
// Iframe-load guard mirrors DemoExperiencePage: if the embedded page
// navigates somewhere unexpected we reset the src once. The sandbox
// keeps the blast radius contained.

const REPORT_SHOWCASE_TOKEN = 'demo-token';

const SHOWCASE_TABS = [
  {
    k: 'workflow',
    n: '01',
    label: 'The Hub',
    sub: 'Steer the agent, keep it honest',
    src: '/showcase/home',
    urlLabel: 'taali.ai/home · your hiring Hub',
    why: {
      headline: 'Mission control: steer the agent in plain English, keep every call honest.',
      outcomes: [
        'Chat to each role’s agent in plain English — “cap salary at AED 25k”, “drop the cut-off to 65” — it reasons, shows the impact, and asks before it spends',
        'Decision feed: every advance, escalate, and pre-screen reject — reviewable, reversible, and your overrides become its training signal',
        'One rail for every agent: pending counts, budgets, bulk-message many roles at once, and approve the assessment tasks it drafts',
      ],
    },
  },
  {
    k: 'agent',
    n: '02',
    label: 'Agentic triage',
    sub: 'The agent that runs your top of funnel',
    src: '/jobs?demo=1&showcase=1',
    urlLabel: 'taali.ai/jobs · agent on duty',
    why: {
      headline: 'See the real Jobs board the agent works against.',
      outcomes: [
        'Live agent bar shows what it advanced, escalated, or pre-screen rejected in the last hour',
        'Each role has its own budget, autonomy dial, and decisions feed',
        'Click into a role to see the pipeline the way your team will every morning',
      ],
    },
  },
  {
    k: 'assessment',
    n: '03',
    label: 'AI assessment',
    sub: 'See how candidates pair with AI',
    src: '/assessment/live?demo=1&showcase=1',
    urlLabel: 'taali.ai/assess · candidate workspace',
    why: {
      headline: 'Step into the chat-first workspace the candidate drives.',
      outcomes: [
        'Claude at the centre, a real editor + sandboxed runtime alongside — the agent is the work',
        'Every prompt, paste, and edit captured for replay',
        'No proctoring overlay — the transcript is the record',
      ],
    },
  },
  {
    k: 'scoring',
    n: '04',
    label: 'Standing report',
    sub: 'Recruiter-readable, evidence-linked',
    src: `/c/demo?view=client&k=${REPORT_SHOWCASE_TOKEN}&showcase=1`,
    urlLabel: 'taali.ai/c/demo · candidate standing report',
    why: {
      headline: 'The standing report your hiring manager opens.',
      outcomes: [
        'Five evidence-linked axes — the 4 Ds + Deliverable: Delegation, Description, Discernment, Diligence, Deliverable',
        'Every score links back to the moment in the session it came from',
        'Shareable link, expiring, no PDFs, no leaks',
      ],
    },
  },
  {
    k: 'chat',
    n: '05',
    label: 'Chat & agents',
    sub: 'Ask anything, or steer each role’s agent',
    src: '/showcase/chat',
    urlLabel: 'taali.ai/chat · ask + per-role agents',
    why: {
      headline: 'Two ways to talk to your pipeline: ask it anything, or steer a role’s agent.',
      outcomes: [
        '“Top 3 who’ve owned a GenAI launch, run Postgres in prod, and led a backend team” → a grounded shortlist, every verdict backed by a verbatim CV quote',
        'Ask mode searches your whole pipeline; tool calls are visible — see exactly what was queried, compare side-by-side, pull shortlists',
        'Agent mode is per-role: it finds, reasons over the evidence, and acts — invite the matches to assessment, adjust the cut-off, draft the task — within the budget you set',
      ],
    },
  },
];

const useFrameLoadGuard = () => {
  const counts = useRef(new Map());

  return useCallback((tab) => (event) => {
    const frame = event.currentTarget;
    if (typeof window === 'undefined') return;

    let frameHref;
    try {
      frameHref = frame.contentWindow?.location?.href;
    } catch {
      return;
    }
    if (!frameHref) return;

    let frameUrl;
    try {
      frameUrl = new URL(frameHref, window.location.origin);
    } catch {
      return;
    }

    const intendedUrl = new URL(tab.src, window.location.origin);
    const sameRoute = frameUrl.pathname === intendedUrl.pathname;
    let allowed = sameRoute;

    if (tab.k === 'chat' || tab.k === 'workflow') {
      // /showcase/* routes are public-by-design — no query params required.
      allowed = sameRoute;
    } else if (tab.k === 'scoring') {
      allowed = sameRoute
        && frameUrl.searchParams.get('view') === 'client'
        && frameUrl.searchParams.get('k') === REPORT_SHOWCASE_TOKEN
        && frameUrl.searchParams.get('showcase') === '1';
    } else {
      allowed = sameRoute
        && frameUrl.searchParams.get('demo') === '1'
        && frameUrl.searchParams.get('showcase') === '1';
    }

    if (allowed) return;

    const used = counts.current.get(tab.k) || 0;
    if (used >= 1) return;
    counts.current.set(tab.k, used + 1);
    frame.src = tab.src;
  }, []);
};

export const DemoShowcasePage = ({ onNavigate }) => {
  const [active, setActive] = useState('workflow');
  const guard = useFrameLoadGuard();
  const tab = SHOWCASE_TABS.find((t) => t.k === active) || SHOWCASE_TABS[0];
  const idx = SHOWCASE_TABS.findIndex((t) => t.k === active);
  const next = SHOWCASE_TABS[idx + 1];
  const prev = SHOWCASE_TABS[idx - 1];

  return (
    <div className="mc-show">
      {/* TOP BAR */}
      <div className="mc-show-topbar">
        <PageLink
          page="landing"
          className="mc-show-logo"
          aria-label="Taali home"
        >
          <TaaliTile
            className="h-7 w-7 rounded-[7px]"
            fillClassName="text-[var(--purple)]"
            lineClassName="text-white"
            strokeWidth={2.4}
            cornerRadius={6.5}
          />
          <span>taali<em>.</em></span>
        </PageLink>
        <span className="mc-show-topbar-meta">· LIVE WALKTHROUGH · ACME / SR. BACKEND</span>
        <span className="mc-show-spacer" />
        <span className="mc-show-chip green">Demo data · resets daily</span>
        <PageLink page="landing" className="mc-show-btn">Skip the tour</PageLink>
        <PageLink page="demo-lead" className="mc-show-btn primary">Start with free credits →</PageLink>
      </div>

      {/* HERO */}
      <section className="mc-show-section">
        <div className="mc-show-kicker mc-show-mb-14">// THE WALKTHROUGH · 5 SURFACES · ~ 6 MIN</div>
        <div className="mc-show-hero">
          <h1 className="mc-show-hero-title">
            Your hiring <em>Hub</em> —<br />one agent per role,<br />and you in command.
          </h1>
          <p className="mc-show-hero-sub">
            Every role gets an agent that screens, scores, sends assessments, and queues advances
            or rejects within the budget you set. You run it all from the <b>Hub</b>: steer each
            agent in plain English, review every consequential call, and teach it when you override.
            Every tab below is a <b>real product surface</b>, not a mock — starting with the Hub.
          </p>
        </div>
      </section>

      {/* TAB STRIP */}
      <section className="mc-show-section mc-show-tabs-wrap">
        <div className="mc-show-tabs" role="tablist" aria-label="Walkthrough sections">
          {SHOWCASE_TABS.map((t) => {
            const on = t.k === active;
            return (
              <button
                key={t.k}
                type="button"
                role="tab"
                aria-selected={on}
                className={`mc-show-tab ${on ? 'on' : ''}`.trim()}
                onClick={() => setActive(t.k)}
              >
                <div className={`mc-show-tab-num ${on ? 'on' : ''}`.trim()}>{t.n}</div>
                <div className={`mc-show-tab-l ${on ? 'on' : ''}`.trim()}>{t.label}</div>
                <div className="mc-show-tab-s">{t.sub}</div>
              </button>
            );
          })}
        </div>
      </section>

      {/* ACTIVE PANEL — real product page in an iframe */}
      <section className="mc-show-section mc-show-panel">
        <div className="mc-show-why">
          <div className="mc-show-why-head">
            <span className="mc-show-why-eyebrow">Why this matters to you</span>
            <span className="mc-show-why-headline">{tab.why.headline}</span>
          </div>
          <ul className="mc-show-why-list">
            {tab.why.outcomes.map((outcome) => (
              <li key={outcome}>{outcome}</li>
            ))}
          </ul>
        </div>

        {/* One iframe per tab kept in the DOM so re-clicking doesn't reload the page. */}
        {SHOWCASE_TABS.map((t) => (
          <div
            key={t.k}
            className="mc-show-frame"
            data-tab={t.k}
            hidden={t.k !== active}
          >
            <div className="mc-show-frame-chrome">
              <span className="mc-show-frame-dots" aria-hidden="true">
                <i /><i /><i />
              </span>
              <span className="mc-show-frame-url">
                <span className="mc-show-frame-lock">●</span>
                {t.urlLabel}
              </span>
              <span className="mc-show-frame-badge">Locked preview</span>
            </div>
            <div className="mc-show-frame-stage">
              <iframe
                title={t.label}
                src={t.src}
                sandbox="allow-scripts allow-same-origin"
                referrerPolicy="no-referrer"
                onLoad={guard(t)}
              />
              <div className="mc-show-frame-tip">
                <span className="dot" /> {t.sub}
              </div>
            </div>
          </div>
        ))}

        <div className="mc-show-pager">
          <button
            type="button"
            className="mc-show-btn"
            disabled={!prev}
            onClick={() => prev && setActive(prev.k)}
          >
            <span>←</span> Previous
          </button>
          <div className="mc-show-pager-count">{idx + 1} / {SHOWCASE_TABS.length}</div>
          {next ? (
            <button type="button" className="mc-show-btn primary" onClick={() => setActive(next.k)}>
              Next: {next.label} <span>→</span>
            </button>
          ) : (
            <PageLink page="demo-lead" className="mc-show-btn primary">
              Book a demo <span>→</span>
            </PageLink>
          )}
        </div>

        <div className="mc-show-cta">
          <div>
            <div className="mc-show-kicker mc-show-mb-10">READY TO PUT IT TO WORK?</div>
            <h2 className="mc-show-cta-h">
              Wire your <em>real pipeline</em> into Taali. Start with $1.50 in free credits.
            </h2>
            <p className="mc-show-cta-sub">
              Connect Workable, point the agent at one role, and watch it triage your next batch of CVs by morning. Usage-based — like Anthropic, OpenAI, Cursor. No card, no subscription, no monthly minimum.
            </p>
          </div>
          <div className="mc-show-cta-side">
            <PageLink page="demo-lead" className="mc-show-btn primary tall">
              Start with free credits →
            </PageLink>
            <div className="mc-show-cta-foot">SOC 2 · GDPR · NEVER USED FOR TRAINING</div>
          </div>
        </div>
      </section>
    </div>
  );
};

export default DemoShowcasePage;
