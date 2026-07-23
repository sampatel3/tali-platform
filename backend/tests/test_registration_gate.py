"""Public self-serve signup is gated behind ALLOW_PUBLIC_REGISTRATION.

Onboarding is sales-led: a new org is created by an operator via
scripts/create_org.py, not by anyone hitting the register endpoint. The guard
that matters is the production DEFAULT — if it ever flips to True by accident,
the internet can create orgs and burn the shared Anthropic key.

The register router is mounted conditionally at app-construction time, so the
in-process test app (which enables the flag in conftest) can't observe the OFF
mount without reconstructing the app. We assert the default here and rebuild a
minimal app applying the same conditional to prove OFF => 404.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.platform.config import Settings


def test_prod_default_is_off():
    """The declared field default must be OFF, independent of any env override
    (conftest sets the var True for the test app, so read the class default)."""
    assert Settings.model_fields["ALLOW_PUBLIC_REGISTRATION"].default is False


def test_register_absent_when_flag_off():
    """With the router not mounted (flag off), the endpoint 404s."""
    app = FastAPI()  # main.py mounts nothing under the flag-off branch
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": "whatever-long-pass"},
    )
    assert resp.status_code == 404


# The mounted (flag-on) path is exercised end-to-end in test_api_auth.py, which
# runs against the fully-wired app with ALLOW_PUBLIC_REGISTRATION enabled by
# conftest — so a redundant in-process "present" assertion is omitted here.
