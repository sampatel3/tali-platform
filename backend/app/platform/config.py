from pydantic_settings import BaseSettings
from dataclasses import dataclass
from typing import Optional

from .brand import brand_email_from


@dataclass(frozen=True)
class MvpFeatureFlags:
    disable_stripe: bool
    disable_workable: bool
    disable_claude_scoring: bool
    disable_calibration: bool
    disable_proctoring: bool
    scoring_v2_enabled: bool


class Settings(BaseSettings):
    # Deployment environment
    DEPLOYMENT_ENV: str = "development"

    # Database
    DATABASE_URL: str = "postgresql://taali:taali_dev_password@localhost:5432/taali_db"

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # E2B
    E2B_API_KEY: str = ""
    E2B_TEMPLATE: Optional[str] = None

    # Claude / Anthropic
    ANTHROPIC_API_KEY: str = ""
    # Model for assessment terminal, chat, and general use. Default Haiku for cost/debugging.
    CLAUDE_MODEL: str = "claude-3-5-haiku-latest"
    # Legacy compatibility only: when set, must match CLAUDE_MODEL.
    CLAUDE_SCORING_MODEL: str = ""
    # Batch-scoring override (cost optimized). If empty, falls back to CLAUDE_MODEL.
    CLAUDE_SCORING_BATCH_MODEL: str = "claude-3-5-haiku-latest"
    # Candidate-facing agentic chat model. Independent of CLAUDE_MODEL (which
    # the recruitment agent overrides to Sonnet on prod for reasoning quality).
    # Defaults to Haiku because: (a) ~5× faster round-trip — candidate UX gets
    # ~30s → ~5s per tool-using prompt; (b) ~10× cheaper inside the $5/assessment
    # budget; (c) Haiku is fully capable for the read/edit-file tool-use shape
    # the chat exercises.
    CLAUDE_CHAT_MODEL: str = "claude-3-5-haiku-latest"
    # Autonomous cohort-loop (agent_runtime/orchestrator) model. Independent of
    # CLAUDE_MODEL — the interactive recruitment agent + chat stay on it. The
    # cron deliberation loop is ~92% no-op/fail and the safety-critical decisions
    # are made deterministically by bulk_decision_service (no LLM) + HITL review,
    # so it runs on a cheaper model by default. Per-role role.agent_model still
    # wins. Empty → falls back to resolved_claude_model (no behaviour change).
    CLAUDE_AGENT_AUTONOMOUS_MODEL: str = ""
    # Redundant-cycle gate for the autonomous cohort loop: skip a cron LLM cycle
    # when the previous one succeeded with 0 decisions and nothing in the cohort
    # changed since — with a force-run backstop so a gated role still runs at
    # least every N hours (a missed yield is delayed ≤N h, never lost).
    # off (no-op) | shadow (log would-skip, still run) | on (actually skip).
    AGENT_COHORT_GATE_MODE: str = "off"
    AGENT_COHORT_GATE_MAX_STALENESS_HOURS: int = 4
    MAX_TOKENS_PER_RESPONSE: int = 1024
    # Terminal-native Claude Code runtime
    ASSESSMENT_TERMINAL_ENABLED: bool = True
    ASSESSMENT_TERMINAL_DEFAULT_MODE: str = "claude_cli_terminal"  # "claude_cli_terminal"
    CLAUDE_CLI_PERMISSION_MODE_DEFAULT: str = "acceptEdits"
    CLAUDE_CLI_COMMAND: str = "claude"
    CLAUDE_CLI_DISALLOWED_TOOLS: str = "Bash"
    # Strict production posture: no global key fallback for candidate sessions.
    ASSESSMENT_TERMINAL_ALLOW_GLOBAL_KEY_FALLBACK: bool = False
    # Budget controls
    DEMO_CLAUDE_BUDGET_LIMIT_USD: float = 1.0
    ASSESSMENT_CLAUDE_BUDGET_DEFAULT_USD: float | None = 5.0
    # Require provider-reported usage in CLI transcript for cost/budget enforcement.
    CLAUDE_CLI_REQUIRE_PROVIDER_USAGE: bool = True
    CLAUDE_CLI_PROVIDER_USAGE_GRACE_OUTPUT_EVENTS: int = 40
    # Agentic chat (terminal-removal replacement) — multi-turn tool-use loop.
    # CLAUDE_TOOL_MAX_TURNS caps how many ``messages.create`` calls one
    # candidate→assistant turn can fan into. History:
    #   12 (#398): Sonnet fanned to 19 tool calls (52s latency)
    #    6 (#409): cut latency but SDK raised mid-answer (no recovery)
    #   10 (#414): added partial recovery; Haiku used full 10 (60s)
    #    4 (#415): too low for "fix it" — model burned 4 reads before
    #              edits, hit cap with no text, soft-recovery fell
    #              through to generic retry (assessment 77, 2026-05-26)
    #    8 (#…): enough for a read-N + edit-N round-trip but too tight for
    #             a real "find → edit → run pytest → read output → fix"
    #             loop — Claude burned its turns locating a module + writing
    #             a doc and never reached the tests (it reported "hit the
    #             call limit"). Candidates expect Cursor/Claude-Code-grade
    #             autonomy: run the tests themselves and iterate.
    #   25 (now): comfortable headroom for find+edit+test+fix in one turn.
    #             The $ budget + assessment timer remain the hard ceilings;
    #             the empty-text soft recovery still covers the rare hit.
    CLAUDE_TOOL_MAX_TURNS: int = 25
    # Per-command wall-clock cap inside the executor's run_command tool.
    # Bumped 10→60: 10s killed anything but a trivial test run; a real
    # pytest/build needs headroom. The executor reads this setting.
    CLAUDE_TOOL_TIMEOUT_SECONDS: int = 60

    # Cost model defaults (all overridable via environment).
    # Rates match Claude Haiku 4.5 — the model the platform actually
    # routes to today. The pre-2026 defaults ($0.25 / $1.25) were Haiku
    # 3.5 rates and produced a ~4x under-count of every billable call,
    # surfaced by the Anthropic reconciliation panel as -75% drift.
    CLAUDE_INPUT_COST_PER_MILLION_USD: float = 1.0
    CLAUDE_OUTPUT_COST_PER_MILLION_USD: float = 5.0
    # Anthropic prompt-cache pricing (Haiku 4.5 official rates). Cache
    # reads are ~10x cheaper than uncached input; cache writes are
    # ~1.25x. The candidate-facing budget UI was undercounting by ~2x
    # because it priced only ``input_tokens`` and ``output_tokens``,
    # ignoring the large ``cache_read_input_tokens`` value the SDK
    # streams back (assessment 77, 2026-05-26 — real spend was $0.149
    # but budget UI said $0.075).
    CLAUDE_CACHE_READ_COST_PER_MILLION_USD: float = 0.10
    CLAUDE_CACHE_CREATION_COST_PER_MILLION_USD: float = 1.25

    # Usage-based pricing (2026-04-29 cutover from Lemon Squeezy).
    # When False, every Claude call writes a usage_events row but the
    # ledger is NOT debited and gates do NOT block — shadow mode for
    # validating attribution numbers against Anthropic's dashboard. Flip
    # to True (Phase 6) once shadow data confirms the meter is accurate.
    USAGE_METER_LIVE: bool = False
    # Anthropic Admin API key for provisioning per-org workspace keys.
    # Empty = workspace provisioning disabled, all calls fall back to
    # ANTHROPIC_API_KEY (the shared Taali key).
    ANTHROPIC_ADMIN_API_KEY: str = ""
    # Master gate for per-org Anthropic WORKSPACE-KEY routing. OFF (default) =
    # every call uses the shared Taali key (current behaviour); ON = billable
    # calls with an org context route through that org's workspace key (lazily
    # provisioned via the Admin API, graceful shared-key fallback on any
    # failure). Routing per-org makes Anthropic's Admin API report cost
    # per-workspace, which is what enables TRUE per-org reconciliation (vs the
    # allocation in anthropic_reconciliation_allocation). Keep OFF until
    # ANTHROPIC_ADMIN_API_KEY is set and provisioning has been validated.
    ANTHROPIC_WORKSPACE_KEYS_ENABLED: bool = False
    E2B_COST_PER_HOUR_USD: float = 0.30
    EMAIL_COST_PER_SEND_USD: float = 0.01
    STORAGE_COST_PER_GB_MONTH_USD: float = 0.023
    STORAGE_RETENTION_DAYS_DEFAULT: int = 30
    COST_ALERT_DAILY_SPEND_USD: float = 200.0
    COST_ALERT_PER_COMPLETED_ASSESSMENT_USD: float = 10.0

    # Outbound mainspring brain feed (bidirectional tali<->mainspring link).
    # When True, a periodic sweep enqueues ANONYMIZED, aggregable learning
    # signal (resolved decisions + human disposition, teach outcomes, daily
    # usage rollups — never PII/free-text/raw ids) into brain_feed_outbox, and
    # a drain task POSTs it to mainspring's ingest API. Default OFF: nothing is
    # enqueued and the live platform is completely unaffected. With the flag on
    # but MAINSPRING_INGEST_URL empty, the drain runs in shadow (log-only) —
    # the intended posture until the mainspring ingest endpoint is live.
    MAINSPRING_BRAIN_FEED_ENABLED: bool = False
    # Base URL of the mainspring deployment exposing /api/v1/ingest/*. Empty =
    # shadow mode (drain logs what it would send, leaves rows pending).
    MAINSPRING_INGEST_URL: str = ""
    # ADR-0010 metering convergence: CUT OVER. record_event() now prices the
    # billed cost directly through mainspring's vendored seam (the single source
    # of truth). The pre-cutover shadow comparator + its MAINSPRING_METERING_SHADOW
    # flag have been retired now that the seam is the live pricer.
    # ADR-0010 decision-policy convergence: CUT OVER. The verdict cascade is now
    # PRODUCED by mainspring's vendored deterministic PolicyEngine
    # (evaluate_decision_points) via decision_policy/mainspring_engine.py — net-
    # zero behaviour change on decision_type (proved by the parity corpus in
    # tests/decision_policy/test_engine_mainspring_parity.py). The log-only
    # MAINSPRING_POLICY_SHADOW flag + its shadow comparator were removed once
    # parity was proven. (historical)
    # ADR-0010 convergence (cut #3, promotion gate): CUT OVER. The gate now
    # composes its AND-decision via mainspring's vendored gate seam unconditionally
    # (decision_policy/promotion_gate.py); the log-only MAINSPRING_GATE_SHADOW flag
    # and its shadow comparator were removed once parity was proven. (historical)
    # ADR-0010 convergence (cut #4) CUTOVER COMPLETE: the bias/EEOC audit verdict
    # is now mainspring's vendored bias seam (decision_policy/bias_audit.py
    # delegates to pairwise_fairness_verdict). The log-only MAINSPRING_BIAS_SHADOW
    # flag + its shadow comparator were removed once parity was proven
    # (test_bias_seam_parity.py). (historical)
    # ADR-0010 KG convergence (cut #5) CUTOVER COMPLETE: the GraphRAG prior is now
    # mainspring's vendored GraphitiBackend.get_priors (graph_priors sub-agent
    # routes through vendor/mainspring_kg). The synthesis + Cypher are a
    # character-identical port, proven by test_kg_graphrag_synthesis_parity.py, so
    # the prior is identical given the same graph. The log-only MAINSPRING_KG_SHADOW
    # flag + its shadow comparator were removed once the cutover landed. (historical)
    # Brand service token for the ingest API (sent as Bearer). Empty in shadow.
    MAINSPRING_BRAND_TOKEN: str = ""
    # How far back each sweep looks for newly-resolved decisions / teach
    # outcomes to enqueue. The sweep runs continuously, so this only needs to
    # comfortably exceed the inter-sweep interval; the daily usage rollup only
    # aggregates whole past days regardless of this value.
    MAINSPRING_BRAIN_FEED_LOOKBACK_HOURS: int = 72

    @property
    def resolved_claude_model(self) -> str:
        """Claude model for assessment terminal, chat, and general use. Defaults to claude-3-5-haiku-latest."""
        model = (self.CLAUDE_MODEL or "").strip()
        return model or "claude-3-5-haiku-latest"

    @property
    def resolved_agent_autonomous_model(self) -> str:
        """Model for the autonomous cohort loop (agent_runtime/orchestrator).
        Falls back to the interactive agent model when unset, so the code default
        is unchanged; set CLAUDE_AGENT_AUTONOMOUS_MODEL to run the loop cheaper."""
        model = (self.CLAUDE_AGENT_AUTONOMOUS_MODEL or "").strip()
        return model or self.resolved_claude_model

    @property
    def resolved_claude_chat_model(self) -> str:
        """Candidate agentic-chat model. Independent of CLAUDE_MODEL — prod
        overrides CLAUDE_MODEL to Sonnet for the recruitment agent, but the
        candidate chat should stay on Haiku for speed + cost."""
        model = (self.CLAUDE_CHAT_MODEL or "").strip()
        return model or "claude-3-5-haiku-latest"

    @property
    def resolved_claude_scoring_model(self) -> str:
        """Scoring model resolver. Prefers CLAUDE_SCORING_BATCH_MODEL when set."""
        scoring = (self.CLAUDE_SCORING_MODEL or "").strip()
        resolved = self.resolved_claude_model
        if scoring and scoring != resolved:
            raise ValueError(
                "CLAUDE_SCORING_MODEL is deprecated and must match CLAUDE_MODEL when set."
            )
        batch_scoring_model = (self.CLAUDE_SCORING_BATCH_MODEL or "").strip()
        return batch_scoring_model or resolved

    @property
    def active_claude_model(self) -> str:
        return self.resolved_claude_model

    @property
    def active_claude_scoring_model(self) -> str:
        return self.resolved_claude_scoring_model

    def model_post_init(self, __context) -> None:
        scoring = (self.CLAUDE_SCORING_MODEL or "").strip()
        if scoring and scoring != self.resolved_claude_model:
            raise ValueError(
                "CLAUDE_SCORING_MODEL is deprecated and must match CLAUDE_MODEL when set."
            )

    # GitHub assessment repository integration
    GITHUB_TOKEN: str = ""
    GITHUB_ORG: str = "taali-assessments"
    GITHUB_MOCK_MODE: bool = False

    # Task authoring API guardrail (tasks are backend-authored by default).
    TASK_AUTHORING_API_ENABLED: bool = False

    # V2 AI-assisted evaluator (suggestions only)
    AI_ASSISTED_EVAL_ENABLED: bool = False

    # Workable
    WORKABLE_CLIENT_ID: str = ""
    WORKABLE_CLIENT_SECRET: str = ""
    WORKABLE_WEBHOOK_SECRET: str = ""
    # Workable Assessments-Provider marketplace add-on. Off by default: the
    # result-callback sweep/drain is a no-op until deliberately enabled, so the
    # live platform is unaffected.
    WORKABLE_PROVIDER_ENABLED: bool = False

    # Stripe
    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Resend
    RESEND_API_KEY: str = ""
    # Svix signing secret for the Resend delivery webhook (`whsec_...`). When
    # unset the /webhooks/resend endpoint 503s — delivery/open/bounce tracking
    # is simply off until configured in the Resend dashboard.
    RESEND_WEBHOOK_SECRET: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Neo4j (optional). Powers the candidate knowledge-graph view and
    # graph predicates in natural-language search. When NEO4J_URI is
    # blank the graph features degrade gracefully: the graph view shows
    # a configuration hint, and graph predicates drop out of NL queries.
    # Production is deployed via Railway's Neo4j template (see
    # docs/neo4j-railway-setup.md); local dev typically leaves it blank.
    NEO4J_URI: str = ""
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""
    NEO4J_DATABASE: str = "neo4j"

    # Graphiti — temporal knowledge graph on top of Neo4j. Replaces the
    # manual sync/Cypher path when configured. Uses Anthropic for LLM
    # extraction (reuses ANTHROPIC_API_KEY) and Voyage AI for embeddings.
    # If VOYAGE_API_KEY is empty, Graphiti is disabled and graph features
    # degrade exactly like Neo4j-not-configured.
    VOYAGE_API_KEY: str = ""
    GRAPHITI_LLM_MODEL: str = "claude-haiku-4-5-20251001"
    GRAPHITI_LLM_SMALL_MODEL: str = "claude-haiku-4-5-20251001"
    GRAPHITI_EMBEDDING_MODEL: str = "voyage-3"
    GRAPHITI_EMBEDDING_DIMS: int = 1024  # voyage-3 native dimension
    # Hard cap on per-candidate Graphiti episode count during backfill —
    # safeguard against runaway LLM cost on a candidate with hundreds of
    # experience entries.
    GRAPHITI_MAX_EPISODES_PER_CANDIDATE: int = 40

    # Admin
    ADMIN_SECRET: str = ""

    # URLs
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"
    # Optional comma-separated extra CORS origins (e.g. Vercel preview URL)
    CORS_EXTRA_ORIGINS: Optional[str] = None
    # Optional regex for additional allowed CORS origins (e.g. all Vercel previews)
    CORS_ALLOW_ORIGIN_REGEX: Optional[str] = None

    # S3-compatible object storage (AWS S3, Tigris, R2, etc.)
    # The vars are still named AWS_* for backwards compat, but they
    # apply to whichever S3-compatible store AWS_S3_ENDPOINT_URL points
    # at. Leave AWS_S3_ENDPOINT_URL unset for AWS S3.
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_S3_BUCKET: Optional[str] = "taali-assessments"
    AWS_REGION: str = "us-east-1"
    AWS_S3_ENDPOINT_URL: Optional[str] = None
    # Set to True to bypass S3 entirely (creds expired, bucket down, or
    # storage isn't required for the current deploy). Files persist
    # locally only in this mode — fine for cv_text-driven scoring since
    # the extracted text lives in Postgres regardless.
    S3_DISABLED: bool = False

    # Sentry
    SENTRY_DSN: Optional[str] = None

    # Assessment Configuration
    ASSESSMENT_PRICE_CURRENCY: str = "aed"
    ASSESSMENT_PRICE_MAJOR: int = 25
    ASSESSMENT_PRICE_MINOR: int = 2500
    # Deprecated alias (sunset target: 2026-04-15). Keep until clients fully
    # migrate to ASSESSMENT_PRICE_MINOR.
    ASSESSMENT_PRICE_PENCE: int = 2500
    ASSESSMENT_EXPIRY_DAYS: int = 7
    EMAIL_FROM: str = brand_email_from()

    # Prompt Scoring Weights (configurable per deployment)
    # Keys: tests, code_quality, prompt_quality, prompt_efficiency, independence,
    #        context_utilization, design_thinking, debugging_strategy, written_communication
    SCORE_WEIGHTS: str = '{"tests":0.30,"code_quality":0.15,"prompt_quality":0.15,"prompt_efficiency":0.10,"independence":0.10,"context_utilization":0.05,"design_thinking":0.05,"debugging_strategy":0.05,"written_communication":0.05}'

    # Default calibration prompt (used when task has no custom calibration_prompt)
    DEFAULT_CALIBRATION_PROMPT: str = "Ask Claude to help you write a Python function that reverses a string. Show your approach to working with AI assistance."

    # Optional path to write cv_match telemetry rows (one JSON line per call).
    # Empty string = ring-buffer-only (admin route reads from memory).
    CV_MATCH_TRACE_LOG_PATH: str = ""

    # Two-tier scoring gate: when True, every v3 score is preceded by a
    # cheap pre-screen LLM call (~$0.0002/CV). Candidates scoring below
    # PRE_SCREEN_THRESHOLD skip v3 entirely (marked pre_screen_filtered).
    # Scores at or above the threshold fall through to full scoring.
    # Error/parse failures always fall through. Default off.
    # Recruiters override per candidate via enqueue_score(force=True).
    ENABLE_PRE_SCREEN_GATE: bool = False

    # Cost guard (2026-06): when True, ``enqueue_score(bypass_pre_screen=True)``
    # is downgraded to a normal gated score for candidates that have NOT
    # genuinely passed pre-screen (never-screened, stale CV, or genuine score
    # below PRE_SCREEN_THRESHOLD). Stops bulk / engine-migration re-scores from
    # paying for the expensive holistic score on candidates the cheap pre-screen
    # would have filtered. No effect unless ENABLE_PRE_SCREEN_GATE is also on.
    # Default off; flip to true to enforce. (2026-06 audit: ~56% of the June
    # score line went to fail/never-pre-screened candidates.)
    PRE_SCREEN_GATE_GUARD_RESCORE: bool = False

    # Holistic scoring engine (cv_match holistic_v1). When enabled for an
    # org, the full-score stage (after the pre-screen gate) runs the
    # single-call Sonnet holistic scorer (app.cv_matching.holistic) instead
    # of the Haiku run_cv_match main+graded pipeline. The Sonnet ``overall``
    # becomes role_fit_score directly (no 0.40·cv_fit+0.60·req_match
    # aggregation).
    # HOLISTIC_SCORING_ORG_IDS is a comma-separated allowlist of org ids,
    # or "*" for every org. Both must be set for the engine to activate.
    # DEFAULT ON for every org (2026-06-14): v2.1.0 holistic is the standing
    # scoring engine platform-wide. This governs NEW scores only — existing
    # scores are NOT re-scored on deploy (a re-score is opt-in, prompted by the
    # agent when switched on for a role carrying stale v1.x scores). An env
    # override can still pin a narrower allowlist or disable it per environment.
    HOLISTIC_SCORING_ENABLED: bool = True
    HOLISTIC_SCORING_ORG_IDS: str = "*"

    # Numeric threshold (0-100) for the pre-screen gate. Candidates whose
    # pre-screen score is strictly below this value are filtered out without
    # running the full v3 scoring pipeline. Tune this to filter 10-50% of
    # the worst-fit profiles while keeping permissive defaults.
    # Recommended: 30 (catches only clear mismatches).
    PRE_SCREEN_THRESHOLD: int = 30

    # When True, the Stage-1 gate ENFORCES the data-driven threshold from
    # ``prescreen_gate_calibration.compute_gate_threshold`` (a false-reject-
    # budgeted, org-wide cut) instead of the static ``PRE_SCREEN_THRESHOLD``.
    # Default False = SHADOW: the dynamic value is still computed, logged, and
    # stamped into cv_match_details for measurement, but the static env value
    # decides — so nothing changes live until the false-reject numbers are
    # proven. Flip to True per environment once the shadow data validates.
    PRE_SCREEN_DYNAMIC_GATE_ENFORCE: bool = False

    # Pre-screen fraud detection — currently the only signal is "CV
    # copy-pasted from the JD". When the copy-paste fraction of the CV
    # crosses FRAUD_COPY_PASTE_THRESHOLD (0.0–1.0), the pre-screen agent
    # caps the candidate's score at FRAUD_PENALTY_CAP_SCORE so the gate
    # filters them before the expensive v3 call. Cap defaults below
    # PRE_SCREEN_THRESHOLD intentionally — fraud-positive should always
    # skip CV match. Set the threshold to 1.0 to disable the signal.
    FRAUD_COPY_PASTE_THRESHOLD: float = 0.05
    FRAUD_PENALTY_CAP_SCORE: float = 10.0

    # CV integrity penalty (v3 full scoring). Two deterministic signals feed
    # it: (1) unverified extraordinary claims the v3 model flagged in
    # ``claims_to_verify`` (a hackathon win / award / publication it can
    # neither corroborate from the CV nor place as a known event), and
    # (2) timeline inconsistencies (future dates, end-before-start, impossible
    # role spans, too many concurrent "current" roles). Each issue deducts
    # FRAUD_INTEGRITY_PENALTY_POINTS from role_fit, capped at
    # FRAUD_INTEGRITY_PENALTY_MAX. The cap is deliberate: integrity signals
    # NUDGE the score so fraud can't inflate a candidate into interview, but
    # never single-handedly auto-reject (the timeline is LLM-extracted and
    # familiarity is a model prior, so a false positive must stay cheap).
    # Set MAX to 0.0 to disable the v3 integrity penalty.
    FRAUD_INTEGRITY_PENALTY_POINTS: float = 5.0
    FRAUD_INTEGRITY_PENALTY_MAX: float = 15.0
    # Pre-screen soft penalty (Stage 1 gate). Flat deduction when the
    # pre-screen flags reliance on an extraordinary, CV-uncorroborated claim.
    # Soft on purpose — a few points can't single-handedly drop a plausible
    # candidate below the gate. Set to 0.0 to disable.
    FRAUD_PRESCREEN_UNVERIFIED_PENALTY: float = 5.0

    # MVP feature flags (default to MVP-safe behavior).
    # Stripe is now the live payment processor for credit top-ups; default
    # changed True → False as part of the 2026-04-29 usage-pricing cutover.
    MVP_DISABLE_STRIPE: bool = False
    MVP_DISABLE_WORKABLE: bool = True
    MVP_DISABLE_CLAUDE_SCORING: bool = True
    MVP_DISABLE_CALIBRATION: bool = False
    MVP_DISABLE_PROCTORING: bool = True
    SCORING_V2_ENABLED: bool = False
    # ATS: when on, pipeline stages are per-org configurable (pipeline_stages
    # table) and recruiters may move candidates to any active stage. Default OFF
    # preserves the legacy hard-coded PIPELINE_STAGES tuple + strict transition
    # graph EXACTLY (so the live workable_primary org is unaffected). Flipped on
    # per-environment (staging) once the reader surface is fully migrated.
    ATS_CONFIGURABLE_STAGES_ENABLED: bool = False

    # TAALI score blending. assessment vs. role-fit (0.0..1.0 each); role-fit
    # is a 50/50 mix of CV fit and requirements fit. Weights are normalized in
    # taali_scoring.weighted_average_100, so any pair that's > 0 works.
    TAALI_WEIGHT_ASSESSMENT: float = 0.5
    TAALI_WEIGHT_ROLE_FIT: float = 0.5
    TAALI_WEIGHT_CV_FIT: float = 0.5
    TAALI_WEIGHT_REQUIREMENTS_FIT: float = 0.5

    # When a new role is created (in Taali or via Workable sync), auto-generate
    # a DRAFT assessment task from its JD (JD→spec generator) and link it,
    # pending recruiter review. Default OFF: generation is a paid Sonnet
    # operation, so an org opts in before its whole role catalog gets
    # auto-authored. Generated tasks are is_active=False until approved.
    AUTO_GENERATE_ASSESSMENT_TASKS: bool = False

    @property
    def mvp_flags(self) -> MvpFeatureFlags:
        return MvpFeatureFlags(
            disable_stripe=self.MVP_DISABLE_STRIPE,
            disable_workable=self.MVP_DISABLE_WORKABLE,
            disable_claude_scoring=self.MVP_DISABLE_CLAUDE_SCORING,
            disable_calibration=self.MVP_DISABLE_CALIBRATION,
            disable_proctoring=self.MVP_DISABLE_PROCTORING,
            scoring_v2_enabled=self.SCORING_V2_ENABLED,
        )

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
    }


settings = Settings()
