// Scoped CSS for LandingVariantF — the "Vivid Purple" design handoff, recreated
// pixel-accurately. Ported from the handoff's taali-brand.css (design system) +
// vivid.html (page-specific styles), with EVERY selector scoped under the `.lvf`
// root so nothing leaks into the app and the look holds regardless of the app's
// active brand/theme. The handoff `:root` tokens live on `.lvf`; keyframes are
// renamed (`lvfAgentFlow`, `lvfStamp`) to avoid global collision; the gradient
// SHIMMER animations are gated behind `prefers-reduced-motion: no-preference`
// (the gradients themselves always paint — only the motion is gated).
//
// Injected via a <style> tag inside the `.lvf` root so the whole variant
// lazy-loads as one chunk with its component. LIGHT theme, purple family only —
// the reject state is a muted grey, never red. No CSS zoom; no horizontal scroll.
export const VARIANT_F_CSS = `
.lvf {
  /* ── palette (founder-approved), hardcoded ── */
  --purple:       #5e3aa8;
  --purple-deep:  #4a2d80;
  --purple-soft:  #ede5f8;
  --lavender:     #c4a5fd;
  --bg:           #f7f4fb;
  --surface:      #ffffff;
  --ink:          #15121a;
  --ink-2:        #3a3343;
  --mute:         #8b8595;
  --line:         #e8e2ee;

  --agent-on:      linear-gradient(150deg, #3a1d6e, #241147);
  --agent-on-flow: linear-gradient(120deg, #3a1d6e, #6a3fb8, #2a1556, #4a2a8a, #3a1d6e);

  --sh-sm: 0 1px 2px rgba(21,18,26,.05), 0 1px 0 rgba(21,18,26,.02);
  --sh-md: 0 2px 4px rgba(21,18,26,.04), 0 12px 28px -10px rgba(21,18,26,.12);
  --sh-lg: 0 4px 8px rgba(21,18,26,.04), 0 30px 60px -22px rgba(74,45,128,.22);
  --sh-glow: 0 20px 60px -18px rgba(94,58,168,.45);

  --r-sm: 10px;
  --r:    14px;
  --r-lg: 18px;
  --r-xl: 24px;

  --font: "Geist", system-ui, -apple-system, sans-serif;
  --mono: "Geist Mono", ui-monospace, "SF Mono", monospace;

  --maxw: 1200px;
  --pad:  40px;

  /* the vivid-direction page-background wash over --bg */
  background:
    radial-gradient(60% 40% at 88% 0%, rgba(196,165,253,.28), transparent 60%),
    radial-gradient(50% 40% at 0% 8%, rgba(94,58,168,.12), transparent 55%),
    var(--bg);
  color: var(--ink);
  font-family: var(--font);
  font-feature-settings: "ss01","cv01","cv11";
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  line-height: 1.5;
  min-height: 100vh;
  overflow-x: hidden;
}
.lvf *, .lvf *::before, .lvf *::after { box-sizing: border-box; }
.lvf a { color: var(--purple); text-decoration: none; }
.lvf a:hover { color: var(--purple-deep); }
.lvf button { font: inherit; }
.lvf img { max-width: 100%; display: block; }

.lvf .wrap { width: 100%; max-width: var(--maxw); margin: 0 auto; padding: 0 var(--pad); }

/* ── eyebrow (mono label) ── */
.lvf .eyebrow {
  font-family: var(--mono);
  font-size: 12px;
  letter-spacing: .18em;
  text-transform: uppercase;
  color: var(--purple);
  font-weight: 500;
  display: inline-block;
}
.lvf .eyebrow.mute { color: var(--mute); }

/* ── headline helpers ── */
.lvf .display {
  font-family: var(--font);
  font-weight: 600;
  letter-spacing: -.035em;
  line-height: 1.02;
  color: var(--ink);
  margin: 0;
  text-wrap: balance;
}
.lvf .display .accent { color: var(--purple); }
.lvf .lede { color: var(--ink-2); font-size: 18px; line-height: 1.6; margin: 0; }

.lvf .grad-text {
  background: linear-gradient(96deg, #6a3fb8, #5e3aa8 40%, #8b5cf6);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}

/* ── buttons ── */
.lvf .btn {
  --lvf-btn-bg: var(--surface);
  --lvf-btn-color: var(--ink-2);
  --lvf-btn-border: var(--line);
  --lvf-btn-shadow: 0 0 transparent;
  --lvf-btn-hover-bg: var(--purple-soft);
  --lvf-btn-hover-color: var(--purple-deep);
  --lvf-btn-hover-border: color-mix(in oklab, var(--purple) 34%, var(--line));

  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 40px;
  min-height: 40px;
  padding: 0 20px;
  border: 1px solid var(--lvf-btn-border) !important;
  border-radius: 10px;
  background: var(--lvf-btn-bg) !important;
  color: var(--lvf-btn-color) !important;
  box-shadow: var(--lvf-btn-shadow) !important;
  font-size: 15px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -.01em;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  transition: transform .1s ease, background .16s ease, border-color .16s, color .16s, box-shadow .16s, opacity .16s;
}
.lvf .btn:hover:not(:disabled):not([aria-disabled="true"]) {
  background: var(--lvf-btn-hover-bg) !important;
  color: var(--lvf-btn-hover-color) !important;
  border-color: var(--lvf-btn-hover-border) !important;
}
.lvf .btn:focus-visible {
  outline: 0;
  box-shadow: 0 0 0 3px rgba(94,58,168,.24), var(--lvf-btn-shadow) !important;
}
.lvf .btn:active:not(:disabled):not([aria-disabled="true"]) { transform: translateY(1px); }
.lvf .btn:is(:disabled, [aria-disabled="true"]) {
  opacity: .48;
  cursor: not-allowed;
  transform: none;
}
.lvf .btn-primary,
.lvf .cta-band.dark .btn:not(.btn-outline) {
  --lvf-btn-bg: var(--purple);
  --lvf-btn-color: var(--surface);
  --lvf-btn-border: var(--purple);
  --lvf-btn-shadow: var(--sh-sm);
  --lvf-btn-hover-bg: var(--purple-deep);
  --lvf-btn-hover-color: var(--surface);
  --lvf-btn-hover-border: var(--purple-deep);
}
.lvf .btn-primary .arw { transition: transform .18s; }
.lvf .btn-primary:hover:not(:disabled) .arw { transform: translateX(3px); }
.lvf .btn-outline { --lvf-btn-bg: var(--surface); --lvf-btn-color: var(--ink-2); --lvf-btn-border: var(--line); }
.lvf .btn-ghost { --lvf-btn-bg: transparent; --lvf-btn-color: var(--ink-2); --lvf-btn-border: transparent; }
.lvf .cta-band.dark .btn-outline {
  --lvf-btn-bg: transparent;
  --lvf-btn-color: var(--surface);
  --lvf-btn-border: rgba(255,255,255,.28);
  --lvf-btn-hover-bg: rgba(255,255,255,.12);
  --lvf-btn-hover-color: var(--surface);
  --lvf-btn-hover-border: rgba(255,255,255,.44);
}
.lvf .btn-sm { height: 32px; min-height: 32px; padding: 0 14px; font-size: 13px; }
.lvf .btn-lg { height: 48px; min-height: 48px; padding: 0 24px; font-size: 16px; }

/* ── nav ── */
.lvf .nav {
  position: sticky; top: 0; z-index: 40;
  background: color-mix(in oklab, var(--bg) 82%, transparent);
  backdrop-filter: saturate(1.4) blur(16px);
  -webkit-backdrop-filter: saturate(1.4) blur(16px);
  border-bottom: 1px solid transparent;
  transition: border-color .2s, background .2s;
}
.lvf .nav.scrolled { border-bottom-color: var(--line); }
.lvf .nav-in { display: flex; align-items: center; justify-content: space-between; height: 68px; }
.lvf .brand { display: flex; align-items: center; gap: 10px; }
.lvf .brand-mark {
  width: 30px; height: 30px; border-radius: 8px;
  background: var(--agent-on); background-size: 200% 200%;
  display: grid; place-items: center; color: #fff;
  font-weight: 600; font-size: 17px; letter-spacing: -.04em;
  box-shadow: var(--sh-sm);
}
.lvf .brand-word { font-size: 20px; font-weight: 600; letter-spacing: -.03em; color: var(--ink); }
.lvf .brand-word .dot { color: var(--purple); }
.lvf .nav-links { display: flex; gap: 30px; }
.lvf .nav-links a { color: var(--ink-2); font-size: 14.5px; font-weight: 500; }
.lvf .nav-links a:hover { color: var(--purple); }
.lvf .nav-right { display: flex; align-items: center; gap: 14px; }
@media (max-width: 820px) { .lvf .nav-links { display: none; } }

/* ============================================================
   AGENT-ON SIGNATURE (the job card + ON pill)
   ============================================================ */
@keyframes lvfAgentFlow { 0% { background-position: 0% 50%; } 100% { background-position: 200% 50%; } }

.lvf .agent-pill {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 5px 11px 5px 9px; border-radius: 999px;
  font-family: var(--mono); font-size: 11px; letter-spacing: .12em;
  color: #efe7ff; font-weight: 500;
  background: var(--agent-on-flow); background-size: 200% 100%;
  box-shadow: var(--sh-glow);
}
.lvf .agent-pill .led {
  width: 7px; height: 7px; border-radius: 50%;
  background: #c4a5fd; box-shadow: 0 0 0 3px rgba(196,165,253,.28);
}
.lvf .agent-pill.off {
  background: var(--bg); color: var(--mute);
  border: 1px solid var(--line); box-shadow: none;
}
.lvf .agent-pill.off .led { background: var(--mute); box-shadow: none; }

/* job / role card */
.lvf .job-card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-lg); padding: 20px; box-shadow: var(--sh-md);
  transition: box-shadow .5s, border-color .5s;
}
.lvf .job-card.is-on { border-color: color-mix(in oklab, var(--purple) 30%, var(--line)); box-shadow: var(--sh-lg); }
.lvf .job-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.lvf .job-title { font-size: 18px; font-weight: 600; letter-spacing: -.02em; }
.lvf .job-meta { font-family: var(--mono); font-size: 11.5px; color: var(--mute); margin-top: 4px; letter-spacing: .02em; }

/* funnel stat row */
.lvf .funnel-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin-top: 18px; background: var(--line); border: 1px solid var(--line); border-radius: var(--r); overflow: hidden; }
.lvf .fstat { background: var(--surface); padding: 12px 14px; }
.lvf .fstat .k { font-family: var(--mono); font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: var(--mute); }
.lvf .fstat .v { font-size: 22px; font-weight: 600; letter-spacing: -.02em; margin-top: 3px; font-variant-numeric: tabular-nums; }
.lvf .fstat.hot .v { color: var(--purple); }

/* decision lane */
.lvf .lane { margin-top: 16px; }
.lvf .lane-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.lvf .lane-title { font-family: var(--mono); font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase; color: var(--ink-2); }
.lvf .lane-await { font-family: var(--mono); font-size: 10.5px; letter-spacing: .08em; color: var(--purple); }
.lvf .cand-row {
  display: grid; grid-template-columns: 34px 1fr auto auto; gap: 12px; align-items: center;
  padding: 11px 12px; border: 1px solid var(--line); border-radius: var(--r);
  background: var(--surface); margin-top: 8px;
}
.lvf .avatar {
  width: 34px; height: 34px; border-radius: 50%;
  display: grid; place-items: center; font-size: 13px; font-weight: 600;
  background: var(--purple-soft); color: var(--purple-deep);
}
.lvf .cand-name { font-size: 14.5px; font-weight: 550; letter-spacing: -.01em; }
.lvf .cand-sub { font-family: var(--mono); font-size: 11px; color: var(--mute); margin-top: 1px; }
.lvf .score-chip {
  font-family: var(--mono); font-size: 13px; font-weight: 500;
  padding: 4px 9px; border-radius: 8px; background: var(--purple-soft); color: var(--purple-deep);
  font-variant-numeric: tabular-nums;
}
.lvf .score-chip.low { background: var(--bg); color: var(--mute); }
.lvf .verdict {
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase;
  padding: 5px 11px; border-radius: 999px; font-weight: 500;
}
.lvf .verdict.advance { background: var(--purple); color: #fff; }
.lvf .verdict.assess { background: var(--purple-soft); color: var(--purple-deep); }
.lvf .verdict.reject { background: var(--bg); color: var(--mute); border: 1px solid var(--line); }

/* ── section scaffolding ── */
.lvf .section { padding: 120px 0; }
.lvf .section-head { max-width: 760px; margin: 0 auto 64px; text-align: center; }
.lvf .section-head .eyebrow { margin-bottom: 18px; }
.lvf .section-head h2 { font-size: clamp(32px, 4vw, 44px); }
.lvf .section-head .lede { margin: 20px auto 0; max-width: 620px; }

/* ============================================================
   FUNNEL — 5 steps
   ============================================================ */
.lvf .funnel { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; align-items: stretch; }
.lvf .fstep {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg);
  padding: 22px 20px; display: flex; flex-direction: column; position: relative; box-shadow: var(--sh-sm);
}
.lvf .fstep .fnum { font-family: var(--mono); font-size: 11px; letter-spacing: .12em; color: var(--purple); }
.lvf .fstep h3 { font-size: 19px; font-weight: 600; letter-spacing: -.02em; margin: 12px 0 8px; }
.lvf .fstep p { font-size: 13.5px; line-height: 1.55; color: var(--mute); margin: 0 0 16px; }
.lvf .fstep .fviz { margin-top: auto; }
.lvf .fchip {
  display: inline-flex; align-items: center; gap: 5px;
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .04em;
  padding: 4px 9px; border-radius: 7px; background: var(--purple-soft); color: var(--purple-deep);
}
.lvf .fchip.plain { background: var(--bg); color: var(--ink-2); border: 1px solid var(--line); }
.lvf .fchip.ok { background: var(--purple); color: #fff; }
.lvf .fchip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.lvf .evid-row {
  display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--ink-2);
  padding: 8px 10px; background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
}
.lvf .evid-row .tick { color: var(--purple); font-weight: 700; }
.lvf .mini-score { font-family: var(--mono); font-size: 22px; font-weight: 600; color: var(--purple); letter-spacing: -.02em; }
.lvf .mini-score small { font-size: 12px; color: var(--mute); }
.lvf .fflow-track { position: absolute; top: 50%; right: -14px; width: 14px; height: 2px; background: var(--line); z-index: 1; }
@media (max-width: 1000px) { .lvf .funnel { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px) { .lvf .funnel { grid-template-columns: 1fr; } }

/* ============================================================
   AI-FLUENCY SCORECARD (the 5 Ds)
   ============================================================ */
.lvf .scorecard { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-xl); box-shadow: var(--sh-lg); overflow: hidden; }
.lvf .sc-head {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 22px 28px; border-bottom: 1px solid var(--line); background: var(--bg);
}
.lvf .sc-head .who { display: flex; align-items: center; gap: 12px; }
.lvf .sc-head .who .avatar { width: 40px; height: 40px; }
.lvf .sc-title { font-size: 15px; font-weight: 600; }
.lvf .sc-sub { font-family: var(--mono); font-size: 11px; color: var(--mute); margin-top: 2px; }
.lvf .sc-total { text-align: right; }
.lvf .sc-total .big { font-size: 40px; font-weight: 600; letter-spacing: -.03em; color: var(--purple); line-height: 1; font-variant-numeric: tabular-nums; }
.lvf .sc-total .lbl { font-family: var(--mono); font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: var(--mute); }
.lvf .dd-row {
  display: grid; grid-template-columns: 150px 1fr 46px; gap: 20px; align-items: center;
  padding: 18px 28px; border-bottom: 1px solid var(--line);
}
.lvf .dd-row:last-child { border-bottom: 0; }
.lvf .dd-name { font-size: 15px; font-weight: 600; letter-spacing: -.01em; }
.lvf .dd-def { font-size: 13px; color: var(--mute); margin-top: 2px; }
.lvf .dd-track { height: 8px; border-radius: 999px; background: var(--purple-soft); overflow: hidden; }
.lvf .dd-fill { height: 100%; border-radius: 999px; background: var(--agent-on-flow); background-size: 200% 100%; transform-origin: left; }
.lvf .dd-val { font-family: var(--mono); font-size: 16px; font-weight: 500; text-align: right; color: var(--ink); font-variant-numeric: tabular-nums; }
@media (max-width: 620px) { .lvf .dd-row { grid-template-columns: 1fr 44px; } .lvf .dd-track { grid-column: 1 / -1; order: 3; } }

/* ============================================================
   CONTROL
   ============================================================ */
.lvf .control-point {
  display: grid; grid-template-columns: 30px 1fr; gap: 16px; align-items: start;
  padding: 22px 0; border-bottom: 1px solid var(--line);
}
.lvf .control-point:last-child { border-bottom: 0; }
.lvf .control-point .cp-ico {
  width: 30px; height: 30px; border-radius: 9px; background: var(--purple-soft); color: var(--purple-deep);
  display: grid; place-items: center;
}
.lvf .control-point p { margin: 0; font-size: 17px; line-height: 1.5; color: var(--ink); letter-spacing: -.01em; }

/* ============================================================
   PROOF + CTA + FOOTER
   ============================================================ */
.lvf .proof-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 24px; }
.lvf .proof-item { padding: 28px 0; border-top: 2px solid var(--ink); }
.lvf .proof-num { font-size: 30px; font-weight: 600; letter-spacing: -.03em; color: var(--purple); }
.lvf .proof-lbl { font-size: 14px; color: var(--ink-2); line-height: 1.45; margin-top: 10px; }
@media (max-width: 800px) { .lvf .proof-grid { grid-template-columns: repeat(2, 1fr); } }

.lvf .cta-band { border-radius: var(--r-xl); padding: 72px 56px; text-align: center; position: relative; overflow: hidden; }
.lvf .cta-band.dark { background: var(--agent-on); color: #fff; }
.lvf .cta-band.dark h2 { color: #fff; }
.lvf .cta-band.dark .lede { color: rgba(255,255,255,.78); }
.lvf .cta-actions { display: flex; gap: 14px; justify-content: center; margin-top: 30px; flex-wrap: wrap; }

.lvf footer { padding: 80px 0 40px; border-top: 1px solid var(--line); }
.lvf .foot-grid { display: grid; grid-template-columns: 1.6fr 1fr 1fr 1fr 1fr; gap: 40px; }
.lvf .foot-brand .brand { margin-bottom: 16px; }
.lvf .foot-brand p { font-size: 14px; color: var(--mute); line-height: 1.6; max-width: 280px; margin: 0; }
.lvf .foot-col h5 { font-family: var(--mono); font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: var(--mute); margin: 0 0 16px; font-weight: 500; }
.lvf .foot-col ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 11px; }
.lvf .foot-col a { color: var(--ink-2); font-size: 14px; }
.lvf .foot-col a:hover { color: var(--purple); }
.lvf .foot-bottom { display: flex; justify-content: space-between; align-items: center; margin-top: 60px; padding-top: 28px; border-top: 1px solid var(--line); font-family: var(--mono); font-size: 12px; color: var(--mute); letter-spacing: .04em; }
@media (max-width: 880px) { .lvf .foot-grid { grid-template-columns: 1fr 1fr; gap: 32px; } }

/* ============================================================
   HERO (vivid direction)
   ============================================================ */
.lvf .heroC { padding: 66px 0 100px; position: relative; overflow: hidden; }
.lvf .heroC-hero { text-align: center; display: flex; flex-direction: column; align-items: center; }
.lvf .heroC .eyebrow { display: inline-flex; align-items: center; gap: 8px; padding: 7px 14px; border-radius: 999px; background: #fff; border: 1px solid var(--line); box-shadow: var(--sh-sm); margin-bottom: 24px; }
.lvf .heroC .eyebrow::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--agent-on-flow); background-size: 200% 100%; }
.lvf .heroC h1 { font-size: clamp(44px, 5.2vw, 74px); letter-spacing: -.045em; line-height: .98; max-width: 15ch; }
.lvf .heroC .lede { margin: 26px auto 0; max-width: 560px; font-size: 19px; }
.lvf .heroC-actions { display: flex; gap: 14px; align-items: center; justify-content: center; margin-top: 34px; flex-wrap: wrap; }

/* the scene on a dark agent-ON gradient stage that glows against the light page */
.lvf .stage { position: relative; max-width: 560px; width: 100%; margin: 64px auto 0; border-radius: var(--r-xl); padding: 26px; background: var(--agent-on-flow); background-size: 200% 200%; box-shadow: 0 40px 90px -30px rgba(74,45,128,.6); }
.lvf .stage::after { content: ""; position: absolute; inset: 0; border-radius: inherit; box-shadow: inset 0 1px 0 rgba(255,255,255,.14); pointer-events: none; }
.lvf .stage .stage-cap { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.lvf .stage .stage-cap .t { font-family: var(--mono); font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: rgba(255,255,255,.7); }
.lvf .heroC-orb { position: absolute; z-index: 0; border-radius: 50%; filter: blur(60px); pointer-events: none; }
.lvf .heroC-orb.a { width: 420px; height: 420px; right: -60px; top: -80px; background: rgba(196,165,253,.4); }
.lvf .heroC-orb.b { width: 320px; height: 320px; left: 30%; bottom: -140px; background: rgba(94,58,168,.18); }
.lvf .heroC .wrap { position: relative; z-index: 1; }

/* replay button for the hero scene (in the stage cap, on dark) */
.lvf .replay {
  display: inline-flex; align-items: center; gap: 7px;
  font-family: var(--mono); font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
  height: 32px; min-height: 32px; padding: 0 14px; border-radius: 10px;
  color: rgba(255,255,255,.8); border: 1px solid rgba(255,255,255,.22); background: rgba(255,255,255,.1);
  font-weight: 600; line-height: 1; cursor: pointer;
  transition: transform .1s ease, color .16s, border-color .16s, background .16s, opacity .16s;
}
.lvf .replay:hover:not(:disabled):not([aria-disabled="true"]) { color: var(--surface); border-color: rgba(255,255,255,.44); background: rgba(255,255,255,.16); }
.lvf .replay:focus-visible { outline: 0; box-shadow: 0 0 0 3px rgba(196,165,253,.42); }
.lvf .replay:active:not(:disabled):not([aria-disabled="true"]) { transform: translateY(1px); }
.lvf .replay:is(:disabled, [aria-disabled="true"]) { opacity: .48; cursor: not-allowed; transform: none; }

/* ── PROBLEM §2 ── */
.lvf .problemC { text-align: center; padding: 120px 0; position: relative; }
.lvf .problemC .card { max-width: 900px; margin: 0 auto; background: linear-gradient(180deg, #fff, var(--purple-soft)); border: 1px solid var(--line); border-radius: var(--r-xl); padding: 72px 48px; box-shadow: var(--sh-lg); }
.lvf .problemC .big { font-size: clamp(32px, 4.4vw, 52px); font-weight: 600; letter-spacing: -.04em; line-height: 1.1; margin: 20px 0 0; }
.lvf .problemC .big .dim { color: var(--mute); display: block; }

/* ── FLUENCY §4 tinted band ── */
.lvf .fluencyC { background: linear-gradient(180deg, transparent, var(--purple-soft) 40%, transparent); }

/* ── CONTROL §5 ── */
.lvf .controlC-grid { display: grid; grid-template-columns: 1fr 460px; gap: 56px; align-items: center; }
.lvf .glow-card { background: var(--agent-on); border-radius: var(--r-xl); padding: 28px; color: #fff; box-shadow: 0 40px 90px -30px rgba(74,45,128,.55); }
.lvf .glow-card .dg-head { font-family: var(--mono); font-size: 11px; letter-spacing: .12em; color: rgba(255,255,255,.66); margin-bottom: 18px; }
.lvf .glow-card .dg-card { background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.16); border-radius: var(--r); padding: 18px; }
.lvf .glow-card .dg-row { display: flex; align-items: center; gap: 12px; }
.lvf .glow-card .avatar { background: rgba(255,255,255,.18); color: #fff; }
.lvf .glow-card .dg-name { font-weight: 600; font-size: 15px; }
.lvf .glow-card .dg-sub { font-family: var(--mono); font-size: 11px; color: rgba(255,255,255,.6); margin-top: 2px; }
.lvf .glow-card .dg-verdict { margin-left: auto; font-family: var(--mono); font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase; padding: 5px 11px; border-radius: 999px; background: var(--lavender); color: #241147; font-weight: 600; }
.lvf .glow-card .dg-ev { display: flex; gap: 9px; align-items: center; font-size: 12.5px; color: rgba(255,255,255,.82); margin-top: 10px; }
.lvf .glow-card .dg-ev .lk { font-family: var(--mono); font-size: 10px; color: var(--lavender); letter-spacing: .06em; }
.lvf .control-copy .display { font-size: clamp(28px,3vw,38px); margin: 16px 0 6px; }
.lvf .control-points { margin-top: 20px; }
@media (max-width: 940px) {
  .lvf .controlC-grid { grid-template-columns: 1fr; }
  .lvf .glow-card { max-width: 460px; }
}

/* ── hero agent scene: OFF is quiet/desaturated, ON lights up ── */
.lvf .job-card .funnel-stats,
.lvf .job-card .lane { transition: filter .6s ease, opacity .6s ease; }
.lvf .job-card:not(.is-on) .funnel-stats,
.lvf .job-card:not(.is-on) .lane { filter: grayscale(.55) saturate(.6); opacity: .72; }
.lvf .job-card:not(.is-on) .fstat.hot .v { color: var(--ink); }

/* While the hero scene is ARMED (JS mounted + motion allowed), hide the rows +
   verdicts so the useAnimate timeline reveals them cleanly. With NO data-armed
   (reduced motion, or before mount) they render in their settled final state. */
.lvf .stage[data-armed] .cand-row { opacity: 0; }
.lvf .stage[data-armed] .cand-row .verdict { opacity: 0; }

/* ============================================================
   MOTION — gradient shimmer, gated behind no-preference. The gradients above
   always paint; only the position animation is gated so reduced-motion users
   get a static (but full-colour) scene.
   ============================================================ */
@media (prefers-reduced-motion: no-preference) {
  .lvf .agent-pill { animation: lvfAgentFlow 6s linear infinite; }
  .lvf .heroC .eyebrow::before { animation: lvfAgentFlow 6s linear infinite; }
  .lvf .dd-fill { animation: lvfAgentFlow 6s linear infinite; }
  .lvf .stage { animation: lvfAgentFlow 14s linear infinite; }
}
`;
