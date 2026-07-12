"""Unit tests for the deterministic distribution artefact core (no DB, no LLM).

Covers the LinkedIn post shape + apply URL, the JD excerpt truncation, the
share-intent URL encoding, and a valid, parseable JobPosting XML feed (incl. the
empty-org case)."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from app.models.job_page import JobPage
from app.services.distribution_service import (
    build_distribution_artefacts,
    build_job_posting_feed_xml,
    build_linkedin_post,
    build_share_urls,
    jd_excerpt,
)

APPLY_URL = "https://app.example.com/job/abc123"
FEED_URL = "https://api.example.com/api/v1/public/careers/acme/feed.xml"


def _page(**kw) -> JobPage:
    base = dict(
        token="abc123",
        title="Senior Backend Engineer",
        jd_markdown="# Role\n\nBuild and own the payments API.",
        location="Dubai, UAE",
        employment_type="full_time",
        published_at=datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return JobPage(**base)


# ---- jd_excerpt ------------------------------------------------------------


def test_jd_excerpt_strips_markdown_and_is_plain():
    out = jd_excerpt("# Heading\n\n- **Bold** item\n- second [link](http://x)")
    assert "#" not in out
    assert "**" not in out
    assert "Bold item" in out
    assert "link" in out and "http://x" not in out


def test_jd_excerpt_truncates_on_word_boundary_with_ellipsis():
    text = "word " * 400  # ~2000 chars, well over the cap
    out = jd_excerpt(text, max_chars=100)
    assert out.endswith("…")
    assert len(out) <= 101
    # Never slices a word: the char before the ellipsis is a full word.
    assert not out[:-1].endswith("wor")


def test_jd_excerpt_empty_is_empty():
    assert jd_excerpt(None) == ""
    assert jd_excerpt("") == ""


# ---- linkedin post ---------------------------------------------------------


def test_linkedin_post_contains_title_meta_and_apply_url():
    post = build_linkedin_post(
        title="Senior Backend Engineer",
        org_name="Acme",
        location="Dubai, UAE",
        employment_type="full_time",
        excerpt="Build the payments API.",
        apply_url=APPLY_URL,
    )
    assert "Senior Backend Engineer" in post
    assert "Acme" in post
    assert "Dubai, UAE" in post
    assert "full_time" in post
    assert f"Apply here: {APPLY_URL}" in post


def test_linkedin_post_omits_missing_optional_fields():
    post = build_linkedin_post(
        title="Recruiter",
        org_name=None,
        location=None,
        employment_type=None,
        excerpt="",
        apply_url=APPLY_URL,
    )
    assert "Recruiter" in post
    assert APPLY_URL in post
    assert "None" not in post  # no stringified None leaked in


# ---- share urls ------------------------------------------------------------


def test_share_urls_encode_the_apply_url():
    urls = build_share_urls(apply_url=APPLY_URL, title="Senior Backend Engineer")
    # LinkedIn share-offsite carries the apply URL percent-encoded.
    assert urls["linkedin"].startswith(
        "https://www.linkedin.com/sharing/share-offsite/?url="
    )
    assert "https%3A%2F%2Fapp.example.com%2Fjob%2Fabc123" in urls["linkedin"]
    assert urls["apply_url"] == APPLY_URL
    # Mailto: subject + body both present, body carries the raw URL.
    assert urls["email"].startswith("mailto:?")
    qs = parse_qs(urlparse(urls["email"]).query)
    assert "Senior Backend Engineer" in qs["subject"][0]
    assert APPLY_URL in qs["body"][0]


# ---- assembled artefacts ---------------------------------------------------


def test_build_distribution_artefacts_shape():
    out = build_distribution_artefacts(
        _page(), apply_url=APPLY_URL, feed_url=FEED_URL, org_name="Acme"
    )
    assert out["published"] is True
    assert out["apply_url"] == APPLY_URL
    assert out["feed_url"] == FEED_URL
    assert out["title"] == "Senior Backend Engineer"
    assert APPLY_URL in out["linkedin_post"]
    assert out["share_urls"]["apply_url"] == APPLY_URL


# ---- job-posting feed XML --------------------------------------------------


def test_feed_xml_is_valid_and_carries_items():
    pages = [
        _page(token="t1", title="Backend Engineer", jd_markdown="Build APIs & things <fast>"),
        _page(token="t2", title="Data Analyst", location="Remote", employment_type="part_time"),
    ]
    xml = build_job_posting_feed_xml(
        org_name="Acme",
        feed_self_url=FEED_URL,
        pages=pages,
        apply_url_for=lambda p: f"https://app.example.com/job/{p.token}",
    )
    root = ET.fromstring(xml)  # parses = well-formed (raises otherwise)
    jobs = root.findall("job")
    assert len(jobs) == 2
    first = jobs[0]
    assert first.find("title").text == "Backend Engineer"
    assert first.find("link").text == "https://app.example.com/job/t1"
    # The unescaped '&'/'<' in the JD did not break parsing and round-trips.
    assert "&" in first.find("description").text
    # Optional fields present where the page has them.
    assert jobs[1].find("location").text == "Remote"
    assert jobs[1].find("jobType").text == "part_time"


def test_feed_xml_empty_org_is_valid_and_has_no_jobs():
    xml = build_job_posting_feed_xml(
        org_name=None,
        feed_self_url=None,
        pages=[],
        apply_url_for=lambda p: "x",
    )
    root = ET.fromstring(xml)
    assert root.findall("job") == []
