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
  min-height: min(100vh, 920px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 88px 20px 160px;
  overflow: hidden;
}
.lvc-hero-inner { position: relative; z-index: 3; max-width: 900px; }

.lvc-kicker {
  display: inline-flex; align-items: center; gap: 10px;
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--lvc-purple); opacity: calc(0.4 + 0.6 * var(--lvc-glow));
  margin-bottom: 22px; transition: opacity 0.9s ease 0.3s;
}
.lvc-kicker-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--lvc-purple);
  box-shadow: 0 0 0 4px rgba(124,77,255,calc(0.18 * var(--lvc-glow))),
              0 0 14px rgba(124,77,255,calc(0.6 * var(--lvc-glow)));
}

.lvc-h1 {
  position: relative;
  font-weight: 600;
  font-size: clamp(40px, 8vw, 92px);
  line-height: 0.98;
  letter-spacing: -0.045em;
  margin: 0 0 20px;
  /* Reserve space for the taller (wrapped) headline so the OFF↔ON swap
     never shifts layout — a shift here scroll-jumps the whole hero. */
  min-height: 2.05em;
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
  max-width: 620px; margin: 0 auto 34px;
  font-size: clamp(15px, 2vw, 19px); line-height: 1.55;
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
  display: inline-flex; align-items: center; gap: 8px;
  height: 50px; padding: 0 26px; border-radius: 999px;
  font-size: 14px; font-weight: 600; cursor: pointer;
  border: 1px solid transparent;
  transition: transform 0.2s ease, box-shadow 0.3s ease, background 0.3s ease;
}
.lvc-btn:active { transform: translateY(1px) scale(0.98); }
.lvc-btn--primary {
  color: #fff;
  background: linear-gradient(135deg, var(--lvc-purple), var(--lvc-purple-2));
  box-shadow: 0 12px 30px -12px rgba(94,58,168,0.55),
              inset 0 1px 0 rgba(255,255,255,0.2);
}
.lvc-btn--primary:hover { box-shadow: 0 16px 40px -12px rgba(94,58,168,0.7); }
.lvc-btn--ghost {
  color: var(--lvc-purple);
  background: var(--lvc-bg-2);
  border-color: var(--lvc-line);
}
.lvc-btn--ghost:hover { background: var(--lvc-purple-soft); border-color: rgba(196,165,253,0.6); }
.lvc-btn--lg { height: 56px; padding: 0 34px; font-size: 15px; }

/* Falling CVs — abstract light-grey paper drifting on white */
.lvc-cvfield { position: absolute; inset: 0; z-index: 1; pointer-events: none; }
.lvc-cv {
  position: absolute; top: -120px; width: 112px; height: 142px;
  border-radius: 8px; padding: 14px 12px;
  background: rgba(21,18,26,0.03);
  border: 1px solid rgba(21,18,26,0.07);
  box-shadow: 0 8px 24px -16px rgba(21,18,26,0.25);
  filter: blur(0.6px);
  display: flex; flex-direction: column; gap: 9px;
  transform-origin: center;
  animation-name: lvcFall;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
  animation-play-state: var(--lvc-motion);
  opacity: 0.55;
  transition: opacity 0.6s ease;
}
/* On flip: accelerate + stream toward the toggle (bottom-centre vanishing pt). */
.lvc-cvfield[data-on="true"] .lvc-cv {
  animation-name: lvcSuck;
  animation-duration: 1.4s !important;
  animation-iteration-count: 1;
  animation-fill-mode: forwards;
  animation-timing-function: cubic-bezier(0.5, 0, 0.75, 0);
}
.lvc-cv-line { height: 6px; border-radius: 3px; background: rgba(21,18,26,0.1); }
.lvc-cv-line--head { height: 9px; width: 64%; background: rgba(94,58,168,0.22); }
.lvc-cv-line--short { width: 46%; }

@keyframes lvcFall {
  0%   { transform: translateY(-10vh) scale(var(--s,1)); }
  100% { transform: translateY(118vh) scale(var(--s,1)); }
}
@keyframes lvcSuck {
  0%   { opacity: 0.55; }
  60%  { opacity: 0.85; }
  100% {
    /* collapse toward bottom-centre where the switch lives */
    top: 78vh; left: 50% !important;
    transform: translate(-50%, 0) scale(0.05) !important;
    opacity: 0;
  }
}

/* The switch — grey when OFF, purple saturates in when ON */
.lvc-switch-wrap {
  position: relative; z-index: 4;
  margin-top: 46px;
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

/* ── PROBLEM ─────────────────────────────────────────────────────────── */
.lvc-problem {
  max-width: 1100px; margin: 0 auto;
  padding: clamp(80px, 14vh, 180px) 24px;
  display: flex; flex-direction: column; gap: clamp(28px, 5vh, 64px);
}
.lvc-problem-line {
  font-weight: 600; font-size: clamp(28px, 5.4vw, 62px);
  line-height: 1.05; letter-spacing: -0.03em; margin: 0;
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
  max-width: 1120px; margin: 0 auto;
  padding: clamp(60px, 9vh, 120px) 24px;
  display: grid; gap: 40px;
}
.lvc-eyebrow {
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: 0.15em; text-transform: uppercase;
  color: var(--lvc-purple); margin-bottom: 14px;
}
.lvc-h2 {
  font-weight: 600; font-size: clamp(28px, 4vw, 46px);
  line-height: 1.05; letter-spacing: -0.03em; margin: 0 0 16px; color: var(--lvc-ink);
}
.lvc-body { max-width: 620px; font-size: clamp(15px, 1.7vw, 18px); line-height: 1.6; color: var(--lvc-ink-2); margin: 0; }
.lvc-pipe-copy { max-width: 720px; }

/* Abstract ribbon — pure CSS, animates unconditionally when ON */
.lvc-ribbon {
  position: relative; width: 100%; height: 96px;
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
  display: grid; gap: 16px;
  grid-template-columns: 1fr;
}
.lvc-stage {
  background: var(--lvc-bg-2);
  border: 1px solid var(--lvc-line);
  border-radius: 14px;
  padding: 22px 20px;
  box-shadow: 0 10px 30px -22px rgba(21,18,26,0.3);
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
  font-weight: 600; font-size: 18px; letter-spacing: -0.01em;
  margin: 8px 0 6px; color: var(--lvc-ink);
}
.lvc-stage-d { font-size: 14px; line-height: 1.55; color: var(--lvc-ink-2); margin: 0; }

/* ── STANDARD ────────────────────────────────────────────────────────── */
.lvc-standard {
  max-width: 1120px; margin: 0 auto;
  padding: clamp(60px, 9vh, 120px) 24px;
  display: grid; gap: 44px;
}
.lvc-standard-copy { max-width: 640px; }

/* Five Ds as information rows */
.lvc-ds-rows { display: flex; flex-direction: column; margin-top: 30px; }
.lvc-ds-row {
  display: grid; grid-template-columns: 1fr; gap: 4px;
  padding: 16px 0; border-top: 1px solid var(--lvc-line);
  opacity: 0; transform: translateY(10px);
  transition: opacity 0.6s ease, transform 0.6s ease;
  transition-delay: calc(var(--i) * 0.07s);
}
.lvc-standard-copy[data-shown="true"] .lvc-ds-row { opacity: 1; transform: none; }
.lvc-ds-row:last-child { border-bottom: 1px solid var(--lvc-line); }
.lvc-ds-name { font-weight: 600; font-size: 16px; color: var(--lvc-ink); }
.lvc-ds-def { font-size: 14px; line-height: 1.5; color: var(--lvc-ink-2); }
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
  max-width: 640px; width: 100%;
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
  grid-column: 1 / -1;
  display: flex; flex-wrap: wrap; gap: 10px;
}
.lvc-claim {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px;
  letter-spacing: 0.03em; color: var(--lvc-ink-2);
  background: var(--lvc-bg-2);
  border: 1px solid var(--lvc-line);
  padding: 8px 14px; border-radius: 999px;
}

/* ── CTA BAND ────────────────────────────────────────────────────────── */
.lvc-ctaband {
  padding: clamp(70px, 12vh, 150px) 24px;
  background: linear-gradient(135deg, var(--lvc-purple-2) 0%, var(--lvc-purple) 55%, var(--lvc-lav) 130%);
}
.lvc-ctaband-inner {
  max-width: 980px; margin: 0 auto; text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 30px;
}
.lvc-ctaband-h2 {
  font-weight: 600; font-size: clamp(30px, 5vw, 56px);
  line-height: 1.02; letter-spacing: -0.035em; margin: 0; color: #fff;
}
.lvc-ctaband .lvc-btn--primary {
  color: var(--lvc-purple);
  background: #fff;
  box-shadow: 0 14px 34px -14px rgba(21,18,26,0.4);
}
.lvc-ctaband .lvc-btn--primary:hover { box-shadow: 0 18px 44px -14px rgba(21,18,26,0.5); }

/* Footer chrome sits on the light surface. */
.lvc-footer { position: relative; z-index: 2; }

/* ── Responsive ──────────────────────────────────────────────────────── */
@media (min-width: 760px) {
  .lvc-stage-grid { grid-template-columns: repeat(2, 1fr); }
  .lvc-ds-row { grid-template-columns: 168px 1fr auto; align-items: center; gap: 18px; }
  .lvc-ds-chip { margin-top: 0; }
}
@media (min-width: 1024px) {
  .lvc-stage-grid { grid-template-columns: repeat(3, 1fr); }
  .lvc-standard { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; }
  /* Inside the narrow standard column the 3-col row squeezes the
     definition into a ribbon — stack the chip under it instead. */
  .lvc-ds-row { grid-template-columns: 140px 1fr; }
  .lvc-ds-chip { grid-column: 2; justify-self: start; margin-top: 6px; }
}
@media (min-width: 1280px) {
  /* Five stages, one row — the 3+2 split leaves a lopsided hole. */
  .lvc-stage-grid { grid-template-columns: repeat(5, 1fr); }
}
@media (max-width: 560px) {
  .lvc-cv { width: 88px; height: 112px; }
  .lvc-switch-track { width: 112px; height: 56px; }
  .lvc-switch-knob { width: 46px; height: 46px; }
  .lvc-switch.is-on .lvc-switch-knob { transform: translateX(56px); }
}

/* Reduced-motion: static composition, no keyframe scenes. */
@media (prefers-reduced-motion: reduce) {
  .lvc, .lvc * { animation: none !important; }
  .lvc { filter: none; }
  .lvc-cvfield { display: none; }
  .lvc-switch-glow { opacity: 1; }
}
`;
