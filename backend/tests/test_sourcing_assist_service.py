"""Pure-function tests for the deterministic sourcing-search builders.

No LLM, no DB — these cover quoting, the Google term cap, boolean OR branches,
self-referential-criterion stripping, and location extraction from Workable data.
"""
from types import SimpleNamespace

from app.models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from app.services.sourcing_assist_service import (
    build_boolean_string,
    build_deterministic_block,
    build_xray_string,
    must_have_terms,
    role_location,
)


def _chip(text, bucket=BUCKET_MUST, ordering=0, deleted_at=None):
    return SimpleNamespace(
        text=text, bucket=bucket, ordering=ordering, deleted_at=deleted_at
    )


def _role(*, name="Senior Data Engineer", criteria=None, workable_job_data=None, job_spec_text=None):
    return SimpleNamespace(
        id=7,
        organization_id=1,
        name=name,
        criteria=criteria or [],
        workable_job_data=workable_job_data,
        job_spec_text=job_spec_text,
    )


# ---- quoting + term cap ----------------------------------------------------


def test_xray_quotes_multiword_terms_and_prefixes_site():
    s = build_xray_string("Senior Data Engineer", "London", ["Apache Spark", "dbt"])
    assert s.startswith('site:linkedin.com/in ')
    assert '"Senior Data Engineer"' in s
    assert '"Apache Spark"' in s
    assert '"dbt"' in s
    assert '"London"' in s


def test_xray_caps_skills_at_four():
    skills = ["Spark", "dbt", "AWS", "Airflow", "Kafka", "Snowflake"]
    s = build_xray_string("Engineer", "Berlin", skills)
    # Only the first four skills survive the Google term cap.
    assert '"Airflow"' in s
    assert '"Kafka"' not in s
    assert '"Snowflake"' not in s
    # Location still present after the capped skills.
    assert '"Berlin"' in s


def test_xray_omits_empty_title_and_location():
    s = build_xray_string("", "", ["Python"])
    assert s == 'site:linkedin.com/in "Python"'


# ---- boolean OR branches ---------------------------------------------------


def test_boolean_plain_when_no_expansion():
    s = build_boolean_string("Data Engineer", ["Spark", "dbt"])
    assert s == '"Data Engineer" AND "Spark" AND "dbt"'


def test_boolean_adds_title_synonyms_and_skill_variants():
    s = build_boolean_string(
        "Data Engineer",
        ["Spark", "dbt"],
        title_synonyms=["Analytics Engineer", "Data Engineer"],  # dup dropped
        skill_variants={"dbt": ["data build tool"], "Spark": ["Spark"]},  # self-variant dropped
    )
    assert '("Data Engineer" OR "Analytics Engineer")' in s
    # Duplicate title synonym (case-insensitive) not repeated.
    assert s.count('"Data Engineer"') == 1
    assert '("dbt" OR "data build tool")' in s
    # Spark had only a self-referential variant → stays a plain term.
    assert 'AND "Spark"' in s


def test_boolean_caps_skills_at_four():
    s = build_boolean_string("Eng", ["a", "b", "c", "d", "e"])
    assert '"d"' in s
    assert '"e"' not in s


# ---- self-referential stripping --------------------------------------------


def test_must_have_terms_strips_self_referential_taali_score():
    role = _role(
        criteria=[
            _chip("Python", ordering=0),
            _chip("Taali score >= 60", ordering=1),
            _chip("Postgres", ordering=2),
        ]
    )
    terms = must_have_terms(role)
    assert terms == ["Python", "Postgres"]


def test_must_have_terms_only_must_bucket_and_no_deleted_or_dupes():
    role = _role(
        criteria=[
            _chip("Python", bucket=BUCKET_MUST, ordering=0),
            _chip("Kubernetes", bucket=BUCKET_PREFERRED, ordering=1),
            _chip("Onsite", bucket=BUCKET_CONSTRAINT, ordering=2),
            _chip("python", bucket=BUCKET_MUST, ordering=3),  # case-dup
            _chip("Deleted", bucket=BUCKET_MUST, ordering=4, deleted_at="2026-01-01"),
        ]
    )
    assert must_have_terms(role) == ["Python"]


# ---- location extraction ---------------------------------------------------


def test_role_location_from_location_str():
    role = _role(workable_job_data={"location": {"location_str": "London, United Kingdom"}})
    assert role_location(role) == "London, United Kingdom"


def test_role_location_from_city_country():
    role = _role(workable_job_data={"location": {"city": "Dubai", "country": "UAE"}})
    assert role_location(role) == "Dubai, UAE"


def test_role_location_from_bare_string_and_missing():
    assert role_location(_role(workable_job_data={"location": "Remote"})) == "Remote"
    assert role_location(_role(workable_job_data=None)) == ""
    assert role_location(_role(workable_job_data={})) == ""


# ---- full deterministic block ----------------------------------------------


def test_build_deterministic_block_end_to_end():
    role = _role(
        name="Senior Data Engineer",
        workable_job_data={"location": {"location_str": "London"}},
        criteria=[
            _chip("Apache Spark", ordering=0),
            _chip("Taali score >= 70", ordering=1),  # stripped
            _chip("dbt", ordering=2),
        ],
    )
    block = build_deterministic_block(role)
    assert block["xray"] == (
        'site:linkedin.com/in "Senior Data Engineer" "Apache Spark" "dbt" "London"'
    )
    assert block["boolean"] == '"Senior Data Engineer" AND "Apache Spark" AND "dbt"'
