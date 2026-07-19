# Capability auditor compatibility API

This package preserves the historical import path only. The registry marks
`capability_auditor` unavailable because it does not yet produce findings.
Calling this compatibility API fails closed instead of returning a misleading
empty audit.
