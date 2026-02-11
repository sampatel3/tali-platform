# Re-export shim – canonical location is platform.database
from ..platform.database import engine, SessionLocal, Base, get_db  # noqa: F401

__all__ = ["engine", "SessionLocal", "Base", "get_db"]
