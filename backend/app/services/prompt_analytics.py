# Re-export shim – canonical location is components.scoring.analytics
from ..components.scoring.analytics import (  # noqa: F401
    compute_time_to_first_prompt,
    compute_prompt_speed,
    compute_prompt_frequency,
    compute_prompt_length_stats,
    detect_copy_paste,
    compute_code_delta,
    compute_self_correction_rate,
    compute_token_efficiency,
    compute_browser_focus_ratio,
    compute_tab_switch_count,
    compute_all_heuristics,
)

__all__ = [
    "compute_time_to_first_prompt",
    "compute_prompt_speed",
    "compute_prompt_frequency",
    "compute_prompt_length_stats",
    "detect_copy_paste",
    "compute_code_delta",
    "compute_self_correction_rate",
    "compute_token_efficiency",
    "compute_browser_focus_ratio",
    "compute_tab_switch_count",
    "compute_all_heuristics",
]
