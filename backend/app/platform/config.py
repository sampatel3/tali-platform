from pydantic_settings import BaseSettings
from dataclasses import dataclass
from typing import Optional

from .brand import brand_email_from


@dataclass(frozen=True)
class MvpFeatureFlags:
    disable_stripe: bool
    disable_workable: bool
    disable_celery: bool
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

    # Cost model defaults (all overridable via environment)
    CLAUDE_INPUT_COST_PER_MILLION_USD: float = 0.25
    CLAUDE_OUTPUT_COST_PER_MILLION_USD: float = 1.25

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
    E2B_COST_PER_HOUR_USD: float = 0.30
    EMAIL_COST_PER_SEND_USD: float = 0.01
    STORAGE_COST_PER_GB_MONTH_USD: float = 0.023
    STORAGE_RETENTION_DAYS_DEFAULT: int = 30
    COST_ALERT_DAILY_SPEND_USD: float = 200.0
    COST_ALERT_PER_COMPLETED_ASSESSMENT_USD: float = 10.0

    @property
    def resolved_claude_model(self) -> str:
        """Claude model for assessment terminal, chat, and general use. Defaults to claude-3-5-haiku-latest."""
        model = (self.CLAUDE_MODEL or "").strip()
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

    # Stripe
    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Resend
    RESEND_API_KEY: str = ""

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

    # AWS S3
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_S3_BUCKET: Optional[str] = "taali-assessments"
    AWS_REGION: str = "us-east-1"
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

    # Numeric threshold (0-100) for the pre-screen gate. Candidates whose
    # pre-screen score is strictly below this value are filtered out without
    # running the full v3 scoring pipeline. Tune this to filter 10-50% of
    # the worst-fit profiles while keeping permissive defaults.
    # Recommended: 30 (catches only clear mismatches).
    PRE_SCREEN_THRESHOLD: int = 30

    # MVP feature flags (default to MVP-safe behavior).
    # Stripe is now the live payment processor for credit top-ups; default
    # changed True → False as part of the 2026-04-29 usage-pricing cutover.
    MVP_DISABLE_STRIPE: bool = False
    MVP_DISABLE_WORKABLE: bool = True
    MVP_DISABLE_CELERY: bool = True
    MVP_DISABLE_CLAUDE_SCORING: bool = True
    MVP_DISABLE_CALIBRATION: bool = False
    MVP_DISABLE_PROCTORING: bool = True
    SCORING_V2_ENABLED: bool = False

    @property
    def mvp_flags(self) -> MvpFeatureFlags:
        return MvpFeatureFlags(
            disable_stripe=self.MVP_DISABLE_STRIPE,
            disable_workable=self.MVP_DISABLE_WORKABLE,
            disable_celery=self.MVP_DISABLE_CELERY,
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
