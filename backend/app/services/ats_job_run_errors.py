"""Typed errors for durable ATS operation publication."""


class AtsJobRunPersistenceError(RuntimeError):
    """Durable tracking failed before an ATS operation could be published."""

    def __init__(self, op_type: str):
        self.op_type = str(op_type or "unknown")
        super().__init__(
            f"could not persist BackgroundJobRun for ATS operation {self.op_type!r}"
        )


__all__ = ["AtsJobRunPersistenceError"]
