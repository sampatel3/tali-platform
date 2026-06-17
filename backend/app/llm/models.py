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

# Sonnet is the mid-tier model used where Haiku isn't accurate enough — e.g.
# candidate-evidence grounding via Citations, where Haiku under-performed. Pinned
# to the same Sonnet the holistic scorer uses so both CV reads agree, and kept as
# a constant so a module that forgets the env override doesn't silently fall back
# to Haiku (the per-service env drift that put grounding on Haiku in prod).
SONNET_MODEL = "claude-sonnet-4-6"

__all__ = ["FAST_MODEL", "SONNET_MODEL"]
