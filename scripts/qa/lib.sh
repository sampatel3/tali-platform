#!/usr/bin/env bash

# Shared security boundaries for production-account smoke scripts. Callers own
# `set -euo pipefail`, private temporary-directory cleanup, and curl execution.

qa_validate_curl_timeouts() {
  local connect_timeout="$1"
  local total_timeout="$2"

  python3 - "$connect_timeout" "$total_timeout" <<'PY'
import math
import sys

raw_connect, raw_total = sys.argv[1:]
try:
    connect = float(raw_connect)
    total = float(raw_total)
except ValueError:
    connect = total = math.nan

values = (("connect", connect, 60.0), ("total", total, 300.0))
if any(not math.isfinite(value) or value <= 0 or value > maximum for _, value, maximum in values):
    print(
        "error: curl timeouts must be positive finite seconds "
        "(connect <= 60, total <= 300).",
        file=sys.stderr,
    )
    raise SystemExit(2)
if connect > total:
    print(
        "error: curl timeouts require connect <= total.",
        file=sys.stderr,
    )
    raise SystemExit(2)
PY
}


qa_write_auth_header() {
  local auth_json="$1"
  local header_file="$2"

  python3 - "$auth_json" "$header_file" <<'PY'
import json
import os
import sys

auth_json, header_file = sys.argv[1:]
created = False
try:
    with open(auth_json, encoding="utf-8") as handle:
        payload = json.load(handle)
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 16_384
        or "\r" in token
        or "\n" in token
    ):
        raise ValueError("invalid access token")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(header_file, flags, 0o600)
    created = True
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"Authorization: Bearer {token}\n")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
    if created:
        try:
            os.unlink(header_file)
        except FileNotFoundError:
            pass
    print("error: auth response missing or invalid access_token", file=sys.stderr)
    raise SystemExit(1)
PY
}
