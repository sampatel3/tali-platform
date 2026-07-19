#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical Workable QA diagnostic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scripts.workable_qa_diagnostic import main


if __name__ == "__main__":
    main()
