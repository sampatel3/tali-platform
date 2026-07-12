"""Role distribution assist — copy-paste artefacts + a job-board XML feed.

We help a recruiter push a PUBLISHED role OUT to LinkedIn and the job boards
WITHOUT any LinkedIn API, scraping, or automation: everything here is either a
copy-paste artefact the recruiter posts by hand, or a public XML feed the boards
(Google Jobs / Indeed) pull on their own schedule. Mirrors
``sourcing_assist_service`` — a deterministic, unit-testable core is the product;
there is no live posting.

Every artefact points at the role's EXISTING public job page (``/job/{token}``,
served with no auth) — the same URL the recruiter already shares. Nothing new is
exposed; distribution just makes that public page easier to spread.

Two surfaces:
- ``build_distribution_artefacts`` — a LinkedIn post draft, share-intent URLs
  (LinkedIn share-offsite, a mailto email, and the raw apply link), and the org
  careers-board feed URL, all built from a published ``JobPage`` snapshot.
- ``build_job_posting_feed_xml`` — a valid ``JobPosting``-schema RSS/XML document
  built from the SAME open job pages the public careers board serves, for boards
  to pull.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlencode

from ..models.job_page import JobPage

# A LinkedIn post reads best with a short JD taste, not the whole spec — the
# recruiter edits from there. Cap the excerpt so the copy-paste block stays tight.
_JD_EXCERPT_CHARS = 600


# ---------------------------------------------------------------------------
# Deterministic core (pure — no DB, no network, unit-testable)
# ---------------------------------------------------------------------------


def _plain_text(markdown: Optional[str]) -> str:
    """Best-effort markdown → plain text for a JD excerpt.

    We don't render markdown; we just strip the common syntax so a LinkedIn post
    or a feed description reads cleanly (headings, emphasis, list bullets, links).
    """
    text = (markdown or "").replace("\r\n", "\n")
    # Links: [label](url) -> label
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # Images: ![alt](url) -> alt (after the link rule the leading ! may remain)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    out_lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        line = re.sub(r"^#{1,6}\s*", "", line)  # headings
        line = re.sub(r"^[-*+]\s+", "• ", line)  # unordered list bullets
        line = re.sub(r"^\d+\.\s+", "• ", line)  # ordered list bullets
        line = re.sub(r"[*_`]{1,3}([^*_`]+)[*_`]{1,3}", r"\1", line)  # emphasis
        line = re.sub(r"[*_`>]", "", line)  # stray markers / blockquote
        out_lines.append(line)
    # Collapse 3+ blank lines to a single blank line.
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines))
    return collapsed.strip()


def jd_excerpt(markdown: Optional[str], *, max_chars: int = _JD_EXCERPT_CHARS) -> str:
    """A short plain-text taste of the JD, truncated on a word boundary.

    Returns ``""`` for an empty JD. When the plain text is longer than
    ``max_chars`` it is cut at the last whitespace before the limit and an
    ellipsis is appended, so a word is never sliced in half.
    """
    text = _plain_text(markdown)
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    cut = clipped.rfind(" ")
    if cut > 0:
        clipped = clipped[:cut]
    return clipped.rstrip() + "…"


def _job_meta_line(location: Optional[str], employment_type: Optional[str]) -> str:
    """A single '· '-joined meta line (location · type), omitting empty parts."""
    parts = [
        p.strip()
        for p in (location, employment_type)
        if isinstance(p, str) and p.strip()
    ]
    return " · ".join(parts)


def build_linkedin_post(
    *,
    title: Optional[str],
    org_name: Optional[str],
    location: Optional[str],
    employment_type: Optional[str],
    excerpt: str,
    apply_url: str,
) -> str:
    """A formatted, copy-paste-ready LinkedIn job post (plain text, editable).

    Structure: headline (We're hiring / role), the poster, a meta line, a short
    JD taste, and the apply call-to-action pointing at the public job page.
    """
    title = (title or "This role").strip()
    lines: list[str] = []
    lines.append(f"We're hiring: {title}")
    org_name = (org_name or "").strip()
    if org_name:
        lines.append(f"at {org_name}")
    meta = _job_meta_line(location, employment_type)
    if meta:
        lines.append(meta)
    lines.append("")
    if excerpt:
        lines.append(excerpt)
        lines.append("")
    lines.append(f"Apply here: {apply_url}")
    # Collapse a possible trailing blank before the CTA.
    return "\n".join(lines).strip()


def build_share_urls(*, apply_url: str, title: Optional[str]) -> dict[str, str]:
    """Share-intent URLs for the public apply page.

    - ``linkedin`` — LinkedIn's share-offsite intent (opens the composer with the
      page pre-attached; no API, no auth).
    - ``email`` — a ``mailto:`` with a subject and a body that includes the link.
    - ``apply_url`` — the raw public page, for a plain copy.
    """
    encoded_url = quote(apply_url, safe="")
    title = (title or "this role").strip() or "this role"
    subject = f"Job opportunity: {title}"
    body = f"Have a look at this role and apply here:\n{apply_url}"
    # RFC-6068 mailto: percent-encode (space → %20, not +) so clients don't show
    # a literal '+' in the subject/body.
    mailto = "mailto:?" + urlencode({"subject": subject, "body": body}, quote_via=quote)
    return {
        "linkedin": f"https://www.linkedin.com/sharing/share-offsite/?url={encoded_url}",
        "email": mailto,
        "apply_url": apply_url,
    }


def build_distribution_artefacts(
    page: JobPage,
    *,
    apply_url: str,
    feed_url: Optional[str],
    org_name: Optional[str],
) -> dict[str, Any]:
    """Assemble the deterministic distribution artefacts for a published page."""
    excerpt = jd_excerpt(page.jd_markdown)
    linkedin_post = build_linkedin_post(
        title=page.title,
        org_name=org_name,
        location=page.location,
        employment_type=page.employment_type,
        excerpt=excerpt,
        apply_url=apply_url,
    )
    return {
        "published": True,
        "apply_url": apply_url,
        "title": page.title,
        "linkedin_post": linkedin_post,
        "share_urls": build_share_urls(apply_url=apply_url, title=page.title),
        "feed_url": feed_url,
    }


# ---------------------------------------------------------------------------
# Job-board XML feed (JobPosting-schema RSS) — public, mirrors the careers board
# ---------------------------------------------------------------------------


def _feed_item(page: JobPage, *, apply_url: str) -> str:
    """One ``<job>`` element (Indeed / Google Jobs ``JobPosting`` fields).

    All free text is HTML-escaped so a JD with ``&``/``<`` can't break the XML.
    The stable public URL is the item link + guid.
    """
    def esc(value: Optional[str]) -> str:
        return html.escape((value or "").strip())

    fields: list[str] = [
        f"<title>{esc(page.title)}</title>",
        f"<link>{esc(apply_url)}</link>",
        f"<guid isPermaLink=\"true\">{esc(apply_url)}</guid>",
        f"<description>{esc(_plain_text(page.jd_markdown))}</description>",
    ]
    if page.location:
        fields.append(f"<location>{esc(page.location)}</location>")
    if page.employment_type:
        fields.append(f"<jobType>{esc(page.employment_type)}</jobType>")
    if isinstance(page.published_at, datetime):
        # RFC-822 date, the format Indeed / Google Jobs RSS expect.
        fields.append(
            f"<pubDate>{page.published_at.strftime('%a, %d %b %Y %H:%M:%S %z') or ''}</pubDate>"
        )
    return "    <job>\n" + "\n".join(f"      {f}" for f in fields) + "\n    </job>"


def build_job_posting_feed_xml(
    *,
    org_name: Optional[str],
    feed_self_url: Optional[str],
    pages: Iterable[JobPage],
    apply_url_for: Any,
) -> str:
    """A valid ``JobPosting``-schema RSS document for the org's open pages.

    ``apply_url_for(page)`` returns each page's public ``/job/{token}`` URL. An
    org with no open pages yields a valid, empty ``<channel>`` (never an error).
    """
    org_name = (org_name or "Careers").strip() or "Careers"
    channel_title = html.escape(f"{org_name} — open roles")
    self_link = html.escape((feed_self_url or "").strip())
    items = "\n".join(
        _feed_item(page, apply_url=apply_url_for(page)) for page in pages
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<source>\n'
        f"  <publisher>{channel_title}</publisher>\n"
        f"  <publisherurl>{self_link}</publisherurl>\n"
    )
    if items:
        body += items + "\n"
    body += "</source>\n"
    return body
