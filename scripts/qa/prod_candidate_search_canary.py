#!/usr/bin/env python3
"""Read-only, zero-provider production canary for candidate search.

The canary authenticates to a dedicated synthetic tenant, waits until the API
serves the exact expected revision, and executes one deterministic PostgreSQL-
only search. It never creates, updates, or deletes production data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


CANARY_QUERY = "candidates based in UAE with Python and PostgreSQL"
EXPECTED_SKILLS = ["Python", "PostgreSQL"]
EXPECTED_COUNTRIES = ["United Arab Emirates"]
EXPECTED_EMAIL = "search-canary-hit@example.com"
FIXTURE_TRUTH = {
    EXPECTED_EMAIL: {
        "skills": ["Python", "PostgreSQL"],
        "country": "United Arab Emirates",
        "assessment_status": "completed",
    },
    "search-canary-wrong-skill@example.com": {
        "skills": ["Python"],
        "country": "United Arab Emirates",
        "assessment_status": "completed",
    },
    "search-canary-pending-assessment@example.com": {
        "skills": ["Python", "PostgreSQL"],
        "country": "United Arab Emirates",
        "assessment_status": "pending",
    },
    "search-canary-wrong-location@example.com": {
        "skills": ["Python", "PostgreSQL"],
        "country": "Germany",
        "assessment_status": "completed",
    },
}
EXCLUDED_EMAILS = frozenset(FIXTURE_TRUTH) - {EXPECTED_EMAIL}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class CanaryFailure(RuntimeError):
    """A safe-to-print canary failure that never contains credentials."""


@dataclass(frozen=True)
class CanaryConfig:
    base_url: str
    expected_sha: str
    token: str
    role_id: int
    wait_seconds: int = 900
    poll_seconds: int = 10
    request_timeout_seconds: int = 15


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise CanaryFailure(f"required environment variable is missing: {name}")
    return value


def _normalise_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise CanaryFailure("TALI_PROD_URL must be an HTTPS origin without a path")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise CanaryFailure("TALI_PROD_URL must not contain credentials, query, or fragment")
    return f"https://{parsed.netloc}"


def _config_from_env(expected_sha: str, wait_seconds: int) -> CanaryConfig:
    normalized_sha = expected_sha.strip().lower()
    if not SHA_RE.fullmatch(normalized_sha):
        raise CanaryFailure("expected release SHA must be a full 40-character Git SHA")
    try:
        role_id = int(_required_env("TALI_SEARCH_CANARY_ROLE_ID"))
    except ValueError as exc:
        raise CanaryFailure("TALI_SEARCH_CANARY_ROLE_ID must be a positive integer") from exc
    if role_id <= 0:
        raise CanaryFailure("TALI_SEARCH_CANARY_ROLE_ID must be a positive integer")

    return CanaryConfig(
        base_url=_normalise_base_url(_required_env("TALI_PROD_URL")),
        expected_sha=normalized_sha,
        token=_required_env("TALI_SEARCH_CANARY_TOKEN"),
        role_id=role_id,
        wait_seconds=max(1, int(wait_seconds)),
    )


class _RejectRedirects(HTTPRedirectHandler):
    """Never forward the bearer token to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_RejectRedirects())


def _get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int,
) -> tuple[int, Any]:
    """Issue the canary's only allowed request type: a read-only GET."""

    request = Request(url, headers=dict(headers or {}), method="GET")
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read()
    except HTTPError as exc:
        status = int(exc.code)
        try:
            raw = exc.read()
        finally:
            exc.close()
    except (OSError, URLError) as exc:
        raise CanaryFailure("production API request failed") from exc
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanaryFailure(f"production API returned non-JSON data (HTTP {status})") from exc


def _wait_for_exact_release(config: CanaryConfig) -> None:
    deadline = time.monotonic() + config.wait_seconds
    while True:
        try:
            status, payload = _get_json(
                f"{config.base_url}/ready",
                timeout=config.request_timeout_seconds,
            )
        except CanaryFailure:
            status, payload = 0, {}
        actual_sha = (
            payload.get("deployment", {}).get("commit_sha")
            if isinstance(payload, dict)
            else None
        )
        if status == 200 and actual_sha == config.expected_sha:
            return
        if time.monotonic() >= deadline:
            raise CanaryFailure(
                "timed out waiting for a healthy API on the exact expected release SHA"
            )
        time.sleep(config.poll_seconds)


def _search(config: CanaryConfig, token: str) -> dict[str, Any]:
    params = urlencode(
        {
            "role_id": config.role_id,
            "application_outcome": "all",
            # This correlated latest-assessment filter covers the PostgreSQL
            # composition seam that previously failed in production.
            "assessment_status": "completed",
            "nl_query": CANARY_QUERY,
            "view": "list",
            "rerank": "false",
            "provider_mode": "forbid",
            "include_stage_counts": "false",
            "include_cv_text": "false",
            "limit": 50,
            "offset": 0,
        }
    )
    status, payload = _get_json(
        f"{config.base_url}/api/v1/applications?{params}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=config.request_timeout_seconds,
    )
    if status != 200 or not isinstance(payload, dict):
        raise CanaryFailure(f"candidate search failed (HTTP {status})")
    return payload


def _inventory(config: CanaryConfig, token: str) -> dict[str, Any]:
    params = urlencode(
        {
            "role_id": config.role_id,
            "application_outcome": "all",
            "view": "list",
            "rerank": "false",
            "provider_mode": "forbid",
            "include_stage_counts": "false",
            "include_cv_text": "false",
            "limit": 50,
            "offset": 0,
        }
    )
    status, payload = _get_json(
        f"{config.base_url}/api/v1/applications?{params}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=config.request_timeout_seconds,
    )
    if status != 200 or not isinstance(payload, dict):
        raise CanaryFailure(f"canary fixture inventory failed (HTTP {status})")
    return payload


def _assert_inventory(config: CanaryConfig, payload: dict[str, Any]) -> None:
    if payload.get("deployment_sha") != config.expected_sha:
        raise CanaryFailure("canary inventory came from a different release SHA")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != len(FIXTURE_TRUTH):
        raise CanaryFailure("canary inventory fixture count did not match truth")
    if payload.get("total") != len(FIXTURE_TRUTH):
        raise CanaryFailure("canary inventory total did not match truth")

    by_email: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise CanaryFailure("canary inventory contained a malformed item")
        email = str(item.get("candidate_email") or "").casefold()
        if not email or email in by_email:
            raise CanaryFailure("canary inventory contained a duplicate candidate")
        by_email[email] = item
    if set(by_email) != set(FIXTURE_TRUTH):
        raise CanaryFailure("canary inventory email universe did not match truth")

    for email, truth in FIXTURE_TRUTH.items():
        item = by_email[email]
        score_summary = item.get("score_summary")
        assessment_status = (
            score_summary.get("assessment_status")
            if isinstance(score_summary, dict)
            else None
        )
        if (
            item.get("candidate_skills") != truth["skills"]
            or item.get("candidate_location") != truth["country"]
            or assessment_status != truth["assessment_status"]
            or item.get("application_outcome") != "open"
            or item.get("source") != "manual"
            or item.get("role_id") != config.role_id
            or item.get("external_refs") != {"internal_canary": "search-v1"}
            or not isinstance(item.get("id"), int)
            or not isinstance(item.get("candidate_id"), int)
        ):
            raise CanaryFailure("canary inventory candidate fields did not match truth")


def _assert_truth(
    config: CanaryConfig,
    payload: dict[str, Any],
    *,
    inventory_payload: dict[str, Any] | None = None,
) -> None:
    if payload.get("deployment_sha") != config.expected_sha:
        raise CanaryFailure("candidate search response came from a different release SHA")
    if payload.get("nl_provider_mode") != "forbid":
        raise CanaryFailure("candidate search did not honor provider-forbidden execution")

    parsed = payload.get("parsed_filter") or {}
    expected_empty_fields = (
        "skills_any",
        "titles_all",
        "titles_any",
        "locations_region",
        "graph_predicates",
        "soft_criteria",
        "preferred_criteria",
        "keywords",
    )
    if (
        parsed.get("skills_all") != EXPECTED_SKILLS
        or parsed.get("locations_country") != EXPECTED_COUNTRIES
        or parsed.get("min_years_experience") is not None
        or parsed.get("graph_predicate_operator") != "all"
        or parsed.get("free_text") != CANARY_QUERY
        or parsed.get("parse_degraded") is not False
        or any(parsed.get(field) for field in expected_empty_fields)
    ):
        raise CanaryFailure("candidate search parser output did not match grounded truth")

    items = payload.get("items")
    if not isinstance(items, list):
        raise CanaryFailure("candidate search response is missing result items")
    if len(items) != 1 or not isinstance(items[0], dict):
        raise CanaryFailure("candidate search inclusion truth did not match exactly")
    actual_emails = {
        str(item.get("candidate_email") or "").casefold()
        for item in items
        if isinstance(item, dict)
    }
    if actual_emails != {EXPECTED_EMAIL} or payload.get("total") != 1:
        raise CanaryFailure("candidate search inclusion truth did not match exactly")
    if actual_emails & EXCLUDED_EMAILS:
        raise CanaryFailure("candidate search returned a grounded exclusion candidate")

    if payload.get("nl_warnings") != []:
        raise CanaryFailure("candidate search returned a degraded warning")
    if payload.get("nl_rerank_applied") is not False:
        raise CanaryFailure("candidate search unexpectedly applied model reranking")
    if payload.get("nl_verification") != []:
        raise CanaryFailure("candidate search unexpectedly ran deep verification")

    coverage = payload.get("nl_coverage") or {}
    expected_coverage = {
        "database_matches": 1,
        "retrieval_matches": 1,
        "deep_checked": 0,
        "evidence_succeeded": 0,
        "evidence_failed": 0,
        "qualified": None,
        "capped": False,
        "exhaustive": True,
        "is_exact_empty": False,
        "filtered_matches": 1,
    }
    if coverage != expected_coverage:
        raise CanaryFailure("candidate search coverage did not match grounded truth")

    retrieval = payload.get("nl_retrieval") or {}
    if (
        retrieval.get("mode") != "postgres_only"
        or retrieval.get("graph_status") != "not_selected"
        or retrieval.get("capped") is not False
        or retrieval.get("exhaustive") is not True
        or retrieval.get("is_exact_empty") is not False
        or retrieval.get("total_hits") != 1
        or retrieval.get("filtered_hits") != 1
        or retrieval.get("returned_hits") != 1
    ):
        raise CanaryFailure("candidate search retrieval contract was not PostgreSQL-only")
    hits = retrieval.get("hits")
    result_item = items[0]
    application_id = result_item.get("id")
    candidate_id = result_item.get("candidate_id")
    if (
        not isinstance(application_id, int)
        or isinstance(application_id, bool)
        or not isinstance(candidate_id, int)
        or isinstance(candidate_id, bool)
        or not isinstance(hits, list)
        or len(hits) != 1
        or not isinstance(hits[0], dict)
        or not isinstance(hits[0].get("application_id"), int)
        or isinstance(hits[0].get("application_id"), bool)
        or not isinstance(hits[0].get("candidate_id"), int)
        or isinstance(hits[0].get("candidate_id"), bool)
        or hits[0].get("sources") != ["postgres"]
        or hits[0].get("application_id") != application_id
        or hits[0].get("candidate_id") != candidate_id
    ):
        raise CanaryFailure("candidate search retrieval provenance was not PostgreSQL-only")

    if inventory_payload is not None:
        inventory_items = inventory_payload.get("items")
        expected_inventory = (
            next(
                (
                    item
                    for item in inventory_items
                    if isinstance(item, dict)
                    and str(item.get("candidate_email") or "").casefold()
                    == EXPECTED_EMAIL
                ),
                None,
            )
            if isinstance(inventory_items, list)
            else None
        )
        if (
            not isinstance(expected_inventory, dict)
            or expected_inventory.get("id") != application_id
            or expected_inventory.get("candidate_id") != candidate_id
        ):
            raise CanaryFailure(
                "candidate search identity did not match the inventoried fixture"
            )


def run(config: CanaryConfig) -> None:
    _wait_for_exact_release(config)
    # The dedicated token is minted during one-time fixture provisioning.
    # Avoiding the login endpoint keeps every recurring canary request read-only
    # (login records audit events and lockout state).
    inventory = _inventory(config, config.token)
    _assert_inventory(config, inventory)
    payload = _search(config, config.token)
    _assert_truth(config, payload, inventory_payload=inventory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--wait-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        config = _config_from_env(args.expected_sha, args.wait_seconds)
        run(config)
    except CanaryFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: grounded PostgreSQL candidate search canary ({config.expected_sha})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
