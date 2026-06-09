"""Curated public API surface (``/public/v1``).

A deliberately small, frozen, versioned contract that external services and
the Workable provider consume — decoupled from the internal ``/api/v1``
routers so app refactors never break it. Authenticated with a Taali API key.
"""
from .router import router

__all__ = ["router"]
