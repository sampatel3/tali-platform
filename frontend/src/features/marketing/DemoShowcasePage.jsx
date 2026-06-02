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
    k: 'agent',
    n: '01',
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
    n: '02',
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
    n: '03',
    label: 'Standing report',
    sub: 'Recruiter-readable, evidence-linked',
    src: `/c/demo?view=client&k=${REPORT_SHOWCASE_TOKEN}&showcase=1`,
    urlLabel: 'taali.ai/c/demo · candidate standing report',
    why: {
      headline: 'The standing report your hiring manager opens.',
      outcomes: [
        'Six evidence-linked axes: systems design, code craft, reasoning under pressure, AI collaboration, release safety, communication',
        'Every score links back to the moment in the session it came from',
        'Shareable link, expiring, no PDFs, no leaks',
      ],
    },
  },
  {
    k: 'chat',
    n: '04',
    label: 'Plain-English search',
    sub: 'Query your pipeline in english',
    src: '/showcase/chat',
    urlLabel: 'taali.ai/chat · plain-English candidate search',
    why: {
      headline: 'Ask questions of your pipeline. No boolean strings.',
      outcomes: [
        '"Top backend candidates above 8 on AI prompting" → 3 results in 0.4s',
        'Tool calls visible — see exactly what the agent queried',
        'Compare candidates side-by-side, pull shortlists for new briefs',
      ],
    },
  },
  {
    k: 'workflow',
    n: '05',
    label: 'Hub · the agent narrator',
    sub: 'Every decision the agent makes that needs you',
    src: '/showcase/home',
    urlLabel: 'taali.ai/home · agent narrator',
    why: {
      headline: 'The Hub is where you keep the agent honest.',
      outcomes: [
        'Decision feed: advanced, escalated, pre-screen rejected — all reviewable, all reversible',
        'Pending sidebar surfaces where the agent paused for your judgment',
        'Approve, override, or teach — your calls become its training signal',
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
  const [active, setActive] = useState('agent');
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
            Your hiring funnel,<br />now with an <em>agent</em><br />at the top of it.
          </h1>
          <p className="mc-show-hero-sub">
            The agent decides what to work on each cycle — fetch CVs, score, send assessments,
            queue advances or rejects — paces it within the budget you set, and pauses to ask
            you when it can&apos;t decide on its own. Every consequential call still goes through
            you. Every tab below is a <b>real product surface</b>, not a mock.
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
