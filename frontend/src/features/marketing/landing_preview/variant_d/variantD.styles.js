// Scoped CSS for LandingVariantD ("Watch it work"), injected via a <style> tag
// inside the `.lvd` root. Kept as a string module (not a .css import) so the
// whole variant lazy-loads as one chunk with its component and never leaks
// styles into the rest of the app — every selector is prefixed `.lvd`.
//
// LIGHT theme. Palette is the exact Taali light-purple family variant C uses
// (hardcoded, not the brand token) so the look holds regardless of the app's
// active brand/theme. Purple only — never red/amber/green.
//
// The centrepiece is a PINNED, scroll-scrubbed scene: a `.lvd-scene-wrap`
// ~500vh tall with a `position: sticky; top: 0; height: 100vh` stage inside.
// A single scroll-progress value `p` (0→1 across the wrap) is written to the
// stage as CSS custom properties (--b1..--b5 per-beat local progress, --v1..--v5
// per-beat opacity, --p overall). Every moving element derives its transform
// from those vars in pure CSS calc(), so per-frame JS only sets ~10 vars + a few
// textContents — no React re-render during scroll, transform/opacity only.
//
// Robustness: `overflow-x: clip` on the root (NOT hidden) so it never becomes a
// scroll container and never breaks `position: sticky`. Reduced-motion and short
// viewports fall back to a stacked static composition (`.is-static`) with every
// beat shown in its final state — no pin, no scrub, no Lenis.
export const VARIANT_D_CSS = `
.lvd {
  /* Taali light purple family — hardcoded. Purple only. */
  --lvd-purple: #5e3aa8;
  --lvd-purple-2: #4a2d80;
  --lvd-purple-soft: #ede5f8;
  --lvd-lav: #c4a5fd;
  --lvd-bg: #f7f4fb;      /* pale lavender base */
  --lvd-bg-2: #ffffff;    /* card surface */
  --lvd-ink: #15121a;
  --lvd-ink-2: #3a3343;
  --lvd-mute: #8b8595;
  --lvd-line: #e8e2ee;

  --lvd-maxw: 1140px;

  /* Scene progress vars — overwritten each frame by the progress hook. In the
     static / reduced-motion fallback they are forced to their final state. */
  --p: 0;
  --b1: 0; --b2: 0; --b3: 0; --b4: 0; --b5: 0;
  --v1: 1; --v2: 0; --v3: 0; --v4: 0; --v5: 0;

  position: relative;
  min-height: 100vh;
  background:
    radial-gradient(1100px 640px at 78% -12%, rgba(124,77,255,0.10), transparent 60%),
    radial-gradient(820px 620px at 10% 8%, rgba(196,165,253,0.10), transparent 58%),
    var(--lvd-bg);
  color: var(--lvd-ink);
  font-family: 'Geist', system-ui, -apple-system, sans-serif;
  /* clip (not hidden) — hides sideways overflow WITHOUT creating a scroll
     container, so the pinned scene's position:sticky keeps pinning to the
     viewport. This is load-bearing; see the file header. */
  overflow-x: clip;
}
.lvd *, .lvd *::before, .lvd *::after { box-sizing: border-box; }

/* ── HERO ────────────────────────────────────────────────────────────────
   Restrained: soft purple radial glow only (no dot lattice, no falling cards).
   The agent switch loads OFF (grey) and flips ON after ~1.4s or on click. */
.lvd-hero {
  position: relative;
  min-height: min(100vh, 820px);
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; text-align: center;
  padding: 72px 20px 96px;
}
.lvd-hero-inner { position: relative; z-index: 2; max-width: 860px; }

.lvd-kicker {
  display: inline-flex; align-items: center; gap: 10px;
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--lvd-purple); margin-bottom: 20px;
}
.lvd-kicker-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--lvd-purple);
  box-shadow: 0 0 0 4px rgba(124,77,255,0.16), 0 0 14px rgba(124,77,255,0.5);
}

.lvd-h1 {
  position: relative; font-weight: 600;
  font-size: clamp(38px, 5vw, 64px);
  line-height: 1.0; letter-spacing: -0.04em; margin: 0 0 18px;
  color: var(--lvd-ink);
}
.lvd-word {
  display: inline-block; opacity: 0;
  transform: translateY(0.5em); filter: blur(6px);
  transition: opacity 0.7s cubic-bezier(0.16,1,0.3,1),
              transform 0.7s cubic-bezier(0.16,1,0.3,1),
              filter 0.7s cubic-bezier(0.16,1,0.3,1);
}
.lvd-word:last-child { color: var(--lvd-purple); }
.lvd.is-on .lvd-word { opacity: 1; transform: none; filter: none; }
.lvd.is-static .lvd-word { opacity: 1; transform: none; filter: none; transition: none; }

.lvd-sub {
  max-width: 640px; margin: 0 auto 30px;
  font-size: clamp(15px, 1.7vw, 17px); line-height: 1.55; color: var(--lvd-ink-2);
  opacity: 0; transform: translateY(10px);
  transition: opacity 0.8s ease 0.7s, transform 0.8s ease 0.7s;
}
.lvd.is-on .lvd-sub, .lvd.is-static .lvd-sub { opacity: 1; transform: none; }

.lvd-cta-row {
  display: flex; flex-wrap: wrap; gap: 12px; justify-content: center;
  opacity: 0; transform: translateY(10px);
  transition: opacity 0.8s ease 0.9s, transform 0.8s ease 0.9s;
}
.lvd.is-on .lvd-cta-row, .lvd.is-static .lvd-cta-row { opacity: 1; transform: none; }

.lvd-btn {
  --lvd-btn-bg: var(--lvd-bg-2);
  --lvd-btn-color: var(--lvd-ink-2);
  --lvd-btn-border: var(--lvd-line);
  --lvd-btn-shadow: 0 0 transparent;
  --lvd-btn-hover-bg: var(--lvd-purple-soft);
  --lvd-btn-hover-color: var(--lvd-purple-2);
  --lvd-btn-hover-border: rgba(196,165,253,0.6);

  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  height: 40px; min-height: 40px; padding: 0 20px; border-radius: 10px;
  font-family: inherit; font-size: 14px; font-weight: 600; line-height: 1; cursor: pointer;
  color: var(--lvd-btn-color); background: var(--lvd-btn-bg);
  border: 1px solid var(--lvd-btn-border); box-shadow: var(--lvd-btn-shadow);
  white-space: nowrap;
  transition: transform 0.1s ease, box-shadow 0.16s ease, background 0.16s ease, border-color 0.16s ease, color 0.16s ease, opacity 0.16s ease;
}
.lvd-btn:hover:not(:disabled):not([aria-disabled="true"]) {
  color: var(--lvd-btn-hover-color);
  background: var(--lvd-btn-hover-bg);
  border-color: var(--lvd-btn-hover-border);
}
.lvd-btn:focus-visible {
  outline: 0;
  box-shadow: 0 0 0 3px rgba(94,58,168,0.24), var(--lvd-btn-shadow);
}
.lvd-btn:active:not(:disabled):not([aria-disabled="true"]) { transform: translateY(1px); }
.lvd-btn:is(:disabled, [aria-disabled="true"]) {
  opacity: 0.48;
  cursor: not-allowed;
  transform: none;
}
.lvd-btn--primary {
  --lvd-btn-bg: var(--lvd-purple);
  --lvd-btn-color: var(--lvd-bg-2);
  --lvd-btn-border: var(--lvd-purple);
  --lvd-btn-shadow: 0 1px 2px rgba(94,58,168,0.22);
  --lvd-btn-hover-bg: var(--lvd-purple-2);
  --lvd-btn-hover-color: var(--lvd-bg-2);
  --lvd-btn-hover-border: var(--lvd-purple-2);
}
.lvd-btn--ghost { --lvd-btn-bg: var(--lvd-bg-2); --lvd-btn-color: var(--lvd-ink-2); --lvd-btn-border: var(--lvd-line); }
.lvd-btn--sm { height: 32px; min-height: 32px; padding: 0 14px; font-size: 13px; }
.lvd-btn--lg { height: 48px; min-height: 48px; padding: 0 24px; }

/* Switch — grey OFF, purple ON (same vocabulary as variant C). */
.lvd-switch-wrap {
  position: relative; z-index: 3; margin-top: 40px;
  display: flex; flex-direction: column; align-items: center; gap: 14px;
}
.lvd-switch {
  appearance: none; border: 0; padding: 0; cursor: pointer; background: none;
  border-radius: 999px; transition: transform 0.18s cubic-bezier(0.34,1.56,0.64,1);
}
.lvd-switch:focus-visible { outline: 2px solid var(--lvd-purple); outline-offset: 6px; }
.lvd-switch.is-pressing { transform: scale(0.94); }
.lvd-switch-track {
  position: relative; display: block; width: 128px; height: 62px; border-radius: 999px;
  background: linear-gradient(180deg, #e9e4f0, #d9d2e2); border: 1px solid var(--lvd-line);
  box-shadow: inset 0 2px 6px rgba(21,18,26,0.14), inset 0 -1px 0 rgba(255,255,255,0.6);
  transition: border-color 0.5s ease, box-shadow 0.5s ease, background 0.6s ease;
}
.lvd-switch.is-on .lvd-switch-track {
  background: linear-gradient(120deg, var(--lvd-purple-2), var(--lvd-lav), var(--lvd-purple), var(--lvd-purple-2));
  background-size: 300% 300%; border-color: rgba(196,165,253,0.7);
  box-shadow: inset 0 2px 6px rgba(74,45,128,0.3), 0 0 26px rgba(124,77,255,0.35), 0 0 0 1px rgba(196,165,253,0.4);
}
.lvd-switch-glow {
  position: absolute; inset: -18px; border-radius: 999px;
  background: radial-gradient(closest-side, rgba(124,77,255,0.35), transparent 75%);
  opacity: 0; transition: opacity 0.8s ease; filter: blur(8px);
}
.lvd-switch.is-on .lvd-switch-glow { opacity: 1; }
.lvd-switch-knob {
  position: absolute; top: 5px; left: 5px; width: 52px; height: 52px; border-radius: 50%;
  background: linear-gradient(160deg, #ffffff, #f0ecf7);
  box-shadow: 0 5px 14px rgba(21,18,26,0.22), inset 0 -2px 4px rgba(21,18,26,0.06);
  display: flex; align-items: center; justify-content: center;
  transition: transform 0.34s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.4s ease;
}
.lvd-switch.is-on .lvd-switch-knob {
  transform: translateX(66px);
  box-shadow: 0 8px 20px rgba(124,77,255,0.4), inset 0 -2px 4px rgba(94,58,168,0.08);
}
.lvd-switch.is-pressing .lvd-switch-knob { width: 60px; }
.lvd-switch-ring { width: 16px; height: 16px; border-radius: 50%; border: 2px solid rgba(139,133,149,0.4); }
.lvd-switch.is-on .lvd-switch-ring { border-color: rgba(124,77,255,0.8); }
.lvd-switch-caption {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 12px;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--lvd-mute); transition: color 0.5s ease;
}
.lvd-switch-caption b { color: var(--lvd-mute); font-weight: 600; }
.lvd-switch.is-on ~ .lvd-switch-caption b,
.lvd-switch-wrap .lvd-switch.is-on + .lvd-switch-caption b { color: var(--lvd-purple); }
.lvd.is-on .lvd-switch-caption b { color: var(--lvd-purple); }

/* Scroll-to-watch affordance — fades in once the agent is ON. */
.lvd-scrollcue {
  margin-top: 22px; display: inline-flex; flex-direction: column; align-items: center; gap: 6px;
  background: none; border: 0; cursor: pointer;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--lvd-mute);
  opacity: 0; transform: translateY(6px);
  transition: opacity 0.7s ease 1.2s, transform 0.7s ease 1.2s, color 0.2s ease;
}
.lvd.is-on .lvd-scrollcue, .lvd.is-static .lvd-scrollcue { opacity: 1; transform: none; }
.lvd-scrollcue:hover { color: var(--lvd-purple); }
.lvd-scrollcue-chev { width: 18px; height: 18px; }

/* ── PINNED SCENE ─────────────────────────────────────────────────────── */
.lvd-scene-wrap { position: relative; height: 500vh; }
.lvd-stage {
  position: sticky; top: 0; height: 100vh; overflow: hidden;
  display: flex; flex-direction: column; align-items: center;
  padding: 0 20px;
}
.lvd-stage-inner {
  position: relative; width: 100%; max-width: var(--lvd-maxw);
  flex: 1; display: flex; align-items: center; justify-content: center;
}

/* Progress rail + 5 step labels (top of the stage). */
.lvd-rail {
  width: 100%; max-width: var(--lvd-maxw); margin: 0 auto;
  padding-top: clamp(24px, 5vh, 56px); flex-shrink: 0;
}
.lvd-rail-track { position: relative; height: 2px; border-radius: 2px; background: var(--lvd-line); }
.lvd-rail-fill {
  position: absolute; left: 0; top: 0; height: 100%; border-radius: 2px;
  transform-origin: left; transform: scaleX(var(--p));
  background: linear-gradient(90deg, var(--lvd-purple-2), var(--lvd-purple));
}
.lvd-rail-steps { display: flex; justify-content: space-between; margin-top: 12px; }
.lvd-rail-step {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--lvd-mute);
  transition: color 0.25s ease, opacity 0.25s ease; opacity: 0.7;
}
.lvd-rail-step.is-active { color: var(--lvd-purple); opacity: 1; }

/* Caption — one per beat, crossfaded by its beat opacity var. */
.lvd-caps { width: 100%; max-width: 760px; margin: 0 auto; padding-bottom: clamp(24px, 5vh, 56px); flex-shrink: 0; position: relative; min-height: 88px; }
.lvd-cap {
  position: absolute; inset: 0; text-align: center;
  display: flex; flex-direction: column; gap: 6px; justify-content: flex-end;
}
.lvd-cap-eyebrow {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--lvd-purple);
}
.lvd-cap-text { font-size: clamp(15px, 1.9vw, 18px); line-height: 1.45; color: var(--lvd-ink); margin: 0; }

/* Beat layers — absolutely stacked; only their CSS-var opacity changes/frame. */
.lvd-beat {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  pointer-events: none; will-change: opacity;
}
.lvd-beat--1 { opacity: var(--v1); }
.lvd-beat--2 { opacity: var(--v2); }
.lvd-beat--3 { opacity: var(--v3); }
.lvd-beat--4 { opacity: var(--v4); }
.lvd-beat--5 { opacity: var(--v5); }

/* Abstract CV card — same visual language as the app's candidate rows. */
.lvd-card {
  position: relative; width: 240px; border-radius: 12px;
  background: var(--lvd-bg-2); border: 1px solid var(--lvd-line);
  box-shadow: 0 12px 34px -22px rgba(21,18,26,0.4);
  padding: 12px 14px; display: flex; flex-direction: column; gap: 8px;
}
.lvd-card-name { height: 9px; width: 56%; border-radius: 4px; background: var(--lvd-purple); opacity: 0.85; }
.lvd-card-line { height: 7px; border-radius: 4px; background: rgba(21,18,26,0.10); }
.lvd-card-line.s2 { width: 88%; }
.lvd-card-line.s3 { width: 68%; }
.lvd-card-chip {
  position: absolute; top: -9px; right: 10px;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9px;
  letter-spacing: 0.03em; color: var(--lvd-purple);
  background: var(--lvd-purple-soft); border: 1px solid rgba(196,165,253,0.6);
  padding: 2px 7px; border-radius: 999px; white-space: nowrap;
}

/* BEAT 1 — SOURCE. Cards fly in from the right into a left queue; counter tics. */
.lvd-b1-queue { position: absolute; left: 4%; top: 50%; width: 240px; height: 0; }
.lvd-b1-card {
  position: absolute; left: 0;
  /* Each card's local ramp: appears staggered by --i, slides from off-right. */
  --t: clamp(0, (var(--b1) - var(--i) * 0.085) * 3.4, 1);
  top: calc(-176px + var(--i) * 44px);
  transform: translate3d(calc((1 - var(--t)) * 520px), 0, 0);
  opacity: var(--t);
  will-change: transform;
}
.lvd-b1-counter {
  position: absolute; right: 6%; top: 50%; transform: translateY(-50%);
  text-align: right;
}
.lvd-b1-count { font-weight: 600; font-size: clamp(40px, 6vw, 72px); letter-spacing: -0.04em; color: var(--lvd-ink); font-variant-numeric: tabular-nums; }
.lvd-b1-count-cap { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvd-mute); margin-top: 4px; }

/* BEAT 2 — SCREEN. Column fans out; ~40% slide aside w/ evidence chip + dim. */
.lvd-b2-col { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.lvd-b2-card {
  position: absolute; left: 50%; top: 50%;
  --t: var(--b2);
}
/* Survivors pull forward + brighten. Rejects (data-reject) slide aside, shrink, dim. */
.lvd-b2-card[data-reject="0"] {
  transform: translate3d(calc(-50% + var(--x) * 1px), calc(-50% + var(--y) * 1px), 0)
             scale(calc(1 + var(--t) * 0.06));
}
.lvd-b2-card[data-reject="1"] {
  transform: translate3d(calc(-50% + var(--x) * 1px + var(--t) * var(--dir) * 300px), calc(-50% + var(--y) * 1px), 0)
             scale(calc(1 - var(--t) * 0.34));
  opacity: calc(1 - var(--t) * 0.72);
}
.lvd-b2-card[data-reject="1"] .lvd-card-chip { opacity: var(--t); }
.lvd-b2-card[data-reject="0"] .lvd-card-chip { display: none; }

/* BEAT 3 — ASSESS. One card scales up + morphs to a transcript that types. */
.lvd-b3-panel {
  position: relative; width: min(520px, 92%);
  border-radius: 16px; background: var(--lvd-bg-2); border: 1px solid var(--lvd-line);
  box-shadow: 0 30px 70px -44px rgba(94,58,168,0.5);
  padding: 20px; transform: scale(calc(0.86 + var(--b3) * 0.14));
  will-change: transform;
}
.lvd-b3-head { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px; letter-spacing: 0.08em; color: var(--lvd-mute); }
.lvd-b3-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--lvd-purple); box-shadow: 0 0 8px rgba(124,77,255,0.6); }
.lvd-turn { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; padding: 11px 13px; border-radius: 12px; min-height: 3.1em; }
.lvd-turn--ai { background: rgba(21,18,26,0.04); }
.lvd-turn--cand { background: var(--lvd-purple-soft); border: 1px solid rgba(196,165,253,0.5); }
.lvd-turn-who { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvd-purple); }
.lvd-turn-text { font-size: 13.5px; line-height: 1.5; color: var(--lvd-ink); }
.lvd-caret { display: inline-block; width: 2px; height: 1em; background: var(--lvd-purple); margin-left: 1px; vertical-align: -2px; opacity: 0.8; }
.lvd-dial { display: flex; align-items: center; gap: 12px; margin-top: 6px; }
.lvd-dial-label { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvd-mute); }
.lvd-dial-track { flex: 1; height: 6px; border-radius: 999px; background: rgba(21,18,26,0.06); overflow: hidden; }
.lvd-dial-fill {
  display: block; height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, var(--lvd-purple-2), var(--lvd-purple));
  transform-origin: left; transform: scaleX(clamp(0, (var(--b3) - 0.4) * 2.4, 1));
}
.lvd-trap-badge {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; font-weight: 600;
  letter-spacing: 0.08em; text-transform: uppercase; color: #fff;
  padding: 4px 9px; border-radius: 999px;
  background: linear-gradient(135deg, var(--lvd-purple), var(--lvd-purple-2));
  opacity: clamp(0, (var(--b3) - 0.86) * 8, 1);
  transform: scale(clamp(0.6, (var(--b3) - 0.82) * 6, 1));
}

/* BEAT 4 — DECIDE. Decision card assembles piece by piece; score counts up. */
.lvd-b4-card {
  position: relative; width: min(460px, 92%);
  border-radius: 16px; background: var(--lvd-bg-2); border: 1px solid var(--lvd-line);
  box-shadow: 0 30px 70px -44px rgba(94,58,168,0.5); padding: 22px;
}
.lvd-b4-name { font-weight: 600; font-size: 18px; letter-spacing: -0.01em; color: var(--lvd-ink); opacity: clamp(0, var(--b4) * 12, 1); }
.lvd-b4-role { font-size: 12.5px; color: var(--lvd-mute); margin-top: 2px; opacity: clamp(0, var(--b4) * 12, 1); }
.lvd-b4-reqs { margin: 16px 0; display: flex; flex-direction: column; gap: 10px; }
.lvd-b4-req { display: grid; grid-template-columns: 96px 1fr; gap: 10px; align-items: center; }
.lvd-b4-req-label { font-size: 11.5px; color: var(--lvd-ink-2); }
.lvd-b4-req-track { height: 6px; border-radius: 999px; background: rgba(21,18,26,0.06); overflow: hidden; }
.lvd-b4-req-fill {
  display: block; height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, var(--lvd-purple-2), var(--lvd-purple));
  transform-origin: left;
  /* Ramp 0→1 (staggered by --i), then scale to the bar's own target --w so the
     three bars settle at distinct widths, not all pinned to full. */
  transform: scaleX(calc(var(--w) * clamp(0, (var(--b4) - 0.12 - var(--i) * 0.1) * 4, 1)));
}
.lvd-b4-scorerow { display: flex; align-items: baseline; gap: 10px; margin-top: 6px; }
.lvd-b4-score { font-weight: 600; font-size: clamp(34px, 5vw, 48px); letter-spacing: -0.03em; color: var(--lvd-purple); font-variant-numeric: tabular-nums; }
.lvd-b4-score-cap { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvd-mute); }
.lvd-b4-verdict {
  margin-top: 16px; display: inline-flex; align-items: center; gap: 7px;
  font-size: 12.5px; font-weight: 600; color: #fff;
  background: linear-gradient(135deg, var(--lvd-purple), var(--lvd-purple-2));
  padding: 8px 14px; border-radius: 999px;
  opacity: clamp(0, (var(--b4) - 0.85) * 8, 1);
  transform: scale(clamp(0.7, (var(--b4) - 0.8) * 5, 1));
}

/* BEAT 5 — HAND BACK. Decision card slides into an ATS lane; audit line writes. */
.lvd-b5-wrap { position: absolute; inset: 0; display: flex; align-items: center; }
.lvd-b5-lane {
  position: absolute; right: 3%; top: 50%; transform: translateY(-50%);
  width: min(300px, 44%); border-radius: 14px;
  border: 1px dashed rgba(94,58,168,0.4); background: rgba(237,229,248,0.35);
  padding: 16px 14px; min-height: 160px;
}
.lvd-b5-lane-head { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lvd-purple); margin-bottom: 10px; }
.lvd-b5-card {
  position: absolute; left: 4%; top: 50%;
  width: min(300px, 44%);
  --t: var(--b5);
  transform: translate3d(calc(var(--t) * var(--slide, 0px)), -50%, 0) scale(calc(1 - var(--t) * 0.08));
  border-radius: 14px; background: var(--lvd-bg-2); border: 1px solid var(--lvd-line);
  box-shadow: 0 20px 50px -34px rgba(94,58,168,0.5); padding: 16px 14px;
  will-change: transform;
}
.lvd-b5-card-name { font-weight: 600; font-size: 15px; color: var(--lvd-ink); }
.lvd-b5-card-verdict { margin-top: 8px; display: inline-flex; font-size: 11px; font-weight: 600; color: #fff; background: linear-gradient(135deg, var(--lvd-purple), var(--lvd-purple-2)); padding: 5px 11px; border-radius: 999px; }
.lvd-b5-audit {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px; line-height: 1.7;
  color: var(--lvd-ink-2); word-break: break-word; min-height: 4.2em;
}
.lvd-b5-audit .lvd-caret { height: 0.9em; }

/* ── STATIC / REDUCED-MOTION FALLBACK ─────────────────────────────────────
   No pin, no scrub. The scene renders as 5 stacked, labelled panels, each in
   its final state. Beats become normal flow blocks; transforms neutralised. */
.lvd.is-static .lvd-scene-wrap { height: auto; }
.lvd.is-static .lvd-stage { position: static; height: auto; overflow: visible; display: block; padding: 8px 20px 40px; }
.lvd.is-static .lvd-stage-inner { display: block; }
.lvd.is-static .lvd-rail { display: none; }
.lvd.is-static .lvd-caps { display: none; }
.lvd.is-static .lvd-beat {
  position: relative; inset: auto; opacity: 1 !important;
  display: grid; gap: 20px; align-items: center;
  max-width: 940px; margin: 0 auto 18px; padding: 24px;
  border-radius: 18px; background: var(--lvd-bg-2); border: 1px solid var(--lvd-line);
  box-shadow: 0 18px 50px -38px rgba(21,18,26,0.35);
}
@media (min-width: 820px) { .lvd.is-static .lvd-beat { grid-template-columns: 1fr 1fr; } }
.lvd.is-static .lvd-beat-static-copy .lvd-cap-eyebrow { display: block; margin-bottom: 8px; font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--lvd-purple); }
.lvd.is-static .lvd-beat-static-copy .lvd-cap-text { font-size: 15px; line-height: 1.5; color: var(--lvd-ink); }
.lvd.is-static .lvd-beat-visual { position: relative; min-height: 200px; }
/* Neutralise per-beat transform gymnastics; show final composed state. */
.lvd.is-static .lvd-b1-queue { position: relative; left: auto; top: auto; height: auto; display: flex; flex-direction: column; gap: 8px; }
.lvd.is-static .lvd-b1-card { position: relative; top: auto; transform: none; opacity: 1; }
.lvd.is-static .lvd-b1-counter { position: relative; right: auto; top: auto; transform: none; text-align: left; margin-top: 16px; }
.lvd.is-static .lvd-b2-card { position: relative; left: auto; top: auto; transform: none !important; opacity: 1 !important; margin-bottom: 10px; }
.lvd.is-static .lvd-b2-card[data-reject="1"] { opacity: 0.5 !important; }
.lvd.is-static .lvd-b2-card[data-reject="1"] .lvd-card-chip { opacity: 1; }
.lvd.is-static .lvd-b3-panel { transform: none; width: 100%; }
.lvd.is-static .lvd-dial-fill { transform: scaleX(0.92); }
.lvd.is-static .lvd-trap-badge { opacity: 1; transform: none; }
.lvd.is-static .lvd-b4-card { width: 100%; }
.lvd.is-static .lvd-b4-name, .lvd.is-static .lvd-b4-role { opacity: 1; }
.lvd.is-static .lvd-b4-req-fill { transform: scaleX(var(--w)); }
.lvd.is-static .lvd-b4-verdict { opacity: 1; transform: none; }
.lvd.is-static .lvd-b5-wrap { position: relative; display: block; }
.lvd.is-static .lvd-b5-card { position: relative; left: auto; top: auto; transform: none; width: 100%; margin-bottom: 12px; }
.lvd.is-static .lvd-b5-lane { position: relative; right: auto; top: auto; transform: none; width: 100%; }

/* ── SECTION: THE STANDARD (5 Ds as a sticky rail) ────────────────────── */
.lvd-standard { max-width: var(--lvd-maxw); margin: 0 auto; padding: clamp(72px, 9vh, 104px) 24px; }
.lvd-sechead { text-align: center; max-width: 720px; margin: 0 auto 40px; }
.lvd-eyebrow { display: inline-flex; align-items: center; gap: 9px; font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px; letter-spacing: 0.15em; text-transform: uppercase; color: var(--lvd-purple); margin-bottom: 14px; }
.lvd-eyebrow-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--lvd-purple); box-shadow: 0 0 0 4px rgba(124,77,255,0.16); }
.lvd-h2 { font-weight: 600; font-size: clamp(26px, 3vw, 38px); line-height: 1.08; letter-spacing: -0.03em; margin: 0; color: var(--lvd-ink); }
.lvd-h2-accent { font-style: normal; color: var(--lvd-purple); }
.lvd-sechead-sub { max-width: 640px; margin: 14px auto 0; font-size: clamp(15px, 1.6vw, 16px); line-height: 1.6; color: var(--lvd-ink-2); }

/* Sticky rail: the D list pins briefly; each D highlights in turn as p advances.
   Same technique as the scene, lighter — one wrapper (~260vh) with a sticky
   inner; --dp (0→1) drives which D is active. */
.lvd-ds-wrap { position: relative; }
.lvd-ds-sticky { position: sticky; top: 0; min-height: 100vh; display: flex; align-items: center; }
.lvd-ds-rows { width: 100%; display: flex; flex-direction: column; }
.lvd-ds-row {
  display: grid; grid-template-columns: 1fr; gap: 6px;
  padding: 18px 0; border-top: 1px solid var(--lvd-line);
  transition: opacity 0.3s ease;
}
.lvd-ds-row:last-child { border-bottom: 1px solid var(--lvd-line); }
.lvd-ds-row.is-dim { opacity: 0.4; }
.lvd-ds-row.is-active .lvd-ds-name { color: var(--lvd-purple); }
.lvd-ds-name { font-weight: 600; font-size: clamp(17px, 2.1vw, 22px); letter-spacing: -0.02em; color: var(--lvd-ink); transition: color 0.3s ease; }
.lvd-ds-body { display: flex; flex-direction: column; gap: 4px; }
.lvd-ds-def { font-size: 14px; line-height: 1.5; color: var(--lvd-ink-2); }
.lvd-ds-evidence { font-size: 12.5px; line-height: 1.5; color: var(--lvd-mute); }
.lvd-ds-chip {
  justify-self: start; margin-top: 4px;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px; letter-spacing: 0.04em;
  color: var(--lvd-purple); background: var(--lvd-purple-soft);
  border: 1px solid rgba(196,165,253,0.5); padding: 3px 9px; border-radius: 999px;
}
.lvd.is-static .lvd-ds-sticky { position: static; min-height: 0; display: block; }
.lvd.is-static .lvd-ds-row.is-dim { opacity: 1; }

/* Claims strip */
.lvd-claims { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin-top: 40px; }
.lvd-claim {
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px; letter-spacing: 0.03em;
  color: var(--lvd-ink-2); background: var(--lvd-bg-2); border: 1px solid var(--lvd-line);
  padding: 8px 14px; border-radius: 999px;
}

/* Stats row */
.lvd-stats { max-width: var(--lvd-maxw); margin: 0 auto; padding: 0 24px clamp(72px, 9vh, 104px); }
.lvd-stats-grid { display: grid; gap: 14px 20px; grid-template-columns: repeat(2, 1fr); padding-top: 24px; border-top: 1px solid var(--lvd-line); }
.lvd-stat { display: flex; flex-direction: column; gap: 4px; }
.lvd-stat-big { font-weight: 600; font-size: clamp(17px, 1.9vw, 21px); letter-spacing: -0.02em; color: var(--lvd-ink); }
.lvd-stat-cap { font-size: 13px; line-height: 1.4; color: var(--lvd-ink-2); }

.lvd-footer { position: relative; z-index: 2; }

/* ── Responsive ──────────────────────────────────────────────────────── */
@media (min-width: 760px) {
  .lvd-stats-grid { grid-template-columns: repeat(4, 1fr); }
  .lvd-ds-row { grid-template-columns: 190px 1fr auto; align-items: center; gap: 20px; }
  .lvd-ds-chip { margin-top: 0; }
}
/* Short viewports (landscape phones, aggressive zoom-out): the pinned scene is
   swapped for the static stack in JS below --lvd-minh; this is a belt-and-braces
   guard so captions never collide with the rail if it ever does pin. */
@media (max-height: 560px) {
  .lvd-rail { padding-top: 14px; }
  .lvd-caps { padding-bottom: 14px; min-height: 64px; }
}
@media (max-width: 560px) {
  .lvd-switch-track { width: 112px; height: 56px; }
  .lvd-switch-knob { width: 46px; height: 46px; }
  .lvd-switch.is-on .lvd-switch-knob { transform: translateX(56px); }
  .lvd-card { width: 200px; }
  .lvd-b1-queue, .lvd-b5-card { width: 200px; }
}
`;
