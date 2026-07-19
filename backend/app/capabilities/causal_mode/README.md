# Causal mode compatibility API

This package preserves the historical import path only. The registry marks
`causal_mode` unavailable until causal inference and claim validation are
implemented. Calling the compatibility API fails closed; it never emits empty
claims that could be mistaken for a completed causal evaluation.
