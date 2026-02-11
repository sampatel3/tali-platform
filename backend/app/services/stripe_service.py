# Re-export shim – canonical location is components.integrations.stripe.service
from ..components.integrations.stripe.service import StripeService  # noqa: F401

__all__ = ["StripeService"]
