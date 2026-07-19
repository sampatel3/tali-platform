from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_platform_health_contract_import_is_graphiti_independent() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.platform.health_contracts; "
                "assert not any(name == 'graphiti_core' or "
                "name.startswith('graphiti_core.') for name in sys.modules); "
                "assert not any(name == 'app.candidate_graph' or "
                "name.startswith('app.candidate_graph.') for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(BACKEND_ROOT)},
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_candidate_graph_health_contracts_remain_a_compatibility_reexport() -> None:
    from app.candidate_graph import health_contracts as compatibility
    from app.platform import health_contracts as canonical

    assert compatibility.AdminHealthResponse is canonical.AdminHealthResponse
    assert (
        compatibility.AgentWorkerHealthResponse
        is canonical.AgentWorkerHealthResponse
    )
    assert compatibility.S3HealthResponse is canonical.S3HealthResponse
    assert compatibility.GraphitiHealthResponse is canonical.GraphitiHealthResponse
    assert compatibility.ADMIN_HEALTH_OPENAPI is canonical.ADMIN_HEALTH_OPENAPI
    assert compatibility.GRAPHITI_HEALTH_OPENAPI is canonical.GRAPHITI_HEALTH_OPENAPI
