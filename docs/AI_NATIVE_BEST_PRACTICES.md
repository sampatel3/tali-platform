# AI-Native Best Practices — Coding and Knowledge Work

**Date:** 2026-06-27
**Status:** Reference (collated from deep research — 6 parallel streams, primary-source weighted)
**Companion:** [`AI_NATIVE_PRACTICES_ASSESSMENT_INTEGRATION.md`](./AI_NATIVE_PRACTICES_ASSESSMENT_INTEGRATION.md) — how Taali assesses these.

> **Why this exists.** Taali's assessment already scores *how a candidate steers an agent on a single
> task* (the AI-Fluency 4 Ds — see [`SCORING_SCORECARD.md`](./SCORING_SCORECARD.md)). It does **not** yet
> capture whether a candidate knows and uses the **craft and artifacts of AI-native work** — the
> `CLAUDE.md`/`AGENTS.md` memory files, Skills (`SKILL.md`), plan-/spec-first workflows, context hygiene,
> verification loops, evals — for coding **and** for non-coding deliverables (design, decks, plans). This
> document collates that craft into a clear, cited set of best practices. The companion doc designs the
> assessment integration.

> **Sourcing note.** Claims are cited inline to primary sources where possible (Anthropic engineering/docs,
> company engineering blogs, named practitioners). A few load-bearing accuracy flags from the research are
> preserved in **§9 Caveats** — read them before quoting figures externally.

---

## 0. The unifying frame

Every practice below is an expression of one model and three durable truths.

**The model — Anthropic's AI Fluency "4 Ds."** Working well with AI is four learnable disciplines, and they
apply identically to code and to documents:

| Discipline | Coding | Knowledge work (design / decks / plans / writing) |
|---|---|---|
| **Delegation** — decide *whether/what/when* to hand to AI vs. own | Decompose the task; own the load-bearing design calls; pick the model/tool | Frame the problem; decide which parts AI drafts vs. which need human judgment |
| **Description** — describe intent so the AI behaves usefully | Prompt + context engineering; `CLAUDE.md`; specs | Give source material, audience, brand/voice, examples, constraints |
| **Discernment** — critically evaluate AI output | Catch wrong/incomplete code; reject bad approaches | Spot slop, hallucinated facts, off-brand or off-voice output |
| **Diligence** — take responsibility for the result | Verify with tests/types; explain every line; own residual risk | Fact-check, reconcile numbers, edit to your voice, own what ships |

A fifth axis — **Deliverable** (was the shipped artifact actually correct/good) — completes Taali's scorecard.
(Anthropic AI Fluency framework; see [`SCORING_SCORECARD.md`](./SCORING_SCORECARD.md).)

**Three durable truths that shape every practice:**

1. **AI is an amplifier, not a fixer.** DORA 2025: AI's "primary role is as an amplifier, magnifying an
   organization's existing strengths and weaknesses… AI doesn't fix a team; it amplifies what's already
   there" ([dora.dev](https://dora.dev/dora-report-2025/); [Google Cloud](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report)).
   The individual-level echo (Simon Willison): "the more skills and experience you have… the faster and
   better the results you can get" ([Vibe engineering, Oct 2025](https://simonwillison.net/2025/Oct/7/vibe-engineering/)).
2. **Verification is the new bottleneck.** Generation is nearly free; confirming correctness is the binding
   constraint, and it stays human-owned. Treat output as a draft from an eager junior, build the AI a check
   it can run, and don't become the verification loop yourself ([Claude Code best practices](https://code.claude.com/docs/en/best-practices); [Osmani, "The 70% Problem"](https://addyo.substack.com/p/the-70-problem-hard-truths-about)).
3. **Context is a finite resource you must engineer.** Performance degrades as the window fills ("context
   rot"); the job is to curate "the smallest set of high-signal tokens that maximize the likelihood of the
   desired outcome" ([Anthropic, Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

---

# Part I — Coding

## 1. Persistent context: `CLAUDE.md` / `AGENTS.md` (memory files)

**What it is.** A checked-in markdown file the agent reads at the start of every session, carrying project
context across an otherwise-fresh context window ([memory docs](https://code.claude.com/docs/en/memory)).

- **Generate, then prune.** Run `/init` to auto-generate a starter `CLAUDE.md` (it detects build system, test
  framework, code patterns), then refine with things the agent can't discover itself ([best practices](https://code.claude.com/docs/en/best-practices)).
- **Put in:** non-obvious bash/test commands, code-style rules that differ from defaults, repo etiquette
  (branch/PR conventions), architecture decisions, environment gotchas. **Leave out:** anything readable from
  the code, standard language conventions, detailed API docs (link instead), per-file descriptions, and
  self-evident advice ([best practices](https://code.claude.com/docs/en/best-practices)).
- **Keep it lean — under ~150–200 lines.** "Bloated `CLAUDE.md` files cause Claude to ignore your actual
  instructions." For each line ask: "would removing this cause a mistake?" Push sometimes-relevant procedures
  into Skills and path-scoped rules (`.claude/rules/` with `paths:` frontmatter) instead ([memory docs](https://code.claude.com/docs/en/memory)).
- **Know the hierarchy.** Enterprise/managed → user (`~/.claude/CLAUDE.md`) → project (`./CLAUDE.md`) → local
  (`./CLAUDE.local.md`, gitignored). Files concatenate up the tree; subdirectory files load on demand. Use
  `@path` imports for organization (note: imports don't reduce context size) ([memory docs](https://code.claude.com/docs/en/memory)).
- **`AGENTS.md` is the cross-tool open standard.** A vendor-neutral "README for agents," adopted by 60,000+
  projects and tools (Cursor, Copilot, Codex, Gemini CLI, etc.) and now governed by the Linux Foundation's
  **Agentic AI Foundation** (launched Dec 2025; OpenAI donated AGENTS.md, Anthropic donated MCP)
  ([agents.md](https://agents.md/); [Linux Foundation](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)).
  Emerging team convention: `CLAUDE.md` for Claude-specific context, `AGENTS.md` as the portable cross-agent
  standard. Stripe maintains rule files synced across Claude Code, Cursor, and its internal agents
  ([Stripe Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2)).

## 2. Skills (`SKILL.md`)

**What it is.** A folder containing a `SKILL.md` (instructions + YAML frontmatter) plus optional bundled
scripts/resources, that the agent discovers and loads on demand to specialize at a task — an open format that
runs across Claude.ai, Claude Code, the Agent SDK, and the API ([Anthropic, Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).

- **Frontmatter is the trigger.** Only `name` + `description` are preloaded into the system prompt; write a
  high-signal `description` covering *what it does and when to use it*. Descriptions should lean slightly
  "pushy" because models tend to under-trigger Skills ([Anthropic](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills); [Skill authoring patterns](https://generativeprogrammer.com/p/skill-authoring-patterns-from-anthropics)).
- **Progressive disclosure (3 levels):** (1) name+description in the prompt; (2) full `SKILL.md` loads on a
  match; (3) `SKILL.md` points to extra files (`reference.md`, `forms.md`) loaded only when needed — so
  bundled context is effectively unbounded because it's never all loaded at once ([Anthropic](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).
- **Bundle scripts for determinism.** Ship pre-written code the agent *executes* rather than reasons through,
  pushing deterministic work out of the token budget ([Anthropic](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).
- **Build from evals; keep them small; iterate.** Run the agent on real tasks, find capability gaps, add
  Skills incrementally; split a `SKILL.md` when it grows unwieldy. Skills *complement* MCP (MCP supplies the
  connections; a Skill supplies the procedural know-how) ([Anthropic](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).
- **Custom slash commands are now Skills.** `.claude/commands/<name>.md` still creates `/name`, but Anthropic
  has merged the concept into Skills (`.claude/skills/<name>/SKILL.md`); a Skill wins a name collision
  ([skills docs](https://code.claude.com/docs/en/skills)).

## 3. The agentic loops

**What it is.** Structured workflows that separate research/planning from execution and give the agent a
verification signal ([best practices](https://code.claude.com/docs/en/best-practices)).

- **Explore → Plan → Code → Commit.** Use plan mode to read code without editing, ask for a detailed plan,
  edit the plan, then implement and commit. Skipping explore/plan makes the agent "jump straight to coding"
  and risk solving the wrong problem. *Skip the plan only for one-sentence-diff changes* ([best practices](https://code.claude.com/docs/en/best-practices)).
- **Spec-driven for non-trivial work.** "Intent is the source of truth": write the spec/plan first, let the
  agent implement against it. GitHub Spec Kit's flow is **Specify → Plan → Tasks → Implement** (+ a
  `constitution.md` of non-negotiables); AWS Kiro's is `requirements.md → design.md → tasks.md`. A spec
  captures user journeys, problem, and acceptance criteria — and front-loads the judgment agents are worst at
  inferring. "A vague prompt… forces the model to guess at thousands of unstated requirements"
  ([GitHub Spec Kit](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/); [Kiro specs](https://kiro.dev/docs/specs/)).
- **Test-driven with agents.** Ask for tests from input/output pairs; *say you're doing TDD* so it doesn't
  write mock implementations; then implement until green. "A robust… test suite [lets] agentic coding tools
  fly" because the agent that can execute its own code can self-verify ([best practices](https://code.claude.com/docs/en/best-practices); [Willison, Designing agentic loops](https://simonwillison.net/2025/Sep/30/designing-agentic-loops/)).
- **Visual iteration for UI.** Give the agent a screenshot/design, have it implement, screenshot the result,
  compare, and fix the diffs ([best practices](https://code.claude.com/docs/en/best-practices)).

## 4. Subagents & parallelism

**What it is.** Specialized assistants that run in their own context window with their own tools, plus running
multiple sessions in parallel ([subagents docs](https://code.claude.com/docs/en/sub-agents)).

- **Delegate to preserve context.** Best when "a side task would flood your main conversation with search
  results, logs, or file contents you won't reference again." Define in `.claude/agents/<name>.md`; the
  `description` drives auto-delegation ([subagents docs](https://code.claude.com/docs/en/sub-agents)).
- **Use a *different* reviewer than the author.** A fresh-context subagent that sees only the diff + criteria
  reviews with less bias toward code it just wrote ([best practices](https://code.claude.com/docs/en/best-practices)).
- **Parallelize with git worktrees** (isolated checkouts so edits don't collide) or Claude Code on the web
  (isolated cloud VMs); patterns include writer/reviewer and one-writes-tests/another-writes-code
  ([worktrees](https://code.claude.com/docs/en/worktrees)).

## 5. Context-management discipline

- **`/clear` between unrelated tasks; don't run a "kitchen-sink" session.** Irrelevant context degrades
  performance ([best practices](https://code.claude.com/docs/en/best-practices)).
- **`/compact` for long horizons** — summarize and reinitialize, preserving architectural decisions, open
  bugs, key details; discard redundant tool output ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
- **Just-in-time retrieval.** Hold lightweight references (paths, URLs, queries); load data at runtime rather
  than pre-loading everything ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
- **Course-correct early.** After two failed corrections on the same issue, `/clear` and write a better
  prompt — a clean session beats a long polluted one ([best practices](https://code.claude.com/docs/en/best-practices)).
- **System prompts at the "right altitude"** — specific enough to guide, flexible enough to give strong
  heuristics; organize with XML/markdown sections ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

## 6. MCP & tool design

- **MCP** is the open standard ("USB-C for AI apps") for connecting agents to external data/tools; the host
  approves servers (tools / resources / prompts) ([Anthropic, MCP](https://www.anthropic.com/news/model-context-protocol)).
- **Don't over-load tools.** "More tools don't always lead to better outcomes" — build a few thoughtful tools
  for high-impact workflows; consolidate overlapping low-level tools; namespace related ones. As tool counts
  grow, prefer *code execution with MCP* so definitions/results load on demand instead of flooding context
  ([Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents); [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)).
- **Design tools like onboarding docs.** Find "the smallest set of high-signal tokens"; return semantic data
  not opaque UUIDs; paginate/filter/truncate with sane defaults; write actionable error messages; tune
  against evals ([Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- **Security:** scope server access, approve servers explicitly, treat third-party servers and their tool
  output as untrusted (prompt-injection / poisoned-tool risk) ([Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)).

## 7. Permissions & safe operation

- **Cut prompts safely** with allowlists (`/permissions`, `permissions.allow` in `settings.json`), **auto
  mode** (a classifier blocks only risky commands), or **sandboxing** (OS-level isolation). Rules evaluate
  deny → ask → allow ([permissions docs](https://code.claude.com/docs/en/permissions); [auto mode](https://www.anthropic.com/engineering/claude-code-auto-mode)).
- **`--dangerously-skip-permissions` is for isolated containers only.** Skipping all prompts "is risky and can
  result in data loss, system corruption, or even data exfiltration (e.g., via prompt injection)." Use only in
  an internet-less VM ([permissions docs](https://code.claude.com/docs/en/permissions)).
- **Headless for automation.** `claude -p` (non-interactive) for CI/hooks/pipelines; `--output-format json`,
  pipe data in/out, scope with `--allowedTools`; test a fan-out on 2–3 files before running at scale
  ([headless docs](https://code.claude.com/docs/en/headless)).

## 8. Verification & ownership

- **Build the agent a check, don't be the check.** Tests, a build exit code, a linter, a fixture-diff, or a
  screenshot comparison. Escalate gating from in-prompt → `/goal` (re-checked each turn) → a Stop hook
  (blocks turn end until green) → a verification subagent that tries to *refute* the result ([best practices](https://code.claude.com/docs/en/best-practices)).
- **Demand evidence, not assertions** — show the test output / the command and its return / a screenshot.
  Address root causes, not symptoms ([best practices](https://code.claude.com/docs/en/best-practices)).
- **Be able to explain every committed line.** Willison's long-standing rule: don't commit code you couldn't
  explain to someone else. (Genuine, widely cited — predates the 2025 "vibe engineering" post.)
- **Calibrate reliance (appropriate reliance).** Neither blind acceptance (automation complacency) nor blanket
  distrust. "Having 100% trust in the output is wrong. Having 0% trust is wrong" (DORA's Nathen Harvey). The
  measurable constructs: switch *to* good AI advice (RAIR), *reject* wrong AI advice (RSR); self-reported
  confidence does **not** predict appropriate reliance — you must test verification ([Schemmer et al.](https://link.springer.com/article/10.1007/s00146-025-02422-7)).
- **Know when to abandon the AI approach.** Reserve full autonomy for *measurable* tasks (porting, perf with
  metrics, security scans); abandon when "the model misunderstands something early and builds a feature on
  faulty premises" ([Ronacher](https://lucumr.pocoo.org/2025/6/12/agentic-coding/); [Osmani, "The 80% Problem"](https://addyo.substack.com/p/the-80-problem-in-agentic-coding)).

## 9. Evals — measure, don't trust vibes

- **Evals are the new unit tests.** "If you streamline your evaluation process, all other activities become
  easy." Start with 20–50 realistic tasks drawn from real failures; prefer binary good/bad labels; calibrate
  any LLM-judge to human labels ([Husain, evals](https://hamel.dev/blog/posts/evals/); [Anthropic, demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)).
- **Grade the *trial*, not just the output.** A trial = the full transcript (tool calls, reasoning,
  intermediate results) + the outcome (the real end-state — e.g. the row actually exists in the DB). Grade
  what was produced, not the path; allow partial credit ([Anthropic, demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)).
- **Measure your own workflow** (task time, retries, tokens, tool errors) instead of self-reported speed —
  the antidote to the METR perception gap below ([Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- **Add agentic complexity only when justified.** "The most successful implementations use simple, composable
  patterns rather than complex frameworks" ([Building effective agents](https://www.anthropic.com/research/building-effective-agents)).

---

# Part II — Knowledge work (design · decks · plans · writing · research)

**The craft transfers.** The same 4 Ds govern non-coding work — give the model the source material, audience,
brand/voice, and constraints up front (context engineering for documents); review and own the output. Anthropic's
finance team uses Claude for "diagnostic work — tracing references, drafting first passes, pulling context —
while keeping humans responsible for judgment: framing, scenario questions, and forward-looking analyses"
([Anthropic finance team](https://claude.com/blog/how-anthropics-finance-team-uses-claude-to-shape-the-narrative-behind-the-numbers)).

## 10. Design

- **Generate the first draft, design the hard parts.** AI (e.g. Figma First Draft) turns a prompt into editable
  wireframes in minutes; spend the saved time on the edge cases AI skips — loading/empty/error states. Anthropic's
  Product Design team uses Claude to map error states and logic flows *during design rather than during
  development* ([Figma First Draft](https://help.figma.com/hc/en-us/articles/23955143044247-Use-First-Draft-with-Figma-AI); [How Anthropic teams use Claude Code](https://claude.com/blog/how-anthropic-teams-use-claude-code)).
- **Feed it your design system; usefulness scales with it.** "Connected to a complete, well-maintained component
  library, it produces… on-brand [output]. Connected to nothing, it produces Figma defaults." Use semantic layer
  names, not `Group 7` ([Figma best practices](https://help.figma.com/hc/en-us/articles/38978644498199-Best-practices-to-help-Figma-AI-understand-your-design-system)).
- **Beat slop with intent.** Slop is "the average of all design ever fed into a model… speed without intention
  produces mediocrity at scale." Replace default fonts, use real brand assets, rewrite AI copy in your voice
  ([925 Studios](https://www.925studios.co/blog/ai-slop-web-design-guide)).

## 11. Presentations / decks

- **Storyline first, slides second.** Draft and pressure-test the narrative (consulting's SCR/SCQA arc + Minto
  Pyramid) *before* generating any slide ([Slideworks SCR](https://slideworks.io/resources/how-to-use-McKinseys-scr-framework-with-examples)).
- **Use document Skills for the build.** Claude's built-in pptx/docx/xlsx/pdf Skills write and run code to
  produce real Office files ([Anthropic, create & edit files](https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude)).
- **Give brand/template/audience context.** Keep separate projects per audience (board vs. monthly review);
  connect a brand kit so drafts are on-brand from the start ([Anthropic finance team](https://claude.com/blog/how-anthropics-finance-team-uses-claude-to-shape-the-narrative-behind-the-numbers); [Canva Brand Kit](https://www.canva.com/help/create-on-brand-designs/)).
- **Verify every number and chart.** Ask the model to "validate that every number and claim reconciles to a
  single source of truth," re-running on each data refresh ([Anthropic finance team](https://claude.com/blog/how-anthropics-finance-team-uses-claude-to-shape-the-narrative-behind-the-numbers)).
- *Industry signal:* Gamma ("Cursor for slides") hit $100M+ ARR / 70M users in 2025; one agency reports
  retiring PowerPoint and saving 50,000+ hours/year ([TechCrunch](https://techcrunch.com/2025/11/10/ai-powerpoint-killer-gamma-hits-2-1b-valuation-100m-arr-founder-says/)).

## 12. Planning / PRDs / strategy / specs

- **AI drafts; the human owns the decisions.** PMs save hours auto-drafting PRDs — but "AI is a starting point,
  not an ending point… [it] couldn't fully capture organizational context, internal politics, constraints, or
  hidden priorities" ([Chisel](https://chisellabs.com/blog/how-to-write-prd-using-ai/)).
- **Spec-first / working-backwards.** Amazon's PR/FAQ writes the future press release *before* build to test the
  idea; a perfect AI-drafting frame — cheap to produce, decision-forcing ([Working Backwards](https://workingbackwards.com/concepts/working-backwards-pr-faq-process/)).
- **Drive with a structured template and real data.** Encode "good" once (PRD/ADR/one-pager templates); feed
  tickets, reviews, NPS so requirements are data-grounded ([ChatPRD](https://chatprd.ai/resources/using-ai-to-write-prd)).

## 13. Writing & research

- **Give the model your voice as persistent context**, then run **draft → critique → revise** loops (critique
  against a quality checklist) rather than accepting the first pass ([Anthropic finance team](https://claude.com/blog/how-anthropics-finance-team-uses-claude-to-shape-the-narrative-behind-the-numbers)).
- **Citation discipline is non-negotiable.** ~712 court decisions worldwide have addressed AI-hallucinated
  content (~90% in 2025), with fines and sanctions; verify every source against the original ([Bloomberg Law](https://news.bloomberglaw.com/legal-ops-and-tech/ai-faked-cases-become-core-issue-irritating-overworked-judges)).
- **Deep research = fan-out + independent verification.** Anthropic's multi-agent research system uses a lead
  agent to plan, parallel sub-agents per sub-question, and "a separate Claude with its own context window to
  verify every citation before anything reaches the user" — but at ~15× the tokens of chat, so reserve it for
  high-value work ([Anthropic, multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)).

## 14. Reusable artifacts (the leverage)

- **Skills for repeatable document types** (a deck-builder, a PRD-writer), built once and portable across
  surfaces ([Anthropic, Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).
- **Style/brand guides as memory** committed to project memory so output stays on-voice across sessions.
- **Prompt/template libraries + a quality checklist (a lightweight eval)** turn a one-off good prompt into a
  durable asset.

---

# Part III — Organization-level practices

These are how leading orgs operationalize the individual practices — useful context for what "AI-native at
scale" looks like.

- **A context-file convention.** `AGENTS.md`/`CLAUDE.md` as a checked-in standard; Stripe syncs rule files
  across tools ([Stripe](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2)).
- **Shared Skill / prompt libraries.** Anthropic packages workflows as version-controlled, company-wide Skills
  (org-level Skills shipped Dec 2025); Intercom built domain "guidance skills" contributed by experts
  ([Anthropic, transforming work](https://www.anthropic.com/research/how-ai-is-transforming-work-at-anthropic); [Intercom](https://ideas.fin.ai/p/we-gave-claude-code-to-everyone-at)).
- **AI guilds / champions / enablement.** GitHub's "AI for Everyone" + a phased AI Advocates network (~30–60
  min/week peer coaching) ([GitHub](https://github.com/resources/insights/activating-internal-ai-champions)).
- **Golden-path agent tooling.** Stripe Minions (1,300+ merged PRs/week, internal MCP "Toolshed" ~500 tools);
  Atlassian Rovo Dev across 1,900+ repos (PR cycle time −30.8%, eval-gated LLM-as-judge reviewer); Faire's
  platform-built review agent (~3,000 reviews/week) ([Stripe](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2); [Atlassian](https://www.atlassian.com/blog/ai-at-work/developer-productivity-improved-with-rovo-dev); [Faire/DX](https://getdx.com/blog/how-faire-platform-team-built-an-ai-code-review-agent/)).
- **Evals in CI.** LLM-as-judge + actionability filters gating AI review bots; eval corpora versioned and run
  nightly ([Atlassian](https://www.atlassian.com/blog/ai-at-work/developer-productivity-improved-with-rovo-dev)).
- **"Agent-first" culture & expectations.** Intercom: make "all technical work agent-first… give agents
  problems, not tasks." Shopify: "reflexive AI usage" is now a baseline expectation, factored into performance
  and peer review. BCG/McKinsey treat AI use as an evaluation expectation ([Intercom](https://ideas.fin.ai/p/we-gave-claude-code-to-everyone-at); Business Insider/BCG; Tobi Lütke memo).
- **Non-engineering AI-native orgs.** McKinsey "Lilli" (~75% of staff monthly), BCG "Deckster" (~40% of
  associates weekly) + 18,000+ custom GPTs, Anthropic Legal (marketing-review turnaround 2–3 days → 24h via a
  legal "skill" + MCP into Google Drive/Jira/Slack), Anthropic Marketing (Figma plugin generating 100 ad
  variants, 30 min → 30 sec) ([McKinsey Lilli](https://www.mckinsey.com/capabilities/tech-and-ai/how-we-help-clients/rewiring-the-way-mckinsey-works-with-lilli); [Anthropic Legal](https://claude.com/blog/how-anthropic-uses-claude-legal); [Anthropic Marketing](https://claude.com/blog/how-anthropic-uses-claude-marketing)).

---

# Part IV — Anti-patterns & failure modes (what *weak* AI-native work looks like)

These matter for assessment: the difference between strong and weak operators shows up here.

- **Workslop.** "AI-generated content that masquerades as good work but lacks the substance to advance a task."
  41% of workers received it in a month, ~2 hrs rework each, and 42% trusted the sender less ([HBR, Sept 2025](https://hbr.org/2025/09/ai-generated-workslop-is-destroying-productivity)).
- **Automation complacency / blind acceptance.** A run of valid AI edits lulls people into accepting a final
  *insecure* one; "house of cards code" that "looks complete but collapses under real-world pressure"
  ([Osmani, "The 70% Problem"](https://addyo.substack.com/p/the-70-problem-hard-truths-about)).
- **Comprehension debt.** "It's trivially easy to review code you can no longer write from scratch… if your
  ability to read doesn't scale with the agent's ability to output, you're not engineering, you're hoping"
  ([Osmani, "The 80% Problem"](https://addyo.substack.com/p/the-80-problem-in-agentic-coding)).
- **Mis-estimating your own productivity.** METR RCT: experienced devs were **~19% slower** with AI while
  believing they were **~20% faster** ([METR](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)). (See §Caveats — *not* retracted.)
- **Hallucinated facts/citations; unverified numbers in decks; generic slop; losing your own voice.**
- **Coding-tool-specific anti-patterns** (Anthropic): the kitchen-sink session, correcting over and over
  (instead of `/clear` + a better prompt), the over-specified `CLAUDE.md` the agent ignores, the trust-then-
  verify gap, and unscoped "investigate X" exploration ([best practices](https://code.claude.com/docs/en/best-practices)).

---

# Part V — The proficiency ladder (novice → expert)

A compact rubric of what each discipline looks like across skill levels. This is the bridge to assessment.

| Discipline | Weak (novice) | Strong (expert) |
|---|---|---|
| **Delegation** | "Do it all" / "fix it"; lets AI make load-bearing calls; one model for everything | Decomposes; owns design decisions; picks model/tool/approach deliberately; plans/specs before building; knows when to abandon AI |
| **Description** | Vague one-liners; no context; no memory files | Rich, specific prompts; gives source material/examples/constraints; maintains a lean `CLAUDE.md`/`AGENTS.md`; authors reusable Skills/templates |
| **Discernment** | Accepts output at face value; ships slop; doesn't notice a green test still hides a gap | Critically reviews; catches wrong/incomplete output; rejects bad approaches; calibrated trust (RAIR/RSR) |
| **Diligence** | Declares "done" with no verification; can't explain the result; no fact-check | Verifies with tests/checks before claiming done; explains every line/number; owns residual risk; reconciles to source of truth |
| **Deliverable** | Broken / house-of-cards / off-brand | Correct, coherent, on-brand, maintainable |
| **Practice/craft (cross-cutting)** | No artifacts; no context hygiene; cargo-cults tools | Context files, Skills, plan/spec artifacts, context hygiene (`/clear`, just-in-time), evals to measure own workflow |

---

# §9 Caveats (read before quoting externally)

These accuracy flags came out of the research and matter for credibility:

1. **METR was NOT retracted.** Claims it was "fatally flawed / backtracked" are a misread of the Feb 2026
   follow-up, which flags a *selection-effect* bias; the returning cohort still showed **−18%**. Represent it
   as nuance, not reversal ([METR update](https://metr.org/blog/2026-02-24-uplift-update/)).
2. **Willison's "explain every line" rule** is genuine and widely cited but predates the Oct-2025 "Vibe
   engineering" post — cite his earlier writing for the verbatim.
3. **HBR workslop figures** are self-reported survey estimates (body paywalled; corroborated via Axios/TNW).
4. **GitHub Copilot ~30% acceptance** is a 2023 figure — directionally robust, not current.
5. The **"think / think hard / ultrathink" budget ladder is outdated**: only `ultrathink` remains a keyword;
   reasoning depth is now governed by `/effort` ([model config](https://code.claude.com/docs/en/model-config)).
6. **DORA team-profile names/percentages and the Harvey trust quote** are from secondary summaries — verify
   against the full DORA 2025 PDF before quoting verbatim.
7. Several adoption stats (e.g. Google "~50% of code by Q4 2025", Bain/Deloitte seat counts, Canva internal
   dogfooding) were **unconfirmed** in the research and are deliberately excluded above.

---

# Source list (primary first)

**Anthropic — engineering / docs / blog**
- Claude Code best practices — https://code.claude.com/docs/en/best-practices
- Memory (`CLAUDE.md`) — https://code.claude.com/docs/en/memory · Skills — https://code.claude.com/docs/en/skills · Subagents — https://code.claude.com/docs/en/sub-agents · Permissions — https://code.claude.com/docs/en/permissions · Headless — https://code.claude.com/docs/en/headless · Worktrees — https://code.claude.com/docs/en/worktrees · Model config — https://code.claude.com/docs/en/model-config
- Equipping agents for the real world with Agent Skills — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Effective context engineering for AI agents — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Writing tools for agents — https://www.anthropic.com/engineering/writing-tools-for-agents
- Code execution with MCP — https://www.anthropic.com/engineering/code-execution-with-mcp
- Building effective agents — https://www.anthropic.com/research/building-effective-agents
- Demystifying evals for AI agents — https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Introducing MCP — https://www.anthropic.com/news/model-context-protocol
- Multi-agent research system — https://www.anthropic.com/engineering/multi-agent-research-system
- How AI is transforming work at Anthropic — https://www.anthropic.com/research/how-ai-is-transforming-work-at-anthropic
- How Anthropic teams use Claude Code — https://claude.com/blog/how-anthropic-teams-use-claude-code · Finance — https://claude.com/blog/how-anthropics-finance-team-uses-claude-to-shape-the-narrative-behind-the-numbers · Legal — https://claude.com/blog/how-anthropic-uses-claude-legal · Marketing — https://claude.com/blog/how-anthropic-uses-claude-marketing
- Create & edit files (document Skills) — https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude

**Standards / spec-driven / verification**
- AGENTS.md — https://agents.md/ · Agentic AI Foundation — https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation
- GitHub Spec Kit — https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/ · AWS Kiro specs — https://kiro.dev/docs/specs/
- Willison, Vibe engineering — https://simonwillison.net/2025/Oct/7/vibe-engineering/ · Designing agentic loops — https://simonwillison.net/2025/Sep/30/designing-agentic-loops/
- Osmani, The 70% Problem — https://addyo.substack.com/p/the-70-problem-hard-truths-about · The 80% Problem — https://addyo.substack.com/p/the-80-problem-in-agentic-coding
- Ronacher, Agentic Coding Recommendations — https://lucumr.pocoo.org/2025/6/12/agentic-coding/
- Husain, Your AI Product Needs Evals — https://hamel.dev/blog/posts/evals/

**Research / measurement**
- DORA 2025 — https://dora.dev/dora-report-2025/ · Google Cloud announcement — https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report · AI Capabilities Model — https://cloud.google.com/blog/products/ai-machine-learning/introducing-doras-inaugural-ai-capabilities-model
- METR uplift study — https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/ · Feb-2026 update — https://metr.org/blog/2026-02-24-uplift-update/
- HBR, AI workslop — https://hbr.org/2025/09/ai-generated-workslop-is-destroying-productivity
- Automation bias (AI & Society 2025) — https://link.springer.com/article/10.1007/s00146-025-02422-7
- Bloomberg Law, AI-faked cases — https://news.bloomberglaw.com/legal-ops-and-tech/ai-faked-cases-become-core-issue-irritating-overworked-judges

**Org / company practice**
- Stripe Minions — https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2 · Atlassian Rovo Dev — https://www.atlassian.com/blog/ai-at-work/developer-productivity-improved-with-rovo-dev · Faire/DX — https://getdx.com/blog/how-faire-platform-team-built-an-ai-code-review-agent/ · Intercom — https://ideas.fin.ai/p/we-gave-claude-code-to-everyone-at · GitHub AI champions — https://github.com/resources/insights/activating-internal-ai-champions
- McKinsey Lilli — https://www.mckinsey.com/capabilities/tech-and-ai/how-we-help-clients/rewiring-the-way-mckinsey-works-with-lilli · Gamma — https://techcrunch.com/2025/11/10/ai-powerpoint-killer-gamma-hits-2-1b-valuation-100m-arr-founder-says/ · ChatPRD — https://chatprd.ai/resources/using-ai-to-write-prd

**Design / decks / non-coding craft**
- Figma First Draft — https://help.figma.com/hc/en-us/articles/23955143044247-Use-First-Draft-with-Figma-AI · Design-system best practices — https://help.figma.com/hc/en-us/articles/38978644498199-Best-practices-to-help-Figma-AI-understand-your-design-system
- Canva Brand Kit — https://www.canva.com/help/create-on-brand-designs/ · Slideworks SCR — https://slideworks.io/resources/how-to-use-McKinseys-scr-framework-with-examples · Working Backwards PR/FAQ — https://workingbackwards.com/concepts/working-backwards-pr-faq-process/
- 925 Studios, AI slop — https://www.925studios.co/blog/ai-slop-web-design-guide

---

*Collated 2026-06-27 from six parallel deep-research streams (Claude Code craft · Skills/MCP/context engineering ·
spec-driven/verification · org practices · non-coding knowledge work). Treat §9 caveats as binding for external use.*
