// Scoped CSS for LandingVariantC, injected via a <style> tag inside the `.lvc`
// root. Kept as a string module (not a .css import) so the whole cinematic
// variant lazy-loads as one chunk with its component and never leaks styles
// into the rest of the app — every selector is prefixed `.lvc`.
//
// The OFF→ON "flood" is one transition on `.lvc`: a `filter`
// (grayscale+brightness) plus a set of custom properties that ripple into every
// child. Flipping the single `.lvc.is-on` class drives the entire page.
export const VARIANT_C_CSS = `
.lvc {
  /* Taali purple family — hardcoded so the dark cinematic look holds
     regardless of the app's active brand/theme. Purple only, no r/a/g. */
  --lvc-purple: #7c4dff;
  --lvc-purple-2: #5e3aa8;
  --lvc-purple-deep: #3a1d6e;
  --lvc-purple-ink: #241147;
  --lvc-lav: #c4a5fd;
  --lvc-bg: #0a0710;
  --lvc-bg-2: #120b1e;
  --lvc-ink: #f4f0fb;
  --lvc-ink-2: #b9adcf;
  --lvc-mute: #7c6e94;
  --lvc-grad-on: linear-gradient(150deg, #3a1d6e, #241147);
  --lvc-grad-on-animated: linear-gradient(120deg, #3a1d6e, #6a3fb8, #2a1556, #4a2a8a, #3a1d6e);

  /* Floodable properties — animate on flip. */
  --lvc-glow: 0;                 /* 0 → 1 as agent turns on */
  --lvc-motion: paused;

  position: relative;
  min-height: 100vh;
  background:
    radial-gradient(1100px 620px at 80% -10%, rgba(124,77,255,calc(0.10 * var(--lvc-glow))), transparent 60%),
    radial-gradient(900px 700px at 8% 22%, rgba(94,58,168,calc(0.12 * var(--lvc-glow))), transparent 55%),
    var(--lvc-bg);
  color: var(--lvc-ink);
  font-family: 'Geist', system-ui, -apple-system, sans-serif;
  overflow-x: hidden;
  /* OFF: desaturated + dim. ON: full colour. This one line is the flood. */
  filter: grayscale(0.92) brightness(0.7) contrast(0.95);
  transition: filter 1.1s cubic-bezier(0.16, 1, 0.3, 1);
}
.lvc.is-on {
  --lvc-glow: 1;
  --lvc-motion: running;
  filter: grayscale(0) brightness(1) contrast(1);
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
  min-height: 100vh;
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
  color: var(--lvc-lav); opacity: calc(0.35 + 0.65 * var(--lvc-glow));
  margin-bottom: 22px; transition: opacity 0.9s ease 0.3s;
}
.lvc-kicker-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--lvc-purple);
  box-shadow: 0 0 0 4px rgba(124,77,255,calc(0.25 * var(--lvc-glow))),
              0 0 18px rgba(124,77,255,calc(0.9 * var(--lvc-glow)));
}

.lvc-h1 {
  position: relative;
  font-weight: 600;
  font-size: clamp(40px, 8vw, 92px);
  line-height: 0.98;
  letter-spacing: -0.045em;
  margin: 0 0 20px;
  min-height: 1.1em;
}
.lvc-h1-off, .lvc-h1-on {
  display: block;
  transition: opacity 0.6s cubic-bezier(0.16,1,0.3,1);
}
.lvc-h1-on { position: absolute; inset: 0; }
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
.lvc-word:last-child { color: var(--lvc-lav); }
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
  box-shadow: 0 12px 34px -10px rgba(124,77,255,0.7),
              inset 0 1px 0 rgba(255,255,255,0.2);
}
.lvc-btn--primary:hover { box-shadow: 0 16px 44px -10px rgba(124,77,255,0.9); }
.lvc-btn--ghost {
  color: var(--lvc-ink);
  background: rgba(255,255,255,0.04);
  border-color: rgba(196,165,253,0.28);
}
.lvc-btn--ghost:hover { background: rgba(196,165,253,0.1); }
.lvc-btn--lg { height: 56px; padding: 0 34px; font-size: 15px; }

/* Falling CVs */
.lvc-cvfield { position: absolute; inset: 0; z-index: 1; pointer-events: none; }
.lvc-cv {
  position: absolute; top: -120px; width: 118px; height: 148px;
  border-radius: 8px; padding: 14px 12px;
  background: rgba(255,255,255,0.045);
  border: 1px solid rgba(255,255,255,0.07);
  backdrop-filter: blur(1px);
  filter: blur(1.2px);
  display: flex; flex-direction: column; gap: 9px;
  transform-origin: center;
  animation-name: lvcFall;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
  animation-play-state: var(--lvc-motion);
  opacity: 0.5;
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
.lvc-cv-line { height: 6px; border-radius: 3px; background: rgba(255,255,255,0.14); }
.lvc-cv-line--head { height: 9px; width: 64%; background: rgba(196,165,253,0.3); }
.lvc-cv-line--short { width: 46%; }

@keyframes lvcFall {
  0%   { transform: translateY(-10vh) scale(var(--s,1)); }
  100% { transform: translateY(118vh) scale(var(--s,1)); }
}
@keyframes lvcSuck {
  0%   { opacity: 0.5; }
  60%  { opacity: 0.85; }
  100% {
    /* collapse toward bottom-centre where the switch lives */
    top: 78vh; left: 50% !important;
    transform: translate(-50%, 0) scale(0.05) !important;
    opacity: 0;
  }
}

/* The switch */
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
.lvc-switch:focus-visible { outline: 2px solid var(--lvc-lav); outline-offset: 6px; }
.lvc-switch.is-pressing { transform: scale(0.94); }
.lvc-switch-track {
  position: relative; display: block;
  width: 128px; height: 62px; border-radius: 999px;
  background: linear-gradient(180deg, #1a1326, #0d0916);
  border: 1px solid rgba(196,165,253,0.16);
  box-shadow: inset 0 3px 10px rgba(0,0,0,0.7), inset 0 -1px 0 rgba(255,255,255,0.04);
  transition: border-color 0.5s ease, box-shadow 0.5s ease, background 0.6s ease;
}
.lvc-switch.is-on .lvc-switch-track {
  background: var(--lvc-grad-on-animated);
  background-size: 300% 300%;
  border-color: rgba(196,165,253,0.5);
  box-shadow: inset 0 2px 8px rgba(0,0,0,0.4),
              0 0 34px rgba(124,77,255,0.55),
              0 0 0 1px rgba(196,165,253,0.2);
  animation: lvcSwitchFlow 6s ease-in-out infinite;
  animation-play-state: var(--lvc-motion);
}
.lvc-switch-glow {
  position: absolute; inset: -20px; border-radius: 999px;
  background: radial-gradient(closest-side, rgba(124,77,255,0.55), transparent 75%);
  opacity: var(--lvc-glow); transition: opacity 0.8s ease;
  filter: blur(8px);
}
.lvc-switch-knob {
  position: absolute; top: 5px; left: 5px;
  width: 52px; height: 52px; border-radius: 50%;
  background: linear-gradient(160deg, #fbfaff, #d9cff2);
  box-shadow: 0 6px 16px rgba(0,0,0,0.55), inset 0 -2px 4px rgba(0,0,0,0.15);
  display: flex; align-items: center; justify-content: center;
  transition: transform 0.34s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.4s ease;
}
.lvc-switch.is-on .lvc-switch-knob {
  transform: translateX(66px);
  box-shadow: 0 8px 22px rgba(124,77,255,0.6), inset 0 -2px 4px rgba(0,0,0,0.1);
}
.lvc-switch.is-pressing .lvc-switch-knob { width: 60px; }
.lvc-switch-ring {
  width: 16px; height: 16px; border-radius: 50%;
  border: 2px solid rgba(94,58,168,0.35);
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
.lvc-switch-caption b { color: var(--lvc-lav); font-weight: 600; }
.lvc:not(.is-on) .lvc-switch-caption b { color: var(--lvc-mute); }

@keyframes lvcSwitchFlow { 0%,100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }
@keyframes lvcRing { 0% { transform: scale(1); opacity: 0.8; } 100% { transform: scale(2.4); opacity: 0; } }

/* Reveal primitive (scroll-triggered) */
.lvc [data-reveal] {
  opacity: 0; transform: translateY(28px);
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
  opacity: 0; transform: translateY(30px);
  transition: opacity 0.85s cubic-bezier(0.16,1,0.3,1), transform 0.85s cubic-bezier(0.16,1,0.3,1);
}
.lvc-problem-line[data-shown="true"] { opacity: 1; transform: none; }
.lvc-problem-line.has-strike { color: var(--lvc-ink-2); }
.lvc-strike { position: relative; color: var(--lvc-ink); white-space: nowrap; }
.lvc-strike::after {
  content: ''; position: absolute; left: -2px; right: -2px; top: 54%; height: 4px;
  border-radius: 3px; background: var(--lvc-purple);
  box-shadow: 0 0 14px rgba(124,77,255,0.8);
  transform: scaleX(0); transform-origin: left;
  transition: transform 0.6s cubic-bezier(0.16,1,0.3,1) 0.5s;
}
.lvc-problem-line[data-shown="true"] .lvc-strike::after { transform: scaleX(1); }

/* ── PIPELINE ────────────────────────────────────────────────────────── */
.lvc-pipeline {
  max-width: 1200px; margin: 0 auto;
  padding: clamp(60px, 9vh, 120px) 24px;
  display: grid; gap: 40px;
}
.lvc-eyebrow {
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: 0.15em; text-transform: uppercase;
  color: var(--lvc-lav); margin-bottom: 14px;
}
.lvc-h2 {
  font-weight: 600; font-size: clamp(28px, 4vw, 46px);
  line-height: 1.05; letter-spacing: -0.03em; margin: 0 0 16px;
}
.lvc-body { max-width: 560px; font-size: clamp(15px, 1.7vw, 18px); line-height: 1.6; color: var(--lvc-ink-2); margin: 0; }
.lvc-pipe-copy { max-width: 620px; }

.lvc-pipe-canvas-wrap {
  position: relative; width: 100%;
  height: clamp(260px, 34vh, 400px);
  border-radius: 16px;
  background: radial-gradient(700px 300px at 50% 40%, rgba(94,58,168,0.14), transparent 70%),
              rgba(255,255,255,0.02);
  border: 1px solid rgba(196,165,253,0.12);
  overflow: hidden;
}
.lvc-pipe-canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.lvc-pipe-labels {
  position: absolute; left: 0; right: 0; bottom: 14px;
  display: grid; grid-template-columns: repeat(5, 1fr);
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: clamp(9px, 1.4vw, 12px); letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--lvc-mute); text-align: center;
}
.lvc-pipe-static {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  padding: 40px 20px; justify-content: center;
}
.lvc-pipe-node {
  padding: 10px 16px; border-radius: 999px;
  border: 1px solid rgba(196,165,253,0.4); color: var(--lvc-lav);
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 12px; letter-spacing: 0.06em;
}
.lvc-pipe-rail { width: 28px; height: 2px; background: rgba(196,165,253,0.3); }

/* ── STANDARD ────────────────────────────────────────────────────────── */
.lvc-standard {
  max-width: 1200px; margin: 0 auto;
  padding: clamp(60px, 9vh, 120px) 24px;
  display: grid; gap: 44px;
}
.lvc-standard-copy { max-width: 620px; }
.lvc-ds-bars { display: flex; gap: 14px; margin-top: 34px; height: 120px; align-items: flex-end; }
.lvc-ds {
  position: relative; flex: 1; max-width: 64px;
  height: 100%; border-radius: 8px;
  background: rgba(255,255,255,0.03); border: 1px solid rgba(196,165,253,0.12);
  overflow: hidden;
}
.lvc-ds-fill {
  position: absolute; left: 0; right: 0; bottom: 0; height: 0;
  background: linear-gradient(180deg, var(--lvc-purple), var(--lvc-purple-2));
  box-shadow: 0 0 20px rgba(124,77,255,0.5);
  transition: height 0.9s cubic-bezier(0.16,1,0.3,1);
}
.lvc-standard-copy[data-shown="true"] .lvc-ds-fill { height: 100%; }
.lvc-ds-name {
  position: absolute; left: 50%; bottom: 10px; transform: translateX(-50%) rotate(-90deg);
  transform-origin: center; white-space: nowrap;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px;
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvc-ink);
}

.lvc-chat {
  max-width: 620px; width: 100%;
  border-radius: 16px; padding: 22px;
  background: linear-gradient(180deg, rgba(36,17,71,0.5), rgba(10,7,16,0.6));
  border: 1px solid rgba(196,165,253,0.16);
  box-shadow: 0 30px 80px -40px rgba(124,77,255,0.5);
}
.lvc-chat-head {
  display: flex; align-items: center; gap: 8px; margin-bottom: 16px;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px;
  letter-spacing: 0.08em; color: var(--lvc-mute);
}
.lvc-chat-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--lvc-purple); box-shadow: 0 0 10px var(--lvc-purple); }
.lvc-turn {
  display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px;
  padding: 12px 14px; border-radius: 12px;
  opacity: 0; transform: translateY(8px);
  transition: opacity 0.5s ease, transform 0.5s ease;
}
.lvc-turn[data-shown="true"] { opacity: 1; transform: none; }
.lvc-turn--ai { background: rgba(255,255,255,0.04); }
.lvc-turn--cand { background: rgba(124,77,255,0.14); border: 1px solid rgba(196,165,253,0.2); }
.lvc-turn-who {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px;
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvc-lav);
}
.lvc-turn-text { font-size: 14px; line-height: 1.5; color: var(--lvc-ink); }

.lvc-dial { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
.lvc-dial-label {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px;
  letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvc-mute);
}
.lvc-dial-track { flex: 1; height: 6px; border-radius: 999px; background: rgba(255,255,255,0.06); overflow: hidden; }
.lvc-dial-fill {
  display: block; height: 100%; width: 42%;
  background: linear-gradient(90deg, var(--lvc-purple-2), var(--lvc-purple));
  border-radius: 999px; transition: width 0.9s cubic-bezier(0.16,1,0.3,1);
}
.lvc-chat[data-caught="true"] .lvc-dial-fill { width: 92%; box-shadow: 0 0 16px rgba(124,77,255,0.9); }
.lvc-trap-badge {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; font-weight: 600;
  letter-spacing: 0.08em; text-transform: uppercase; color: #fff;
  padding: 4px 9px; border-radius: 999px;
  background: linear-gradient(135deg, var(--lvc-purple), var(--lvc-purple-2));
  opacity: 0; transform: scale(0.6) rotate(-8deg);
  transition: opacity 0.4s ease, transform 0.4s cubic-bezier(0.34,1.56,0.64,1);
}
.lvc-chat[data-caught="true"] .lvc-trap-badge { opacity: 1; transform: none; }

/* ── VISION ──────────────────────────────────────────────────────────── */
.lvc-vision {
  max-width: 980px; margin: 0 auto;
  padding: clamp(80px, 14vh, 190px) 24px;
}
.lvc-vision-inner { border-top: 1px solid rgba(196,165,253,0.14); padding-top: 48px; }
.lvc-vision-h2 {
  font-weight: 600; font-size: clamp(34px, 6vw, 74px);
  line-height: 1.0; letter-spacing: -0.04em; margin: 0 0 28px;
}
.lvc-vision-body {
  font-size: clamp(18px, 2.4vw, 28px); line-height: 1.5; letter-spacing: -0.01em;
  color: var(--lvc-ink); max-width: 860px; margin: 0 0 56px; font-weight: 500;
}
.lvc-vision-body::first-line { color: var(--lvc-lav); }
.lvc-milestones { display: grid; gap: 20px; }
.lvc-milestone {
  display: flex; align-items: center; gap: 18px;
  padding-top: 18px; border-top: 1px solid rgba(196,165,253,0.1);
  opacity: 0.35; transform: translateY(8px);
  transition: opacity 0.7s ease, transform 0.7s ease;
}
.lvc-vision-inner[data-shown="true"] .lvc-milestone { opacity: 1; transform: none; }
.lvc-milestone-k {
  min-width: 92px; font-weight: 600; font-size: 16px; color: var(--lvc-lav);
}
.lvc-milestone-rule { flex: 0 0 40px; height: 1px; background: rgba(196,165,253,0.35); }
.lvc-milestone-v { font-size: clamp(15px, 2vw, 19px); color: var(--lvc-ink-2); }

/* ── CTA BAND ────────────────────────────────────────────────────────── */
.lvc-ctaband {
  padding: clamp(70px, 12vh, 150px) 24px;
  background: linear-gradient(135deg, var(--lvc-bg) 0%, var(--lvc-purple-ink) 55%, var(--lvc-purple-2) 130%);
}
.lvc-ctaband-inner {
  max-width: 980px; margin: 0 auto; text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 30px;
}
.lvc-ctaband-h2 {
  font-weight: 600; font-size: clamp(30px, 5vw, 56px);
  line-height: 1.02; letter-spacing: -0.035em; margin: 0;
}

/* Footer chrome sits on the dark surface. */
.lvc-footer { position: relative; z-index: 2; }

/* ── Responsive ──────────────────────────────────────────────────────── */
@media (min-width: 900px) {
  .lvc-pipeline { grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr); align-items: center; }
  .lvc-standard { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: center; }
}
@media (max-width: 560px) {
  .lvc-cv { width: 92px; height: 116px; }
  .lvc-switch-track { width: 112px; height: 56px; }
  .lvc-switch-knob { width: 46px; height: 46px; }
  .lvc-switch.is-on .lvc-switch-knob { transform: translateX(56px); }
  .lvc-ds-bars { height: 96px; }
}

/* Reduced-motion: static composition, no keyframe scenes. */
@media (prefers-reduced-motion: reduce) {
  .lvc, .lvc * { animation: none !important; }
  .lvc { filter: none; }
  .lvc-cvfield { display: none; }
  .lvc-switch-glow { opacity: 1; }
}
`;
