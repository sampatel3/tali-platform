# Re-export shim – canonical location is platform.logging
from ..platform.logging import JsonFormatter, setup_logging  # noqa: F401

__all__ = ["JsonFormatter", "setup_logging"]
