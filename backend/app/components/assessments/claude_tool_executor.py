"""Dispatcher that turns tool-name + input dicts into E2B sandbox
operations on a candidate's assessment repo.

Originally built as leaf A of the terminal-removal refactor and consumed
by the hand-rolled tool loop. Now reused by the ``claude-agent-sdk``
integration: the sandbox MCP server (``components.integrations.claude_agent
.sandbox_tools``) calls :meth:`AssessmentToolExecutor.dispatch` for each
``mcp__sandbox__Read|Write|Edit|Bash`` invocation, and the dict result is
serialised back to the model as a ``tool_result`` content block.

Design contract
---------------
- Every public dispatch returns a plain JSON-serializable dict shaped
  ``{"ok": bool, "result": str | dict, "error": str?}``. The caller
  embeds this verbatim as a ``tool_result`` content block in the next
  turn so Claude can self-correct.
- We NEVER raise out of ``dispatch``. Any failure — bad path, missing
  file, sandbox RPC error — turns into ``{"ok": False, "error": ...}``.
  Raising would force the caller to invent an error-result shape on the
  fly; doing it here keeps the surface consistent.
- Path sanitization mirrors
  :func:`app.domains.assessments_runtime.candidate_claude_routes._sanitize_repo_path`
  exactly: empty rejects, absolute rejects, ``..``/``.`` parts reject,
  backslash normalization, no escape from ``repo_root``.
- The executor is stateless given the ``(e2b, sandbox, repo_root)``
  triple. No DB session, no org-scoped config. The caller owns sandbox
  lifecycle.
"""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from typing import Any, Dict, List

from ...platform.config import settings


logger = logging.getLogger(__name__)


# Per-command wall-clock cap. Sourced from settings (default 60s) so a real
# pytest/build run has headroom — the previous hardcoded 10s killed anything
# but a trivial test invocation.
_RUN_COMMAND_TIMEOUT_SECONDS = max(1, int(getattr(settings, "CLAUDE_TOOL_TIMEOUT_SECONDS", 60) or 60))

# Defense-in-depth command guardrail for the candidate sandbox. The sandbox is
# isolated + ephemeral and (on the agentic path) holds no platform secret, so
# this is not the primary control — the scoring rubric measures the candidate's
# judgment, not the agent's raw capability. But we block raw network/exfil tools
# and privilege escalation so the easy misuse paths (curl a solution, exfiltrate,
# sudo) are closed. pip/pytest/python/grep/git are intentionally NOT blocked —
# they're core to the task. (A full egress cut is the E2B_SANDBOX_ALLOW_INTERNET
# switch once deps are pre-baked into the template.)
_BLOCKED_COMMAND_PATTERNS = [
    re.compile(p)
    for p in (
        r"(^|[\s;&|`(])(sudo|doas)([\s;&|`)]|$)",
        r"(^|[\s;&|`(])(curl|wget|nc|ncat|netcat|socat|telnet|ssh|scp|sftp|ftp)([\s;&|`)]|$)",
    )
]


def _blocked_command_reason(command: str) -> str | None:
    """Return a human reason if ``command`` hits the guardrail, else None."""
    text = (command or "").lower()
    for pattern in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(text):
            return (
                "blocked_command: network/exfil and privilege-escalation commands "
                "(curl, wget, nc, ssh, sudo, …) are disabled in the assessment "
                "sandbox. Use the repository tools and pytest instead."
            )
    return None

# Cap stdout/stderr in run_command results — a 50 MB log dump would
# blow the Anthropic message token budget and the tool_result would be
# rejected. The cap is chosen to match what fits comfortably in a few
# hundred tokens of context per stream.
_RUN_COMMAND_OUTPUT_CAP = 8000


def _sanitize_repo_path(path: str | None) -> str:
    """Normalize a repo-relative path or return ``""`` to signal reject.

    Mirrors the helper in ``candidate_claude_routes`` byte-for-byte so the
    Claude-side and tool-use side agree on what's traversable. Reject
    rules (any one fires → return ``""``):

    - empty/whitespace-only input
    - absolute path (starts with ``/``)
    - any ``.`` or ``..`` segment
    - non-parsable as ``PurePosixPath``
    """
    raw_path = str(path or "").strip().replace("\\", "/")
    if not raw_path:
        return ""
    try:
        normalized = PurePosixPath(raw_path)
    except Exception:
        return ""
    if normalized.is_absolute():
        return ""
    parts = [str(part).strip() for part in normalized.parts if str(part).strip()]
    if not parts or any(part in {".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def _join_under_root(repo_root: str, rel: str) -> str:
    """Join an already-sanitized relative path under ``repo_root``."""
    root = (repo_root or "").rstrip("/")
    if not rel:
        return root or "/"
    if not root:
        return rel
    return f"{root}/{rel}"


def _err(error: str) -> Dict[str, Any]:
    return {"ok": False, "error": error}


def _ok(result: Any) -> Dict[str, Any]:
    return {"ok": True, "result": result}


def _extract_process_output(result: Any) -> tuple[str, str, int | None]:
    """Pull (stdout, stderr, exit_code) out of an E2B command result.

    Same logic as ``submission_runtime._extract_process_output`` —
    duplicated rather than imported to keep this module dependency-free
    (submission_runtime drags in the whole assessment domain).
    """
    if isinstance(result, dict):
        stdout = str(result.get("stdout") or result.get("out") or "")
        stderr = str(result.get("stderr") or result.get("err") or "")
        exit_code = result.get("exit_code")
        try:
            exit_code = int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            exit_code = None
        return stdout, stderr, exit_code

    stdout = str(getattr(result, "stdout", "") or getattr(result, "out", "") or "")
    stderr = str(getattr(result, "stderr", "") or getattr(result, "err", "") or "")
    exit_code = getattr(result, "exit_code", None)
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    return stdout, stderr, exit_code


def _truncate(text: str, cap: int = _RUN_COMMAND_OUTPUT_CAP) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n... [truncated {len(text) - cap} chars]"


class AssessmentToolExecutor:
    """Dispatches Anthropic tool-use calls into a candidate's E2B sandbox.

    Each :meth:`dispatch` call returns a JSON-serializable result shaped
    ``{"ok": bool, "result": str | dict, "error": str?}`` that the caller
    embeds as a ``tool_result`` content block in the next message turn.
    Errors are surfaced to Claude as tool results (not exceptions) so the
    model can self-correct.

    Args:
        e2b_service: ``E2BService`` instance — used for ``run_command``
            so we share the same RPC wrapper as ``submission_runtime``.
        sandbox: An active ``e2b.Sandbox`` for this candidate's
            assessment session. The caller owns its lifecycle.
        repo_root: Absolute sandbox path under which all tool operations
            must occur (e.g. ``/home/user/repo``). Tool ``path`` inputs
            are joined under this root after sanitization.
    """

    def __init__(self, e2b_service: Any, sandbox: Any, repo_root: str):
        self._e2b = e2b_service
        self._sandbox = sandbox
        self._repo_root = (repo_root or "").rstrip("/") or "/"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def dispatch(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Route a single Anthropic ``tool_use`` block to the matching op.

        Unknown tool names return ``{"ok": False, "error": "unknown_tool: ..."}``
        rather than raising — Claude can recover by picking a known tool
        on the next turn.
        """
        if not isinstance(tool_input, dict):
            return _err("invalid_input: tool_input must be an object")

        handler = self._HANDLERS.get(tool_name)
        if handler is None:
            return _err(f"unknown_tool: {tool_name}")

        try:
            return handler(self, tool_input)
        except Exception as exc:  # noqa: BLE001 — defensive perimeter
            logger.warning(
                "AssessmentToolExecutor.dispatch %s raised: %s",
                tool_name,
                exc,
            )
            return _err(f"internal_error: {exc.__class__.__name__}: {exc}")

    # ------------------------------------------------------------------
    # Individual tool handlers
    # ------------------------------------------------------------------

    def _resolve_path(self, raw: Any, *, allow_root: bool = False) -> tuple[str, str | None]:
        """Sanitize ``raw`` and return ``(absolute_sandbox_path, error)``.

        When ``allow_root`` is True an empty input resolves to the repo
        root itself (useful for ``list_dir("")``). Otherwise empty input
        is rejected.
        """
        # PurePosixPath treats "" and "." as the current dir — collapse
        # them to "" so allow_root can opt-in cleanly.
        raw_str = str(raw or "").strip().replace("\\", "/")
        if raw_str in {"", "."}:
            if allow_root:
                return self._repo_root, None
            return "", "invalid_path: path is required"

        sanitized = _sanitize_repo_path(raw_str)
        if not sanitized:
            return "", "invalid_path: path must be repo-relative and not contain '..'"
        return _join_under_root(self._repo_root, sanitized), None

    def _list_dir(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        target, err = self._resolve_path(tool_input.get("path"), allow_root=True)
        if err:
            return _err(err)
        try:
            entries = self._sandbox.files.list(target)
        except Exception as exc:  # noqa: BLE001 — E2B raises a hierarchy of errors
            return _err(f"list_failed: {exc.__class__.__name__}: {exc}")

        # E2B returns EntryInfo objects (or possibly dicts if mocked) —
        # accept both, then sort by name so output is deterministic for
        # the model.
        names: List[str] = []
        for entry in entries or []:
            if isinstance(entry, dict):
                name = str(entry.get("name") or "")
            else:
                name = str(getattr(entry, "name", "") or "")
            if name:
                names.append(name)
        return _ok(sorted(names))

    def _read_file(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        target, err = self._resolve_path(tool_input.get("path"))
        if err:
            return _err(err)
        try:
            content = self._sandbox.files.read(target)
        except Exception as exc:  # noqa: BLE001
            return _err(f"read_failed: {exc.__class__.__name__}: {exc}")
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                return _err(f"read_failed: not valid UTF-8: {exc}")
        return _ok(str(content))

    def _write_file(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        target, err = self._resolve_path(tool_input.get("path"))
        if err:
            return _err(err)
        content = tool_input.get("content")
        if content is None:
            return _err("invalid_input: content is required")
        if not isinstance(content, str):
            return _err("invalid_input: content must be a string")
        try:
            self._sandbox.files.write(target, content)
        except Exception as exc:  # noqa: BLE001
            return _err(f"write_failed: {exc.__class__.__name__}: {exc}")
        return _ok(f"wrote {len(content)} chars to {tool_input.get('path')}")

    def _apply_edit(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        target, err = self._resolve_path(tool_input.get("path"))
        if err:
            return _err(err)

        old = tool_input.get("old")
        new = tool_input.get("new")
        if not isinstance(old, str) or not old:
            return _err("invalid_input: 'old' must be a non-empty string")
        if not isinstance(new, str):
            return _err("invalid_input: 'new' must be a string")

        try:
            current = self._sandbox.files.read(target)
        except Exception as exc:  # noqa: BLE001
            return _err(f"read_failed: {exc.__class__.__name__}: {exc}")
        if isinstance(current, bytes):
            try:
                current = current.decode("utf-8")
            except UnicodeDecodeError as exc:
                return _err(f"read_failed: not valid UTF-8: {exc}")
        current = str(current)

        hits = current.count(old)
        if hits == 0:
            return _err("no_match")
        if hits > 1:
            return _err(f"ambiguous_match: {hits} hits")

        updated = current.replace(old, new, 1)
        try:
            self._sandbox.files.write(target, updated)
        except Exception as exc:  # noqa: BLE001
            return _err(f"write_failed: {exc.__class__.__name__}: {exc}")

        # Best-effort delta summary so Claude sees something more useful
        # than a bare True. Keep it one line.
        old_lines = old.count("\n") + 1
        new_lines = new.count("\n") + 1
        summary = (
            f"replaced 1 occurrence in {tool_input.get('path')} "
            f"({old_lines} line(s) -> {new_lines} line(s))"
        )
        return _ok(summary)

    def _run_command(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return _err("invalid_input: 'command' is required")

        blocked = _blocked_command_reason(command)
        if blocked:
            logger.warning(
                "run_command blocked in assessment sandbox: %r", command.strip()[:120]
            )
            return _err(blocked)

        try:
            process = self._e2b.run_command(
                self._sandbox,
                command,
                cwd=self._repo_root,
                timeout=_RUN_COMMAND_TIMEOUT_SECONDS,
            )
            stdout, stderr, exit_code = _extract_process_output(process)
            return _ok(
                {
                    "stdout": _truncate(stdout),
                    "stderr": _truncate(stderr),
                    "exit_code": exit_code,
                }
            )
        except Exception as exc:  # noqa: BLE001 — E2B raises on timeout/non-zero
            # Some E2B versions attach stdout/stderr/exit_code to the
            # exception itself (the "command failed" path). Pull what we
            # can so Claude sees the real output, not just the error
            # class name.
            stdout, stderr, exit_code = _extract_process_output(exc)
            if stdout or stderr or exit_code is not None:
                return _ok(
                    {
                        "stdout": _truncate(stdout),
                        "stderr": _truncate(stderr or str(exc)),
                        "exit_code": exit_code,
                    }
                )
            return _err(f"run_failed: {exc.__class__.__name__}: {exc}")

    # ------------------------------------------------------------------
    # Dispatch table — kept at class scope so it's bound once at import
    # time. Tool names are the internal verbs; the agent SDK MCP layer in
    # ``sandbox_tools.py`` maps Read/Write/Edit/Bash → these.
    # ------------------------------------------------------------------

    _HANDLERS: Dict[str, Any] = {
        "list_dir": _list_dir,
        "read_file": _read_file,
        "write_file": _write_file,
        "apply_edit": _apply_edit,
        "run_command": _run_command,
    }


__all__ = ["AssessmentToolExecutor"]
