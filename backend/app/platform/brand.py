"""Centralized brand configuration for user-facing copy."""

BRAND_NAME = "TALI"
BRAND_DOMAIN = "tali.dev"
BRAND_PRODUCT_NAME = "Technical Assessment Platform"
BRAND_APP_DESCRIPTION = "AI-augmented technical assessment platform"

def brand_email_from() -> str:
    return f"{BRAND_NAME} <noreply@{BRAND_DOMAIN}>"
