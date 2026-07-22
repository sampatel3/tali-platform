from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "relative_path",
    (
        "app/candidate_search/parser.py",
        "app/candidate_search/rerank.py",
        "app/candidate_search/grounded_evidence.py",
    ),
)
def test_migrated_search_routes_convey_role_authority_constraint(
    relative_path: str,
) -> None:
    root = Path(__file__).resolve().parents[3]
    path = root / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "prepare_route"
    ]

    assert calls, f"{relative_path} has no migrated prepare_route call"
    for call in calls:
        assert any(
            keyword.arg == "require_role_authority" for keyword in call.keywords
        ), f"{relative_path}:{call.lineno} drops the role-authority constraint"
