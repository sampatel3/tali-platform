# Archetype rubrics

Each YAML in this directory describes one role-family archetype the
v4.2 matching pipeline knows about. The runtime cosine-matches a JD
against archetype centroid embeddings; the closest archetype's rubric
is injected into the prompt to ground per-requirement `match_tier`
classification and adjust dimension weights.

## File layout

```
rubrics/
├── __init__.py                           — re-exports loader
├── schema.py                             — Pydantic schema (validate-on-load)
├── README.md                             — this file
├── _generation_prompt.md                 — Sonnet prompt that produced the YAMLs
├── aws_glue_data_engineer.yaml           — first archetype (committed)
├── genai_engineer.yaml                   — TODO (RALPH 2.6)
└── senior_swe_backend.yaml               — TODO (RALPH 2.6)
```

`_*.md` files are tooling, not archetypes. Filenames starting with
underscore are skipped by `list_rubrics()`.

## Naming

`{role_family}.yaml`, all-lowercase snake_case. The filename (minus
`.yaml`) **must equal** the file's `archetype_id` field. Loader
reads by `archetype_id`, so a mismatch silently breaks routing.

## Schema

The full schema is in [schema.py](schema.py); the short version:

```yaml
archetype_id: aws_glue_data_engineer
description: |
  Senior data engineer who owns AWS-native ETL pipelines (Glue/EMR/Athena),
  partners with analytics/ML teams, and writes Python + SQL daily.

# Canonical JD prose for this archetype. Embedded once at startup; runtime
# routes incoming JDs by cosine to this centroid.
jd_centroid_text: |
  We're hiring a Senior Data Engineer to own our AWS Glue / Spark ETL
  platform. You'll design schemas, optimise jobs, and partner with
  analytics teams to ship reliable daily pipelines.

must_have_archetypes:
  - cluster: managed_spark_etl
    description: AWS Glue, Databricks, EMR — managed Spark with metadata catalog
    exact_matches: [AWS Glue, Glue ETL]
    strong_substitutes: [Databricks, EMR, Synapse Spark]
    weak_substitutes: [self-managed Spark, Hadoop MapReduce]
    unrelated: [pandas-only ETL, dbt-only stack]

depth_signals:
  skills_depth:
    - "owned a production Glue pipeline at >1TB/day"
    - "led a job-cost optimisation"
  industry_match:
    - "regulated industry data engineering (banking, healthcare, gov)"

seniority_anchors:
  band_100: |
    Maintainer-level: led a multi-team Glue/Spark migration, named in talks
    or open-source, owns SLA for a tier-1 pipeline.
  band_75: |
    Owns end-to-end Glue ETL for a product surface; comfortable with
    Athena tuning, Step Functions, IAM least-privilege.
  band_50: |
    Has shipped Glue jobs but as a contributor; relies on team for
    deeper Spark tuning or schema-evolution decisions.
  band_25: |
    Adjacent stack (Databricks, EMR) but no Glue; or Glue exposure
    limited to <1 year of read-only contributions.
  band_0: |
    Wrong stack entirely (Snowflake-pure analyst, BI dashboard owner).

dimension_weights:
  skills_coverage: 0.25
  skills_depth: 0.25
  title_trajectory: 0.10
  seniority_alignment: 0.15
  industry_match: 0.15
  tenure_pattern: 0.10
```

## Lifecycle

1. **Generation (one-shot, offline):** Sonnet is called once per
   archetype with 3-5 anonymised JDs from that role family using the
   prompt template in `_generation_prompt.md`. The output YAML is
   committed here. Re-generate only when:
   - the role family meaningfully shifts (e.g. "data engineer"
     reorienting from EMR-centric to dbt+Snowflake)
   - a new substitution pattern is observed in recruiter overrides

2. **Validation (on every PR):** `ArchetypeRubric.model_validate`
   runs against every `*.yaml` file in CI. A typo = red build.

3. **Runtime:** `list_rubrics()` yields all archetypes. The runner
   embeds each archetype's `jd_centroid_text` and caches the centroid;
   incoming JDs are cosine-matched. If no centroid passes the
   threshold, the generic v4.1 prompt is used as fallback.

## Manual review

Sam reviews each newly-generated rubric for technical correctness
(the must-have substitutions in particular) before promoting. The
generation prompt deliberately produces an over-broad starting set
of substitutes — manual pruning happens here, not in code.
