# Workable API Response Samples

Reference for SPI v3 response shapes. Run `python scripts/workable_qa_diagnostic.py sampatel@deeplight.ae` with DB + credentials to capture live samples.

## GET /jobs (list jobs)

```json
{
  "jobs": [
    {
      "id": "abc123",
      "shortcode": "ABC123",
      "title": "Backend Engineer",
      "state": "published",
      "department": "Engineering",
      "employment_type": "full_time",
      "application_url": "https://apply.workable.com/..."
    }
  ],
  "paging": {
    "next": "https://subdomain.workable.com/spi/v3/jobs?cursor=..."
  }
}
```

Job list items may include `description`, `full_description`, `requirements` when expanded. Often minimal; use GET /jobs/:shortcode for full details.

## GET /jobs/:shortcode (job details)

```json
{
  "job": {
    "shortcode": "ABC123",
    "title": "Backend Engineer",
    "details": {
      "description": "<p>We are looking for...</p>",
      "full_description": "<p>Full job description...</p>",
      "requirements": "<ul><li>5+ years Python</li></ul>",
      "benefits": "<p>Health, 401k...</p>"
    },
    "location": {
      "city": "San Francisco",
      "region": "CA",
      "country": "United States",
      "workplace_type": "remote"
    }
  }
}
```

Alternative: response may be flat (no `job` wrapper); `details` may be top-level or nested.

## GET /jobs/:shortcode/candidates (list candidates)

```json
{
  "candidates": [
    {
      "id": "cand_xyz",
      "email": "candidate@example.com",
      "name": "Jane Doe",
      "stage": "Screening",
      "stage_name": "Screening",
      "stage_kind": "screening",
      "headline": "Senior Engineer",
      "created_at": "2026-01-15T10:00:00Z"
    }
  ],
  "paging": {
    "next": "https://subdomain.workable.com/spi/v3/jobs/ABC123/candidates?limit=100&..."
  }
}
```

Some accounts use `data` or `results` instead of `candidates`. Email may be in `email`, `contact.email`, `emails[0].value`, or `profile.email`.

## Terminal stages (excluded from sync)

- `hired`, `rejected`, `withdrawn`, `disqualified`, `declined`, `archived`
- `disqualified: true` or `hired_at` present

## Rate limits

- 10 requests per 10 seconds
- Use ~0.5–1s throttle between requests
