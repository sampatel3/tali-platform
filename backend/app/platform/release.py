"""Runtime source-revision metadata for exact deployment verification."""

from __future__ import annotations

import os
import re


def runtime_release_sha() -> str | None:
    """Return the exact deployed source revision without provider I/O."""

    # Railway supplies its Git revision to GitHub-connected deployments. The
    # explicit Taali value is a fallback for other deployment environments.
    for key in ("RAILWAY_GIT_COMMIT_SHA", "TALI_RELEASE_SHA"):
        value = (os.environ.get(key) or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", value):
            return value
    return None


__all__ = ["runtime_release_sha"]
