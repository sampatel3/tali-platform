"""CV match eval harness.

Run from the backend dir::

    python -m app.cv_matching.evals.run_evals

Exits 0 if all golden cases pass; non-zero otherwise. Snapshots results to
``baseline_results/{prompt_version}_{timestamp}.json`` for later diffing
against new prompt versions.

Per the handover: only run on prompt version changes, not every commit.
"""
