// Scoped CSS for LandingVariantE v4 ("rebuilt to the narrative spine"), injected
// via a <style> tag inside the `.lve` root. Kept as a string module (not a .css
// import) so the whole variant lazy-loads as one chunk with its component and
// never leaks styles into the rest of the app — every selector is `.lve…`.
//
// LIGHT theme. Palette is the exact Taali light-purple family variants C/D use
// (hardcoded, not the brand token) so the look holds regardless of the app's
// active brand/theme. Purple only — never red/amber/green. No CSS zoom. No
// horizontal scroll at 1024/1440. Mobile-first; nothing depends on exact vh.
//
// MOTION MODEL — this variant does NOT scroll-scrub. Section entrances use the
// shared one-shot CSS <Reveal> (previewMotion). The two SCENES (hero job-on
// loop, funnel advance) arm an initial-hidden state via a `data-armed` attribute
// that CSS keys off: while armed (JS mounted, motion allowed) the scene's
// animatable children are hidden by the rules below and a Motion useAnimate
// timeline reveals them. With NO `data-armed` (reduced motion, or JS not yet
// armed) every scene renders in its FINAL, legible state.
export const VARIANT_E_CSS = `
.lve {
  /* Taali light purple family — hardcoded. Purple only. */
  --lve-purple: #5e3aa8;
  --lve-purple-2: #4a2d80;
  --lve-purple-soft: #ede5f8;
  --lve-lav: #c4a5fd;
  --lve-bg: #f7f4fb;      /* pale lavender base */
  --lve-bg-2: #ffffff;    /* card surface */
  --lve-ink: #15121a;
  --lve-ink-2: #3a3343;
  --lve-mute: #8b8595;
  --lve-line: #e8e2ee;

  --lve-maxw: 1180px;
  --lve-nav-h: 66px;

  position: relative;
  min-height: 100vh;
  background:
    radial-gradient(1100px 620px at 82% -12%, rgba(124,77,255,0.10), transparent 60%),
    radial-gradient(880px 640px at 6% 4%, rgba(196,165,253,0.10), transparent 58%),
    var(--lve-bg);
  color: var(--lve-ink);
  font-family: 'Geist', system-ui, -apple-system, sans-serif;
  /* clip (not hidden) so no sideways overflow but sticky nav still pins. */
  overflow-x: clip;
}
.lve *, .lve *::before, .lve *::after { box-sizing: border-box; }
.lve a { color: inherit; text-decoration: none; }

.lve-wrap { width: 100%; max-width: var(--lve-maxw); margin: 0 auto; padding: 0 24px; }

/* ── Buttons ─────────────────────────────────────────────────────────── */
.lve-btn {
  --lve-btn-bg: var(--lve-bg-2);
  --lve-btn-color: var(--lve-ink-2);
  --lve-btn-border: var(--lve-line);
  --lve-btn-shadow: 0 0 transparent;
  --lve-btn-hover-bg: var(--lve-purple-soft);
  --lve-btn-hover-color: var(--lve-purple-2);
  --lve-btn-hover-border: rgba(196,165,253,0.6);

  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  height: 40px; min-height: 40px; padding: 0 20px; border-radius: 10px;
  font: inherit; font-size: 14px; font-weight: 600; line-height: 1; cursor: pointer;
  color: var(--lve-btn-color); background: var(--lve-btn-bg);
  border: 1px solid var(--lve-btn-border); box-shadow: var(--lve-btn-shadow);
  white-space: nowrap;
  transition: transform 0.1s ease, box-shadow 0.16s ease, background 0.16s ease, border-color 0.16s ease, color 0.16s ease, opacity 0.16s ease;
}
.lve-btn:hover:not(:disabled):not([aria-disabled="true"]) {
  color: var(--lve-btn-hover-color);
  background: var(--lve-btn-hover-bg);
  border-color: var(--lve-btn-hover-border);
}
.lve-btn:focus-visible {
  outline: 0;
  box-shadow: 0 0 0 3px rgba(94,58,168,0.24), var(--lve-btn-shadow);
}
.lve-btn:active:not(:disabled):not([aria-disabled="true"]) { transform: translateY(1px); }
.lve-btn:is(:disabled, [aria-disabled="true"]) {
  opacity: 0.48;
  cursor: not-allowed;
  transform: none;
}
.lve-btn--primary {
  --lve-btn-bg: var(--lve-purple);
  --lve-btn-color: var(--lve-bg-2);
  --lve-btn-border: var(--lve-purple);
  --lve-btn-shadow: 0 1px 2px rgba(94,58,168,0.22);
  --lve-btn-hover-bg: var(--lve-purple-2);
  --lve-btn-hover-color: var(--lve-bg-2);
  --lve-btn-hover-border: var(--lve-purple-2);
}
.lve-btn--ghost { --lve-btn-bg: var(--lve-bg-2); --lve-btn-color: var(--lve-ink-2); --lve-btn-border: var(--lve-line); }
.lve-btn--sm { height: 32px; min-height: 32px; padding: 0 14px; font-size: 13px; }
.lve-btn--lg { height: 48px; min-height: 48px; padding: 0 24px; }

/* ── NAV ─────────────────────────────────────────────────────────────── */
.lve-nav {
  position: sticky; top: 0; z-index: 50;
  height: var(--lve-nav-h);
  display: flex; align-items: center;
  border-bottom: 1px solid transparent;
  background: transparent;
  transition: background 0.3s ease, border-color 0.3s ease, backdrop-filter 0.3s ease, box-shadow 0.3s ease;
}
.lve-nav.is-scrolled {
  background: color-mix(in oklab, var(--lve-bg) 82%, transparent);
  backdrop-filter: saturate(1.4) blur(12px);
  -webkit-backdrop-filter: saturate(1.4) blur(12px);
  border-bottom-color: var(--lve-line);
  box-shadow: 0 8px 24px -20px rgba(21,18,26,0.5);
}
.lve-nav-inner {
  width: 100%; max-width: var(--lve-maxw); margin: 0 auto; padding: 0 24px;
  display: flex; align-items: center; justify-content: space-between; gap: 20px;
}
.lve-nav-left { display: flex; align-items: center; gap: 10px; }
.lve-brand { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; background: none; border: 0; padding: 0; }
.lve-brand-mark {
  width: 26px; height: 26px; border-radius: 8px; flex-shrink: 0;
  background: linear-gradient(140deg, var(--lve-purple), var(--lve-purple-2));
  box-shadow: 0 6px 16px -8px rgba(94,58,168,0.7), inset 0 1px 0 rgba(255,255,255,0.3);
  position: relative;
}
.lve-brand-mark::after {
  content: ''; position: absolute; inset: 8px; border-radius: 50%;
  background: rgba(255,255,255,0.9);
}
.lve-brand-name {
  font-family: 'Geist', system-ui, sans-serif; font-weight: 600; font-size: 18px;
  letter-spacing: -0.02em; color: var(--lve-ink);
}
.lve-brand-name em { font-style: normal; color: var(--lve-purple); }
.lve-nav-links { display: none; align-items: center; gap: 4px; }
.lve-nav-link {
  padding: 8px 12px; border-radius: 8px; background: none; border: 0; cursor: pointer;
  font: inherit; font-size: 14px; color: var(--lve-ink-2);
  transition: background 0.2s ease, color 0.2s ease;
}
.lve-nav-link:hover { background: var(--lve-purple-soft); color: var(--lve-purple); }
.lve-nav-right { display: none; align-items: center; gap: 12px; }
.lve-nav-login {
  display: inline-flex; align-items: center; justify-content: center;
  height: 32px; min-height: 32px; padding: 0 14px; border-radius: 10px;
  background: transparent; border: 1px solid transparent; cursor: pointer;
  font: inherit; font-size: 13px; font-weight: 600; line-height: 1; color: var(--lve-ink-2);
  transition: transform 0.1s ease, background 0.16s ease, color 0.16s ease, opacity 0.16s ease;
}
.lve-nav-login:hover:not(:disabled):not([aria-disabled="true"]) { color: var(--lve-purple-2); background: var(--lve-purple-soft); }
.lve-nav-login:focus-visible { outline: 0; box-shadow: 0 0 0 3px rgba(94,58,168,0.24); }
.lve-nav-login:active:not(:disabled):not([aria-disabled="true"]) { transform: translateY(1px); }
.lve-nav-login:is(:disabled, [aria-disabled="true"]) { opacity: 0.48; cursor: not-allowed; transform: none; }
.lve-nav-burger {
  display: inline-flex; align-items: center; justify-content: center;
  width: 42px; height: 42px; border-radius: 10px; cursor: pointer;
  background: var(--lve-bg-2); border: 1px solid var(--lve-line); color: var(--lve-ink);
}
.lve-nav-burger svg { width: 20px; height: 20px; }

/* Mobile drawer */
.lve-drawer {
  position: fixed; inset: 0; z-index: 60;
  display: flex; flex-direction: column;
  background: color-mix(in oklab, var(--lve-bg) 96%, transparent);
  backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
  padding: 16px 24px 32px;
}
.lve-drawer-head { display: flex; align-items: center; justify-content: space-between; height: var(--lve-nav-h); }
.lve-drawer-close {
  width: 42px; height: 42px; border-radius: 10px; cursor: pointer;
  background: var(--lve-bg-2); border: 1px solid var(--lve-line); color: var(--lve-ink);
  display: inline-flex; align-items: center; justify-content: center;
}
.lve-drawer-links { display: flex; flex-direction: column; gap: 2px; margin-top: 12px; }
.lve-drawer-link {
  text-align: left; padding: 16px 6px; border: 0; border-bottom: 1px solid var(--lve-line);
  background: none; cursor: pointer; font: inherit; font-size: 18px; font-weight: 500; color: var(--lve-ink);
}
.lve-drawer-cta { margin-top: 24px; display: flex; flex-direction: column; gap: 12px; }
.lve-drawer-cta .lve-btn { width: 100%; justify-content: center; height: 48px; min-height: 48px; }

@media (min-width: 900px) {
  .lve-nav-links, .lve-nav-right { display: flex; }
  .lve-nav-burger { display: none; }
}

/* ── Section header triad (mono eyebrow → verb H2 → one-line sub) ──────── */
.lve-eyebrow {
  display: inline-flex; align-items: center; gap: 9px;
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: 0.15em; text-transform: uppercase;
  color: var(--lve-purple); margin-bottom: 14px;
}
.lve-eyebrow-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--lve-purple); box-shadow: 0 0 0 4px rgba(124,77,255,0.16); }
.lve-h2 { font-weight: 600; font-size: clamp(27px, 3.1vw, 40px); line-height: 1.08; letter-spacing: -0.03em; margin: 0; color: var(--lve-ink); }
.lve-h2 em { font-style: normal; color: var(--lve-purple); }
.lve-sub { max-width: 620px; margin: 14px 0 0; font-size: clamp(15px, 1.6vw, 17px); line-height: 1.6; color: var(--lve-ink-2); }
.lve-sechead { text-align: center; max-width: 720px; margin: 0 auto; }
.lve-sechead .lve-eyebrow, .lve-sechead .lve-sub { margin-left: auto; margin-right: auto; }

.lve-section { padding: clamp(64px, 8vh, 104px) 0; }

/* ── 1 · HERO ────────────────────────────────────────────────────────── */
.lve-hero { position: relative; padding: clamp(40px, 6vh, 72px) 0 clamp(56px, 8vh, 96px); overflow: hidden; }
.lve-hero-glow {
  position: absolute; z-index: 0; top: -140px; right: -80px; width: 620px; height: 620px;
  border-radius: 50%; pointer-events: none;
  background: radial-gradient(closest-side, rgba(124,77,255,0.16), transparent 72%);
  filter: blur(8px);
}
.lve-hero-grid {
  position: relative; z-index: 1;
  display: grid; grid-template-columns: 1fr; gap: 44px; align-items: center;
}
.lve-hero-kicker {
  display: inline-flex; align-items: center; gap: 10px;
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--lve-purple);
  margin-bottom: 20px;
}
.lve-hero-kicker-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--lve-purple); box-shadow: 0 0 0 4px rgba(124,77,255,0.16), 0 0 14px rgba(124,77,255,0.5); }
.lve-h1 {
  font-weight: 600; font-size: clamp(34px, 4.6vw, 56px); line-height: 1.03;
  letter-spacing: -0.035em; margin: 0 0 20px; color: var(--lve-ink); max-width: 16ch;
}
.lve-h1 em { font-style: normal; color: var(--lve-purple); }
.lve-hero-sub { max-width: 42ch; margin: 0 0 28px; font-size: clamp(15px, 1.7vw, 18px); line-height: 1.55; color: var(--lve-ink-2); }
.lve-hero-cta { display: flex; flex-wrap: wrap; gap: 12px; }

@media (min-width: 940px) {
  .lve-hero-grid { grid-template-columns: minmax(0, 1.02fr) minmax(0, 0.98fr); gap: 52px; }
}

/* ── HERO SCENE — the real jobs-board role card turns its agent ON, then
   candidates flow into a compact decision lane. Right-sized. ───────────── */
.lve-hero-scene-wrap { position: relative; z-index: 1; }
.lve-hs {
  position: relative; max-width: 440px; margin: 0 auto;
  display: flex; flex-direction: column; gap: 14px;
}
@media (min-width: 940px) { .lve-hs { margin-left: auto; margin-right: 0; } }

.lve-hs-glow {
  position: absolute; z-index: 0; left: -18px; right: -18px; top: -26px; height: 250px;
  border-radius: 36px; pointer-events: none; opacity: 0.2;
  background: radial-gradient(closest-side, rgba(124,77,255,0.32), transparent 72%); filter: blur(10px);
}
.lve-hs[data-armed] .lve-hs-glow { opacity: 0; }

.lve-hs-card {
  position: relative; z-index: 1;
  transition: filter 0.55s ease, box-shadow 0.55s ease, border-color 0.55s ease, opacity 0.55s ease;
}
.lve-hs-card:not(.agent-on) { filter: grayscale(0.5) opacity(0.92); }
.lve-hs-card.agent-on { box-shadow: 0 26px 64px -36px rgba(94,58,168,0.6); }

.lve-hs-pillbox {
  position: relative; display: inline-flex; align-items: center; justify-content: flex-end;
  flex-shrink: 0; min-width: 88px; min-height: 23px;
}
.lve-hs-pill-off { transition: opacity 0.35s ease; }
.lve-hs-pill-on { position: absolute; right: 0; top: 0; }
.lve-hs[data-armed] .lve-hs-pill-on { opacity: 0; }

.lve-hs-foot {
  color: var(--purple-2, var(--lve-purple-2)); font-weight: 500;
  display: inline-flex; align-items: center; gap: 5px; transition: opacity 0.5s ease;
}

.lve-hs-lane {
  position: relative; z-index: 1;
  border-radius: 14px; background: var(--lve-bg-2); border: 1px solid var(--lve-line);
  box-shadow: 0 22px 54px -42px rgba(94,58,168,0.5); padding: 12px 14px;
  display: flex; flex-direction: column; gap: 8px;
}
.lve-hs-lane-head { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9.5px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--lve-mute); }
.lve-hs-chip {
  display: flex; align-items: center; gap: 9px; padding: 7px 9px; border-radius: 10px;
  border: 1px solid var(--lve-line); background: color-mix(in oklab, var(--lve-bg) 55%, var(--lve-bg-2));
}
.lve-hs-chip.is-top { border-color: rgba(196,165,253,0.7); background: var(--lve-purple-soft); }
.lve-hs-chip-name { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 600; color: var(--lve-ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lve-hs-verdict { display: inline-flex; flex-shrink: 0; }
.lve-hs[data-armed] .lve-hs-chip { opacity: 0; }
.lve-hs[data-armed] .lve-hs-verdict { opacity: 0; transform: scale(0.7); }

.lve-hs-replay {
  align-self: flex-end; display: inline-flex; align-items: center; gap: 6px;
  height: 32px; min-height: 32px; padding: 0 14px; border-radius: 10px; border: 1px solid var(--lve-line);
  background: var(--lve-bg-2); color: var(--lve-ink-2); font: inherit; font-size: 12px; font-weight: 600;
  line-height: 1; cursor: pointer;
  transition: transform 0.1s ease, background 0.16s ease, color 0.16s ease, border-color 0.16s ease, opacity 0.16s ease;
}
.lve-hs-replay:hover:not(:disabled):not([aria-disabled="true"]) { color: var(--lve-purple-2); background: var(--lve-purple-soft); border-color: rgba(196,165,253,0.6); }
.lve-hs-replay:focus-visible { outline: 0; box-shadow: 0 0 0 3px rgba(94,58,168,0.24); }
.lve-hs-replay:active:not(:disabled):not([aria-disabled="true"]) { transform: translateY(1px); }
.lve-hs-replay:is(:disabled, [aria-disabled="true"]) { opacity: 0.48; cursor: not-allowed; transform: none; }

/* ── 2 · THE PROBLEM ─────────────────────────────────────────────────── */
.lve-problem { padding: clamp(52px, 7vh, 92px) 0; }
.lve-problem-inner { max-width: 760px; }
.lve-problem-lead {
  font-weight: 600; font-size: clamp(24px, 3.4vw, 38px); line-height: 1.16;
  letter-spacing: -0.03em; color: var(--lve-ink); margin: 6px 0 0;
}
.lve-problem-lead em { font-style: normal; color: var(--lve-purple); display: block; margin-top: 6px; }
.lve-problem-tail { max-width: 620px; margin: 18px 0 0; font-size: clamp(15px, 1.6vw, 17px); line-height: 1.6; color: var(--lve-ink-2); }

/* ── 3 · THE FUNNEL (shown once) ─────────────────────────────────────── */
.lve-funnel-stage { margin-top: 44px; }
.lve-fn {
  border-radius: 20px; background: var(--lve-bg-2); border: 1px solid var(--lve-line);
  box-shadow: 0 34px 90px -60px rgba(94,58,168,0.5); padding: clamp(18px, 3vw, 30px);
}
.lve-fn-cand { display: flex; align-items: center; gap: 12px; padding-bottom: 18px; margin-bottom: 20px; border-bottom: 1px solid var(--lve-line); }
.lve-fn-cand-body { flex: 1; min-width: 0; }
.lve-fn-cand-name { display: block; font-weight: 600; font-size: 15px; color: var(--lve-ink); }
.lve-fn-cand-role { font-size: 12px; color: var(--lve-mute); }
.lve-fn-track { position: relative; }
.lve-fn-rail { display: none; }
.lve-fn-steps { display: grid; grid-template-columns: 1fr; gap: 14px; }
.lve-fn-step {
  position: relative; z-index: 1;
  display: flex; flex-direction: column; gap: 8px; padding: 14px 12px;
  border-radius: 14px; border: 1px solid var(--lve-line);
  background: color-mix(in oklab, var(--lve-bg) 55%, var(--lve-bg-2));
}
.lve-fn-node {
  width: 34px; height: 34px; border-radius: 10px; display: inline-flex; align-items: center; justify-content: center;
  color: #fff; background: linear-gradient(135deg, var(--lve-purple), var(--lve-purple-2));
  box-shadow: 0 8px 18px -10px rgba(94,58,168,0.8), inset 0 1px 0 rgba(255,255,255,0.25);
}
.lve-fn-n { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; letter-spacing: 0.1em; color: var(--lve-mute); }
.lve-fn-label { font-weight: 600; font-size: 14px; letter-spacing: -0.01em; color: var(--lve-ink); }
.lve-fn-glimpse { margin-top: 2px; min-height: 24px; display: flex; align-items: center; }
.lve-fn-tag { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; letter-spacing: 0.04em; color: var(--lve-purple); background: var(--lve-purple-soft); padding: 3px 9px; border-radius: 999px; }
.lve-fn-tag.is-synced { color: #fff; background: linear-gradient(135deg, var(--lve-purple), var(--lve-purple-2)); }
.lve-fn-ev { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 500; color: var(--lve-ink-2); }
.lve-fn-ev svg { color: var(--lve-purple); }
.lve-fn-score { font-weight: 600; font-size: 22px; letter-spacing: -0.02em; color: var(--lve-purple); font-variant-numeric: tabular-nums; }
.lve-fn-score em { font-style: normal; font-size: 11px; color: var(--lve-mute); font-weight: 500; }

/* Armed (JS mounted, motion allowed) → steps hidden + rail empty until the
   scroll-in trigger adds .is-playing, which runs the one-shot CSS entrance
   (fill:both, so it always resolves to the final state — it can't get stuck the
   way an interrupted useAnimate timeline can). */
.lve-fn[data-armed] .lve-fn-step { opacity: 0; }
.lve-fn[data-armed] .lve-fn-rail-fill { transform: scaleX(0); }
.lve-fn.is-playing .lve-fn-step {
  animation: lveFnStepIn 0.5s cubic-bezier(0.16, 1, 0.3, 1) both;
}
.lve-fn.is-playing .lve-fn-step:nth-child(1) { animation-delay: 0.05s; }
.lve-fn.is-playing .lve-fn-step:nth-child(2) { animation-delay: 0.24s; }
.lve-fn.is-playing .lve-fn-step:nth-child(3) { animation-delay: 0.43s; }
.lve-fn.is-playing .lve-fn-step:nth-child(4) { animation-delay: 0.62s; }
.lve-fn.is-playing .lve-fn-step:nth-child(5) { animation-delay: 0.81s; }
.lve-fn.is-playing .lve-fn-rail-fill {
  animation: lveFnRail 1.5s cubic-bezier(0.16, 1, 0.3, 1) both;
}
@keyframes lveFnStepIn { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }
@keyframes lveFnRail { from { transform: scaleX(0); } to { transform: scaleX(1); } }

@media (min-width: 860px) {
  .lve-fn-steps { grid-template-columns: repeat(5, 1fr); gap: 12px; }
  .lve-fn-rail {
    display: block; position: absolute; z-index: 0; top: 31px; left: 9%; right: 9%; height: 2px;
    border-radius: 2px; background: var(--lve-line); overflow: hidden;
  }
  .lve-fn-rail-fill {
    display: block; height: 100%; width: 100%; transform-origin: left;
    background: linear-gradient(90deg, var(--lve-purple-2), var(--lve-purple), var(--lve-lav));
  }
}

/* ── "Live component" frame around embedded real product surfaces ─────── */
.lve-frame {
  overflow: hidden; border-radius: 16px; background: var(--lve-bg-2);
  border: 1px solid var(--lve-line);
  box-shadow: 0 34px 80px -46px rgba(94,58,168,0.5), 0 2px 8px -4px rgba(21,18,26,0.06);
}
.lve-frame-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 11px 16px; border-bottom: 1px solid var(--lve-line);
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px; color: var(--lve-mute);
}
.lve-frame-dot { width: 9px; height: 9px; border-radius: 50%; background: rgba(21,18,26,0.14); }
.lve-frame-dot:nth-child(1) { background: #e6b8c8; }
.lve-frame-dot:nth-child(2) { background: #e8cfa0; }
.lve-frame-dot:nth-child(3) { background: #b9d8bf; }
.lve-frame-path { margin-left: 6px; }
.lve-frame-live { margin-left: auto; font-size: 10px; font-weight: 600; letter-spacing: 0.04em; color: var(--lve-purple); background: var(--lve-purple-soft); padding: 2px 9px; border-radius: 999px; }
.lve-frame-body { padding: clamp(14px, 2.2vw, 22px); }
/* The embedded real components self-size; keep them from forcing sideways
   scroll inside the frame on narrow viewports. */
.lve-frame-body > * { max-width: 100%; }

/* ── 4 · THE WEDGE — real 5-Ds scorecard, right-sized ─────────────────── */
.lve-wedge-stage { margin: 44px auto 0; max-width: 560px; }

/* 5-Ds scorecard reveal contract. While armed ([data-lve-sc], set only when JS
   is mounted and motion is allowed) the five rows are hidden and every score bar
   is zeroed; the scoped Motion timeline reveals the rows (fade+rise), fills the
   bars 0→value, and a rAF loop ticks each score up. Under reduced motion no
   attribute is set — the scorecard renders complete. */
.lve [data-lve-sc] .sc5-row { opacity: 0; }
.lve [data-lve-sc] .sc5-bar > i { transform-origin: left center; transform: scaleX(0); }

/* ── 5 · YOU STAY IN CONTROL ─────────────────────────────────────────── */
.lve-control { border-radius: 24px; background: color-mix(in oklab, var(--lve-purple-soft) 55%, var(--lve-bg-2)); border: 1px solid var(--lve-line); padding: clamp(28px, 4.5vw, 52px); }
.lve-control .lve-h2 { margin: 4px 0 0; }
.lve-control .lve-sub { margin-top: 14px; }
.lve-control-grid { display: grid; grid-template-columns: 1fr; gap: 32px; align-items: start; }
.lve-control-points { display: flex; flex-direction: column; gap: 16px; margin-top: 26px; }
.lve-control-point { display: grid; grid-template-columns: 24px 1fr; gap: 12px; align-items: start; }
.lve-control-check { width: 22px; height: 22px; border-radius: 7px; background: var(--lve-purple); color: #fff; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; margin-top: 1px; }
.lve-control-check svg { width: 13px; height: 13px; }
.lve-control-point-h { font-weight: 600; font-size: 15px; color: var(--lve-ink); }
.lve-control-point-p { font-size: 13.5px; line-height: 1.5; color: var(--lve-ink-2); margin-top: 2px; }
.lve-control-artifact { position: relative; }
@media (min-width: 900px) { .lve-control-grid { grid-template-columns: minmax(0, 1.02fr) minmax(0, 0.98fr); gap: 44px; } }

/* Compact real AgentDecisionCard glimpse — drop its own chrome so it sits flush
   in the browser frame; hide the deep-link row so only the glimpse remains
   (ScoreRing + name/role, the agent-recommends verdict slab, requirement bars). */
.lve-frame--decision { max-width: 400px; margin: 0 auto; }
.lve-frame--decision .lve-frame-body { padding: 16px 18px 18px; }
.lve-frame--decision .rq-hybrid-detail { border: 0; background: transparent; border-radius: 0; padding: 0; }
.lve-frame--decision .rq-detail-links { display: none !important; }
.lve-frame--decision .rq-rec { margin-top: 14px; }

/* ── 6 · PROOF + CLOSE ───────────────────────────────────────────────── */
.lve-proof-band { margin-top: 44px; border-radius: 22px; background: var(--lve-ink); color: var(--lve-bg); padding: clamp(32px, 5vw, 52px); }
.lve-proof-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 28px 20px; }
.lve-stat { text-align: center; }
.lve-stat-big { font-weight: 600; font-size: clamp(26px, 3.4vw, 40px); letter-spacing: -0.03em; color: #fff; font-variant-numeric: tabular-nums; line-height: 1; }
.lve-stat-big em { font-style: normal; color: var(--lve-lav); }
.lve-stat-cap { margin-top: 10px; font-size: 12.5px; line-height: 1.5; color: color-mix(in oklab, var(--lve-bg) 70%, transparent); }
@media (min-width: 760px) { .lve-proof-grid { grid-template-columns: repeat(4, 1fr); } }

/* ── CLOSING CTA + FOOTER (reused production treatment) ───────────────── */
.lve-footer { position: relative; z-index: 2; }

/* Reduced-motion: kill any residual keyframes. Scenes already render their final
   composed state because JS never arms [data-armed] under reduce. */
@media (prefers-reduced-motion: reduce) {
  .lve *, .lve *::before, .lve *::after { animation: none !important; }
}
`;
