# Landing Motion & Structure Research

**Date:** 2026-07-11 · Two primary-source research passes to inform the `/landing-preview` variant work (esp. variant D, the scroll-scrubbed "watch the agent work" scene). Companion: memory `landing_review_and_variants`.

Two tracks: (1) design/interaction teardown of the three reference sites Sam gave — gradient-labs.ai, cursor.com, scale.com/careers; (2) implementation technique for premium scroll-driven motion in our React 18 + Vite + Lenis stack.

---

## The pivotal finding

**None of the three reference sites ships a true scroll-*scrubbed* pinned scene** (DOM-verified 2026-07-11):

- **Gradient Labs** — built entirely in **Framer** (`meta[generator]=Framer`); motion = Framer "Appear" **enter-triggered reveals** (fade + rise + scale). Zero `position:sticky`, no canvas, no scrub. The "calm, expensive" end: premium via typography + spacing + one-shot reveals + gradient.
- **Cursor** — Next.js/Turbopack. The "watch it work" beats (cloud-agent task board, Slack thread, terminal) are **autoplaying React state machines on their own timers** when in view — **not** scroll-scrubbed. Sticky is used only for short edge-fade masks over media. framer-motion inferred (not DOM-confirmed). Native scroll, no smooth-scroll lib.
- **Scale careers** — Next.js + **Lenis** (`html.lenis` confirmed) smooth-scroll + light enter reveals + big imagery + whitespace. One small sticky sub-header. Almost nothing scrubs.

**Implication for us:** variant D's scroll-scrubbed pinned scene is *more* motion-forward than all three references. That's not wrong — but the references prove the premium feel comes mostly from **restraint + smooth-scroll momentum + tiny-copy/strong-visual beats**, not from scrubbing. The recommended refinement (below) is a **hybrid**: scrub the beat-to-beat *transitions*, but let the *within-beat* life (typing, streaming, cards flipping to Done) **autoplay on enter** — Cursor's lesson is that streaming text tied directly to scroll velocity feels janky.

---

## What makes these pages premium AND informative (ranked)

1. **Tiny copy per beat (~15–50 words), one hero visual carries the meaning.** Biggest lever. Let a mock/screenshot/bento cell explain.
2. **Autoplaying product mocks that look alive** (Cursor) — streaming cards, messages appearing, state transitions, on a timer when in view. Vanilla: React state + rAF/timers + IntersectionObserver start-on-enter.
3. **Enter-triggered reveals: fade + rise (~16–24px) + subtle scale, staggered, 250–500ms ease-out, one-shot** (don't replay on scroll-up). The universal baseline.
4. **Smooth-scroll momentum layer** (Scale = Lenis). Highest premium-per-effort; ~2–4KB. Instantly signals "designed."
5. **Generous vertical rhythm + restraint.** 23k–28k px pages; headlines and numbers that DON'T move. What stays still matters as much as what moves.
6. **Evidence spine: stat row → logo/press wall → named-face testimonials**, in that credibility order.
7. **Bento grid for "many small truths"** (Scale credos, Gradient security certs). Compresses low-density facts into a scannable mosaic.
8. **Sticky + gradient edge-mask over scrolling media** (Cursor). Cheap, high-polish.

Everything except #4 is vanilla CSS/JS/IntersectionObserver. #4 needs Lenis (which we already ship in variant D).

---

## Sharpening the "watch the agent work" scene (5 takeaways)

1. **3–5 beats max.** Cursor's whole product story is 3 mocks. Our five (Source→Screen→Assess→Decide→Hand back) is at the ceiling — keep it there, don't add.
2. **~6–12 words of copy per beat**, verb-led, pinned beside the visual, changing as each beat locks in.
3. **~100vh of scroll distance per beat**; scrub only `transform`/`opacity`, never `top/height/margin`. Ease the mapping so beats "snap" toward rest near each threshold rather than moving linearly.
4. **Hybrid motion:** scrub the structural beat-to-beat transitions; **autoplay the within-beat streaming** (the transcript typing, cards flipping to Done) on its own timer once the beat is in view. Avoids janky scroll-velocity-tied text.
5. **Restraint around the scene:** keep it the *only* scrubbed thing; flat background (no particles competing); ship the reduced-motion fallback (jump to each beat's end state). A scrub with no smooth-scroll under it feels steppy — Lenis makes it liquid.

---

## Implementation technique (React 18 + Vite + Lenis)

Primary sources: MDN, web.dev, caniuse, official Lenis repo.

**Drive scroll-progress from Lenis, not native scroll.** Because Lenis owns the wheel/touch→scroll pipeline and re-applies scroll every frame, native `window.scrollY` diverges from Lenis's eased value — the scene lags a frame or two, and programmatic `window.scrollTo` gets overwritten (this is exactly why the D scene was hard to drive in review). Subscribe via `lenis.on('scroll')` / `useLenis(cb)` and read `lenis.animatedScroll` / `lenis.progress`. That callback already fires once per frame inside Lenis's rAF tick — no second rAF throttle needed. **→ Variant D currently reads native rect.top in `sceneProgress.useScrollProgress`; switching it to Lenis's animated scroll is the top follow-up for smoothness.**

**Reduced motion:** gate Lenis init on `matchMedia('(prefers-reduced-motion: reduce)')` — do NOT instantiate Lenis; don't pin; render every beat at its final composition; use `behavior:'auto'` for jumps. (Variant D already skips Lenis + pinning in static mode, which includes reduced-motion — keep that.)

**60fps checklist:**
1. Animate only `transform` + `opacity` in the per-frame path.
2. No `getBoundingClientRect`/offset reads inside the frame — measure pin start/end once on mount + resize, cache them.
3. Read progress from Lenis's scroll event + `animatedScroll`, not `window.scrollY`.
4. `will-change` only on the moving sub-elements, added just-in-time, removed after — never on the full-viewport pin wrapper (GPU-memory blowout).
5. IntersectionObserver to short-circuit all scene work when off-screen.
6. Reserve the pin section's full height up front so CLS = 0.
7. Keep the frame handler well under the 50ms long-task line (math + transform writes, no allocation) so INP stays clean — note INP *does* measure JS scroll handlers.

**Counters + typing derived from progress** so scrubbing back reverses them: `value = round(from + (to-from)*eased(p))`; `chars = floor(len*eased(p))` → `el.textContent = full.slice(0, chars)`. (Variant D already does this — but see hybrid recommendation: within-beat streaming may feel better autoplayed than scrubbed.)

**CSS scroll-driven animations** (`animation-timeline: scroll()/view()`): real but **progressive-enhancement only** as of mid-2026 — caniuse ~83.7% global; Chrome/Edge 115+, but Firefox only 155+ and Safari only 26.0+ (both months-old). And native-scroll timelines desync from Lenis's animated scroll. Use behind `@supports` for *independent decorative* layers (progress bar, parallax) only; keep the load-bearing scene on the Lenis JS path.

**Library sizing:** Lenis ~2–4KB gz (we ship it). For scrubbing, `motion`/framer-motion `useScroll` is the small-bundle option if we ever want it; GSAP+ScrollTrigger (~40–50KB) only for complex multi-tween pinned timelines. We do NOT need GSAP for variant D.

---

## Variant D follow-ups (post-merge #931)

1. **Lenis-synced progress** — switch `sceneProgress.useScrollProgress` to read `lenis.animatedScroll`/`on('scroll')` instead of native rect.top. Biggest smoothness win; also fixes the programmatic-scroll fragility.
2. **Hybrid within-beat motion** — autoplay the transcript typing / card-flip streaming on enter (timer), scrub only the beat transitions. Per Cursor's pattern.
3. **Static/reduced-motion fallback spacing** — the stacked beats have loose vertical gaps (visuals were tuned for the 100vh pinned stage); tighten into compact panels.
4. **Copy density per beat** — current captions are ~20–35 words; trim toward 6–12 verb-led words to match reference density.
5. **Local review limitation** — the scrub scene is NOT reviewable in our preview harness (Lenis + sticky + harness scroll reset). Review scroll-driven variants on the **Vercel preview** (variant D makes no API calls, so its preview renders fully).

### Sources
web.dev (animations-guide, animations-and-performance, inp, learn/accessibility/motion) · MDN (scroll-driven animations, prefers-reduced-motion, will-change) · caniuse (animation-timeline) · Lenis repo + lenis/react · live-DOM inspection of cursor.com / gradient-labs.ai / scale.com/careers (2026-07-11). Flagged secondary: Cursor's framer-motion (inferred), FF/Safari point-release history (caniuse stable figures used instead).
