# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Sam

## Context

The platform is built almost entirely through AI coding agents, which are stateless
across sessions. Settled decisions get re-litigated, and the reasoning behind the
architecture lives only in Sam's head and scattered prose. We need a durable, agent-
readable record of *why* things are the way they are.

## Decision

We will keep Architecture Decision Records in `architecture/decisions/`, using the
Nygard format. Every significant architectural decision gets an ADR. The ADR log is
part of the North Star and is injected into agent context across all repos. Changing
the North Star is done *by raising an ADR*, not by editing code and letting the model
drift.

## Consequences

- Agents (and humans) have a single place to learn why a decision holds, reducing
  re-litigation.
- There is a small, ongoing discipline cost: architectural changes must come with an
  ADR. CI and the agent guardrail enforce this softly.
- ADR ids are referenced from `model.yaml`; `validate_model.py` checks they resolve.
