from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RAILWAY_DIR = ROOT / "scripts" / "railway"
SHELL_FILES = [
    ROOT / "scripts" / "deploy_production.sh",
    ROOT / "scripts" / "release" / "assert_canonical_source.sh",
    ROOT / "scripts" / "release" / "assert_canonical_release.sh",
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


def test_predeploy_pins_metering_and_runs_separate_migrations():
    script = (RAILWAY_DIR / "prepare_production.sh").read_text()

    assert "USAGE_METER_LIVE=true" in script
    assert "ATS_PUBLIC_APPLY_ENABLED=true" in script
    assert "BULLHORN_ENABLED=true" in script
    assert "MVP_DISABLE_WORKABLE=false" in script
    assert "--skip-deploys" in script
    assert 'payload.get("DATABASE_PUBLIC_URL")' in script
    assert '[sys.executable, "-m", "alembic", "upgrade", "head"]' in script
    assert '[sys.executable, "-m", "alembic", "current"]' in script


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
    assert "railway up ./backend" in script
    assert "--path-as-root" in script
    assert 'cd "$BACKEND_DIR"' not in script


def test_status_wrapper_validates_agent_and_ats_contract_everywhere():
    script = (RAILWAY_DIR / "check_status.sh").read_text()

    assert '"USAGE_METER_LIVE" "true"' in script
    assert '"ATS_PUBLIC_APPLY_ENABLED" "true"' in script
    assert '"BULLHORN_ENABLED" "true"' in script
    assert '"MVP_DISABLE_WORKABLE" "false"' in script


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
    fake_curl.write_text("#!/usr/bin/env bash\ncat \"$FAKE_HEALTH_JSON\"\n")
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
