# Re-export shim – canonical location is components.notifications.email_client
from ..components.notifications.email_client import EmailService  # noqa: F401

__all__ = ["EmailService"]
