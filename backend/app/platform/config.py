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

    # Claude / Anthropic
    ANTHROPIC_API_KEY: str = ""
    # Model overrides (AGENT E / Phase P6)
    # Precedence: CLAUDE_MODEL (explicit) > env-derived default below
    CLAUDE_MODEL: Optional[str] = None
    CLAUDE_MODEL_NON_PROD: str = "claude-3-5-haiku-latest"
    CLAUDE_MODEL_PRODUCTION: str = "claude-3-5-sonnet-20241022"
    MAX_TOKENS_PER_RESPONSE: int = 1024

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
        """Resolve the Claude model for the current environment."""
        if self.CLAUDE_MODEL:
            return self.CLAUDE_MODEL
        env = (self.DEPLOYMENT_ENV or "").lower()
        if env in {"prod", "production"}:
            return self.CLAUDE_MODEL_PRODUCTION
        return self.CLAUDE_MODEL_NON_PROD


    # GitHub assessment repository integration
    GITHUB_TOKEN: str = ""
    GITHUB_ORG: str = "taali-assessments"
    GITHUB_MOCK_MODE: bool = True

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

    # MVP feature flags (default to MVP-safe behavior)
    MVP_DISABLE_STRIPE: bool = True
    MVP_DISABLE_WORKABLE: bool = True
    MVP_DISABLE_CELERY: bool = True
    MVP_DISABLE_CLAUDE_SCORING: bool = True
    MVP_DISABLE_CALIBRATION: bool = True
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
