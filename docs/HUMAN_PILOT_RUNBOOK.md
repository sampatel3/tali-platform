# Human Pilot Runbook

Date: 2026-03-03 (canonical set refreshed 2026-07-10; earlier revisions said 2, then 5 tasks)

This runbook is for human pilots of the canonical assessment tasks. The
canonical set is exactly the specs in `backend/tasks/*.json` (10 as of
2026-07-10) — that directory is the source of truth, enforced by
`backend/tests/test_task_spec_contract.py`. Role-specific generated drafts
(org-owned, `extra_data.generated`) are additional to this set and carry
their own automated battle-test report (`scripts/battle_test_drafts.py`).

## Goal

Confirm that real candidate sessions behave correctly in production:

- start succeeds
- workspace bootstrap succeeds
- candidate repo opens with the expected context
- submit runs the task-specific test runner
- evaluator-visible results match the intended failure shape

## Preflight

Confirm every catalog spec still satisfies the design contract, and that
prod's active templates match the catalog (`check_two_task_rollout.py` was
removed in the 2026-07 backend de-bloat; use these instead):

```bash
# Contract: every backend/tasks/*.json validates (rubric sums to 1.0,
# interrogation dim ↔ decision_points, jd_to_signal_map coverage, ...)
cd backend && .venv/bin/python -m pytest tests/test_task_spec_contract.py -q

# Run once in a dedicated operator shell before either psql command below.
# libpq reads mode-0600 service/password files, so the URI never appears in
# psql's process arguments. The EXIT trap removes both files with the shell.
set -eu
set +x
umask 077
: "${DATABASE_PUBLIC_URL:?export DATABASE_PUBLIC_URL before this setup}"
case "$DATABASE_PUBLIC_URL" in
  *$'\n'*|*$'\r'*) echo "DATABASE_PUBLIC_URL must not contain newlines" >&2; exit 2 ;;
  postgres://*|postgresql://*) ;;
  *) echo "DATABASE_PUBLIC_URL must be a PostgreSQL URI" >&2; exit 2 ;;
esac
PGSERVICEFILE="$(mktemp "${TMPDIR:-/tmp}/taali-human-pilot-pgservice.XXXXXX")"
pgpass_file="$(mktemp "${TMPDIR:-/tmp}/taali-human-pilot-pgpass.XXXXXX")"
database_uri_file="$(mktemp "${TMPDIR:-/tmp}/taali-human-pilot-uri.XXXXXX")"
export PGSERVICEFILE
trap 'rm -f -- "$PGSERVICEFILE" "$pgpass_file" "$database_uri_file"' EXIT
chmod 600 "$PGSERVICEFILE" "$pgpass_file" "$database_uri_file"
printf '%s' "$DATABASE_PUBLIC_URL" > "$database_uri_file"
unset DATABASE_PUBLIC_URL
python3 - "$database_uri_file" "$PGSERVICEFILE" "$pgpass_file" <<'PY'
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

try:
    parsed = urlsplit(Path(sys.argv[1]).read_text(encoding="utf-8"))
    port = parsed.port or 5432
except ValueError as exc:
    raise SystemExit("DATABASE_PUBLIC_URL is malformed") from exc

database = unquote(parsed.path.removeprefix("/"))
username = unquote(parsed.username or "")
password = unquote(parsed.password or "")
if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
    raise SystemExit("DATABASE_PUBLIC_URL must name a PostgreSQL host")
if not database or not username or not password or parsed.fragment:
    raise SystemExit("DATABASE_PUBLIC_URL must include database, user, and password")

fields = [
    ("host", parsed.hostname),
    ("port", str(port)),
    ("dbname", database),
    ("user", username),
]
reserved = {"service", "servicefile", "host", "port", "dbname", "user", "password", "passfile"}
seen = {key for key, _value in fields}

def uri_decode(value: str) -> str:
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise SystemExit("invalid percent escape in PostgreSQL URI option")
    try:
        return unquote(value, errors="strict")
    except UnicodeDecodeError as exc:
        raise SystemExit("PostgreSQL URI option is not valid UTF-8") from exc

for raw_option in parsed.query.split("&") if parsed.query else ():
    if raw_option.count("=") != 1:
        raise SystemExit("PostgreSQL URI options must use one key=value pair")
    raw_key, raw_value = raw_option.split("=", 1)
    key, value = uri_decode(raw_key), uri_decode(raw_value)
    if key == "ssl" and value == "true":
        key, value = "sslmode", "require"
    if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in reserved or key in seen:
        raise SystemExit(f"unsupported or duplicate PostgreSQL URI option: {key!r}")
    fields.append((key, value))
    seen.add(key)

for key, value in fields:
    if not value or "\n" in value or "\r" in value or value != value.strip():
        raise SystemExit(f"unsafe PostgreSQL service value: {key}")
    if len(f"{key}={value}".encode()) >= 1000:
        raise SystemExit(f"PostgreSQL service value is too long: {key}")

service_file, pass_file = map(Path, sys.argv[2:])
service_file.write_text(
    "[taali-human-pilot]\n"
    + "\n".join(f"{key}={value}" for key, value in fields)
    + f"\npassfile={pass_file}\n",
    encoding="utf-8",
)

def pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")

pass_file.write_text(
    ":".join(
        pgpass_escape(value)
        for value in (parsed.hostname, str(port), database, username, password)
    )
    + "\n",
    encoding="utf-8",
)
PY
: > "$database_uri_file"
rm -f -- "$database_uri_file"
export PGSERVICE=taali-human-pilot

# Prod: active org-less templates == catalog task_keys
psql --no-psqlrc --set=ON_ERROR_STOP=1 -c "SELECT task_key FROM tasks WHERE organization_id IS NULL AND is_active ORDER BY task_key;"
```

This uses libpq's documented
[connection service file](https://www.postgresql.org/docs/current/libpq-pgservice.html);
the setup decodes the standard
[PostgreSQL connection URI](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING)
into explicit service parameters and a private
[password file](https://www.postgresql.org/docs/current/libpq-pgpass.html).
Keep both database checks in the same dedicated shell. Do not put the URI back
on the `psql` command line.

Expected preflight state: the SQL list equals the catalog filenames; legacy
templates (pre-catalog ids) stay `is_active = false` — they anchor historical
assessments and must not be deleted.

## Expected Runtime Shape

For the AI task (`ai_eng_genai_production_readiness`):

- start returns `200`
- bootstrap succeeds
- baseline submit shape is `5 passed / 8 total`

For the data task (`data_eng_aws_glue_pipeline_recovery`):

- start returns `200`
- bootstrap succeeds
- baseline submit shape is `0 passed / 7 total`

For the other eight canonical tasks, baseline shapes still need to be captured
during their first dry-run — record them here when known.

These are the untouched baseline expectations, not the target candidate outcome.

## Pilot Execution

Use a small first batch:

1. Run one human session per canonical task (10 sessions total).
2. Review outcomes before expanding volume on any one task.

During the session, verify manually:

- candidate instructions are clear without recruiter intervention
- no missing dependency or environment errors appear
- the repo contains the expected task files
- the candidate can edit files and run tests normally

## Live Monitoring

Check funnel health (per-status counts + the first-minutes events shipped
2026-07-10: `preview_viewed`, `runtime_loaded`, `file_opened`, `first_prompt`):

```bash
psql --no-psqlrc --set=ON_ERROR_STOP=1 -c "SELECT status, count(*) FROM assessments WHERE created_at > now() - interval '7 days' AND is_voided IS NOT TRUE GROUP BY 1;"
(
  set -eu
  set +x
  umask 077
  admin_secret="${ADMIN_SECRET:?set ADMIN_SECRET before this probe}"
  unset ADMIN_SECRET
  export -n admin_secret
  case "$admin_secret" in *$'\n'*|*$'\r'*) exit 2 ;; esac
  auth_header_file="$(mktemp "${TMPDIR:-/tmp}/taali-github-health-auth.XXXXXX")"
  trap 'rm -f "$auth_header_file"' EXIT
  printf 'X-Admin-Secret: %s\n' "$admin_secret" > "$auth_header_file"
  unset admin_secret
  chmod 600 "$auth_header_file"
  curl --silent --show-error --header "@$auth_header_file" \
    https://resourceful-adaptation-production.up.railway.app/admin/health/github
) # {ok:true} or provisioning is down
```

Pull backend logs if a session looks wrong:

```bash
railway logs --service resourceful-adaptation --environment production --lines 200
```

## Stop Conditions

Pause the pilot immediately if any of these appear:

- prod's active org-less templates stop matching `backend/tasks/*.json` (drop OR unexplained growth)
- any bootstrap failure is recorded
- any completed assessment has `tests_total = 0`
- candidate start fails for any canonical task
- evaluator reports rubric mismatch with the role
- candidates hit missing dependency, missing file, or permission errors

## Notes

- Automated smoke submissions can trigger `suspiciously_fast`; ignore that for scripted checks.
- The task repos now include `.gitignore` entries for `.venv`, `.pytest_cache`, and `__pycache__` so bootstrap artifacts do not pollute candidate branch evidence.
- Dry-run evidence is recorded in:
  - [ai_eng_genai_production_readiness.md](/Users/sampatel/tali-platform/docs/task_dry_runs/ai_eng_genai_production_readiness.md)
  - [data_eng_aws_glue_pipeline_recovery.md](/Users/sampatel/tali-platform/docs/task_dry_runs/data_eng_aws_glue_pipeline_recovery.md)
