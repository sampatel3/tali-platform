from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

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


@pytest.mark.parametrize("script", SHELL_FILES)
def test_railway_shell_scripts_are_executable_and_syntax_valid(script: Path):
    assert os.access(script, os.X_OK), f"{script} must be executable"
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_shared_railway_config_has_no_http_healthcheck():
    payload = json.loads((ROOT / "backend" / "railway.json").read_text())
    deploy = payload["deploy"]

    assert "healthcheckPath" not in deploy
    assert "healthcheckTimeout" not in deploy


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
    assert "railway up ./backend" in script
    assert "--path-as-root" in script
    assert 'cd "$BACKEND_DIR"' not in script


def test_web_wrapper_checks_workers_and_polls_readiness():
    script = (RAILWAY_DIR / "deploy_backend.sh").read_text()

    assert "RAILWAY_STATUS_SCOPE=workers" in script
    assert "railway_wait_for_new_successful_deployment" in script
    assert "railway_wait_for_readiness" in script
    assert "railway_validate_default_agent_capabilities" in script
    assert 'payload.get("ADMIN_SECRET")' in script
    assert "railway up ./backend" in script
    assert "--path-as-root" in script
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
        "resend_probe_ok",
        "github_probe_ok",
        "github_mock_mode=false",
    ):
        assert capability in script
    validator = script.split("railway_validate_default_agent_capabilities()", 1)[1]
    assert '"$base_url/admin/health"' in validator
    assert "X-Admin-Secret: %s" in validator
    assert '"$base_url/ready"' not in validator


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


def test_default_agent_capability_gate_fails_closed(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
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
[[ "$(cat "$header_file")" == "X-Admin-Secret: $RAILWAY_ADMIN_SECRET" ]]
cat "$FAKE_HEALTH_JSON"
"""
    )
    fake_curl.chmod(0o755)
    health_file = tmp_path / "health.json"
    capabilities = {
        "anthropic_configured": True,
        "anthropic_probe_ok": True,
        "usage_meter_live": True,
        "e2b_configured": True,
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
        "railway_validate_default_agent_capabilities https://api.example.test"
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "FAKE_HEALTH_JSON": str(health_file),
        "RAILWAY_ADMIN_SECRET": "test-admin-secret-that-is-at-least-32-characters",
    }

    healthy = subprocess.run(
        ["bash", "-c", command], env=env, capture_output=True, text=True
    )
    assert healthy.returncode == 0, healthy.stderr

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


def test_default_agent_capability_gate_requires_admin_secret(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "curl-was-called"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n")
    fake_curl.chmod(0o755)
    command = (
        f"source {RAILWAY_DIR / 'lib.sh'}; "
        "railway_validate_default_agent_capabilities https://api.example.test"
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
