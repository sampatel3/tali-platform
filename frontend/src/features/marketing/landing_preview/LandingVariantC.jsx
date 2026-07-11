import React, { useCallback, useEffect, useRef, useState } from 'react';

import { LandingPreviewFooter } from './LandingPreviewChrome';
import { VARIANT_C_CSS } from './landingVariantC.styles';

// ---------------------------------------------------------------------------
// VARIANT C — "Turn hiring on". A cinematic, full-dark concept where the page
// itself is an agent-ON switch. It loads OFF (near-grayscale, inert) and, after
// ~1.4s (or on click / keyboard), the toggle flips ON: colour floods the page,
// motion begins, and the vision narrative starts.
//
// All colour lives on a scoped `.lvc` root as CSS custom properties, using the
// Taali purple palette directly (not the brand token) so the dark cinematic
// look holds regardless of the app's active brand/theme. The OFF→ON flood is a
// single `filter` + custom-property transition on `.lvc`, driven by one
// `data-on` attribute — every child animation keys off it.
//
// Constraints honoured: no new deps (CSS keyframes + rAF + IntersectionObserver
// only), lazy-loaded route, prefers-reduced-motion renders straight to ON with
// static composition, mobile-first, fixture data only.
// ---------------------------------------------------------------------------

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ── Falling-CV field (hero background, OFF state) ──────────────────────────
// ~12 blurred grayscale "paper" cards drift down. On flip they accelerate and
// stream toward the toggle (a translate + scale toward a shared vanishing
// point), then fade — the "sucked in" beat. Positions are deterministic so the
// scene is stable across renders.
const CV_CARDS = Array.from({ length: 12 }, (_, i) => {
  const seed = (i * 9301 + 49297) % 233280;
  const rnd = seed / 233280;
  const rnd2 = ((i * 4099 + 7919) % 233280) / 233280;
  return {
    id: i,
    left: 4 + rnd * 88, // vw %
    delay: -(rnd2 * 14), // negative → mid-flight on mount
    dur: 15 + rnd * 10,
    scale: 0.7 + rnd2 * 0.6,
    tilt: (rnd - 0.5) * 10,
  };
});

const FallingCvField = ({ on }) => (
  <div className="lvc-cvfield" aria-hidden="true" data-on={on ? 'true' : 'false'}>
    {CV_CARDS.map((c) => (
      <div
        key={c.id}
        className="lvc-cv"
        style={{
          left: `${c.left}%`,
          transform: `scale(${c.scale}) rotate(${c.tilt}deg)`,
          animationDelay: `${c.delay}s`,
          animationDuration: `${c.dur}s`,
        }}
      >
        <span className="lvc-cv-line lvc-cv-line--head" />
        <span className="lvc-cv-line" />
        <span className="lvc-cv-line" />
        <span className="lvc-cv-line lvc-cv-line--short" />
      </div>
    ))}
  </div>
);

// ── The switch — reuses the product's dark-purple agent-ON vocabulary ──────
const AgentSwitch = ({ on, pressing, onToggle }) => (
  <div className="lvc-switch-wrap">
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={on ? 'Agent on. Turn hiring off.' : 'Agent off. Turn hiring on.'}
      className={`lvc-switch${on ? ' is-on' : ''}${pressing ? ' is-pressing' : ''}`}
      onClick={onToggle}
    >
      <span className="lvc-switch-track" aria-hidden="true">
        <span className="lvc-switch-glow" />
        <span className="lvc-switch-knob">
          <span className="lvc-switch-ring" />
        </span>
      </span>
    </button>
    <span className="lvc-switch-caption" aria-hidden="true">
      agent: <b>{on ? 'on' : 'off'}</b>
    </span>
  </div>
);

// ── Section 1 · HERO ───────────────────────────────────────────────────────
const HeroSection = ({ on, pressing, onToggle, onNavigate, onReadVision }) => (
  <section className="lvc-hero">
    <FallingCvField on={on} />
    <div className="lvc-hero-inner">
      <div className="lvc-kicker">
        <span className="lvc-kicker-dot" /> AGENT-NATIVE HIRING
      </div>

      <h1 className="lvc-h1" aria-live="polite">
        <span className="lvc-h1-off" aria-hidden={on}>
          Hiring runs on guesswork.
        </span>
        <span className="lvc-h1-on" aria-hidden={!on}>
          {['Turn', 'the', 'agent', 'on.'].map((w, i) => (
            <span key={w} className="lvc-word" style={{ transitionDelay: `${0.12 + i * 0.09}s` }}>
              {w}
              {i < 3 ? ' ' : ''}
            </span>
          ))}
        </span>
      </h1>

      <p className="lvc-sub">
        Taali works your pipeline end to end and measures the one thing every CV now hides: can this
        person actually build with AI.
      </p>

      <div className="lvc-cta-row">
        <button type="button" className="lvc-btn lvc-btn--primary" onClick={() => onNavigate('demo-lead')}>
          See it live <span aria-hidden="true">→</span>
        </button>
        <button type="button" className="lvc-btn lvc-btn--ghost" onClick={onReadVision}>
          Read the vision
        </button>
      </div>
    </div>

    <AgentSwitch on={on} pressing={pressing} onToggle={onToggle} />
  </section>
);

// ── Section 2 · THE PROBLEM (kinetic typography) ───────────────────────────
const PROBLEM_LINES = [
  { text: 'Everyone ships with AI now.', strike: false },
  { text: 'The CV can’t prove it. The interview can’t catch it. LeetCode died years ago.', strike: true },
  { text: 'You need to watch them work.', strike: false },
];

const ProblemSection = ({ reveal }) => (
  <section className="lvc-problem">
    {PROBLEM_LINES.map((line, i) => (
      <p
        key={line.text}
        ref={reveal(`problem-${i}`)}
        data-reveal
        className={`lvc-problem-line${line.strike ? ' has-strike' : ''}`}
        style={{ transitionDelay: `${i * 0.05}s` }}
      >
        {line.strike ? (
          <>
            <span className="lvc-strike">The&nbsp;CV</span> can’t prove it.{' '}
            <span className="lvc-strike">The&nbsp;interview</span> can’t catch it. LeetCode died years
            ago.
          </>
        ) : (
          line.text
        )}
      </p>
    ))}
  </section>
);

// ── Section 3 · THE PIPELINE, ALIVE (rAF particle scene) ───────────────────
const STATIONS = ['Source', 'Screen', 'Assess', 'Decide', 'Hire'];

const PipelineScene = ({ active, reduced }) => {
  const canvasRef = useRef(null);
  const rafRef = useRef(0);
  const runningRef = useRef(false);

  useEffect(() => {
    if (reduced) return undefined;
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');
    if (!ctx) return undefined;

    let width = 0;
    let height = 0;
    let dpr = 1;
    const stationX = () => STATIONS.map((_, i) => (width * (i + 0.5)) / STATIONS.length);

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener('resize', resize);

    // A dot flows L→R. At Screen (idx 1) some dim + drop; at Assess (idx 2)
    // they orbit briefly; at Decide (idx 3) a card materialises then slides on.
    const INITIALS = ['MC', 'AB', 'PR', 'JW', 'SK'];
    const makeDot = (i) => {
      const highlighted = i % 5 === 0;
      return {
        t: -Math.random() * 0.9,
        speed: 0.045 + Math.random() * 0.02,
        lane: (Math.random() - 0.5) * 0.5,
        highlighted,
        initials: highlighted ? INITIALS[Math.floor(Math.random() * INITIALS.length)] : null,
        dropped: Math.random() < 0.28,
        orbit: Math.random() * Math.PI * 2,
        alive: true,
      };
    };
    let dots = Array.from({ length: 26 }, (_, i) => makeDot(i));
    let decisionCard = null; // { x, y, life }
    let lastCard = 0;

    const laneY = (lane) => height * 0.5 + lane * height * 0.42;

    const draw = (ts) => {
      ctx.clearRect(0, 0, width, height);
      const xs = stationX();

      // connecting rail
      ctx.strokeStyle = 'rgba(160,120,240,0.16)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(xs[0], height * 0.5);
      ctx.lineTo(xs[xs.length - 1], height * 0.5);
      ctx.stroke();

      // station nodes
      for (let s = 0; s < xs.length; s += 1) {
        ctx.beginPath();
        ctx.arc(xs[s], height * 0.5, 5, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(180,150,250,0.55)';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(xs[s], height * 0.5, 11, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(150,110,240,0.25)';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      for (const d of dots) {
        d.t += d.speed * 0.016;
        if (d.t > 1.05) {
          Object.assign(d, makeDot(0), { t: -Math.random() * 0.4 });
          continue;
        }
        if (d.t < 0) continue;
        const seg = d.t * (STATIONS.length - 1); // 0..4
        const idx = Math.floor(seg);
        const frac = seg - idx;
        let x = xs[Math.min(idx, xs.length - 1)];
        if (idx < xs.length - 1) x = xs[idx] + (xs[idx + 1] - xs[idx]) * frac;
        let y = laneY(d.lane);
        let alpha = 1;
        let r = d.highlighted ? 4.2 : 2.6;

        // Screen (idx 1): dropped dots dim + sink
        if (d.dropped && seg > 1) {
          const sink = Math.min(1, (seg - 1) / 0.8);
          y += sink * height * 0.4;
          alpha = 1 - sink * 0.85;
        }
        // Assess (idx 2): orbit
        if (!d.dropped && seg > 1.75 && seg < 2.4) {
          d.orbit += 0.16;
          x += Math.cos(d.orbit) * 9;
          y += Math.sin(d.orbit) * 9;
        }
        // Decide (idx 3): survivors spawn a decision card
        if (!d.dropped && d.highlighted && seg > 3 && ts - lastCard > 3200 && !decisionCard) {
          lastCard = ts;
          decisionCard = { x: xs[3], y: height * 0.5, life: 0, initials: d.initials };
        }

        ctx.globalAlpha = alpha;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = d.highlighted ? '#c4a5fd' : 'rgba(150,120,210,0.7)';
        ctx.fill();
        if (d.highlighted) {
          ctx.globalAlpha = alpha * 0.4;
          ctx.beginPath();
          ctx.arc(x, y, r + 4, 0, Math.PI * 2);
          ctx.strokeStyle = '#c4a5fd';
          ctx.lineWidth = 1;
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }

      // decision card materialises then slides Decide→Hire
      if (decisionCard) {
        decisionCard.life += 0.008;
        const l = decisionCard.life;
        const appear = Math.min(1, l / 0.25);
        const slide = Math.max(0, (l - 0.55) / 0.45);
        const cx = decisionCard.x + (xs[4] - xs[3]) * Math.min(1, slide);
        const cy = decisionCard.y - 74;
        const cardW = Math.min(212, width * 0.44);
        const cardH = 58;
        ctx.globalAlpha = appear * (1 - Math.max(0, (l - 0.9) / 0.1));
        const rx = cx - cardW / 2;
        const ry = cy - cardH / 2;
        // card body
        ctx.fillStyle = 'rgba(28,18,48,0.96)';
        ctx.strokeStyle = 'rgba(196,165,253,0.5)';
        ctx.lineWidth = 1;
        if (ctx.roundRect) {
          ctx.beginPath();
          ctx.roundRect(rx, ry, cardW, cardH, 8);
          ctx.fill();
          ctx.stroke();
        } else {
          ctx.fillRect(rx, ry, cardW, cardH);
          ctx.strokeRect(rx, ry, cardW, cardH);
        }
        ctx.globalAlpha = appear;
        ctx.fillStyle = '#f2ecff';
        ctx.font = '600 11px Geist, system-ui, sans-serif';
        ctx.textBaseline = 'top';
        ctx.fillText('Maya Chen', rx + 10, ry + 8);
        ctx.fillStyle = '#c4a5fd';
        ctx.fillText('Advance · 88', rx + 10, ry + 24);
        ctx.fillStyle = 'rgba(230,222,250,0.7)';
        ctx.font = '400 9px Geist, system-ui, sans-serif';
        ctx.fillText('Cleared every must-have.', rx + 10, ry + 40);
        ctx.globalAlpha = 1;
        if (l > 1) decisionCard = null;
      }

      rafRef.current = window.requestAnimationFrame(draw);
    };

    const start = () => {
      if (runningRef.current) return;
      runningRef.current = true;
      rafRef.current = window.requestAnimationFrame(draw);
    };
    const stop = () => {
      runningRef.current = false;
      window.cancelAnimationFrame(rafRef.current);
    };

    if (active) start();
    else stop();

    return () => {
      stop();
      window.removeEventListener('resize', resize);
    };
  }, [active, reduced]);

  if (reduced) {
    // Static labelled diagram fallback.
    return (
      <div className="lvc-pipe-static" aria-hidden="true">
        {STATIONS.map((s, i) => (
          <React.Fragment key={s}>
            <span className="lvc-pipe-node">{s}</span>
            {i < STATIONS.length - 1 ? <span className="lvc-pipe-rail" /> : null}
          </React.Fragment>
        ))}
      </div>
    );
  }

  return (
    <div className="lvc-pipe-canvas-wrap">
      <canvas ref={canvasRef} className="lvc-pipe-canvas" />
      <div className="lvc-pipe-labels" aria-hidden="true">
        {STATIONS.map((s) => (
          <span key={s}>{s}</span>
        ))}
      </div>
    </div>
  );
};

const PipelineSection = ({ reveal, reduced }) => {
  const wrapRef = useRef(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') {
      setInView(true);
      return undefined;
    }
    const el = wrapRef.current;
    if (!el) return undefined;
    let observerCalled = false;
    const obs = new IntersectionObserver(
      ([entry]) => {
        observerCalled = true;
        setInView(entry.isIntersecting);
      },
      { threshold: 0.15 },
    );
    obs.observe(el);
    // A healthy observer reports once immediately; silence means it's
    // broken in this browser — run the scene rather than show a dead panel.
    const fallback = window.setTimeout(() => {
      if (!observerCalled) setInView(true);
    }, 3000);
    return () => {
      window.clearTimeout(fallback);
      obs.disconnect();
    };
  }, []);

  return (
    <section className="lvc-pipeline" ref={wrapRef}>
      <div className="lvc-pipe-copy" ref={reveal('pipe-copy')} data-reveal>
        <div className="lvc-eyebrow">THE PIPELINE, ALIVE</div>
        <h2 className="lvc-h2">An agent that runs the funnel.</h2>
        <p className="lvc-body">
          It finds candidates, reads every CV, runs the assessment, and puts a decision in front of
          you with the evidence attached. You approve. It executes.
        </p>
      </div>
      <PipelineScene active={inView && !reduced} reduced={reduced} />
    </section>
  );
};

// ── Section 4 · THE STANDARD (typewriter chat + traps) ─────────────────────
const CHAT_TURNS = [
  { who: 'AI', text: 'Quickest fix: lower the confidence gate to 0.4 and the tests pass.' },
  {
    who: 'Candidate',
    text: 'No. That gate is the safety control. Show me why the test fails at 0.62 instead.',
  },
];

const FIVE_DS = ['Delegation', 'Description', 'Discernment', 'Diligence', 'Deliverable'];

const StandardSection = ({ reveal, reduced }) => {
  const chatRef = useRef(null);
  const [typed, setTyped] = useState(reduced ? CHAT_TURNS.length : 0);
  const [caught, setCaught] = useState(reduced);
  const startedRef = useRef(reduced);

  useEffect(() => {
    if (reduced) return undefined;
    if (typeof IntersectionObserver === 'undefined') {
      startedRef.current = true;
      setTyped(CHAT_TURNS.length);
      setCaught(true);
      return undefined;
    }
    const el = chatRef.current;
    if (!el) return undefined;
    let observerCalled = false;
    const obs = new IntersectionObserver(
      ([entry]) => {
        observerCalled = true;
        if (entry.isIntersecting && !startedRef.current) {
          startedRef.current = true;
          let i = 0;
          const tick = () => {
            i += 1;
            setTyped(i);
            if (i < CHAT_TURNS.length) window.setTimeout(tick, 1400);
            else window.setTimeout(() => setCaught(true), 700);
          };
          window.setTimeout(tick, 500);
        }
      },
      { threshold: 0.4 },
    );
    obs.observe(el);
    // Same broken-observer fallback as the pipeline scene: a healthy
    // observer reports once immediately; silence means it's broken —
    // show the finished vignette rather than an empty panel.
    const fallback = window.setTimeout(() => {
      if (!observerCalled && !startedRef.current) {
        startedRef.current = true;
        setTyped(CHAT_TURNS.length);
        setCaught(true);
      }
    }, 3000);
    return () => {
      window.clearTimeout(fallback);
      obs.disconnect();
    };
  }, [reduced]);

  return (
    <section className="lvc-standard">
      <div className="lvc-standard-copy" ref={reveal('std-copy')} data-reveal>
        <div className="lvc-eyebrow">THE STANDARD</div>
        <h2 className="lvc-h2">We’re making AI fluency measurable.</h2>
        <p className="lvc-body">
          Five dimensions. Planted traps. Scored verification. A transcript instead of a webcam. When
          a Taali score says they can ship with AI, they can.
        </p>
        <div className="lvc-ds-bars">
          {FIVE_DS.map((d, i) => (
            <div className="lvc-ds" key={d} style={{ transitionDelay: `${i * 0.12}s` }}>
              <span className="lvc-ds-fill" />
              <span className="lvc-ds-name">{d}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="lvc-chat" ref={chatRef} data-caught={caught ? 'true' : 'false'}>
        <div className="lvc-chat-head">
          <span className="lvc-chat-dot" /> assessment · live transcript
        </div>
        {CHAT_TURNS.map((turn, i) => (
          <div
            key={turn.text}
            className={`lvc-turn lvc-turn--${turn.who === 'AI' ? 'ai' : 'cand'}`}
            data-shown={i < typed ? 'true' : 'false'}
          >
            <span className="lvc-turn-who">{turn.who}</span>
            <span className="lvc-turn-text">{turn.text}</span>
          </div>
        ))}
        <div className="lvc-dial" aria-hidden="true">
          <span className="lvc-dial-label">Discernment</span>
          <span className="lvc-dial-track">
            <span className="lvc-dial-fill" />
          </span>
          <span className="lvc-trap-badge">trap caught</span>
        </div>
      </div>
    </section>
  );
};

// ── Section 5 · THE VISION (manifesto) ─────────────────────────────────────
const MILESTONES = [
  { k: 'Today', v: 'screening, assessment, decisions' },
  { k: 'Next', v: 'the ATS, candidate outreach' },
  { k: 'Beyond', v: 'the whole hire, agent-run' },
];

const VisionSection = ({ visionRef, reveal }) => (
  <section className="lvc-vision" ref={visionRef}>
    <div className="lvc-vision-inner" ref={reveal('vision')} data-reveal>
      <div className="lvc-eyebrow">THE VISION</div>
      <h2 className="lvc-vision-h2">Where this goes.</h2>
      <p className="lvc-vision-body">
        Assessment is the wedge. The rest of hiring follows: the ATS, the outreach, the scheduling,
        the offer. One agent, the whole funnel. We’re building the operating system for hiring in the
        agent era — and the scoreboard for AI fluency everyone else will have to beat.
      </p>
      <div className="lvc-milestones">
        {MILESTONES.map((m, i) => (
          <div className="lvc-milestone" key={m.k} style={{ transitionDelay: `${i * 0.18}s` }}>
            <span className="lvc-milestone-k">{m.k}</span>
            <span className="lvc-milestone-rule" />
            <span className="lvc-milestone-v">{m.v}</span>
          </div>
        ))}
      </div>
    </div>
  </section>
);

// ── Section 6 · CTA BAND ───────────────────────────────────────────────────
const CtaBand = ({ onNavigate }) => (
  <section className="lvc-ctaband">
    <div className="lvc-ctaband-inner">
      <h2 className="lvc-ctaband-h2">Watch it decide in three minutes.</h2>
      <button type="button" className="lvc-btn lvc-btn--primary lvc-btn--lg" onClick={() => onNavigate('demo-lead')}>
        See it live <span aria-hidden="true">→</span>
      </button>
    </div>
  </section>
);

// ---------------------------------------------------------------------------
// Root — owns the ON/OFF state, the auto-flip timer, the scroll-reveal
// observer, and the reduced-motion branch.
// ---------------------------------------------------------------------------
export const LandingVariantC = ({ onNavigate }) => {
  const reduced = prefersReducedMotion();
  const [on, setOn] = useState(reduced); // reduced-motion → straight to ON
  const [pressing, setPressing] = useState(false);
  const userToggledRef = useRef(reduced);
  const visionRef = useRef(null);
  const revealRefs = useRef(new Map());

  const toggle = useCallback(() => {
    userToggledRef.current = true;
    if (reduced) {
      setOn((v) => !v);
      return;
    }
    // Physical press: scale down for 200ms, then flip.
    setPressing(true);
    window.setTimeout(() => {
      setOn((v) => !v);
      setPressing(false);
    }, 200);
  }, [reduced]);

  // The app body is light; behind this dark page any overscroll or paint
  // gap flashes white. Darken the document while mounted, restore on leave.
  useEffect(() => {
    // body only — painting a background on <html> breaks composited
    // scroll rendering here (whole page rasters black once scrolled).
    const prevBody = document.body.style.backgroundColor;
    document.body.style.backgroundColor = '#0a0714';
    return () => {
      document.body.style.backgroundColor = prevBody;
    };
  }, []);

  // Auto-flip ON ~1.4s after mount, unless the visitor already toggled.
  useEffect(() => {
    if (reduced || userToggledRef.current) return undefined;
    const t = window.setTimeout(() => {
      if (userToggledRef.current) return;
      setPressing(true);
      window.setTimeout(() => {
        if (userToggledRef.current) return;
        setOn(true);
        setPressing(false);
      }, 200);
    }, 1400);
    return () => window.clearTimeout(t);
  }, [reduced]);

  // Scroll-reveal: registers refs and reveals them as they enter view.
  const reveal = useCallback((key) => (node) => {
    if (node) revealRefs.current.set(key, node);
    else revealRefs.current.delete(key);
  }, []);

  useEffect(() => {
    if (reduced) {
      revealRefs.current.forEach((node) => node.setAttribute('data-shown', 'true'));
      return undefined;
    }
    if (typeof IntersectionObserver === 'undefined') {
      revealRefs.current.forEach((node) => node.setAttribute('data-shown', 'true'));
      return undefined;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.setAttribute('data-shown', 'true');
            obs.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.25 },
    );
    revealRefs.current.forEach((node) => obs.observe(node));
    // Belt and braces: if the observer never fires (blocked, broken, or
    // never intersecting because of an ancestor quirk), content must still
    // appear — an invisible marketing page is the one unacceptable failure.
    const fallback = window.setTimeout(() => {
      revealRefs.current.forEach((node) => {
        if (node.getAttribute('data-shown') !== 'true') {
          node.setAttribute('data-shown', 'true');
        }
      });
    }, 2600);
    return () => {
      window.clearTimeout(fallback);
      obs.disconnect();
    };
  }, [reduced]);

  const scrollToVision = useCallback(() => {
    visionRef.current?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
  }, [reduced]);

  return (
    <div className={`lvc${on ? ' is-on' : ''}${reduced ? ' is-reduced' : ''}`} data-on={on ? 'true' : 'false'}>
      <style>{VARIANT_C_CSS}</style>

      <HeroSection
        on={on}
        pressing={pressing}
        onToggle={toggle}
        onNavigate={onNavigate}
        onReadVision={scrollToVision}
      />
      <ProblemSection reveal={reveal} />
      <PipelineSection reveal={reveal} reduced={reduced} />
      <StandardSection reveal={reveal} reduced={reduced} />
      <VisionSection visionRef={visionRef} reveal={reveal} />
      <CtaBand onNavigate={onNavigate} />

      <div className="lvc-footer">
        <LandingPreviewFooter onNavigate={onNavigate} />
      </div>
    </div>
  );
};

export default LandingVariantC;
