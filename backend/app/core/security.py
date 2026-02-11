# Re-export shim – canonical location is platform.security
from ..platform.security import (  # noqa: F401
    pwd_context,
    oauth2_scheme,
    verify_password,
    get_password_hash,
    create_access_token,
    decode_token,
    get_current_user,
)

__all__ = [
    "pwd_context",
    "oauth2_scheme",
    "verify_password",
    "get_password_hash",
    "create_access_token",
    "decode_token",
    "get_current_user",
]
