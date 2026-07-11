// Scoped CSS for LandingVariantE ("Watch it work" — conventional B2B structure),
// injected via a <style> tag inside the `.lve` root. Kept as a string module (not
// a .css import) so the whole variant lazy-loads as one chunk with its component
// and never leaks styles into the rest of the app — every selector is `.lve…`.
//
// LIGHT theme. Palette is the exact Taali light-purple family variants C/D use
// (hardcoded, not the brand token) so the look holds regardless of the app's
// active brand/theme. Purple only — never red/amber/green. No CSS zoom. No
// horizontal scroll at 1024/1440. Mobile-first; nothing depends on exact vh.
//
// MOTION MODEL — this variant does NOT scroll-scrub. Motion (motion.dev) drives:
//   • Reveal / Stagger  — one-shot section entrances (whileInView, once).
//   • Autoplay mocks     — self-playing loops that arm an initial-hidden state via
//                          `[data-animated]` and play only while in view.
// The `[data-animated]` attribute is the contract between JS and CSS: when the
// mock will animate (JS mounted, not reduced-motion) its animatable children are
// hidden by the rules below and Motion reveals them. With NO `[data-animated]`
// (reduced-motion, or JS not yet armed) every mock renders in its FINAL, legible
// state — an inert-but-complete page, which is the one acceptable fallback.
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
  display: inline-flex; align-items: center; gap: 8px;
  height: 46px; padding: 0 22px; border-radius: 999px;
  font: inherit; font-size: 14px; font-weight: 600; cursor: pointer;
  border: 1px solid transparent; white-space: nowrap;
  transition: transform 0.18s ease, box-shadow 0.3s ease, background 0.25s ease, border-color 0.25s ease;
}
.lve-btn:active { transform: translateY(1px) scale(0.98); }
.lve-btn--primary {
  color: #fff; background: linear-gradient(135deg, var(--lve-purple), var(--lve-purple-2));
  box-shadow: 0 12px 30px -12px rgba(94,58,168,0.55), inset 0 1px 0 rgba(255,255,255,0.2);
}
.lve-btn--primary:hover { box-shadow: 0 16px 40px -12px rgba(94,58,168,0.7); }
.lve-btn--ghost { color: var(--lve-purple); background: var(--lve-bg-2); border-color: var(--lve-line); }
.lve-btn--ghost:hover { background: var(--lve-purple-soft); border-color: rgba(196,165,253,0.6); }
.lve-btn--sm { height: 40px; padding: 0 18px; font-size: 13px; }

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
  background: none; border: 0; cursor: pointer; font: inherit; font-size: 14px; font-weight: 500;
  color: var(--lve-ink-2); padding: 8px 6px; transition: color 0.2s ease;
}
.lve-nav-login:hover { color: var(--lve-purple); }
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
.lve-drawer-cta .lve-btn { width: 100%; justify-content: center; height: 50px; }

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

/* ── HERO ────────────────────────────────────────────────────────────── */
.lve-hero { position: relative; padding: clamp(40px, 6vh, 72px) 0 clamp(56px, 8vh, 96px); overflow: hidden; }
.lve-hero-glow {
  position: absolute; z-index: 0; top: -140px; right: -80px; width: 620px; height: 620px;
  border-radius: 50%; pointer-events: none;
  background: radial-gradient(closest-side, rgba(124,77,255,0.18), transparent 72%);
  filter: blur(8px);
}
.lve-hero-grid {
  position: relative; z-index: 1;
  display: grid; grid-template-columns: 1fr; gap: 40px; align-items: center;
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
.lve-hero-sub { max-width: 40ch; margin: 0 0 28px; font-size: clamp(15px, 1.7vw, 18px); line-height: 1.55; color: var(--lve-ink-2); }
.lve-hero-cta { display: flex; flex-wrap: wrap; gap: 12px; }

/* FIX 1 — the REAL agent-ON strip (.abar) in the hero. The strip's own look /
   animation comes from the global 13-page-hero-agentheader.css + the Taali
   tokens (resolved via data-brand="taali" on the .lve root); we only re-size it
   to fill the hero column instead of the header's fixed clamp width. */
.lve-hero-abar { margin-top: 28px; max-width: 520px; }
.lve-hero-abar .abar { width: 100%; }

/* Hero product card frame — reveals + lifts a touch the moment the agent flips
   ON (the frame itself is always mounted; ON is the "populated" beat). */
.lve-hero-mock-wrap { position: relative; z-index: 1; }
.lve-hero-mock-wrap .lve-frame {
  transition: box-shadow 0.55s ease, transform 0.55s ease, border-color 0.55s ease;
}
.lve-hero-mock-wrap.is-on .lve-frame {
  box-shadow: 0 48px 100px -46px rgba(94,58,168,0.6), 0 2px 8px -4px rgba(21,18,26,0.06);
  border-color: rgba(196,165,253,0.6);
}

@media (min-width: 940px) {
  .lve-hero-grid { grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr); gap: 56px; }
}

/* ── TRUST STRIP (marquee logo wall) ─────────────────────────────────── */
.lve-trust { padding: 34px 0; border-top: 1px solid var(--lve-line); border-bottom: 1px solid var(--lve-line); background: color-mix(in oklab, var(--lve-bg-2) 55%, transparent); }
.lve-trust-label { text-align: center; font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--lve-mute); margin-bottom: 20px; }
.lve-marquee { position: relative; overflow: hidden; -webkit-mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent); mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent); }
.lve-marquee-track { display: flex; width: max-content; gap: 56px; animation: lveMarquee 32s linear infinite; }
.lve-marquee:hover .lve-marquee-track { animation-play-state: paused; }
.lve-marquee-item { display: inline-flex; align-items: center; gap: 9px; opacity: 0.5; filter: grayscale(1); white-space: nowrap; font-weight: 600; font-size: 16px; letter-spacing: -0.01em; color: var(--lve-ink-2); }
.lve-marquee-glyph { width: 20px; height: 20px; border-radius: 6px; background: currentColor; opacity: 0.85; }
.lve-marquee-glyph.round { border-radius: 50%; }
.lve-marquee-glyph.diamond { border-radius: 4px; transform: rotate(45deg); }
@keyframes lveMarquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }

/* ── PRODUCT IN ACTION (signature autoplay) ──────────────────────────── */
.lve-run { padding-top: clamp(64px, 8vh, 104px); }
.lve-run-stage {
  margin-top: 44px; position: relative;
  border-radius: 22px; background: var(--lve-bg-2); border: 1px solid var(--lve-line);
  box-shadow: 0 40px 100px -60px rgba(94,58,168,0.5); padding: clamp(20px, 3vw, 34px);
  display: grid; grid-template-columns: 1fr; gap: 22px; align-items: stretch;
}
.lve-run-col { display: flex; flex-direction: column; gap: 12px; }
.lve-run-coltitle { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--lve-mute); }
.lve-run-cards { display: flex; flex-direction: column; gap: 10px; }
.lve-run-card {
  position: relative; border-radius: 12px; background: var(--lve-bg-2); border: 1px solid var(--lve-line);
  box-shadow: 0 12px 30px -24px rgba(21,18,26,0.4); padding: 11px 13px;
  display: flex; flex-direction: column; gap: 7px;
}
.lve-run-card.reject { opacity: 0.35; }
.lve-run-card-name { height: 8px; width: 54%; border-radius: 4px; background: var(--lve-purple); opacity: 0.85; }
.lve-run-card-line { height: 6px; border-radius: 4px; background: rgba(21,18,26,0.10); }
.lve-run-card-line.s2 { width: 86%; }
.lve-run-chip {
  position: absolute; top: -8px; right: 10px;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9px; letter-spacing: 0.02em;
  color: var(--lve-purple); background: var(--lve-purple-soft); border: 1px solid rgba(196,165,253,0.6);
  padding: 2px 7px; border-radius: 999px; white-space: nowrap;
}
.lve-run-card:not(.reject) .lve-run-chip { display: none; }

.lve-run-transcript { border-radius: 14px; background: color-mix(in oklab, var(--lve-purple-soft) 40%, var(--lve-bg-2)); border: 1px solid var(--lve-line); padding: 14px; display: flex; flex-direction: column; gap: 9px; }
.lve-run-turn { display: flex; flex-direction: column; gap: 3px; padding: 9px 11px; border-radius: 10px; }
.lve-run-turn--ai { background: rgba(21,18,26,0.04); }
.lve-run-turn--cand { background: var(--lve-bg-2); border: 1px solid rgba(196,165,253,0.5); }
.lve-run-turn-who { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lve-purple); }
.lve-run-turn-text { font-size: 12px; line-height: 1.45; color: var(--lve-ink); }
.lve-run-trap { align-self: flex-start; font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9.5px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #fff; padding: 4px 9px; border-radius: 999px; background: linear-gradient(135deg, var(--lve-purple), var(--lve-purple-2)); }

.lve-run-decision { border-radius: 14px; background: var(--lve-bg-2); border: 1px solid var(--lve-line); box-shadow: 0 20px 50px -40px rgba(94,58,168,0.5); padding: 15px; }
.lve-run-dname { font-weight: 600; font-size: 15px; color: var(--lve-ink); }
.lve-run-drole { font-size: 11.5px; color: var(--lve-mute); margin-top: 1px; }
.lve-run-bars { margin: 13px 0; display: flex; flex-direction: column; gap: 8px; }
.lve-run-bar { display: grid; grid-template-columns: 84px 1fr; gap: 9px; align-items: center; }
.lve-run-bar-label { font-size: 10.5px; color: var(--lve-ink-2); }
.lve-run-bar-track { height: 5px; border-radius: 999px; background: rgba(21,18,26,0.06); overflow: hidden; }
.lve-run-bar-fill { display: block; height: 100%; border-radius: 999px; transform-origin: left; background: linear-gradient(90deg, var(--lve-purple-2), var(--lve-purple)); }
.lve-run-scorerow { display: flex; align-items: baseline; gap: 9px; }
.lve-run-score { font-weight: 600; font-size: 30px; letter-spacing: -0.03em; color: var(--lve-purple); font-variant-numeric: tabular-nums; }
.lve-run-score-cap { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lve-mute); }
.lve-run-verdict { margin-top: 12px; display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; font-weight: 600; color: #fff; background: linear-gradient(135deg, var(--lve-purple), var(--lve-purple-2)); padding: 6px 12px; border-radius: 999px; }
.lve-run-lane { margin-top: 12px; border-radius: 12px; border: 1px dashed rgba(94,58,168,0.4); background: rgba(237,229,248,0.35); padding: 12px; }
.lve-run-lane-head { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lve-purple); margin-bottom: 8px; }
.lve-run-audit { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px; line-height: 1.6; color: var(--lve-ink-2); word-break: break-word; }
.lve-run-caption { text-align: center; margin: 22px auto 0; max-width: 560px; font-size: 13.5px; line-height: 1.55; color: var(--lve-mute); }

@media (min-width: 920px) {
  .lve-run-stage { grid-template-columns: 0.9fr 1.1fr 1.1fr; gap: 26px; }
}

/* Autoplay arm/reveal contract — hidden only while [data-animated] is set. */
.lve-mock[data-animated] .lve-anim { opacity: 0; }
.lve-mock[data-animated] .lve-anim-bar { transform: scaleX(0); }

/* ── FIX 2 — "live component" frame around embedded real product surfaces. ── */
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

/* ── VALUE PILLARS — FIX 3: bespoke, backed by real product micro-visuals. ── */
.lve-pillars-grid { margin-top: 44px; display: grid; grid-template-columns: 1fr; gap: 18px; }
.lve-pillar {
  border-radius: 16px; background: var(--lve-bg-2); border: 1px solid var(--lve-line);
  box-shadow: 0 16px 44px -34px rgba(21,18,26,0.35); padding: 20px;
  display: flex; flex-direction: column;
}
.lve-pillar-visual {
  border-radius: 12px; border: 1px solid var(--lve-line);
  background: color-mix(in oklab, var(--lve-bg) 60%, var(--lve-bg-2));
  padding: 14px; margin-bottom: 18px; min-height: 132px;
  display: flex; flex-direction: column; justify-content: center;
}
.lve-pillar-eyebrow {
  display: inline-flex; align-items: center; gap: 8px;
  font-family: 'Geist Mono', ui-monospace, monospace;
  font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--lve-purple);
  margin-bottom: 10px;
}
.lve-pillar-h { font-weight: 600; font-size: 17px; letter-spacing: -0.01em; color: var(--lve-ink); margin: 0 0 8px; }
.lve-pillar-p { font-size: 13.5px; line-height: 1.55; color: var(--lve-ink-2); margin: 0; }
@media (min-width: 820px) { .lve-pillars-grid { grid-template-columns: repeat(3, 1fr); } }

/* Pillar micro-visuals — composed from the REAL product atoms (feed rows, the
   5-Ds axes, a decision-card header). --purple / --font-mono etc. resolve via
   data-brand="taali" on the .lve root. */
.lve-pv-screen { gap: 8px; }
.lve-pv-row { display: flex; align-items: center; gap: 8px; }
.lve-pv-name { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 600; color: var(--lve-ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.lve-pv-ds { gap: 7px; }
.lve-pv-ds-row { display: grid; grid-template-columns: 74px 1fr 22px; gap: 8px; align-items: center; }
.lve-pv-ds-name { font-size: 11px; color: var(--lve-ink-2); }
.lve-pv-ds-track { height: 6px; border-radius: 999px; background: rgba(21,18,26,0.06); overflow: hidden; }
.lve-pv-ds-fill { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--purple-2, #4a2d80), var(--purple, #5e3aa8)); }
.lve-pv-ds-val { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px; color: var(--purple, #5e3aa8); text-align: right; }

.lve-pv-decide { flex-direction: row; align-items: center; gap: 14px; }
.lve-pv-decide-body { min-width: 0; }
.lve-pv-decide-name { font-weight: 600; font-size: 15px; color: var(--lve-ink); }
.lve-pv-decide-role { font-size: 11.5px; color: var(--lve-mute); margin: 1px 0 9px; }
.lve-pv-decide-verdict { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.lve-pv-decide-rec { display: inline-flex; align-items: center; gap: 4px; font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; color: var(--lve-mute); }

/* ── DEEP FEATURE BANDS ──────────────────────────────────────────────── */
.lve-bands { display: flex; flex-direction: column; gap: clamp(56px, 8vh, 96px); }
.lve-band { display: grid; grid-template-columns: 1fr; gap: 32px; align-items: center; }
.lve-band-copy { max-width: 46ch; }
.lve-band-visual { position: relative; }
@media (min-width: 900px) {
  .lve-band { grid-template-columns: 1fr 1fr; gap: 56px; }
  .lve-band.flip .lve-band-copy { order: 2; }
  .lve-band.flip .lve-band-visual { order: 1; }
}

/* Shared mini-mock card frame for the bands */
.lve-mini {
  border-radius: 16px; background: var(--lve-bg-2); border: 1px solid var(--lve-line);
  box-shadow: 0 30px 80px -56px rgba(94,58,168,0.5); padding: 20px;
}
.lve-mini-head { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--lve-mute); }
.lve-mini-head-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--lve-purple); box-shadow: 0 0 8px rgba(124,77,255,0.6); }

/* Screen mock — CV rows gated with evidence chips */
.lve-cvrow { display: flex; align-items: center; gap: 12px; padding: 11px 12px; border-radius: 11px; border: 1px solid var(--lve-line); background: var(--lve-bg-2); margin-bottom: 9px; }
.lve-cvrow:last-child { margin-bottom: 0; }
.lve-cvrow.reject { opacity: 0.5; }
.lve-cvrow-body { flex: 1; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.lve-cvrow-name { height: 8px; width: 44%; border-radius: 4px; background: var(--lve-purple); opacity: 0.8; }
.lve-cvrow-line { height: 6px; width: 80%; border-radius: 4px; background: rgba(21,18,26,0.10); }
.lve-cvrow-chip { flex-shrink: 0; font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9px; letter-spacing: 0.02em; padding: 3px 8px; border-radius: 999px; white-space: nowrap; }
.lve-cvrow-chip.pass { color: var(--lve-purple); background: var(--lve-purple-soft); border: 1px solid rgba(196,165,253,0.6); }
.lve-cvrow-chip.fail { color: var(--lve-mute); background: rgba(21,18,26,0.04); border: 1px solid var(--lve-line); }

/* Assess mock — 5-Ds scorecard filling */
.lve-ds { display: flex; flex-direction: column; gap: 12px; }
.lve-ds-row { display: grid; grid-template-columns: 96px 1fr 30px; gap: 12px; align-items: center; }
.lve-ds-name { font-size: 12.5px; font-weight: 500; color: var(--lve-ink); }
.lve-ds-track { height: 6px; border-radius: 999px; background: rgba(21,18,26,0.06); overflow: hidden; }
.lve-ds-fill { display: block; height: 100%; border-radius: 999px; transform-origin: left; background: linear-gradient(90deg, var(--lve-purple-2), var(--lve-purple)); }
.lve-ds-val { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 11px; color: var(--lve-mute); text-align: right; }

/* Decide mock — reuses .lve-run-decision look; declared above. */

/* Hand-back mock — ATS lanes + audit line */
.lve-lanes { display: flex; gap: 10px; margin-bottom: 14px; }
.lve-lane { flex: 1; border-radius: 10px; border: 1px solid var(--lve-line); background: var(--lve-bg-2); padding: 10px; text-align: center; }
.lve-lane.active { border-color: rgba(196,165,253,0.7); background: var(--lve-purple-soft); }
.lve-lane-name { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--lve-mute); }
.lve-lane.active .lve-lane-name { color: var(--lve-purple); }
.lve-lane-dot { width: 8px; height: 8px; border-radius: 50%; margin: 8px auto 0; background: rgba(21,18,26,0.12); }
.lve-lane.active .lve-lane-dot { background: var(--lve-purple); box-shadow: 0 0 8px rgba(124,77,255,0.6); }
.lve-audit-line { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 10.5px; line-height: 1.7; color: var(--lve-ink-2); border-top: 1px solid var(--lve-line); padding-top: 12px; }

/* ── HOW IT WORKS ────────────────────────────────────────────────────── */
.lve-steps { margin-top: 44px; display: grid; grid-template-columns: 1fr; gap: 18px; }
.lve-step { position: relative; border-radius: 16px; background: var(--lve-bg-2); border: 1px solid var(--lve-line); padding: 24px; box-shadow: 0 16px 44px -36px rgba(21,18,26,0.35); }
.lve-step-n { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 12px; letter-spacing: 0.1em; color: var(--lve-purple); }
.lve-step-h { font-weight: 600; font-size: 16px; letter-spacing: -0.01em; margin: 10px 0 7px; color: var(--lve-ink); }
.lve-step-p { font-size: 13.5px; line-height: 1.55; color: var(--lve-ink-2); margin: 0; }
@media (min-width: 820px) { .lve-steps { grid-template-columns: repeat(3, 1fr); } }

/* ── TRUST / CONTROL ─────────────────────────────────────────────────── */
.lve-control { border-radius: 24px; background: color-mix(in oklab, var(--lve-purple-soft) 55%, var(--lve-bg-2)); border: 1px solid var(--lve-line); padding: clamp(32px, 5vw, 56px); }
.lve-control-grid { display: grid; grid-template-columns: 1fr; gap: 32px; align-items: start; }
.lve-control-points { display: flex; flex-direction: column; gap: 16px; }
.lve-control-point { display: grid; grid-template-columns: 24px 1fr; gap: 12px; align-items: start; }
.lve-control-check { width: 22px; height: 22px; border-radius: 7px; background: var(--lve-purple); color: #fff; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; margin-top: 1px; }
.lve-control-check svg { width: 13px; height: 13px; }
.lve-control-point-h { font-weight: 600; font-size: 15px; color: var(--lve-ink); }
.lve-control-point-p { font-size: 13.5px; line-height: 1.5; color: var(--lve-ink-2); margin-top: 2px; }
@media (min-width: 900px) { .lve-control-grid { grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.08fr); gap: 48px; } }

/* ── STATS BAND (number tickers) ─────────────────────────────────────── */
.lve-stats { border-radius: 22px; background: var(--lve-ink); color: var(--lve-bg); padding: clamp(32px, 5vw, 52px); }
.lve-stats-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 28px 20px; }
.lve-stat { text-align: center; }
.lve-stat-big { font-weight: 600; font-size: clamp(26px, 3.4vw, 40px); letter-spacing: -0.03em; color: #fff; font-variant-numeric: tabular-nums; line-height: 1; }
.lve-stat-big em { font-style: normal; color: var(--lve-lav); }
.lve-stat-cap { margin-top: 10px; font-size: 12.5px; line-height: 1.5; color: color-mix(in oklab, var(--lve-bg) 70%, transparent); }
@media (min-width: 760px) { .lve-stats-grid { grid-template-columns: repeat(4, 1fr); } }

/* ── INTEGRATIONS ────────────────────────────────────────────────────── */
.lve-integrations-row { margin-top: 40px; display: grid; grid-template-columns: 1fr; gap: 16px; }
.lve-integration { display: flex; align-items: center; gap: 14px; border-radius: 14px; background: var(--lve-bg-2); border: 1px solid var(--lve-line); padding: 18px 20px; box-shadow: 0 14px 40px -34px rgba(21,18,26,0.35); }
.lve-integration-glyph { width: 38px; height: 38px; border-radius: 10px; flex-shrink: 0; background: var(--lve-purple-soft); position: relative; }
.lve-integration-glyph::after { content: ''; position: absolute; inset: 11px; border-radius: 5px; background: var(--lve-purple); opacity: 0.85; }
.lve-integration-glyph.round::after { border-radius: 50%; }
.lve-integration-glyph.diamond::after { border-radius: 3px; transform: rotate(45deg); }
.lve-integration-name { font-weight: 600; font-size: 15px; color: var(--lve-ink); }
.lve-integration-sub { font-size: 12.5px; color: var(--lve-mute); margin-top: 1px; }
@media (min-width: 760px) { .lve-integrations-row { grid-template-columns: repeat(3, 1fr); } }

/* ── CLOSING CTA + FOOTER (reused production treatment) ───────────────── */
.lve-footer { position: relative; z-index: 2; }

/* Reduced-motion: kill marquee + any residual keyframes. Mocks already render
   their final composed state because JS drops [data-animated] under reduce. */
@media (prefers-reduced-motion: reduce) {
  .lve *, .lve *::before, .lve *::after { animation: none !important; }
  .lve-marquee-track { animation: none !important; }
}
`;
