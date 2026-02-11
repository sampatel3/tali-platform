# Re-export shim – canonical location is platform.config
from ..platform.config import Settings, settings  # noqa: F401

__all__ = ["Settings", "settings"]
