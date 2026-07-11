# Taali — Landing Page Design Brief

**For:** a design tool / designer producing high-fidelity mockups.
**Ask:** deliver **at least 3 distinct visual directions** for the Taali marketing landing page (desktop + mobile), based on the fixed narrative and brand below. These are *design comps* — nail the look, feel, layout, and craft. Implementation happens after one direction is chosen.

This brief is self-contained. You do not need any codebase access.

---

## 1. What Taali is (context)

Taali is an **agentic hiring platform**. A governed AI agent works a recruiter's whole funnel — it screens every applicant, runs a hands-on assessment, and puts a **decision** in front of the recruiter with the evidence attached. The recruiter approves, overrides, or teaches it back. **The human stays in control of every consequential call.**

Its one unique differentiator: it is **the only platform that measures how well a candidate actually works with AI** (an "AI-fluency" assessment), for engineering *and* knowledge work. Everyone hires people who use AI now; nobody else can tell you who's genuinely good at it.

**Audience:** recruiters and talent leaders at modern/tech-forward companies (B2B SaaS buyer). Credibility-sensitive category — hiring must feel fair, defensible, evidence-backed, never hypey.

**Primary goal of the page:** get the visitor to "See it live" (self-serve demo) or "Book a demo." Sell the vision (agent runs the funnel end to end) with the AI-fluency assessment as the wedge.

**Competitors' landing pages** (for category tone, all live B2B AI-hiring): metaview.ai, gem.com, ashbyhq.com, eightfold.ai, harver.com. **Aesthetic references we admire** (craft/restraint, NOT hiring): cursor.com, linear.app, vercel.com, gradient-labs.ai, scale.com.

---

## 2. The narrative (FIXED — do not restructure)

The page tells **one story, and each thing is said exactly once**. Do not repeat the funnel across multiple sections (a previous version said it 4× and felt disjointed). The spine, in order:

**One-line story:** *"Turn a job on. The agent works your whole funnel — and it's the only one that measures how people actually work with AI."*

1. **Hero — the product loop, live.** The signature moment (see §4). A real **job/role** turns its **agent ON**, and **candidates flow into a decision lane** with verdicts (Advance / Assess / Reject). This is the product in miniature — it earns the headline rather than just captioning it.
2. **The problem** — one tight beat: *"Everyone works with AI now. The CV can't prove it. The interview can't catch it."*
3. **The funnel, shown once** — one candidate moving through **Source → Screen → Assess → Decide → Hand back** as a single coherent flow. Headline: *"One agent, your whole funnel."*
4. **The wedge — AI fluency** — the differentiator gets its own dedicated moment: a **5-dimension scorecard**. Headline: *"Measure how people actually work with AI."*
5. **You stay in control** — the credibility keystone. Headline: *"The agent advises. You decide."* Evidence-linked, deterministic, audit trail, never acts on protected characteristics.
6. **Proof + close** — a few hard stat lines, then a final CTA and footer.

Design each of the 6 as a distinct section with clear rhythm. Sections 2–6 should feel calmer/tighter than the hero.

---

## 3. Brand & visual system

**Theme: LIGHT.** (A dark cinematic version was explicitly rejected as "too much." Keep it light, clean, premium — closer to Linear/Vercel than to a neon dark landing.)

**Colour palette (use these exact values):**
| Token | Hex | Use |
|---|---|---|
| Purple (primary accent) | `#5e3aa8` | primary buttons, accent word in headline, key marks |
| Purple deep | `#4a2d80` | gradients, hover |
| Purple soft | `#ede5f8` | tints, chips, hover fills |
| Lavender | `#c4a5fd` | light accents, glows |
| Background | `#f7f4fb` | page base (pale lavender-white) |
| Surface / card | `#ffffff` | cards, panels |
| Ink | `#15121a` | headlines, primary text |
| Ink-2 | `#3a3343` | body text |
| Mute | `#8b8595` | captions, meta |
| Line | `#e8e2ee` | thin borders/dividers |

**Agent-ON signature gradient** (the "agent is on" state — a rich dark purple): `linear-gradient(150deg, #3a1d6e, #241147)`, with an animated 5-stop variant `linear-gradient(120deg, #3a1d6e, #6a3fb8, #2a1556, #4a2a8a, #3a1d6e)` for a subtle flowing shimmer. This is a real, distinctive product motif — the agent turning "on" lights up in this deep animated purple against the light page. **Use it as a hero anchor.**

**Purple family only.** No red/amber/green as accents (even for reject states — use greys/muted, never red). This is a firm brand rule.

**Typography:** Geist (sans) for everything, Geist Mono for small eyebrow labels / meta / code-like chips. Big, tight display headlines (weight ~600, tight letter-spacing). Reference type scale: hero H1 ~56–72px, section H2 ~32–40px, body 15–17px. Restraint — lots of the premium feel comes from generous whitespace and headlines/numbers that DON'T move.

**Card / product-surface language:** white cards, thin `#e8e2ee` borders, soft shadows, ~14–18px radius. Small mono eyebrow labels above headlines (e.g. `AGENT-NATIVE HIRING`, `THE DIFFERENTIATOR`).

**Overall feel to hit:** confident, precise, evidence-forward, agent-native. Premium B2B, not playful, not hypey. Think Linear's restraint + one distinctive product motif (the agent-ON purple).

---

## 4. The hero (most important — describe/mock it richly)

The hero is **the product's core loop shown live**, grounded in the real product UI. Left = words, right = a compact product scene (~500–560px wide, balanced beside the headline — NOT a giant panel; an oversized card was rejected).

**Left column:**
- Mono eyebrow: `AGENT-NATIVE HIRING`
- H1 (dark ink, with the last phrase in purple `#5e3aa8`): **"Taali is the hiring agent that screens, assesses, and decides — with you."**
- Sub (ink-2): *"An agentic recruiting platform that runs screening, AI-fluency assessment, and defensible decisions end to end. You stay in control of every call that matters."*
- Primary button (purple fill): **"See it live →"**. Secondary (ghost/outline): **"Book a demo"**.

**Right column — the scene (animated; show its key frames in the mockup):**
A real-looking **job/role card** (e.g. "AI Engineer · #312 · Engineering · Remote · 312 applied") that carries a small **AGENT ON** pill in the deep-purple agent gradient. It shows a compact funnel stat row: **Applied 312 · Screened 184 · Assessed 22 · Advanced 9**. Below it, a **"Decision lane — awaiting you"** panel where 3 candidate rows have flowed in, each with an avatar, name, a **score chip**, and a **verdict**: Maya Chen · 88 · Advance · Jordan Patel · 84 · Advance · Tariq Al-Ahmad · 41 · Reject (reject shown in muted grey, never red).

**The intended motion** (for the eventual build; show the story in stills): on load the job sits with agent OFF (quiet, desaturated) → the agent flips **ON** (the purple gradient lights up, a soft glow) → candidate rows flow into the decision lane one by one → verdict stamps land. Autoplay once, then hold; replayable. Tasteful, ~3–4s. (For mockups: show an "OFF" state and the settled "ON" state.)

This scene = "turn a job on, watch the agent work it." It is the page's hook.

---

## 5. Section-by-section content (exact copy where given)

**§2 Problem.** Big type, minimal visual. *"Everyone works with AI now."* / *"The CV can't prove it. The interview can't catch it."* / *"You need to watch them work."* Confident, short.

**§3 The funnel, once.** Eyebrow `THE FUNNEL`. H2: **"One agent, your whole funnel."** Sub: *"It finds candidates, reads every CV, runs the assessment, and puts a decision in front of you with the evidence attached. You approve. It executes."* Visual: one candidate moving left→right through 5 compact steps, each with a tiny real glimpse:
- **Source** — "Plugs into your ATS. Every candidate, role and JD flows in." (chips: workable · bullhorn · greenhouse)
- **Screen** — "Reads every CV against the role's real requirements. Weak fits gated with evidence, not guesswork." (a requirement-evidence row)
- **Assess** — "A task authored from your JD, battle-tested in a sandbox. Candidates pair with Claude on real work — engineering or knowledge work." (a mini score: 88/100)
- **Decide** — "A deterministic verdict on every candidate, the evidence attached." (an "Advance" verdict pill)
- **Hand back** — "Decisions, notes and reports written back to your ATS. The audit trail comes free." (a "synced" chip)

**§4 The wedge — AI fluency.** Eyebrow `THE DIFFERENTIATOR`. H2: **"Measure how people actually work with AI."** Sub: *"Five dimensions, scored from the real session. Planted traps they should catch. Verification that's scored, not assumed. Engineering or knowledge work — the same rubric for every candidate."* Visual: a **5-dimension scorecard** ("The 5 Ds") — rows with a label, a one-line definition, a filled bar, and a /100 score:
- **Delegation** — "Deciding what to own vs. hand to the agent." — 82
- **Description** — "Directing the agent — clear prompts, the right context." — 86
- **Discernment** — "Catching what the AI gets wrong." — 90
- **Diligence** — "Verifying before claiming done." — 80
- **Deliverable** — "What actually shipped, on its merits." — 84

(Always "the 5 Ds" — five dimensions. Never "4 Ds.")

**§5 You stay in control.** H2: **"The agent advises. You decide."** Four short control points beside a small decision-card glimpse:
- "Every consequential call is deterministic and evidence-linked."
- "Approve, override, or teach it back — in one click."
- "A full audit trail comes free."
- "It advises; it never acts on protected characteristics."

**§6 Proof + close.** A tight stat row (4 items, big-number/small-caption): *"Every task — battle-tested before use"* · *"Every decision — carries its evidence"* · *"Every session — captured turn by turn"* · *"Zero — webcams or lockdown browsers."* Then a closing CTA band: H2 **"Ready to put the agent to work?"** + "See it live" / "Book a demo", and a fat footer (Product · Solutions · Resources · Company · Legal columns + logo).

---

## 6. Copywriting rules

- **Voice:** confident, specific, human — NOT AI-sounding. Short declarative sentences. Concrete nouns (shortlist, evidence, audit trail, brief).
- **Every AI claim sits next to a human-control claim** (the trust move for hiring).
- Say **"works with AI"** — coding *and* knowledge work. Never "ship with AI" / "build with AI".
- **Banned words** (they read as AI-slop and lower trust): revolutionize, supercharge, unleash, harness, unlock, seamless, empower, elevate, transform (as filler), cutting-edge, next-generation, reimagine, game-changing, "at the speed of AI", "10x". No rule-of-three abstractions ("faster, smarter, fairer"), no "Whether you're X or Y", no em-dash triads.
- Nav labels: `The funnel · AI fluency · Control · Proof` + `Log in` + a primary `See it live`.

---

## 7. What has already been tried and REJECTED (do not repeat)

- **A dark, cinematic, scroll-scrubbed page** where scrolling scrubbed one long animation — "way too over the top." Keep it light; use tasteful autoplay-on-enter, not scroll-scrubbing.
- **An abstract "dot lattice" / particle hero background** — disliked ("horrible").
- **A standalone abstract ON/OFF toggle** in the hero — "doesn't fit." The agent-ON must be shown as the *real product* (a job turning on → candidates into the funnel), not an abstract switch.
- **An oversized product card** dominating the hero — "way too big." Right-size product visuals; they support, they don't dominate.
- **Generic icon+text value-pillar boxes** ("Screen every applicant in hours") — looked "cheap and Claude-designed, not unique." Anything product-y should look like the real Taali UI, not stock SaaS cards.
- **Repeating the same funnel** across hero + a "watch it work" section + pillars + feature bands — felt disjointed. Say each thing once (the fixed narrative above already does this).

The core lesson: **it must look genuinely designed and premium (Linear/Cursor-grade craft), while the product surfaces feel like the real Taali app** — grounded, not generic, not utilitarian, not over-animated.

---

## 8. Deliverable: 3+ distinct directions

Produce **at least three** genuinely different visual directions (not variations of one). Suggested axes to differentiate — pick or propose your own:

1. **Product-forward** — the real product UI is the star (Cursor-style). Crisp white cards, the agent-ON scene front and centre, dense but clean. Bet: "look how real and capable this is."
2. **Editorial / type-led** — big confident typography, generous whitespace, restrained (Linear/Vercel). Product surfaces appear smaller, as supporting proof. Bet: "this is a serious, premium platform."
3. **Warm / vivid purple** — leans into the brand purple and the agent-ON gradient as a hero visual language (gradients, soft glows, more colour), while staying light and tasteful. Bet: "distinctive, ownable, alive."

For each direction, mock: the **hero** (with the job-on → decision-lane scene, OFF and ON states), and at least the **funnel (§3)** and **AI-fluency wedge (§4)** sections. Desktop first; include a mobile hero.

**Constraints for all directions:** light theme; purple family only (no red/amber/green); Geist type; the exact copy above; the fixed 6-section narrative; premium & restrained; product surfaces grounded (look like real Taali UI). Accessible contrast; responsive; no reliance on heavy scroll-scrubbing.

---

*Everything in §2 (narrative), §3 (brand), §4 (hero motif) and §6 (copy rules) is fixed and founder-approved. §7 lists dead ends. §8 is where you have creative latitude — surprise us within the guardrails.*
