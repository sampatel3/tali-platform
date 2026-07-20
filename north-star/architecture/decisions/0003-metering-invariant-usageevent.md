# 3. Metering invariant: every Anthropic call writes a UsageEvent

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Sam

## Context

The product's economics depend on attributing AI cost to orgs/assessments. If any
Anthropic call can bypass metering, usage and cost numbers are silently wrong and
billing under-counts. AI agents frequently "just call the SDK" when adding a feature,
which is exactly the path that breaks this.

## Decision

We will permit **no raw Anthropic SDK call path**. Every call to Anthropic goes
through the metered client, which writes a `UsageEvent` for the call. The metered
client is substrate-owned (`mainspring`). This is CI-gated: a check fails the build if
the raw SDK is imported/called outside the metered client.

Current (legacy) home: `backend/app/services/metered_anthropic_client.py` and
`backend/app/services/metered_async_anthropic_client.py`. Target home: `mainspring`.

## Consequences

- Usage and cost are complete and trustworthy by construction.
- Adding an AI feature means going through the metered client — slightly more
  ceremony than a direct SDK call, deliberately.
- The CI gate must be kept in sync with the SDK surface (e.g. new client classes).
- Modelled as `invariants[].metering-mandatory` and on the `metered-anthropic-client`
  component in `model.yaml`.
