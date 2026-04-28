from pydantic_settings import BaseSettings
from dataclasses import dataclass
from typing import Optional

from .brand import brand_email_from


@dataclass(frozen=True)
class MvpFeatureFlags:
    disable_stripe: bool
    disable_lemon: bool
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

    # Lemon Squeezy
    LEMON_API_KEY: str = ""
    LEMON_STORE_ID: str = ""
    LEMON_WEBHOOK_SECRET: str = ""
    LEMON_TEST_MODE: bool = False
    # JSON object keyed by UI pack id:
    # {"starter_5":{"variant_id":"12345","credits":5,"label":"Starter (5 credits)"}, ...}
    LEMON_PACKS_JSON: str = (
        '{"starter_5":{"variant_id":"0","credits":5,"label":"Starter (5 credits)"},'
        '"growth_10":{"variant_id":"0","credits":10,"label":"Growth (10 credits)"},'
        '"scale_20":{"variant_id":"0","credits":20,"label":"Scale (20 credits)"}}'
    )

    # Resend
    RESEND_API_KEY: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

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

    # cv_match_v3.0 cutover flag (Phase 10 of the production-grade upgrade).
    # When True, cv_score_orchestrator routes scoring through
    # `app.cv_matching.runner.run_cv_match` instead of the legacy v3/v4
    # `fit_matching_service` calls. Default off — flip to True in dev/staging
    # first, monitor traces via `/admin/cv-match/traces`, then promote.
    USE_CV_MATCH_V3: bool = False
    # Optional path to write cv_match telemetry rows (one JSON line per call).
    # Empty string = ring-buffer-only (admin route reads from memory).
    CV_MATCH_TRACE_LOG_PATH: str = ""

    # cv_match v4.1 (Phase 1) cutover flag. When True, run_cv_match dispatches
    # to the v4.1 path (UNTRUSTED_CV spotlighting, anchored 25-point rubric,
    # anti-default rule, evidence-first per-requirement schema). Default off
    # so v3 stays the production path until shadow-eval confirms parity.
    # Tasks 1.4 (tool-use) and 1.5 (prompt caching) require Anthropic SDK
    # >=0.39 and are NOT enabled at this flag flip — they ship in a follow-up.
    USE_CV_MATCH_V4_PHASE1: bool = False

    # Phase 2 cutover flag for cv_match_v4.2 (archetype-aware substitution
    # rules + decomposed dimension scoring). When True, run_cv_match
    # dispatches to v4.2 and overrides PHASE1. Default off until the
    # archetype rubric library is reviewed and the eval harness shows
    # parity-or-better against v3.
    USE_CV_MATCH_V4_PHASE2: bool = False

    # Phase 3 borderline tie-break. When the composite role_fit lands
    # in [BORDERLINE_LO, BORDERLINE_HI], the runner runs Bradley-Terry
    # pairwise comparisons against per-archetype anchor candidates to
    # produce a finer continuous score and a self-consistency
    # uncertainty band. Only fires when USE_CV_MATCH_V4_PHASE3 is True.
    USE_CV_MATCH_V4_PHASE3: bool = True
    CV_MATCH_BORDERLINE_LO: float = 40.0
    CV_MATCH_BORDERLINE_HI: float = 75.0
    # CISC-style self-consistency: max samples for borderline cases.
    # Stops early once the running stddev stabilises.
    CV_MATCH_BORDERLINE_MAX_SAMPLES: int = 5

    # Probability (0.0-1.0) that a v3 cv_match call also fires a silent v4.1
    # shadow run. Shadow runs write a telemetry trace with shadow=True but do
    # NOT affect the recruiter response. Recommended rollout: 0.10 once the
    # v4.1 path is wired and validated; 0.0 before then. Independent from
    # USE_CV_MATCH_V4_PHASE1 — the shadow path is the safest way to gather
    # comparison data before flipping the primary path.
    CV_MATCH_V4_SHADOW_SAMPLE_RATE: float = 0.0

    # Phase 2 embedding pre-filter. When True, batch matches (>30 candidates
    # against one JD) embed all CVs once, cosine-rank against the JD, and
    # drop the bottom 50% before spending Haiku tokens. Override-tagged
    # candidates are NEVER dropped regardless of this flag.
    USE_CV_MATCH_V4_PREFILTER: bool = False
    # Cosine threshold below which a candidate is considered an obvious
    # mismatch. Empirical Voyage-3.5-lite typical range is [0.55, 0.85] for
    # in-domain matches; 0.50 is a conservative drop floor.
    CV_MATCH_V4_PREFILTER_COSINE_THRESHOLD: float = 0.50
    # Pre-filter only fires above this batch size. At small N the embedding
    # round-trip overhead exceeds what the filter saves on Haiku calls.
    CV_MATCH_V4_PREFILTER_MIN_BATCH: int = 30

    # Embedding provider selection. "voyage" (default), "openai", or "mock"
    # (deterministic hash-based; for tests).
    EMBEDDING_PROVIDER: str = "mock"
    EMBEDDING_MODEL: str = ""  # blank = provider default
    VOYAGE_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # Two-tier scoring gate: when True, every v3 score is preceded by a
    # cheap pre-screen LLM call (~$0.0002/CV). "no" verdicts skip the
    # full v3 call and short-circuit with a "pre_screened_out" cache_hit.
    # "yes"/"maybe"/"error" fall through to v3 unchanged. Default off.
    # Recruiters override per candidate via enqueue_score(force=True).
    ENABLE_PRE_SCREEN_GATE: bool = False

    # MVP feature flags (default to MVP-safe behavior)
    MVP_DISABLE_STRIPE: bool = True
    MVP_DISABLE_LEMON: bool = True
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
            disable_lemon=self.MVP_DISABLE_LEMON,
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
