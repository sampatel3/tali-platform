"""Canonical model identifiers shared across the single-shot pipelines.

``FAST_MODEL`` is the cheap Haiku model pinned for structured single-shot
and parsing calls (CV scoring, CV parsing, NL-query parsing, pre-screen,
intent-chip parsing, material-change detection, interview-tech prompts).

It was previously a hardcoded string literal copied into six modules — a
drift hazard, since bumping the Haiku version meant editing all six and
risked leaving one behind. They now all reference this one constant.

Note this is intentionally NOT ``settings.resolved_claude_model``: that
setting drives the configurable agent/chat model (defaulting to an older
Haiku) and can be pointed at Sonnet/Opus. The structured pipelines pin a
specific cheap model on purpose, independent of the agent model.
"""

from __future__ import annotations

FAST_MODEL = "claude-haiku-4-5-20251001"

__all__ = ["FAST_MODEL"]
