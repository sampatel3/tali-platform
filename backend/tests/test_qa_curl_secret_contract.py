"""Credential-boundary contracts for operator curl smoke tooling."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
from urllib.parse import parse_qs

import pytest


ROOT = Path(__file__).resolve().parents[2]
QA_SCRIPTS = (
    ROOT / "scripts" / "qa" / "prod_model_smoke.sh",
    ROOT / "scripts" / "qa" / "prod_candidates_directory_smoke.sh",
    ROOT / "scripts" / "qa" / "prod_account_workable_smoke.sh",
)
QA_LIB = ROOT / "scripts" / "qa" / "lib.sh"
WORKABLE_API_SCRIPT = ROOT / "scripts" / "test_workable_api.sh"
HUMAN_PILOT_RUNBOOK = ROOT / "docs" / "HUMAN_PILOT_RUNBOOK.md"
GRAPHITI_RUNBOOK = ROOT / "docs" / "graphiti-railway-setup.md"
BULLHORN_RUNBOOK = ROOT / "docs" / "BULLHORN_LIVE_VALIDATION_RUNBOOK.md"
ACTIVE_QA_SCRIPTS = (*QA_SCRIPTS, QA_LIB, WORKABLE_API_SCRIPT)
DEPLOYMENT_SCRIPTS = (
    ROOT / "scripts" / "deploy_production.sh",
    *sorted((ROOT / "scripts" / "railway").glob("*.sh")),
    *sorted((ROOT / "scripts" / "release").glob("*.sh")),
)
ACTIVE_OPERATOR_DOCS = (
    ROOT / "docs" / "DEPLOYMENT.md",
    BULLHORN_RUNBOOK,
    ROOT / "docs" / "HUMAN_PILOT_RUNBOOK.md",
    ROOT / "docs" / "WORKABLE_MULTI_AGENT_RUNBOOK.md",
    ROOT / "docs" / "graphiti-railway-setup.md",
)
ACTIVE_SECRET_SOURCES = (
    *ACTIVE_QA_SCRIPTS,
    *DEPLOYMENT_SCRIPTS,
    *ACTIVE_OPERATOR_DOCS,
)

PASSWORD = "sentinel password +&=% must-not-reach-argv"
TOKEN = "sentinel.jwt.must-not-reach-argv"
RESPONSE_TOKEN = "response-token-must-not-be-printed"
MALICIOUS_RESPONSE_TOKEN = "header-safe-prefix\r\nX-Injected: never"


def _write_guarded_passthrough(
    path: Path,
    real_command: str,
    *,
    forbidden_environment: tuple[str, ...],
) -> None:
    names = " ".join(forbidden_environment)
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"for secret_name in {names}; do\n"
        '  if [[ -n "${!secret_name+x}" ]]; then\n'
        '    echo "credential environment reached child command" >&2\n'
        "    exit 97\n"
        "  fi\n"
        "done\n"
        f"exec {shlex.quote(real_command)} \"$@\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _curl_commands(source: str) -> list[str]:
    commands: list[str] = []
    current: list[str] = []
    for line in source.splitlines():
        if not current and not re.search(r"\bcurl\b", line):
            continue
        current.append(line)
        if line.rstrip().endswith("\\"):
            continue
        commands.append("\n".join(current))
        current = []
    if current:
        commands.append("\n".join(current))
    return commands


def test_active_operator_curl_commands_never_expand_credentials_in_argv() -> None:
    offenders: list[str] = []
    for path in ACTIVE_SECRET_SOURCES:
        source = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if "Authorization: Bearer" in line and "$" in line:
                safe_file_write = (
                    stripped.startswith("printf ")
                    and "Authorization: Bearer %s\\n" in stripped
                    and ">" in stripped
                    and not re.search(r"\bcurl\b", stripped)
                )
                if not safe_file_write:
                    offenders.append(f"{path.relative_to(ROOT)}:{line_number}: bearer")
            if re.search(
                r"--data-urlencode\s+['\"]?(?:username|password)=.*\$",
                line,
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{line_number}: form")

        for command in _curl_commands(source):
            if re.search(
                r"(?:-d|--data(?:-raw|-binary)?)\s+.*\$(?:\{)?"
                r"[A-Z_]*(?:PASSWORD|SECRET|TOKEN|JWT|API_KEY)",
                command,
            ):
                first_line = command.splitlines()[0].strip()
                offenders.append(f"{path.relative_to(ROOT)}: payload: {first_line}")

    assert offenders == []


@pytest.mark.parametrize("script", QA_SCRIPTS, ids=lambda path: path.stem)
def test_smoke_scripts_disable_xtrace_before_reading_secrets(script: Path) -> None:
    source = script.read_text(encoding="utf-8")

    assert source.index("set +x") < source.index("TEST_PASSWORD=")


@pytest.mark.parametrize("script", QA_SCRIPTS, ids=lambda path: path.stem)
def test_every_smoke_curl_uses_validated_connect_and_total_timeouts(
    script: Path,
) -> None:
    source = script.read_text(encoding="utf-8")
    helper = QA_LIB.read_text(encoding="utf-8")

    assert 'HTTP_CONNECT_TIMEOUT_SEC="${HTTP_CONNECT_TIMEOUT_SEC:-5}"' in source
    assert 'HTTP_MAX_TIME_SEC="${HTTP_MAX_TIME_SEC:-30}"' in source
    assert 'curl_timeout_args=(' in source
    assert '--connect-timeout "$HTTP_CONNECT_TIMEOUT_SEC"' in source
    assert '--max-time "$HTTP_MAX_TIME_SEC"' in source
    assert 'qa_validate_curl_timeouts "$HTTP_CONNECT_TIMEOUT_SEC" "$HTTP_MAX_TIME_SEC"' in source
    assert "math.isfinite(value)" in helper
    assert "connect > total" in helper
    commands = _curl_commands(source)
    assert commands
    assert all('"${curl_timeout_args[@]}"' in command for command in commands)
    assert all("curl --disable" in command for command in commands)


@pytest.mark.parametrize("script", QA_SCRIPTS, ids=lambda path: path.stem)
def test_access_tokens_never_enter_shell_variables_or_command_output(
    script: Path,
) -> None:
    source = script.read_text(encoding="utf-8")
    helper = QA_LIB.read_text(encoding="utf-8")

    assert re.search(r"^(?:TOKEN|ACCESS_TOKEN)=", source, re.MULTILINE) is None
    assert 'qa_write_auth_header "$AUTH_JSON" "$AUTH_HEADER_FILE"' in source
    assert "payload.get(\"access_token\")" in helper
    assert "os.open(" in helper
    assert "0o600" in helper
    assert "len(token) > 16_384" in helper
    assert '"\\r" in token' in helper
    assert '"\\n" in token' in helper
    assert "print(token" not in source
    assert "print(token" not in helper
    assert "echo $TOKEN" not in source
    assert "echo ${TOKEN}" not in source


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": ""},
        {"access_token": 123},
        {"access_token": "safe\r\nX-Injected: never"},
        {"access_token": "x" * 16_385},
    ],
)
def test_auth_header_helper_rejects_invalid_tokens_without_a_partial_file(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    auth_json = tmp_path / "auth.json"
    auth_json.write_text(json.dumps(payload), encoding="utf-8")
    header_file = tmp_path / "auth.headers"
    command = (
        'source "$1"; qa_write_auth_header "$2" "$3"'
    )

    result = subprocess.run(
        ["bash", "-c", command, "bash", str(QA_LIB), str(auth_json), str(header_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "missing or invalid access_token" in result.stderr
    assert "X-Injected" not in result.stderr
    assert not header_file.exists()


def test_bullhorn_runbook_uses_one_private_file_boundary_for_all_credentials() -> None:
    source = BULLHORN_RUNBOOK.read_text(encoding="utf-8")
    setup_start = source.index("set -eu")
    setup_end = source.index("\n```", setup_start)
    setup = source[setup_start:setup_end]
    first_secret_clear = setup.index(
        "unset STAGING_ADMIN_JWT BULLHORN_USERNAME BULLHORN_CLIENT_ID"
    )

    assert "export BULLHORN_" not in source
    assert setup.index("set +x") < first_secret_clear < setup.index("mktemp -d")
    assert setup.index("umask 077") < first_secret_clear
    assert setup.index("read -r -p 'Staging base URL") < setup.index(
        "read -r -s -p 'Staging admin JWT"
    )
    assert 'parsed.scheme != "https"' in setup
    assert "parsed.username is not None" in setup
    payload_builder = setup.index('python3 - "$VALIDATION_TMP_DIR"')
    assert setup.index("unset STAGING_ADMIN_JWT", first_secret_clear + 1) < setup.index(
        "chmod 600"
    ) < payload_builder
    assert '[[ -z "$STAGING_ADMIN_JWT"' in setup
    assert '"$STAGING_ADMIN_JWT" == *$\'\\r\'*' in setup
    assert '"$STAGING_ADMIN_JWT" == *$\'\\n\'*' in setup
    assert "credential environment was not cleared before payload build" in setup
    assert payload_builder < setup.index("for credential_file in")
    assert '--credentials-file "$BULLHORN_CONNECT_PAYLOAD_FILE"' in source
    assert "--out tests/fixtures/bullhorn_recorded" in source
    assert "--out backend/tests/fixtures/bullhorn_recorded" not in source
    assert "`seeded_stage_rows`" in source
    assert "`seeded_rows`" not in source
    assert "`candidate.json`, `notes.json`, and `file_attachments.json`" in source
    assert "only when submissions exist" in source
    assert "--job-order-id \"$BULLHORN_TEST_JOB_ORDER_ID\"" in source
    assert "--require-event --event-wait-seconds 120" in source
    assert "Never pass a known real value as a command-line argument" in source
    assert "grep the directory" not in source
    assert "connect_and_start_full_sync" in source
    capture_position = source.index("python backend/scripts/bullhorn_capture_fixtures.py")
    connect_position = source.index('curl -sS -X POST "$STAGING/api/v1/bullhorn/connect"')
    assert capture_position < connect_position
    assert "Only if its returned" in source


def test_bullhorn_runbook_setup_keeps_secrets_out_of_every_child_and_cleans_up(
    tmp_path: Path,
) -> None:
    source = BULLHORN_RUNBOOK.read_text(encoding="utf-8")
    setup_start = source.index("set -eu")
    setup_end = source.index("\n```", setup_start)
    setup = source[setup_start:setup_end]
    connect_start = source.index(
        'curl -sS -X POST "$STAGING/api/v1/bullhorn/connect"'
    )
    connect_end = source.index("\n   ```", connect_start)
    connect_command = source[connect_start:connect_end]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    forbidden = (
        "STAGING_ADMIN_JWT",
        "BULLHORN_USERNAME",
        "BULLHORN_CLIENT_ID",
        "BULLHORN_CLIENT_SECRET",
        "BULLHORN_PASSWORD",
    )
    for command, real_command in (
        ("mktemp", shutil.which("mktemp")),
        ("chmod", shutil.which("chmod")),
        ("python3", sys.executable),
    ):
        assert real_command is not None
        _write_guarded_passthrough(
            fake_bin / command,
            real_command,
            forbidden_environment=forbidden,
        )

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        r'''#!/usr/bin/env bash
set -euo pipefail
for secret_name in STAGING_ADMIN_JWT BULLHORN_USERNAME BULLHORN_CLIENT_ID BULLHORN_CLIENT_SECRET BULLHORN_PASSWORD; do
  if [[ -n "${!secret_name+x}" ]]; then
    echo "credential environment reached curl" >&2
    exit 97
  fi
done
header_file=""
payload_file=""
for arg in "$@"; do
  case "$arg" in
    *read-jwt*|*read-user*|*read-client*|*read-secret*|*read-password*)
      echo "credential reached curl argv" >&2
      exit 98
      ;;
    @*staging-auth.headers) header_file="${arg#@}" ;;
    @*bullhorn-connect.json) payload_file="${arg#@}" ;;
  esac
done
[[ -n "$header_file" && -n "$payload_file" ]]
file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}
[[ "$(file_mode "$header_file")" == "600" ]]
[[ "$(file_mode "$payload_file")" == "600" ]]
[[ "$(<"$header_file")" == "Authorization: Bearer read-jwt" ]]
[[ "$(<"$payload_file")" == '{"username":"read-user","client_id":"read-client","client_secret":"read-secret","password":"read-password\\n-literal"}' ]]
[[ " $* " == *" https://staging.example.test/api/v1/bullhorn/connect "* ]]
validation_dir="${header_file%/*}"
for raw_name in bullhorn-username.raw bullhorn-client-id.raw bullhorn-client-secret.raw bullhorn-password.raw; do
  [[ ! -e "$validation_dir/$raw_name" ]]
done
printf 'curl-ok\ntmp-dir=%s\n' "$validation_dir" > "$BULLHORN_SETUP_AUDIT"
printf '%s\n' '{}'
''',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    audit_file = tmp_path / "bullhorn-setup-audit.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "BULLHORN_SETUP_AUDIT": str(audit_file),
        "STAGING_ADMIN_JWT": "inherited-jwt-must-be-cleared",
        "BULLHORN_USERNAME": "inherited-user-must-be-cleared",
        "BULLHORN_CLIENT_ID": "inherited-client-must-be-cleared",
        "BULLHORN_CLIENT_SECRET": "inherited-secret-must-be-cleared",
        "BULLHORN_PASSWORD": "inherited-password-must-be-cleared",
    }
    command = f"{setup}\n{connect_command}\n"

    result = subprocess.run(
        ["bash", "-c", command],
        input=(
            "https://staging.example.test\n"
            "read-jwt\nread-user\nread-client\nread-secret\n"
            "read-password\\n-literal\n"
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    visible = result.stdout + result.stderr
    for secret in (
        "read-jwt",
        "read-user",
        "read-client",
        "read-secret",
        "read-password",
        "inherited-jwt",
        "inherited-user",
        "inherited-client",
        "inherited-secret",
        "inherited-password",
    ):
        assert secret not in visible
    audit = audit_file.read_text(encoding="utf-8")
    assert "curl-ok" in audit
    validation_dir = Path(
        next(
            line.removeprefix("tmp-dir=")
            for line in audit.splitlines()
            if line.startswith("tmp-dir=")
        )
    )
    assert not validation_dir.exists()


@pytest.mark.parametrize("jwt_assignment", [r"$'bad\rjwt'", r"$'bad\njwt'"])
def test_bullhorn_runbook_rejects_actual_jwt_line_breaks_before_header_write(
    tmp_path: Path,
    jwt_assignment: str,
) -> None:
    source = BULLHORN_RUNBOOK.read_text(encoding="utf-8")
    setup_start = source.index("set -eu")
    setup_end = source.index("\n```", setup_start)
    setup = source[setup_start:setup_end]
    prompt = "read -r -s -p 'Staging admin JWT: ' STAGING_ADMIN_JWT; printf '\\n'"
    setup = setup.replace(
        prompt,
        f"STAGING_ADMIN_JWT={jwt_assignment}; printf '\\n'",
    )
    assert prompt not in setup

    result = subprocess.run(
        ["bash", "-c", setup],
        input=(
            "https://staging.example.test\n"
            "read-user\nread-client\nread-secret\nread-password\n"
        ),
        env={**os.environ, "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode != 0
    visible = result.stdout + result.stderr
    assert "Invalid staging admin JWT" in visible
    assert "bad\rjwt" not in visible
    assert "bad\njwt" not in visible
    assert not list(tmp_path.glob("staging-auth.headers"))


def test_deployment_runbook_uses_a_silent_short_lived_smoke_password() -> None:
    source = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    section = source.split("## Production Smoke (Test Account)", 1)[1].split(
        "## Custom Domain Configuration",
        1,
    )[0]

    assert "export TAALI_TEST_PASSWORD" not in section
    assert "'<secure-secret>'" not in section
    assert section.index("set +x") < section.index("read -r -s -p")
    assert "trap 'unset taali_test_password' EXIT" in section
    assert section.count('TAALI_TEST_PASSWORD="$taali_test_password"') == 2
    assert "approved secret store without printing it" in section
    assert "mode-0600 form file" in section


def test_workable_operator_script_uses_private_credential_files() -> None:
    source = WORKABLE_API_SCRIPT.read_text(encoding="utf-8")
    usage = source.split("set -euo pipefail", 1)[0]

    assert os.access(WORKABLE_API_SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(WORKABLE_API_SCRIPT)], check=True)
    assert usage.count("read -r -s -p") == 3
    assert "approved secret-store command that does not echo values" in usage
    assert "ADMIN_SECRET=$(railway" not in usage
    assert 'ADMIN_SECRET="your-admin-secret"' not in usage
    assert 'AUTH_TOKEN="..."' not in usage
    assert 'PASSWORD="..."' not in usage
    assert "set -euo pipefail" in source
    snapshot = source.index('ADMIN_SECRET_VALUE="${ADMIN_SECRET:-}"')
    inherited_clear = source.index("unset ADMIN_SECRET AUTH_TOKEN PASSWORD")
    first_child = source.index('mktemp "${TMPDIR:-/tmp}')
    assert source.index("set +x") < snapshot < inherited_clear < first_child
    assert "export -n ADMIN_SECRET_VALUE AUTH_TOKEN_VALUE PASSWORD_VALUE" in source
    assert "umask 077" in source
    assert "trap _cleanup EXIT" in source
    assert 'HTTP_CONNECT_TIMEOUT_SEC="${HTTP_CONNECT_TIMEOUT_SEC:-5}"' in source
    assert 'HTTP_MAX_TIME_SEC="${HTTP_MAX_TIME_SEC:-30}"' in source
    assert 'qa_validate_curl_timeouts "$HTTP_CONNECT_TIMEOUT_SEC" "$HTTP_MAX_TIME_SEC"' in source
    assert "curl_args=(" in source
    assert source.index("--disable", source.index("curl_args=(")) < source.index(
        "--no-location", source.index("curl_args=(")
    )
    assert '--proto "$CURL_PROTOCOLS"' in source
    assert '--connect-timeout "$HTTP_CONNECT_TIMEOUT_SEC"' in source
    assert '--max-time "$HTTP_MAX_TIME_SEC"' in source
    commands = [
        command
        for command in _curl_commands(source)
        if re.match(r"^\s*(?:if ! )?curl\b", command)
    ]
    assert commands
    assert all('curl "${curl_args[@]}"' in command for command in commands)
    status_call = next(
        command for command in commands if "/workable/sync/status" in command
    )
    assert "--fail" in status_call
    assert '--output "$RESPONSE_FILE"' in status_call
    assert 'taali-workable-response.XXXXXX' in source
    assert 'chmod 600 \\\n' in source
    assert '--header "@${ADMIN_HEADER_FILE}"' in source
    assert '--header "@${AUTH_HEADER_FILE}"' in source
    assert '--data-urlencode "username@${FORM_USERNAME_FILE}"' in source
    assert '--data-urlencode "password@${FORM_PASSWORD_FILE}"' in source
    assert "unset ADMIN_SECRET AUTH_TOKEN PASSWORD" in source

    for exposed in (
        '-H "X-Admin-Secret:',
        '--header "X-Admin-Secret:',
        '-H "Authorization: Bearer',
        '--header "Authorization: Bearer',
        '-d "username=${EMAIL}&password=${PASSWORD}"',
        '--data-urlencode "password=${PASSWORD}"',
        "Login failed: $RESP",
        "/tmp/workable_admin.json",
    ):
        assert exposed not in source


def test_workable_status_http_failure_never_reports_success_or_leaks_token(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    audit_file = tmp_path / "curl-args.txt"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ -n \"${AUTH_TOKEN+x}\" || -n \"${ADMIN_SECRET+x}\" || "
        "-n \"${PASSWORD+x}\" ]]; then\n"
        "  echo 'credential environment reached curl' >&2\n"
        "  exit 97\n"
        "fi\n"
        "printf '%s\\n' \"$@\" > \"$CURL_AUDIT\"\n"
        "exit 22\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ADMIN_SECRET", "AUTH_TOKEN", "PASSWORD"}
    }
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "BACKEND_URL": "https://api.example.test",
            "AUTH_TOKEN": TOKEN,
            "CURL_AUDIT": str(audit_file),
            "TMPDIR": str(tmp_path),
        }
    )

    result = subprocess.run(
        [str(WORKABLE_API_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    visible = result.stdout + result.stderr
    assert "Workable status request failed; response body withheld." in visible
    assert "Done." not in visible
    assert TOKEN not in visible
    args = audit_file.read_text(encoding="utf-8").splitlines()
    assert args[0] == "--disable"
    assert "--no-location" in args
    assert "--proto" in args and "=https" in args
    assert "--connect-timeout" in args
    assert "--max-time" in args
    assert "--fail" in args
    assert TOKEN not in args


def test_admin_health_runbooks_use_private_header_files() -> None:
    for runbook in (HUMAN_PILOT_RUNBOOK, GRAPHITI_RUNBOOK):
        source = runbook.read_text(encoding="utf-8")
        assert "set +x" in source
        assert "umask 077" in source
        snapshot = source.index('admin_secret="${ADMIN_SECRET:?')
        inherited_clear = source.index("unset ADMIN_SECRET", snapshot)
        first_child = source.index('auth_header_file="$(mktemp ', snapshot)
        local_clear = source.index("unset admin_secret", first_child)
        assert snapshot < inherited_clear < first_child < local_clear
        assert local_clear < source.index("chmod 600", local_clear)
        assert 'auth_header_file="$(mktemp ' in source
        assert "chmod 600 \"$auth_header_file\"" in source
        assert "trap 'rm -f \"$auth_header_file\"' EXIT" in source
        assert '--header "@$auth_header_file"' in source
        assert '-H "X-Admin-Secret: $ADMIN_SECRET"' not in source
        assert '--header "X-Admin-Secret: $ADMIN_SECRET"' not in source


def test_human_pilot_runbook_keeps_database_uri_out_of_psql_argv() -> None:
    source = HUMAN_PILOT_RUNBOOK.read_text(encoding="utf-8")
    psql_commands = [
        line.strip()
        for line in source.splitlines()
        if line.lstrip().startswith("psql ")
    ]

    assert len(psql_commands) == 2
    assert all("DATABASE_PUBLIC_URL" not in command for command in psql_commands)
    assert all(
        command.startswith("psql --no-psqlrc --set=ON_ERROR_STOP=1 -c ")
        for command in psql_commands
    )
    assert 'PGSERVICEFILE="$(mktemp ' in source
    assert 'pgpass_file="$(mktemp ' in source
    assert 'database_uri_file="$(mktemp ' in source
    assert (
        'trap \'rm -f -- "$PGSERVICEFILE" "$pgpass_file" '
        '"$database_uri_file"\' EXIT'
    ) in source
    assert (
        'chmod 600 "$PGSERVICEFILE" "$pgpass_file" "$database_uri_file"'
    ) in source
    assert 'printf \'%s\' "$DATABASE_PUBLIC_URL" > "$database_uri_file"' in source
    parser_call = 'python3 - "$database_uri_file" "$PGSERVICEFILE" "$pgpass_file"'
    assert parser_call in source
    assert 'urlsplit(Path(sys.argv[1]).read_text(encoding="utf-8"))' in source
    assert "parse_qsl" not in source
    assert 'raw_option.split("=", 1)' in source
    assert 'unquote(value, errors="strict")' in source
    assert '("password", password)' not in source
    assert 'f"\\npassfile={pass_file}\\n"' in source
    assert "def pgpass_escape(value: str) -> str:" in source
    assert "*$'\\n'*|*$'\\r'*)" in source
    assert "export PGSERVICE=taali-human-pilot" in source
    assert source.index('printf \'%s\' "$DATABASE_PUBLIC_URL"') < source.index(
        "unset DATABASE_PUBLIC_URL"
    ) < source.index(parser_call) < source.index("psql --no-psqlrc")


def test_human_pilot_database_setup_builds_private_libpq_files(
    tmp_path: Path,
) -> None:
    source = HUMAN_PILOT_RUNBOOK.read_text(encoding="utf-8")
    setup_start = source.index("# Run once in a dedicated operator shell")
    setup_end = source.index("# Prod: active org-less templates", setup_start)
    setup = source[setup_start:setup_end]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    audit_file = tmp_path / "psql-audit.log"
    parser_environment_audit = tmp_path / "parser-environment-audit.log"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        r'''#!/usr/bin/env bash
set -euo pipefail
[[ -z "${DATABASE_PUBLIC_URL+x}" ]]
printf '%s\n' 'parser-clean' > "$PARSER_ENVIRONMENT_AUDIT"
exec "$REAL_PYTHON" "$@"
''',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        r'''#!/usr/bin/env bash
set -euo pipefail
[[ -z "${DATABASE_PUBLIC_URL+x}" ]]
service_mode="$(stat -c '%a' "$PGSERVICEFILE" 2>/dev/null || stat -f '%Lp' "$PGSERVICEFILE")"
passfile="$(sed -n 's/^passfile=//p' "$PGSERVICEFILE")"
pass_mode="$(stat -c '%a' "$passfile" 2>/dev/null || stat -f '%Lp' "$passfile")"
{
  printf 'service-path=%s\nservice-mode=%s\npass-path=%s\npass-mode=%s\n' \
    "$PGSERVICEFILE" "$service_mode" "$passfile" "$pass_mode"
  printf '%s\n' 'service-start'
  cat "$PGSERVICEFILE"
  printf '%s\n' 'service-end' 'pass-start'
  cat "$passfile"
  printf '%s\n' 'pass-end' 'argv-start'
  printf '%s\n' "$@"
  printf '%s\n' 'argv-end'
} > "$PSQL_AUDIT_FILE"
''',
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    database_uri = (
        "postgresql://operator%2Bpilot:p%40ss%3Aword%5Ctail@"
        "db.example.test:6543/taali%2Dprod?sslmode=require&connect_timeout=10"
        "&application_name=pilot+literal%2Bencoded"
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "DATABASE_PUBLIC_URL": database_uri,
        "PSQL_AUDIT_FILE": str(audit_file),
        "PARSER_ENVIRONMENT_AUDIT": str(parser_environment_audit),
        "REAL_PYTHON": sys.executable,
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            f"{setup}\npsql --no-psqlrc --set=ON_ERROR_STOP=1 -c 'SELECT 1;'",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert database_uri not in result.stdout + result.stderr
    audit = audit_file.read_text(encoding="utf-8")
    assert "service-mode=600" in audit
    assert "pass-mode=600" in audit
    assert "host=db.example.test" in audit
    assert "port=6543" in audit
    assert "dbname=taali-prod" in audit
    assert "user=operator+pilot" in audit
    assert "sslmode=require" in audit
    assert "connect_timeout=10" in audit
    assert "application_name=pilot+literal+encoded" in audit
    assert "application_name=pilot literal+encoded" not in audit
    assert "\npassword=" not in audit
    assert "db.example.test:6543:taali-prod:operator+pilot:p@ss\\:word\\\\tail" in audit
    argv = audit.split("argv-start\n", 1)[1].split("argv-end\n", 1)[0]
    assert database_uri not in argv
    assert "p@ss" not in argv
    temp_paths = [
        Path(line.split("=", 1)[1])
        for line in audit.splitlines()
        if line.startswith(("service-path=", "pass-path="))
    ]
    assert len(temp_paths) == 2
    assert all(not path.exists() for path in temp_paths)
    assert parser_environment_audit.read_text(encoding="utf-8") == "parser-clean\n"
    assert not list(tmp_path.glob("taali-human-pilot-*"))


def _write_fake_curl(path: Path) -> None:
    path.write_text(
        r'''#!/usr/bin/env bash
set -euo pipefail

url=""
output_file=""
write_out=""
connect_timeout=""
max_time=""
form_specs=()
header_specs=()

{
  printf '%s\n' 'call'
  for arg in "$@"; do
    printf 'argv=%q\n' "$arg"
    if [[ "$arg" == *"$EXPECTED_PASSWORD"* || "$arg" == *"$EXPECTED_TOKEN"* ]]; then
      printf '%s\n' 'secret-in-argv'
    fi
  done
} >> "$FAKE_CURL_LOG"

if [[ -n "${TAALI_TEST_PASSWORD+x}" \
      || -n "${TOKEN+x}" \
      || -n "${ACCESS_TOKEN+x}" ]]; then
  printf '%s\n' 'secret-in-child-env' >> "$FAKE_CURL_LOG"
fi

while (( $# )); do
  case "$1" in
    -o|--output)
      output_file="$2"
      shift 2
      ;;
    -w|--write-out)
      write_out="$2"
      shift 2
      ;;
    --connect-timeout)
      connect_timeout="$2"
      shift 2
      ;;
    --max-time)
      max_time="$2"
      shift 2
      ;;
    --data-urlencode)
      form_specs+=("$2")
      shift 2
      ;;
    -H|--header)
      header_specs+=("$2")
      shift 2
      ;;
    http://*|https://*)
      url="$1"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [[ "$connect_timeout" == "$EXPECTED_CONNECT_TIMEOUT" \
      && "$max_time" == "$EXPECTED_MAX_TIME" ]]; then
  printf '%s\n' 'timeouts-ok' >> "$FAKE_CURL_LOG"
else
  printf 'timeouts-missing connect=%q total=%q\n' \
    "$connect_timeout" "$max_time" >> "$FAKE_CURL_LOG"
fi

file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

record_temp_file() {
  printf 'temp-path=%s\n' "$1" >> "$FAKE_CURL_LOG"
}

if [[ "$url" == */auth/jwt/login ]]; then
  username_file=""
  password_file=""
  for spec in "${form_specs[@]}"; do
    case "$spec" in
      username@*) username_file="${spec#username@}" ;;
      password@*) password_file="${spec#password@}" ;;
    esac
  done
  if [[ -n "$username_file" && -n "$password_file" \
        && "$(file_mode "$username_file")" == "600" \
        && "$(file_mode "$password_file")" == "600" \
        && "$(cat "$username_file")" == "$EXPECTED_EMAIL" \
        && "$(cat "$password_file")" == "$EXPECTED_PASSWORD" ]]; then
    printf '%s\n' 'login-form-files-ok' >> "$FAKE_CURL_LOG"
    record_temp_file "$username_file"
    record_temp_file "$password_file"
  else
    printf '%s\n' 'login-form-files-missing-or-invalid' >> "$FAKE_CURL_LOG"
  fi

  if [[ "$FAKE_CURL_MODE" == "auth-failure" ]]; then
    printf '{"access_token":"%s","detail":"rejected"}' "$RESPONSE_TOKEN" > "$output_file"
    code="401"
  elif [[ "$FAKE_CURL_MODE" == "malicious-token" ]]; then
    printf '%s' '{"access_token":"header-safe-prefix\r\nX-Injected: never"}' > "$output_file"
    code="200"
  else
    printf '{"access_token":"%s"}' "$EXPECTED_TOKEN" > "$output_file"
    code="200"
  fi
else
  header_file=""
  for spec in "${header_specs[@]}"; do
    if [[ "$spec" == @* ]]; then
      candidate="${spec#@}"
      if [[ "$(cat "$candidate")" == "Authorization: Bearer $EXPECTED_TOKEN" ]]; then
        header_file="$candidate"
      fi
    elif [[ "$spec" == Authorization:* ]]; then
      printf '%s\n' 'secret-in-argv' >> "$FAKE_CURL_LOG"
    fi
  done
  if [[ -n "$header_file" && "$(file_mode "$header_file")" == "600" ]]; then
    printf '%s\n' 'auth-header-file-ok' >> "$FAKE_CURL_LOG"
    record_temp_file "$header_file"
  else
    printf '%s\n' 'auth-header-file-missing-or-invalid' >> "$FAKE_CURL_LOG"
  fi
  printf '{}' > "$output_file"
  code="418"
fi

if [[ -n "$write_out" ]]; then
  rendered="${write_out//\%\{http_code\}/$code}"
  rendered="${rendered//\%\{time_total\}/0.001}"
  printf '%b' "$rendered"
fi
''',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_with_fake_curl(
    script: Path,
    tmp_path: Path,
    *,
    mode: str,
    xtrace: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    _write_fake_curl(fake_curl)
    for command, real_command in (
        ("mktemp", shutil.which("mktemp")),
        ("chmod", shutil.which("chmod")),
        ("python3", sys.executable),
    ):
        assert real_command is not None
        _write_guarded_passthrough(
            fake_bin / command,
            real_command,
            forbidden_environment=("TAALI_TEST_PASSWORD",),
        )
    log_file = tmp_path / "curl.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "TAALI_API_BASE_URL": "https://api.example.test/api/v1",
        "TAALI_TEST_EMAIL": "qa+argv@example.test",
        "TAALI_TEST_PASSWORD": PASSWORD,
        "TAALI_ROLE_ID": "1",
        "EXPECTED_PASSWORD": PASSWORD,
        "EXPECTED_EMAIL": "qa+argv@example.test",
        "EXPECTED_TOKEN": TOKEN,
        "RESPONSE_TOKEN": RESPONSE_TOKEN,
        "EXPECTED_CONNECT_TIMEOUT": "1",
        "EXPECTED_MAX_TIME": "2",
        "HTTP_CONNECT_TIMEOUT_SEC": "1",
        "HTTP_MAX_TIME_SEC": "2",
        "FAKE_CURL_MODE": mode,
        "FAKE_CURL_LOG": str(log_file),
    }
    env.pop("TOKEN", None)
    env.pop("ACCESS_TOKEN", None)
    result = subprocess.run(
        ["bash", *(["-x"] if xtrace else []), str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result, log_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("script", QA_SCRIPTS, ids=lambda path: path.stem)
def test_smoke_scripts_pass_only_mode_0600_secret_files_to_curl(
    script: Path,
    tmp_path: Path,
) -> None:
    result, log = _run_with_fake_curl(script, tmp_path, mode="header-check")

    expected_exit = {
        QA_SCRIPTS[0]: 22,
        QA_SCRIPTS[1]: 20,
        QA_SCRIPTS[2]: 12,
    }[script]
    assert result.returncode == expected_exit
    assert "secret-in-argv" not in log
    assert "secret-in-child-env" not in log
    assert "login-form-files-ok" in log
    assert "auth-header-file-ok" in log
    assert "timeouts-missing" not in log
    assert "timeouts-ok" in log
    temp_paths = [
        Path(line.removeprefix("temp-path="))
        for line in log.splitlines()
        if line.startswith("temp-path=")
    ]
    assert len(temp_paths) == 3
    assert all(not path.exists() for path in temp_paths)


@pytest.mark.parametrize("script", QA_SCRIPTS, ids=lambda path: path.stem)
def test_smoke_scripts_do_not_trace_passwords_or_tokens(
    script: Path,
    tmp_path: Path,
) -> None:
    result, log = _run_with_fake_curl(
        script,
        tmp_path,
        mode="header-check",
        xtrace=True,
    )

    assert PASSWORD not in result.stdout
    assert PASSWORD not in result.stderr
    assert TOKEN not in result.stdout
    assert TOKEN not in result.stderr
    assert "secret-in-child-env" not in log


@pytest.mark.parametrize("script", QA_SCRIPTS, ids=lambda path: path.stem)
def test_smoke_scripts_never_print_auth_response_tokens(
    script: Path,
    tmp_path: Path,
) -> None:
    result, _log = _run_with_fake_curl(script, tmp_path, mode="auth-failure")

    expected_exit = {
        QA_SCRIPTS[0]: 21,
        QA_SCRIPTS[1]: 11,
        QA_SCRIPTS[2]: 11,
    }[script]
    assert result.returncode == expected_exit
    assert RESPONSE_TOKEN not in result.stdout
    assert RESPONSE_TOKEN not in result.stderr


@pytest.mark.parametrize("script", QA_SCRIPTS, ids=lambda path: path.stem)
def test_smoke_scripts_reject_header_injection_tokens_before_second_curl(
    script: Path,
    tmp_path: Path,
) -> None:
    result, log = _run_with_fake_curl(script, tmp_path, mode="malicious-token")

    expected_exit = {
        QA_SCRIPTS[0]: 21,
        QA_SCRIPTS[1]: 11,
        QA_SCRIPTS[2]: 11,
    }[script]
    assert result.returncode == expected_exit
    assert log.count("call") == 1
    assert "auth-header-file-ok" not in log
    assert MALICIOUS_RESPONSE_TOKEN not in result.stdout
    assert MALICIOUS_RESPONSE_TOKEN not in result.stderr
    assert "X-Injected" not in result.stdout
    assert "X-Injected" not in result.stderr


@pytest.mark.parametrize("script", QA_SCRIPTS, ids=lambda path: path.stem)
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HTTP_CONNECT_TIMEOUT_SEC", "0"),
        ("HTTP_CONNECT_TIMEOUT_SEC", "nan"),
        ("HTTP_MAX_TIME_SEC", "inf"),
        ("HTTP_MAX_TIME_SEC", "301"),
        ("HTTP_CONNECT_TIMEOUT_SEC", "31"),
    ],
)
def test_smoke_scripts_reject_unbounded_timeouts_before_curl(
    script: Path,
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "curl-was-called"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        f"#!/usr/bin/env bash\ntouch {shlex.quote(str(marker))}\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TAALI_TEST_PASSWORD": PASSWORD,
        "HTTP_CONNECT_TIMEOUT_SEC": "5",
        "HTTP_MAX_TIME_SEC": "30",
        name: value,
    }

    result = subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "curl timeouts" in result.stderr
    assert not marker.exists()


def _write_fake_workable_curl(path: Path) -> None:
    path.write_text(
        r'''#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${ADMIN_SECRET+x}" || -n "${AUTH_TOKEN+x}" || -n "${PASSWORD+x}" ]]; then
  echo "credential leaked into curl environment" >&2
  exit 9
fi

for arg in "$@"; do
  case "$arg" in
    *admin-secret*|*provided-secret-token*|*password-secret*|*issued-secret-token*)
      echo "credential leaked into curl arguments" >&2
      exit 10
      ;;
  esac
  printf '%s\n' "$arg" >> "$CURL_ARG_LOG"
done

output_file=""
url=""
args=("$@")
for ((index = 0; index < ${#args[@]}; index++)); do
  if [[ "${args[$index]}" == "--output" ]]; then
    output_file="${args[$((index + 1))]}"
  elif [[ "${args[$index]}" == https://* ]]; then
    url="${args[$index]}"
  fi
done

case "$url" in
  */workable/admin/diagnostic)
    if [[ "$CURL_SCENARIO" == "admin_success" ]]; then
      printf '{"ok":true}' > "$output_file"
      printf '200'
    else
      printf '{"detail":"provider-body-must-stay-private"}' > "$output_file"
      printf '404'
    fi
    ;;
  */auth/jwt/login)
    printf '{"access_token":"issued-secret-token"}' > "$output_file"
    printf '200'
    ;;
  */workable/sync/status*)
    printf '{"status":"ok"}\n' > "$output_file"
    ;;
  *)
    echo "unexpected fake curl URL: $url" >&2
    exit 2
    ;;
esac
''',
        encoding="utf-8",
    )
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("scenario", "auth_env", "expected_call_count"),
    [
        ("admin_success", {}, 1),
        ("token_fallback", {"AUTH_TOKEN": "provided-secret-token"}, 2),
        ("login_fallback", {"PASSWORD": "password-secret"}, 3),
    ],
)
def test_workable_operator_fallbacks_redact_and_cleanup_credentials(
    tmp_path: Path,
    scenario: str,
    auth_env: dict[str, str],
    expected_call_count: int,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_workable_curl(fake_bin / "curl")
    for command, real_command in (
        ("mktemp", shutil.which("mktemp")),
        ("chmod", shutil.which("chmod")),
        ("python3", sys.executable),
    ):
        assert real_command is not None
        _write_guarded_passthrough(
            fake_bin / command,
            real_command,
            forbidden_environment=("ADMIN_SECRET", "AUTH_TOKEN", "PASSWORD"),
        )
    argument_log = tmp_path / "curl-arguments.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "BACKEND_URL": "https://backend.example.test",
        "ADMIN_SECRET": "admin-secret",
        "EMAIL": "operator@example.test",
        "AUTH_TOKEN": "",
        "PASSWORD": "",
        "CURL_SCENARIO": scenario,
        "CURL_ARG_LOG": str(argument_log),
        **auth_env,
    }

    result = subprocess.run(
        ["bash", "-x", str(WORKABLE_API_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    arguments = argument_log.read_text(encoding="utf-8")
    visible_output = result.stdout + result.stderr
    for secret in (
        "admin-secret",
        "provided-secret-token",
        "password-secret",
        "issued-secret-token",
        "provider-body-must-stay-private",
    ):
        assert secret not in arguments
        assert secret not in visible_output
    assert arguments.count("https://") == expected_call_count
    assert not list(tmp_path.glob("taali-workable-*"))

    if scenario == "admin_success":
        assert "Done (admin)." in result.stdout
        assert "/auth/jwt/login" not in arguments
        assert "/workable/sync/status" not in arguments
    elif scenario == "token_fallback":
        assert "Login OK." not in result.stdout
        assert "/auth/jwt/login" not in arguments
        assert "/workable/sync/status" in arguments
    else:
        assert "Login OK." in result.stdout
        assert "/auth/jwt/login" in arguments
        assert "/workable/sync/status" in arguments


def test_model_smoke_preserves_form_url_encoding_with_real_curl() -> None:
    observed: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, payload: dict[str, str]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            assert self.path == "/api/v1/auth/jwt/login"
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            observed["form"] = parse_qs(body, keep_blank_values=True)
            self._json({"access_token": TOKEN})

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            assert self.path == "/api/v1/organizations/me"
            observed["authorization"] = self.headers.get("Authorization")
            self._json({"active_claude_model": "claude-test-model"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "TAALI_API_BASE_URL": (
                f"http://127.0.0.1:{server.server_address[1]}/api/v1"
            ),
            "TAALI_TEST_EMAIL": "qa+urlencode@example.test",
            "TAALI_TEST_PASSWORD": PASSWORD,
            "EXPECTED_CLAUDE_MODEL": "claude-test-model",
        }
        result = subprocess.run(
            ["bash", str(QA_SCRIPTS[0])],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert observed["form"] == {
        "username": ["qa+urlencode@example.test"],
        "password": [PASSWORD],
    }
    assert observed["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in result.stdout
    assert TOKEN not in result.stderr


def test_model_smoke_total_timeout_stops_a_stalled_auth_request() -> None:
    request_started = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            request_started.set()
            threading.Event().wait(2)

    class DaemonServer(ThreadingHTTPServer):
        daemon_threads = True

    server = DaemonServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "TAALI_API_BASE_URL": (
                f"http://127.0.0.1:{server.server_address[1]}/api/v1"
            ),
            "TAALI_TEST_EMAIL": "qa+timeout@example.test",
            "TAALI_TEST_PASSWORD": PASSWORD,
            "HTTP_CONNECT_TIMEOUT_SEC": "0.1",
            "HTTP_MAX_TIME_SEC": "0.25",
        }
        result = subprocess.run(
            ["bash", str(QA_SCRIPTS[0])],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert request_started.is_set()
    assert result.returncode != 0
    assert "timed out" in result.stderr.lower()
    assert PASSWORD not in result.stdout + result.stderr
