# Re-export shim – canonical location is components.scoring.service
from ..components.scoring.service import calculate_mvp_score  # noqa: F401

__all__ = ["calculate_mvp_score"]
