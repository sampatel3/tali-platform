"""Bounded identity for the exact ordered Graphiti payload of one operation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable

from ..models.graph_ingest_dispatch import GRAPH_INGEST_WORK_KINDS


MANIFEST_VERSION = 1
MAX_MANIFEST_EPISODES = 100
MAX_EPISODE_NAME_BYTES = 512
MAX_EPISODE_PAYLOAD_BYTES = 128 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
_MANIFEST_KEYS = frozenset(
    {"version", "work_kind", "entity_id", "episode_count", "episodes"}
)
_EPISODE_KEYS = frozenset({"ordinal", "episode_name", "episode_sha256"})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def manifest_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _bounded_text(
    value: Any,
    *,
    max_bytes: int,
    allow_layout_controls: bool = False,
) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("graph operation manifest text is missing")
    allowed_controls = {"\n", "\r", "\t"} if allow_layout_controls else set()
    if any(
        (ord(character) < 32 and character not in allowed_controls)
        or ord(character) == 127
        for character in value
    ):
        raise ValueError("graph operation manifest text contains control characters")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError("graph operation manifest text exceeds its byte limit")
    return value


def _reference_time(value: Any) -> str:
    if not isinstance(value, datetime):
        raise ValueError("graph operation reference time is malformed")
    return value.isoformat()


def build_operation_manifest(
    *,
    work_kind: str,
    entity_id: int,
    episodes: Iterable[Any],
) -> tuple[dict[str, Any], str]:
    """Hash every provider-visible episode field without storing its body."""

    kind = str(work_kind)
    if kind not in GRAPH_INGEST_WORK_KINDS:
        raise ValueError("graph operation work kind is unsupported")
    entity = int(entity_id)
    episode_list = list(episodes)
    if len(episode_list) > MAX_MANIFEST_EPISODES:
        raise ValueError("graph operation contains too many episodes")
    identities: list[dict[str, Any]] = []
    for ordinal, episode in enumerate(episode_list):
        name = _bounded_text(
            getattr(episode, "name", None),
            max_bytes=MAX_EPISODE_NAME_BYTES,
        )
        body = _bounded_text(
            getattr(episode, "body", None),
            max_bytes=MAX_EPISODE_PAYLOAD_BYTES,
            allow_layout_controls=True,
        )
        source_description = _bounded_text(
            getattr(episode, "source_description", None),
            max_bytes=MAX_EPISODE_NAME_BYTES,
        )
        group_id = _bounded_text(
            getattr(episode, "group_id", None),
            max_bytes=MAX_EPISODE_NAME_BYTES,
        )
        exact_payload = {
            "name": name,
            "episode_body": body,
            "source": "text",
            "source_description": source_description,
            "reference_time": _reference_time(
                getattr(episode, "reference_time", None)
            ),
            "group_id": group_id,
        }
        identities.append(
            {
                "ordinal": ordinal,
                "episode_name": name,
                "episode_sha256": manifest_sha256(exact_payload),
            }
        )
    manifest = {
        "version": MANIFEST_VERSION,
        "work_kind": kind,
        "entity_id": entity,
        "episode_count": len(identities),
        "episodes": identities,
    }
    if len(_canonical_bytes(manifest)) > MAX_MANIFEST_BYTES:
        raise ValueError("graph operation manifest exceeds its byte limit")
    return manifest, manifest_sha256(manifest)


def validate_operation_manifest(
    value: Any,
    expected_sha256: Any,
    *,
    work_kind: str,
    entity_id: int,
) -> dict[str, Any]:
    """Return a detached validated manifest or raise without mutating evidence."""

    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise ValueError("graph operation manifest is missing or malformed")
    if (
        value.get("version") != MANIFEST_VERSION
        or value.get("work_kind") != str(work_kind)
        or isinstance(value.get("entity_id"), bool)
        or not isinstance(value.get("entity_id"), int)
        or int(value["entity_id"]) != int(entity_id)
    ):
        raise ValueError("graph operation manifest authority is malformed")
    episodes = value.get("episodes")
    count = value.get("episode_count")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or count > MAX_MANIFEST_EPISODES
        or not isinstance(episodes, list)
        or len(episodes) != count
    ):
        raise ValueError("graph operation manifest episode count is malformed")
    for ordinal, episode in enumerate(episodes):
        if not isinstance(episode, dict) or set(episode) != _EPISODE_KEYS:
            raise ValueError("graph operation manifest episode is malformed")
        name = _bounded_text(
            episode.get("episode_name"),
            max_bytes=MAX_EPISODE_NAME_BYTES,
        )
        digest = episode.get("episode_sha256")
        if (
            episode.get("ordinal") != ordinal
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or name != episode.get("episode_name")
        ):
            raise ValueError("graph operation manifest episode identity is malformed")
    if len(_canonical_bytes(value)) > MAX_MANIFEST_BYTES:
        raise ValueError("graph operation manifest exceeds its byte limit")
    actual_sha256 = manifest_sha256(value)
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or actual_sha256 != expected_sha256
    ):
        raise ValueError("graph operation manifest fingerprint does not match")
    # A JSON round trip is a bounded detached copy and proves the stored shape
    # remains plain JSON rather than an ORM/mutable extension wrapper.
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def public_operation_manifest(value: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "operation_manifest_sha256": digest,
        "operation_episode_count": int(value["episode_count"]),
        "operation_episodes": [
            {
                "ordinal": int(item["ordinal"]),
                "episode_name": str(item["episode_name"]),
                "episode_sha256": str(item["episode_sha256"]),
            }
            for item in value["episodes"]
        ],
    }


__all__ = [
    "build_operation_manifest",
    "manifest_sha256",
    "public_operation_manifest",
    "validate_operation_manifest",
]
