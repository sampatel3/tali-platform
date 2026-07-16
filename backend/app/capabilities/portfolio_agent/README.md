# Portfolio capability compatibility API

This package preserves the historical import path only. The registry marks
`portfolio_agent` unavailable until cohort feature computation is implemented.
Calling the compatibility API fails closed instead of returning zero-valued
features that could silently influence a policy vector.
