// Scoped CSS for LandingVariantC, injected via a <style> tag inside the `.lvc`
// root. Kept as a string module (not a .css import) so the whole variant
// lazy-loads as one chunk with its component and never leaks styles into the
// rest of the app — every selector is prefixed `.lvc`.
//
// LIGHT theme. The OFF→ON "flood" is one transition on `.lvc`: a `filter`
// (grayscale) plus a set of custom properties that ripple into every child.
// OFF = desaturated grey-on-white, inert; ON = purple saturates in with
// restrained lavender glows. Flipping the single `.lvc.is-on` class drives the
// whole page. Colours are hardcoded from the Taali light purple palette so the
// look holds regardless of the app's active brand/theme.
//
// v3: tighter type scale + rhythm (Gradient Labs / Cursor density, not a
// poster), a dot-lattice hero motif (replaces the falling CVs), centred section
// headers in the hero's design language, and denser Pipeline/Standard sections.
export const VARIANT_C_CSS = `
.lvc {
  /* Taali light purple family — hardcoded. Purple only, no r/a/g. */
  --lvc-purple: #5e3aa8;
  --lvc-purple-2: #4a2d80;
  --lvc-purple-soft: #ede5f8;
  --lvc-lav: #c4a5fd;
  --lvc-bg: #f7f4fb;      /* pale lavender base */
  --lvc-bg-2: #ffffff;    /* card surface */
  --lvc-ink: #15121a;
  --lvc-ink-2: #3a3343;
  --lvc-mute: #8b8595;
  --lvc-line: #e8e2ee;

  /* Content column — kept tight so folds carry information, not air. */
  --lvc-maxw: 1140px;

  /* Floodable properties — animate on flip. */
  --lvc-glow: 0;                 /* 0 → 1 as agent turns on */
  --lvc-motion: paused;

  position: relative;
  min-height: 100vh;
  background:
    radial-gradient(1100px 620px at 80% -10%, rgba(124,77,255,calc(0.10 * var(--lvc-glow))), transparent 60%),
    radial-gradient(900px 700px at 8% 22%, rgba(196,165,253,calc(0.14 * var(--lvc-glow))), transparent 55%),
    var(--lvc-bg);
  color: var(--lvc-ink);
  font-family: 'Geist', system-ui, -apple-system, sans-serif;
  overflow-x: hidden;
  /* OFF: desaturated grey-on-white. ON: full colour. This one line is the flood. */
  filter: grayscale(0.92);
  transition: filter 1.1s cubic-bezier(0.16, 1, 0.3, 1);
}
.lvc.is-on {
  --lvc-glow: 1;
  --lvc-motion: running;
  filter: grayscale(0);
}
.lvc.is-reduced,
.lvc.is-reduced * { transition: none !important; animation: none !important; }
.lvc.is-reduced { filter: none; }

.lvc *,
.lvc *::before,
.lvc *::after { box-sizing: border-box; }

/* ── HERO ────────────────────────────────────────────────────────────── */
.lvc-hero {
  position: relative;
  /* Fill a laptop viewport, but never become a void on tall/zoomed-out
     screens — the next section should always hint from below the fold. */
  min-height: min(100vh, 820px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 72px 20px 128px;
  overflow: hidden;
}
.lvc-hero-inner { position: relative; z-index: 3; max-width: 860px; }

.lvc-kicker {
  display: inline-flex; align-items: center; gap: 10px;
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--lvc-purple); opacity: calc(0.4 + 0.6 * var(--lvc-glow));
  margin-bottom: 20px; transition: opacity 0.9s ease 0.3s;
}
.lvc-kicker-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--lvc-purple);
  box-shadow: 0 0 0 4px rgba(124,77,255,calc(0.18 * var(--lvc-glow))),
              0 0 14px rgba(124,77,255,calc(0.6 * var(--lvc-glow)));
}

.lvc-h1 {
  position: relative;
  font-weight: 600;
  font-size: clamp(38px, 5vw, 64px);
  line-height: 1.0;
  letter-spacing: -0.04em;
  margin: 0 0 18px;
  /* Reserve space for the taller (wrapped) headline so the OFF↔ON swap
     never shifts layout — a shift here scroll-jumps the whole hero. */
  min-height: 2.0em;
  display: grid;
  align-items: center;
}
.lvc-h1-off, .lvc-h1-on {
  grid-area: 1 / 1;
  display: block;
  transition: opacity 0.6s cubic-bezier(0.16,1,0.3,1);
}
.lvc.is-on .lvc-h1-off { opacity: 0; }
.lvc:not(.is-on) .lvc-h1-on { opacity: 0; pointer-events: none; }

.lvc-word {
  display: inline-block;
  color: var(--lvc-ink);
  opacity: 0;
  transform: translateY(0.5em);
  filter: blur(6px);
  transition: opacity 0.7s cubic-bezier(0.16,1,0.3,1),
              transform 0.7s cubic-bezier(0.16,1,0.3,1),
              filter 0.7s cubic-bezier(0.16,1,0.3,1);
}
.lvc-word:last-child { color: var(--lvc-purple); }
.lvc.is-on .lvc-word { opacity: 1; transform: none; filter: none; }
.lvc.is-reduced .lvc-word { opacity: 1; transform: none; filter: none; }

.lvc-sub {
  max-width: 620px; margin: 0 auto 30px;
  font-size: clamp(15px, 1.7vw, 17px); line-height: 1.55;
  color: var(--lvc-ink-2);
  opacity: 0; transform: translateY(10px);
  transition: opacity 0.8s ease 0.75s, transform 0.8s ease 0.75s;
}
.lvc.is-on .lvc-sub, .lvc.is-reduced .lvc-sub { opacity: 1; transform: none; }

.lvc-cta-row {
  display: flex; flex-wrap: wrap; gap: 12px; justify-content: center;
  opacity: 0; transform: translateY(10px);
  transition: opacity 0.8s ease 0.95s, transform 0.8s ease 0.95s;
}
.lvc.is-on .lvc-cta-row, .lvc.is-reduced .lvc-cta-row { opacity: 1; transform: none; }

.lvc-btn {
  --lvc-btn-bg: var(--lvc-bg-2);
  --lvc-btn-color: var(--lvc-ink-2);
  --lvc-btn-border: var(--lvc-line);
  --lvc-btn-shadow: 0 0 transparent;
  --lvc-btn-hover-bg: var(--lvc-purple-soft);
  --lvc-btn-hover-color: var(--lvc-purple-2);
  --lvc-btn-hover-border: rgba(196,165,253,0.6);

  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  height: 40px; min-height: 40px; padding: 0 20px; border-radius: 10px;
  font-family: inherit; font-size: 14px; font-weight: 600; line-height: 1; cursor: pointer;
  color: var(--lvc-btn-color); background: var(--lvc-btn-bg);
  border: 1px solid var(--lvc-btn-border); box-shadow: var(--lvc-btn-shadow);
  white-space: nowrap;
  transition: transform 0.1s ease, box-shadow 0.16s ease, background 0.16s ease, border-color 0.16s ease, color 0.16s ease, opacity 0.16s ease;
}
.lvc-btn:hover:not(:disabled):not([aria-disabled="true"]) {
  color: var(--lvc-btn-hover-color);
  background: var(--lvc-btn-hover-bg);
  border-color: var(--lvc-btn-hover-border);
}
.lvc-btn:focus-visible {
  outline: 0;
  box-shadow: 0 0 0 3px rgba(94,58,168,0.24), var(--lvc-btn-shadow);
}
.lvc-btn:active:not(:disabled):not([aria-disabled="true"]) { transform: translateY(1px); }
.lvc-btn:is(:disabled, [aria-disabled="true"]) {
  opacity: 0.48;
  cursor: not-allowed;
  transform: none;
}
.lvc-btn--primary {
  --lvc-btn-bg: var(--lvc-purple);
  --lvc-btn-color: var(--lvc-bg-2);
  --lvc-btn-border: var(--lvc-purple);
  --lvc-btn-shadow: 0 1px 2px rgba(94,58,168,0.22);
  --lvc-btn-hover-bg: var(--lvc-purple-2);
  --lvc-btn-hover-color: var(--lvc-bg-2);
  --lvc-btn-hover-border: var(--lvc-purple-2);
}
.lvc-btn--ghost {
  --lvc-btn-bg: var(--lvc-bg-2);
  --lvc-btn-color: var(--lvc-ink-2);
  --lvc-btn-border: var(--lvc-line);
}
.lvc-btn--sm { height: 32px; min-height: 32px; padding: 0 14px; font-size: 13px; }
.lvc-btn--lg { height: 48px; min-height: 48px; padding: 0 24px; }

/* ── DOT LATTICE (hero motif) ────────────────────────────────────────────
   ~120 small dots in a loose grid. OFF: static grey, low opacity. On flip a
   radial pulse ripples from the toggle (bottom-centre): each dot's colour +
   scale transition is delayed by its distance to the origin (computed per dot
   at render), so the wave visibly propagates. After it settles the whole field
   drifts slowly via one keyframe on the container. CSS transitions only. */
.lvc-lattice {
  position: absolute; inset: 0; z-index: 1; pointer-events: none;
  animation: lvcLatticeDrift 44s ease-in-out infinite;
  animation-play-state: var(--lvc-motion);
  will-change: transform;
}
.lvc-dot {
  position: absolute; border-radius: 50%;
  --d: 0s;                      /* per-dot ripple delay, set inline at render */
  transform: translate(-50%, -50%) scale(1);
  background: rgba(21,18,26,0.16);
  /* Settled ON colour flows in on flip, delayed by distance to the toggle. */
  transition: background 0.5s ease var(--d), box-shadow 0.5s ease var(--d);
}
.lvc.is-on .lvc-dot {
  background: rgba(94,58,168,0.55);
  box-shadow: 0 0 6px rgba(124,77,255,0.35);
  /* Scale pop shares the same per-dot delay so the ripple reads as one wave. */
  animation: lvcDotPop 0.6s cubic-bezier(0.34,1.56,0.64,1) var(--d) forwards;
}
@keyframes lvcDotPop {
  0%   { transform: translate(-50%, -50%) scale(1); }
  45%  { transform: translate(-50%, -50%) scale(1.9); }
  100% { transform: translate(-50%, -50%) scale(1); }
}
@keyframes lvcLatticeDrift {
  0%,100% { transform: translate3d(0, 0, 0); }
  50%     { transform: translate3d(0, -10px, 0); }
}

/* The switch — grey when OFF, purple saturates in when ON */
.lvc-switch-wrap {
  position: relative; z-index: 4;
  margin-top: 44px;
  display: flex; flex-direction: column; align-items: center; gap: 14px;
}
.lvc-switch {
  appearance: none; border: 0; padding: 0; cursor: pointer; background: none;
  border-radius: 999px;
  transition: transform 0.18s cubic-bezier(0.34,1.56,0.64,1);
}
.lvc-switch:focus-visible { outline: 2px solid var(--lvc-purple); outline-offset: 6px; }
.lvc-switch.is-pressing { transform: scale(0.94); }
.lvc-switch-track {
  position: relative; display: block;
  width: 128px; height: 62px; border-radius: 999px;
  background: linear-gradient(180deg, #e9e4f0, #d9d2e2);
  border: 1px solid var(--lvc-line);
  box-shadow: inset 0 2px 6px rgba(21,18,26,0.14), inset 0 -1px 0 rgba(255,255,255,0.6);
  transition: border-color 0.5s ease, box-shadow 0.5s ease, background 0.6s ease;
}
.lvc-switch.is-on .lvc-switch-track {
  background: linear-gradient(120deg, var(--lvc-purple-2), var(--lvc-lav), var(--lvc-purple), var(--lvc-purple-2));
  background-size: 300% 300%;
  border-color: rgba(196,165,253,0.7);
  box-shadow: inset 0 2px 6px rgba(74,45,128,0.3),
              0 0 26px rgba(124,77,255,0.35),
              0 0 0 1px rgba(196,165,253,0.4);
  animation: lvcSwitchFlow 6s ease-in-out infinite;
  animation-play-state: var(--lvc-motion);
}
.lvc-switch-glow {
  position: absolute; inset: -18px; border-radius: 999px;
  background: radial-gradient(closest-side, rgba(124,77,255,0.35), transparent 75%);
  opacity: var(--lvc-glow); transition: opacity 0.8s ease;
  filter: blur(8px);
}
.lvc-switch-knob {
  position: absolute; top: 5px; left: 5px;
  width: 52px; height: 52px; border-radius: 50%;
  background: linear-gradient(160deg, #ffffff, #f0ecf7);
  box-shadow: 0 5px 14px rgba(21,18,26,0.22), inset 0 -2px 4px rgba(21,18,26,0.06);
  display: flex; align-items: center; justify-content: center;
  transition: transform 0.34s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.4s ease;
}
.lvc-switch.is-on .lvc-switch-knob {
  transform: translateX(66px);
  box-shadow: 0 8px 20px rgba(124,77,255,0.4), inset 0 -2px 4px rgba(94,58,168,0.08);
}
.lvc-switch.is-pressing .lvc-switch-knob { width: 60px; }
.lvc-switch-ring {
  width: 16px; height: 16px; border-radius: 50%;
  border: 2px solid rgba(139,133,149,0.4);
}
.lvc-switch.is-on .lvc-switch-ring {
  border-color: rgba(124,77,255,0.8);
  animation: lvcRing 1.8s ease-out infinite;
  animation-play-state: var(--lvc-motion);
}
.lvc-switch-caption {
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--lvc-mute); transition: color 0.5s ease;
}
.lvc-switch-caption b { color: var(--lvc-purple); font-weight: 600; }
.lvc:not(.is-on) .lvc-switch-caption b { color: var(--lvc-mute); }

@keyframes lvcSwitchFlow { 0%,100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }
@keyframes lvcRing { 0% { transform: scale(1); opacity: 0.8; } 100% { transform: scale(2.4); opacity: 0; } }

/* Reveal primitive (scroll-triggered). Deltas kept subtle (translate/opacity
   only) so nothing is lost if the reveal never fires — content stays legible. */
.lvc [data-reveal] {
  opacity: 0; transform: translateY(24px);
  transition: opacity 0.9s cubic-bezier(0.16,1,0.3,1), transform 0.9s cubic-bezier(0.16,1,0.3,1);
}
.lvc [data-reveal][data-shown="true"] { opacity: 1; transform: none; }

/* ── Shared section header (hero design language, centred) ─────────────── */
.lvc-sechead { text-align: center; max-width: 720px; margin: 0 auto; }
.lvc-eyebrow {
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: 0.15em; text-transform: uppercase;
  color: var(--lvc-purple); margin-bottom: 14px;
}
.lvc-eyebrow--center {
  display: inline-flex; align-items: center; gap: 9px;
  opacity: calc(0.5 + 0.5 * var(--lvc-glow));
}
.lvc-eyebrow-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--lvc-purple);
  box-shadow: 0 0 0 4px rgba(124,77,255,calc(0.16 * var(--lvc-glow)));
}
.lvc-h2 {
  font-weight: 600; font-size: clamp(26px, 3vw, 38px);
  line-height: 1.08; letter-spacing: -0.03em; margin: 0; color: var(--lvc-ink);
}
.lvc-h2-accent { font-style: normal; color: var(--lvc-purple); }
.lvc-sechead-sub {
  max-width: 620px; margin: 14px auto 0;
  font-size: clamp(15px, 1.6vw, 16px); line-height: 1.6; color: var(--lvc-ink-2);
}

/* ── PROBLEM ─────────────────────────────────────────────────────────── */
.lvc-problem {
  max-width: var(--lvc-maxw); margin: 0 auto;
  padding: clamp(72px, 10vh, 96px) 24px;
  display: flex; flex-direction: column; gap: clamp(20px, 3.5vh, 40px);
}
.lvc-problem-line {
  font-weight: 600; font-size: clamp(22px, 2.6vw, 36px);
  line-height: 1.1; letter-spacing: -0.025em; margin: 0;
  color: var(--lvc-ink);
  opacity: 0; transform: translateY(28px);
  transition: opacity 0.85s cubic-bezier(0.16,1,0.3,1), transform 0.85s cubic-bezier(0.16,1,0.3,1);
}
.lvc-problem-line[data-shown="true"] { opacity: 1; transform: none; }
.lvc-problem-line.has-strike { color: var(--lvc-ink-2); }
.lvc-strike { position: relative; color: var(--lvc-ink); white-space: nowrap; }
.lvc-strike::after {
  content: ''; position: absolute; left: -2px; right: -2px; top: 54%; height: 4px;
  border-radius: 3px; background: var(--lvc-purple);
  box-shadow: 0 0 12px rgba(124,77,255,0.55);
  transform: scaleX(0); transform-origin: left;
  transition: transform 0.6s cubic-bezier(0.16,1,0.3,1) 0.5s;
}
.lvc-problem-line[data-shown="true"] .lvc-strike::after { transform: scaleX(1); }

/* ── PIPELINE ────────────────────────────────────────────────────────── */
.lvc-pipeline {
  max-width: var(--lvc-maxw); margin: 0 auto;
  padding: clamp(72px, 9vh, 96px) 24px;
  display: grid; gap: 36px;
}
.lvc-body { max-width: 620px; font-size: clamp(15px, 1.6vw, 16px); line-height: 1.6; color: var(--lvc-ink-2); margin: 0; }

/* Abstract ribbon — pure CSS, animates unconditionally when ON */
.lvc-ribbon {
  position: relative; width: 100%; height: 84px;
  display: flex; align-items: center;
}
.lvc-ribbon-rail {
  position: absolute; left: 4%; right: 4%; top: 50%; height: 2px;
  transform: translateY(-50%);
  background: linear-gradient(90deg,
    rgba(196,165,253,0.15), rgba(94,58,168,0.35), rgba(196,165,253,0.15));
}
.lvc-ribbon-flow { position: absolute; left: 4%; right: 4%; top: 50%; height: 0; }
.lvc-ribbon-dot {
  position: absolute; top: 50%; left: 0;
  width: 7px; height: 7px; margin-top: -3.5px; border-radius: 50%;
  background: var(--lvc-purple);
  box-shadow: 0 0 10px rgba(124,77,255,0.7);
  opacity: calc(0.15 + 0.85 * var(--lvc-glow));
  animation: lvcRibbonFlow 6.6s linear infinite;
  animation-play-state: var(--lvc-motion);
}
@keyframes lvcRibbonFlow {
  0%   { left: 0%;   opacity: 0; }
  8%   { opacity: 1; }
  92%  { opacity: 1; }
  100% { left: 100%; opacity: 0; }
}
.lvc-ribbon-nodes {
  position: absolute; left: 4%; right: 4%; top: 50%;
  transform: translateY(-50%);
  display: flex; justify-content: space-between;
}
.lvc-ribbon-node {
  position: relative; display: block;
  width: 16px; height: 16px; border-radius: 50%;
  background: var(--lvc-bg-2);
  border: 1.5px solid rgba(94,58,168,0.35);
}
.lvc-ribbon-node-core {
  position: absolute; inset: 3px; border-radius: 50%;
  background: var(--lvc-purple);
  box-shadow: 0 0 0 4px rgba(124,77,255,calc(0.12 * var(--lvc-glow))),
              0 0 14px rgba(124,77,255,calc(0.7 * var(--lvc-glow)));
  animation: lvcNodePulse 3s ease-in-out infinite;
  animation-delay: calc(var(--n) * 0.4s);
  animation-play-state: var(--lvc-motion);
}
@keyframes lvcNodePulse {
  0%,100% { transform: scale(1); opacity: 0.85; }
  50%     { transform: scale(1.25); opacity: 1; }
}

/* Stage cards — light cards, thin borders, purple node numbers */
.lvc-stage-grid {
  display: grid; gap: 14px;
  grid-template-columns: 1fr;
}
.lvc-stage {
  background: var(--lvc-bg-2);
  border: 1px solid var(--lvc-line);
  border-radius: 14px;
  padding: 20px 18px;
  box-shadow: 0 10px 30px -22px rgba(21,18,26,0.3);
  display: flex; flex-direction: column;
  /* Staggered reveal — parent flips data-shown, cards cascade in. */
  opacity: 0; transform: translateY(14px);
  transition: opacity 0.6s cubic-bezier(0.16,1,0.3,1), transform 0.6s cubic-bezier(0.16,1,0.3,1);
  transition-delay: calc(var(--i) * 0.08s);
}
.lvc-stage-grid[data-shown="true"] .lvc-stage { opacity: 1; transform: none; }
.lvc-stage-n {
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 12px; letter-spacing: 0.1em; color: var(--lvc-purple);
}
.lvc-stage-t {
  font-weight: 600; font-size: 17px; letter-spacing: -0.01em;
  margin: 7px 0 6px; color: var(--lvc-ink);
}
.lvc-stage-d { font-size: 13.5px; line-height: 1.5; color: var(--lvc-ink-2); margin: 0; }
.lvc-stage-meta {
  margin-top: auto; padding-top: 12px;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px;
  letter-spacing: 0.03em; color: var(--lvc-mute);
}

/* Stats row — big word / small caption, hero-consistent restraint */
.lvc-stats {
  display: grid; gap: 14px 20px;
  grid-template-columns: repeat(2, 1fr);
  padding-top: 12px; border-top: 1px solid var(--lvc-line);
  opacity: 0; transform: translateY(14px);
  transition: opacity 0.7s cubic-bezier(0.16,1,0.3,1), transform 0.7s cubic-bezier(0.16,1,0.3,1);
}
.lvc-stats[data-shown="true"] { opacity: 1; transform: none; }
.lvc-stat { display: flex; flex-direction: column; gap: 4px; }
.lvc-stat-big {
  font-weight: 600; font-size: clamp(17px, 1.9vw, 21px);
  letter-spacing: -0.02em; color: var(--lvc-ink);
}
.lvc-stat-cap { font-size: 13px; line-height: 1.4; color: var(--lvc-ink-2); }

/* ── STANDARD ────────────────────────────────────────────────────────── */
.lvc-standard {
  max-width: var(--lvc-maxw); margin: 0 auto;
  padding: clamp(72px, 9vh, 96px) 24px;
  display: grid; gap: 36px;
}
.lvc-standard-body { display: grid; gap: 32px; align-items: start; }

/* Five Ds as information rows */
.lvc-ds-rows { display: flex; flex-direction: column; }
.lvc-ds-row {
  display: grid; grid-template-columns: 1fr; gap: 6px;
  padding: 16px 0; border-top: 1px solid var(--lvc-line);
  opacity: 0; transform: translateY(10px);
  transition: opacity 0.6s ease, transform 0.6s ease;
  transition-delay: calc(var(--i) * 0.07s);
}
.lvc-standard-copy[data-shown="true"] .lvc-ds-row { opacity: 1; transform: none; }
.lvc-ds-row:last-child { border-bottom: 1px solid var(--lvc-line); }
.lvc-ds-name { font-weight: 600; font-size: 16px; color: var(--lvc-ink); }
.lvc-ds-body { display: flex; flex-direction: column; gap: 4px; }
.lvc-ds-def { font-size: 14px; line-height: 1.5; color: var(--lvc-ink-2); }
.lvc-ds-evidence { font-size: 12.5px; line-height: 1.5; color: var(--lvc-mute); }
.lvc-ds-chip {
  justify-self: start;
  margin-top: 4px;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px;
  letter-spacing: 0.04em; color: var(--lvc-purple);
  background: var(--lvc-purple-soft);
  border: 1px solid rgba(196,165,253,0.5);
  padding: 3px 9px; border-radius: 999px;
}

/* Trap vignette — light chat, statically composed, CSS-staggered reveal */
.lvc-chat {
  width: 100%;
  border-radius: 16px; padding: 22px;
  background: var(--lvc-bg-2);
  border: 1px solid var(--lvc-line);
  box-shadow: 0 24px 60px -40px rgba(94,58,168,0.4);
}
.lvc-chat-head {
  display: flex; align-items: center; gap: 8px; margin-bottom: 16px;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px;
  letter-spacing: 0.08em; color: var(--lvc-mute);
}
.lvc-chat-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--lvc-purple); box-shadow: 0 0 8px rgba(124,77,255,0.6); }
.lvc-turn {
  display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px;
  padding: 12px 14px; border-radius: 12px;
  opacity: 0; transform: translateY(8px);
  transition: opacity 0.5s ease, transform 0.5s ease;
  transition-delay: calc(var(--i) * 0.35s + 0.15s);
}
.lvc-chat[data-shown="true"] .lvc-turn { opacity: 1; transform: none; }
.lvc-turn--ai { background: rgba(21,18,26,0.04); }
.lvc-turn--cand { background: var(--lvc-purple-soft); border: 1px solid rgba(196,165,253,0.5); }
.lvc-turn-who {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px;
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvc-purple);
}
.lvc-turn-text { font-size: 14px; line-height: 1.5; color: var(--lvc-ink); }

.lvc-dial { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
.lvc-dial-label {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px;
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvc-mute);
}
.lvc-dial-track { flex: 1; height: 6px; border-radius: 999px; background: rgba(21,18,26,0.06); overflow: hidden; }
.lvc-dial-fill {
  display: block; height: 100%; width: 42%;
  background: linear-gradient(90deg, var(--lvc-purple-2), var(--lvc-purple));
  border-radius: 999px; transition: width 0.9s cubic-bezier(0.16,1,0.3,1) 0.9s;
}
.lvc-chat[data-shown="true"] .lvc-dial-fill { width: 92%; box-shadow: 0 0 12px rgba(124,77,255,0.6); }
.lvc-trap-badge {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; font-weight: 600;
  letter-spacing: 0.08em; text-transform: uppercase; color: #fff;
  padding: 4px 9px; border-radius: 999px;
  background: linear-gradient(135deg, var(--lvc-purple), var(--lvc-purple-2));
  opacity: 0; transform: scale(0.6) rotate(-8deg);
  transition: opacity 0.4s ease 1.2s, transform 0.4s cubic-bezier(0.34,1.56,0.64,1) 1.2s;
}
.lvc-chat[data-shown="true"] .lvc-trap-badge { opacity: 1; transform: none; }

/* Claims strip */
.lvc-claims {
  display: flex; flex-wrap: wrap; gap: 10px; justify-content: center;
}
.lvc-claim {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px;
  letter-spacing: 0.03em; color: var(--lvc-ink-2);
  background: var(--lvc-bg-2);
  border: 1px solid var(--lvc-line);
  padding: 8px 14px; border-radius: 999px;
}

/* Footer chrome sits on the light surface. */
.lvc-footer { position: relative; z-index: 2; }

/* ── Responsive ──────────────────────────────────────────────────────── */
@media (min-width: 760px) {
  .lvc-stage-grid { grid-template-columns: repeat(2, 1fr); }
  .lvc-stats { grid-template-columns: repeat(4, 1fr); }
  .lvc-ds-row { grid-template-columns: 150px 1fr auto; align-items: start; gap: 18px; }
  .lvc-ds-chip { margin-top: 0; align-self: center; }
}
@media (min-width: 1024px) {
  .lvc-stage-grid { grid-template-columns: repeat(3, 1fr); }
  /* Centred header, two-column body: D rows left, vignette right. */
  .lvc-standard-body { grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: 44px; }
  /* Inside the narrower left column the 3-col row squeezes the definition
     into a ribbon — stack the chip under the body instead. */
  .lvc-ds-row { grid-template-columns: 140px 1fr; }
  .lvc-ds-chip { grid-column: 2; justify-self: start; margin-top: 6px; align-self: start; }
}
@media (min-width: 1280px) {
  /* Five stages, one row — the 3+2 split leaves a lopsided hole. */
  .lvc-stage-grid { grid-template-columns: repeat(5, 1fr); }
}
@media (max-width: 560px) {
  .lvc-switch-track { width: 112px; height: 56px; }
  .lvc-switch-knob { width: 46px; height: 46px; }
  .lvc-switch.is-on .lvc-switch-knob { transform: translateX(56px); }
}

/* Reduced-motion: static composition, no keyframe scenes. The lattice renders
   in its settled ON state (dots already purple, no drift, no ripple). */
@media (prefers-reduced-motion: reduce) {
  .lvc, .lvc * { animation: none !important; }
  .lvc { filter: none; }
  .lvc-lattice { animation: none; }
  .lvc-dot {
    background: rgba(94,58,168,0.55);
    box-shadow: 0 0 6px rgba(124,77,255,0.35);
    transition: none;
  }
  .lvc-switch-glow { opacity: 1; }
}
`;
