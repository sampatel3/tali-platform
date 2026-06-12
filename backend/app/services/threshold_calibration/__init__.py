"""Terminal-outcome-driven role_fit threshold calibration.

Learns the advance/reject cut on the RAW role_fit score from recruiters'
actual terminal decisions (advanced/hired = positive, rejected = negative),
bias-gates it, and proposes it for review. The objective score stays raw —
only the policy boundary is learned. See ``service.run_for_all_orgs``.
"""
