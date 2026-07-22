"""Ratcheting architecture gates for the universal AI routing control plane.

These inventories are intentionally exact.  When phase 4 removes a legacy
provider call or model selector, shrink the relevant baseline in this file.
Do not add a new entry to make a feature-local provider call pass: register a
typed task/deployment and route it through an explicit provider adapter.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.components.ai_routing.contracts import LifecycleState
from app.components.ai_routing.model_registry import DEFAULT_MODEL_REGISTRY
from app.components.ai_routing.task_registry import DEFAULT_TASK_REGISTRY
from app.components.ai_routing.validation import validate_control_plane

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
ROUTER_ROOT = APP_ROOT / "components" / "ai_routing"

_PURE_CORE_FILES = (
    "contracts.py",
    "fingerprints.py",
    "model_registry.py",
    "task_registry.py",
    "policy.py",
    "validation.py",
)
_PURE_STDLIB_IMPORTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "decimal",
        "enum",
        "hashlib",
        "json",
        "types",
        "typing",
        "uuid",
    }
)
_PURE_PEER_IMPORTS = frozenset(
    {"contracts", "fingerprints", "model_registry", "task_registry", "validation"}
)

# Approved compatibility seams.  Feature code supplies RoutedAnthropicClient,
# so these preserve Anthropic's native tool/citation/stream shapes without
# becoming model-selection authorities.
_APPROVED_MESSAGES_SEAMS = Counter(
    {
        ("app/llm/core.py", "messages.create"): 1,
        ("app/taali_chat/stream_round.py", "messages.stream"): 1,
    }
)

# Exact phase-4 debt inventory: synchronous one-shots and Message Batches that
# have not yet moved behind typed routing adapters.  Counts prevent an existing
# legacy file from acting as a broad allowlist for additional calls.
_LEGACY_MESSAGES_CALLS = Counter(
    {
        ("app/components/assessments/interrogation.py", "messages.create"): 1,
        ("app/components/assessments/rubric_scoring.py", "messages.create"): 1,
        ("app/cv_matching/archetype_synthesizer.py", "messages.create"): 1,
        ("app/cv_matching/calibrators/judge.py", "messages.create"): 1,
        ("app/cv_parsing/batch.py", "messages.batches.create"): 1,
        ("app/services/fit_matching_service.py", "messages.create"): 2,
        ("app/services/intent_chip_parser.py", "messages.create"): 1,
        ("app/services/interview_focus_service.py", "messages.create"): 1,
        ("app/services/interview_tech_prompt.py", "messages.create"): 1,
        ("app/services/material_change.py", "messages.create"): 1,
        ("app/services/task_spec_generator.py", "messages.create"): 1,
        ("app/sub_agents/intent_parser.py", "messages.create"): 1,
        ("app/tasks/anthropic_batch_tasks.py", "messages.batches.results"): 1,
        ("app/tasks/anthropic_batch_tasks.py", "messages.batches.retrieve"): 2,
    }
)

# Indirect provider traffic is debt too.  These are the migrated callers that
# intentionally preserve the established ``one_call`` / ``generate_structured``
# response shapes while model selection and attempt ownership live in the
# routing control plane.
_MIGRATED_LLM_GATEWAY_CALLS = Counter(
    {
        ("app/agent_chat/engine.py", "one_call"): 1,
        ("app/agent_runtime/orchestrator.py", "one_call"): 1,
        ("app/candidate_search/grounded_evidence.py", "one_call"): 1,
        ("app/candidate_search/parser.py", "generate_structured"): 1,
        ("app/candidate_search/rerank.py", "one_call"): 1,
    }
)

# Shared compatibility seam: ``generate_structured`` delegates its physical
# call to ``one_call``.  It is not a feature-local model authority.
_APPROVED_LLM_GATEWAY_SEAMS = Counter({("app/llm/structured.py", "one_call"): 1})

# Exact phase-4 debt inventory for legacy callers that reach a provider through
# the pre-router LLM helpers.  Removing a caller shrinks this baseline; adding a
# caller requires a typed routing task instead of another allowlist entry.
_LEGACY_LLM_GATEWAY_CALLS = Counter(
    {
        ("app/cv_matching/graded.py", "generate_structured"): 1,
        ("app/cv_matching/holistic.py", "generate_structured"): 3,
        ("app/cv_matching/runner.py", "generate_structured"): 1,
        ("app/cv_matching/runner_pre_screen.py", "one_call"): 1,
        ("app/cv_parsing/runner.py", "generate_structured"): 1,
        ("app/decision_policy/autoresearch.py", "generate_structured"): 1,
        ("app/services/requisition_chat_service.py", "generate_structured"): 3,
        ("app/services/requisition_intake_agent.py", "generate_structured"): 1,
        ("app/services/scorecard_draft_service.py", "generate_structured"): 1,
        ("app/services/sourcing_assist_service.py", "generate_structured"): 2,
        ("app/tasks/outreach_tasks.py", "generate_structured"): 1,
    }
)

# Known inference-provider SDK namespaces.  Keep the list broader than the
# currently installed providers so introducing another common SDK is caught
# with an empty baseline rather than silently creating a new control plane.
_PROVIDER_SDK_PREFIXES = (
    "anthropic",
    "claude_agent_sdk",
    "cohere",
    "google.genai",
    "google.generativeai",
    "graphiti_core.embedder",
    "graphiti_core.llm_client",
    "groq",
    "litellm",
    "mistralai",
    "openai",
    "vertexai.generative_models",
    "voyageai",
)

# Import symbols are inventoried, not merely files, so an existing provider
# integration cannot become a blanket allowlist for another raw client.
_PROVIDER_SDK_IMPORT_BASELINE = Counter(
    {
        ("app/candidate_graph/client.py", "anthropic", "AsyncAnthropic"): 1,
        (
            "app/candidate_graph/client.py",
            "graphiti_core.embedder.voyage",
            "VoyageAIEmbedder",
        ): 1,
        (
            "app/candidate_graph/client.py",
            "graphiti_core.embedder.voyage",
            "VoyageAIEmbedderConfig",
        ): 1,
        (
            "app/candidate_graph/client.py",
            "graphiti_core.llm_client.anthropic_client",
            "AnthropicClient",
        ): 1,
        (
            "app/candidate_graph/client.py",
            "graphiti_core.llm_client.config",
            "LLMConfig",
        ): 1,
        ("app/components/assessments/interrogation.py", "anthropic", "Anthropic"): 1,
        ("app/components/assessments/rubric_scoring.py", "anthropic", "Anthropic"): 1,
        (
            "app/components/integrations/claude_agent/sandbox_tools.py",
            "claude_agent_sdk",
            "create_sdk_mcp_server",
        ): 1,
        (
            "app/components/integrations/claude_agent/sandbox_tools.py",
            "claude_agent_sdk",
            "tool",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude_agent_sdk",
            "AssistantMessage",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude_agent_sdk",
            "ClaudeAgentOptions",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude_agent_sdk",
            "ResultMessage",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude_agent_sdk",
            "TextBlock",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude_agent_sdk",
            "ToolResultBlock",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude_agent_sdk",
            "ToolUseBlock",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude_agent_sdk",
            "UserMessage",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude_agent_sdk",
            "query",
        ): 1,
        ("app/services/claude_client_resolver.py", "anthropic", "Anthropic"): 1,
        ("app/services/fit_matching_service.py", "anthropic", "Anthropic"): 2,
        ("app/services/interview_focus_service.py", "anthropic", "Anthropic"): 1,
        ("app/services/interview_tech_prompt.py", "anthropic", "Anthropic"): 1,
        ("app/services/metered_anthropic_client.py", "anthropic", "*"): 1,
        ("app/services/metered_anthropic_client.py", "anthropic", "Anthropic"): 1,
        (
            "app/services/metered_async_anthropic_client.py",
            "anthropic",
            "AsyncAnthropic",
        ): 1,
        ("app/services/task_spec_generator.py", "anthropic", "Anthropic"): 1,
        ("app/taali_chat/stream_round.py", "anthropic", "Anthropic"): 1,
    }
)

_MODEL_ID_RE = re.compile(r"claude-[a-z0-9][a-z0-9.-]*", re.IGNORECASE)

# model_registry is the sole authority for new identifiers.  Everything below
# it is a frozen compatibility/deprecation inventory for phase 4 (pricing_service
# is the pre-router metering rate table, not a feature selector).
_MODEL_LITERAL_BASELINE = Counter(
    {
        ("app/components/ai_routing/model_registry.py", "claude-haiku-4-5"): 1,
        (
            "app/components/ai_routing/model_registry.py",
            "claude-haiku-4-5-20251001",
        ): 1,
        ("app/components/ai_routing/model_registry.py", "claude-sonnet-4-5"): 1,
        (
            "app/components/ai_routing/model_registry.py",
            "claude-sonnet-4-5-20250929",
        ): 1,
        ("app/components/ai_routing/model_registry.py", "claude-sonnet-4-6"): 1,
        ("app/components/assessments/claude_budget.py", "claude-haiku-4-5"): 1,
        (
            "app/components/assessments/interrogation.py",
            "claude-haiku-4-5-20251001",
        ): 1,
        (
            "app/components/assessments/rubric_scoring.py",
            "claude-sonnet-4-5-20250929",
        ): 1,
        (
            "app/components/integrations/claude/model_fallback.py",
            "claude-3-5-haiku-20241022",
        ): 1,
        (
            "app/components/integrations/claude/model_fallback.py",
            "claude-3-5-haiku-latest",
        ): 1,
        (
            "app/components/integrations/claude/model_fallback.py",
            "claude-3-haiku-20240307",
        ): 1,
        (
            "app/components/integrations/claude/model_fallback.py",
            "claude-haiku-4-5-20251001",
        ): 1,
        (
            "app/components/integrations/claude_agent/service.py",
            "claude-haiku-4-5-20251001",
        ): 1,
        (
            "app/cv_matching/archetype_synthesizer.py",
            "claude-sonnet-4-6",
        ): 1,
        ("app/cv_matching/calibrators/judge.py", "claude-sonnet-4-6"): 1,
        ("app/cv_matching/holistic.py", "claude-sonnet-4-6"): 1,
        ("app/decision_policy/autoresearch.py", "claude-sonnet-4-5"): 1,
        ("app/llm/models.py", "claude-haiku-4-5-20251001"): 1,
        ("app/llm/models.py", "claude-sonnet-4-6"): 1,
        ("app/platform/config.py", "claude-haiku-4-5-20251001"): 7,
        ("app/services/pricing_service.py", "claude-3-5-haiku"): 1,
        ("app/services/pricing_service.py", "claude-3-5-sonnet"): 1,
        ("app/services/pricing_service.py", "claude-3-7-sonnet"): 1,
        ("app/services/pricing_service.py", "claude-3-opus"): 1,
        ("app/services/pricing_service.py", "claude-haiku-4-5"): 1,
        ("app/services/pricing_service.py", "claude-opus-4"): 1,
        ("app/services/pricing_service.py", "claude-opus-4-5"): 1,
        ("app/services/pricing_service.py", "claude-sonnet-4-5"): 1,
        ("app/services/pricing_service.py", "claude-sonnet-4-6"): 1,
        ("app/services/pricing_service.py", "claude-sonnet-4-7"): 1,
        (
            "app/services/task_spec_generator.py",
            "claude-sonnet-4-5-20250929",
        ): 1,
    }
)

_MIGRATED_PRIMARY_TASKS = {
    "app/candidate_search/parser.py": "SEARCH_PARSE",
    "app/candidate_search/rerank.py": "SEARCH_RERANK",
    "app/candidate_search/grounded_evidence.py": "SEARCH_GROUNDING",
    "app/agent_chat/engine.py": "ROLE_CHAT_ORCHESTRATION",
    "app/taali_chat/route_setup.py": "GENERAL_CHAT_ORCHESTRATION",
    "app/agent_runtime/orchestrator.py": "AUTONOMOUS_RECRUITING_ORCHESTRATION",
}
_MIGRATED_PRIMARY_TRANSPORT_FILES = {
    # Streaming lifecycle ownership remains in the service while the typed
    # request contract is isolated in a small setup module.
    "app/taali_chat/route_setup.py": "app/taali_chat/service.py",
}

# The central transport registry is the only routed credential/client factory.
# SDK retries stay disabled because the route executor owns every physical
# attempt, reservation, retry decision, and telemetry row.
_ROUTED_RESOLVER_CALLS = Counter(
    {
        ("app/components/ai_routing/transport_registry.py", "get_metered_client"): 1,
    }
)
_ROUTED_RESOLVER_GUARDED_FILES = frozenset(
    {
        *_MIGRATED_PRIMARY_TASKS,
        *_MIGRATED_PRIMARY_TRANSPORT_FILES.values(),
        "app/candidate_search/top_candidates.py",
        "app/components/ai_routing/transport_registry.py",
    }
)

_EXPECTED_REGISTRY = {
    "anthropic.messages.haiku-4-5-20251001": (
        "claude-haiku-4-5-20251001",
        ("1.00", "5.00", "1.25", "2.00", "0.10", "0.50", "2.50", None),
    ),
    "anthropic.messages.sonnet-4-5-20250929": (
        "claude-sonnet-4-5-20250929",
        ("3.00", "15.00", "3.75", "6.00", "0.30", "1.50", "7.50", None),
    ),
    "anthropic.messages.sonnet-4-6": (
        "claude-sonnet-4-6",
        ("3.00", "15.00", "3.75", "6.00", "0.30", "1.50", "7.50", "1.10"),
    ),
}


@lru_cache(maxsize=1)
def _python_trees() -> tuple[tuple[str, ast.Module], ...]:
    parsed: list[tuple[str, ast.Module]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        parsed.append(
            (rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        )
    return tuple(parsed)


def _attribute_parts(node: ast.AST) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def _messages_operation(node: ast.AST) -> str | None:
    parts = _attribute_parts(node)
    for index, part in enumerate(parts):
        if part != "messages":
            continue
        tail = parts[index + 1 :]
        if tail[:1] in (["create"], ["stream"]):
            return f"messages.{tail[0]}"
        if len(tail) >= 2 and tail[0] == "batches":
            return f"messages.batches.{tail[1]}"
    return None


def _messages_calls() -> Counter[tuple[str, str]]:
    calls: Counter[tuple[str, str]] = Counter()
    for rel, tree in _python_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                operation = _messages_operation(node.func)
                if operation is not None:
                    calls[(rel, operation)] += 1
    return calls


def _llm_gateway_calls() -> Counter[tuple[str, str]]:
    calls: Counter[tuple[str, str]] = Counter()
    gateway_names = {"one_call", "generate_structured"}
    for rel, tree in _python_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts = _attribute_parts(node.func)
            if parts and parts[-1] in gateway_names:
                calls[(rel, parts[-1])] += 1
    return calls


def _is_provider_sdk_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in _PROVIDER_SDK_PREFIXES
    )


def _provider_sdk_imports() -> Counter[tuple[str, str, str]]:
    imports: Counter[tuple[str, str, str]] = Counter()
    for rel, tree in _python_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_provider_sdk_module(alias.name):
                        imports[(rel, alias.name, "*")] += 1
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_provider_sdk_module(module):
                    for alias in node.names:
                        imports[(rel, module, alias.name)] += 1
    return imports


def _model_literals() -> Counter[tuple[str, str]]:
    literals: Counter[tuple[str, str]] = Counter()
    for rel, tree in _python_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            value = node.value.strip()
            if _MODEL_ID_RE.fullmatch(value):
                literals[(rel, value)] += 1
    return literals


def _counter_delta(actual: Counter, expected: Counter) -> str:
    return f"added={dict(actual - expected)}, removed={dict(expected - actual)}"


def test_pure_router_core_has_only_pure_imports_and_stays_below_500_loc() -> None:
    violations: list[str] = []
    for name in _PURE_CORE_FILES:
        path = ROUTER_ROOT / name
        source = path.read_text(encoding="utf-8")
        line_count = len(source.splitlines())
        if line_count >= 500:
            violations.append(f"{name}: {line_count} LOC (maximum is 499)")

        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root not in _PURE_STDLIB_IMPORTS:
                        violations.append(f"{name}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                root = module.split(".", 1)[0]
                allowed = (
                    root in _PURE_PEER_IMPORTS
                    if node.level
                    else root in _PURE_STDLIB_IMPORTS
                )
                if not allowed:
                    violations.append(
                        f"{name}:{node.lineno} imports "
                        f"{' .' * node.level}{module or '<package>'}"
                    )

    assert not violations, (
        "Router policy/registries must remain deterministic pure Python: no "
        "provider, network, settings, database, or application-service imports, "
        "and each file must stay below 500 LOC.\n  " + "\n  ".join(violations)
    )


def test_direct_anthropic_messages_calls_match_exact_migration_inventory() -> None:
    actual = _messages_calls()
    expected = _APPROVED_MESSAGES_SEAMS + _LEGACY_MESSAGES_CALLS
    assert actual == expected, (
        "Direct Anthropic Messages calls changed. New calls must use a typed "
        "TaskKey plus a route/provider adapter; removed calls must shrink the "
        "phase-4 debt baseline. " + _counter_delta(actual, expected)
    )


def test_indirect_llm_gateway_calls_match_exact_migration_inventory() -> None:
    actual = _llm_gateway_calls()
    expected = (
        _MIGRATED_LLM_GATEWAY_CALLS
        + _APPROVED_LLM_GATEWAY_SEAMS
        + _LEGACY_LLM_GATEWAY_CALLS
    )
    assert actual == expected, (
        "Indirect one_call/generate_structured traffic changed. New feature "
        "calls must enter through a typed route; removed legacy calls must "
        "shrink the phase-4 debt baseline. " + _counter_delta(actual, expected)
    )


def test_provider_sdk_imports_match_exact_legacy_inventory() -> None:
    actual = _provider_sdk_imports()
    assert actual == _PROVIDER_SDK_IMPORT_BASELINE, (
        "Inference-provider SDK imports changed. Provider transports belong "
        "behind registered routing adapters; do not introduce a raw Anthropic, "
        "Agent SDK, Graphiti/Voyage, or alternate-provider bypass. "
        + _counter_delta(actual, _PROVIDER_SDK_IMPORT_BASELINE)
    )


def test_central_routed_resolver_disables_hidden_sdk_retries() -> None:
    resolver_entrypoints = {
        "get_client_for_org",
        "get_metered_client",
        "get_raw_shared_client",
        "get_shared_client",
    }
    actual: Counter[tuple[str, str]] = Counter()
    violations: list[str] = []

    for rel, tree in _python_trees():
        if rel not in _ROUTED_RESOLVER_GUARDED_FILES:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts = _attribute_parts(node.func)
            if not parts or parts[-1] not in resolver_entrypoints:
                continue
            entrypoint = parts[-1]
            actual[(rel, entrypoint)] += 1
            retry_keywords = [
                keyword for keyword in node.keywords if keyword.arg == "max_retries"
            ]
            is_literal_zero = (
                len(retry_keywords) == 1
                and isinstance(retry_keywords[0].value, ast.Constant)
                and type(retry_keywords[0].value.value) is int
                and retry_keywords[0].value.value == 0
            )
            if not is_literal_zero:
                violations.append(
                    f"{rel}:{node.lineno} {entrypoint} must pass literal "
                    "max_retries=0"
                )

    if actual != _ROUTED_RESOLVER_CALLS:
        violations.append(
            "routed resolver inventory changed: "
            + _counter_delta(actual, _ROUTED_RESOLVER_CALLS)
        )

    assert not violations, (
        "Only the transport registry may resolve a routed provider client, and "
        "hidden SDK retries are forbidden.\n  " + "\n  ".join(violations)
    )


def test_raw_claude_model_literals_match_exact_registry_and_legacy_inventory() -> None:
    actual = _model_literals()
    assert actual == _MODEL_LITERAL_BASELINE, (
        "Raw Claude model identifiers changed. model_registry.py is the sole "
        "authority for new IDs; migrate feature-local selectors instead of "
        "expanding the phase-4 compatibility inventory. "
        + _counter_delta(actual, _MODEL_LITERAL_BASELINE)
    )


def test_primary_migrations_use_typed_routes_and_no_feature_model_selectors() -> None:
    violations: list[str] = []
    for rel, expected_task in _MIGRATED_PRIMARY_TASKS.items():
        path = BACKEND_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        transport_rel = _MIGRATED_PRIMARY_TRANSPORT_FILES.get(rel, rel)
        transport_path = BACKEND_ROOT / transport_rel
        transport_tree = (
            tree
            if transport_rel == rel
            else ast.parse(
                transport_path.read_text(encoding="utf-8"),
                filename=str(transport_path),
            )
        )
        task_keys = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "TaskKey"
        }
        called_names = {
            parts[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (parts := _attribute_parts(node.func))
        }
        if task_keys != {expected_task}:
            violations.append(
                f"{rel}: expected only TaskKey.{expected_task}, found {sorted(task_keys)}"
            )
        if "prepare_route" not in called_names:
            violations.append(f"{rel}: does not call prepare_route")
        referenced_names = {
            node.id
            for selected_tree in (tree, transport_tree)
            for node in ast.walk(selected_tree)
            if isinstance(node, ast.Name)
        }
        if "routed_messages_client" not in referenced_names:
            violations.append(
                f"{rel} / {transport_rel}: does not use routed_messages_client"
            )
        forbidden_entrypoints = {
            "RoutedAnthropicClient",
            "get_client_for_org",
            "get_metered_client",
            "get_raw_shared_client",
            "get_shared_client",
        }
        bypasses = sorted(forbidden_entrypoints.intersection(referenced_names))
        if bypasses:
            violations.append(
                f"{rel}: bypasses the transport registry via {bypasses}"
            )

        scanned_trees = [(rel, tree)]
        if transport_rel != rel:
            scanned_trees.append((transport_rel, transport_tree))
        for scanned_rel, scanned_tree in scanned_trees:
            for node in ast.walk(scanned_tree):
                if isinstance(node, ast.Call) and _messages_operation(node.func):
                    violations.append(
                        f"{scanned_rel}:{node.lineno} calls Anthropic Messages directly"
                    )
                if isinstance(node, ast.Name) and re.fullmatch(
                    r"[A-Z][A-Z0-9_]*_MODEL", node.id
                ):
                    violations.append(
                        f"{scanned_rel}:{node.lineno} reads selector {node.id}"
                    )
                if isinstance(node, ast.Attribute) and (
                    re.fullmatch(r"CLAUDE_[A-Z0-9_]*MODEL", node.attr)
                    or (
                        node.attr.startswith("resolved_")
                        and node.attr.endswith("model")
                    )
                ):
                    violations.append(
                        f"{scanned_rel}:{node.lineno} reads settings.{node.attr}"
                    )
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and (
                        _MODEL_ID_RE.fullmatch(node.value.strip())
                        or re.fullmatch(
                            r"CLAUDE_[A-Z0-9_]*MODEL", node.value.strip()
                        )
                    )
                ):
                    violations.append(
                        f"{scanned_rel}:{node.lineno} embeds {node.value!r}"
                    )
                if isinstance(node, ast.ImportFrom) and (
                    node.module or ""
                ).endswith("llm.models"):
                    violations.append(
                        f"{scanned_rel}:{node.lineno} imports legacy llm.models"
                    )

    assert not violations, (
        "Migrated primary workflows must enter through their typed TaskKey and "
        "central transport registry; direct adapters, resolvers, and feature-local "
        "model selection are forbidden.\n  "
        + "\n  ".join(violations)
    )


def test_physical_attempt_lifecycle_is_owned_by_provider_adapter() -> None:
    lifecycle_methods = {
        "begin_attempt",
        "finish_success",
        "finish_error",
        "routing_metadata",
    }
    callers: set[str] = set()
    for rel, tree in _python_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in lifecycle_methods
            ):
                callers.add(rel)

    adapter = "app/components/ai_routing/adapters/anthropic_messages.py"
    assert callers == {adapter}, (
        "Only a provider adapter may bracket physical attempts or write routing "
        f"metadata; found callers {sorted(callers)}"
    )

    adapter_tree = ast.parse(
        (BACKEND_ROOT / adapter).read_text(encoding="utf-8"), filename=adapter
    )
    imported_peers = {
        node.module
        for node in ast.walk(adapter_tree)
        if isinstance(node, ast.ImportFrom) and node.level
    }
    assert imported_peers == {
        "admission",
        "anthropic_estimation",
        "contracts",
        "execution",
    }, (
        "The Anthropic adapter boundary may depend only on centralized admission, "
        "request estimation, neutral contracts, and "
        f"route execution, found relative imports {sorted(imported_peers)}"
    )


def test_exact_model_registry_is_priced_and_closes_over_task_profiles() -> None:
    deployments = {
        deployment.deployment_id: deployment
        for deployment in DEFAULT_MODEL_REGISTRY.deployments
    }
    assert set(deployments) == set(_EXPECTED_REGISTRY), (
        "Every added/removed deployment requires an intentional registry, pricing, "
        "adapter, evaluation, and architecture-gate update"
    )

    for deployment_id, (model_id, expected_rates) in _EXPECTED_REGISTRY.items():
        deployment = deployments[deployment_id]
        assert deployment.model_id == model_id
        assert deployment.lifecycle is LifecycleState.ACTIVE
        assert deployment.provider == "anthropic"
        assert deployment.endpoint == "messages"
        assert deployment.runtime == "anthropic_api"
        assert deployment.transport_contract == "anthropic_messages_v1"
        pricing = deployment.pricing
        assert pricing is not None and pricing.currency == "USD"
        actual_rates = (
            pricing.input_per_million,
            pricing.output_per_million,
            pricing.cache_write_5m_per_million,
            pricing.cache_write_1h_per_million,
            pricing.cache_read_per_million,
            pricing.batch_input_per_million,
            pricing.batch_output_per_million,
            pricing.us_inference_multiplier,
        )
        normalized_expected = tuple(
            Decimal(value) if value is not None else None for value in expected_rates
        )
        assert actual_rates == normalized_expected, (
            f"Exact pricing changed for {deployment_id}; update the registry from "
            "authoritative provider pricing and revise this gate intentionally"
        )
        assert all(rate > 0 for rate in actual_rates if rate is not None)
        assert DEFAULT_MODEL_REGISTRY.resolve(model_id) is deployment

    validate_control_plane(DEFAULT_MODEL_REGISTRY, DEFAULT_TASK_REGISTRY)
    referenced_ids = {
        deployment_id
        for profile in DEFAULT_TASK_REGISTRY.profiles
        for deployment_id in (
            *profile.candidate_deployment_ids,
            *profile.fallback_deployment_ids,
        )
    }
    assert referenced_ids <= deployments.keys()
    assert (ROUTER_ROOT / "adapters" / "anthropic_messages.py").is_file()
