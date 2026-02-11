# Re-export from canonical schema location
from ...schemas.candidate import CandidateCreate, CandidateResponse, CandidateUpdate  # noqa: F401

__all__ = ["CandidateCreate", "CandidateResponse", "CandidateUpdate"]
