---
title: "Working AI-Native: A Field Guide to Coding and Knowledge Work in 2026"
date: 2026-06-27
author: Taali
tags: [ai-native, claude, agentic-coding, knowledge-work, ai-fluency]
description: >
  The durable craft of working with AI — for engineers and everyone else.
  What the best practitioners actually do, what separates them from the people
  producing "workslop", and the primary sources to learn it from.
---

# Working AI-Native: A Field Guide to Coding and Knowledge Work in 2026

There are now two kinds of people using AI at work.

The first treats the model like a vending machine: type a vague request, take
whatever falls out, ship it. The second treats it like a fast, eager, slightly
unreliable colleague: they set it up with context, point it at the right
problem, watch what it does, catch its mistakes, and own the result.

Both are "using AI." Only one is **AI-native**. And the gap between them is the
most important skill story in knowledge work right now — because, as Google's
[DORA 2025 report](https://dora.dev/dora-report-2025/) put it bluntly, **AI is
an amplifier**: *"AI doesn't fix a team; it amplifies what's already there.
Strong teams use AI to become even better… struggling teams will find that AI
only highlights and intensifies their existing problems"*
([Google Cloud](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report)).

This is a field guide to being the second kind of person — for code, and for
everything else. It's drawn from primary sources: Anthropic's engineering
documentation, company engineering blogs, and the practitioners who've been
loudest and most useful about how this actually works. Every claim links to
where we got it.

---

## The two truths underneath everything

Before the tactics, two ideas that explain *why* the tactics matter.

**1. Verification is the new bottleneck.** When generating a draft costs
seconds, the expensive, irreplaceable, human-owned step becomes *checking that
the draft is right*. Anthropic's own [Claude Code best
practices](https://code.claude.com/docs/en/best-practices) put it as a design
principle: *"Give Claude a check it can run… Without a check it can run, 'looks
done' is the only signal available, and you become the verification loop."*
Simon Willison's long-standing personal rule is the human version of the same
idea — don't commit code you couldn't explain to someone else.

Shopify's head of engineering, Farhan Thawar, makes the organizational version
of the argument in his Compile 26 talk, [*"What Is Your Job
Now?"*](https://www.youtube.com/watch?v=ByOF8qByGHU) — when AI writes most of
the code, [**the bottleneck always
moves**](https://www.youtube.com/watch?v=ByOF8qByGHU&t=273s): generation gets
cheap, so the constraint shifts downstream to review, judgment, and deciding
what's worth building. Shopify still doesn't let AI check code into its repos
unreviewed; a human owns every merge.

**2. AI amplifies expertise; it doesn't replace it.** The most-cited evidence
here is uncomfortable: in a randomized trial by [METR](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/),
experienced open-source developers were **~19% slower** with AI tools on their
own repos — while *believing* they were ~20% faster. (Worth knowing: METR was
**not** retracted, despite rumors; their [Feb 2026
update](https://metr.org/blog/2026-02-24-uplift-update/) flags a selection
effect, and the returning cohort still showed −18%.) The lesson isn't "AI is
useless" — it's that the *feeling* of speed is not the same as being fast, and
the people who win measure instead of vibe.

Hold those two truths in your head and most of the "best practices" below stop
being arbitrary rules and start being obvious.

---

## One framework for all of it: the 4 Ds

The cleanest model of human AI skill comes from Anthropic's
[AI Fluency framework](https://www.anthropic.com/learn/claude-for-you) — the
**4 Ds**. They apply identically to writing code and to writing a board deck:

- **Delegation** — deciding *whether, when, and what* to hand to the AI versus
  own yourself. Decomposing the problem; making the load-bearing calls; knowing
  when to abandon the AI approach entirely.
- **Description** — describing intent so the AI behaves usefully. Prompts,
  context, examples, the memory files you give it.
- **Discernment** — critically evaluating what comes back. Catching the wrong,
  the incomplete, the plausible-but-false.
- **Diligence** — taking responsibility for the result. Verifying, fact-checking,
  owning the residual risk.

That's the whole game. Everything below is a concrete expression of one of these
four disciplines.

---

## Part 1 — AI-native coding

Start with what the job *is* now. In [*"What Is Your Job
Now?"*](https://www.youtube.com/watch?v=ByOF8qByGHU) (Compile 26), Shopify's
Farhan Thawar walks through [how the SDLC itself is
shifting](https://www.youtube.com/watch?v=ByOF8qByGHU&t=55s) once agents write
most of the code: the engineer's day moves from authoring lines to directing
parallel agents, reviewing their output, and merging what survives scrutiny —
with the hard-won caveat that [a prototype is not
production](https://www.youtube.com/watch?v=ByOF8qByGHU&t=1179s). The loop
below — memory, plan, skills, context, verification — is that new job
description in practice.

### Give the agent a memory: `CLAUDE.md` and `AGENTS.md`

The single highest-leverage habit is the most boring one: a checked-in markdown
file the agent reads at the start of every session. In Claude Code it's
[`CLAUDE.md`](https://code.claude.com/docs/en/memory); the cross-tool open
standard is [`AGENTS.md`](https://agents.md/), now adopted by 60,000+ projects
and governed by the Linux Foundation's [Agentic AI
Foundation](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)
(launched December 2025, with OpenAI donating AGENTS.md and Anthropic donating
MCP).

Do it well:

- **Generate, then prune.** Run `/init` to bootstrap one, then add the things
  the agent can't discover on its own — non-obvious build/test commands, repo
  conventions, gotchas.
- **Keep it lean — under ~150–200 lines.** Anthropic is explicit: *"Bloated
  CLAUDE.md files cause Claude to ignore your actual instructions."* For each
  line, ask whether removing it would cause a mistake. A bloated memory file
  isn't diligence; it's noise the model learns to skip.

Stripe runs this at scale — [rule files synced across Claude Code, Cursor, and
their internal agents](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2).

### Plan first, then build

The difference between a senior engineer and a junior one with the same model is
often just this: the senior plans before generating.

Anthropic's recommended loop is **Explore → Plan → Code → Commit**: read the
relevant code, ask for a detailed plan, *edit the plan*, then implement against
it ([best practices](https://code.claude.com/docs/en/best-practices)). For
anything non-trivial, go further into **spec-driven development**: GitHub's
[Spec Kit](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/)
frames it as *"intent is the source of truth"* with a **Specify → Plan → Tasks →
Implement** flow; AWS's [Kiro](https://kiro.dev/docs/specs/) uses
`requirements.md → design.md → tasks.md`. The point, in GitHub's words: *"a
vague prompt… forces the model to guess at thousands of unstated
requirements."* A spec front-loads exactly the judgment the model is worst at
inferring — and gives you something reviewable before any code exists.

The flip side is knowing when *not* to plan: for a one-sentence diff, just ask
for the change.

### Skills: turn a good workflow into reusable leverage

A [Skill](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
is a folder with a `SKILL.md` (instructions + YAML frontmatter) plus optional
scripts, that the agent loads on demand. The clever part is **progressive
disclosure**: only the name and description sit in context until the Skill is
relevant, so a library of them costs almost nothing until used. Write the
description to be high-signal and slightly "pushy" (models tend to *under*-trigger
Skills), and bundle scripts for anything that should be deterministic. Build them
from real failures, keep them small, iterate.

### Engineer the context

Anthropic calls [context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
*"the #1 job of engineers building AI agents."* Context is a finite resource;
performance degrades as the window fills ("context rot"). The disciplines:
`/clear` between unrelated tasks, `/compact` for long sessions, just-in-time
retrieval (hold file paths and queries, load data when needed), and — when a
side quest would flood your main thread with logs — delegate it to a
[subagent](https://code.claude.com/docs/en/sub-agents) with its own context
window. A nice pattern: have a *different* agent review the diff than the one
that wrote it, so the reviewer isn't biased toward code it just produced.

And if you connect external tools, resist the urge to wire up everything:
*"more tools don't always lead to better outcomes"* — build [a few thoughtful
tools](https://www.anthropic.com/engineering/writing-tools-for-agents) for
high-impact workflows, and scope [MCP](https://www.anthropic.com/news/model-context-protocol)
servers tightly (treat third-party tool output as untrusted input).

### Close the loop, then measure it

Give the agent a verifiable signal — tests, a build, a linter, a screenshot
diff — and demand evidence, not assertions. Then measure your *own* workflow.
This is the antidote to the METR perception gap: track task time, retries, and
tokens instead of trusting how fast it felt. As Hamel Husain argues, [evals are
the new unit tests](https://hamel.dev/blog/posts/evals/) — *"if you streamline
your evaluation process, all other activities become easy."* Start with 20–50
real tasks, prefer binary good/bad labels, and grade the *trial* (the whole
trajectory) not just the final output ([Anthropic on
evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)).

---

## Part 2 — AI-native knowledge work

Here's the part most "AI for coding" guides skip: **the exact same craft applies
to design, decks, plans, and writing.** Anthropic's own finance team describes
the split perfectly — use Claude for *"diagnostic work — tracing references,
drafting first passes, pulling context — while keeping humans responsible for
judgment: framing, scenario questions, and forward-looking analyses"*
([Anthropic finance team](https://claude.com/blog/how-anthropics-finance-team-uses-claude-to-shape-the-narrative-behind-the-numbers)).

### Design

Generate the first draft, then design the hard parts. Tools like [Figma First
Draft](https://help.figma.com/hc/en-us/articles/23955143044247-Use-First-Draft-with-Figma-AI)
turn a prompt into editable wireframes in minutes — so spend the saved time on
the edge cases AI skips (loading, empty, and error states). Anthropic's product
designers use Claude to map those system states *during* design rather than
during development ([how Anthropic teams use Claude
Code](https://claude.com/blog/how-anthropic-teams-use-claude-code)). The
usefulness scales with your design system: connected to a complete component
library it produces on-brand work; [connected to
nothing](https://help.figma.com/hc/en-us/articles/38978644498199-Best-practices-to-help-Figma-AI-understand-your-design-system)
it produces defaults that need rework. The antidote to "AI slop" is intent —
[*"speed without intention produces mediocrity at
scale."*](https://www.925studios.co/blog/ai-slop-web-design-guide)

### Presentations and decks

Storyline first, slides second. Draft and pressure-test the *narrative* — the
consulting SCR/SCQA arc and the [Minto Pyramid](https://slideworks.io/resources/how-to-use-McKinseys-scr-framework-with-examples) —
before generating a single slide. Then let Claude's built-in [document
Skills](https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude)
(pptx/docx/xlsx) do the build, fed brand and audience context. And verify every
number: the finance team's habit is to ask the model to *"validate that every
number and claim reconciles to a single source of truth,"* re-running the check
on each data refresh. (The market agrees this is real work: [Gamma](https://techcrunch.com/2025/11/10/ai-powerpoint-killer-gamma-hits-2-1b-valuation-100m-arr-founder-says/),
"Cursor for slides," hit $100M+ ARR and 70M users in 2025.)

### Plans, PRDs, and strategy

AI drafts; the human owns the decisions. PMs report saving hours auto-drafting
PRDs — but [the consensus](https://chisellabs.com/blog/how-to-write-prd-using-ai/)
is that *"AI is a starting point, not an ending point… it couldn't fully capture
organizational context, internal politics, constraints, or hidden priorities."*
Amazon's [working-backwards PR/FAQ](https://workingbackwards.com/concepts/working-backwards-pr-faq-process/)
— writing the future press release *before* you build — is a near-perfect
AI-drafting frame: cheap to produce, decision-forcing, and the human still owns
every call.

### Writing and research

Give the model your voice as persistent context, then run **draft → critique →
revise** loops rather than shipping the first pass. And treat citations as
sacred: roughly 712 court decisions worldwide have addressed AI-hallucinated
content, the vast majority in 2025, [with real
sanctions](https://news.bloomberglaw.com/legal-ops-and-tech/ai-faked-cases-become-core-issue-irritating-overworked-judges).
For serious research, fan out and verify — Anthropic's [multi-agent research
system](https://www.anthropic.com/engineering/multi-agent-research-system) uses
parallel sub-agents and *"a separate Claude with its own context window to
verify every citation before anything reaches the user"* (at ~15× the tokens of
chat, so reserve it for high-value work). *(This very post was researched that
way — six parallel research agents, each citing primary sources, cross-checked
before writing.)*

---

## What weak AI-native work looks like

You can't define "good" without naming the failure modes — and they're
remarkably consistent across code and knowledge work:

- **Workslop.** Coined by BetterUp Labs and Stanford in [HBR (Sept
  2025)](https://hbr.org/2025/09/ai-generated-workslop-is-destroying-productivity):
  *"AI-generated content that masquerades as good work but lacks the substance to
  advance a task."* 41% of workers received it in a single month; each instance
  cost ~2 hours of rework, and 42% trusted the sender less afterward.
- **Automation complacency.** A run of good AI suggestions lulls you into
  accepting a bad one. Addy Osmani calls the result ["house of cards
  code"](https://addyo.substack.com/p/the-70-problem-hard-truths-about) — *"it
  looks complete but collapses under real-world pressure."*
- **Comprehension debt.** [*"It's trivially easy to review code you can no
  longer write from scratch… if your ability to read doesn't scale with the
  agent's ability to output, you're not engineering, you're
  hoping."*](https://addyo.substack.com/p/the-80-problem-in-agentic-coding)
  Thawar's framing of the same risk: [**learning is the
  collateral**](https://www.youtube.com/watch?v=ByOF8qByGHU&t=193s) — delegate
  the typing, not the understanding. His rule of thumb is that engineers should
  still understand the system two or three layers below where they work, and
  use the agent to accelerate that learning rather than skip it.
- **Hallucinated facts, generic slop, and lost voice** — the knowledge-work
  equivalents, all preventable with verification and intent.

The throughline: the artifact existing is not the same as the work being done.

---

## The proficiency ladder

| Discipline | Weak | Strong |
|---|---|---|
| **Delegation** | "Do it all"; lets AI make load-bearing calls | Decomposes; owns the design decisions; plans/specs first; knows when to abandon AI |
| **Description** | Vague one-liners; no context or memory files | Rich, specific direction; maintains a lean `AGENTS.md`; builds reusable Skills/templates |
| **Discernment** | Accepts output at face value; ships slop | Catches wrong/incomplete output; rejects bad approaches |
| **Diligence** | "Done" with no verification; can't explain it | Verifies before claiming done; explains every line/number; owns residual risk |

---

## How leading orgs operationalize this

Individual habits become culture when companies build for them:

- **Golden-path agent tooling.** Stripe's [Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2)
  ship 1,300+ merged PRs/week against an internal MCP "Toolshed" of ~500 tools;
  Atlassian's [Rovo Dev](https://www.atlassian.com/blog/ai-at-work/developer-productivity-improved-with-rovo-dev)
  runs an eval-gated, LLM-as-judge code reviewer across 1,900+ repos (median PR
  cycle time down 30.8%).
- **Shared Skill libraries.** Anthropic packages workflows as version-controlled,
  company-wide [Skills](https://www.anthropic.com/research/how-ai-is-transforming-work-at-anthropic);
  Intercom built domain ["guidance skills"](https://ideas.fin.ai/p/we-gave-claude-code-to-everyone-at)
  and made technical work "agent-first."
- **Enablement, not mandates-only.** GitHub runs an internal ["AI for Everyone"
  champions network](https://github.com/resources/insights/activating-internal-ai-champions).
- **Culture set from the top.** Shopify made [reflexive AI usage a baseline
  expectation](https://www.youtube.com/watch?v=ByOF8qByGHU&t=478s) via CEO Tobi
  Lütke's 2025 memo — teams must show why AI *can't* do a job before asking for
  headcount, AI usage is part of performance reviews, and progress is judged by
  weekly demos rather than output metrics. The same conviction runs down to
  hiring: Thawar's team is [hiring ~1,000
  interns](https://www.youtube.com/watch?v=ByOF8qByGHU&t=677s) on the bet that
  AI-native juniors plus agents outrun headcount-as-usual.
- **Non-engineering, too.** McKinsey's [Lilli](https://www.mckinsey.com/capabilities/tech-and-ai/how-we-help-clients/rewiring-the-way-mckinsey-works-with-lilli)
  reaches ~75% of staff monthly; Anthropic's [legal team](https://claude.com/blog/how-anthropic-uses-claude-legal)
  cut a marketing-review turnaround from 2–3 days to 24 hours with a legal
  "skill" plus MCP into their document stores.

---

## Why we measure this (and where to get certified)

At [Taali](https://taali.ai) this is more than a reading list — it's what we
assess. Traditional technical screens ask whether someone can code unaided; the
more predictive question in 2026 is *how they work with AI*: do they own the
load-bearing decisions, set up their context, catch the model's mistakes, and
verify before shipping? We score that against Anthropic's AI Fluency framework
(the 4 Ds) so the result is grounded in a public, named standard rather than a
bespoke vibe.

If you want to level up your own fluency, two Anthropic resources are the place
to start:

- **[AI Fluency: Framework & Foundations](https://www.anthropic.com/learn/claude-for-you)**
  — the free course (with a completion certificate) that defines the 4 Ds.
- **[Claude Certified Architect — Foundations (CCA-F)](https://dev.to/aws-builders/the-claude-certified-architect-exam-5-domains-6-scenarios-and-everything-you-need-to-know-4le3)**
  — Anthropic's first technical certification (March 2026) for people *building*
  production Claude systems, spanning agentic architecture, Claude Code
  workflows, prompt engineering, tool/MCP design, and context management.

---

## Further reading — the primary sources

Everything above, grouped so you can go deeper.

**Anthropic — engineering & docs**
- [Claude Code best practices](https://code.claude.com/docs/en/best-practices) ·
  [Memory / CLAUDE.md](https://code.claude.com/docs/en/memory) ·
  [Subagents](https://code.claude.com/docs/en/sub-agents) ·
  [Permissions](https://code.claude.com/docs/en/permissions) ·
  [Headless mode](https://code.claude.com/docs/en/headless)
- [Equipping agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) ·
  [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) ·
  [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) ·
  [Building effective agents](https://www.anthropic.com/research/building-effective-agents)
- [Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) ·
  [Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) ·
  [Model Context Protocol](https://www.anthropic.com/news/model-context-protocol)
- [How Anthropic teams use Claude Code](https://claude.com/blog/how-anthropic-teams-use-claude-code) ·
  [Finance team](https://claude.com/blog/how-anthropics-finance-team-uses-claude-to-shape-the-narrative-behind-the-numbers) ·
  [Legal team](https://claude.com/blog/how-anthropic-uses-claude-legal) ·
  [AI Fluency course](https://www.anthropic.com/learn/claude-for-you)

**Standards & spec-driven development**
- [AGENTS.md](https://agents.md/) ·
  [Agentic AI Foundation](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation) ·
  [GitHub Spec Kit](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/) ·
  [AWS Kiro specs](https://kiro.dev/docs/specs/)

**Practitioners**
- Farhan Thawar (Shopify): [What Is Your Job Now? — Compile 26](https://www.youtube.com/watch?v=ByOF8qByGHU)
  — how the SDLC shifts when agents write most of the code
- Simon Willison: [Vibe engineering](https://simonwillison.net/2025/Oct/7/vibe-engineering/) ·
  [Designing agentic loops](https://simonwillison.net/2025/Sep/30/designing-agentic-loops/)
- Addy Osmani: [The 70% Problem](https://addyo.substack.com/p/the-70-problem-hard-truths-about) ·
  [The 80% Problem](https://addyo.substack.com/p/the-80-problem-in-agentic-coding)
- Armin Ronacher: [Agentic coding recommendations](https://lucumr.pocoo.org/2025/6/12/agentic-coding/) ·
  Hamel Husain: [Your AI product needs evals](https://hamel.dev/blog/posts/evals/)

**Research & measurement**
- [DORA 2025 report](https://dora.dev/dora-report-2025/) ·
  [Google Cloud summary](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report) ·
  [DORA AI Capabilities Model](https://cloud.google.com/blog/products/ai-machine-learning/introducing-doras-inaugural-ai-capabilities-model)
- METR: [uplift study](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) ·
  [Feb-2026 update](https://metr.org/blog/2026-02-24-uplift-update/)
- [HBR — AI "workslop"](https://hbr.org/2025/09/ai-generated-workslop-is-destroying-productivity) ·
  [Bloomberg Law — AI-faked citations](https://news.bloomberglaw.com/legal-ops-and-tech/ai-faked-cases-become-core-issue-irritating-overworked-judges)

**Company practice & non-coding craft**
- [Stripe Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2) ·
  [Atlassian Rovo Dev](https://www.atlassian.com/blog/ai-at-work/developer-productivity-improved-with-rovo-dev) ·
  [Intercom](https://ideas.fin.ai/p/we-gave-claude-code-to-everyone-at) ·
  [GitHub AI champions](https://github.com/resources/insights/activating-internal-ai-champions) ·
  [McKinsey Lilli](https://www.mckinsey.com/capabilities/tech-and-ai/how-we-help-clients/rewiring-the-way-mckinsey-works-with-lilli)
- [Figma First Draft](https://help.figma.com/hc/en-us/articles/23955143044247-Use-First-Draft-with-Figma-AI) ·
  [Working Backwards PR/FAQ](https://workingbackwards.com/concepts/working-backwards-pr-faq-process/) ·
  [McKinsey SCR framework](https://slideworks.io/resources/how-to-use-McKinseys-scr-framework-with-examples) ·
  [Gamma](https://techcrunch.com/2025/11/10/ai-powerpoint-killer-gamma-hits-2-1b-valuation-100m-arr-founder-says/)

---

*A note on sourcing: a few widely-circulated figures didn't survive
fact-checking and were left out — the claim that METR was "retracted" (it
wasn't), and several unverifiable adoption stats. Where a number is a
self-reported survey estimate (e.g. the workslop figures) we've said so. If you
spot something that's drifted, tell us — verification is the whole point.*
