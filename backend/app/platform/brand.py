"""Centralized brand configuration for user-facing copy."""

BRAND_NAME = "TAALI"
BRAND_DOMAIN = "taali.ai"
BRAND_PRODUCT_NAME = "Technical Assessments That Tally Real Skill"
BRAND_APP_DESCRIPTION = "AI-augmented technical assessments inspired by Arabic clarity and tally precision"

def brand_email_from() -> str:
    return f"{BRAND_NAME} <noreply@{BRAND_DOMAIN}>"
