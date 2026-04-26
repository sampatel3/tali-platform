# Claude Integration Package

This package owns direct Anthropic API access for TAALI.

- `service.py` is the canonical wrapper for direct Messages API calls used by candidate chat and submission analysis.
- `model_fallback.py` owns the supported Haiku fallback chain.
- Runtime budget helpers live in `backend/app/components/assessments/claude_budget.py` because they operate on assessment prompt records, not raw API calls.
- Claude CLI terminal setup lives in `backend/app/components/assessments/terminal_runtime.py` because it prepares candidate assessment workspaces.

Before adding a new Claude call, update `docs/claude/README.md` with the product surface, trigger, stored fields, frontend consumer, and test coverage.
