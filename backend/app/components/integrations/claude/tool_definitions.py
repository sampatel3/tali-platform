"""Anthropic ``tools=`` schemas for the assessment sandbox tool surface.

Exposes ``TOOLS`` — the canonical list of tool definitions handed to
``messages.create`` so Claude can call into a candidate's E2B sandbox
during agentic chat. Each entry is the exact shape Anthropic expects
(``name``, ``description``, ``input_schema``).

These pair 1:1 with the dispatchers in
``app.components.assessments.claude_tool_executor.AssessmentToolExecutor``.
Keep the two files in sync — adding or renaming a tool here without an
executor branch (or vice versa) will surface as a ``tool_result`` error
to the model at runtime.

This module is leaf A of the terminal-removal refactor. The agentic-chat
service (separate PR) will be the caller that threads ``TOOLS`` into
``messages.create(..., tools=TOOLS)``.
"""

from __future__ import annotations

from typing import Any, Dict, List


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_dir",
        "description": (
            "List files and subdirectories under a path inside the candidate's "
            "sandbox repo. Returns sorted names (no leading slash). Use this to "
            "explore the repo before reading specific files. Paths are relative "
            "to the repo root; absolute paths and traversal (..) are rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Repo-relative directory path (use \"\" or \".\" for the "
                        "repo root)."
                    ),
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file from the sandbox repo and return its full "
            "contents. Use before apply_edit so you can match the existing text "
            "exactly. Returns an error result (not an exception) if the file is "
            "missing or unreadable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "apply_edit",
        "description": (
            "Replace an exact string in a sandbox file. The ``old`` string must "
            "appear EXACTLY ONCE in the file; if it appears zero times the call "
            "returns ``no_match`` and if it appears more than once it returns "
            "``ambiguous_match`` — in either case make the snippet more "
            "specific and retry. Whitespace and indentation must match byte-for-"
            "byte. Prefer this over write_file for surgical changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path to edit.",
                },
                "old": {
                    "type": "string",
                    "description": (
                        "Exact text to replace. Must occur exactly once in the "
                        "file."
                    ),
                },
                "new": {
                    "type": "string",
                    "description": "Replacement text.",
                },
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or overwrite an existing file with ``content``. "
            "Use for new files or wholesale rewrites; prefer apply_edit for "
            "small in-place changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path to write.",
                },
                "content": {
                    "type": "string",
                    "description": "Full UTF-8 file content.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Execute a shell command inside the sandbox at the repo root with a "
            "10-second timeout. Returns stdout, stderr, and the exit code. Use "
            "for running tests, linters, or quick filesystem inspection. The "
            "command is not interactive — anything that prompts for input will "
            "hang and time out."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run (single line).",
                }
            },
            "required": ["command"],
        },
    },
]


__all__ = ["TOOLS"]
