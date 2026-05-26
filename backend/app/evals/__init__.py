"""Shared evaluation utilities.

Lives at the platform layer (not under any one pipeline) so any pipeline
that needs golden-case scoring, agreement metrics, or calibration error
measurement can plug in without each one reinventing the math.

Today: pure-Python metrics (rank correlation, kappa, ECE, etc.). The
pipeline-specific harness shape (golden loader → run → metrics → baseline
diff) still lives next to its consumer (e.g. ``cv_matching/evals``); it
will be extracted here once a second pipeline has its own evals to share.
"""
