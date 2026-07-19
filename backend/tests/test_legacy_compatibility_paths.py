"""Safety contracts for deliberately retained historical import paths."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.capabilities._stub_helpers import (
    CapabilityContext,
    CapabilityUnavailableError,
)
from app.capabilities.bias_monitor_continuous import audit_streaming
from app.capabilities.capability_auditor import audit_capabilities
from app.capabilities.causal_mode import decide_causal_mode
from app.capabilities.portfolio_agent import contribute
from app.components.scoring.schemas import FraudResult, ScoringResult
from app.services.credit_ledger_service import (
    DeprecatedCreditLedgerAPIError,
    append_credit_ledger_entry,
)


@pytest.mark.parametrize(
    ("call", "capability"),
    [
        (lambda ctx: audit_streaming(ctx), "bias_monitor_continuous"),
        (lambda ctx: audit_capabilities(ctx), "capability_auditor"),
        (
            lambda ctx: decide_causal_mode(ctx, features={"score": 1.0}),
            "causal_mode",
        ),
        (lambda ctx: contribute(ctx), "portfolio_agent"),
    ],
)
def test_unavailable_capability_apis_fail_closed(call, capability: str) -> None:
    ctx = CapabilityContext(
        db=object(),  # type: ignore[arg-type]
        organization_id=1,
        decision_id="compatibility-test",
    )

    with pytest.raises(CapabilityUnavailableError) as exc_info:
        call(ctx)

    assert exc_info.value.capability == capability
    assert exc_info.value.reason


def test_scoring_schema_defaults_are_isolated_and_safe() -> None:
    first = FraudResult()
    second = FraudResult()
    first.flags.append("test")

    result = ScoringResult(final_score=72.5)

    assert second.flags == []
    assert result.component_scores == {}
    assert result.fraud.flags == []
    assert result.v2.enabled is False


def test_generic_credit_ledger_facade_rejects_before_touching_session() -> None:
    db = MagicMock()
    organization = SimpleNamespace(id=1, credits_balance=100)

    with pytest.raises(DeprecatedCreditLedgerAPIError):
        append_credit_ledger_entry(
            db,
            organization=organization,  # type: ignore[arg-type]
            delta=-10,
            reason="legacy",
        )

    assert db.mock_calls == []
    assert organization.credits_balance == 100


def test_legacy_demo_seeder_is_credential_free_and_has_no_side_effects(
    capsys,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "scripts" / "seed_data.py"
    source = path.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("legacy_seed_data", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "demo1234" not in source
    assert "hashed_password" not in source
    assert module.main() == 2
    assert "made no changes" in capsys.readouterr().err
    with pytest.raises(module.LegacySeederUnavailableError):
        module.seed()
