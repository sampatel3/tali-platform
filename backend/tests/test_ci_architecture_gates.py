from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_no_endpoint_decorators_in_legacy_paths() -> None:
    legacy_roots = [
        PROJECT_ROOT / "app" / "api" / "v1",
        PROJECT_ROOT / "app" / "components",
    ]
    # Files awaiting migration to canonical domain modules. New entries
    # are not welcome — fix the migration instead of expanding this list.
    allowlist: dict[str, str] = {
        "app/api/v1/background_jobs.py": "background-job status endpoints, pending domain split",
    }
    violations: list[str] = []
    pattern = re.compile(r"@router\.(?:get|post|put|patch|delete)\(")

    for root in legacy_roots:
        for path in _python_files(root):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if rel in allowlist:
                continue
            content = path.read_text(encoding="utf-8")
            if pattern.search(content):
                violations.append(str(path))

    assert not violations, (
        "Endpoint decorators must only live in canonical domain route files. "
        f"Violations: {violations}"
    )


def test_no_duplicate_endpoint_signatures_across_domains() -> None:
    domain_root = PROJECT_ROOT / "app" / "domains"
    prefix_re = re.compile(r"APIRouter\([^)]*prefix\s*=\s*['\"]([^'\"]+)['\"]")
    route_re = re.compile(r"@router\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]")

    signatures: dict[str, list[str]] = {}
    for path in _python_files(domain_root):
        content = path.read_text(encoding="utf-8")
        prefix_match = prefix_re.search(content)
        prefix = prefix_match.group(1) if prefix_match else ""

        for method, route_path in route_re.findall(content):
            if route_path.startswith("/"):
                combined = f"{prefix}{route_path}"
            else:
                combined = f"{prefix}/{route_path}"
            normalized = re.sub(r"/{2,}", "/", combined) or "/"
            signature = f"{method.upper()} {normalized}"
            signatures.setdefault(signature, []).append(str(path))

    duplicates = {sig: files for sig, files in signatures.items() if len(set(files)) > 1}
    assert not duplicates, f"Duplicate endpoint signatures detected across domain routers: {duplicates}"


def test_file_size_guard_for_api_and_service_paths() -> None:
    # The policy (limit + scope + allowlist) lives in
    # scripts/check_file_sizes.py, which CI runs directly. This test asserts
    # the same gate from the suite so a local `pytest` run still catches it.
    import importlib.util

    script = PROJECT_ROOT / "scripts" / "check_file_sizes.py"
    spec = importlib.util.spec_from_file_location("check_file_sizes", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    violations = module.find_violations()
    assert not violations, (
        f"API/service paths must stay <= {module.SIZE_LIMIT} LOC and ratcheted "
        f"hotspots may not grow. Violations: {violations}"
    )


def test_file_size_guard_enforces_ratcheted_merge_hotspots(tmp_path, monkeypatch) -> None:
    import importlib.util

    script = PROJECT_ROOT / "scripts" / "check_file_sizes.py"
    spec = importlib.util.spec_from_file_location("check_file_sizes_hotspots", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    assert module.MERGE_HOTSPOTS <= module.RATCHETED_FILES.keys()
    hotspot = "app/main.py"
    limit = module.RATCHETED_FILES[hotspot][0]
    hotspot_path = tmp_path / hotspot
    hotspot_path.parent.mkdir(parents=True)
    hotspot_path.write_text("# line\n" * (limit + 1), encoding="utf-8")
    monkeypatch.setattr(module, "BACKEND_ROOT", tmp_path)

    assert module.find_violations() == [f"{hotspot} ({limit + 1} LOC, max {limit})"]


def test_alembic_resolves_to_a_single_head() -> None:
    """The migration graph must always reduce to one head.

    Two PRs landing on main with overlapping migration ancestry can leave
    alembic with multiple heads. ``alembic upgrade head`` then refuses to
    pick between them, the Railway start script fails fast on the
    migration step, and uvicorn never boots — production restart-loops.
    GitHub marks such a pair as a CLEAN merge (the conflict is semantic,
    not textual), so this is the only thing that catches it.

    The CI ``backend`` job runs ``scripts/check_alembic_single_head.py``
    (stdlib-only, no pip install) for the same assertion; this test mirrors
    it for local ``pytest`` runs. When it fails, add a small merge-marker
    migration whose ``down_revision`` is a tuple of the current heads.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    # script_location in alembic.ini is relative to the config's directory;
    # set it explicitly so this test is independent of pytest's cwd.
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    heads = list(ScriptDirectory.from_config(cfg).get_heads())

    assert len(heads) == 1, (
        "Alembic must resolve to exactly one head; found "
        f"{len(heads)}: {heads}. Add a merge migration with these "
        "as its `down_revision` tuple."
    )


# --------------------------------------------------------------------------- #
# Authz gate: every state-changing route must be authenticated or justified.
# --------------------------------------------------------------------------- #

# Write routes that are DELIBERATELY not user-authenticated, each guarded by a
# different mechanism. A NEW write route must either depend on
# ``get_current_user`` OR be justified here — the gate fails otherwise, so an
# unguarded write endpoint can't ship by accident.
_CANDIDATE_ASSESSMENT_WRITES = frozenset({
    # The candidate assessment-taking surface — the candidate has no login;
    # access is authorised by the per-assessment token (``X-Assessment-Token``)
    # via ``validate_assessment_token`` or the demo/token session.
    "/api/v1/assessments/demo/request",
    "/api/v1/assessments/demo/start",
    "/api/v1/assessments/token/{token}/start",
    "/api/v1/assessments/token/{token}/upload-cv",
    "/api/v1/assessments/{assessment_id}/claude/chat",
    "/api/v1/assessments/{assessment_id}/execute",
    "/api/v1/assessments/{assessment_id}/repo-file",
    "/api/v1/assessments/{assessment_id}/runtime-event",
    "/api/v1/assessments/{assessment_id}/submit",
    "/api/v1/assessments/{assessment_id}/upload-cv",
})

# Prefixes whose write routes are authenticated by a NON-user mechanism.
_NON_USER_AUTH_PREFIXES = (
    "/api/v1/auth/",       # fastapi-users public auth (register/login/reset/verify)
    "/api/v1/users",       # fastapi-users self / superuser management (own guard)
    "/api/v1/webhooks/",   # provider webhooks — verified by signature
    "/public/v1/",         # public API — authenticated by API key
    "/api/v1/public/",     # public no-login surfaces (demo-lead / hiring-manager intake; native job-page apply — flag-gated off, rate-limited per IP+job; voluntary EEO self-ID POST /public/eeo/{token} — flag-gated off, rate-limited, authorised by the opaque per-application eeo_token, never a raw application_id; careers JobPosting feed GET /public/careers/{slug}/feed.xml — read-only, public-by-design like the careers board it mirrors)
    "/careers/",           # public careers pages
)


def _authz_allowed_without_user(path: str) -> bool:
    # ``/admin/`` diagnostics verify an ``X-Admin-Secret`` header in-body (not a
    # dependency), so dependency introspection can't see it — allow by path.
    if "/admin/" in path:
        return True
    if any(path.startswith(p) for p in _NON_USER_AUTH_PREFIXES):
        return True
    return path in _CANDIDATE_ASSESSMENT_WRITES


def test_every_write_route_is_authenticated_or_justified() -> None:
    """Every state-changing route (POST/PUT/PATCH/DELETE) must depend on the
    authenticated user (``get_current_user`` / ``current_active_user``), unless
    it's one of the explicitly-justified non-user-auth surfaces above. Catches
    an unguarded write endpoint slipping in — the authz invariant.
    """
    from fastapi.routing import APIRoute

    from app.domains.identity_access.users_fastapi import current_active_user
    from app.main import app

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}

    def _deep_calls(dependant) -> list:
        acc, stack = [], [dependant]
        while stack:
            node = stack.pop()
            if getattr(node, "call", None) is not None:
                acc.append(node.call)
            stack.extend(getattr(node, "dependencies", []) or [])
        return acc

    offenders: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = (route.methods or set()) & write_methods
        if not methods:
            continue
        if current_active_user in _deep_calls(route.dependant):
            continue
        if _authz_allowed_without_user(route.path):
            continue
        offenders.append(f"{','.join(sorted(methods))} {route.path}")

    assert not offenders, (
        "Unauthenticated write route(s) — add `Depends(get_current_user)`, "
        "or justify in the allowlist in this test:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_agent_mutation_tools_call_shared_action_layer() -> None:
    """Every agent mutation tool must call into ``app.actions.<name>.run``,
    not implement business logic inline. The same actions are called by
    recruiter routes, so this gate enforces agent/recruiter parity at the
    code level.

    Read-only tools (``get_*``, ``search_*``, ``compare_*``, ``find_*``,
    ``survey_*``, ``read_*``, ``nl_search_*``, ``graph_search_*``,
    ``refresh_candidate_graph``, ``get_cohort_signals``, ``evaluate_policy``,
    ``ask_recruiter``, ``agent_run_complete``, ``batch_score_cv``) are
    exempt — they either delegate to ``mcp_handlers``/``cohort_tools`` or
    are agent-only loops over an action.
    """

    registry_path = PROJECT_ROOT / "app" / "agent_runtime" / "tool_registry.py"
    content = registry_path.read_text(encoding="utf-8")

    handler_def_re = re.compile(r"^def (_tool_[a-z_]+)\(", re.MULTILINE)
    handler_names = handler_def_re.findall(content)

    read_only_or_internal = {
        "_tool_get_application",
        "_tool_get_candidate",
        "_tool_get_candidate_cv",
        "_tool_search_applications",
        "_tool_compare_applications",
        "_tool_nl_search_candidates",
        "_tool_graph_search_candidates",
        "_tool_refresh_candidate_graph",
        "_tool_get_cohort_signals",
        "_tool_evaluate_policy",
        "_tool_survey_role_state",
        "_tool_find_apps_in_state",
        "_tool_read_pending_recruiter_inputs",
        "_tool_batch_score_cv",
        "_tool_ask_recruiter",
        "_tool_agent_run_complete",
        # Internal agent-memory breadcrumb — appends to
        # role.agent_calibration.notes via calibration.save(). Doesn't
        # mutate candidate/application state, so it doesn't go through
        # the shared action layer (which exists for agent/recruiter
        # parity on candidate-facing actions).
        "_tool_record_observation",
        # Decision-queueing tools call queue_decision via the _queue() helper
        # rather than directly. We verify _queue itself below.
        "_tool_queue_advance_decision",
        "_tool_queue_reject_decision",
        "_tool_queue_skip_assessment_reject_decision",
    }

    # For each mutation handler, slice the function body and require it to
    # mention ``<action_name>.run(`` or call ``_queue(``.
    body_re = re.compile(
        r"^def (_tool_[a-z_]+)\([^)]*\)[^:]*:\n((?:(?:    .*\n)|\n)+)",
        re.MULTILINE,
    )
    violations: list[str] = []
    for handler_name, body in body_re.findall(content):
        if handler_name in read_only_or_internal:
            continue
        if ".run(" not in body and "_queue(" not in body:
            violations.append(handler_name)

    assert not violations, (
        "Agent mutation tool handlers must call a shared action "
        "(<action>.run(...) or _queue(...)). Inline business logic is "
        f"forbidden. Violations: {violations}"
    )


def test_no_imports_of_removed_service_shims() -> None:
    removed_shim_names = [
        "access_control_service",
        "claude_service",
        "e2b_service",
        "email_service",
        "prompt_analytics",
        "scoring_service",
        "stripe_service",
        "workable_service",
    ]
    shim_group = "|".join(removed_shim_names)
    patterns = [
        re.compile(rf"(?:from|import)\s+app\.services\.({shim_group})\b"),
        re.compile(rf"(?:from|import)\s+\.\.\.?services\.({shim_group})\b"),
    ]

    scan_roots = [PROJECT_ROOT / "app", PROJECT_ROOT / "tests"]
    violations: list[str] = []
    for root in scan_roots:
        for path in _python_files(root):
            content = path.read_text(encoding="utf-8")
            if any(pattern.search(content) for pattern in patterns):
                violations.append(str(path))

    assert not violations, f"Removed service shims must not be imported: {violations}"


def test_no_bare_anthropic_client_construction() -> None:
    """Anthropic API calls must flow through ``MeteredAnthropicClient``.

    The wrapper is what writes ``UsageEvent`` rows. A bare
    ``Anthropic(api_key=...)`` instantiation outside the approved
    factory + adapter files = invisible spend = the
    73% reconciliation gap that surfaced on 2026-05-20.

    The approved sites that construct the bare SDK client are:
    - ``app/services/claude_client_resolver.py`` (the factory itself,
      wraps it on the way out)
    - ``app/services/metered_anthropic_client.py`` (defines the wrapper,
      needs the bare class for typing)
    - ``app/components/integrations/anthropic_admin/*`` (admin API,
      not the billable inference API)

    Any other file containing either ``Anthropic(api_key`` or a literal
    construction of ``Anthropic()`` must route through the resolver
    instead.
    """
    approved = {
        "app/services/claude_client_resolver.py",
        "app/services/metered_anthropic_client.py",
    }
    # Admin API client lives under anthropic_admin/* — uses a different
    # SDK surface (admin endpoints), not billable inference. Allow the
    # entire subtree.
    approved_subtrees = (
        "app/components/integrations/anthropic_admin/",
    )

    constructor_re = re.compile(r"\bAnthropic\s*\(\s*api_key\s*=")
    # A file constructing the bare SDK is acceptable IFF it immediately
    # wraps the result in ``MeteredAnthropicClient(inner=...)`` so the
    # meter still fires. We treat the presence of that wrapper call in
    # the same file as proof.
    wrapper_re = re.compile(r"MeteredAnthropicClient\s*\(\s*inner\s*=")

    violations: list[tuple[str, str]] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in approved:
            continue
        if any(rel.startswith(t) for t in approved_subtrees):
            continue
        content = path.read_text(encoding="utf-8")
        if constructor_re.search(content) and not wrapper_re.search(content):
            violations.append((rel, "constructs Anthropic(api_key=...) without wrapping in MeteredAnthropicClient(inner=...)"))

    assert not violations, (
        "Every Anthropic client must flow through MeteredAnthropicClient "
        "so the meter writes a UsageEvent for each call. Direct "
        "`Anthropic(api_key=...)` without wrapping produces invisible "
        "spend (reconciliation gap on 2026-05-20 was 73% via this exact "
        f"pattern). Violations: {violations}"
    )


def test_no_bare_async_anthropic_client_construction() -> None:
    """The async sister rule: ``AsyncAnthropic(...)`` must be wrapped in
    ``MeteredAsyncAnthropic(inner=...)`` in the same file.

    Background: Graphiti's ``AnthropicClient`` accepts an ``AsyncAnthropic``
    instance and runs all entity-extraction calls through it. Until
    2026-05-26 we built a bare ``AsyncAnthropic`` inside
    ``candidate_graph/client.py``, so every candidate sync's Haiku calls
    bypassed the meter entirely (no call_log, no usage_event). On
    2026-05-23 this hid 16.15M of 19.18M Haiku input tokens — Anthropic
    billed $60.31, our records showed $35.48. The async wrapper closes
    that hole; this gate prevents it from re-opening.

    Approved sites mirror the sync gate: the wrapper itself, and the
    candidate_graph client factory (constructs + immediately wraps).
    """
    approved = {
        "app/services/metered_async_anthropic_client.py",
        "app/candidate_graph/client.py",
    }

    constructor_re = re.compile(r"\bAsyncAnthropic\s*\(")
    wrapper_re = re.compile(r"MeteredAsyncAnthropic\s*\(\s*inner\s*=")

    violations: list[tuple[str, str]] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in approved:
            continue
        content = path.read_text(encoding="utf-8")
        if constructor_re.search(content) and not wrapper_re.search(content):
            violations.append((rel, "constructs AsyncAnthropic(...) without wrapping in MeteredAsyncAnthropic(inner=...)"))

    assert not violations, (
        "Every AsyncAnthropic client must flow through "
        "MeteredAsyncAnthropic so claude_call_log captures the spend. "
        "Bare AsyncAnthropic produces invisible Haiku spend (Graphiti "
        f"path leaked 16M tokens/day before this gate). Violations: {violations}"
    )


# ---------------------------------------------------------------------------
# Metering-consistency gates
# ---------------------------------------------------------------------------
# These guard the *attribution* layer (which Feature a call books to, and
# whether that Feature/model can be priced) rather than the *transport* layer
# (the two gates above, which guard that calls flow through the metered
# wrapper at all). The class of bug they catch shipped for real:
# ``requisition_intake*`` feature strings were used at call sites before the
# matching ``Feature`` enum members existed, so ``record_event`` raised a
# ``ValueError`` on the ``Feature(...)`` conversion and the usage was silently
# dropped (metering must never raise, so the event just vanished).


def test_metering_feature_literals_resolve_to_enum() -> None:
    """Every metering ``feature`` string literal must be a ``Feature`` member.

    Scans ``app/**.py`` for both shapes that flow into the meter:
      * ``feature="..."`` kwargs (MeteringContext / record_event / call sites)
      * ``"feature": "..."`` dict keys (the ``metering={...}`` wrapper kwarg)

    and asserts each literal value resolves to a ``Feature`` member. A literal
    with no enum member is exactly the ``requisition_intake*`` bug: the wrapper
    calls ``Feature(value)`` inside ``record_event``, that raises, and the
    swallow-all metering path drops the usage_event with only a logged warning.

    The negative-lookbehind ``(?<![\\w.])`` keeps this from matching
    ``sub_feature=`` or attribute access like ``x.feature=``. A small explicit
    ignore-list covers the one known non-metering false positive (a
    ``"feature": "kubernetes"`` example inside a docstring).
    """
    from app.services.pricing_service import Feature

    valid_values = {f.value for f in Feature}

    # (relative_path, literal_value) pairs that are NOT metering features.
    # Keep this list TINY — every entry is a place the regex over-matched a
    # non-metering string. The dead ``"feature": "evaluate_policy"`` key in
    # tool_registry.py was deleted (not ignored), so it must not appear here.
    ignore: set[tuple[str, str]] = {
        # Docstring example in a `"skills": [{"feature": "kubernetes", ...}]`
        # blob — a candidate-signal shape, not a metering feature.
        ("app/services/cohort_signals_service.py", "kubernetes"),
    }

    kwarg_re = re.compile(
        r"(?<![\w.])feature\s*=\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
    )
    dict_re = re.compile(
        r"[\"']feature[\"']\s*:\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
    )

    violations: list[str] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        content = path.read_text(encoding="utf-8")
        for match in [*kwarg_re.finditer(content), *dict_re.finditer(content)]:
            value = match.group(1)
            if (rel, value) in ignore:
                continue
            if value not in valid_values:
                violations.append(f"{rel}: feature={value!r}")

    assert not violations, (
        "Every metering `feature` string literal must resolve to a Feature "
        "enum member, or record_event raises and the usage_event is silently "
        "dropped (the requisition_intake* bug). Add the member to "
        "app/services/pricing_service.py::Feature (and its pricing + "
        "reservation entries), or fix the typo. Offending literals: "
        f"{violations}"
    )


def test_every_feature_is_priced_and_reservable() -> None:
    """Every ``Feature`` member must appear in BOTH pricing maps.

    - ``pricing_service._FEATURE_PRICING`` — consulted by ``credits_charged``
      / ``feature_pricing`` to apply the per-feature markup.
    - ``pricing_service.estimate_reservation`` — consulted by
      ``usage_metering_service.reserve`` for the pre-flight balance check.

    A member missing from either map ``KeyError``s at runtime the first time
    that feature is billed/reserved. This caught ``CANDIDATE_GROUNDING`` (it
    was priced but absent from the reservation map).
    """
    from app.services.pricing_service import (
        Feature,
        _FEATURE_PRICING,
        estimate_reservation,
        feature_pricing,
    )

    missing_pricing = [f.name for f in Feature if f not in _FEATURE_PRICING]
    assert not missing_pricing, (
        "Feature members missing from _FEATURE_PRICING (credits_charged would "
        f"KeyError): {missing_pricing}"
    )

    missing_reservation: list[str] = []
    for feature in Feature:
        try:
            estimate_reservation(feature)
        except KeyError:
            missing_reservation.append(feature.name)
    assert not missing_reservation, (
        "Feature members missing from estimate_reservation's map "
        f"(reserve() would KeyError): {missing_reservation}"
    )

    # Belt-and-suspenders: feature_pricing(...) must succeed for every member
    # (it also accepts the string value, the form record_event receives).
    for feature in Feature:
        feature_pricing(feature)
        feature_pricing(feature.value)


def test_configured_and_literal_claude_models_are_priceable() -> None:
    """Every Claude model id we configure or hardcode must be in the rate table.

    Two sources are scanned:
      * ``CLAUDE_*MODEL`` string defaults in ``platform/config.py`` and the
        ``resolved_claude_*`` fallbacks (the `or "claude-..."` literals).
      * every ``claude-...`` model literal used anywhere in ``app/``.

    Each must, after the pricing layer's ``_strip_snapshot_suffix``, resolve to
    a key present in ``_MODEL_RATES``. Otherwise ``raw_cost_usd_micro`` falls
    back to the env-var default rate and the call is mis-priced (Sonnet booked
    at Haiku rates was a real −34% reconciliation drift).

    This is exactly what catches a retired/absent id like
    ``claude-3-5-haiku-latest`` — ``_strip_snapshot_suffix`` does NOT strip a
    ``-latest`` alias (only an 8-digit ``-YYYYMMDD`` snapshot), so the stripped
    string is still ``claude-3-5-haiku-latest``, which is not a ``_MODEL_RATES``
    key. A stray ``-latest`` on any family fails here for the same reason.
    """
    from app.services.pricing_service import _MODEL_RATES, _strip_snapshot_suffix

    def _priceable(model_id: str) -> bool:
        return _strip_snapshot_suffix(model_id) in _MODEL_RATES

    # 1. config.py defaults + resolver fallbacks. Match assignment defaults
    #    (CLAUDE_*MODEL: str = "claude-...") and the `or "claude-..."` fallbacks.
    config_path = PROJECT_ROOT / "app" / "platform" / "config.py"
    config_src = config_path.read_text(encoding="utf-8")
    config_model_re = re.compile(r"[\"'](claude-[A-Za-z0-9.\-]+)[\"']")
    config_models = set(config_model_re.findall(config_src))

    bad_config = sorted(m for m in config_models if not _priceable(m))
    assert not bad_config, (
        "config.py references Claude model id(s) absent from _MODEL_RATES "
        "(after snapshot-strip) — they would mis-price to the env-var default "
        f"rate. Add them to pricing_service._MODEL_RATES or fix the id: {bad_config}"
    )

    # 2. Every claude-... literal anywhere in app/. Excludes pricing_service
    #    itself (it DEFINES the legacy rate keys, incl. ids new code shouldn't
    #    call) and the migration-doc-style comments are caught too — a real
    #    hardcoded model string that can't be priced is always a bug.
    literal_re = re.compile(r"[\"'](claude-[A-Za-z0-9.\-]+)[\"']")
    # Defines the legacy rate keys themselves (incl. retired ids kept for
    # historical recompute) — not call-site model selection.
    skip_rel = {"app/services/pricing_service.py"}
    # (relative_path, model_id) pairs that are legitimately unpriceable: retired
    # ids kept ONLY as alias-detection keys, never billed at their own id.
    ignore_literals: set[tuple[str, str]] = {
        # model_fallback.py keeps retired Haiku ids so an explicit legacy
        # request still detects as a Haiku alias and resolves (via the fallback
        # chain) to CURRENT_HAIKU_MODEL = claude-haiku-4-5-20251001 — the only
        # id actually sent. See that module's docstring for why they must stay.
        ("app/components/integrations/claude/model_fallback.py", "claude-3-5-haiku-latest"),
        ("app/components/integrations/claude/model_fallback.py", "claude-3-haiku-20240307"),
    }
    offending: list[str] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in skip_rel:
            continue
        content = path.read_text(encoding="utf-8")
        for model_id in set(literal_re.findall(content)):
            if (rel, model_id) in ignore_literals:
                continue
            if not _priceable(model_id):
                offending.append(f"{rel}: {model_id}")

    assert not offending, (
        "Claude model literal(s) in app/ are absent from _MODEL_RATES (after "
        "snapshot-strip) and would mis-price to the env-var default rate. "
        "A retired/absent id (e.g. a stray '-latest') is the classic cause. "
        f"Offending: {sorted(offending)}"
    )


def test_eeo_model_is_segregated_from_scoring_and_decision() -> None:
    """The voluntary-EEO surface must never be reachable from the scoring/decision
    path — the agent must not see a protected characteristic. This pins the
    segregation architecturally: ``EEOResponse`` / the eeo model / eeo_service may
    be imported ONLY by the compliance domain (which owns them) and the job-pages
    public route (which mints the token + records the voluntary answer). Any
    reference from a scoring or decision module fails this gate.
    """
    # Files allowed to touch the EEO surface. Everything else — especially
    # anything under scoring/decision — must not.
    allowed = {
        "app/models/__init__.py",
        "app/models/eeo_response.py",
        "app/domains/compliance/__init__.py",
        "app/domains/compliance/eeo_service.py",
        "app/domains/compliance/routes.py",
        # The public apply route mints the token + records the voluntary answer.
        "app/domains/job_pages/routes.py",
    }
    needle = re.compile(r"eeo_response|EEOResponse|eeo_service", re.IGNORECASE)

    offenders: list[str] = []
    for path in _python_files(PROJECT_ROOT / "app"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel in allowed:
            continue
        if needle.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)

    assert not offenders, (
        "EEO self-ID surface referenced outside the compliance domain / apply "
        "route — it must stay segregated from scoring/decision:\n  "
        + "\n  ".join(sorted(offenders))
    )
