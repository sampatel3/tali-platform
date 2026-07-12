// Scoped CSS for LandingVariantG — F's "Vivid Purple" visual system with G's
// tight, one-screen-per-section layout. Same tokens, agent-ON stage, orb glows
// and gradient shimmer as F; every selector is scoped under the `.lvg` root so
// nothing leaks into the app. Keyframes are renamed (`lvgAgentFlow`) to avoid
// global collision; the gradient SHIMMER animations are gated behind
// `prefers-reduced-motion: no-preference` (the gradients themselves always paint
// — only the motion is gated).
//
// TYPE — the landing is MARKETING, not the app. It runs on its OWN bespoke,
// bigger `--l-*` px scale (defined in the `.lvg` block below), NOT the app
// `--fs-*` rem tokens: a landing is allowed — expected — to read larger and more
// confident than the login / product. The one rule is INTERNAL consistency: the
// scale is a small closed set of role tokens and every same-role element uses the
// same token. No raw px / one-off literals anywhere — every font-size is one of:
//   --l-hero   hero H1 (the single biggest headline)
//   --l-h2     EVERY section heading + prominent card totals — all identical
//   --l-h3     card / step titles + stat numbers
//   --l-lead   hero + section ledes
//   --l-body   body copy, buttons, nav, names
//   --l-small  meta / captions / chips / footer links-meta
//   --l-eyebrow  uppercase mono pills, kickers, verdict/lane labels
//
// ONE deliberate exception: the mock PRODUCT cards (hero agent scene, the 5-Ds
// scorecard, the white decision card) are meant to look like real product
// screenshots, so their INTERNAL text uses the app's small `--fs-*` product
// tokens — not this bigger `--l-*` marketing scale. Their card shapes/paddings
// and the surrounding landing copy stay on `--l-*`.
//
// What differs from F: the hero is a two-column grid (copy + CTAs | agent stage)
// and every body section is `.section-vp` — content-height with ONE uniform
// vertical rhythm (--sec-pad, same top & bottom on every section incl. hero).
// `scroll-margin-top` offsets the sticky nav for the native fallback.
//
// Injected via a <style> tag inside the `.lvg` root so the whole variant
// lazy-loads as one chunk with its component. LIGHT theme, purple family only —
// the reject state is a muted grey, never red. No CSS zoom; no horizontal scroll.
export const VARIANT_G_CSS = `
.lvg {
  /* ── palette — consume the SHARED taali brand tokens (00-tokens.css,
     data-brand="taali") instead of hardcoded literals, so the landing tracks
     the brand palette AND dark mode exactly like the rest of the site. The
     colour/surface tokens (--purple, --purple-soft, --bg, --surface, --ink,
     --ink-2, --mute, --line) inherit from <html data-brand="taali">; the two
     aliases below map the scoped names this file uses onto the shared tokens,
     and --agent-on(-flow) point at the shared agent-ON gradient vocabulary. ── */
  --purple-deep:   var(--purple-2);
  --lavender:      var(--purple-lav);

  --agent-on:      var(--grad-agent-on);
  --agent-on-flow: var(--grad-agent-on-animated);

  /* ── TYPE — bespoke MARKETING scale (see the header note). Bigger + confident
     than the app, and internally consistent: a small closed set of role tokens,
     every same-role element on the same token. px/clamp on purpose — the landing
     does NOT track the app --fs-* rem density. ── */
  --l-hero:    clamp(40px, 5vw, 54px);
  --l-h2:      clamp(26px, 3vw, 32px);
  --l-h3:      20px;
  --l-lead:    18px;
  --l-body:    16px;
  --l-small:   14px;
  --l-eyebrow: 12px;

  /* ── ONE uniform section rhythm — every band (hero + body sections + footer)
     pads by this single value top & bottom so the vertical spacing is even. */
  --sec-pad: 64px;

  --sh-sm: 0 1px 2px rgba(21,18,26,.05), 0 1px 0 rgba(21,18,26,.02);
  --sh-md: 0 2px 4px rgba(21,18,26,.04), 0 12px 28px -10px rgba(21,18,26,.12);
  --sh-lg: 0 4px 8px rgba(21,18,26,.04), 0 30px 60px -22px rgba(74,45,128,.22);
  --sh-glow: 0 20px 60px -18px rgba(94,58,168,.45);

  --r-sm: 10px;
  --r:    14px;
  --r-lg: 18px;
  --r-xl: 24px;

  --font: var(--font-sans);
  --mono: var(--font-mono);

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
.lvg *, .lvg *::before, .lvg *::after { box-sizing: border-box; }
.lvg a { color: var(--purple); text-decoration: none; }
.lvg a:hover { color: var(--purple-deep); }
.lvg button { font: inherit; cursor: pointer; border: 0; background: none; color: inherit; }
.lvg img { max-width: 100%; display: block; }

.lvg .wrap { width: 100%; max-width: var(--maxw); margin: 0 auto; padding: 0 var(--pad); }

/* ── eyebrow — ONE crisp pill everywhere (section heads, hero, CTA). Consistent
   padding, --l-eyebrow, tidy soft-purple fill + hairline border so every kicker
   reads the same, tidy way. Dark bands get a translucent variant below. ── */
.lvg .eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-family: var(--mono);
  font-size: var(--l-eyebrow);
  letter-spacing: .14em;
  text-transform: uppercase;
  color: var(--purple);
  font-weight: 500;
  padding: 5px 11px;
  border-radius: 999px;
  background: var(--purple-soft);
  border: 1px solid color-mix(in oklab, var(--purple) 16%, var(--line));
}
.lvg .eyebrow.mute { color: var(--mute); background: var(--bg); border-color: var(--line); }

/* ── headline helpers ── */
.lvg .display {
  font-family: var(--font);
  font-weight: 600;
  letter-spacing: -.035em;
  line-height: 1.04;
  color: var(--ink);
  margin: 0;
  text-wrap: balance;
}
.lvg .display .accent { color: var(--purple); }
.lvg .lede { color: var(--ink-2); font-size: var(--l-lead); line-height: 1.6; margin: 0; }

.lvg .grad-text {
  background: linear-gradient(96deg, #6a3fb8, #5e3aa8 40%, #8b5cf6);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}

/* ── buttons ── */
.lvg .btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 12px 20px; border-radius: 10px;
  font-size: var(--l-body); font-weight: 500; letter-spacing: -.01em;
  transition: transform .1s ease, background .16s ease, border-color .16s, color .16s, box-shadow .16s;
  white-space: nowrap;
}
.lvg .btn:active { transform: translateY(1px); }
.lvg .btn-primary { background: var(--purple); color: #fff; box-shadow: 0 1px 0 rgba(255,255,255,.15) inset, var(--sh-sm); }
.lvg .btn-primary:hover { background: var(--purple-deep); color: #fff; }
.lvg .btn-primary .arw { transition: transform .18s; }
.lvg .btn-primary:hover .arw { transform: translateX(3px); }
.lvg .btn-outline { background: var(--surface); color: var(--ink); border: 1px solid var(--line); }
.lvg .btn-outline:hover { border-color: var(--purple); color: var(--purple); }
.lvg .btn-ghost { color: var(--ink-2); padding-left: 6px; padding-right: 6px; }
.lvg .btn-ghost:hover { color: var(--purple); }
.lvg .btn-lg { padding: 15px 26px; font-size: var(--l-body); }

/* ── nav ── */
.lvg .nav {
  position: sticky; top: 0; z-index: 40;
  background: color-mix(in oklab, var(--bg) 82%, transparent);
  backdrop-filter: saturate(1.4) blur(16px);
  -webkit-backdrop-filter: saturate(1.4) blur(16px);
  border-bottom: 1px solid transparent;
  transition: border-color .2s, background .2s;
}
.lvg .nav.scrolled { border-bottom-color: var(--line); }
.lvg .nav-in { display: flex; align-items: center; justify-content: space-between; height: 68px; }
.lvg .brand { display: flex; align-items: center; gap: 10px; }
.lvg .brand-mark {
  width: 30px; height: 30px; border-radius: 8px;
  background: var(--agent-on); background-size: 200% 200%;
  display: grid; place-items: center; color: #fff;
  font-weight: 600; font-size: var(--l-h3); letter-spacing: -.04em;
  box-shadow: var(--sh-sm);
}
.lvg .brand-word { font-size: var(--l-h3); font-weight: 600; letter-spacing: -.03em; color: var(--ink); }
.lvg .brand-word .dot { color: var(--purple); }
.lvg .nav-links { display: flex; gap: 30px; }
.lvg .nav-links a { color: var(--ink-2); font-size: var(--l-body); font-weight: 500; position: relative; padding: 4px 0; transition: color .16s; }
.lvg .nav-links a:hover { color: var(--purple); }
.lvg .nav-links a.is-active { color: var(--purple); }
.lvg .nav-links a.is-active::after {
  content: ""; position: absolute; left: 0; right: 0; bottom: -2px; height: 2px; border-radius: 2px;
  background: var(--agent-on-flow); background-size: 200% 100%;
}
.lvg .nav-right { display: flex; align-items: center; gap: 14px; }
@media (max-width: 820px) { .lvg .nav-links { display: none; } }

/* ============================================================
   AGENT-ON SIGNATURE (the job card + ON pill)
   ============================================================ */
@keyframes lvgAgentFlow { 0% { background-position: 0% 50%; } 100% { background-position: 200% 50%; } }

.lvg .agent-pill {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 5px 11px 5px 9px; border-radius: 999px;
  font-family: var(--mono); font-size: var(--l-eyebrow); letter-spacing: .12em;
  color: #efe7ff; font-weight: 500;
  background: var(--agent-on-flow); background-size: 200% 100%;
  box-shadow: var(--sh-glow);
}
.lvg .agent-pill .led {
  width: 7px; height: 7px; border-radius: 50%;
  background: #c4a5fd; box-shadow: 0 0 0 3px rgba(196,165,253,.28);
}
.lvg .agent-pill.off {
  background: var(--bg); color: var(--mute);
  border: 1px solid var(--line); box-shadow: none;
}
.lvg .agent-pill.off .led { background: var(--mute); box-shadow: none; }

/* job / role card */
.lvg .job-card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-lg); padding: 13px; box-shadow: var(--sh-md);
  transition: box-shadow .5s, border-color .5s;
}
.lvg .job-card.is-on { border-color: color-mix(in oklab, var(--purple) 30%, var(--line)); box-shadow: var(--sh-lg); }
.lvg .job-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.lvg .job-title { font-size: var(--fs-h3); font-weight: 600; letter-spacing: -.02em; }
.lvg .job-meta { font-family: var(--mono); font-size: var(--fs-caption); color: var(--mute); margin-top: 4px; letter-spacing: .02em; }

/* funnel stat row */
.lvg .funnel-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin-top: 12px; background: var(--line); border: 1px solid var(--line); border-radius: var(--r); overflow: hidden; }
.lvg .fstat { background: var(--surface); padding: 8px 6px; }
.lvg .fstat .k { font-family: var(--mono); font-size: var(--fs-eyebrow); letter-spacing: .03em; text-transform: uppercase; color: var(--mute); white-space: nowrap; }
.lvg .fstat .v { font-size: var(--fs-h2); font-weight: 600; letter-spacing: -.02em; margin-top: 2px; font-variant-numeric: tabular-nums; }
.lvg .fstat.hot .v { color: var(--purple); }

/* decision lane */
.lvg .lane { margin-top: 12px; }
.lvg .lane-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.lvg .lane-title { font-family: var(--mono); font-size: var(--fs-eyebrow); letter-spacing: .12em; text-transform: uppercase; color: var(--ink-2); }
.lvg .lane-await { font-family: var(--mono); font-size: var(--fs-eyebrow); letter-spacing: .08em; color: var(--purple); }
.lvg .cand-row {
  display: grid; grid-template-columns: 30px 1fr auto auto; gap: 10px; align-items: center;
  padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--r);
  background: var(--surface); margin-top: 6px;
}
/* keep each row a single, tidy line (name + one-line sub) so the card stays
   compact and the hero columns balance */
.lvg .cand-row > div { min-width: 0; }
.lvg .cand-name, .lvg .cand-sub { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lvg .avatar {
  width: 30px; height: 30px; border-radius: 50%;
  display: grid; place-items: center; font-size: var(--fs-body); font-weight: 600;
  background: var(--purple-soft); color: var(--purple-deep);
}
.lvg .cand-name { font-size: var(--fs-body-lg); font-weight: 550; letter-spacing: -.01em; }
.lvg .cand-sub { font-family: var(--mono); font-size: var(--fs-caption); color: var(--mute); margin-top: 1px; }
.lvg .score-chip {
  font-family: var(--mono); font-size: var(--fs-eyebrow); font-weight: 500;
  padding: 4px 9px; border-radius: 8px; background: var(--purple-soft); color: var(--purple-deep);
  font-variant-numeric: tabular-nums;
}
.lvg .score-chip.low { background: var(--bg); color: var(--mute); }
.lvg .verdict {
  font-family: var(--mono); font-size: var(--fs-eyebrow); letter-spacing: .08em; text-transform: uppercase;
  padding: 5px 11px; border-radius: 999px; font-weight: 500;
}
.lvg .verdict.advance { background: var(--purple); color: #fff; }
.lvg .verdict.assess { background: var(--purple-soft); color: var(--purple-deep); }
.lvg .verdict.reject { background: var(--bg); color: var(--mute); border: 1px solid var(--line); }

/* ── section scaffolding — content-height bands on ONE uniform rhythm ──
   Every body section pads by the single --sec-pad value top & bottom (no forced
   viewport fill, no per-section variance) so the vertical gaps between sections
   are even; scroll-margin-top clears the sticky nav for the native fallback. */
.lvg .section-vp {
  position: relative;
  padding: var(--sec-pad) 0;
  scroll-margin-top: 68px;
}
.lvg .section-vp-in { width: 100%; }
.lvg .section-head { max-width: 760px; margin: 0 auto 30px; text-align: center; }
.lvg .section-head .eyebrow { margin-bottom: 12px; }
.lvg .section-head h2 { font-size: var(--l-h2); }
.lvg .section-head .lede { margin: 14px auto 0; max-width: 600px; font-size: var(--l-lead); }

/* ============================================================
   FUNNEL — 5 steps
   ============================================================ */
.lvg .funnel { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; align-items: stretch; }
.lvg .fstep {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg);
  padding: 22px 20px; display: flex; flex-direction: column; position: relative; box-shadow: var(--sh-sm);
}
.lvg .fstep .fnum { font-family: var(--mono); font-size: var(--l-eyebrow); letter-spacing: .12em; color: var(--purple); }
.lvg .fstep h3 { font-size: var(--l-h3); font-weight: 600; letter-spacing: -.02em; margin: 12px 0 8px; }
.lvg .fstep p { font-size: var(--l-body); line-height: 1.55; color: var(--mute); margin: 0 0 16px; }
.lvg .fstep .fviz { margin-top: auto; }
.lvg .fchip {
  display: inline-flex; align-items: center; gap: 5px;
  font-family: var(--mono); font-size: var(--l-eyebrow); letter-spacing: .04em;
  padding: 4px 9px; border-radius: 7px; background: var(--purple-soft); color: var(--purple-deep);
}
.lvg .fchip.plain { background: var(--bg); color: var(--ink-2); border: 1px solid var(--line); }
.lvg .fchip.ok { background: var(--purple); color: #fff; }
.lvg .fchip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.lvg .evid-row {
  display: flex; align-items: center; gap: 8px; font-size: var(--l-small); color: var(--ink-2);
  padding: 8px 10px; background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
}
.lvg .evid-row .tick { color: var(--purple); font-weight: 700; }
.lvg .mini-score { font-family: var(--mono); font-size: var(--l-h3); font-weight: 600; color: var(--purple); letter-spacing: -.02em; }
.lvg .mini-score small { font-size: var(--l-small); color: var(--mute); }
.lvg .fflow-track { position: absolute; top: 50%; right: -14px; width: 14px; height: 2px; background: var(--line); z-index: 1; }
@media (max-width: 1000px) { .lvg .funnel { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px) { .lvg .funnel { grid-template-columns: 1fr; } }

/* ============================================================
   AI-FLUENCY SCORECARD (the 5 Ds)
   ============================================================ */
.lvg .scorecard { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-xl); box-shadow: var(--sh-lg); overflow: hidden; }
.lvg .scorecard { max-width: 680px; margin: 0 auto; }
.lvg .sc-head {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 15px 28px; border-bottom: 1px solid var(--line); background: var(--bg);
}
.lvg .sc-head .who { display: flex; align-items: center; gap: 12px; }
.lvg .sc-head .who .avatar { width: 40px; height: 40px; }
.lvg .sc-title { font-size: var(--fs-body-lg); font-weight: 600; }
.lvg .sc-sub { font-family: var(--mono); font-size: var(--fs-caption); color: var(--mute); margin-top: 2px; }
.lvg .sc-total { text-align: right; }
.lvg .sc-total .big { font-size: var(--fs-stat); font-weight: 600; letter-spacing: -.03em; color: var(--purple); line-height: 1; font-variant-numeric: tabular-nums; }
.lvg .sc-total .lbl { font-family: var(--mono); font-size: var(--fs-eyebrow); letter-spacing: .12em; text-transform: uppercase; color: var(--mute); }
.lvg .dd-row {
  display: grid; grid-template-columns: 232px 1fr 46px; gap: 20px; align-items: center;
  padding: 10px 28px; border-bottom: 1px solid var(--line);
}
.lvg .dd-row:last-child { border-bottom: 0; }
.lvg .dd-name { font-size: var(--fs-body-lg); font-weight: 600; letter-spacing: -.01em; }
.lvg .dd-def { font-size: var(--fs-caption); line-height: 1.35; color: var(--mute); margin-top: 2px; }
.lvg .dd-track { height: 8px; border-radius: 999px; background: var(--purple-soft); overflow: hidden; }
.lvg .dd-fill { height: 100%; border-radius: 999px; background: var(--agent-on-flow); background-size: 200% 100%; transform-origin: left; }
.lvg .dd-val { font-family: var(--mono); font-size: var(--fs-body-lg); font-weight: 500; text-align: right; color: var(--ink); font-variant-numeric: tabular-nums; }
@media (max-width: 620px) { .lvg .dd-row { grid-template-columns: 1fr 44px; } .lvg .dd-track { grid-column: 1 / -1; order: 3; } }

/* ============================================================
   CONTROL
   ============================================================ */
.lvg .control-point {
  display: grid; grid-template-columns: 30px 1fr; gap: 16px; align-items: start;
  padding: 20px 0; border-bottom: 1px solid var(--line);
}
.lvg .control-point:last-child { border-bottom: 0; }
.lvg .control-point .cp-ico {
  width: 30px; height: 30px; border-radius: 9px; background: var(--purple-soft); color: var(--purple-deep);
  display: grid; place-items: center;
}
.lvg .control-point p { margin: 0; font-size: var(--l-body); line-height: 1.5; color: var(--ink); letter-spacing: -.01em; }

/* ============================================================
   CTA + FOOTER
   ============================================================ */
.lvg .cta-band { border-radius: var(--r-xl); padding: 52px 48px; text-align: center; position: relative; overflow: hidden; }
.lvg .cta-band.dark { background: var(--agent-on); color: #fff; }
.lvg .cta-band.dark h2 { color: #fff; }
.lvg .cta-band.dark .lede { color: rgba(255,255,255,.78); }
/* eyebrow pill sitting on a dark band — translucent, crisp, same size */
.lvg .cta-band.dark .eyebrow { background: rgba(255,255,255,.12); border-color: rgba(255,255,255,.22); color: var(--lavender); }
.lvg .cta-actions { display: flex; gap: 14px; justify-content: center; margin-top: 30px; flex-wrap: wrap; }

.lvg footer { padding: var(--sec-pad) 0 36px; border-top: 1px solid var(--line); }
.lvg .foot-grid { display: grid; grid-template-columns: 1.8fr 1fr 1fr 1fr; gap: 48px; }
.lvg .foot-brand .brand { margin-bottom: 16px; }
.lvg .foot-brand .logo { color: var(--ink); }
.lvg .foot-brand p { font-size: var(--l-small); color: var(--mute); line-height: 1.6; max-width: 300px; margin: 0; }
.lvg .foot-col h5 { font-family: var(--mono); font-size: var(--l-small); letter-spacing: .14em; text-transform: uppercase; color: var(--mute); margin: 0 0 16px; font-weight: 500; }
.lvg .foot-col ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 11px; }
.lvg .foot-col a { color: var(--ink-2); font-size: var(--l-body); }
.lvg .foot-col a:hover { color: var(--purple); }
.lvg .foot-bottom { display: flex; justify-content: space-between; align-items: center; margin-top: 44px; padding-top: 28px; border-top: 1px solid var(--line); font-family: var(--mono); font-size: var(--l-small); color: var(--mute); letter-spacing: .04em; }
@media (max-width: 880px) { .lvg .foot-grid { grid-template-columns: 1fr 1fr; gap: 32px; } }

/* ============================================================
   HERO (vivid direction) — two columns so headline + live stage both fit
   one viewport. Left: eyebrow/H1/lede/CTAs. Right: the agent-ON stage.
   ============================================================ */
.lvg .heroC { position: relative; overflow: hidden; display: flex; align-items: center; padding: var(--sec-pad) 0; scroll-margin-top: 68px; }
.lvg .heroC-grid { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 48px; align-items: center; }
.lvg .heroC-copy { display: flex; flex-direction: column; align-items: flex-start; text-align: left; }
.lvg .heroC .eyebrow { margin-bottom: 26px; }
.lvg .heroC .eyebrow::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--agent-on-flow); background-size: 200% 100%; }
.lvg .heroC h1 { font-size: var(--l-hero); letter-spacing: -.045em; line-height: 1.08; max-width: 15ch; }
.lvg .heroC .lede { margin: 26px 0 0; max-width: 500px; font-size: var(--l-lead); line-height: 1.65; }
.lvg .heroC-actions { display: flex; gap: 14px; align-items: center; justify-content: flex-start; margin-top: 48px; flex-wrap: wrap; }
.lvg .heroC-stage-col { min-width: 0; }

/* the scene on a dark agent-ON gradient stage that glows against the light page */
.lvg .stage { position: relative; max-width: 340px; width: 100%; margin: 0 0 0 auto; border-radius: var(--r-lg); padding: 13px; background: var(--agent-on-flow); background-size: 200% 200%; box-shadow: 0 30px 70px -28px rgba(74,45,128,.55); }
.lvg .stage::after { content: ""; position: absolute; inset: 0; border-radius: inherit; box-shadow: inset 0 1px 0 rgba(255,255,255,.14); pointer-events: none; }
.lvg .stage .stage-cap { display: flex; align-items: center; justify-content: space-between; margin-bottom: 11px; }
.lvg .stage .stage-cap .t { font-family: var(--mono); font-size: var(--l-eyebrow); letter-spacing: .14em; text-transform: uppercase; color: rgba(255,255,255,.7); }
.lvg .heroC-orb { position: absolute; z-index: 0; border-radius: 50%; filter: blur(60px); pointer-events: none; }
.lvg .heroC-orb.a { width: 420px; height: 420px; right: -60px; top: -80px; background: rgba(196,165,253,.4); }
.lvg .heroC-orb.b { width: 320px; height: 320px; left: 30%; bottom: -140px; background: rgba(94,58,168,.18); }
.lvg .heroC .wrap { position: relative; z-index: 1; }

/* stack the hero below 940px: copy centred, stage below, type steps down. */
@media (max-width: 940px) {
  .lvg .heroC { padding: var(--sec-pad) 0; }
  .lvg .heroC-grid { grid-template-columns: 1fr; gap: 40px; }
  .lvg .heroC-copy { align-items: center; text-align: center; }
  .lvg .heroC .lede { margin-left: auto; margin-right: auto; }
  .lvg .heroC-actions { justify-content: center; }
  .lvg .stage { margin: 0 auto; }
}

/* replay button for the hero scene (in the stage cap, on dark) */
.lvg .replay {
  display: inline-flex; align-items: center; gap: 7px;
  font-family: var(--mono); font-size: var(--l-eyebrow); letter-spacing: .08em; text-transform: uppercase;
  color: rgba(255,255,255,.8); padding: 6px 12px; border: 1px solid rgba(255,255,255,.22); border-radius: 999px; background: rgba(255,255,255,.1);
  transition: color .16s, border-color .16s;
}
.lvg .replay:hover { color: #fff; border-color: #fff; }

/* ── FLUENCY tinted band ── */
.lvg .fluencyC { background: linear-gradient(180deg, transparent, var(--purple-soft) 40%, transparent); }

/* ── CONTROL §5 ── */
.lvg .controlC-grid { display: grid; grid-template-columns: 1fr 380px; gap: 56px; align-items: center; }
/* WHITE decision card — matches the hero's white job-card and the app's white
   decision cards (was a dark-purple glow card). Agent advises → you decide, on a
   clean surface: candidate + evidence + the Advance verdict pill. */
.lvg .glow-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-xl); padding: 28px; color: var(--ink); box-shadow: var(--sh-lg); }
.lvg .glow-card .dg-head { font-family: var(--mono); font-size: var(--fs-eyebrow); letter-spacing: .12em; color: var(--mute); margin-bottom: 18px; }
.lvg .glow-card .dg-card { background: var(--bg); border: 1px solid var(--line); border-radius: var(--r); padding: 18px; }
.lvg .glow-card .dg-row { display: flex; align-items: center; gap: 12px; }
.lvg .glow-card .avatar { background: var(--purple-soft); color: var(--purple-deep); }
.lvg .glow-card .dg-name { font-weight: 600; font-size: var(--fs-body-lg); color: var(--ink); }
.lvg .glow-card .dg-sub { font-family: var(--mono); font-size: var(--fs-caption); color: var(--mute); margin-top: 2px; }
.lvg .glow-card .dg-verdict { margin-left: auto; font-family: var(--mono); font-size: var(--fs-eyebrow); letter-spacing: .08em; text-transform: uppercase; padding: 5px 11px; border-radius: 999px; background: var(--purple); color: #fff; font-weight: 600; }
.lvg .glow-card .dg-ev { display: flex; gap: 9px; align-items: center; font-size: var(--fs-caption); color: var(--ink-2); margin-top: 10px; }
.lvg .glow-card .dg-ev .lk { font-family: var(--mono); font-size: var(--fs-eyebrow); color: var(--purple); letter-spacing: .06em; }
.lvg .control-copy .display { font-size: var(--l-h2); margin: 16px 0 6px; }
.lvg .control-points { margin-top: 20px; }
/* the relocated closing CTA — Control's finale */
.lvg .cta-band.control-cta { margin-top: 48px; padding: 40px 44px; }
.lvg .cta-band.control-cta .cta-actions { margin-top: 24px; }
@media (max-width: 940px) {
  .lvg .controlC-grid { grid-template-columns: 1fr; }
  .lvg .glow-card { max-width: 380px; }
}

/* ── hero agent scene: OFF is quiet/desaturated, ON lights up ── */
.lvg .job-card .funnel-stats,
.lvg .job-card .lane { transition: filter .6s ease, opacity .6s ease; }
.lvg .job-card:not(.is-on) .funnel-stats,
.lvg .job-card:not(.is-on) .lane { filter: grayscale(.55) saturate(.6); opacity: .72; }
.lvg .job-card:not(.is-on) .fstat.hot .v { color: var(--ink); }

/* While the hero scene is ARMED (JS mounted + motion allowed), hide the rows +
   verdicts so the useAnimate timeline reveals them cleanly. With NO data-armed
   (reduced motion, or before mount) they render in their settled final state. */
.lvg .stage[data-armed] .cand-row { opacity: 0; }
.lvg .stage[data-armed] .cand-row .verdict { opacity: 0; }

/* ============================================================
   MOTION — gradient shimmer, gated behind no-preference. The gradients above
   always paint; only the position animation is gated so reduced-motion users
   get a static (but full-colour) scene.
   ============================================================ */
@media (prefers-reduced-motion: no-preference) {
  .lvg .agent-pill { animation: lvgAgentFlow 6s linear infinite; }
  .lvg .heroC .eyebrow::before { animation: lvgAgentFlow 6s linear infinite; }
  .lvg .dd-fill { animation: lvgAgentFlow 6s linear infinite; }
  .lvg .stage { animation: lvgAgentFlow 14s linear infinite; }
}
`;
