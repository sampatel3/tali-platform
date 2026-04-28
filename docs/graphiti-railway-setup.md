# Graphiti + Neo4j on Railway — Setup Guide

The candidate knowledge graph is built on **Graphiti** (Zep AI's
temporal-aware framework) backed by **Neo4j**. Graphiti owns the
ingestion pipeline: it pulls Candidate profiles, full interview
transcripts, raw CV text, and pipeline events from Postgres, extracts
entities/relations using Anthropic, embeds them with Voyage AI, and
serves hybrid (graph + BM25 + vector) search on top.

The integration is **optional**: when `NEO4J_URI` or `VOYAGE_API_KEY`
is empty the graph features degrade gracefully (graph view shows a
configuration hint, NL queries skip graph predicates and run on
Postgres alone).

## 1. Provision Neo4j on Railway

1. Open the Railway project that hosts the Tali backend service.
2. Click **+ New** → **Template** → search for **"Neo4j"** → pick the
   community-edition template (deploys `neo4j:5-community` with bolt
   on port 7687 and the browser on 7474).
3. Railway provisions a service named `neo4j`. After it boots, open
   the service → **Variables** and copy:
   - `NEO4J_AUTH` (format `neo4j/<password>`)
   - The internal bolt URL — Railway exposes it as
     `bolt://${{Neo4j.RAILWAY_PRIVATE_DOMAIN}}:7687` (private domain →
     same-region traffic, no egress fees).

## 2. Provision Voyage AI

Voyage powers Graphiti's embeddings (`voyage-3`, 1024 dims). It's the
single non-Anthropic vendor in the stack — Anthropic recommends them
as the embedding partner for Claude-centric apps.

1. Sign up at [voyageai.com](https://www.voyageai.com/) and create an
   API key.
2. Note the key — you'll paste it as a Railway env var below. **Don't
   commit it** (Tali's `.gitignore` covers `.env`, but not arbitrary
   files; only set keys via Railway/`backend/.env`).

## 3. Wire env vars on the backend service

In the Tali backend service's **Variables** tab, add:

| Variable                | Value                                                  |
| ----------------------- | ------------------------------------------------------ |
| `NEO4J_URI`             | `bolt://${{Neo4j.RAILWAY_PRIVATE_DOMAIN}}:7687`        |
| `NEO4J_USER`            | `neo4j`                                                |
| `NEO4J_PASSWORD`        | The password from `NEO4J_AUTH` (the part after `neo4j/`) |
| `NEO4J_DATABASE`        | `neo4j` (default; only change if you create extra DBs) |
| `VOYAGE_API_KEY`        | Your Voyage AI key                                     |
| `GRAPHITI_LLM_MODEL`    | `claude-haiku-4-5-20251001` (default)                  |
| `GRAPHITI_EMBEDDING_MODEL` | `voyage-3` (default)                                |

Anthropic credentials (`ANTHROPIC_API_KEY`) are reused — Graphiti
shares Tali's existing Anthropic configuration.

Railway re-deploys the backend service automatically on save.

## 4. Verify the connection

After redeploy:

```bash
curl https://<backend-domain>/healthz/graphiti
```

Expected: `{"status": "ok"}`. A `"status":"unconfigured"` response
means env vars aren't picked up; `"status":"error"` means the backend
reached Neo4j but the driver, LLM, or embedder errored — check the
backend logs for the specific cause.

## 5. Backfill existing candidates and interviews

After the connection is healthy, run the one-shot backfill against
your production org from a Railway one-off shell or your local machine
pointed at the production env:

```bash
# One organisation
python -m app.candidate_graph.backfill --org <organization_id>

# All organisations (does the same work, organised by org)
python -m app.candidate_graph.backfill --all-orgs
```

The backfill is idempotent — safe to re-run after schema bumps. It
walks every candidate's `experience_entries`, `education_entries`,
`skills`, raw `cv_text`, every linked `application_interviews` row
(transcript + structured summary), and every `candidate_application_events`
row with non-trivial notes. Each becomes a Graphiti episode tagged
with `group_id = "org:<id>"`.

**Cost estimate** (defaults: Anthropic Haiku 4.5 + Voyage `voyage-3`):

- ~$0.005 per profile/interview/CV episode (Anthropic extraction)
- ~$0.0001 per Voyage embedding call (1024-dim, voyage-3)
- Typical org of 200 candidates with 1 interview each: **~$3-8 total**.
- Cap per candidate via `GRAPHITI_MAX_EPISODES_PER_CANDIDATE` (default 40).

## 6. Multi-tenancy

A single Neo4j + Graphiti instance serves all orgs. Tenancy is
enforced via Graphiti's built-in `group_id`: every episode, entity,
and edge carries `group_id = "org:<organization_id>"` and every
`graphiti.search()` call passes `group_ids=[group_id]` to filter
results. Cross-org traversal is impossible by construction.

There is no row-level security in Neo4j; the safety guarantee comes
from `app.candidate_graph.client.group_id_for_org()` being the only
way to derive the group_id, and from `app.candidate_graph.search.*`
always passing it through. Direct Graphiti driver use is forbidden
by the architecture — the runner / rerank / endpoint paths all go
through the adapter functions in `search.py`.

## 7. Backups

Railway snapshots the Neo4j volume daily by default. Because Postgres
is the source of truth and the graph is a derived projection, the
disaster-recovery story is:

1. Re-provision Neo4j (or fail-over to a fresh instance).
2. Wire the new credentials into the backend.
3. Run `python -m app.candidate_graph.backfill --all-orgs` to rebuild.

Estimated rebuild time at current scale: ~3 minutes per 10k candidates,
plus per-candidate LLM extraction time (1-5s per episode), so plan
for ~30-60 minutes per 1000 candidates of real wall clock.

## 8. Disabling the integration

If you ever need to take Graphiti down (incident, cost cut, Voyage
outage), unset `VOYAGE_API_KEY` on the backend service. The graph
view, graph-predicate NL queries, and the rerank step's graph
context all degrade silently — list-style NL search keeps working
against Postgres.

## 9. What gets ingested

Per candidate, the backfill produces episodes for:

| Source                                     | Episodes per candidate | Notes |
| ------------------------------------------ | ---------------------- | ----- |
| `Candidate.headline + summary + location`  | 1                      | `candidate.profile` |
| `skills + education_entries`               | 0-1                    | `candidate.skills_education` |
| `experience_entries` (oldest first)        | 1 each, capped         | `candidate.experience` |
| `cv_text` (truncated to 12KB)              | 0-1                    | `candidate.cv_text` |
| `ApplicationInterview.transcript_text`     | 1 per interview        | `interview.transcript.<stage>` |
| `ApplicationInterview.summary` JSON        | 1 per interview        | `interview.summary.<stage>` |
| `CandidateApplicationEvent.notes`          | 0-1 (skip pure transitions) | `event.<event_type>` |

Pure pipeline state transitions (no recruiter notes) are dropped on
purpose — they wouldn't extract useful facts.
