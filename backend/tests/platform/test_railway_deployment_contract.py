from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import sys
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[3]
RAILWAY_DIR = ROOT / "scripts" / "railway"
RELEASE_DIR = ROOT / "scripts" / "release"
SHELL_FILES = [
    ROOT / "scripts" / "deploy_production.sh",
    RELEASE_DIR / "assert_canonical_source.sh",
    RELEASE_DIR / "assert_canonical_release.sh",
    RELEASE_DIR / "assert_provider_preflight.sh",
    RAILWAY_DIR / "lib.sh",
    RAILWAY_DIR / "check_status.sh",
    RAILWAY_DIR / "prepare_production.sh",
    RAILWAY_DIR / "deploy_worker.sh",
    RAILWAY_DIR / "deploy_backend.sh",
    RAILWAY_DIR / "deploy_production.sh",
]
RUNTIME_VENV_CREATE = (
    "python -m venv --copies /opt/venv && . /opt/venv/bin/activate"
)
RUNTIME_LOCK_CHECK = "python scripts/check_requirements_lock.py --runtime-only"
RUNTIME_HASH_INSTALL = (
    "python -m pip install --require-hashes --no-deps "
    "-r requirements-runtime-lock.txt"
)
RUNTIME_INSTALL_COMMAND = " && ".join(
    [RUNTIME_VENV_CREATE, RUNTIME_LOCK_CHECK, RUNTIME_HASH_INSTALL, "python -m pip check"]
)


@pytest.mark.parametrize("script", SHELL_FILES)
def test_railway_shell_scripts_are_executable_and_syntax_valid(script: Path):
    assert os.access(script, os.X_OK), f"{script} must be executable"
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_shared_railway_config_has_no_http_healthcheck():
    payload = json.loads((ROOT / "backend" / "railway.json").read_text())
    deploy = payload["deploy"]

    assert "healthcheckPath" not in deploy
    assert "healthcheckTimeout" not in deploy


def test_every_railway_config_uses_the_explicit_locked_nixpacks_plan():
    for filename in ("railway.json", "railway.worker.json"):
        payload = json.loads((ROOT / "backend" / filename).read_text())
        assert payload["$schema"] == "https://railway.com/railway.schema.json"
        assert payload["build"] == {
            "builder": "NIXPACKS",
            "nixpacksConfigPath": "nixpacks.toml",
        }

    plan = tomllib.loads((ROOT / "backend" / "nixpacks.toml").read_text())
    assert plan["phases"]["install"]["cmds"] == [RUNTIME_INSTALL_COMMAND]
    assert "pip install -r requirements.txt" not in str(plan)


def test_production_wrappers_pin_and_revalidate_locked_runtime_install():
    prepare = (RAILWAY_DIR / "prepare_production.sh").read_text()
    worker = (RAILWAY_DIR / "deploy_worker.sh").read_text()
    web = (RAILWAY_DIR / "deploy_backend.sh").read_text()
    library = (RAILWAY_DIR / "lib.sh").read_text()
    assert f'TALI_NIXPACKS_INSTALL_CMD="{RUNTIME_INSTALL_COMMAND}"' in library
    assert 'NIXPACKS_INSTALL_CMD="$TALI_NIXPACKS_INSTALL_CMD"' in prepare
    assert library.index("python -m venv --copies /opt/venv") < library.index(
        "check_requirements_lock.py"
    )
    assert library.index("check_requirements_lock.py") < library.index(
        "pip install --require-hashes"
    )
    assert prepare.index("check_requirements_lock.py") < prepare.index(
        "railway variable set"
    )
    assert prepare.count('"NIXPACKS_INSTALL_CMD"') == 1
    assert prepare.count("railway_validate_service_variable_exact") == 1
    assert worker.count("railway_validate_service_variable_exact") == 2
    assert web.count("railway_validate_service_variable_exact") == 1
    for script in (worker, web):
        assert script.index("check_requirements_lock.py") < script.index(
            "railway environment"
        )
        assert script.index('"NIXPACKS_INSTALL_CMD"') < script.index("railway up \\")


def test_locked_install_command_readback_is_exact_but_boolean_readback_is_not(
    tmp_path: Path,
):
    variables_file = tmp_path / "variables.json"
    env = {
        **os.environ,
        "RAILWAY_VARIABLES_FIXTURE": str(variables_file),
        "EXPECTED_VARIABLE": RUNTIME_INSTALL_COMMAND,
    }
    railway_stub = """
railway() {
  cat "$RAILWAY_VARIABLES_FIXTURE"
}
"""

    variables_file.write_text(
        json.dumps({"NIXPACKS_INSTALL_CMD": RUNTIME_INSTALL_COMMAND.upper()})
    )
    exact_result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{RAILWAY_DIR / "lib.sh"}"; {railway_stub} '
            "railway_validate_service_variable_exact production web "
            'NIXPACKS_INSTALL_CMD "$EXPECTED_VARIABLE"',
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert exact_result.returncode != 0

    variables_file.write_text(json.dumps({"USAGE_METER_LIVE": "TRUE"}))
    boolean_result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{RAILWAY_DIR / "lib.sh"}"; {railway_stub} '
            "railway_validate_service_variable production web "
            "USAGE_METER_LIVE true",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert boolean_result.returncode == 0, boolean_result.stderr


def test_coordinated_rollout_order_is_prepare_workers_then_web():
    script = (RAILWAY_DIR / "deploy_production.sh").read_text()

    assert script.index("prepare_production.sh") < script.index("deploy_worker.sh")
    assert script.index("deploy_worker.sh") < script.index("deploy_backend.sh")


def test_root_rollout_preflights_both_providers_before_any_deploy():
    script = (ROOT / "scripts" / "deploy_production.sh").read_text()

    assert script.index("assert_canonical_release.sh") < script.index(
        "assert_provider_preflight.sh"
    )
    assert script.index("assert_provider_preflight.sh") < script.index(
        "scripts/railway/deploy_production.sh"
    )
    assert script.index("railway_begin_coordinated_release") < script.index(
        "scripts/railway/deploy_production.sh"
    )
    assert script.index("scripts/railway/deploy_production.sh") < script.index(
        "vercel --prod --yes"
    )
    assert script.count('assert_canonical_source.sh" --expected-sha') >= 3
    assert "git status --porcelain" in script


def _provider_preflight_fixture(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "provider-calls.log"
    railway_user = tmp_path / "railway-user.json"
    railway_status = tmp_path / "railway-status.json"
    vercel_user = tmp_path / "vercel-user.json"
    vercel_link = tmp_path / "vercel-project.json"

    railway_user.write_text(json.dumps({"email": "release@example.test"}))
    railway_status.write_text(
        json.dumps(
            {
                "id": "railway-project-test",
                "name": "tali-test",
                "environments": {
                    "edges": [
                        {
                            "node": {
                                "name": "production",
                                "serviceInstances": {
                                    "edges": [
                                        {"node": {"serviceName": "web"}},
                                        {"node": {"serviceName": "worker"}},
                                        {"node": {"serviceName": "scoring"}},
                                    ]
                                },
                            }
                        }
                    ]
                },
            }
        )
    )
    vercel_user.write_text(json.dumps({"username": "release-user"}))
    vercel_link.write_text(
        json.dumps(
            {
                "projectId": "vercel-project-test",
                "orgId": "vercel-org-test",
                "projectName": "frontend-test",
            }
        )
    )

    fake_railway = fake_bin / "railway"
    fake_railway.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'railway %s\\n' "$*" >> "$PROVIDER_CALL_LOG"
case "$*" in
  "whoami --json") cat "$FAKE_RAILWAY_USER" ;;
  "status --json") cat "$FAKE_RAILWAY_STATUS" ;;
  *) echo "unexpected Railway command: $*" >&2; exit 97 ;;
esac
"""
    )
    fake_railway.chmod(0o755)

    fake_vercel = fake_bin / "vercel"
    fake_vercel.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'vercel %s\\n' "$*" >> "$PROVIDER_CALL_LOG"
case "$1 $2" in
  "whoami --format=json") cat "$FAKE_VERCEL_USER" ;;
  "project inspect") exit 0 ;;
  *) echo "unexpected Vercel command: $*" >&2; exit 98 ;;
esac
"""
    )
    fake_vercel.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "PROVIDER_CALL_LOG": str(call_log),
        "FAKE_RAILWAY_USER": str(railway_user),
        "FAKE_RAILWAY_STATUS": str(railway_status),
        "FAKE_VERCEL_USER": str(vercel_user),
        "TALI_VERCEL_LINK_FILE": str(vercel_link),
        "TALI_RAILWAY_PROJECT_ID": "railway-project-test",
        "TALI_RAILWAY_PROJECT_NAME": "tali-test",
        "RAILWAY_BACKEND_SERVICE": "web",
        "RAILWAY_WORKER_SERVICE": "worker",
        "RAILWAY_SCORING_WORKER_SERVICE": "scoring",
        "TALI_VERCEL_PROJECT_ID": "vercel-project-test",
        "TALI_VERCEL_ORG_ID": "vercel-org-test",
        "TALI_VERCEL_PROJECT_NAME": "frontend-test",
    }
    return env, call_log


def test_provider_preflight_is_read_only_and_validates_both_links(tmp_path: Path):
    env, call_log = _provider_preflight_fixture(tmp_path)

    result = subprocess.run(
        ["bash", str(RELEASE_DIR / "assert_provider_preflight.sh")],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text().splitlines()
    assert calls == [
        "railway whoami --json",
        "railway status --json",
        "vercel whoami --format=json --non-interactive",
        f"vercel project inspect --cwd {ROOT / 'frontend'} --non-interactive --no-color",
    ]
    assert not any(" up " in f" {call} " for call in calls)
    assert not any(" deploy " in f" {call} " for call in calls)
    assert not any(" variable set " in f" {call} " for call in calls)


def test_provider_preflight_fails_before_vercel_when_railway_link_is_wrong(
    tmp_path: Path,
):
    env, call_log = _provider_preflight_fixture(tmp_path)
    env["TALI_RAILWAY_PROJECT_ID"] = "different-production-project"

    result = subprocess.run(
        ["bash", str(RELEASE_DIR / "assert_provider_preflight.sh")],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "wrong Railway project" in result.stderr
    assert call_log.read_text().splitlines() == [
        "railway whoami --json",
        "railway status --json",
    ]


def test_predeploy_pins_metering_and_runs_separate_migrations():
    script = (RAILWAY_DIR / "prepare_production.sh").read_text()

    assert "USAGE_METER_LIVE=true" in script
    assert "ATS_PUBLIC_APPLY_ENABLED=true" in script
    assert "BULLHORN_ENABLED=true" in script
    assert "MVP_DISABLE_WORKABLE=false" in script
    assert "TRUST_RAILWAY_X_REAL_IP=true" in script
    assert 'railway_scoring_policy_from_file "$WEB_VARIABLES_FILE"' in script
    assert 'PRE_SCREEN_THRESHOLD="$PRE_SCREEN_THRESHOLD"' in script
    assert 'ENABLE_PRE_SCREEN_GATE="$ENABLE_PRE_SCREEN_GATE"' in script
    assert "--skip-deploys" in script
    assert 'payload.get("DATABASE_PUBLIC_URL")' in script
    assert "railway_assert_database_provenance_from_variables_file" in script
    assert "scripts/check_alembic_provenance.py" in script
    assert script.index("railway_assert_database_provenance_from_variables_file") < (
        script.index("railway variable set")
    )
    assert script.index("scripts/check_alembic_provenance.py") < script.index(
        '[sys.executable, "-m", "app.scripts.database_migrate"]'
    )
    assert '[sys.executable, "-m", "app.scripts.database_migrate"]' in script
    assert '[sys.executable, "-m", "alembic", "upgrade", "head"]' not in script


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "30\tfalse"),
        ({"PRE_SCREEN_THRESHOLD": "50", "ENABLE_PRE_SCREEN_GATE": "true"}, "50\ttrue"),
    ],
)
def test_scoring_policy_parser_validates_and_defaults(tmp_path: Path, payload, expected):
    variables_file = tmp_path / "variables.json"
    variables_file.write_text(json.dumps(payload))
    command = (
        f"source {RAILWAY_DIR / 'lib.sh'}; "
        f"railway_scoring_policy_from_file {variables_file}"
    )

    result = subprocess.run(
        ["bash", "-c", command], check=True, capture_output=True, text=True
    )

    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"PRE_SCREEN_THRESHOLD": "101"},
        {"PRE_SCREEN_THRESHOLD": "not-a-number"},
        {"ENABLE_PRE_SCREEN_GATE": "sometimes"},
    ],
)
def test_scoring_policy_parser_rejects_invalid_values(tmp_path: Path, payload):
    variables_file = tmp_path / "variables.json"
    variables_file.write_text(json.dumps(payload))
    command = (
        f"source {RAILWAY_DIR / 'lib.sh'}; "
        f"railway_scoring_policy_from_file {variables_file}"
    )

    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True
    )

    assert result.returncode != 0


def test_worker_wrapper_enforces_split_queue_and_single_beat_topology():
    script = (RAILWAY_DIR / "deploy_worker.sh").read_text()

    assert "TALI_WORKER_QUEUES=celery" in script
    assert "TALI_WORKER_BEAT=true" in script
    assert "TALI_WORKER_QUEUES=scoring" in script
    assert "TALI_WORKER_BEAT=false" in script
    assert script.count("deploy_worker_service") == 3  # definition + two calls
    assert 'cd "$ROOT_DIR"' in script
    assert "railway up \\" in script
    assert "railway up ./backend" not in script
    assert "--path-as-root" not in script
    assert 'cd "$BACKEND_DIR"' not in script


def test_web_wrapper_checks_workers_and_polls_readiness():
    script = (RAILWAY_DIR / "deploy_backend.sh").read_text()

    assert "RAILWAY_STATUS_SCOPE=workers" in script
    assert "railway_wait_for_new_successful_deployment" in script
    assert "railway_wait_for_readiness" in script
    assert "railway_validate_default_agent_capabilities" in script
    assert "railway_validate_service_base_url" in script
    assert script.index("railway_validate_service_base_url") < script.index(
        'railway_wait_for_readiness "$BACKEND_BASE_URL"'
    )
    assert (
        '"$BACKEND_BASE_URL" "$STATUS_FILE" "$ENV_NAME" "$WEB_SERVICE"'
        in script
    )
    assert 'payload.get("ADMIN_SECRET")' in script
    assert script.index("set +x") < script.index('ROOT_DIR="')
    assert "export RAILWAY_ADMIN_SECRET" not in script
    assert 'chmod 600 "$STATUS_FILE" "$WEB_VARIABLES_FILE"' in script
    assert 'cd "$ROOT_DIR"' in script
    assert "railway up \\" in script
    assert "railway up ./backend" not in script
    assert "--path-as-root" not in script
    assert 'cd "$BACKEND_DIR"' not in script


def test_status_wrapper_validates_agent_and_ats_contract_everywhere():
    script = (RAILWAY_DIR / "check_status.sh").read_text()

    assert '"USAGE_METER_LIVE" "true"' in script
    assert '"ATS_PUBLIC_APPLY_ENABLED" "true"' in script
    assert '"BULLHORN_ENABLED" "true"' in script
    assert '"MVP_DISABLE_WORKABLE" "false"' in script
    assert '"TRUST_RAILWAY_X_REAL_IP" "true"' in script


def test_default_agent_capability_gate_covers_assessment_providers():
    script = (RAILWAY_DIR / "lib.sh").read_text()

    for capability in (
        "anthropic_probe_ok",
        "e2b_configured",
        "e2b_probe_ok",
        "resend_probe_ok",
        "github_probe_ok",
        "github_mock_mode=false",
    ):
        assert capability in script
    validator = script.split("railway_validate_default_agent_capabilities()", 1)[1]
    assert '"$base_url/admin/health"' in validator
    assert "X-Admin-Secret: %s" in validator
    assert '"$base_url/ready"' not in validator
    assert validator.index("set +x") < validator.index("RAILWAY_ADMIN_SECRET")
    assert validator.index("railway_validate_service_base_url") < validator.index(
        "auth_header_file=\"$(mktemp)\""
    )
    assert validator.index("unset RAILWAY_ADMIN_SECRET") < validator.index(
        "\n    if ! curl "
    )
    assert "--connect-timeout 5 --max-time 15" in validator
    readiness = script.split("railway_wait_for_readiness()", 1)[1].split(
        "railway_validate_default_agent_capabilities()", 1
    )[0]
    assert "--connect-timeout 5 --max-time 10" in readiness
    assert 'chmod 600 "$health_file" "$auth_header_file"' in validator
    assert 'trap \'rm -f -- "$health_file" "$auth_header_file"\' EXIT' in validator


def test_status_helpers_resolve_environment_specific_service(tmp_path: Path):
    status_file = tmp_path / "status.json"
    status_file.write_text(
        json.dumps(
            {
                "environments": {
                    "edges": [
                        {
                            "node": {
                                "name": "production",
                                "serviceInstances": {
                                    "edges": [
                                        {
                                            "node": {
                                                "serviceName": "web",
                                                "latestDeployment": {
                                                    "id": "deploy-123",
                                                    "status": "SUCCESS",
                                                },
                                                "domains": {
                                                    "serviceDomains": [
                                                        {"domain": "api.example.test"}
                                                    ]
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                }
            }
        )
    )
    command = (
        f"source {RAILWAY_DIR / 'lib.sh'}; "
        f"railway_service_snapshot {status_file} production web; "
        f"railway_service_public_url {status_file} production web"
    )

    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "deploy-123\tSUCCESS",
        "https://api.example.test",
    ]


def _write_service_domain_status(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "environments": {
                    "edges": [
                        {
                            "node": {
                                "name": "production",
                                "serviceInstances": {
                                    "edges": [
                                        {
                                            "node": {
                                                "serviceName": "web",
                                                "domains": {
                                                    "serviceDomains": [
                                                        {"domain": "api.example.test"}
                                                    ],
                                                    "customDomains": [
                                                        {"domain": "custom.example.test"}
                                                    ],
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("https://API.EXAMPLE.TEST/", "https://api.example.test"),
        ("https://custom.example.test", "https://custom.example.test"),
        ("https://api.example.test:443", "https://api.example.test"),
    ],
)
def test_service_base_url_normalizes_only_declared_domains(
    tmp_path: Path,
    raw_url: str,
    expected: str,
) -> None:
    status_file = tmp_path / "status.json"
    _write_service_domain_status(status_file)
    command = (
        'source "$1"; '
        'railway_validate_service_base_url "$2" production web "$3"'
    )

    result = subprocess.run(
        ["bash", "-c", command, "bash", str(RAILWAY_DIR / "lib.sh"), str(status_file), raw_url],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "raw_url",
    [
        "http://api.example.test",
        "https://operator@api.example.test",
        "https://api.example.test/path",
        "https://api.example.test?redirect=https://evil.example",
        "https://api.example.test#fragment",
        "https://evil.example.test",
        " https://api.example.test",
        "https://api.example.test\\@evil.example",
    ],
)
def test_capability_gate_rejects_untrusted_url_before_secret_header_or_curl(
    tmp_path: Path,
    raw_url: str,
) -> None:
    status_file = tmp_path / "status.json"
    _write_service_domain_status(status_file)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "curl-was-called"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        f"#!/usr/bin/env bash\ntouch {marker}\nexit 99\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    command = (
        'source "$1"; '
        'railway_validate_default_agent_capabilities "$2" "$3" production web'
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "RAILWAY_ADMIN_SECRET": "test-admin-secret-that-is-at-least-32-characters",
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            command,
            "bash",
            str(RAILWAY_DIR / "lib.sh"),
            raw_url,
            str(status_file),
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "trusted Railway service URL" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    ("raw_url", "expected_url"),
    [
        ("https://API.EXAMPLE.TEST/", "https://api.example.test/admin/health"),
        ("https://custom.example.test", "https://custom.example.test/admin/health"),
    ],
)
def test_capability_gate_allows_declared_canonical_and_custom_domains(
    tmp_path: Path,
    raw_url: str,
    expected_url: str,
) -> None:
    status_file = tmp_path / "status.json"
    _write_service_domain_status(status_file)
    health_file = tmp_path / "health.json"
    health_file.write_text(
        json.dumps(
            {
                "agent_worker": {
                    "queues": {
                        "celery": {
                            "capabilities": {
                                "anthropic_configured": True,
                                "anthropic_probe_ok": True,
                                "usage_meter_live": True,
                                "e2b_configured": True,
                                "e2b_probe_ok": True,
                                "resend_configured": True,
                                "resend_probe_ok": True,
                                "github_configured": True,
                                "github_probe_ok": True,
                                "github_mock_mode": False,
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "curl-url"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
  if [[ "$arg" == https://* ]]; then
    printf '%s' "$arg" > "$CURL_MARKER"
  fi
done
cat "$FAKE_HEALTH_JSON"
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    command = (
        'source "$1"; '
        'railway_validate_default_agent_capabilities "$2" "$3" production web'
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "RAILWAY_ADMIN_SECRET": "test-admin-secret-that-is-at-least-32-characters",
        "CURL_MARKER": str(marker),
        "FAKE_HEALTH_JSON": str(health_file),
    }

    result = subprocess.run(
        [
            "bash",
            "-c",
            command,
            "bash",
            str(RAILWAY_DIR / "lib.sh"),
            raw_url,
            str(status_file),
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == expected_url


def test_default_agent_capability_gate_fails_closed(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    railway_status_file = tmp_path / "railway-status.json"
    _write_service_domain_status(railway_status_file)
    expected_secret_file = tmp_path / "expected-admin-secret"
    expected_secret_file.write_text(
        "test-admin-secret-that-is-at-least-32-characters",
        encoding="utf-8",
    )
    expected_secret_file.chmod(0o600)
    environment_audit = tmp_path / "child-environment-audit.log"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[[ -z "${RAILWAY_ADMIN_SECRET+x}" ]]
header_file=""
url=""
while (( $# )); do
  case "$1" in
    --header)
      header_file="${2#@}"
      shift 2
      ;;
    http*)
      url="$1"
      shift
      ;;
    *)
      shift
      ;;
  esac
done
[[ "$url" == "https://api.example.test/admin/health" ]]
[[ -n "$header_file" ]]
header_mode="$(stat -c '%a' "$header_file" 2>/dev/null || stat -f '%Lp' "$header_file")"
[[ "$header_mode" == "600" ]]
[[ "$(cat "$header_file")" == "X-Admin-Secret: $(cat "$EXPECTED_ADMIN_SECRET_FILE")" ]]
printf 'curl-clean\nheader-path=%s\n' "$header_file" >> "$CHILD_ENVIRONMENT_AUDIT"
cat "$FAKE_HEALTH_JSON"
"""
    )
    fake_curl.chmod(0o755)
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[[ -z "${RAILWAY_ADMIN_SECRET+x}" ]]
[[ "$1" == "-" && -f "$2" ]]
file_mode="$(stat -c '%a' "$2" 2>/dev/null || stat -f '%Lp' "$2")"
[[ "$file_mode" == "600" ]]
if [[ "$#" == "5" ]]; then
  printf 'python-clean\nstatus-path=%s\n' "$2" >> "$CHILD_ENVIRONMENT_AUDIT"
else
  printf 'python-clean\nhealth-path=%s\n' "$2" >> "$CHILD_ENVIRONMENT_AUDIT"
fi
exec "$REAL_PYTHON" "$@"
"""
    )
    fake_python.chmod(0o755)
    health_file = tmp_path / "health.json"
    capabilities = {
        "anthropic_configured": True,
        "anthropic_probe_ok": True,
        "usage_meter_live": True,
        "e2b_configured": True,
        "e2b_probe_ok": True,
        "resend_configured": True,
        "resend_probe_ok": True,
        "github_configured": True,
        "github_probe_ok": True,
        "github_mock_mode": False,
    }
    health_file.write_text(
        json.dumps(
            {
                "agent_worker": {
                    "queues": {"celery": {"capabilities": capabilities}}
                }
            }
        )
    )
    command = (
        f"source {RAILWAY_DIR / 'lib.sh'}; "
        "railway_validate_default_agent_capabilities "
        f"https://api.example.test {railway_status_file} production web"
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "FAKE_HEALTH_JSON": str(health_file),
        "EXPECTED_ADMIN_SECRET_FILE": str(expected_secret_file),
        "CHILD_ENVIRONMENT_AUDIT": str(environment_audit),
        "REAL_PYTHON": sys.executable,
        "TMPDIR": str(tmp_path),
        "RAILWAY_ADMIN_SECRET": "test-admin-secret-that-is-at-least-32-characters",
    }

    healthy = subprocess.run(
        ["bash", "-x", "-c", command], env=env, capture_output=True, text=True
    )
    assert healthy.returncode == 0, healthy.stderr
    assert env["RAILWAY_ADMIN_SECRET"] not in healthy.stdout
    assert env["RAILWAY_ADMIN_SECRET"] not in healthy.stderr

    capabilities["e2b_probe_ok"] = False
    health_file.write_text(
        json.dumps(
            {
                "agent_worker": {
                    "queues": {"celery": {"capabilities": capabilities}}
                }
            }
        )
    )
    e2b_unhealthy = subprocess.run(
        ["bash", "-c", command], env=env, capture_output=True, text=True
    )
    assert e2b_unhealthy.returncode != 0
    assert "e2b_probe_ok" in e2b_unhealthy.stderr

    capabilities["e2b_probe_ok"] = True
    capabilities["resend_probe_ok"] = False
    health_file.write_text(
        json.dumps(
            {
                "agent_worker": {
                    "queues": {"celery": {"capabilities": capabilities}}
                }
            }
        )
    )
    unhealthy = subprocess.run(
        ["bash", "-c", command], env=env, capture_output=True, text=True
    )
    assert unhealthy.returncode != 0
    assert "resend_probe_ok" in unhealthy.stderr

    audit_lines = environment_audit.read_text(encoding="utf-8").splitlines()
    assert audit_lines.count("curl-clean") == 3
    assert audit_lines.count("python-clean") == 6
    temp_paths = [
        Path(line.split("=", 1)[1])
        for line in audit_lines
        if line.startswith(("header-path=", "health-path="))
    ]
    assert len(temp_paths) == 6
    assert all(not path.exists() for path in temp_paths)


def test_default_agent_capability_gate_requires_admin_secret(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    railway_status_file = tmp_path / "railway-status.json"
    _write_service_domain_status(railway_status_file)
    marker = tmp_path / "curl-was-called"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n")
    fake_curl.chmod(0o755)
    command = (
        f"source {RAILWAY_DIR / 'lib.sh'}; "
        "railway_validate_default_agent_capabilities "
        f"https://api.example.test {railway_status_file} production web"
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
    }

    result = subprocess.run(
        ["bash", "-c", command], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "RAILWAY_ADMIN_SECRET" in result.stderr
    assert not marker.exists()
