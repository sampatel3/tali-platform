# View — Substrate Components (C4 L3)

> Derived from `../model.yaml` (`components` where `container: mainspring-substrate`).
> These are the invariant-bearing pieces agents most often get wrong. Each links to
> the ADR that makes it non-negotiable.

```mermaid
flowchart TB
    subgraph mainspring [Mainspring Substrate]
        metered[Metered Anthropic Client<br/>ADR-0003: every call writes a UsageEvent]
        decision[Workable Decision Engine<br/>ADR-0004: writes serialized per org]
        migrations[Per-Brand Migration Chains<br/>ADR-0005: each brand owns its chain]
        billing[Billing<br/>Stripe + cost estimation]
    end

    anthropic[[Anthropic]]
    workable[[Workable]]
    stripe[[Stripe]]

    metered --> anthropic
    decision --> workable
    billing --> stripe
```

| Component                | Invariant | Today (verifiable in tali-platform)                                       |
| ------------------------ | --------- | ------------------------------------------------------------------------- |
| Metered Anthropic Client | ADR-0003  | `backend/app/services/metered_anthropic_client.py`, `.../metered_async_anthropic_client.py` |
| Workable Decision Engine | ADR-0004  | `backend/app/components/integrations/workable/service.py`, `.../sync_service.py` |
| Per-Brand Migrations     | ADR-0005  | `backend/alembic`                                                         |
| Billing                  | —         | `backend/app/services/pricing_service.py`, `.../credit_ledger_service.py` |

These migrate into `mainspring` (`migratesTo`) as legacy capabilities are drained.
