"""Database URL selection shared by web and worker runtimes."""

from __future__ import annotations

import os


def runtime_database_url(configured_url: str) -> str:
    """Select the reachable database URL for this process.

    Deployed Railway replicas can resolve ``*.railway.internal`` and should
    use the private URL. ``railway run`` executes locally with service
    variables injected, so it must keep preferring the public proxy instead.
    ``RAILWAY_REPLICA_ID`` is supplied only to deployed replicas.
    """
    private_url = (os.environ.get("DATABASE_URL") or "").strip()
    public_url = (os.environ.get("DATABASE_PUBLIC_URL") or "").strip()
    configured = (configured_url or "").strip()
    deployed_replica = bool((os.environ.get("RAILWAY_REPLICA_ID") or "").strip())
    candidates = (
        (private_url, public_url, configured)
        if deployed_replica
        else (public_url, private_url, configured)
    )
    for candidate in candidates:
        if candidate:
            return candidate
    raise RuntimeError("DATABASE_URL is not configured")


__all__ = ["runtime_database_url"]
