from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .document_service import sanitize_json_for_storage, sanitize_text_for_storage

FIREFLIES_GRAPHQL_URL = "https://api.fireflies.ai/graphql"


def normalize_email(value: Any) -> str | None:
    text = sanitize_text_for_storage(str(value or "").strip()).lower()
    return text or None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def verify_fireflies_webhook_signature(*, payload: bytes, signature: str | None, secret: str | None) -> bool:
    token = str(secret or "").strip()
    header_value = str(signature or "").strip()
    if not token or not header_value:
        return False
    expected = hmac.new(token.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header_value, expected)


class FirefliesService:
    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise ValueError("Fireflies API key is required")

    def _graphql(self, *, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = httpx.post(
            FIREFLIES_GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "variables": variables or {},
            },
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        if payload.get("errors"):
            raise RuntimeError(str(payload["errors"]))
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def get_transcript(self, meeting_id: str) -> dict[str, Any]:
        query = """
        query Transcript($transcriptId: String!) {
          transcript(id: $transcriptId) {
            id
            title
            date
            organizer_email
            host_email
            transcript_url
            duration
            participants
            speakers {
              id
              name
            }
            meeting_attendees {
              email
              name
              displayName
              location
            }
            summary {
              short_summary
              short_overview
              overview
              bullet_gist
              shorthand_bullet
              topics_discussed
            }
            sentences {
              speaker_name
              text
              start_time
              end_time
            }
          }
        }
        """
        data = self._graphql(query=query, variables={"transcriptId": meeting_id})
        transcript = data.get("transcript")
        return transcript if isinstance(transcript, dict) else {}

    def search_transcripts(
        self,
        *,
        candidate_email: str | None = None,
        owner_email: str | None = None,
        meeting_date: datetime | None = None,
        window_hours: int = 48,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        from_date = None
        to_date = None
        if meeting_date is not None:
            start = meeting_date - timedelta(hours=window_hours)
            end = meeting_date + timedelta(hours=window_hours)
            from_date = start.astimezone(timezone.utc).isoformat()
            to_date = end.astimezone(timezone.utc).isoformat()
        query = """
        query Transcripts(
          $limit: Int
          $fromDate: DateTime
          $toDate: DateTime
          $organizers: [String]
          $participants: [String]
        ) {
          transcripts(
            limit: $limit
            fromDate: $fromDate
            toDate: $toDate
            organizers: $organizers
            participants: $participants
          ) {
            id
            title
            date
            organizer_email
            host_email
            participants
            transcript_url
            summary {
              short_summary
              overview
              bullet_gist
            }
            speakers {
              id
              name
            }
          }
        }
        """
        variables = {
            "limit": max(1, min(int(limit or 10), 50)),
            "fromDate": from_date,
            "toDate": to_date,
            "organizers": [owner_email] if owner_email else None,
            "participants": [candidate_email] if candidate_email else None,
        }
        data = self._graphql(query=query, variables=variables)
        transcripts = data.get("transcripts")
        if not isinstance(transcripts, list):
            return []
        return [item for item in transcripts if isinstance(item, dict)]


def normalized_transcript_bundle(transcript: dict[str, Any]) -> dict[str, Any]:
    summary = transcript.get("summary") if isinstance(transcript.get("summary"), dict) else {}
    sentences = transcript.get("sentences") if isinstance(transcript.get("sentences"), list) else []
    transcript_lines: list[str] = []
    for item in sentences:
        if not isinstance(item, dict):
            continue
        text = sanitize_text_for_storage(str(item.get("text") or "").strip())
        if not text:
            continue
        speaker = sanitize_text_for_storage(str(item.get("speaker_name") or "").strip())
        transcript_lines.append(f"{speaker}: {text}" if speaker else text)
    speakers = transcript.get("speakers") if isinstance(transcript.get("speakers"), list) else []
    return {
        "provider": "fireflies",
        "provider_meeting_id": sanitize_text_for_storage(str(transcript.get("id") or "").strip()) or None,
        "provider_url": sanitize_text_for_storage(str(transcript.get("transcript_url") or "").strip()) or None,
        "meeting_date": _parse_dt(transcript.get("date")),
        "summary": sanitize_text_for_storage(
            str(
                summary.get("short_summary")
                or summary.get("short_overview")
                or summary.get("overview")
                or summary.get("bullet_gist")
                or ""
            ).strip()
        ) or None,
        "transcript_text": "\n".join(transcript_lines).strip() or None,
        "speakers": [
            {
                "id": sanitize_text_for_storage(str(item.get("id") or "").strip()) or None,
                "name": sanitize_text_for_storage(str(item.get("name") or "").strip()) or None,
            }
            for item in speakers
            if isinstance(item, dict)
        ],
        "organizer_email": normalize_email(transcript.get("organizer_email")),
        "host_email": normalize_email(transcript.get("host_email")),
        "participants": sanitize_json_for_storage(
            transcript.get("participants") if isinstance(transcript.get("participants"), list) else []
        ),
        "raw": sanitize_json_for_storage(transcript),
    }


def attach_fireflies_match_metadata(
    raw_payload: dict[str, Any] | None,
    *,
    invite_email: str | None = None,
    linked_via: str | None = None,
    matched_application_id: int | None = None,
    linked_by_user_id: int | None = None,
) -> dict[str, Any]:
    payload = sanitize_json_for_storage(raw_payload if isinstance(raw_payload, dict) else {})
    match_metadata: dict[str, Any] = {}
    normalized_invite_email = normalize_email(invite_email)
    if normalized_invite_email:
        match_metadata["fireflies_invite_email"] = normalized_invite_email
    linked_via_text = sanitize_text_for_storage(str(linked_via or "").strip())
    if linked_via_text:
        match_metadata["linked_via"] = linked_via_text
    if matched_application_id is not None:
        try:
            match_metadata["matched_application_id"] = int(matched_application_id)
        except (TypeError, ValueError):
            pass
    if linked_by_user_id is not None:
        try:
            match_metadata["linked_by_user_id"] = int(linked_by_user_id)
        except (TypeError, ValueError):
            pass
    if match_metadata:
        payload["taali_match"] = sanitize_json_for_storage(match_metadata)
    return payload
